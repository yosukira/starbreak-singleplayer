"""EFB-owned wire/ownership helpers, reusing native motion primitives."""
import copy
import json
import math
from pathlib import Path
from ..sc_boss.motion import NativeMotion
from ..lab_hard.arena import swept_hit
from ...world_snapshot import WorldSnapshot
from .physics import swept_shape

NATIVE=json.loads(Path(__file__).with_name('native_profiles.json').read_text(encoding='utf8'))

def rect(row):return (*row['pos'],row['props'].get('wmp',0),row['props'].get('hmp',0))

def center(player):
    value=player.get('center') if isinstance(player,dict) else getattr(player,'center',None)
    if callable(value):return tuple(value())
    if value is not None:return tuple(value)
    if isinstance(player,dict):return player.get('xmp',0)+player.get('wmp',0)/2,player.get('ymp',0)+player.get('hmp',0)/2
    return player.xmp+player.wmp/2,player.ymp+player.hmp/2

def players(host):
    values=list(getattr(host,'players',{}).values())
    if not values and getattr(host,'player',None) is not None:values=[host.player]
    return [p for p in values if p is not None and (p.get('alive',True) if isinstance(p,dict) else getattr(p,'alive',True))]

def nearest(host,point):
    values=players(host)
    return min(values,key=lambda p:math.dist(center(p),point)) if values else None

def changed(host):host._efb_geometry_epoch=getattr(host,'_efb_geometry_epoch',0)+1

def solids(host):
    key=(host.room_time,len(host.actors),getattr(host,'_next_id',0),getattr(host,'_efb_geometry_epoch',0))
    cached=getattr(host,'_efb_solids',None)
    if not cached or cached[0]!=key:
        cached=(key,[(oid,rect(row)) for oid,row in host.actors.items() if not row.get('dead') and row['props'].get('terraintype')==1])
        host._efb_solids=cached
    return cached[1]

def swept(host,start,end,size,ignore=()):
    hits=[];w,h=size
    for oid,(x,y,rw,rh) in solids(host):
        if oid in ignore:continue
        t=swept_hit(*start,*end,(x-w,y-h,x+rw,y+rh))
        shape=host.actors[oid]['props'].get('shapetype',0)
        if t is not None and shape:t=swept_shape((x,y,rw,rh),shape,start,end,size)
        if t is not None:hits.append((t,oid))
    return min(hits) if hits else None

def prop(host,oid,name,value):
    row=host.actors.get(oid)
    if row is None or row['props'].get(name.lower())==value:return
    row['props'][name.lower()]=value
    if name.lower()=='vid':row['vid']=value
    if name.lower() in ('terraintype','wmp','hmp','shapetype'):changed(host)
    kwargs={'boolval':value} if isinstance(value,bool) else {'stringval':value} if isinstance(value,str) else {'intval':round(value)}
    host.batch.modify(oid,name.upper(),offset=host._offset(),**kwargs)

def move(host,oid,position,dt,pose=None,facing=None):
    row=host.actors.get(oid)
    if row is None:return
    old=row['pos'];row['pos']=list(position);row['vel']=[(position[i]-old[i])/dt if dt else 0 for i in (0,1)]
    if old!=row['pos']:
        if row['props'].get('terraintype') in (1,2):changed(host)
        interval=getattr(host,'_slice_ms',round(dt*1000)) if getattr(host,'_in_step',False) else round(dt*1000)
        host.batch.move_to(oid,round(position[0]),round(position[1]),dt=max(1,interval),offset=host._offset())
    if pose is not None:prop(host,oid,'pose',pose)
    if facing is not None:prop(host,oid,'facing',facing)

def stop_motion(host,oid):
    row=host.actors[oid]
    row['props']['move']=dict(vel=dict(xvel=0,yvel=0))
    host.batch.modify(oid,'MOVE',message=('Properties_Move',dict(vel_=dict(xvel_=0,yvel_=0))),offset=host._offset())

