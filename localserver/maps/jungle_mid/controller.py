"""Thirteen damage gates; wave/recovery and victory are relative to actual hits."""
import heapq
import math
import random

from .. import MapController
from .scene import JungleMidPack

class JungleMidController(MapController):
    name = 'jungle_mid'

    def __init__(self, pack=None, seed=0):
        self.pack = pack or JungleMidPack()
        self.seed = seed
        self.prepare()

    def prepare(self):
        self.rng = random.Random(self.seed)
        self.time, self.broken, self.damage_total = 0., 0, 0.
        self.hp = float(self.pack.entities['mouth']['hp_per_bar'])
        self.state, self.started = 'idle', False
        self.queue, self.sequence = [], 0
        self.cocoons = False

    def begin(self):
        if self.started:
            return []
        self.started = True
        return [dict(kind='wave', at=self.time, ring=0)]

    def _schedule(self, at, kind, **values):
        self.sequence += 1
        heapq.heappush(self.queue, (at, self.sequence, dict(kind=kind, at=at, **values)))

    def add_damage(self, value, objid=None):
        if not math.isfinite(value) or value <= 0 or not self.started or self.state not in ('idle', 'sweep'):
            return []
        taken = min(float(value), self.hp)
        self.hp -= taken
        self.damage_total += taken
        if self.hp > 0:
            return []
        self.broken += 1
        config = self.pack.entities['mouth']
        self.state = 'finishing' if self.broken == config['bars'] else 'recovering'
        self.hp = float(config['hp_per_bar'])
        self._schedule(self.time + config['wave_delay_s'], 'wave', ring=self.broken)
        self._schedule(self.time + self.pack.entities['movement']['reaction_s'], 'motion')
        if self.broken >= self.pack.entities['cocoons']['unlock_bars'] and not self.cocoons:
            self.cocoons = True
            self._schedule(self.time+self.rng.uniform(*self.pack.entities['cocoons']['first_cast_delay_s']), 'egg_cast')
        if self.state == 'finishing':
            self._schedule(self.time + config['finish_delay_s'], 'victory')
        else:
            self._schedule(self.time + config['recovery_s'], 'recover')
        return [dict(kind='bar_break', at=self.time, bar=self.broken)]

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        end, events = self.time + dt, []
        while self.queue and self.queue[0][0] <= end + 1e-9:
            at, _, event = heapq.heappop(self.queue)
            if self.state == 'defeated':
                continue
            if event['kind'] == 'recover':
                self.hp = float(self.pack.entities['mouth']['hp_per_bar'])
                self.state = 'sweep' if self.broken >= 2 else 'idle'
            elif event['kind'] == 'victory':
                self.state = 'defeated'
            elif event['kind'] == 'egg_cast':
                if self.state == 'finishing':
                    continue
                self._schedule(at+self.pack.entities['cocoons']['cast_windup_s'], 'egg')
                self._schedule(at+self.pack.entities['movement']['reaction_s'], 'motion')
                self._schedule(at+self.rng.uniform(*self.pack.entities['cocoons']['interval_s']), 'egg_cast')
            events.append(event)
        self.time = end
        return events

    def status(self):
        bars = self.pack.entities['mouth']['bars']
        return dict(state=self.state, time=self.time, broken_bars=self.broken,
                    bars_left=bars-self.broken, bar_hp=self.hp, damage_total=self.damage_total,
                    cocoon_gate=dict(bars=self.pack.entities['cocoons']['unlock_bars'], enabled=self.cocoons))
