"""SC actor wire helpers and native enemy projectile ownership."""
import copy
import math

from .physics import flight, ground, rect, sinusoid


def overlaps(a, b):
    """Whether two (x, y, w, h) boxes share interior area."""
    return a[0] < b[0]+b[2] and a[0]+a[2] > b[0] and a[1] < b[1]+b[3] and a[1]+a[3] > b[1]


def center(value):
    point = value.get('center') if isinstance(value, dict) else getattr(value, 'center', None)
    if callable(point):
        return tuple(point())
    if point is not None:
        return tuple(point)
    if isinstance(value, dict):
        if 'pos' in value:
            x, y, w, h = rect(value)
        else:
            x, y, w, h = (value.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))
    else:
        x, y, w, h = (getattr(value, k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))
    return (x+w/2, y+h/2)


def players(host):
    values = getattr(host, 'players', {})
    values = list(values.items())
    if not values and getattr(host, 'player', None) is not None:
        values = [(0, host.player)]
    return {key: p for key, p in values if p is not None and
            (p.get('alive', True) if isinstance(p, dict) else getattr(p, 'alive', True))}


def prop(host, oid, name, value):
    row = host.actors.get(oid)
    if row is None or (name in row['props'] and row['props'][name] == value):
        return
    row['props'][name] = value
    if name == 'vid':
        row['vid'] = value
    values = {} if value is None else ({'boolval': value} if isinstance(value, bool)
             else {'stringval': value} if isinstance(value, str) else {'intval': round(value)})
    host.batch.modify(oid, name.upper(), offset=host._offset(), **values)


def move(host, oid, point, dt, pose=None, facing=None):
    row = host.actors.get(oid)
    if row is None:
        return
    old = row['pos']
    row['pos'] = list(point)
    row['vel'] = [(point[i]-old[i])/dt if dt else 0. for i in (0, 1)]
    if old != row['pos']:
        interval = getattr(host, '_slice_ms', round(dt*1000)) if getattr(host, '_in_step', False) else round(dt*1000)
        host.batch.move_to(oid, round(point[0]), round(point[1]), dt=max(1, interval), offset=host._offset())
    if pose is not None:
        prop(host, oid, 'pose', pose)
    if facing is not None:
        prop(host, oid, 'facing', facing)


def stop_motion(host, oid):
    host.actors[oid]['props']['move'] = dict(vel=dict(xvel=0, yvel=0))
    host.batch.modify(oid, 'MOVE', message=('Properties_Move', dict(vel_=dict(xvel_=0, yvel_=0))), offset=host._offset())