class NativeTrack:
    """Same native velocity and looping-waypoint math as reconnect snapshots."""
    def __init__(self,position,movement=None,started_ms=0):
        self.origin=tuple(position);self.movement=copy.deepcopy(movement or {});self.started_ms=started_ms

    def position(self,clock_ms):
        elapsed=max(0,clock_ms-self.started_ms);m=self.movement
        if m.get('waypoint'):
            from ..ice_boss.scene import normalize
            return WorldSnapshot.waypoint_state(normalize('Properties_Move_Waypoint',m['waypoint']),self.origin,elapsed)[0]
        motion=NativeMotion(*self.origin);motion.move=m
        return motion.position(elapsed/1000.)

class Ownership:
    def __init__(self,host,tag):
        self.host=host;self.tag=tag;self.owned=set();self.retired=set();self.seen=set();self.recent=[]

    def own(self,oid):self.owned.add(oid);self.host.actors[oid][self.tag]=True

    def retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        self.retired.add(oid);self.owned.discard(oid)
        row=self.host.actors.get(oid)
        if row and row['props'].get('terraintype') in (1,2):changed(self.host)
        self.host._retire(oid,reason,native=native)

    def feedback(self,event,drop=False):
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned and oid not in self.retired:return False
        if oid in self.retired:return True
        identity=event.get('event_id',event.get('seq'))
        if identity is not None:
            key=(event.get('kind'),oid,str(identity))
            if key in self.seen:return True
            self.seen.add(key)
        elif any(event is old for old in self.recent):return True
        self.recent=(self.recent+[event])[-256:];row=self.host.actors.get(oid)
        if row is None:return True
        kind=event.get('kind');reason=event.get('reason',1)
        if kind=='damage':
            if row['props'].get('invulnerable') or row.get('hp') is None:return True
            row['hp']=max(0,row['hp']-max(0,int(event.get('value',0))));prop(self.host,oid,'hitpoints',row['hp'])
            if row['hp']>0:return True
            reason='HITPOINTS'
        elif kind!='destroy':return True
        if reason in (2,'HITPOINTS','hitpoints') and drop and getattr(self.host,'supplies',None):
            x,y,w,h=rect(row);self.host.supplies.killed(x+w/2,y+h/2)
        self.retire(oid,reason,kind=='destroy');return True

class Projectiles(Ownership):
    def __init__(self,host):super().__init__(host,'_efb_projectile');self.shots={}

    def spawn(self,vid,point,velocity,owner,*,gravity=0.,ttl=None,changes=None,audible=True):
        props=self.host.pack.props(vid);props.update(changes or {})
        if ttl is not None:props['lifetime']=round(ttl*1000)
        props['move']=dict(vel=dict(xvel=0,yvel=0));props['facing']=round(math.degrees(math.atan2(velocity[1],velocity[0])))%360
        oid=self.host._create(vid,point,owner,props);self.own(oid)
        self.shots[oid]=dict(vid=vid,vx=velocity[0],vy=velocity[1],gravity=gravity,owner=owner,
            born=getattr(self.host,'_frame_start',self.host.room_time),expiry=self.host.room_time+props.get('lifetime',8000)/1000.)
        if audible:self.host.presentation.projectile_created(oid,vid)
        return oid

    def step(self,dt):
        for oid,s in list(self.shots.items()):
            row=self.host.actors.get(oid)
            if row is None:self.shots.pop(oid,None);continue
            due=self.host.wire_lifetime_due(oid)
            if due is True or (due is None and self.host.room_time>=s['expiry']):self.retire(oid,'LIFETIME');continue
            if s['born']>=self.host.room_time-1e-9:continue
            x,y,w,h=rect(row);end=(x+s['vx']*dt,y+s['vy']*dt+.5*s['gravity']*dt*dt)
            s['vy']+=s['gravity']*dt;hit=swept(self.host,(x,y),end,(w,h),ignore=(oid,s['owner']))
            if hit:
                f=max(0,hit[0]-1e-6);end=(x+(end[0]-x)*f,y+(end[1]-y)*f)
            move(self.host,oid,end,dt)
            if hit:self.retire(oid,'BLOCKED')

    def retire(self,oid,reason='INTERNAL',native=False):
        self.shots.pop(oid,None)
        for child,row in list(self.host.actors.items()):
            if row.get('_efb_flame_head')==oid:self.host.enemies.retire(child,'INTERNAL')
        super().retire(oid,reason,native)

    def expire(self,oid):
        if oid not in self.owned:return False
        self.retire(oid,'LIFETIME');return True
