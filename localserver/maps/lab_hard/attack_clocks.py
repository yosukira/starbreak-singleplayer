"""Action clocks that survive phase changes, measured from ETL commands."""
import bisect
import copy
from .scheduling import arm_layout, remap_pull_row, phase_wall_pulls

class WallPullClock(object):
    """Draw walls afresh; wait from the previous live release, across phases."""

    def __init__(self, pack, rng):
        self.pack = pack
        self.rng = rng
        self.policy = (getattr(pack,'attack_policy',{}) or {}).get('wall',{})
        self.enabled = bool(self.policy.get('enabled'))
        self.groups = {}

        starts = [float(row.get('start', 0.0)) - float(pack.fight_start)
                  for row in (pack.phase_doc.get('phases') or [])]
        for row in phase_wall_pulls(pack):
            phase = row.get('phase_index')
            if phase is None:
                at = float(row.get('at') or 0.0)
                phase = max(0, bisect.bisect_right(starts, at) - 1)
            key = (int(phase), row.get('group_at', row.get('at')))
            self.groups.setdefault(key,[]).append(row)
        self.claws,self.bases,self.wall_bases = arm_layout(pack)
        self.next_at = None
        self.serial = 0
        self.release_wait = None
        self.release_at = None
        self.wait_phase = None
        self.ring_group_seen = False
        self.available_claws = None
        self.pending_source = None
        self.pending_phase = None
        self.waiting_for_claws = False

    def note_release(self, until):
        if self.release_wait is not None:

            self.release_at = float(until)
            self.wait_phase = None
            self.next_at = float('inf')

    def _release_samples(self, phase):
        table = self.policy.get('next_after_release_by_phase') or {}
        rows = table.get('p%d' % (int(phase)+1))
        return list(rows) if rows else list(
            self.policy.get('next_after_release_samples_s') or [15.])

    def _hold_samples(self, phase):
        table = self.policy.get('hold_after_hands_home_by_phase') or {}
        rows = table.get('p%d' % (int(phase)+1))
        return list(rows) if rows else list(
            self.policy.get('hold_after_hands_home_samples_s') or [12.])

    def step(self, now, phase, age):
        if not self.enabled or phase < 1:
            return []
        if self.next_at is None:
            delay = self.rng.choice(self.policy.get('initial_delay_samples_s') or [9.])
            self.next_at = now-age+delay
        elif self.release_at is not None and self.wait_phase != phase:

            self.release_wait = self.rng.choice(self._release_samples(phase))
            self.wait_phase = phase
            self.next_at = self.release_at + self.release_wait
        if now+1e-9 < self.next_at:
            return []
        candidates = [rows for (p,_),rows in self.groups.items() if p==phase]
        if not candidates:
            return []
        if phase>=2 and not self.ring_group_seen:
            leaders=[rows for rows in candidates if len(rows)>=2
                     and any(r['wall']==217 for r in rows)]
            pairs=[rows for rows in candidates if len(rows)>=2]
            candidates=leaders or pairs or candidates
        if self.pending_source is None or self.pending_phase!=phase:
            self.pending_source=self.rng.choice(candidates)
            self.pending_phase=phase
        source = self.pending_source
        key = (source[0].get('phase_index'),source[0].get('group_at'))
        available=set(self.available_claws()) if self.available_claws else set(self.claws.values())
        eligible=[wall for wall in sorted(self.pack.wall_ids)
                  if self.claws.get(self.wall_bases.get(wall)) in available]
        if len(eligible)<len(source):
            self.waiting_for_claws=True
            return []
        drawn = self.rng.sample(eligible,len(source))
        self.waiting_for_claws=False
        self.pending_source=None
        if phase>=2:self.ring_group_seen=True
        mapping = dict(zip(sorted(r['wall'] for r in source),drawn))
        hold = self.rng.choice(self._hold_samples(phase))
        self.release_wait = self.rng.choice(self.policy.get('next_after_release_samples_s') or [15.])
        self.serial += 1
        self.next_at = float('inf')
        events = []
        for original in source:
            row = copy.deepcopy(original)
            row.update(kind='wall_pull',group_walls=[r['wall'] for r in source],
                       walls=[row['wall']],claws=[row['claw']],captured_pull=copy.deepcopy(original))
            remap_pull_row(row,{key:mapping},self.claws,self.bases,self.wall_bases)
            row.update(at=round(now,6),phase_index=phase,phase_at=age,
                       phase='p%d'%(phase+1),group_at=now,wall_group=self.serial,
                       loop=self.serial,interactive=True,hold_after_hands_home_s=hold)
            events.append(row)
        return events

