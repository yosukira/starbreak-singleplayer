"""Live jungle boss encounter clock.

The controller owns the boss sweep cycle (4 speed tiers, lane changes
off-screen), the time+damage phase gates and the per-phase spawn cadence.
It never touches the client directly: the session turns its events into
native creates, MOVE deltas and sounds.
"""
import math
import random

from . import params
from .scene import JungleBossPack

class JungleBossController:
    def __init__(self, pack=None, seed=None, players=1):
        self.pack = pack or JungleBossPack()
        self.seed = 0x4A554E47 if seed is None else int(seed)
        self.rng = random.Random(self.seed)
        self.players = max(1, int(players))
        self.reset()

    def reset(self):
        self.state = 'dormant'
        self.phase = 1
        self.time = 0.0
        self.phase_started = 0.0
        self.damage_total = 0.0
        self.phase_damage = 0.0
        self.pass_index = 0
        self.lane = float(self.pack.scene.get('first_lane_mp') or params.FIRST_LANE_Y_MP)
        self.dir = -1
        self.span = [float(v) for v in (self.pack.scene.get('sweep_mp') or params.SWEEP_X_MP)]
        self.x = self.span[1]
        self.pass_t = 0.0
        self.pass_dur = self._pass_duration()
        self.dwell_t = 0.0
        self.hold_phase = 1
        self.kill_unlocked = False
        self.kill_armed = False

        self.hp = float(params.SOLO_HP) * float(params.COOP_HP_MULTIPLIER)
        self.worms = {int(w['head']) for w in self.pack.scene.get('initial_worms') or []}
        self.queue = []
        self.counts = dict(snake_waves=0, skitter_waves=0, fireshot_volleys=0, passes=0, phases=1)
        self._next_snake = float('inf')

        self._next_skitter = float('inf')
        self._next_fireshot = self._draw_fireshot()

    def begin(self):
        return []

    def _draw(self, table):
        rate = float(table[params.phase_index(self.phase)])
        if rate <= 0:
            return float('inf')
        return self.time + self.rng.expovariate(rate)

    def _draw_fireshot(self):
        """Remaining initial cooldown; later rounds are measured fixed clocks."""
        return self.time+self.rng.uniform(0.,params.FIRESHOT_COOLDOWN_S)

    def _pass_duration(self):
        return abs(self.span[1] - self.span[0]) / float(params.phase_speed(self.phase))

    def add_damage(self, value, objid=None):
        value = max(0., float(value))
        self.damage_total += value
        self.phase_damage += value

        pools = getattr(params, 'PHASE_POOLS', None)
        if (value > 0 and self.phase == 4 and self.kill_unlocked
                and pools and self.phase_damage >= float(pools[3])):
            self.kill_armed = True
        return self.damage_total

    def defeated(self):

        pools = getattr(params, 'PHASE_POOLS', None)
        if pools:

            return (self.phase == 4 and self.kill_unlocked and self.kill_armed
                    and self.phase_damage >= float(pools[3]))
        return self.phase == 4 and self.damage_total >= self.hp

    def remaining_hp(self):
        pools=params.PHASE_POOLS
        index=params.phase_index(self.phase)
        return sum(pools[index+1:])+max(0.,pools[index]-self.phase_damage)

    def gate_open(self):
        index = params.phase_index(self.phase)
        need_t = params.PHASE_TIME_S[index]
        pools = getattr(params, 'PHASE_POOLS', None) or params.PHASE_GATES
        need_d = pools[index]
        if need_t is None or need_d is None:
            return False
        return self.time - self.phase_started >= float(need_t) and self.phase_damage >= float(need_d)

    def npc_died(self, objid):
        """Initial eye worms gate the encounter; waves do not."""
        if objid in self.worms:
            self.worms.discard(objid)
            if not self.worms and self.state == 'dormant':
                return self.activate()
        return []

    def activate(self):
        self.state = 'sweep'
        self.kill_unlocked = False
        self.kill_armed = False

        self.damage_total = 0.0
        self.phase_damage = 0.0
        self.phase_started = self.time
        self._next_snake = self._draw(params.SNAKE_WAVE_RATE)+params.SNAKE_OPENING_DELAY_S
        self._next_skitter = float('inf')
        self._next_fireshot = self._draw_fireshot()
        self.x = self.span[1]
        self.dir = -1
        self.pass_t = 0.0
        self.pass_dur = self._pass_duration()
        self.counts['passes'] += 1
        self.pass_index = self.counts['passes']
        return [dict(kind='boss_activate', lane=self.lane),
                self._pass_event()]

    def _pass_event(self):
        end_x = self.span[0] if self.dir < 0 else self.span[1]
        return dict(kind='boss_pass', start=(self.x, self.lane), end=(end_x, self.lane),
                    duration_s=self.pass_dur, dir=-1 if self.dir < 0 else 1)

    def choose_lane(self, player_y=None, player_ys=None):
        """Roll a 50% player preference while keeping the mandatory gap.

        Inputs are desired body-top lanes (the session aligns the body centre
        with the player). The 1 -> 2 transition alone is exempt. Projection
        onto legal intervals also keeps the gap when all players stay on the
        old height; an unbounded random retry is never needed.
        """
        lo, hi = (getattr(params, 'LANE_RANGE_MP', None)
                  or self.pack.scene.get('lane_mp') or params.LANE_Y_MP)
        if self.pass_index < 2:
            intervals = [(lo, hi)]
        else:
            gap = params.MIN_LANE_GAP_MP
            intervals = [(math.ceil(a), math.floor(b)) for a, b in
                         ((lo, min(hi, self.lane-gap)), (max(lo, self.lane+gap), hi))
                         if math.ceil(a) <= math.floor(b)]
        if not intervals:
            raise ValueError('boss lane band cannot satisfy the mandatory lane gap')
        heights = player_ys if player_ys is not None else ([] if player_y is None else [player_y])
        heights = [float(y) for y in heights if y is not None and math.isfinite(float(y))]
        if heights and self.rng.random() < params.LANE_PLAYER_CHANCE:

            candidates = set()
            for height in heights:
                projected = [min(max(height, a), b) for a, b in intervals]
                distance = min(abs(y-height) for y in projected)
                candidates.update(y for y in projected if abs(y-height) == distance)
            scores = {y: (sum(abs(height-y) <= params.LANE_PLAYER_GROUP_MP for height in heights),
                          -min(abs(height-y) for height in heights)) for y in candidates}
            best = max(scores.values())
            self.lane = float(self.rng.choice(sorted(y for y in candidates if scores[y] == best)))
        else:

            draw = self.rng.random() * sum(b-a for a, b in intervals)
            self.lane = float(intervals[-1][1])
            for a, b in intervals:
                if draw <= b-a:
                    self.lane = float(a+draw)
                    break
                draw -= b-a

        self.lane = float(math.floor(self.lane))
        return self.lane

    def step(self, dt, player_y=None, player_ys=None):
        if self.state == 'defeated' or dt <= 0:
            return []
        self.time += dt
        events = []
        if self.state == 'dormant':
            return events
        if self.state == 'sweep':
            self.pass_t += dt
            fraction = min(1., self.pass_t/self.pass_dur)
            self.x = (self.span[1]+(self.span[0]-self.span[1])*fraction if self.dir < 0
                      else self.span[0]+(self.span[1]-self.span[0])*fraction)
            if self.pass_t >= self.pass_dur:
                self.x = self.span[0] if self.dir < 0 else self.span[1]
                self.state = 'dwell'
                self.dwell_t = 0.0
                self.hold_phase = self.phase
                events.append(dict(kind='boss_hold', lane=self.lane))
            events.extend(self._spawn_events(skitter=True))
        elif self.state == 'dwell':
            self.dwell_t += dt
            if self.dwell_t >= float(params.phase_dwell(self.hold_phase)):

                if self.phase == 4 and self.gate_open():
                    self.kill_unlocked = True
                if self.phase < 4 and self.gate_open():
                    self.phase += 1
                    self.phase_damage = 0.0
                    self.kill_armed = False
                    self.phase_started = self.time
                    self.counts['phases'] = self.phase
                    self._next_snake = self._draw(params.SNAKE_WAVE_RATE)
                    self._next_skitter = float('inf')

                    events.append(dict(kind='phase', phase=self.phase))
                self.choose_lane(player_y, player_ys=player_ys)
                self.dir = 1 if self.dir < 0 else -1
                self.pass_t = 0.0
                self.pass_dur = self._pass_duration()
                self.state = 'sweep'
                self.counts['passes'] += 1
                self.pass_index = self.counts['passes']
                events.append(self._pass_event())
            events.extend(self._spawn_events(skitter=False))
        if self.defeated() and self.state != 'defeated':
            self.state = 'defeated'
            events.append(dict(kind='boss_defeated'))
        return events

    def _spawn_events(self, skitter=True):
        events = []
        while skitter and self.time >= self._next_skitter:

            events.append(dict(kind='skitter_wave'))
            self.counts['skitter_waves'] += 1
            self._next_skitter = float('inf')
        while self.time >= self._next_snake:
            events.append(dict(kind='snake_wave',count=params.SNAKE_WAVE_SIZE[params.phase_index(self.phase)]))
            self.counts['snake_waves'] += 1
            self._next_snake = self._draw(params.SNAKE_WAVE_RATE)
        while self.time >= self._next_fireshot:
            index=params.phase_index(self.phase)
            aimed=self.rng.random()<params.FIRESHOT_AIM_CHANCE[index]
            count=1 if aimed else self.rng.randint(*params.FIRESHOT_BURST_SIZE[index])
            events.append(dict(kind='fireshot_volley',aimed=aimed,count=count))
            self.counts['fireshot_volleys'] += 1
            self._next_fireshot += params.FIRESHOT_COOLDOWN_S+(0. if aimed else count*params.FIRESHOT_RUN_GAP_S)
        return events

    def status(self):
        return dict(state=self.state, phase=self.phase, time=round(self.time, 3),
                    lane_px=round(self.lane / 256, 1), x_px=round(self.x / 256, 1),
                    dir=self.dir, pass_index=self.pass_index,
                    hp=round(self.hp), damage=round(self.damage_total),
                    remaining_hp=round(self.remaining_hp()),
                    phase_damage=round(self.phase_damage),
                    gate_open=self.gate_open(), worms_left=len(self.worms),
                    counts=dict(self.counts))