class ScProjectiles:
    def __init__(self, controller):
        self.controller = controller
        self.host = controller.host
        self.shots = {}

    def adopt(self, oid, *, owner=None, velocity=None, gravity=0., bounce=False, orbit=None):
        row = self.host.actors[oid]
        m = copy.deepcopy(row['props'].get('move') or {})
        v = m.get('vel', {})
        vx, vy = velocity if velocity is not None else (v.get('xvel', 0)*1000/256, v.get('yvel', 0)*1000/256)
        self.shots[oid] = dict(vid=row['vid'], owner=owner or row.get('creator', 0),
            vx=vx, vy=vy, gravity=gravity, bounce=bounce, orbit=orbit,
            born=self.host.room_time, origin=tuple(row['pos']), native=m if m.get('sin') else None,
            expiry=math.inf if orbit is not None else self.host.room_time+(row['props'].get('lifetime') or 8000)/1000)
        row['_sc_projectile'] = True
        stop_motion(self.host, oid)
        return oid

    def spawn(self, vid, point, velocity, owner, *, gravity=0., bounce=False, changes=None, creator=None):
        changes = dict(changes or {})
        changes['move'] = dict(vel=dict(xvel=0, yvel=0))
        if velocity != (0., 0.):
            changes['facing'] = round(math.degrees(math.atan2(velocity[1], velocity[0]))) % 360
        oid = self.host._create(vid, point, owner if creator is None else creator, changes)
        self.adopt(oid, owner=owner, velocity=velocity, gravity=gravity, bounce=bounce)
        self.host.presentation.projectile_created(oid, vid)
        return oid

    def spawn_sinusoid(self, vid, point, parameters, *, owner=0, lifetime_s=None):
        """Start a new native sinusoid at its birth origin, never a CW phase.

        ``lifetime_s`` only bounds the server-side instance: the authored
        lifetime field still travels unchanged on the wire.
        """
        motion = dict(sin=dict(parameters, xmp=round(point[0]), ymp=round(point[1]), pos=0))
        oid = self.host._create(vid, point, owner,
            dict(move=motion, facing=parameters['angle']))
        self.adopt(oid, owner=owner)
        if lifetime_s is not None:
            self.shots[oid]['expiry']=self.host.room_time+float(lifetime_s)
        self.host.presentation.projectile_created(oid, vid)
        return oid

    def retire(self, oid, reason='INTERNAL', native=False):
        shot = self.shots.pop(oid, None)
        row = self.host.actors.get(oid)
        if shot is None or row is None:
            return
        # Sliver's second damage actor has an explicit projectile creator.
        if shot['vid'] == 'sc.sliver' and reason in ('BLOCKED', 'SHIELD', 'DAMAGES', 3, 4, 5):
            p = self.host.pack.props('sc.sliverx')
            point = center(row)
            child = self.spawn('sc.sliverx', (point[0]-p['wmp']/2, point[1]-p['hmp']/2), (0., 0.), oid)
            callback = getattr(self.host, 'created_at_end', None)
            if callback:
                callback(child)
        self.host._retire(oid, reason, native=native)

    def feedback(self, event):
        oid = int(event.get('victim', event.get('objid', 0)) or 0)
        if oid not in self.shots:
            return False
        if event.get('kind') == 'destroy':
            self.retire(oid, event.get('reason', 'INTERNAL'), native=True)
        elif event.get('kind') == 'damage':
            row = self.host.actors.get(oid)
            if row is not None and row.get('hp') is not None and not row['props'].get('invulnerable'):
                row['hp'] = max(0, row['hp']-max(0, int(event.get('value', 0))))
                prop(self.host, oid, 'hitpoints', row['hp'])
                if row['hp'] == 0:
                    self.retire(oid, 'HITPOINTS')
        # Captured orbiting monkb is invulnerable; no inferred HP here.
        return True

    def expire(self, oid):
        if oid not in self.shots:
            return False
        self.retire(oid, 'LIFETIME')
        return True

    def step(self, dt, terrain):
        now = self.host.room_time
        for oid, s in list(self.shots.items()):
            row = self.host.actors.get(oid)
            if row is None:
                self.shots.pop(oid, None)
                continue
            due = self.host.wire_lifetime_due(oid) if hasattr(self.host, 'wire_lifetime_due') else None
            if due is True or (due is None and now >= s['expiry']):
                self.retire(oid, 'LIFETIME')
                continue
            if s['orbit'] is not None:
                parent = self.host.actors.get(s['owner'])
                if parent is None:
                    self.retire(oid)
                    continue
                phase = s['orbit']+now*math.pi
                # Circular orbit is a local FIT to the observed ring; native
                # source elliptical parameters remain in the content evidence.
                point = (parent['pos'][0]+5120+20480*math.cos(phase),
                         parent['pos'][1]+9216+20480*math.sin(phase))
                move(self.host, oid, point, dt)
                continue
            if s['born'] >= now-1e-9:
                continue
            if s['native']:
                point = sinusoid(s['native'], s['origin'], now-s['born'])
                if not row['props'].get('blocktype'):
                    end, hit = point, None
                else:
                    end, hit = flight(terrain, row, ((point[0]-row['pos'][0])/dt,
                                                    (point[1]-row['pos'][1])/dt), dt, (s['owner'],))
            elif s['bounce']:
                oldvy = s['vy']
                # USER: the thrown Cathedral ball is native terrain
                # (terraintype1) and must collide with the creature that threw
                # it. Only the spawn overlap is ignored, so it cannot bounce
                # inside that body before it has left it.
                solids = terrain
                owner_row = self.host.actors.get(s['owner'])
                if owner_row is not None and overlaps(rect(row), rect(owner_row)):
                    solids = [t for t in terrain if t[0] != s['owner']]
                point = ground(solids, row, s, dt, s['vx'], s['gravity'])
                hit = None
                if s.get('grounded') and oldvy > 0:
                    s['vy'] = -max(25000., oldvy*self.controller.fit['golem_restitution'])
                    s['grounded'] = False
                    prop(self.host, oid, 'pose', 3)
                if s.get('blocked'):
                    s['vx'] *= -self.controller.fit['golem_restitution']
                end = point
            else:
                velocity = (s['vx'], s['vy']+.5*s['gravity']*dt)
                s['vy'] += s['gravity']*dt
                if not row['props'].get('blocktype'):
                    end = (row['pos'][0]+velocity[0]*dt, row['pos'][1]+velocity[1]*dt)
                    hit = None
                else:
                    end, hit = flight(terrain, row, velocity, dt, (s['owner'],))
            move(self.host, oid, end, dt)
            if hit:
                self.retire(oid, 'BLOCKED')
