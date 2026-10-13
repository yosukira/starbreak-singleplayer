'Live Dumb Hard encounter state machine.'
import math
import random

from . import params
from .scene import DumbBossPack
from .scene import motion_position

class DumbBossController:
    name = 'dumb_boss'

    def __init__(self, pack=None, seed=None, behaviour=None):
        self.pack = pack or DumbBossPack()
        self.behaviour = behaviour if behaviour is not None else getattr(self.pack, 'behaviour', {}) or {}
        boss = (self.behaviour.get('boss') or {})
        self.gates = [int(s['gate']) for s in boss.get('stages') or []] or list(params.STAGE_GATES)

        self.attack_windows = list(params.ATTACK_WINDOW_S)
        timing=getattr(self.pack,'wave_timing',{})
        self.wave_windows=self._list(timing.get('nominal_rear_hold_s'),params.WAVE_WINDOW_S)[:4]
        self.wave_spawns = self._list(boss.get('wave_spawns_by_stage'), params.WAVE_SPAWNS)
        self.descend_s = float(boss.get('descend_s') or params.DESCEND_S)
        self.ascend_s = float(boss.get('ascend_s') or params.ASCEND_S)
        self.grid = [tuple(p) for p in ((boss.get('grid') or {}).get('points') or [])]
        self.regions = params.region_choices(self.behaviour)
        self.seed = 0xC0FFEE if seed is None else int(seed)
        self.rng = random.Random(self.seed)
        self.reset()

    @staticmethod
    def _list(value, default):
        try:
            rows = [float(v) for v in value]
        except (TypeError, ValueError):
            rows = []
        while len(rows) < len(default):
            rows.append(float(default[len(rows)]))
        return rows

    def reset(self):
        self.time = 0.0
        self.state = 'waiting'
        self.state_at = self.state_until = 0.0
        self.stage = 1
        self.stage_damage = 0.0
        self.total_damage = 0.0
        self.pending = []
        self.event_count = 0
        self.heavy_id = None
        self.boss_spawned = False
        self.finished = False
        self.final_stage_cleared = False
        self.waves = 0
        self.spawned = 0
        self.next_move_at = self.next_laser_at = self.next_drop_at = self.next_lob_at = 0.0
        self.next_spawn_at = 0.0
        self.volleys = {}
        self.next_volley = {}
        self.move_target = None
        self.motion_start = params.ARENA_TOP
        self.motion = {}
        self.motion_epoch = 0.
        self.pending_stage = False

    def begin(self, heavy_id=None):
        if self.state != 'waiting':
            return []
        self.heavy_id = heavy_id
        self._enter('intro')
        self.emit('heavy_create', objid=heavy_id)
        return self._drain()

    def emit(self, kind, **values):
        self.pending.append(dict(kind=kind, at=round(self.time, 6), **values))
        self.event_count += 1

    def npc_died(self, objid):
        if self.state == 'intro' and self.heavy_id is not None and int(objid) == int(self.heavy_id):
            self.emit('heavy_down', objid=objid)
            self._enter('opening')

    def add_damage(self, value, objid=None):
        if objid is not None and int(objid)!=self.pack.boss_id:
            return False
        if not self.vulnerable:
            return False
        value = float(value or 0)
        if not math.isfinite(value) or value <= 0:
            return False
        self.stage_damage += value
        self.total_damage += value
        return True

    @property
    def vulnerable(self):
        return self.state in ('descend','attack','recall_pause','recall') and self.boss_spawned

    @property
    def armored(self):
        return self.stage <= 2

    @property
    def gate(self):
        return self.gates[min(self.stage, len(self.gates)) - 1]

    def status(self):
        return dict(map=self.name, state=self.state, time=round(self.time, 3),
                    stage=self.stage, stages=len(self.gates), armor=self.armored,
                    stage_damage=round(self.stage_damage, 1), gate=self.gate,
                    total_damage=round(self.total_damage, 1), waves=self.waves,
                    spawned=self.spawned, boss_spawned=self.boss_spawned,
                    finished=self.finished, events=self.event_count,
                    evidence='stages: player-reported gates; windows: calibrated recordings')

    def _enter(self, state):
        self.state = state
        self.state_at = self.time
        self.state_until = math.inf
        if state == 'opening':
            self.state_until=self.time+.45
        elif state == 'birth':
            self.emit('boss_create')
            self.boss_spawned = True
            self.state_until=self.time+.45
        elif state == 'descend':
            self.emit('boss_show')

            duration=params.TRANSIT_BY_STAGE[getattr(self,'cycle_stage',self.stage)-1]
            self._emit_move(params.ARENA_CENTER, duration)
            self.state_until = self.time + duration
        elif state == 'attack':
            self.slow_reveal_move=self.pending_stage and self.stage==3
            self.planned_grid_point=None
            self.next_eye_plan_at=math.inf
            if self.pending_stage:
                self.emit('stage',index=self.stage,armor=self.armored)
                self.pending_stage=False
            self.cycle_stage = self.stage
            self.state_until = self.time + self.attack_windows[self.stage - 1]
            self.next_move_at=self.time+(.57 if self.slow_reveal_move else 1.2)

            self.volleys = {}
            self.next_volley = {
                'laser': self.time + params.CHANNEL_FIRST_S['laser'],
            }
            if self.stage >= 3:
                self.next_volley['drop'] = self.time + params.CHANNEL_FIRST_S['drop']
                self.next_volley['lob'] = self.time + params.CHANNEL_FIRST_S['lob']
        elif state == 'recall_pause':
            self.next_eye_plan_at=math.inf
            self.planned_grid_point=None
            self.motion_start=self.position()
            self.motion={}
            self.motion_epoch=self.time
            self.volleys = {}
            self.emit('boss_move',move={},to={})
            self.state_until=self.time+(1.32 if self.stage==5 else 1.8)
        elif state == 'recall':

            target = params.ARENA_RECALL
            current = self.position()
            distance = math.hypot(target[0] - current[0], target[1] - current[1])
            duration = max(0.05, distance / params.GRID_CRUISE_SPEED[self.cycle_stage-1])
            self._emit_move(target, duration)
            self.state_until = self.time + duration
        elif state == 'ascend':
            duration=params.TRANSIT_BY_STAGE[self.cycle_stage-1]
            self._emit_move(params.ARENA_TOP, duration)
            self.state_until = self.time + duration
        elif state == 'wave_descend':

            self.emit('boss_hide')
            duration=params.TRANSIT_BY_STAGE[self.cycle_stage-1]
            self._emit_move(params.ARENA_CENTER, duration)
            self.state_until = self.time + duration
        elif state == 'wave':
            self.waves += 1
            stage = getattr(self, 'cycle_stage', self.stage)

            slot=min(stage-1,3)
            window=self.wave_windows[slot]
            count=max(1,int(self.wave_spawns[slot]))
            self.spawn_interval=params.WAVE_INTERVALS_S[slot]
            self.wave_heavies=0
            self.spawns_left = count
            self.next_spawn_at=self.time+params.WAVE_FIRST_FLASH_S
            self.state_until = self.time + window
            self.emit('wave_begin', stage=stage, window=round(window, 2), mobs=count)
        elif state == 'wave_ascend':
            duration=params.TRANSIT_BY_STAGE[self.cycle_stage-1]
            self._emit_move(params.ARENA_TOP, duration)
            self.state_until = self.time + duration
        elif state == 'death':
            self.emit('boss_defeated')
            death = (self.pack.programs.get('programs') or {}).get('death') or {}
            self.state_until = self.time + float(death.get('duration_s') or 6.0)
        elif state == 'done':
            self.state_until = self.time
            self.finished = True

    def position(self):
        return motion_position(self.motion_start,self.motion,self.time-self.motion_epoch)

    def _emit_move(self, point, duration, eased=False, eye=False, cruise_speed=None):
        'Publish a MOVE.  `eye` marks the attack-phase grid hops whose'
        start=self.position()
        self.move_target = (int(point[0]), int(point[1]))
        to=dict(xmp=int(point[0]),ymp=int(point[1]),dt=int(round(duration*1000)))
        move=dict(to=to)
        if eased:
            speed=cruise_speed or params.GRID_CRUISE_SPEED[self.stage-1]
            distance=math.dist(start,point)
            first=dict(xmp=round(start[0]+(point[0]-start[0])/8),
                       ymp=round(start[1]+(point[1]-start[1])/8),dt=int(distance*.5/speed*1000))
            last=dict(xmp=to['xmp'],ymp=to['ymp'],dt=int(distance*.875/speed*1000))
            move=dict(waypoint=dict(points=[first,last]))
        self.motion_start,self.motion,self.motion_epoch=start,move,self.time
        self.emit('boss_move',to=to,move=move,eye=bool(eye))

    def _leave_attack(self):
        cleared = self.stage_damage >= self.gate - 1e-9
        if cleared:
            self.emit('stage_cleared', stage=self.stage, damage=round(self.stage_damage, 1))
            if self.stage >= len(self.gates):
                self.final_stage_cleared = True
            else:
                self.stage += 1
                self.stage_damage = 0.0
                self.pending_stage=True
        else:
            self.emit('stage_incomplete', stage=self.stage,
                      damage=round(self.stage_damage, 1), gate=self.gate)
        self._enter('recall_pause')

    def _leave_state(self):
        state = self.state
        if state == 'opening':
            self.emit('curtain_open')
            self.state='birth_wait';self.state_until=self.time+.45
        elif state == 'birth_wait':
            self._enter('birth')
        elif state == 'birth':
            self._enter('descend')
        elif state == 'descend':
            self._enter('attack')
        elif state == 'attack':
            self._leave_attack()
        elif state == 'recall_pause':
            self.volleys = {}
            self._enter('recall')
        elif state == 'recall':
            self._enter('death' if self.final_stage_cleared else 'ascend')
        elif state == 'ascend':
            self._enter('wave_descend')
        elif state == 'wave_descend':
            self._enter('wave')
        elif state == 'wave':
            self._enter('wave_ascend')
        elif state == 'wave_ascend':
            self._enter('descend')
        elif state == 'death':
            self._enter('done')

    def _grid_point(self):
        """Fixed 3x3 anchors: centre plus wall/ground-flush positions.

        The recordings only ever stop at x in {left flush, centre, right flush}
        and y in {top, middle, ground flush}; the extra sampled coordinates are
        mid-leg sprite positions, not stopping points.
        """
        options = []
        for gx in params.BOSS_GRID_X:
            for gy in params.BOSS_GRID_Y:
                point = (gx, gy)
                if point != self.move_target:
                    options.append(point)
        return self.rng.choice(options)

    def _attack_tick(self):
        if self.stage>=3 and self.time+1e-9>=self.next_eye_plan_at:
            self.planned_grid_point=self._grid_point()
            current=self.move_target or params.ARENA_CENTER
            frame=params.boss_eye_frame(self.planned_grid_point[0]-current[0],
                                        self.planned_grid_point[1]-current[1])
            if frame:
                self.emit('boss_eye_show',frame=frame)
            self.next_eye_plan_at=math.inf
        while self.next_move_at <= self.time + 1e-9:
            point=self.planned_grid_point or self._grid_point()
            self.planned_grid_point=None
            dx = point[0] - (self.move_target or params.ARENA_CENTER)[0]
            dy = point[1] - (self.move_target or params.ARENA_CENTER)[1]
            slow=self.slow_reveal_move
            speed=params.GRID_CRUISE_SPEED[1]if slow else params.GRID_CRUISE_SPEED[self.stage-1]
            duration=math.hypot(dx,dy)*1.375/speed

            self.next_move_at = self.time+duration+params.GRID_DWELL_BY_STAGE[self.stage-1]
            if self.stage>=3:
                self.next_eye_plan_at=self.next_move_at-params.BOSS_EYE_LEAD_S
            self._emit_move(point,duration,eased=True,eye=not slow,cruise_speed=speed)
            self.slow_reveal_move=False
        for kind in self._channels():
            volley = self.volleys.get(kind)
            if volley is None:
                if self.time + 1e-9 >= self.next_volley.get(kind, self.time):
                    self._start_volley(kind)
            else:
                self._volley_tick(kind)
                self._volley_done(kind)

    def _channels(self):
        if self.stage >= 3:
            return ('laser', 'drop', 'lob')
        return ('laser',)

    def _next_channel_event(self):
        """Earliest time any channel needs a tick (for the step clock)."""
        rows = [self.next_move_at, self.state_until,self.next_eye_plan_at]
        for kind in self._channels():
            volley = self.volleys.get(kind)
            if volley is None:
                rows.append(self.next_volley.get(kind, self.time))
            else:
                rows.append(volley['fire_at'])
        return min(rows)

    def _start_volley(self, kind):
        'Local battle simulator runtime.'
        armored = self.stage <= 2
        if kind == 'laser':

            slot, charge_slot = self.rng.choice(
                params.LASER_VOLLEYS_ARMORED if armored
                else params.LASER_VOLLEYS_EXPOSED)
            charge = params.LASER_CHARGE_S
            shots = params.LASER_SHOTS_ARMORED if armored else params.LASER_SHOTS_EXPOSED
            tick = params.LASER_TICK_S
            sound = 'dumb.bosslasercharge'
        elif kind == 'drop':
            slot, charge, charge_slot = 3, params.DROP_CHARGE_S, 3
            shots, tick, sound = params.DROP_SHOTS, params.DROP_TICK_S, None
        else:
            slot, charge, charge_slot = 0, 0.0, 0
            shots, tick, sound = params.LOB_SHOTS, params.LOB_TICK_S, None
        self.volleys[kind] = dict(kind=kind, shots=shots, left=shots, tick=tick,
                                  slot=slot, fire_at=self.time + charge,
                                  started=self.time)

        time_ms = int(charge * 1000)
        if kind == 'laser':
            table = (self.pack.animations.get(
                'dumb.bossshield' if armored else 'dumb.boss') or [])
            if 0 <= int(charge_slot) < len(table):
                time_ms = int(table[int(charge_slot)].get('length') or time_ms)
        self.emit('boss_charge', attack=kind, anim=charge_slot, slot=charge_slot,
                  time_ms=time_ms, sound=sound)
        if charge <= 0:
            self._volley_tick(kind)
            self._volley_done(kind)

    def _volley_tick(self, kind):
        volley = self.volleys.get(kind)
        if volley is None:
            return
        if self.time + 1e-9 >= volley['fire_at'] and volley['left'] > 0:
            volley['left'] -= 1
            volley['fire_at'] += volley['tick']
            self.emit('boss_attack', attack=kind, stage=self.stage, slot=volley['slot'])

    def _volley_done(self, kind):
        volley = self.volleys.get(kind)
        if volley is None:
            return
        if volley['left'] <= 0 and self.time + 1e-9 >= volley['fire_at']:
            del self.volleys[kind]
            interval = params.CHANNEL_INTERVAL_S.get(kind)
            self.next_volley[kind] = volley['started'] + (
                interval if interval is not None else self.rng.uniform(*params.BOSS_COOLDOWN_S))

    def _wave_tick(self):
        while self.next_spawn_at<=self.time+1e-9 and self.next_spawn_at<self.state_until:
            self.next_spawn_at += self.spawn_interval
            batch=self.rng.choices(params.WAVE_BATCH_SIZES,weights=params.WAVE_BATCH_WEIGHTS,k=1)[0]
            for _ in range(batch):
                self.emit('wave_spawn',region=self._pick_region(),
                          stage=getattr(self,'cycle_stage',self.stage))
                self.spawned+=1

    def _pick_region(self):
        names = [row['name'] for row in self.regions]
        weights = [row['weight'] for row in self.regions]
        if self.wave_heavies>=params.WAVE_HEAVY_LIMIT:
            choices=[(n,w)for n,w in zip(names,weights)if n!='center_heavy']
            names,weights=zip(*choices)
        region=self.rng.choices(names,weights=weights,k=1)[0]
        if region=='center_heavy':
            self.wave_heavies+=1
        return region

    def step(self, dt):
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        end = self.time + dt
        guard = 0
        while self.time < end - 1e-9 and not self.finished:
            guard += 1
            if guard > 10000:
                raise RuntimeError('dumb boss controller did not converge')
            limit = end
            if self.state == 'attack':
                self._attack_tick()
                limit = min(limit, self._next_channel_event())
            elif self.state == 'wave':
                self._wave_tick()
                limit = min(limit, self.next_spawn_at, self.state_until)
            elif self.state == 'intro':
                break
            else:
                limit = min(limit, self.state_until)
            if self.state == 'waiting':
                break
            self.time = max(self.time, min(limit, end))
            if self.time >= self.state_until - 1e-9 and math.isfinite(self.state_until):
                self._leave_state()
            elif self.time >= end - 1e-9:
                break
        self.time = max(self.time, end)
        return self._drain()

    def _drain(self):
        out, self.pending = self.pending, []
        return out
