'EST exploration fauna, sharing existing Jungle capabilities, not arenas.'
import copy
import math
from collections import Counter,deque
from types import SimpleNamespace

from ..jungle_common.session import JungleRoomActors
from ..jungle_common.scene import motion_position,normalize
from ..jungle_mid import params as fauna
from ..jungle_mid.session import JungleMidLiveSession
from ..jungle_boss.session import JungleBossLiveSession
from ...live.terrain import GroundMotion
from ...live.wire import DESTROY_REASON
from .presentation import Presentation

SPECIES={
 'jungle.skitterup':'skitter','jungle.skitterdown':'skitter','jungle.skitterp':'pod',
 'jungle.hopperhive':'hive','jungle.hopper':'hopper','jungle.snakehead':'snake',
 'jungle.snakebody':'link','jungle.snaketail':'link',
 'jungle.spitter':'spitter','jungle.spitegg':'egg','jungle.larva':'larva','jungle.chrysalis':'chrysalis',
 'jungle.wasp':'wasp','jungle.nest':'nest','jungle.fly':'fly','jungle.frog':'frog',
 'jungle.spider':'spider','jungle.gnatsl':'gnats','jungle.gnatsm':'gnats','jungle.gnatss':'gnats',
 'jungle.pinwheel':'pinwheel','jungle.petal':'petal','jungle.turtlehead':'turtlehead',
 'jungle.turtleparasite':'parasite','jungle.slinkypart':'slinky',
}
SHOTS={'jungle.stinger','jungle.flyspit','jungle.pollen','jungle.drops','jungle.swirlbb'}
COCOONS=dict(egg_gravity_mp_s2=78125.,chrysalis_lifetime_s=2.5,
            spitter_acquire_radius_mp=600*256,spitter_windup_s=1.4)
SIM=dict(wake_px=1800.,nest_cap=4,nest_cooldown_s=4.5,wasp_cooldown_s=2.6,
         fly_cooldown_s=2.3,frog_cooldown_s=2.6,spider_depth_px=300.,
         pinwheel_cooldown_s=3.1,turtle_cooldown_s=2.8,gnats_lock_s=.7,
         frog_jump_mp_s=215000.,frog_horizontal_mp_s=39000.)

NEST_ACTIONS=('nest','nest_right','nest_center')
NEST_SUMMONS={
    'nest':dict(emit_ms=792,birth_mp=(5888,-3072)),
    'nest_right':dict(emit_ms=792,birth_mp=(41216,-3072)),
    'nest_center':dict(emit_ms=792,birth_mp=(22016,-2048)),
}

def center(row):
    p=row['props'];return row['pos'][0]+p.get('wmp',0)/2,row['pos'][1]+p.get('hmp',0)/2

class _Surface(dict):
    def __init__(self,key,rect,kind):
        super().__init__(terraintype_=kind);self.objid=key;self.value=rect
    def rect(self):return self.value

class _Room:
    def __init__(self,host):self.host=host;self.objects={};self.refresh()
    def refresh(self):
        rows={}
        for oid,row in self.host.actors.items():
            p=row['props'];kind=p.get('terraintype',0)
            if row.get('dead') or kind not in (1,2):continue
            rect=tuple(row['pos'])+(p.get('wmp',0),p.get('hmp',0))
            if min(rect[2:])>0:rows[oid]=_Surface(oid,rect,kind)
        known={item.rect()+(item['terraintype_'],) for item in rows.values()}
        for rect in self.host.surfaces():
            rect=tuple(rect)
            if rect not in known:rows[('surface',rect)]=_Surface(('surface',rect),rect[:4],rect[4])
        self.objects=rows
    def surfaces(self):return [(key,row.rect(),row['terraintype_']) for key,row in self.objects.items()]

class _Body:
    def __init__(self,row):self.row=row;self.x,self.y=row['pos'];self.w=row['props']['wmp'];self.h=row['props']['hmp']

