"""Live EUB NPCs using Dumb's actual actions/physics on current-room terrain."""
import copy
import heapq
import math
from types import FunctionType,SimpleNamespace

from ..dumb_common.actors import DumbActorHost
from ..dumb_common import npcs,params
from ..dumb_wave.controller import DumbWaveController
from ..dumb_common.physics import sweep_entry
from ..sc_boss.motion import NativeMotion
from ...supply import is_hp_death
from .profiles import COMMON,SPECIAL,SIM,SHIELD,TOWER_FLIGHTS,SWEEPER_SPEEDS_MP_S,LIGHT_SHOT_GAPS_S,SWEEPER_SHOT_SPEED_MP_S
from .scene import EubNpcPack

class _Batch:
    def __init__(self,adapter):self.adapter=adapter
    def __getattr__(self,name):return getattr(self.adapter.host.batch,name)
    def create(self,oid,props,**kwargs):
        a=self.adapter;h=a.host
        if not kwargs.get('creatorid') and a._spawn_creator:kwargs['creatorid']=a._spawn_creator
        props=copy.deepcopy(props)
        for axis,value in zip(('xmp_','ymp_'),h.actors[oid]['pos']):
            if axis in props:props[axis]=round(value)
        update=h.batch.create(oid,props,**kwargs)
        h._pending_creates[oid]=update
        h._set_wire_birth(oid,h._sent_ms+int(kwargs.get('offset',0)))
        if kwargs.get('creatorid'):h.actors[oid]['creator']=kwargs['creatorid']
        h.presentation.body_created(oid)
        return update
    def modify(self,oid,key,**kwargs):
        a=self.adapter;row=a.actors.get(oid)
        names={13:'facing',17:'damage',19:'hitpoints',43:'vid',46:'pose'}
        field=key.lower() if isinstance(key,str) else names.get(key)
        if row is not None and field:
            value=next((kwargs[k] for k in ('intval','boolval','stringval') if k in kwargs),None)
            if value is not None:
                row['props'][field]=value
                if field=='vid':row['vid']=value
                if field=='hitpoints':row['hp']=value
        return a.host.batch.modify(oid,key,**kwargs)
    def destroy(self,oid,**kwargs):
        self.adapter.host._lifetimes.pop(oid,None)
        return self.adapter.host.batch.destroy(oid,**kwargs)
    def move_to(self,oid,x,y,**kwargs):
        row=self.adapter.actors.get(oid)
        if row is not None and row['vid']=='dumb.elevator':
            kwargs.update(offset=0,dt=max(1,self.adapter.host._slice_ms))
        return self.adapter.host.batch.move_to(oid,x,y,**kwargs)

