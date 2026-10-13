"""ETL motion primitives, independent of encounter decisions and Hulk AI."""
import math

class NativeMotion(object):
    """Server mirror of native linear / waypoint / circular motion."""
    def __init__(self, position):
        self.origin = tuple(position)
        self.started = 0.
        self.move = {}

    def set(self, move, now):
        self.origin = self.position(now)
        self.started = float(now)
        self.move = move

    @property
    def duration(self):
        if 'waypoint' in self.move:
            return sum(p.get('dt', 0) for p in self.move['waypoint']['points'])/1000.
        if 'elliptical' in self.move:
            return self.move['elliptical']['period']/1000.
        return (self.move.get('to') or {}).get('dt', 0)/1000.

    def position(self, now):
        age = max(0., float(now)-self.started)
        ellipse = self.move.get('elliptical')
        if ellipse:
            angle = math.radians(ellipse.get('angle', 0)) + 2*math.pi*age/(ellipse['period']/1000.)
            radius = ellipse.get('radiusamp', 0)
            return (ellipse['xmp']+radius*math.cos(angle),
                    ellipse['ymp']+radius*math.sin(angle))
        points = (self.move.get('waypoint') or {}).get('points')
        if points is None:
            points = [self.move['to']] if self.move.get('to') else []
        origin = self.origin
        for p in points:
            target = (p.get('xmp', origin[0]), p.get('ymp', origin[1]))
            duration = p.get('dt', 0)/1000.
            if duration > 0 and age < duration:
                f = age/duration
                return tuple(a+(b-a)*f for a,b in zip(origin,target))
            origin = target
            age -= duration
        return origin

class Terrain(object):
    """Static ETL terrain; ledges only support descending actors."""
    def __init__(self, templates):
        self.surfaces = []
        for key, p in templates.items():
            kind = int(p.get('terraintype') or 0)
            if kind in (1, 2):
                self.surfaces.append((int(key), (p.get('xmp',0),p.get('ymp',0),
                                      p.get('wmp',0),p.get('hmp',0)), kind))

    def support(self, x, y, w, h):
        for objid, (sx,sy,sw,sh), kind in self.surfaces:
            if abs(y+h-sy)<2 and x<sx+sw and x+w>sx:
                return objid
        return None

    def step(self, body, dt, vx, gravity, terminal=160000.):
        """Exact ballistic integration with swept walls and one-way floors."""
        x,y,w,h = body['x'],body['y'],body['w'],body['h']
        oldvy = body.get('vy',0.)
        if body.get('grounded') and self.support(x,y,w,h) is None:
            body['grounded'] = False
        grounded = body.get('grounded',False)
        tx = x+vx*dt
        blocked = False
        for _,(sx,sy,sw,sh),kind in self.surfaces:
            if kind!=1 or y>=sy+sh or y+h<=sy:
                continue
            if vx>0 and x+w<=sx and tx+w>sx:
                tx,blocked=sx-w,True
            elif vx<0 and x>=sx+sw and tx<sx+sw:
                tx,blocked=sx+sw,True
        if grounded and self.support(tx,y,w,h) is not None:
            body.update(x=tx,vy=0.,grounded=True,blocked=blocked)
            return
        accelerated = min(dt,max(0.,(terminal-oldvy)/gravity))
        ty=y+oldvy*accelerated+.5*gravity*accelerated**2+terminal*(dt-accelerated)
        vy=min(terminal,oldvy+gravity*dt)
        floor,ceil=None,None
        for objid,(sx,sy,sw,sh),kind in self.surfaces:
            if ty+h>=sy and y+h<=sy+1 and ty>y:
                f=max(0.,min(1.,(sy-y-h)/(ty-y)))
                cx=x+(tx-x)*f
                if cx<sx+sw and cx+w>sx and (floor is None or sy<floor[1]):
                    floor=(objid,sy)
            if kind==1 and ty<sy+sh<=y and ty<y:
                f=max(0.,min(1.,(sy+sh-y)/(ty-y)))
                cx=x+(tx-x)*f
                if cx<sx+sw and cx+w>sx and (ceil is None or sy+sh>ceil):
                    ceil=sy+sh
        if floor is not None:
            ty,vy=floor[1]-h,0.
        elif ceil is not None:
            ty,vy=ceil,0.
        body.update(x=tx,y=ty,vy=vy,grounded=floor is not None,
                    support=floor[0] if floor else None,blocked=blocked)

    def landing_above(self, body, player_y, max_rise):
        feet=body['y']+body['h']
        return [(i,r,k) for i,r,k in self.surfaces
                if 0<feet-r[1]<=max_rise and body['x']+body['w']>r[0]
                and body['x']<r[0]+r[2] and r[1]>=player_y-40*256]