class ExplorationEnemies(JungleRoomActors):
    _snake_tick=JungleBossLiveSession._snake_tick
    _cocoon_tick=JungleMidLiveSession._cocoon_tick
    _spawn_egg=JungleMidLiveSession._spawn_egg

    def __init__(self,host):
        self.host=host;self.actors={};self.retired=set();self.owned=set();self.counts=Counter()
        self.rng=host.rng;self.worldid=host.worldid;self.room_time=host.room_time
        self.fauna=SimpleNamespace(**{k:getattr(fauna,k) for k in dir(fauna) if k.isupper()})
        self.fauna.SKITTER_RANGE_MP=SIM['wake_px']*256
        self.pack=SimpleNamespace(entities={'cocoons':copy.deepcopy(COCOONS)})
        self.room=_Room(host);self.platforms=[];self.solids=[];self.protected_platforms=[]
        self.slinky_groups={}
        self._seen=set();self._seen_order=deque();self._event_refs=deque(maxlen=4096)
        self.presentation=getattr(host,'presentation',None) or Presentation(host)
        if getattr(host,'presentation',None) is None:host.presentation=self.presentation
        self._geometry()

    @property
    def batch(self):return self.host.batch
    def _offset(self,at=None):return self.host._offset()
    def _due(self,oid,fallback):
        callback=getattr(self.host,'wire_lifetime_due',None)
        due=callback(oid) if callback else None
        return self.room_time+1e-9>=fallback if due is None else due
    def _pod_tick(self,row,at):
        if row.get('expire') is not None and self._due(row['objid'],row['expire']):
            self._pod_fire(row,at)
    def _record(self,kind,at=None,**values):self.host.record(kind,**values)
    def _geometry(self):
        self.room.refresh();surfaces=self.room.surfaces()
        self.platforms=[rect for _,rect,kind in surfaces if kind==2]
        self.solids=[rect for _,rect,kind in surfaces if kind==1]
        all_rects=self.platforms+self.solids
        self.pit_y=max((y+h for x,y,w,h in all_rects),default=0)+1024*256
        self.bounds=(min((r[0] for r in all_rects),default=-1000000),
                     min((r[1] for r in all_rects),default=-1000000),
                     max((r[0]+r[2] for r in all_rects),default=1000000),self.pit_y)

    def _target_player(self,origin):
        players=list(getattr(self.host,'players',{}).values()) or [getattr(self.host,'player',None)]
        candidates=[]
        for p in players:
            if p is None:continue
            alive=p.get('alive',True) if isinstance(p,dict) else getattr(p,'alive',True)
            if not alive:continue
            if isinstance(p,dict):
                w,h=p.get('wmp',10240),p.get('hmp',20480)
                c=p.get('center')
                if callable(c):c=c()
                if c is None:c=(p.get('xmp',p.get('pos',[0,0])[0])+w/2,p.get('ymp',p.get('pos',[0,0])[1])+h/2)
                q=SimpleNamespace(alive=True,xmp=c[0]-w/2,ymp=c[1]-h/2,wmp=w,hmp=h,center=lambda c=c:tuple(c))
            else:q=p
            candidates.append(q)
        return min(candidates,key=lambda p:math.dist(p.center(),origin)) if candidates else None
    def combat_players(self):
        p=self._target_player((0,0));return [p] if p is not None else []

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        self._chains()
        self._prepare_slinky()
        self.host.record('est_fauna_policy',evidence='SIM decisions; D props/actions preserved',policy=copy.deepcopy(SIM))

    def add_actor(self,oid):
        source=self.host.actors.get(oid)
        if not source or source.get('dead') or oid in self.retired:return False
        if oid in self.actors:return True
        vid=source['vid'];species=SPECIES.get(vid)
        if species is None:return False
        if source.get('hp') is not None and source['hp']<=0:return False
        row=dict(objid=oid,props=source['props'],vid=vid,pos=source['pos'],hp=source.get('hp'),
            kind='npc',npc=True,ai=dict(species=species),facing=source['props'].get('facing',0),
            expire=None,vel=[0.,0.],accel=[0.,0.],parent=source.get('creator'),
            native_motion=species in ('link','petal','turtlehead','parasite'),
            send_at=-1.,sent_at=self.room_time,sent=list(source['pos']),tick_s=0.)
        self.actors[oid]=row;self.owned.add(oid);source['_enemy_ai']=True
        ai=row['ai'];ai.update(active=False,home=tuple(row['pos']),next_action=self.room_time+.5,
            direction=-1 if row['facing']==180 else 1,pending=None,next_nav=self.room_time)
        if species=='skitter':self._activate_skitter(oid);row['hp']=source.get('hp')
        elif species=='hive':self._activate_hive(oid,row['hp'])
        elif species=='pod':row['expire']=self.room_time+source['props'].get('lifetime',3000)/1000
        elif species=='hopper':ai.update(vel=[0.,0.],grounded=False,birth_arc=False,next_decision=self.room_time,dir=ai['direction'])
        elif species=='snake':ai.update(vel=[0.,0.],next_lock=self.room_time,next_pose=self.room_time+1.1)
        elif species in ('egg','larva','chrysalis','spitter'):
            ai.update(platform=self._platform_of(*center(row)),born=self.room_time)
            if species=='egg':ai.update(vx=0.,vy=0.,transform=None)
            elif species=='larva':ai.update(transform=self.room_time+1.4,direction=ai['direction'])
            elif species=='chrysalis':ai['transform']=self.room_time+source['props'].get('lifetime',2500)/1000
            else:ai['next_shot']=self.room_time+3.
        elif species=='frog':
            ai.update(body=_Body(row),motion=GroundMotion(self.room),next_hop=self.room_time+1.)
        elif species=='spider':ai.update(mode='wait',vy=0.,goal_y=row['pos'][1],wait_until=0.)
        if species not in ('link','petal','turtlehead','parasite'):
            source['_est_driven']=True
            source['props']['move']={'vel':{'xvel':0,'yvel':0}}
            self.batch.modify(oid,'MOVE',message=('Properties_Move',normalize('Properties_Move',source['props']['move'])),offset=self._offset())
        return True

    def _chains(self):
        for head in self.actors.values():
            if head['ai']['species']!='snake':continue
            chain=[head['objid']]
            while True:
                children=[oid for oid,row in self.actors.items() if row['ai']['species']=='link'
                    and (row['props'].get('move') or {}).get('relativeto',{}).get('objid')==chain[-1]]
                if not children:break
                child=min(children)
                if child in chain:break
                chain.append(child)
            head['chain']=chain

    def _chain_tick(self,row,at):
        chain=row.get('chain',[])
        for previous,oid in zip(chain,chain[1:]):
            parent,child=self.actors.get(previous),self.actors.get(oid)
            if not parent or not child:continue
            dx,dy=[child['pos'][j]-parent['pos'][j] for j in (0,1)];distance=math.hypot(dx,dy)
            limit=(child['props'].get('move') or {}).get('follow',{}).get('maxdist',48)*256
            if distance>limit:child['pos']=[parent['pos'][0]+dx/distance*limit,parent['pos'][1]+dy/distance*limit]
            self._sync_actor(child,at)

    def _prepare_slinky(self):
        groups={}
        for oid,row in self.actors.items():
            if row['ai']['species']=='slinky':groups.setdefault(row['pos'][0],[]).append(oid)
        for members in groups.values():
            members.sort(key=lambda oid:self.actors[oid]['pos'][1])
            for start in range(0,len(members),5):
                group=members[start:start+5]
                if len(group)>1 and max(self.actors[o]['pos'][1] for o in group)-min(self.actors[o]['pos'][1] for o in group)<=4*8192:
                    key=min(group);self.slinky_groups[key]=group
                    for oid in group:self.actors[oid]['ai']['slinky_group']=key
                    self._bind_slinky(key,emit=False)

    def _bind_slinky(self,key,emit=True):
        group=[oid for oid in self.slinky_groups[key] if oid in self.actors and oid not in self.retired]
        if not group:return
        leader=group[0]
        for index,oid in enumerate(group):
            row=self.actors[oid];source=self.host.actors[oid]
            row['ai']['species']='slinky' if index==0 else 'slinky_link'
            row['ai']['leader']=leader
            if not emit:continue
            move=({'vel':{'xvel':0,'yvel':0}} if index==0 else
                  {'relativeto':{'objid':group[index-1]},'follow':{'maxdist':64}})
            row['props']['move']=move;row['native_motion']=index>0;source['_est_driven']=index==0
            self.batch.modify(oid,'MOVE',message=('Properties_Move',normalize('Properties_Move',move)),offset=self._offset())
        self.counts['slinky_bindings']+=1

    def spawn_props(self,vid):return self.host.pack.props(vid)
    def _create(self,vid,props,at,kind='npc',hp=None,ai=None,vel=None,accel=None,parent=None):
        changes=copy.deepcopy(props or {});position=[changes.pop('xmp',0),changes.pop('ymp',0)]

        changes['move']={'vel':{'xvel':0,'yvel':0}}
        if hp is not None:changes['hitpoints']=hp
        oid=self.host._create(vid,position,creator=parent or 0,changes=changes)
        self.add_actor(oid)
        if oid not in self.actors:
            source=self.host.actors[oid]
            self.actors[oid]=dict(objid=oid,props=source['props'],vid=vid,pos=source['pos'],
                hp=source.get('hp'),kind=kind,npc=kind=='npc',ai=ai or {},vel=list(vel or (0.,0.)),
                accel=list(accel or (0.,0.)),parent=parent,facing=source['props'].get('facing',0),
                native_motion=False,send_at=-1.,sent_at=at,sent=list(position),expire=None)
            self.owned.add(oid);source['_enemy_ai']=True;source['_est_driven']=True
        row=self.actors[oid];row.update(kind=kind,npc=kind=='npc',parent=parent)
        if ai is not None:row['ai']=ai
        if vel is not None:row['vel']=list(vel)
        if accel is not None:row['accel']=list(accel)
        if kind in ('projectile','fx'):
            row['expire']=at+row['props'].get('lifetime',3000)/1000
            if vid=='jungle.pollen':row['next_growth']=at+.125
        callback=getattr(self.host,'created_at_end',None)
        if callback:callback(oid)
        self.presentation.body_created(oid)
        if vid in ('jungle.spitegg','jungle.larva','jungle.spitter'):
            from .presentation import SOUNDS
            self.presentation.sound(oid,SOUNDS[vid],token='morph-birth')
        self.counts['spawns']+=1
        return oid

    def _sync_actor(self,row,at,interval=None):
        source=self.host.actors.get(row['objid'])
        if not source or source.get('dead'):return
        source['pos']=list(row['pos']);source['hp']=row.get('hp')
        source['vid']=row['vid'];source['props']['vid']=row['vid']
        if round(row.get('facing',0))!=source['props'].get('facing',0):
            source['props']['facing']=round(row['facing'])
        if row.get('native_motion'):return
        if at+1e-9<row.get('send_at',0):return
        if row['pos']!=row.get('sent'):
            self.batch.move_to(row['objid'],round(row['pos'][0]),round(row['pos'][1]),
                dt=max(1,round((at-row['sent_at'])*1000)),offset=self._offset(at))
            row['sent']=list(row['pos']);row['sent_at']=at
        row['send_at']=at+(interval if interval is not None else .05)

    def _retire(self,oid,reason,at=None,native=False):
        if oid in self.retired:return
        row=self.actors.pop(oid,None);self.retired.add(oid)
        if row is None:return
        group=row.get('ai',{}).get('slinky_group')
        if group is not None:self._bind_slinky(group)
        code=reason if isinstance(reason,int) else DESTROY_REASON.get(str(reason).upper(),1)
        if row.get('ai',{}).get('species')=='snake':
            for child in row.get('chain',[])[1:]:self._retire(child,1,at)

        for child,source in list(self.host.actors.items()):
            if source.get('vid') or source.get('dead'):continue
            if (source['props'].get('move') or {}).get('relativeto',{}).get('objid')==oid:
                self.host._retire(child,reason='INTERNAL')
        if code==2 and row['kind']=='npc' and row.get('ai',{}).get('species') not in ('hopper','link'):
            supplies=getattr(self.host,'supplies',None)
            if supplies is not None:supplies.killed(*center(row))
        self.host._retire(oid,reason=code,native=native)
        self.counts['retired']+=1

    def _transform(self,row,vid,species,hp,at):
        if row['objid'] in self.retired:return
        plat=row['ai'].get('platform')
        if plat is None:plat=self._platform_of(*center(row))
        if plat is None:self._retire(row['objid'],1,at);return
        props=self.spawn_props(vid);cx=center(row)[0]
        x=max(plat[0],min(plat[0]+plat[2]-props['wmp'],cx-props['wmp']/2))
        ai=dict(species=species,platform=plat,born=at)
        if species=='larva':ai.update(transform=at+1.4,direction=self.rng.choice((-1,1)))
        elif species=='chrysalis':ai['transform']=at+2.5;props['animprops']={'animid':0,'time':1452}
        else:ai['next_shot']=at+3.
        old=row['objid']

        self._create(vid,dict(props,xmp=x,ymp=plat[1]-props['hmp']),at,hp=hp,ai=ai,parent=old)
        self._retire(old,1,at)
        self.counts['transforms']+=1

    def _pose(self,row,pose):
        if row['props'].get('pose')!=pose:
            row['props']['pose']=pose;self.batch.modify(row['objid'],'POSE',intval=pose,offset=self._offset())
    def _face(self,row,direction):
        facing=180 if direction<0 else 0
        if row['facing']!=facing:
            row['facing']=facing;row['props']['facing']=facing
            self.batch.modify(row['objid'],'FACING',intval=facing,offset=self._offset())
    def _shot(self,row,vid,speed=0.,gravity=0.,aim=True,drop_fast=False):
        p=self.spawn_props(vid);origin=center(row);target=self._player_center(origin)
        angle=math.atan2(target[1]-origin[1],target[0]-origin[0]) if aim else math.pi/2
        if vid in ('jungle.stinger','jungle.drops'):

            left=row['facing']==180;angle=math.pi if left else 0.
            speed=(88000. if left else 87000.) if vid=='jungle.stinger' else (
                (102000. if left else 101000.) if drop_fast else (55000. if left else 54000.))
            gravity=340000. if vid=='jungle.drops' else 0.
            p['facing']=180 if left else 0
            if vid=='jungle.stinger':p['pose']=2
        position=[origin[0]-p['wmp']/2,origin[1]-p['hmp']/2]

        offsets={'jungle.stinger':(-1792 if row['facing']==180 else 8960,8192),
            'jungle.flyspit':(2304 if row['facing']==180 else 5888,5888),
            'jungle.pollen':(8448,13568)}
        if vid in offsets:position=[row['pos'][j]+offsets[vid][j] for j in (0,1)]
        elif vid=='jungle.drops':position[1]=row['pos'][1]+(3072 if drop_fast else 7168)
        velocity=(((-speed if row['facing']==180 else speed),0.)
                  if vid in ('jungle.stinger','jungle.drops') else
                  (speed*math.cos(angle),speed*math.sin(angle)))
        oid=self._create(vid,dict(p,xmp=position[0],ymp=position[1]),
            self.room_time,kind='projectile',vel=velocity,
            accel=(0,gravity),parent=row['objid'])
        if vid=='jungle.drops':self.actors[oid]['terminal_y']=160000.
        self.counts['shots']+=1;return oid

    def _action(self,row,name,delay,emit):
        ai=row['ai'];token=self.room_time
        self.presentation.action(row['objid'],name,token)
        ai['pending']=dict(at=token+delay,emit=emit,action=name)

    def _new_tick(self,row,dt,at,target):
        ai=row['ai'];species=ai['species'];near=target is not None and math.dist(center(row),target.center())<=SIM['wake_px']*256
        if near:ai['active']=True
        if not ai['active']:return
        pending=ai.get('pending')
        if pending and at+1e-9>=pending['at']:
            ai['pending']=None;emit=pending['emit']
            if emit=='fly':
                if sum(r.get('parent')==row['objid'] and r.get('ai',{}).get('species')=='fly' for r in self.actors.values())<SIM['nest_cap']:
                    port=NEST_SUMMONS[pending.get('action','nest')]
                    p=self.spawn_props('jungle.fly');self._create('jungle.fly',dict(p,
                        xmp=row['pos'][0]+port['birth_mp'][0],
                        ymp=row['pos'][1]+port['birth_mp'][1]),at,parent=row['objid'])
            elif emit=='stinger':self._shot(row,'jungle.stinger',88000.)
            elif emit=='flyspit':self._shot(row,'jungle.flyspit',54687.5)
            elif emit=='pollen':self._shot(row,'jungle.pollen',59000.)
            elif emit in ('drops','drops_fast'):self._shot(row,'jungle.drops',drop_fast=emit=='drops_fast')
        if species=='wasp':

            if at>=ai['next_nav']:
                ai['next_nav']=at+.666;home=ai['home'];offset=self.rng.randint(-6144,6144)
                ai['native_start']=tuple(row['pos']);ai['native_epoch']=at
                ai['native_move']={'waypoint':{'points':[dict(xmp=home[0],ymp=home[1]+offset,dt=333),dict(xmp=home[0],ymp=home[1],dt=333)]}}
                self.batch.modify(row['objid'],'MOVE',message=('Properties_Move',normalize('Properties_Move',ai['native_move'])),offset=self._offset())
                row['native_motion']=True
            row['pos']=list(motion_position(ai['native_start'],ai['native_move'],at-ai['native_epoch']))
        elif species=='fly':
            if at>=ai['next_nav']:
                ai['next_nav']=at+2.5;ai['direction']*=-1;home=ai['home']
                ai['nav_goal']=[home[0]+ai['direction']*45000,home[1]-50000+self.rng.randint(-8192,8192)]
                self._face(row,ai['direction'])
            goal=ai['nav_goal'];delta=[goal[j]-row['pos'][j] for j in (0,1)];distance=math.hypot(*delta)
            scale=min(1,36000*dt/max(1,distance));row['pos']=[row['pos'][j]+delta[j]*scale for j in (0,1)]
        elif species=='gnats':
            if at>=ai['next_nav']:
                ai['next_nav']=at+SIM['gnats_lock_s']
                goal=target.center() if near else ai['home'];delta=[goal[j]-center(row)[j] for j in (0,1)]
                angle=math.atan2(delta[1],delta[0])+self.rng.uniform(-.4,.4)
                speed=75000 if row['vid'].endswith('l') else 90000
                ai['vel']=[math.cos(angle)*speed,math.sin(angle)*speed]
            row['pos']=[row['pos'][j]+ai['vel'][j]*dt for j in (0,1)]
        elif species=='spider':
            x,y=ai['home'];player_below=near and target.ymp>y and abs(target.center()[0]-center(row)[0])<120*256
            if ai['mode']=='wait' and player_below and at>=ai['next_action']:
                ai.update(mode='down',vy=0.,goal_y=min(y+SIM['spider_depth_px']*256,target.ymp-row['props']['hmp']))
                self.presentation.action(row['objid'],'spider_drop',at)
            elif ai['mode']=='down':
                speed=min(160000,ai['vy']+340000*dt);row['pos'][1]=min(ai['goal_y'],row['pos'][1]+(ai['vy']+speed)*dt/2);ai['vy']=speed
                if row['pos'][1]>=ai['goal_y']:ai.update(mode='hold',wait_until=at+.6)
            elif ai['mode']=='hold' and at>=ai['wait_until']:ai['mode']='up'
            elif ai['mode']=='up':
                row['pos'][1]=max(y,row['pos'][1]-160000*dt)
                if row['pos'][1]<=y:ai.update(mode='wait',next_action=at+2.)
        elif species=='frog':
            b=ai['body'];b.x,b.y=row['pos'];motion=ai['motion'];vx=ai.get('air_vx',0.) if not motion.grounded else 0.
            if near and motion.grounded and at>=ai['next_hop']:
                direction=1 if target.center()[0]>=center(row)[0] else -1;self._face(row,direction)
                if abs(target.center()[0]-center(row)[0])<180*256 and abs(target.ymp-b.y)<100*256:
                    self.presentation.action(row['objid'],'frog_tongue',at);ai['next_hop']=at+3.0
                else:
                    motion.jump(SIM['frog_jump_mp_s'],b);vx=direction*SIM['frog_horizontal_mp_s'];ai['air_vx']=vx
                    ai['next_hop']=at+SIM['frog_cooldown_s'];self.presentation.sound(row['objid'],'jungle.frogjump',at)
            motion.step(b,dt,vx,at);row['pos']=[b.x,b.y];self._pose(row,4 if motion.grounded else 3)
        elif species=='slinky':

            if not ai.get('follow_started') and 'slinky_group' in ai:
                self._bind_slinky(ai['slinky_group']);ai['follow_started']=True
            platform=ai.get('platform') or self._platform_of(*center(row))
            if platform:
                ai['platform']=platform;lo,py,w,h=platform
                nx=row['pos'][0]+ai['direction']*125000*dt
                if nx<lo or nx>lo+w-row['props']['wmp']:ai['direction']*=-1
                goal_y=py-row['props']['hmp'];dy=goal_y-row['pos'][1]
                row['pos']=[max(lo,min(lo+w-row['props']['wmp'],nx)),
                    row['pos'][1]+max(-125000*dt,min(125000*dt,dy))]
                self._face(row,ai['direction']);self._pose(row,2 if int(at)%2 else 3)
        if not near or ai.get('pending') is not None or at<ai['next_action']:return
        config={'wasp':('wasp',.462,'stinger',SIM['wasp_cooldown_s']),
            'fly':('fly',.198,'flyspit',SIM['fly_cooldown_s']),
            'pinwheel':('pinwheel',2.244,'pollen',SIM['pinwheel_cooldown_s']),
            'turtlehead':('turtle_drop',.462,'drops',SIM['turtle_cooldown_s'])}.get(species)
        if species=='nest':
            name=self.rng.choice(NEST_ACTIONS)
            config=(name,NEST_SUMMONS[name]['emit_ms']/1000.,'fly',SIM['nest_cooldown_s'])
        if config:
            name,delay,emit,cooldown=config
            if species=='turtlehead' and abs(target.center()[0]-center(row)[0])>300*256:
                name,emit='turtle_drop_fast','drops_fast'

            if target and species!='nest':self._face(row,1 if target.center()[0]>=center(row)[0] else -1)
            self._action(row,name,delay,emit);ai['next_action']=at+cooldown

    def step(self,dt):
        self.room_time=self.host.room_time;self._geometry();at=self.room_time
        for oid,row in list(self.actors.items()):
            source=self.host.actors.get(oid)
            if not source or source.get('dead'):
                self.actors.pop(oid,None);self.retired.add(oid);continue
            row['pos']=source['pos'];row['hp']=source.get('hp')
            if row['kind'] in ('projectile','fx'):
                if row.get('expire') is not None and self._due(oid,row['expire']):self._retire(oid,6,at);continue
                if row['kind']=='projectile':
                    for j in (0,1):
                        acceleration=row['accel'][j];velocity=row['vel'][j]
                        cap=row.get('terminal_y') if j==1 else None
                        active=min(dt,max(0.,(cap-velocity)/acceleration)) if cap is not None and acceleration>0 else dt
                        row['pos'][j]+=velocity*active+.5*acceleration*active*active
                        if active<dt:row['pos'][j]+=cap*(dt-active)
                        row['vel'][j]=min(cap,velocity+acceleration*dt) if cap is not None else velocity+acceleration*dt
                    if row['vid']=='jungle.pollen' and at+1e-9>=row['next_growth']:
                        row['next_growth']=at+.125
                        row['props']['wmp']+=512
                        self.batch.modify(oid,8,intval=row['props']['wmp']//256,offset=self._offset())
                self._sync_actor(row,at);continue
            species=row['ai']['species'];target=self._target_player(center(row));self.player=target
            if species=='skitter':self._skitter_tick(row,dt,at)
            elif species=='hive':self._hive_tick(row,at)
            elif species=='hopper':self._hopper_tick(row,dt,at)
            elif species=='pod':self._pod_tick(row,at)
            elif species=='snake':
                if target and math.dist(center(row),target.center())<=SIM['wake_px']*256:row['ai']['active']=True
                if row['ai']['active']:self._snake_tick(row,dt,at)
            elif species in ('egg','larva','chrysalis','spitter'):
                pending=row['ai'].get('release_at') is not None
                if species=='chrysalis' and self._due(oid,row['ai']['transform']):
                    self._transform(row,'jungle.spitter','spitter',600,at);continue
                if species=='chrysalis':continue
                if species!='spitter' or pending or (target and math.dist(center(row),target.center())<=COCOONS['spitter_acquire_radius_mp']):
                    self._cocoon_tick(row,dt,at)
            elif species not in ('link','petal','parasite','slinky_link'):self._new_tick(row,dt,at,target)
            if oid in self.actors:self._sync_actor(row,at)

    def _duplicate(self,event):
        token=next(((key,event[key]) for key in ('event_id','eventId','seq','native_t','source_t_ms') if key in event),None)
        key=(str(token),event.get('kind'),event.get('victim',event.get('objid')),event.get('value'),event.get('reason')) if token else ('object',id(event))
        if key in self._seen:return True
        self._seen.add(key);self._seen_order.append(key);self._event_refs.append(event)
        if len(self._seen_order)>4096:self._seen.discard(self._seen_order.popleft())
        return False
    def feedback(self,event):
        if not isinstance(event,dict) or event.get('kind') not in ('damage','destroy'):return False
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.worldid:return False
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        if oid in self.retired or self._duplicate(event):return True
        row=self.actors.get(oid)
        if row is None:return True
        source=self.host.actors.get(oid)
        if source is not None:row['pos']=source['pos'];row['hp']=source.get('hp')
        if row['props'].get('invulnerable'):return True
        reason=event.get('reason',2)
        if event['kind']=='damage':
            value=int(event.get('value',event.get('val',0)) or 0)
            if value<=0 or row.get('hp') is None:return True
            row['hp']=max(0,row['hp']-value);self.host.actors[oid]['hp']=row['hp'];row['props']['hitpoints']=row['hp']
            self.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self._offset())
            if row['hp']>0:return True
            reason=2
        code=reason if isinstance(reason,int) else DESTROY_REASON.get(str(reason).upper(),1)
        if code==6 and row['ai'].get('species') in ('pod','chrysalis'):return self.expire(oid)
        if code==2 and row['ai'].get('species')=='hopper':self._hopper_burst(row,self.room_time)
        self._retire(oid,code,self.room_time,native=event['kind']=='destroy');return True
    def expire(self,oid):
        row=self.actors.get(oid)
        if oid not in self.owned:return False
        if row is None or oid in self.retired:return True
        if row['ai'].get('species')=='pod':self._pod_fire(row,self.host.room_time)
        elif row['ai'].get('species')=='chrysalis':self._transform(row,'jungle.spitter','spitter',600,self.host.room_time)
        else:self._retire(oid,6,self.host.room_time)
        return True
    def status(self):return dict(alive=len(self.actors),owned=len(self.owned),counts=dict(self.counts),policy=copy.deepcopy(SIM),decisionEvidence='SIM')
