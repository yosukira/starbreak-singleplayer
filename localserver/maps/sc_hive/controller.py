"""Headless Hive clock: six staggered emitters, HP refills and death."""
import math

from .. import MapController
from .scene import ScHivePack

class ScHiveController(MapController):
    name = 'sc_hive'

    def __init__(self, pack=None, sink=None):
        self.pack = pack or ScHivePack()
        self.sink = sink
        self.prepare()

    def prepare(self):
        self.time = 0.
        self.state = 'fighting'
        self.hp = float(self.pack.entities['hp']['initial'])
        self.next_refill = self.pack.entities['hp']['first_refill_s']
        self.kill_unlocked = False
        self.door_at = None
        self.pending = []
        self.event_count = 0
        self.spawn_count = 0
        self.refill_count = 0
        self.damage_after_unlock = 0.
        lead = self.pack.entities['spawner']['emit_ms']/1000.
        self.emitters = []
        for spec in self.pack.spawners:
            if spec['period_s'] <= 0:
                raise ValueError('spawner period must be positive')
            first = float(spec['first_spawn_s'])
            self.emitters.append(dict(spec, next_spawn=first,
                next_summon=max(0., first-lead),
                anim_pos_ms=max(0, int(round((lead-first)*1000.)))))

    def _emit(self, kind, **values):
        row = dict(values, kind=kind, at=self.time)
        self.pending.append(row)
        self.event_count += 1
        if self.sink:
            self.sink(row)

    def add_damage(self, value, objid=None):
        if self.state != 'fighting' or objid != self.pack.overlay_id or self.hp <= 0:
            return
        value = float(value or 0)
        if not math.isfinite(value) or value <= 0:
            return

        self.hp = max(0. if self.kill_unlocked else 1., self.hp-value)
        if self.kill_unlocked:
            self.damage_after_unlock += value
        if self.hp == 0:
            if self.kill_unlocked:
                self.state = 'cleared'
                self.door_at = self.time + self.pack.entities['death']['door_delay_s']
                self._emit('hive_destroy', overlay=self.pack.overlay_id,
                           hive=self.pack.hive_id,
                           spawners=[row['objid'] for row in self.emitters])
        else:
            self._emit('hive_hp', hp=int(self.hp))

    def step(self, dt):
        dt = float(dt)
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        end = self.time+dt
        while self.state == 'fighting':
            when = min([self.next_refill] + [row[key]
                for row in self.emitters for key in ('next_spawn', 'next_summon')])
            if when > end+1e-9:
                break
            self.time = when
            if self.next_refill <= when+1e-9:
                rule = self.pack.entities['hp']
                self.hp = float(rule['refill'])
                self.refill_count += 1
                self.kill_unlocked = when >= rule['last_refill_s']-1e-9
                if self.kill_unlocked:
                    self.damage_after_unlock = 0.
                self._emit('hive_refill', hp=int(self.hp), kill_unlocked=self.kill_unlocked)
                self.next_refill = (math.inf if self.kill_unlocked else
                                    self.next_refill + rule['refill_period_s'])
            for row in self.emitters:
                if row['next_summon'] <= when+1e-9:
                    self._emit('summon', spawner=row['objid'],
                               anim_pos_ms=row['anim_pos_ms'])
                    row['next_summon'] += row['period_s']
                    row['anim_pos_ms'] = 0
                if row['next_spawn'] <= when+1e-9:
                    self.spawn_count += 1
                    self._emit('spirit_spawn', spawner=row['objid'])
                    row['next_spawn'] += row['period_s']
        if self.state == 'cleared' and self.door_at <= end+1e-9:
            self.time = self.door_at
            self._emit('doors_open')
            self.state = 'done'
        self.time = end
        result, self.pending = self.pending, []
        return result

    def status(self):
        return dict(map=self.name, state=self.state, time=round(self.time, 3),
                    hp=round(self.hp, 1), refills=self.refill_count,
                    kill_unlocked=self.kill_unlocked,
                    damage_after_unlock=self.damage_after_unlock,
                    spirits_spawned=self.spawn_count, events=self.event_count)
