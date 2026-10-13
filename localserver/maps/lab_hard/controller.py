"""Headless Zomputer boss controller.

P1 advances at 10,000 damage. Multiplayer-calibrated later phases require
40,000 damage and
60 seconds, and a ready phase waits for the live wall release command.
Player claws and wall claws are distinct.
Poison gas suppresses boss shots inside the two-arm phase and across its
transition. ``step`` emits semantic events; the session owns the wire.
"""

import random

from .. import MapController
from .arena import wall_normal
from .attack_clocks import RingBurstClock, WallPullClock
from .phases import PhaseMachine, specs_from_document
from .scene import LabHardPack
from .scheduling import encounter_schedules, randomised_pull_plan

ARM_BATCH_MIN = 30
WALL_RETURN_S = 1.7
WALL_GROUP_WINDOW = 0.25
ADD_HITBOX_DAMAGE = 15
ADD_HITPOINTS = 75
BULLET_DAMAGE = 45

def _median(values):
    values = sorted(values)
    if not values:
        return None
    return values[len(values) // 2]

def measure_cadence(pack):
    """Return the compiled encounter cadence."""
    return dict(pack.cadence)

class GasScheduler(object):
    'Line gas swept along a pulled pair of parallel walls.'

    WAVE_COUNT = 15
    LEAD_S = 0.561
    SUPPRESS_LEAD_S = 3.0
    RESUME_TAIL_S = 4.3

    def __init__(self, pack):
        self.pack = pack
        doc = pack.gas_mode or {}
        gas = doc.get('gas') or {}
        wire=getattr(pack,'line_gas',{}) or {}
        self.wave_period = float(wire.get('wave_period_s') or 2.421)
        self.wave_offsets=list(wire.get('wave_offsets_s') or [i*self.wave_period for i in range(self.WAVE_COUNT)])
        self.anim_lead=float(wire.get('anim_lead_s') or 1.17)
        self.resume_tail=float(wire.get('resume_tail_s') or 4.28)
        self.suppress_lead=float(wire.get('suppress_lead_s') or 3.025)

        self.first_offset = float(gas.get('first_offset_mp') or 36176)
        self.cell_mp = float(gas.get('cell_mp') or 40960)
        self.gap_mp = float(gas.get('gap_mp') or 3328)
        self.lifetime_s = float(gas.get('lifetime_s') or 1.05)
        self.front_speed = float(gas.get('front_speed') or 102400)
        self.spawn_interval = float(gas.get('spawn_interval_s') or 0.205)
        self.axis = None
        self.walls = []
        self.start = None
        self.next_wave = None
        self.waves = 0
        self.fired = False
        self.started = False
        self.stopped = False
        self.suppressed = False
        self.anim_cursor=0
        self.announced=False
        self.group_key=None

    @property
    def active(self):
        return bool(self.started and not self.stopped)

    def note_wall_pull(self, row, phase_index, now):
        """Start the sweep when a two-arm pull drags two parallel walls."""
        events = []
        if phase_index != 2 or row.get('attach_at') is None:
            return events
        key=(row.get('wall_group',row.get('group_at')),row.get('loop',0))
        if self.fired:
            if key==self.group_key and self.waves==0:
                self.start=max(self.start,float(row['attach_at'])+self.LEAD_S)
                self.next_wave=self.start
            return events
        walls = [int(w) for w in (row.get('group_walls')
                                  or row.get('walls') or [])]
        if len(walls) != 2:
            return events
        normals = [wall_normal(self.pack.template(w), self.pack.play_bounds)
                   for w in walls]
        parallel = ((normals[0][0] == 0 == normals[1][0])
                    or (normals[0][1] == 0 == normals[1][1]))
        if not parallel:
            return events

        self.axis = 'horizontal' if normals[0][0] == 0 else 'vertical'
        self.walls = walls
        self.fired = True
        self.group_key=key
        self.started = True
        self.start = float(row['attach_at']) + self.LEAD_S
        self.next_wave = self.start
        return events

    def step(self, now, phase_index, phase_age):
        events = []
        if not self.started or self.stopped:
            if self.stopped:
                self.suppressed = False
            return events
        self.suppressed = now + 1e-9 >= self.start - self.suppress_lead
        while self.anim_cursor<len(self.wave_offsets) and now+1e-9>=self.start+self.wave_offsets[self.anim_cursor]-self.anim_lead:
            when=self.start+self.wave_offsets[self.anim_cursor]-self.anim_lead
            if not self.announced:
                events.append(dict(kind='gas_start',at=round(when,6),wall=self.walls[0],axis=self.axis,front_speed=self.front_speed,spawn_interval=self.spawn_interval,first_offset_mp=self.first_offset,cell_mp=self.cell_mp,gap_mp=self.gap_mp,lifetime_s=self.lifetime_s,anim='SHOOT3',origin='boss'))
                self.announced=True
            else:events.append(dict(kind='boss_anim',anim='SHOOT3',at=round(when,6)))
            self.anim_cursor+=1
        while (self.waves < len(self.wave_offsets)
               and self.start+self.wave_offsets[self.waves] <= now + 1e-9):
            events.append({'kind': 'gas_wave',
                           'at': round(self.start+self.wave_offsets[self.waves], 6),
                           'wave': self.waves})
            self.waves += 1
        last = self.start + self.wave_offsets[-1]
        if self.waves >= len(self.wave_offsets) and now + 1e-9 >= last + self.resume_tail:
            self.stopped = True
            self.suppressed = False
            events.append({'kind': 'gas_stop', 'at': round(now, 6)})
        return events

class SpecialGasScheduler(object):
    'P5 radial gas: one fight-level roll chooses gas or rotating volleys.'

    FIRST_BURST_DELAY_S = 5.8
    RESUME_TAIL_S = 2.8

    def __init__(self, pack, rng=None):
        self.pack = pack
        self.rng = rng or random.Random()
        doc = getattr(pack, 'special_gas', {}) or {}
        self.gas_chance = float(doc.get('gas_chance') or .3)
        self.roll_scope = doc.get('roll_scope') or 'fight'
        self.first_burst_delay=float(doc.get('first_burst_after_freeze_s',8.599))
        self.suppression_delay=float(doc.get('suppression_after_freeze_s',2.814))
        self.resume_tail=float(doc.get('resume_after_last_ring_s',2.735))
        self.phase = doc.get('phase') or 'p5'
        self.bursts = []
        offsets = []
        for burst in doc.get('bursts') or []:
            base = float(burst.get('phase_at') or 0.0)
            offsets.append(base)
            timeline = []
            anim = burst.get('anim') or {}
            timeline.append((0.0, {
                'kind': 'special_gas_anim',
                'animid': int(anim.get('animid') or 5),
                'time_ms': int(anim.get('time') or 2640),
            }))
            core_at = float(burst.get('core_delay_s') or 0.67)
            timeline.append((core_at, {
                'kind': 'special_gas_core',
                'life_s': float(burst.get('core_life_s') or 0.2),
            }))
            for index, ring in enumerate(burst.get('rings') or []):
                timeline.append((core_at + float(ring.get('delay_s') or 0.0), {
                    'kind': 'special_gas_ring',
                    'ring': index,
                    'offsets': list(ring.get('offsets') or []),
                    'count': int(ring.get('count')
                                 or len(ring.get('offsets') or [])),
                    'sound': 'lab.blaser' if index == 0 else None,
                }))
            self.bursts.append(sorted(timeline, key=lambda row: row[0]))
        if offsets:
            first = offsets[0]
            self.burst_offsets = [value - first for value in offsets]
        else:
            self.burst_offsets = []
        self.timeline = []
        self.cursor = 0
        self.decided = False
        self.rolled_once = False
        self.seen_groups=set()
        self.started = False
        self.stopped = False
        self.suppressed = False
        self.ends_at = None
        self.active_group=None
        self.anchor_action_end=0.
        self.suppression_at=None
        self.planned_walls=set()

    @property
    def active(self):
        return bool(self.started and not self.stopped)

    def note_wall_pull(self, row, phase_index, now, rng=None):
        """Roll 30/70 gas-vs-ring once per fight on the first four-hand pull."""
        events = []
        if phase_index != len(self.pack.phase_doc['phases']) - 1 or row.get('attach_at') is None:
            return events
        walls = list(row.get('group_walls') or row.get('walls') or [])
        if len(walls) < 4:
            return events
        key=(row.get('wall_group',row.get('group_at')),row.get('loop',0))
        if self.started and key!=self.active_group:return events
        if key!=self.active_group:
            if key in self.seen_groups:return events
            self.seen_groups.add(key)
            if self.roll_scope == 'fight' and self.rolled_once:
                return events
            self.rolled_once = True
            self.decided = True
            picker = rng or self.rng
            if picker.random() >= self.gas_chance:
                return events
            self.active_group=key
            self.planned_walls=set()

        self.anchor_action_end=max(self.anchor_action_end,float(row.get('freeze_at',row['attach_at'])))
        self.planned_walls.add(int(row['wall']))

        if len(self.planned_walls) < 4:
            return events
        self.suppression_at=self.anchor_action_end+self.suppression_delay
        anchor = self.anchor_action_end + self.first_burst_delay
        self.timeline = []
        for index, offset in enumerate(self.burst_offsets):
            for local, payload in self.bursts[index]:
                self.timeline.append((anchor + offset + local, payload))
        self.timeline.sort(key=lambda row: row[0])
        self.started = True
        last = self.timeline[-1][0] if self.timeline else anchor
        self.ends_at = last + self.resume_tail
        return events

    def step(self, now, phase_index, phase_age):
        events = []
        if not self.started or self.stopped or len(self.planned_walls)<4:
            if self.stopped:
                self.suppressed = False
            return events
        self.suppressed = now+1e-9>=self.suppression_at
        while (self.cursor < len(self.timeline)
               and self.timeline[self.cursor][0] <= now + 1e-9):
            local_at, payload = self.timeline[self.cursor]
            row = dict(payload)
            row['at'] = round(local_at, 6)
            events.append(row)
            self.cursor += 1
        if now + 1e-9 >= self.ends_at:
            self.stopped = True
            self.suppressed = False
        return events

PHASE_ANIM = {'p1': 'SHOOT', 'p2': 'SHOOT', 'p3': 'SHOOT', 'p4': 'SHOOT4',
              'p5': 'SHOOT4'}

TRANSITION_ANIM = {0: 'INTERACT', 1: 'INTERACT2', 2: 'INTERACT2',
                   3: 'INTERACT2'}

class LabHardBossController(MapController):

    name = 'lab_hard'

    def __init__(self, pack=None, sink=None, damage_required=None,rng=None):
        self.pack = pack or LabHardPack()
        self.sink = sink or (lambda event: None)
        self.damage_required = damage_required
        self.rng=rng or random.Random()
        self.cadence = measure_cadence(self.pack)
        self.prepare()

    def prepare(self):
        specs = specs_from_document(self.pack.phase_doc,
                                    damage_required=self.damage_required)

        self.machine = PhaseMachine(specs)
        self.time = 0.0
        self.events = []
        self.death_emitted = False

        self.rng = getattr(self, 'rng', None) or random.Random()
        self.pull_plan = randomised_pull_plan(self.pack, self.rng)
        self.volleys, self.structures, self.seekers = encounter_schedules(
            self.pack, self.cadence, self.pull_plan,self.rng)
        self.gas = GasScheduler(self.pack)
        self._gas_resume_done = False
        self.special_gas = SpecialGasScheduler(self.pack,self.rng)
        self.rings = RingBurstClock(self.pack,self.rng)
        self.wall_clock = WallPullClock(self.pack,self.rng)
        self._wall_hold_until = 0.0
        self._emit({'kind': 'boss_anim', 'anim': PHASE_ANIM.get('p1', 'SHOOT'),
                    'phase': 'p1', 'at': 0.0})

    def _emit(self, event):
        self.events.append(event)
        self.sink(event)

    def note_wall_hold(self, until, release_at=None):
        """The session reports the pull's wall-home completion.

        A ready phase waits until the wall has actually arrived home
        (``until``).  The wall clock's next warning is measured from the
        home *command* time (``release_at``) when the session supplies it.
        """
        self._wall_hold_until = max(self._wall_hold_until,
                                    float(until or 0.0))
        self.wall_clock.note_release(
            float(release_at) if release_at is not None
            else self._wall_hold_until)

    def add_damage(self, value, objid=None):
        if objid is not None and int(objid) != self.pack.boss_objid:
            return
        self.machine.add_damage(value)

    def step(self, dt, aim_deg=None):
        if dt < 0:
            raise ValueError('dt must be non-negative')
        if self.machine.finished:
            return []
        tick_start=self.time
        self.time += dt
        events = []

        held=min(dt,max(0.,self._wall_hold_until-tick_start))
        transitions=[]
        if held>0:
            self.machine.hold=True
            self.machine.step(held)
        self.machine.hold=False
        if dt-held>0 or held==0:
            transitions=self.machine.step(dt-held)
        for transition in transitions:
            index = transition.index
            events.append({'kind': 'phase_change',
                           'phase': (self.machine.specs[index + 1].phase_id
                                     if index + 1 < len(self.machine.specs) else 'dead'),
                           'anim': TRANSITION_ANIM.get(index, 'INTERACT2'),
                           'at': round(transition.at_time, 6),
                           'phase_damage': transition.phase_damage})
        phase = self.machine.index
        age = self.machine.time_in_phase
        if self.machine.finished:
            event = {'kind': 'death', 'at': round(self.time, 6),
                     'bursts': 45, 'corpse_delay_s': 2.73, 'loot_delay_s': 4.9}
            self.death_emitted = True
            events.append(event)
            for event in events:
                self._emit(event)
            return events
        gas_events = self.gas.step(self.time, phase, age)
        special_events = self.special_gas.step(self.time, phase, age)
        events.extend(gas_events)
        events.extend(special_events)
        suppress = self.gas.suppressed or self.special_gas.suppressed
        if self.gas.stopped and not self._gas_resume_done and not self.rings.enabled:

            suppress = True
            self._gas_resume_done = True
        recorded=self.volleys.step(self.time, phase, age,
                                  suppressed=('volley',) if suppress else ())
        if self.rings.enabled and phase >= 2:
            if self.rings.unlock_at is None:

                recorded = [row for row in recorded
                            if row.get('pattern') == 'triple']
            else:

                recorded = []
        events.extend(recorded)
        resume=[]
        if self.gas.active:
            resume.append(self.gas.start+self.gas.wave_offsets[-1]+self.gas.resume_tail)
        if self.special_gas.active and self.special_gas.ends_at is not None:
            resume.append(self.special_gas.ends_at)
        events.extend(self.rings.step(self.time,phase,suppress,
                                      max(resume) if resume else None,
                                      aim_deg=aim_deg))
        wall_events = self.structures.step(self.time, phase, age)
        if self.wall_clock.enabled:
            wall_events=[r for r in wall_events if r['kind']!='wall_pull']
            wall_events.extend(self.wall_clock.step(self.time,phase,age))
        events.extend(wall_events)

        for row in wall_events:
            if row.get('kind') != 'wall_pull':
                continue
            events.extend(self.gas.note_wall_pull(row, phase, self.time))
            events.extend(self.special_gas.note_wall_pull(row, phase,
                                                          self.time))
        events.extend(self.seekers.step(self.time, phase, age))

        for event in events:
            self._emit(event)
        return events

    def status(self):
        status = self.machine.status()
        status.update({'map': self.name, 'time': round(self.time, 3),
                       'cadence': dict((k, (round(v, 3)
                                            if isinstance(v, float) else v))
                                       for k, v in self.cadence.items()
                                       if k != 'wall_groups'),
                       'events': len(self.events),
                       'death': self.death_emitted,
                       'gas': {'active': self.gas.active,
                               'started': self.gas.started,
                               'stopped': self.gas.stopped},
                       'special_gas': {'active': self.special_gas.active,
                                       'started': self.special_gas.started,
                                       'stopped': self.special_gas.stopped,
                                       'bursts': len(self.special_gas.bursts)}})
        return status
