"""ETL motion/ownership helpers; policies live in the species controllers."""
import copy
import json
import math
from pathlib import Path

from ..etl_motion import Terrain,NativeMotion
from ..lab_hard.arena import swept_hit

NATIVE=json.loads(Path(__file__).with_name('native_profiles.json').read_text(encoding='utf8'))

def center(player):
    value=player.get('center') if isinstance(player,dict) else getattr(player,'center',None)
    if callable(value):return tuple(value())
    if value is not None:return tuple(value)
    if isinstance(player,dict):return player.get('xmp',0)+player.get('wmp',15360)/2,player.get('ymp',0)+player.get('hmp',21504)/2
    return player.xmp+player.wmp/2,player.ymp+player.hmp/2

def players(host):
    values=list(getattr(host,'players',{}).values())
    if not values and getattr(host,'player',None) is not None:values=[host.player]
    return [p for p in values if p is not None and (p.get('alive',True) if isinstance(p,dict) else getattr(p,'alive',True))]

def nearest(host,point):
    values=players(host)
    return min(values,key=lambda p:math.dist(center(p),point)) if values else None

def rect(row):return (*row['pos'],row['props'].get('wmp',0),row['props'].get('hmp',0))

def body(row):
    x,y,w,h=rect(row)
    return dict(x=float(x),y=float(y),w=w,h=h,vy=0.,grounded=False)

def body_center(value):return value['x']+value['w']/2,value['y']+value['h']/2

def terrain(host):
    return Terrain({oid:dict(row['props'],xmp=row['pos'][0],ymp=row['pos'][1])
        for oid,row in host.actors.items() if not row.get('dead') and row['props'].get('terraintype') in (1,2)})

def solid_rows(host):
    key=(host.room_time,len(host.actors),getattr(host,'_next_id',0),getattr(host,'_etl_geometry_epoch',0))
    cached=getattr(host,'_etl_solid_cache',None)
    if cached is None or cached[0]!=key:
        cached=(key,[(oid,rect(row)) for oid,row in host.actors.items()
            if not row.get('dead') and row['props'].get('terraintype')==1])
        host._etl_solid_cache=cached
    return cached[1]

def geometry_changed(host):host._etl_geometry_epoch=getattr(host,'_etl_geometry_epoch',0)+1

def swept(host,start,end,size,ignore=(),departing_contact=False):
    hits=[];w,h=size
    for oid,(x,y,rw,rh) in solid_rows(host):
        if oid in ignore:continue
        bounds=(x-w,y-h,x+rw,y+rh)
        t=swept_hit(start[0],start[1],end[0],end[1],bounds)
        if departing_contact and t==0 and (
                (start[0]==bounds[0] and end[0]<start[0]) or
                (start[0]==bounds[2] and end[0]>start[0]) or
                (start[1]==bounds[1] and end[1]<start[1]) or
                (start[1]==bounds[3] and end[1]>start[1])):

            continue
        if t is not None:hits.append((t,oid))
    return min(hits) if hits else None

def visible(host,a,b,ignore=()):return swept(host,a,b,(0,0),ignore) is None

def set_prop(host,oid,name,value):
    row=host.actors.get(oid)
    if row is None or row['props'].get(name.lower())==value:return
    row['props'][name.lower()]=value
    if name.lower() in ('terraintype','wmp','hmp'):geometry_changed(host)
    if isinstance(value,str):host.batch.modify(oid,name.upper(),stringval=value,offset=host._offset())
    elif isinstance(value,bool):host.batch.modify(oid,name.upper(),boolval=value,offset=host._offset())
    else:host.batch.modify(oid,name.upper(),intval=round(value),offset=host._offset())
    if name.lower()=='vid':row['vid']=value

def move(host,oid,point,dt,pose=None,facing=None):
    row=host.actors.get(oid)
    if row is None or row.get('dead'):return
    old=row['pos'];row['vel']=[(point[i]-old[i])/dt if dt else 0. for i in (0,1)]
    row['pos']=list(point)
    if tuple(old)!=tuple(point) and row['props'].get('terraintype') in (1,2):geometry_changed(host)
    interval=getattr(host,'_slice_ms',round(dt*1000)) if getattr(host,'_in_step',False) else round(dt*1000)
    if tuple(old)!=tuple(point):host.batch.move_to(oid,round(point[0]),round(point[1]),dt=max(1,interval),offset=host._offset())
    if pose is not None:set_prop(host,oid,'pose',pose)
    if facing is not None:set_prop(host,oid,'facing',facing)

