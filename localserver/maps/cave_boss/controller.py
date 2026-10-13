"""Live Cave encounter, pulse insects and terrain physics. Coordinates are px."""
import math
import random
from collections import Counter
from .. import MapController
from .scene import CaveBossPack

def overlap(a, b):
    return a[0] < b[0] + b[2] and a[0] + a[2] > b[0] and a[1] < b[1] + b[3] and a[1] + a[3] > b[1]

def sweep_box(start, end, size, wall):
    """First contact of a flying box with a solid slab, including corners."""
    enter,leave=0.,1.
    for a,b,c,w,s in zip(start,end,wall[:2],wall[2:],size):
        delta=b-a;lo,hi=c-s,c+w
        if abs(delta)<1e-12:
            if a<=lo or a>=hi:return None
            continue
        t0,t1=(lo-a)/delta,(hi-a)/delta
        enter=max(enter,min(t0,t1));leave=min(leave,max(t0,t1))
        if enter>leave:return None
    return enter if 0<=enter<=1 and leave>0 else None

class CaveBossController(MapController):
    name = 'cave_boss'

    def __init__(self, pack=None, seed=0):
        self.pack = pack or CaveBossPack()
        self.seed = int(seed)
        self.rules = self.pack.entities
        self.prepare()

    def prepare(self):
        self.rng = random.Random(self.seed)
        self.time = 0.; self.state = 'waiting'; self.players = {}; self.events = []
        self.weak_round = 0; self.body_number = 0; self.deadline = math.inf
        self.body_pos = list(self.pack.scene['body_start'])
        self.mouths = {}; self.actors = {}; self.retired = set(); self._next_id = 2000
        self.hives_due = 1.15; self.hives_spawned = False; self.next_roller = math.inf
        self.next_explosion = 0.; self.roller_started = False
        self.stats = dict(insects=0, rollers=0, sprays=0, pools=0, consumed=0, attacks=0)

    def emit(self, kind, **values):
        self.events.append(dict(kind=kind, at=self.time, **values))

    def drain(self):
        out, self.events = self.events, []
        return out

    def set_players(self, players):

        self.players = {k: [c / 256 for c in p['center']] for k, p in players.items() if p.get('alive', True)}

    def start(self):
        if self.state == 'waiting':
            self.state = 'intro'; self.deadline = self.time + .1

    def _new(self, kind, vid, pos, **fields):
        self._next_id += 1
        template = self.pack.props(vid)
        initial_velocity=fields.pop('initial_velocity',[0.,0.])
        row = dict(id=self._next_id, kind=kind, vid=vid, pos=list(pos), vel=list(initial_velocity),
                   size=[template.get('wmp', 0) / 256, template.get('hmp', 0) / 256],
                   hp=template.get('hitpoints'), born=self.time, next_sync=self.time + .05,
                   expiry=self.time + template['lifetime'] / 1000 if 'lifetime' in template else math.inf,
                   children=set(), **fields)
        self.actors[row['id']] = row
        self.emit('create', actor=dict(row))
        return row

    def _remove(self, row, reason='INTERNAL', native=False):
        oid = row['id']
        if oid in self.retired:
            return
        self.retired.add(oid); self.actors.pop(oid, None)
        parent = self.actors.get(row.get('parent'))
        if parent:
            parent['children'].discard(oid)
        self.emit('remove', id=oid, reason=reason, native=native, actor=dict(row))

    def _effect(self, pos):
        self._new('effect', 'cave.bossx', pos)

    def _round(self, number):
        self.weak_round = number
        self.state = 'weak' if number <= self.rules['boss']['weak_rounds'] else 'final_wait'
        chosen = {self.rng.randrange(4), self.rng.randrange(4, 8)} if self.state == 'weak' else set()
        first = number == 1
        for index, slot in enumerate(self.pack.scene['slots']):
            weak = index in chosen
            hp = self.rules['boss']['first_hp' if first else 'later_hp']
            armor = self.rules['boss']['first_armor' if first else 'later_armor']
            pos = [slot['x'] - slot['side'] * 220, slot['y']]
            duration = self.rng.uniform(.5, 1.1) if first or not weak else self.rng.uniform(1.7, 2.2)
            mouth = dict(slot, pos=pos, home=[slot['x'], slot['y']], hp=hp, maxhp=hp, armor=armor,
                         weak=weak, action='enter', action_at=self.time, duration=duration, origin=list(pos),
                         next_sync=self.time + .05, amplitude=self.rng.uniform(10, 110), next_attack=math.inf,
                         dead=False, visible=True)
            self.mouths[slot['id']] = mouth
            self.emit('mouth_round', mouth=dict(mouth), initial=first)
        self.emit('round', number=number, targets=[m['slot'] for m in self.mouths.values() if m['weak']])
        if self.state == 'final_wait':
            self.deadline = self.time + self.rng.uniform(*self.rules['boss']['final_wait_s'])

    def _body(self):
        self.body_number += 1; self.state = 'body'; self.body_at = self.time
        leg = self.rules['boss']['slow_leg_s' if self.body_number <= 3 else 'fast_leg_s']
        self.body_leg = leg
        self.deadline = self.time + leg * (1 if self.body_number == 5 else 2)
        if self.body_number <= 3:
            for mouth in self.mouths.values():
                mouth.update(action='hidden', hp=0, visible=False)
                self.emit('mouth_hide', id=mouth['id'])
        self.emit('body_move', number=self.body_number, up_s=leg,
                  down_s=None if self.body_number == 5 else leg)

    def _phase(self):
        r = self.rules['boss']
        if self.state == 'intro' and self.time >= self.deadline:
            self._round(1)
        elif self.state == 'weak' and all(m['dead'] for m in self.mouths.values() if m['weak']):
            self.state = 'transition'; self.transition_at = self.time
            self.deadline = self.time + r['last_weak_delay_s']
            self.emit('weak_pair_defeated', number=self.weak_round)
        elif self.state == 'transition' and self.time >= self.deadline:
            self._body()
        elif self.state == 'body':
            t = self.time - self.body_at; leg = self.body_leg
            a, b = self.pack.scene['body_start'], self.pack.scene['body_top']
            factor = min(1., t / leg) if t <= leg else max(0., 1 - (t - leg) / leg)
            self.body_pos = [a[j] + (b[j] - a[j]) * factor for j in range(2)]
            if self.time >= self.deadline - 1e-9:
                if self.body_number <= 3:
                    self.body_pos = list(a)
                    self.emit('body_return', number=self.body_number)
                    self._round(self.body_number + 1)
                    if self.body_number == 2:
                        self.roller_started = True
                        self.next_roller = self.time + self.rng.uniform(*self.rules['roller']['first_delay_s'])
                elif self.body_number == 4:
                    self.state = 'final_wait_2'; self.body_pos = list(a)
                    self.deadline = self.time + self.rng.uniform(*r['final_wait_after_sweep_s'])
                    self.emit('body_return', number=4)
                else:
                    self.state = 'death'; self.death_at = self.time; self.deadline = self.time + r['death_s']
                    self.emit('death_start')
        elif self.state in ('final_wait', 'final_wait_2') and self.time >= self.deadline:
            self._body()
        elif self.state == 'death':
            progress = min(1., (self.time - self.death_at) / r['death_s'])
            a, b = self.pack.scene['body_top'], self.pack.scene['body_start']
            self.body_pos = [a[0] + math.sin(progress * 180) * 5, a[1] + (b[1] - a[1]) * progress]
            if self.time >= self.next_explosion:
                self.next_explosion = self.time + .2
                for _ in range(4):
                    self._effect([self.body_pos[0] + self.rng.uniform(0, 400), self.body_pos[1] + self.rng.uniform(0, 500)])
                for m in self.mouths.values():
                    self._effect([m['pos'][0] + self.rng.uniform(0, 160), m['pos'][1] + self.rng.uniform(0, 120)])
                self.emit('death_move', pos=list(self.body_pos))
            if self.time >= self.deadline:
                self.state = 'done'
                for row in list(self.actors.values()):
                    self._remove(row)
                self.emit('victory')

    def _mouths(self):
        r = self.rules['mouth']
        for mouth in self.mouths.values():
            action = mouth['action']; elapsed = self.time - mouth['action_at']; home = mouth['home']
            if self.state == 'transition' and self.time - self.transition_at >= 2.5 and action not in ('hidden', 'hide'):
                mouth.update(action='hide', action_at=self.time, origin=list(mouth['pos']), duration=.5)
                action = 'hide'; elapsed = 0.
            if action == 'hidden':
                continue
            if action == 'dying':
                if self.time >= mouth.get('next_fx', 0.):
                    mouth['next_fx'] = self.time + .2
                    self._effect([mouth['pos'][0] + self.rng.uniform(0, 150), mouth['pos'][1] + self.rng.uniform(0, 120)])
                if elapsed >= 2.:
                    mouth.update(action='hide', action_at=self.time, origin=list(mouth['pos']), duration=.5)
            elif action in ('enter', 'return', 'hide', 'windup'):
                if action == 'hide':
                    target = [home[0] - mouth['side'] * 220, home[1]]
                elif action == 'windup':
                    target = [home[0] - mouth['side'] * 200, mouth['windup_y']]
                else:
                    target = list(home)
                    if mouth['slot'] == 'R4':
                        target[1] = min(target[1], self._corner_y(160))
                factor = min(1., elapsed / mouth['duration'])
                mouth['pos'] = [a + (b - a) * factor for a, b in zip(mouth['origin'], target)]
                if factor == 1.:
                    if action == 'hide':
                        mouth.update(action='hidden', visible=False)
                        self.emit('mouth_hide', id=mouth['id'])
                    elif action == 'windup':
                        mouth.update(action='attack', action_at=self.time, origin=list(mouth['pos']),
                                     next_attack=self.time + self.rng.uniform(*r['cooldown_s']))
                        self.stats['attacks'] += 1
                        self.emit('mouth_attack', id=mouth['id'], slot=mouth['slot'], angle=mouth['angle'])
                    else:
                        mouth.update(action='idle', action_at=self.time)
                        if action == 'enter':
                            mouth['next_attack'] = self.time + self.rng.uniform(2.5, 4.)
                            self.emit('mouth_active', mouth=dict(mouth))
                            if self.weak_round > 1 and self.rng.random() < .65:
                                for _ in range(self.rng.randint(1, 3)):
                                    self._insect([mouth['pos'][0] + 85, mouth['pos'][1] + 65], mouth['id'],
                                                 0 if mouth['side'] == 1 else 180)
            elif action == 'idle':
                mouth['pos'] = [home[0], home[1] + mouth['amplitude'] * math.sin(elapsed * math.pi / 1.5)]
                if mouth['slot'] == 'R4':
                    mouth['pos'][1] = min(mouth['pos'][1], self._corner_y(160))
                if self.time >= mouth['next_attack'] and self.state not in ('transition', 'death', 'done'):
                    angle = self.rng.uniform(-10, 10) if self.rng.random() < .65 else self.rng.uniform(-r['angle_limit_deg'], r['angle_limit_deg'])
                    if mouth['slot'] == 'R4':
                        angle = -abs(angle)
                    mouth.update(action='windup', action_at=self.time, origin=list(mouth['pos']),
                                 duration=r['windup_s'], angle=angle,
                                 windup_y=min(mouth['pos'][1], home[1] - 100) if mouth['slot'] == 'R4' else mouth['pos'][1])
            elif action == 'attack':
                accel_s, brake_s = r['acceleration_s'], r['braking_s']
                peak = min(r['speed_px_s'], r['accel_px_s2'] * accel_s)
                if elapsed <= accel_s:
                    distance = .5 * peak / accel_s * elapsed ** 2
                else:
                    t = min(brake_s, elapsed - accel_s)
                    distance = .5 * peak * accel_s + peak * t - .5 * peak / brake_s * t ** 2
                angle = math.radians(mouth['angle'])
                mouth['pos'] = [mouth['origin'][0] + mouth['side'] * math.cos(angle) * distance,
                                mouth['origin'][1] + math.sin(angle) * distance]
                if elapsed >= accel_s + brake_s:
                    mouth.update(action='return', action_at=self.time, origin=list(mouth['pos']), duration=r['return_s'])
            if mouth['slot'] == 'R4' and mouth['action'] not in ('hidden', 'hide'):
                x = mouth['pos'][0]; safe = self.pack.scene['right_corner']
                if x < safe['x'] + safe['width'] and x + 220 > safe['x']:
                    mouth['pos'][1] = min(mouth['pos'][1], self._corner_y(160))
            if self.time >= mouth['next_sync']:
                mouth['next_sync'] = self.time + .05
                self.emit('mouth_move', id=mouth['id'], pos=list(mouth['pos']))

    def _corner_y(self, height):
        p = self.pack.scene['right_corner']
        return p['floor_y'] - p['clearance'] - height

    def chain_positions(self, mouth):

        home = mouth['home']; target = mouth['pos']; safe = self.pack.scene['right_corner']
        rows = []
        for percentage in (18, 36, 54, 72, 90):
            k = percentage / 100
            x = home[0] * (1 - k) + (target[0] + 30) * k
            y = home[1] * (1 - k) + target[1] * k
            if mouth['slot'] == 'R4':
                y = min(y, self._corner_y(160))
            rows.append([x, y])
        return rows

    def _spawn_hives(self):
        r = self.rules['hive']
        for anchor in self.rng.sample(self.pack.scene['hive_anchors'], r['count']):
            row = self._new('hive', anchor['vid'], [anchor['x'], anchor['y']], facing=anchor['facing'])
            row['next_spawn'] = self.time + self.rng.uniform(*r['first_spawn_s'])
        self.hives_spawned = True

    def _insect(self, pos, parent, facing):
        r = self.rules['insect']
        if sum(a['kind'] == 'insect' for a in self.actors.values()) >= r['cap']:
            return None
        heading=[math.cos(math.radians(facing)),math.sin(math.radians(facing))]
        origin=list(pos);pos=None

        for distance in range(0,260,4):
            candidate=[origin[j]+heading[j]*distance for j in range(2)]
            if not any(overlap([*candidate,48,30],wall) for wall in self.pack.solids):
                pos=candidate;break
        if pos is None:
            return None
        row = self._new('insect', 'cave.lworm', pos, parent=parent, facing=facing,
                        pulse='launch', pulse_at=self.time, duration=.5, target=None,
                        direction=self.rng.choice((-1, 1)))
        row['heading'] = heading
        if parent in self.actors:
            self.actors[parent]['children'].add(row['id'])
        self.stats['insects'] += 1
        return row

    def _fly(self, row, dt):
        r = self.rules['insect']; elapsed = self.time - row['pulse_at']
        if elapsed >= row['duration']:
            row['pulse_at'] = self.time; elapsed = 0.
            if row['pulse'] in ('flight', 'launch'):
                row.update(pulse='pause', duration=r['pause_s'], vel=[0., 0.])
            else:
                center = [row['pos'][0] + 24, row['pos'][1] + 15]
                old = row['target']
                if old not in self.players or math.dist(center, self.players[old]) > r['release_px']:
                    row['target'] = min((k for k in self.players if math.dist(center, self.players[k]) <= r['acquire_px']),
                                        key=lambda k: math.dist(center, self.players[k]), default=None)
                if row['target'] is None:
                    row['direction'] *= -1; row['heading'] = [row['direction'], 0.]
                    speed = r['patrol_px_s']
                else:
                    target = self.players[row['target']]
                    dx, dy = target[0] - center[0], target[1] - center[1]
                    length = math.hypot(dx, dy)
                    if length:
                        row['heading'] = [dx / length, dy / length]
                    speed = r['chase_px_s']
                    self.emit('insect_dart', id=row['id'])
                row.update(pulse='flight', duration=r['flight_s'], pulse_speed=speed)
        if row['pulse'] == 'pause':
            row['vel'] = [0., 0.]; return

        speed = row.get('pulse_speed', r['patrol_px_s'])
        ramp = min(1., max(0., elapsed) / .15, max(0., row['duration'] - elapsed) / .15)
        row['vel'] = [speed * ramp * h for h in row['heading']]
        before = list(row['pos']); delta = [v * dt for v in row['vel']]
        destination=[p+d for p,d in zip(before,delta)];fraction=1.
        broad=[min(before[0],destination[0]),min(before[1],destination[1]),
               abs(delta[0])+row['size'][0],abs(delta[1])+row['size'][1]]
        for wall in self.pack.solids:
            if not overlap(broad,wall):continue
            contact=sweep_box(before,destination,row['size'],wall)
            if contact is not None:fraction=min(fraction,contact)
        if fraction<1.:
            fraction=max(0.,fraction-.05/max(abs(delta[0]),abs(delta[1]),.01))
            row['pos']=[p+d*fraction for p,d in zip(before,delta)]
            row.update(pulse='pause',pulse_at=self.time,duration=r['pause_s'],vel=[0.,0.])
            return
        row['pos'] = destination

        if row['pulse'] != 'launch':
            fraction = 1.
            for axis, bounds in ((0, (-448, 1160)), (1, (-180, 1280))):
                if row['pos'][axis] < bounds[0] or row['pos'][axis] > bounds[1]:
                    boundary = bounds[0] if row['pos'][axis] < bounds[0] else bounds[1]
                    if delta[axis]:
                        fraction = min(fraction, max(0., (boundary - before[axis]) / delta[axis]))
            if fraction < 1.:
                row['pos'] = [p + d * fraction for p, d in zip(before, delta)]
                row.update(pulse='pause', pulse_at=self.time, duration=r['pause_s'], vel=[0., 0.])

    def _ball(self, row, dt):
        if row['kind']=='roller':
            return self._roller(row,dt)

        r=self.rules['acid'];x,y=row['pos'];w,h=row['size'];vx,vy=row['vel']
        vy = min(r.get('terminal_px_s',850.), vy + r.get('gravity_px_s2',900.) * dt)
        nx, ny = x + vx * dt, y + vy * dt
        for bx, by, bw, bh in self.pack.solids:
            if not (y < by + bh and y + h > by):
                continue
            if vx > 0 and x + w <= bx + .1 and nx + w >= bx:
                nx = bx - w; vx = -abs(vx)
            elif vx < 0 and x >= bx + bw - .1 and nx <= bx + bw:
                nx = bx + bw; vx = abs(vx)
        surface = None
        if vy >= 0:
            candidates = [(by, bx, bw) for bx, by, bw, bh in self.pack.surfaces
                          if nx < bx + bw and nx + w > bx and y + h <= by + .1 and ny + h >= by]
            if candidates:
                top, bx, bw = min(candidates)
                ny = top - h; vy = 0.; surface = (bx, top, bw)
        else:
            ceilings = [by + bh for bx, by, bw, bh in self.pack.solids
                        if nx < bx + bw and nx + w > bx and y >= by + bh - .1 and ny <= by + bh]
            if ceilings:
                ny = max(ceilings); vy = 0.
        row['pos'] = [nx, ny]; row['vel'] = [vx, vy]
        if surface:
            self._pools(row, surface); self._remove(row)
        elif ny > 1750:
            self._remove(row)

    def _spawn_roller(self,pos,speed=None):
        r=self.rules['roller']
        speed=float(speed if speed is not None else self.rng.uniform(*r['horizontal_px_s_range']))
        return self._new('roller','cave.rollersnake',pos,roll_speed=speed,roll_direction=-1,
                         initial_velocity=[-speed,r['terminal_px_s']])

    @staticmethod
    def _roller_contact(start,end,size,rect,platform=False):
        dx,dy=end[0]-start[0],end[1]-start[1]
        if platform:
            if dy<=0 or start[1]+size[1]>rect[1]+.01:return None
            t=(rect[1]-start[1]-size[1])/dy
            x=start[0]+dx*t
            return (max(0.,t),(0,-1)) if -.000001<=t<=1 and x<rect[0]+rect[2] and x+size[0]>rect[0] else None
        if overlap([*start,*size],rect):return None
        enter,leave=0.,1.;normal=[0,0]
        for axis,(a,b,c,w,s) in enumerate(zip(start,end,rect[:2],rect[2:],size)):
            delta=b-a;lo,hi=c-s,c+w
            if abs(delta)<1e-12:
                if a<=lo or a>=hi:return None
                continue
            near,far=sorted(((lo-a)/delta,(hi-a)/delta))
            side=-1 if delta>0 else 1
            if near>enter+1e-8:
                enter=near;normal=[0,0];normal[axis]=side
            elif abs(near-enter)<1e-8:normal[axis]=side
            leave=min(leave,far)
            if enter>leave:return None
        return (enter,tuple(normal)) if 0<=enter<=1 and leave>0 and any(normal) else None

    def _roller(self,row,dt):
        r=self.rules['roller'];vx,vy=row['vel'];g=r['gravity_px_s2'];cap=r['terminal_px_s']
        speed=row.setdefault('roll_speed',abs(vx) or r['roll_px_s'])
        direction=-1 if vx<0 else 1 if vx>0 else row.get('roll_direction',-1)
        vx=direction*speed;remaining=dt;elapsed=0.
        for _ in range(8):
            if remaining<1e-9:break
            start=list(row['pos'])
            accel_time=min(remaining,max(0.,(cap-vy)/g))
            dy=vy*accel_time+.5*g*accel_time**2+cap*(remaining-accel_time)
            end=[start[0]+vx*remaining,start[1]+dy];hit=None
            broad=[min(start[0],end[0]),min(start[1],end[1]),abs(end[0]-start[0])+row['size'][0],abs(dy)+row['size'][1]]
            for platform,rectangles in ((False,self.pack.solids),(True,self.pack.ledges)):
                for rect in rectangles:
                    if not overlap(broad,rect):continue
                    result=self._roller_contact(start,end,row['size'],rect,platform)
                    if result is not None and (hit is None or result[0]<hit[0]):hit=(*result,platform)
            if hit is None:
                row['pos']=end;vy=min(cap,vy+g*remaining);elapsed+=remaining;remaining=0.;break
            fraction,normal,platform=hit;used=remaining*fraction
            row['pos']=[start[j]+(end[j]-start[j])*fraction+normal[j]*.01 for j in range(2)]
            vy=min(cap,vy+g*used)
            if normal[0]:vx=-vx
            if normal[1]<0:vy=-r['rebound_px_s']
            elif normal[1]>0:vy=abs(vy)
            elapsed+=used;remaining-=used;row['vel']=[vx,vy]
            contact_time=self.time-dt+elapsed
            self.events.append(dict(kind='roller_contact',at=contact_time,id=row['id'],normal=list(normal),
                                    surface='platform' if platform else 'wall',speed=speed,actor=dict(row)))

            self.events.append(dict(kind='actor_move',at=contact_time,actor=dict(row)))
            if contact_time>=row.get('next_roll_sound',0.):
                row['next_roll_sound']=contact_time+r['roll_sound_period_s']
                self.events.append(dict(kind='roller_roll',at=contact_time,id=row['id']))
        row['vel']=[vx,vy];row['roll_direction']=-1 if vx<0 else 1
        mouth=[self.body_pos[0]+40,self.body_pos[1]+10,396,115]
        if vy>0 and overlap([*row['pos'],*row['size']],mouth):self._consume_roller(row)
        elif row['pos'][1]>1750:self._remove(row)

    def _consume_roller(self, row):
        if row['id'] in self.retired:
            return
        self.stats['consumed'] += 1
        origin = [row['pos'][0] + 3, self.body_pos[1] + 22]
        self._remove(row); self.emit('roller_consumed', id=row['id'], pos=origin)
        for _ in range(self.rng.randint(*self.rules['acid']['spray_count'])):
            spray = self._new('spray', 'cave.acidspray', origin)
            spray['vel'] = [self.rng.uniform(-260, 260), self.rng.uniform(-740, -530)]
            self.stats['sprays'] += 1

    def _pools(self, row, surface=None):
        bx, top, bw = surface or (row['pos'][0] - 80, row['pos'][1] + row['size'][1], 180)
        count = self.rng.randint(*self.rules['acid']['pool_count'])
        for i in range(count):
            x = min(bx + bw - 16, max(bx, row['pos'][0] + (i - (count - 1) / 2) * 18))
            self._new('pool', 'cave.acidpool', [x, top - 16])
            self.stats['pools'] += 1

    def _actors(self, dt):
        if not self.hives_spawned and self.time >= self.hives_due:
            self._spawn_hives()
        if self.time >= self.next_roller:
            self.next_roller = self.time + self.rng.uniform(*self.rules['roller']['spawn_interval_s'])
            if sum(a['kind'] == 'roller' for a in self.actors.values()) < self.rules['roller']['cap']:
                self._spawn_roller(self.rng.choice(self.pack.scene['roller_spawns']))
                self.stats['rollers'] += 1
        for row in list(self.actors.values()):
            if row['id'] not in self.actors:
                continue
            if self.time >= row['expiry']:
                self._remove(row, 'LIFETIME'); continue
            kind = row['kind']
            if kind == 'hive':
                if self.time >= row['next_spawn']:
                    row['next_spawn'] = self.time + self.rng.uniform(*self.rules['hive']['spawn_interval_s'])
                    if len(row['children']) < self.rules['hive']['cap']:
                        angle = math.radians(row['facing'])
                        self._insect([row['pos'][0] + 8 + math.cos(angle) * 28,
                                      row['pos'][1] + 17 + math.sin(angle) * 28], row['id'], row['facing'])
            elif kind == 'insect':
                self._fly(row, dt)
            elif kind in ('roller', 'spray'):
                self._ball(row, dt)
            if row['id'] in self.actors and kind in ('insect', 'roller', 'spray') and self.time >= row['next_sync']:
                row['next_sync'] = self.time + .05
                self.emit('actor_move', actor=dict(row))

    def add_damage(self, value, objid=None, player_id=None):
        if value <= 0 or self.state in ('death', 'done', 'waiting'):
            return []
        mouth = self.mouths.get(objid)
        if mouth:
            if self.state != 'weak' or not mouth['weak'] or mouth['dead'] or mouth['action'] == 'enter':
                return []
            mouth['hp'] = max(0, mouth['hp'] - value)
            self.emit('mouth_hp', id=objid, hp=mouth['hp'])
            if mouth['hp'] == 0:
                mouth.update(dead=True, action='dying', action_at=self.time, next_fx=self.time)
                self.emit('mouth_defeated', id=objid, slot=mouth['slot'])
            return self.drain()
        row = self.actors.get(objid)
        if row and row.get('hp') is not None:
            row['hp'] = max(0, row['hp'] - value)
            self.emit('actor_hp', id=objid, hp=row['hp'])
            if row['hp'] == 0:
                self._remove(row, 'HITPOINTS')
        return self.drain()

    def native_destroy(self, objid, reason):
        row = self.actors.get(objid)
        if row is None:
            return []
        if row['kind'] == 'roller' and reason == 5:

            self._remove(row, native=True)
            fresh = self._new('roller', row['vid'], row['pos'],initial_velocity=row['vel'],
                              roll_speed=row.get('roll_speed',abs(row['vel'][0])),roll_direction=row.get('roll_direction',-1))
            fresh['hp'] = row['hp']
            self.emit('actor_hp', id=fresh['id'], hp=fresh['hp'])
        elif row['kind'] == 'spray' and reason == 5:
            self._pools(row); self._remove(row, native=True)
        elif row['kind'] not in ('hive',) or reason == 2:
            self._remove(row, 'HITPOINTS' if reason == 2 else 'INTERNAL', native=True)
        return self.drain()

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('invalid dt')
        if self.state == 'waiting':
            return self.drain()
        end = self.time + dt
        while self.time < end - 1e-9:
            sub = min(1 / 60, end - self.time); self.time += sub
            if self.state != 'done':
                self._phase()
                if self.state != 'done':
                    self._mouths(); self._actors(sub)
        return self.drain()

    def status(self):
        return dict(time=self.time, state=self.state, weak_round=self.weak_round, body_number=self.body_number,
                    body_pos=list(self.body_pos), mouth_hp={i: m['hp'] for i, m in self.mouths.items()},
                    targets=[m['id'] for m in self.mouths.values() if m['weak']],
                    actors=dict(Counter(a['kind'] for a in self.actors.values())),
                    stats=self.stats.copy())