class ExplorationEnemies(DumbActorHost):
    def __init__(self,host):
        self.host=host;self.pack=EubNpcPack(host.pack,host.recipe)
        self.actors=host.actors;self.retired=host.retired;self.batch=_Batch(self)
        self.layouts=host.layouts;self.worldid=1;self.bridge=host.bridge;self.rng=host.rng
        self.events=host.events;self.supplies=host.supplies;self.controller=None
        self.sequence=0;self.queue=[];self.drone_children={};self.drone_parents={};self.npc_regions={}
        self.counts=dict(spawns=0,npcs=0,shots=0,native_damage=0,native_destroy=0,splits=0,cap_skips=0,wall_hits=0,lifetime=0)
        self.special={};self.custom={};self.owned=set();self._seen=set();self._spawn_creator=0
        self.bounds=tuple(host.recipe['metadata']['bounds_mp']);self.npc_ceiling=self.bounds[1];self.npc_floor=self.bounds[3]

        self.source_role=(host.recipe.get('metadata') or {}).get('source_role') or host.recipe['role']
        local_params=SimpleNamespace(**{k:v for k,v in vars(params).items() if not k.startswith('__')})
        if self.source_role!='wave_chamber':local_params.LIGHT_SHOT_GAPS_S=LIGHT_SHOT_GAPS_S
        ground_step=npcs.ground_step if self.source_role=='wave_chamber' else self._hop_ground_step
        self._tick=FunctionType(npcs.tick.__code__,dict(npcs.__dict__,fly_step=self._fly_step,
            ground_step=ground_step,params=local_params),name='eub_room_npc_tick')
        self.wave_started=False;self.wave_pending=[]
        if host.recipe['role']=='wave_chamber':
            self.controller=DumbWaveController(SimpleNamespace(waves=host.pack.wave_rules,map=dict(id='eub_run')),host.instance.seed)

    @property
    def _next_id(self):return self.host._next_id
    @_next_id.setter
    def _next_id(self,value):self.host._next_id=value
    @property
    def room_time(self):return self.host.room_time
    @property
    def _frame_start(self):return self.host._frame_start
    def _offset(self,at=None):return max(0,min(self.host._slice_ms,round(((self.room_time if at is None else at)-self._frame_start)*1000)))
    def _record(self,kind,at=None,**values):self.host.record(kind,**values)

    def _decorate(self,oid,kind='static',ai=None):
        current=self.actors[oid];saved=copy.deepcopy(current)
        row=DumbActorHost._add_actor(self,oid,current['props'],self.room_time,kind=kind,hp=current.get('hp'),ai=ai,vel=current.get('vel'))
        for key,value in saved.items():
            if key.startswith('_') or key in ('id','creator','born'):row[key]=value
        row['id']=oid
        return row

    def _add_actor(self,oid,props,at,**kwargs):

        props=copy.deepcopy(props)
        for axis in ('xmp','ymp'):
            if axis in props:props[axis]=round(props[axis])
        row=DumbActorHost._add_actor(self,oid,props,at,**kwargs);row['id']=oid
        row['hp']=props.get('hitpoints',row.get('hp'))
        if kwargs.get('kind') in ('npc','projectile','fx'):
            row['_eub_enemy']=kwargs.get('kind')=='npc';row['_eub_projectile']=kwargs.get('kind')!='npc';self.owned.add(oid)
        return row

    def prepare(self):
        for oid,row in list(self.actors.items()):
            species=row['vid'].removeprefix('dumb.');kind='npc' if species in COMMON else 'special' if species in SPECIAL else 'static'
            ai=None
            if kind=='npc':
                spec=params.SPECIES[species]
                ai=dict(species=species,dir=-1 if row['props'].get('facing')==180 else 1,next_shot_at=self.rng.uniform(.2,spec.get('cadence_s') or 1),
                        min_x=self.bounds[0],max_x=self.bounds[2]-row['props'].get('wmp',0))
            row=self._decorate(oid,kind,ai)
            if kind in ('npc','special'):
                self.owned.add(oid);row['_eub_enemy']=True
            if kind=='special':self.special[oid]=dict(species=species,next=self.rng.uniform(.2,1),phase='idle',volley=0,direction=-1 if row['facing']==180 else 1)
        self._refresh_geometry()

    def _refresh_geometry(self):
        self.solids=[(*r['pos'],r['props'].get('wmp',0),r['props'].get('hmp',0),r['props']['terraintype']) for r in self.actors.values() if r['props'].get('terraintype') in (1,2)]
        self.walls=[r for r in self.solids if r[4]==1]

    @staticmethod
    def _fly_step(host,row,dt,vx,vy):
        x,y=row['pos'];w,h=row['props']['wmp'],row['props']['hmp'];nx=x+vx*dt;ny=y+vy*dt;hit=False
        for sx,sy,sw,sh,kind in npcs.surfaces(host):
            if kind!=1:continue
            if y+h>sy and y<sy+sh:
                if vx>0 and x+w<=sx and nx+w>sx:nx=sx-w;hit=True
                elif vx<0 and x>=sx+sw and nx<sx+sw:nx=sx+sw;hit=True
            if nx+w>sx and nx<sx+sw:
                if vy>0 and y+h<=sy and ny+h>sy:ny=sy-h;hit=True
                elif vy<0 and y>=sy+sh and ny<sy+sh:ny=sy+sh;hit=True
        row['pos']=[max(host.bounds[0],min(nx,host.bounds[2]-w)),max(host.bounds[1],min(ny,host.bounds[3]-h))]
        return hit

    @staticmethod
    def _hop_ground_step(host,row,dt,vx):
        ai=row.get('ai') or {}
        hopping=(ai.get('species')=='light' and ai.get('hop_started') is not None
                 and not ai.get('hop_landed'))
        grounded,hit=npcs.ground_step(host,row,dt,vx)
        if hopping and not grounded and hit and vx:

            direction=-ai['hop_dir'];ai['hop_dir']=direction
            ai['_eub_hop_return_dir']=direction
            npcs.face(host,row,direction,host.room_time)
            host._record('eub_light_hop_wall',objid=row['objid'],direction=direction,
                position=list(row['pos']),evidence='D air side contact; reflection timing/return leg FIT')
        return grounded,hit

    def _npc_tick(self,row,dt,at):
        ai=row.get('ai') or {};was_hopping=ai.get('hop_started') is not None
        self._tick(self,row,dt,at)
        if (was_hopping and ai.get('hop_started') is None
                and '_eub_hop_return_dir' in ai and self.source_role!='wave_chamber'):

            ai['dir']=ai.pop('_eub_hop_return_dir')
            self._record('eub_light_hop_return',objid=row['objid'],direction=ai['dir'],
                evidence='D154 postland heading; reflected-leg recovery FIT')

    def _sync_actor(self,row,at):
        """The target is this slice's END, so its interpolation starts at 0.

        Dumb Boss's 80ms display schedule is not reused as a second clock for
        this common snapshot host. No-op birth frames emit no movement: all
        ordinary/end-spawned bullets have epoch=end and integrate dt=0 then.
        """
        if max(abs(row['pos'][i]-row['sent'][i]) for i in (0,1))<1:return
        self.batch.move_to(row['objid'],round(row['pos'][0]),round(row['pos'][1]),
                           dt=max(1,self.host._slice_ms),offset=0)
        row['sent']=list(row['pos']);row['sent_at']=at;row['send_at']=at

    def _spawn_shot(self,key,creator,at,**kwargs):

        kwargs.setdefault('lifetime_ms',self.pack.projectile_props(key).get('lifetime',0))
        return DumbActorHost._spawn_shot(self,key,creator,at,**kwargs)

    def _native_velocity(self,oid,at):
        """Mirror the actual integer MOVE.vel clock, including its caps."""
        row=self.actors[oid];vx,vy=row['vel'];ax,ay=row['accel']
        cap=round(row.get('max_speed',195312.5)/3.90625)
        velocity=dict(xvel=round(vx/3.90625),yvel=round(vy/3.90625),
            xaccel=round(ax/3906.25),yaccel=round(ay/3906.25),minxvel=-cap,maxxvel=cap)
        model=NativeMotion(*row['pos']);model.move=dict(vel=velocity)
        row.update(native_velocity=True,_native_motion=model,
                   _native_motion_ms=self.host._sent_ms+self._offset(at))
        self.batch.modify(oid,'MOVE',message=('Properties_Move',
            self.host.normalize('Properties_Move',dict(vel=velocity))),offset=self._offset(at))

    def _step_projectiles(self,start,end):
        native={oid:row for oid,row in self.actors.items() if row.get('native_velocity') and row['kind']=='projectile'}
        for row in native.values():row['kind']='eub-native-velocity'
        DumbActorHost._step_projectiles(self,start,end)
        for oid,row in native.items():
            row['kind']='projectile'
            if oid not in self.actors:continue
            point=list(row['_native_motion'].position(max(0,self.host.wire_end_ms-row['_native_motion_ms'])/1000))
            size=(row['props']['wmp'],row['props']['hmp'])
            hits=[t for wall in self.walls if (t:=sweep_entry(row['pos'],point,size,wall[:4])) is not None]
            if hits:
                fraction=max(0,min(hits)-1e-6)
                point=[row['pos'][axis]+(point[axis]-row['pos'][axis])*fraction for axis in (0,1)]
            row['pos']=point
            expired=self.host.wire_lifetime_due(oid)
            blocked=bool(hits) or self._outside(*point,row['props']) or self._blocked(*point,row['props'])
            if expired or blocked:
                row['split_origin']=list(row['sent']);self._sync_actor(row,end)
                self.counts['lifetime' if expired else 'wall_hits']+=1
                self._retire(oid,6 if expired else 5,end)

    def _munitions_burst(self,row,at):
        previous=self._spawn_creator;self._spawn_creator=row['objid']
        try:DumbActorHost._munitions_burst(self,row,at)
        finally:self._spawn_creator=previous

    def spawn(self,species,pos,creator=0):
        self._spawn_creator=creator
        try:return self._spawn_npc(species,self.room_time,pos)
        finally:self._spawn_creator=0

    def shot(self,vid,creator,pos,velocity,*,gravity=0.,changes=None,kind='projectile'):
        oid=self.host._create(vid,pos,creator,changes)
        saved=self.actors[oid];row=self._decorate(oid,kind)
        row.update(vel=list(velocity),accel=[0.,gravity]);row['_eub_projectile']=True;self.owned.add(oid)
        deadline=self.host.wire_lifetime_deadline(oid);row['expire']=deadline/1000 if deadline is not None else None
        self.host.created_at_end(oid)
        self.counts['shots']+=1
        return oid

    def _retire(self,oid,reason,at=None,native=False):
        if oid not in self.actors:return
        if reason in (7,'CANCEL','INTERNAL_CANCEL','DESTROYREASON_CANCEL'):
            self.host._retire(oid,'CANCEL',native);self.owned.discard(oid);self.special.pop(oid,None);self.custom.pop(oid,None);return
        row=self.actors[oid]
        if row['vid']=='dumb.robosweeperbullet':self._burst(row,'dumb.shrapnel',8,39000.,15)
        elif row['vid']=='dumb.explodingproj':
            fx=self.host.pack.props('dumb.explodingprojx')
            point=[row['pos'][0]+(row['props']['wmp']-fx['wmp'])/2,row['pos'][1]+(row['props']['hmp']-fx['hmp'])/2]
            self.shot('dumb.explodingprojx',oid,point,(0,0),kind='fx')
        if row['kind']=='special' and is_hp_death(reason):
            self.supplies.killed(row['pos'][0]+row['props']['wmp']/2,row['pos'][1]+row['props']['hmp']/2)
        self.custom.pop(oid,None);self.special.pop(oid,None)
        DumbActorHost._retire(self,oid,reason,self.room_time if at is None else at,native=False)
        self.host._lifetimes.pop(oid,None);self.owned.discard(oid)
        callback=getattr(self.host.mechanics,'enemy_retired',None)
        if callback:callback(oid,reason)

    def _burst(self,row,vid,count,speed,damage):
        x,y=row['pos']
        for index in range(count):
            angle=2*math.pi*index/count
            self.shot(vid,row['objid'],(x,y),(speed*math.cos(angle),speed*math.sin(angle)),changes=dict(damage=damage))
        self.counts['splits']+=1

    def expire(self,oid):
        if oid not in self.owned:return False
        self._retire(oid,6);return True

    def feedback(self,event):
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        token=event.get('event_id')
        if token is not None:
            if token in self._seen:return True
            self._seen.add(token)
        row=self.actors.get(oid)
        if row is None:return True
        if row['props'].get('invulnerable'):return True
        if event.get('kind')=='damage' and row.get('hp') is not None:
            row['hp']=max(0,row['hp']-max(0,int(event.get('value',0))));row['props']['hitpoints']=row['hp']
            self.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self._offset());self._note_npc_damage(row,self.room_time)
            self.counts['native_damage']+=1
            if row['hp']<=0:self._retire(oid,2)
        elif event.get('kind')=='destroy':
            reason=event.get('reason',1);self._retire(oid,2 if is_hp_death(reason) else 'CANCEL' if reason=='CANCEL' else reason)
        return True

    def _special_step(self,oid,state,dt):
        row=self.actors.get(oid)
        if row is None:return
        p=self.player
        if p is None or not p.alive:return
        x,y=row['pos'];w,h=row['props']['wmp'],row['props']['hmp']
        dx=p.xmp+p.wmp/2-x-w/2;dy=p.ymp+p.hmp/2-y-h/2;now=self.room_time
        species=state['species'];near=math.hypot(dx,dy)<=SIM['activation_px']*256
        if species=='towersegment':
            npcs.ground_step(self,row,dt,0.)
            if near and now>=state['next']:
                direction=1 if dx>=0 else -1;flight=self.rng.choice(TOWER_FLIGHTS)
                self._anim(oid,0,1188,now);state.update(next=now+SIM['tower_cycle_s'],fire=now+.955,flight=flight,direction=direction)
            if state.get('fire',math.inf)<=now:
                vx,vy,g=state['flight'];state.pop('fire')
                self.shot('dumb.munitions',oid,(x+(w if state['direction']>0 else -4096),y+8192),(state['direction']*vx,vy),gravity=g)
        elif species=='robosweeper':
            speed=SWEEPER_SPEEDS_MP_S[state['direction']]
            if self._fly_step(self,row,dt,state['direction']*speed,0):state['direction']*=-1
            npcs.face(self,row,state['direction'],now);self._pose(oid,2,now)
            if near and now>=state['next'] and 'fire' not in state:
                delay=self.host.presentation.sweeper_prepare(oid)
                state['fire']=now+delay
                state['next']=now+SIM['sweeper_shot_s']
            if state.get('fire',math.inf)<=now:
                state.pop('fire');x,y=row['pos']

                origin=(x+(6144 if row['facing']==0 else 2048),y+13312)
                tx,ty=p.center();angle=math.atan2(ty-origin[1],tx-origin[0])
                speed=(SIM['sweeper_shot_speed'] if self.source_role=='wave_chamber' else SWEEPER_SHOT_SPEED_MP_S)
                self.shot('dumb.robosweeperbullet',oid,origin,
                    (math.cos(angle)*speed,math.sin(angle)*speed))
        elif species=='shieldturret':
            if near and now>=state['next']:
                npcs.face(self,row,dx,now);self._anim(oid,2,462,now)
                state.update(next=now+SIM['shield_cycle_s'],shot_at=now+SHIELD['windup_s'],left=8,direction=1 if dx>=0 else -1)
            if state.get('left',0) and now>=state['shot_at']:
                direction=state['direction'];pos=(x+(10496 if direction>0 else -4864),y+10752)
                child=self.shot('dumb.turretwave',oid,pos,(direction*SHIELD['speed'],0))
                self.actors[child]['kind']='custom-projectile';self.custom[child]=dict(origin=pos,born=now,direction=direction)
                self.host.presentation.shield_shot(oid);state['left']-=1;state['shot_at']+=SHIELD['gap_s']
                if not state['left']:
                    state['volley']+=1
                    if state['volley']%2==0:
                        wall=self.host._create('dumb.shieldwall',(x+(16384 if direction>0 else -8192),y),oid)
                        self._decorate(wall,'static');self.owned.add(wall);self.host.created_at_end(wall)
                        self.host.presentation.sound(wall,'dumb.shieldwallgen',at_end=True)
                        self.host.record('shield_created',parent=oid,child=wall,evidence='D independent250HP/armor75/lifetime3000; SIM placement timing')
        elif species=='tank':
            if near and now>=state['next']:
                self.shot('dumb.tankmunitions',oid,(x+w/2,y),(0,20000),gravity=340000.)
                state['next']=now+4.
        if oid in self.actors:self._sync_actor(row,now)

    def step(self,dt):
        self.player=self.host.player
        for oid,row in list(self.actors.items()):
            if 'kind' not in row:self._decorate(oid)
        self._refresh_geometry()
        start=self.room_time-dt;end=self.room_time
        if self.controller is not None and self.player is not None and self.player.alive:
            events=[]
            if not self.wave_started:events.extend(self.controller.begin());self.wave_started=True
            events.extend(self.controller.step(dt))
            for event in events:
                if event['kind']=='spawn_batch':
                    weights=self.controller.row['species_weights']
                    for _ in range(event['count']):
                        species=self.rng.choices(list(weights),weights=list(weights.values()),k=1)[0]
                        lanes=[lane for lane in self.host.pack.wave_rules['spawn_lanes'] if lane['species'].get(species)]
                        lane=self.rng.choice(lanes);x,feet,width,_=lane['rect_mp'];body=self.pack.species_props(species)
                        left=self.rng.uniform(x,max(x,x+width-body['wmp']))
                        position=[left,feet-body['hmp']]
                        fx=self.host._create('dumb.largeteleportfxenemy' if species=='heavy' else 'dumb.teleportfxenemy',position,0)
                        self._decorate(fx,'fx')
                        self.host.created_at_end(fx)
                        self.wave_pending.append(dict(at=end+.775,species=species,pos=position,fx=fx))
                elif event['kind']=='complete':self.host.mechanics.unlock_exits('encounter:wave_chamber')
            for pending in list(self.wave_pending):
                if end<pending['at']:continue
                oid=self.spawn(pending['species'],pending['pos']);self.controller.npc_created(oid);self.wave_pending.remove(pending)

        saved={}
        for oid in self.owned:
            row=self.actors.get(oid)
            if row is not None and row.get('expire') is not None:
                saved[oid]=row['expire'];row['expire']=-math.inf if self.host.wire_lifetime_due(oid) else math.inf
        self._step_dynamics(start,end)
        for oid,value in saved.items():
            if oid in self.actors:self.actors[oid]['expire']=value
        for oid,state in list(self.special.items()):self._special_step(oid,state,dt)
        for oid,state in list(self.custom.items()):
            row=self.actors.get(oid)
            if row is None:continue
            if state.get('ricochet'):
                deadline=row.get('expire');row['expire']=-math.inf if self.host.wire_lifetime_due(oid) else math.inf
                self._step_bounding(row,dt,end)
                if oid in self.actors:self.actors[oid]['expire']=deadline
                continue
            age=end-state['born'];x=state['origin'][0]+state['direction']*SHIELD['speed']*age
            y=state['origin'][1]+SHIELD['amplitude']*math.sin(2*math.pi*age/SHIELD['period_s'])
            hit=any(sweep_entry(row['pos'],(x,y),(row['props']['wmp'],row['props']['hmp']),wall[:4]) is not None for wall in self.walls)
            if hit:self._retire(oid,5)
            else:row['pos']=[x,y];self._sync_actor(row,end)

    def status(self):return dict(owned=len(self.owned),ordinary=sum(r.get('kind')=='npc' for r in self.actors.values()),special=len(self.special),counts=dict(self.counts),policy=dict(SIM),wave=self.controller.status() if self.controller is not None else None)

EubEnemies=ExplorationEnemies
