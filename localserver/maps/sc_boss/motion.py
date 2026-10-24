"""Server geometry mirror of native MOVE, without encounter AI."""
import copy
import math

MP_PER_WIRE_VELOCITY_SECOND=1000./256.

def profile_position(profile,at):
    """Evaluate packet lanes; coordinates are never copied from another run."""
    motion=NativeMotion(profile['props']['xmp'],profile['props']['ymp'])

    for c in profile.get('commands') or ():
        t=c.get('t',0.)
        if t>at:break
        if c.get('movetoxmp') is not None:
            sx,sy=motion.position(t)
            motion.set({'to':{'xmp':c['movetoxmp'],'ymp':c['movetoymp'],
                        'dt':max(1,int(c.get('movetodt') or 1)),
                        'start':{'xmp':sx,'ymp':sy}}},t,copy_move=False)
        mp=c.get('modifyprops') or {}
        if mp.get('property')==41 and mp.get('decoded'):motion.set(mp['decoded'],t,copy_move=False)
    return motion.position(at)

def translate(value, dx=0, dy=0):
    """Translate coordinate fields, preserving signed velocity and durations."""
    if isinstance(value,list):
        return [translate(v,dx,dy) for v in value]
    if not isinstance(value,dict):
        return value
    return {k: (int(round(v+(dx if k=='xmp' else dy))) if k in ('xmp','ymp')
                else translate(v,dx,dy)) for k,v in value.items()}

def axis(origin, velocity, accel, minimum, maximum, ms):
    if not accel:
        return origin+(int(velocity) >> 8)*ms
    end=velocity+accel*ms
    limit = minimum if end < minimum else maximum if end > maximum else None
    if limit is not None and accel:
        before=max(0.,min(ms,(limit-velocity)/accel))
        return origin+(velocity*before+.5*accel*before*before+limit*(ms-before))/256.
    return origin+(velocity*ms+.5*accel*ms*ms)/256.

class NativeMotion:
    def __init__(self,x,y):
        self.origin=(float(x),float(y))
        self.at=0.
        self.move={}

    def position(self,at):
        ms=max(0.,(at-self.at)*1000.)
        x,y=self.origin
        m=self.move
        if m.get('vel'):
            v=m['vel']
            return tuple(axis(o,v.get(a+'vel',0),v.get(a+'accel',0),v.get('min'+a+'vel',-math.inf),
                              v.get('max'+a+'vel',math.inf),ms) for a,o in zip('xy',(x,y)))
        if m.get('elliptical'):
            e=m['elliptical']
            radians=(ms+e.get('pos',0))*2*math.pi/max(1,e.get('period',10000))
            angle=math.radians(e.get('angle',0))
            radius_a=e.get('radiusamp',0)+e.get('ravel',0)*ms
            a=radius_a*math.cos(radians)
            # The native evaluator defaults the second radius to the first;
            # an explicitly present zero still describes a straight segment.
            # Radius growth is MP/ms and starts at birth. Angular pos advances
            # the phase only; including it in growth launches a new spiral
            # thousands of pixels away from its original zero-radius socket.
            radius_b=e.get('radiusbmp',e.get('radiusamp',0))+e.get('rbvel',0)*ms
            b=radius_b*math.sin(radians)
            return (e.get('xmp',x)+a*math.cos(angle)-b*math.sin(angle),
                    e.get('ymp',y)+a*math.sin(angle)+b*math.cos(angle))
        waypoint=m.get('waypoint') or {}
        points=waypoint.get('points') or ([m['to']] if m.get('to') else [])
        total=sum(max(1,p.get('dt',1)) for p in points)
        if total and waypoint.get('loop'):
            ms%=total
        if m.get('to'):
            p=m['to']
            ms+=p.get('pos',0)
            if p.get('start'):
                x,y=p['start'].get('xmp',x),p['start'].get('ymp',y)
        for p in points:
            dt=max(1,p.get('dt',1))
            tx,ty=p.get('xmp',x),p.get('ymp',y)
            if ms < dt:
                f=ms/dt
                return (x+(tx-x)*f,y+(ty-y)*f)
            ms-=dt
            x,y=tx,ty
        return (x,y)

    def set(self,move,at,copy_move=True):
        self.origin=self.position(at)
        self.at=at
        self.move=copy.deepcopy(move) if copy_move else move

    def correct(self,x,y,at):
        old=self.position(at)
        self.origin=(float(old[0] if x is None else x),float(old[1] if y is None else y))
        self.at=at
        self.move={}
