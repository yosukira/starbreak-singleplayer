'Crossbeast decisions and fire propagation; no runtime timeline at runtime.'
import copy
import math
import random
from .scene import IceMidPack
from ..ice_boss.controller import overlap

class IceMidController:
    name = 'ice_mid'
    STEP = 1/120

    def __init__(self, pack=None, seed=0):
        self.pack = pack or IceMidPack(seed=seed)
        self.seed = seed
        self.prepare()

    def prepare(self):
        self.rng = random.Random(self.seed)
        self.time = self.remainder = 0.
        self.state = 'waiting'
        self.players = {}; self.events = []; self.actors = {}; self.rays = []
        self.retired = set(); self._next_id = 20000
        p = self.pack.templates['39']
        self.boss = dict(id=39, pos=list(self.pack.rect(p)[:2]), size=[224, 144], facing=180, pose=4)
        self.damage = 0; self.started_at = None
        self.phase = 1; self.phase_started = None; self.phase_history = []
        self.shots = self.volleys = self.splits = 0
        self.pending = []; self.next_sync = 0.; self.until = math.inf

    def emit(self, kind, **fields):
        self.events.append(dict(kind=kind, at=self.time, **copy.deepcopy(fields)))

    def drain(self):
        result, self.events = self.events, []
        return result

    def set_players(self, players):
        self.players = {k: [v/256 for v in p['center']] for k, p in players.items()
                        if p.get('alive', True) and len(p.get('center', [])) == 2
                        and all(math.isfinite(v) for v in p['center'])}

    def start(self):
        if self.state == 'waiting' and self.players:
            self.started_at = self.time
            self.phase_started = self.time
            self._state('walk'); self.emit('start')

    def _pose(self, pose):
        if self.boss['pose'] != pose:
            self.boss['pose'] = pose; self.emit('props', id=39, values=dict(POSE=pose))

    def _state(self, state):
        self.state = state
        self._pose({'walk': 5, 'attack': 4, 'up': 3, 'down': 1, 'land': 4}.get(state, 4))
        self.emit('state', state=state, pos=self.boss['pos'], facing=self.boss['facing'])

    def _support(self, y, x=None):
        x = self.boss['pos'][0] if x is None else x
        return next(((a, b) for a, b in self.pack.runs.get(round(y), [])
                     if a-.01 <= x and x+224 <= b+.01), None)

    def _vertical_options(self):
        x, y = self.boss['pos']; feet = y+144
        result = {'walk': None}
        for mode, destination in (('up', feet-160), ('down', feet+160)):
            if not self._support(destination):
                continue
            rect = (x, min(y, destination-144), 224, abs(destination-feet)+144)
            if not any(overlap(rect, s) for s in self.pack.solids):
                result[mode] = destination
            elif mode == 'down' and destination == self.pack.scene['floor']:
                result[mode] = destination
        return result

    def _choose_move(self):
        options = self._vertical_options()
        mode = self.rng.choice(list(options))
        self.emit('choice', choice=mode, available=list(options))
        if mode == 'walk':
            self._state('walk')
        else:
            self.landing_y = options[mode]-144
            self.vertical_v = -self.pack.entities['boss']['jump_v_px_s'] if mode == 'up' else 0.
            self._state(mode)

    def _edge_attack(self):
        self.boss['facing'] = 180 if self.boss['facing'] == 0 else 0
        self.emit('props', id=39, values=dict(FACING=self.boss['facing']))
        self._state('attack'); self.volleys += 1
        p = self.pack.entities['boss']
        self.until = self.time+p['attack_s']
        self.pending = [self.time+p['first_shot_s']+i*p['shot_gap_s'] for i in range(3)]
        self.emit('volley', count=3)

    def _new(self, vid, pos, **fields):
        self._next_id += 1
        p = self.pack.props(vid)
        actor = dict(id=self._next_id, vid=vid, pos=list(pos), size=list(self.pack.rect(p)[2:]),
                     facing=0, pose=1, born=self.time, expiry=self.time+p['lifetime']/1000, kind='fire')
        actor.update(fields); self.actors[actor['id']] = actor
        self.emit('create', actor=actor)
        return actor

    def _remove(self, actor, reason='INTERNAL'):
        if actor['id'] in self.retired:
            return
        self.retired.add(actor['id']); self.actors.pop(actor['id'], None)
        self.emit('remove', actor=actor, reason=reason)

    def _throw(self):
        p = self.pack.entities['fire']; x, y = self.boss['pos']

        start = [x+96, y-16]
        target = self.rng.choice(list(self.players.values())) if self.players else [x+112, y+72]
        flight = self.rng.uniform(1.4, 2.5)
        vx = max(-p['seed_vx_limit'], min(p['seed_vx_limit'], (target[0]-start[0]-16)/flight+self.rng.uniform(-80, 80)))
        vy = (target[1]-start[1]-16-.5*p['gravity_px_s2']*flight*flight)/flight+self.rng.uniform(-120, 40)
        vy = max(-p['seed_up_max'], min(-p['seed_up_min'], vy))

        layers = [s for s in self.pack.ledges if s[0] <= target[0] <= s[0]+s[2]]
        layer = min(layers, key=lambda s: abs(s[1]-target[1]))[1] if layers else None
        if self.rng.random() < .5:
            layer = None
        actor = self._new('ice.crossseed', start, kind='seed', velocity=[vx, vy], anchor_y=layer, owner=39)
        self.emit('sound', id=actor['id'], name=self.pack.entities['sounds']['seed'])
        self.shots += 1; self.emit('shot', id=actor['id'], target=target, velocity=[vx, vy], anchor_y=layer)

    def _split(self, actor, reason):
        if actor['id'] in self.retired:
            return
        centre = [actor['pos'][0]+16, actor['pos'][1]+16]
        self._remove(actor, reason)
        fx = self._new('ice.crossx', [centre[0]-24, centre[1]-24])
        self.emit('sound', id=fx['id'], name=self.pack.entities['sounds']['split'])
        p = self.pack.entities['fire']
        self.rays.append(dict(centre=centre, radius=p['first_distance_px'],
                              next=self.time+p['split_period_s'],
                              until=self.time+p['ray_life_s'], directions=[0, 90, 180, 270]))
        self.splits += 1; self.emit('split', id=actor['id'], centre=centre, reason=reason)

    def _fire_tick(self, dt):
        p = self.pack.entities['fire']
        for actor in list(self.actors.values()):
            if actor['kind'] != 'seed':
                if self.time+1e-8 >= actor['expiry']:
                    self._remove(actor, 'LIFETIME')
                continue
            old = actor['pos'][:]; vx, vy = actor['velocity']
            actor['pos'][0] += vx*dt
            actor['pos'][1] += vy*dt+.5*p['gravity_px_s2']*dt*dt
            actor['velocity'][1] += p['gravity_px_s2']*dt
            x, y = actor['pos']
            blocked = any(overlap((x, y, 32, 32), box) for box in self.pack.solids)
            if not blocked and actor['anchor_y'] is not None and actor['velocity'][1] >= 0:
                for sx, sy, sw, sh in self.pack.ledges:
                    if sy == actor['anchor_y'] and old[1]+32 <= sy <= y+32 and x+32 > sx and x < sx+sw:
                        actor['pos'][1] = sy-32; blocked = True; break
            if blocked:
                self._split(actor, 'BLOCKED')
            elif self.time+1e-8 >= actor['expiry']:
                self._split(actor, 'LIFETIME')
        for ray in list(self.rays):
            if self.time+1e-8 < ray['next']:
                continue
            ray['next'] += p['split_period_s']
            cx, cy = ray['centre']; radius = ray['radius']
            for direction in list(ray['directions']):
                dx, dy = {0:(1,0), 90:(0,1), 180:(-1,0), 270:(0,-1)}[direction]
                pos = [cx+dx*radius-24, cy+dy*radius-24]

                if any(overlap((*pos, 48, 48), box) for box in self.pack.solids):
                    ray['directions'].remove(direction)
                else:
                    self._new('ice.crosspart', pos, facing=direction)
            ray['radius'] += p['step_px']
            if not ray['directions'] or self.time >= ray['until']:
                self.rays.remove(ray)

    def _boss_tick(self, dt):
        p = self.pack.entities['boss']; x, y = self.boss['pos']
        if self.state == 'walk':
            support = self._support(y+144)
            if support is None:

                self._edge_attack(); return
            lo, hi = support; sign = 1 if self.boss['facing'] == 0 else -1
            nx = max(lo, min(hi-224, x+sign*p['speed_px_s']*dt))
            self.boss['pos'][0] = nx
            if nx <= lo+.001 and sign < 0 or nx >= hi-224-.001 and sign > 0:
                self._edge_attack()
        elif self.state == 'attack':
            while self.pending and self.time+1e-8 >= self.pending[0]:
                self.pending.pop(0); self._throw()
            if self.time+1e-8 >= self.until:
                self._choose_move()
        elif self.state in ('up', 'down'):
            old_v = self.vertical_v
            self.boss['pos'][1] += old_v*dt+.5*p['gravity_px_s2']*dt*dt
            self.vertical_v += p['gravity_px_s2']*dt
            if old_v < 0 <= self.vertical_v:
                self._pose(1)
            if self.vertical_v > 0 and self.boss['pos'][1] >= self.landing_y:
                self.boss['pos'][1] = self.landing_y
                self._state('land'); self.until = self.time+.05
        elif self.state == 'land' and self.time+1e-8 >= self.until:
            self._state('walk')

    def add_damage(self, value, objid=None, player_id=None):
        if objid != 39 or self.state in ('waiting', 'dying', 'done') or not math.isfinite(value) or value <= 0:
            return []
        p = self.pack.entities['boss']
        self.damage = min(p['required_damage'], self.damage+value)
        self.emit('props', id=39, values=dict(HITPOINTS=max(1, p['required_damage']-self.damage)))
        return self.drain()

    def _phase_tick(self):
        p = self.pack.entities['boss']
        if self.damage < p['required_damage'] or self.time+1e-8 < self.phase_started+p['phase_min_s']:
            return
        self.phase_history.append(dict(phase=self.phase, start=self.phase_started, end=self.time,
                                       elapsed=self.time-self.phase_started, damage=self.damage))
        if self.phase == p['phases']:
            self._die()
        else:
            self.phase += 1; self.phase_started = self.time; self.damage = 0
            self.emit('props', id=39, values=dict(HITPOINTS=p['required_damage']))
            self.emit('refill', phase=self.phase, hitpoints=p['required_damage'])

    def native_destroy(self, oid, reason):

        actor = self.actors.get(oid)
        if actor and reason == 3:
            self._remove(actor, 'SHIELD')
        return self.drain()

    def _die(self):
        self._state('dying'); self.pending = []; self.rays = []
        for actor in list(self.actors.values()):
            self._remove(actor)
        self.until = self.time+self.pack.entities['boss']['death_s']
        self.emit('death_begin', id=39)

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('invalid dt')
        if self.state == 'waiting':
            return self.drain()
        self.remainder += dt
        while self.remainder+1e-10 >= self.STEP:
            self.remainder -= self.STEP; self.time += self.STEP
            if self.state in ('waiting', 'done'):
                continue
            if self.state == 'dying':
                if self.time+1e-8 >= self.until:
                    self._state('done'); self.emit('victory')
                continue
            self._phase_tick()
            if self.state == 'dying':
                continue
            self._boss_tick(self.STEP); self._fire_tick(self.STEP)
            if self.time+1e-8 >= self.next_sync:
                self.next_sync = self.time+.05
                self.emit('move', id=39, pos=self.boss['pos'])
                for actor in self.actors.values():
                    if actor['kind'] == 'seed':
                        self.emit('move', id=actor['id'], pos=actor['pos'])
        return self.drain()

    def status(self):
        return dict(state=self.state, time=self.time, boss=self.boss, damage=self.damage,
                    phase=self.phase, phase_started=self.phase_started, phase_history=self.phase_history,
                    shots=self.shots, volleys=self.volleys,
                    splits=self.splits, actors=len(self.actors), rays=len(self.rays))
