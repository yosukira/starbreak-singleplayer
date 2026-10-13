"""SC Boss health/time gates from doc 52; never use the wire HP as phase HP."""
import math

from .. import MapController

class ScBossController(MapController):
    name = 'sc_boss'

    def __init__(self, rules=None):
        self.rules = rules or dict(intro_s=30., stages=[
            dict(hp=5000, duration_s=75., squid_cap=9, wave_period_s=7.8),
            dict(hp=15000, duration_s=80., squid_cap=15, wave_period_s=5.7),
            dict(hp=20000, duration_s=85., squid_cap=21, wave_period_s=3.8),
            dict(hp=25000, duration_s=90., squid_cap=27, wave_period_s=2.3),
            dict(hp=50000, duration_s=25., squid_cap=33, wave_period_s=2.1)])
        self.prepare()

    def prepare(self):
        self.time = 0.
        self.state = 'intro'
        self.stage = 0
        self.stage_started = 0.
        self.hp = None
        self.damage = 0.
        self.stage_damage = 0.
        self.squid_count = 0
        self.next_wave = float(self.rules.get('first_wave_s', 0.))
        self.wave_count = 0
        self.pending = []

    @property
    def rule(self):
        return self.rules['stages'][max(0, self.stage-1)]

    @property
    def ready(self):
        return (self.state == 'fighting' and self.hp == 0
                and self.time >= self.stage_started+self.rule['duration_s']-1e-9)

    def _emit(self, kind, **values):
        self.pending.append(dict(kind=kind, at=self.time, **values))

    def _start_stage(self, stage):
        self.stage = stage
        self.state = 'fighting'
        self.stage_started = self.time
        self.hp = float(self.rule['hp'])
        self.stage_damage = 0.
        self.next_wave = min(self.next_wave, self.time+self.rule['wave_period_s'])
        self._emit('stage_start', stage=stage, hp=int(self.hp),
                   duration_s=self.rule['duration_s'], squid_cap=self.rule['squid_cap'])

    def add_damage(self, value, objid=None):
        if self.state != 'fighting' or (objid is not None and objid != 6):
            return
        value = float(value or 0)
        if not math.isfinite(value) or value <= 0:
            return
        self.damage += value

        if self.ready:
            if self.stage == len(self.rules['stages']):
                self.state = 'done'
                self._emit('boss_destroy')
            else:
                self._start_stage(self.stage+1)
            return
        self.stage_damage += value
        self.hp = max(0., self.hp-value)
        self._emit('boss_damage', stage=self.stage, hp=int(self.hp))

    def set_squid_count(self, count):
        self.squid_count = max(0, int(count))

    def step(self, dt):
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        end = self.time+dt
        while self.state != 'done':
            deadline = self.rules['intro_s'] if self.state == 'intro' else math.inf
            when = min(deadline, self.next_wave)
            if when > end+1e-9:
                break
            self.time = when
            if deadline <= when+1e-9:
                self._start_stage(1)
            if self.next_wave <= when+1e-9:
                cap = self.rule['squid_cap']

                allowed = self.squid_count+2 <= cap and not (
                    self.stage == 5 and self.squid_count >= 8)
                if allowed:
                    self.squid_count += 2
                    self.wave_count += 1
                    self._emit('summon_pair', stage=self.stage)
                period = (self.rule['wave_period_s'] if self.state == 'fighting'
                          else self.rules.get('intro_wave_period_s', 6.))
                self.next_wave = when+period
        self.time = end
        result, self.pending = self.pending, []
        return result

    def status(self):
        return dict(map=self.name, state=self.state, time=round(self.time, 3),
                    stage=self.stage, hp=self.hp, ready_for_next_hit=self.ready,
                    stage_age_s=round(self.time-self.stage_started, 3),
                    damage=self.damage, stage_damage=self.stage_damage,
                    squid_count=self.squid_count, waves=self.wave_count)