def follow(host,oid):
    row=host.actors.get(oid)
    if row is None:return False
    movement=row['props'].get('move') or {};parent=movement.get('relativeto',{}).get('objid')
    if parent not in host.actors:return False
    p=host.actors[parent];x,y=p['pos'];to=movement.get('to') or {};x+=to.get('xmp',0);y+=to.get('ymp',0)
    lerp=movement.get('lerp') or {};target=(lerp.get('target') or {}).get('objid')
    if target in host.actors:
        end=host.actors[target];amount=lerp.get('percentage',0)/100.
        ax,ay=p['pos'];bx,by=end['pos']
        if lerp.get('center'):
            ax+=p['props'].get('wmp',0)/2;ay+=p['props'].get('hmp',0)/2
            bx+=end['props'].get('wmp',0)/2;by+=end['props'].get('hmp',0)/2
        x=ax+(bx-ax)*amount;y=ay+(by-ay)*amount
        if lerp.get('center'):x-=row['props'].get('wmp',0)/2;y-=row['props'].get('hmp',0)/2
    if row['pos']!=[x,y] and row['props'].get('terraintype') in (1,2):geometry_changed(host)
    row['pos']=[x,y]
    return True

class Ownership:
    def __init__(self,host,tag):
        self.host=host;self.tag=tag;self.owned=set();self.retired=set();self.seen=set();self.last_events=[]

    def own(self,oid):
        self.owned.add(oid);self.host.actors[oid][self.tag]=True

    def retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        for controller in (getattr(self.host,'enemies',None),getattr(self.host,'mechanics',None)):
            projectiles=getattr(controller,'projectiles',None)
            if projectiles is not None:projectiles.anchor_removed(oid)
        self.retired.add(oid);self.owned.discard(oid)
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
        elif any(event is previous for previous in self.last_events):return True
        self.last_events=(self.last_events+[event])[-256:]
        row=self.host.actors.get(oid)
        if row is None:return True
        if row['props'].get('invulnerable'):return True
        kind=event.get('kind');reason=event.get('reason',2)
        if kind=='damage' and row.get('hp') is not None:
            row['hp']=max(0,row['hp']-max(0,int(event.get('value',0))))
            row['props']['hitpoints']=row['hp']
            self.host.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self.host._offset())
            if row['hp']>0:return True
            reason='HITPOINTS'
        elif kind!='destroy':return True
        hp_death=reason in (2,'HITPOINTS','hitpoints')
        if hp_death and drop and row.get('hp') is not None and getattr(self.host,'supplies',None):
            x,y,w,h=rect(row);self.host.supplies.killed(x+w/2,y+h/2)
        self.retire(oid,reason,native=kind=='destroy')
        return True