class RingBurstClock(object):
    '52-shot bursts with runtime pauses and independently drawn rotation.'

    LEAD_S = .205

    def __init__(self, pack, rng):
        self.policy = (getattr(pack,'attack_policy',{}) or {}).get('ring',{})
        self.rng = rng
        self.enabled = bool(self.policy.get('enabled') and self.policy.get('profiles'))
        self.unlock_at = None
        self.unlock_group = None
        self.unlock_source_wall = None
        self.unlock_group_walls = []
        self.start = None
        self.offsets = []
        self.cursor = 0
        self.anim_cursor = 0
        self.base = 0.
        self.direction = 1
        self.rest = 5.48
        self.paused = False

    def note_wall_pull(self, row, phase):
        'Unlock on the first live extra-hand pull in the two-arm phase.'
        if not self.enabled or phase < 2 or row.get('attach_at') is None:
            return
        key = (row.get('wall_group', row.get('group_at')), row.get('loop', 0))
        if self.unlock_group is None:
            self.unlock_group = key
        elif key != self.unlock_group:
            return
        attach = float(row['attach_at'])
        candidate = attach - float(self.policy.get('unlock_before_attach_s', .102))
        if self.unlock_at is None or candidate < self.unlock_at:
            source = row.get('captured_pull') or row
            self.unlock_at = candidate
            self.unlock_source_wall = int(source.get('wall', 0) or 0)
            self.unlock_group_walls = [
                int(value) for value in
                (row.get('group_walls') or row.get('walls') or [row.get('wall')])
                if value is not None]

    def _begin(self, when, aim_deg=None):
        profile = self.rng.choice(self.policy['profiles'])
        self.offsets = list(profile['offsets_s'])
        self.direction = int(profile['direction'])
        self.base = self.rng.uniform(0.,360.)

        if aim_deg is not None:
            delta = (float(aim_deg) - self.base + 180.0) % 360.0 - 180.0
            if abs(delta) > 1.0:
                self.direction = 1 if delta > 0 else -1
        self.rest = self.rng.choice(self.policy.get('rest_samples_s') or [5.48])
        self.start = float(when)
        self.cursor = self.anim_cursor = 0

    def step(self, now, phase, suppressed=False, resume_at=None, aim_deg=None):
        events = []
        if not self.enabled or self.unlock_at is None:
            return events
        if suppressed:
            if not self.paused:
                self.start = None
                self.paused = True

            if resume_at is None or now < resume_at-self.LEAD_S:
                return events
            if self.start is None:
                self._begin(max(self.unlock_at,float(resume_at)), aim_deg)
        else:
            if self.start is None:
                self._begin(max(self.unlock_at,now) if self.paused else self.unlock_at,
                            aim_deg)
            self.paused = False
        while True:
            while self.anim_cursor < len(self.offsets):
                at = self.start+self.offsets[self.anim_cursor]-self.LEAD_S
                if at > now+1e-9:
                    break
                events.append(dict(kind='boss_anim',anim='SHOOT4',animid=2,time_ms=396,
                                   pattern='ring',at=round(at,6)))
                self.anim_cursor += 1
            while self.cursor < len(self.offsets):
                at = self.start+self.offsets[self.cursor]
                if at > now+1e-9 or suppressed:
                    break
                angle = (self.base+self.direction*14.4*self.cursor)%360.
                events.append(dict(kind='volley',pattern='ring',count=1,damage=45,
                                   facing_deg=angle,base_deg=round(self.base,3),
                                   phase='p%d'%(phase+1),at=round(at,6)))
                self.cursor += 1
            next_start = self.start+self.offsets[-1]+self.rest
            if self.cursor < len(self.offsets) or next_start-self.LEAD_S > now:
                break
            self._begin(next_start, aim_deg)
        return sorted(events,key=lambda r:r['at'])