class Projectiles(Ownership):
    """Native ETL shot fields with swept solids, gravity and integer lifetimes.

    Damage to native players remains in the native collision channel. This
    server mirror only owns its shots and never subtracts player health.
    """
    def __init__(self,host,tag='_etl_projectile'):
        super().__init__(host,tag);self.shots={}

    def spawn(self,vid,position,velocity,creator,*,gravity=0.,lifetime=None,impact=None,changes=None):
        props=self.host.pack.props(vid);props.update(changes or {})
        if lifetime is not None:props['lifetime']=round(lifetime*1000)
        props['move']=dict(vel=dict(xvel=0,yvel=0))
        props['facing']=round(math.degrees(math.atan2(velocity[1],velocity[0])))%360
        oid=self.host._create(vid,position,creator,props);self.own(oid)
        born=getattr(self.host,'_frame_start',self.host.room_time) if getattr(self.host,'_in_step',False) else self.host.room_time
        self.shots[oid]=dict(vid=vid,vx=float(velocity[0]),vy=float(velocity[1]),gravity=gravity,
            born=born,expires=born+props.get('lifetime',8000)/1000,
            impact=impact,creator=creator,collide=props.get('blocktype',0) in (1,4) or impact=='lab.brambled')
        if vid in ('lab.thumper','lab.thumperp'):

            vx,vy=velocity
            pose=2 if vx and abs(vy)<=1e-9 else 3 if vy and abs(vx)<=1e-9 else None
            if pose is not None:self.shots[oid]['first_flight_pose']=pose
        elif vid=='lab.seed':

            self.shots[oid]['first_flight_pose']=2
        presentation=getattr(self.host,'presentation',None)
        if presentation:presentation.projectile_created(oid,vid)
        return oid

    def finish(self,oid,reason='BLOCKED',native=False):
        shot=self.shots.pop(oid,None);row=self.host.actors.get(oid)
        if shot and row and shot['impact'] and reason in ('BLOCKED','LIFETIME',5,6) and not (shot['impact']=='lab.brambled' and reason in ('LIFETIME',6)):
            if shot['impact']=='lab.brambled':
                p=self.host.pack.props('lab.brambled')
                position=[row['pos'][0]+row['props'].get('wmp',0)/2-p['wmp']/2,
                          row['pos'][1]+row['props'].get('hmp',0)/2-p['hmp']/2]
                anchor=shot.get('hit_object')
                if anchor in self.host.actors:
                    base=self.host.actors[anchor]['pos']
                    p['move']=dict(relativeto=dict(objid=anchor),to=dict(xmp=round(position[0]-base[0]),ymp=round(position[1]-base[1])))
                child=self.host._create('lab.brambled',position,oid,p)
                self.own(child);self.shots[child]=dict(vid='lab.brambled',vx=0.,vy=0.,gravity=0.,
                    born=self.host.room_time,expires=self.host.room_time+60.,impact=None,creator=oid,anchored=True)
                if hasattr(self.host,'created_at_end'):self.host.created_at_end(child)
                if getattr(self.host,'presentation',None):self.host.presentation.attack(child,'lab.brambled',3,396,token=self.host.room_time)
            else:
                child=self.host._create(shot['impact'],row['pos'],oid)
                self.own(child);p=self.host.actors[child]['props']
                self.shots[child]=dict(vid=shot['impact'],vx=0.,vy=0.,gravity=0.,born=self.host.room_time,
                    expires=self.host.room_time+p.get('lifetime',500)/1000,impact=None,creator=oid,anchored=True)
                if hasattr(self.host,'created_at_end'):self.host.created_at_end(child)
                if getattr(self.host,'presentation',None):self.host.presentation.effect_created(child,shot['impact'])
        self.retire(oid,reason,native)

    def step(self,dt):
        for oid,shot in list(self.shots.items()):
            row=self.host.actors.get(oid)
            if row is None:self.shots.pop(oid,None);continue
            clock=getattr(self.host,'wire_lifetime_due',None)
            due=clock(oid) if clock else None
            if due is True or (due is None and self.host.room_time+1e-9>=shot['expires']):
                self.finish(oid,'LIFETIME');continue
            if shot.get('anchored'):
                parent=(row['props'].get('move') or {}).get('relativeto',{}).get('objid')
                if parent is not None and parent not in self.host.actors:self.finish(oid,'INTERNAL')
                elif parent is not None:follow(self.host,oid)
                continue
            if shot['born']>=self.host.room_time-1e-9:continue
            x,y,w,h=rect(row);vy=shot['vy'];gy=shot['gravity']
            end=(x+shot['vx']*dt,y+vy*dt+.5*gy*dt*dt)
            shot['vy']=min(160000.,vy+gy*dt)
            hit=swept(self.host,(x,y),end,(w,h),ignore=(oid,shot['creator']),
                departing_contact=shot['vid'] in ('lab.thumper','lab.thumperp')) if shot.get('collide',False) else None
            if hit:
                shot['hit_object']=hit[1]
                f=max(0.,hit[0]-1e-6);end=(x+(end[0]-x)*f,y+(end[1]-y)*f)
            if end!=(x,y) and 'first_flight_pose' in shot:
                set_prop(self.host,oid,'pose',shot.pop('first_flight_pose'))
            move(self.host,oid,end,dt)
            if hit:self.finish(oid,'BLOCKED')

    def feedback(self,event,drop=False):
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned and oid not in self.retired:return False
        if event.get('kind')=='destroy':
            if oid not in self.retired:self.finish(oid,event.get('reason',1),True)
            return True
        result=super().feedback(event,False)
        if oid in self.retired:self.shots.pop(oid,None)
        return result

    def expire(self,oid):
        if oid not in self.owned:return False
        self.finish(oid,'LIFETIME');return True

    def anchor_removed(self,oid):
        for child,shot in list(self.shots.items()):
            row=self.host.actors.get(child)
            if shot.get('anchored') and row and (row['props'].get('move') or {}).get('relativeto',{}).get('objid')==oid:
                self.finish(child,'INTERNAL')
