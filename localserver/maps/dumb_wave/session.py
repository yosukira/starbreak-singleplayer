'Native Wave host; no boss course, preruntime trajectories or damage.'
import copy
import heapq
import math
import random
import threading
import time
from pathlib import Path
from ... import game_settings
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ..dumb_common.actors import DumbActorHost,DESTROY_HITPOINTS,DESTROY_LIFETIME
from .scene import DumbWavePack
from .controller import DumbWaveController

class DumbWaveLiveSession(DumbActorHost):
    HZ=60.
    MAP_ID='dumb_wave'
    PACK_CLASS=DumbWavePack
    CONTROLLER_CLASS=DumbWaveController

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None):
        self.server,self.channel,self.options,self.room=server,channel,options,room
        self.log=log or (lambda msg:None);self.layouts=getattr(server,'layouts',None)or pb_layout.default_layouts()
        path=Path(getattr(options,'create_world_hex','')or '').parent
        self.pack=pack or self.PACK_CLASS(path if (path/'waves.json').exists()else None)
        self.worldid=int(getattr(options,'worldid',1)or 1);self.batch=DeltaBatch(self.layouts,self.worldid)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550)or 21550),log=self.log)
        self.owns_bridge=bridge is None
        self.seed=random.SystemRandom().getrandbits(64) if seed is None else seed
        self._stop=threading.Event();self._thread=None;self.prepare()

    def prepare(self):
        self.room_time=0.;self.phase='waiting';self.player=None;self.controller=None
        self.hp_factor=game_settings.balance('dumb_wave')['enemy_hp'] if self.MAP_ID=='dumb_wave' else 1.
        self.actors={};self.retired=set();self.queue=[];self.events=[];self.sequence=0;self.frames=0
        self.drone_children={};self.drone_parents={};self.rng=random.Random(self.seed)
        self.supplies=SupplyDrops(self,self.MAP_ID);self._wire_ms=0.;self._sent_ms=0;self._frame_start=0.
        self._next_id=max(map(int,self.pack.templates))+1000
        self.counts=dict(spawns=0,npcs=0,shots=0,native_damage=0,native_destroy=0,splits=0,cap_skips=0,wall_hits=0,lifetime=0)
        self.npc_regions={i:dict(rect=r['rect_mp'])for i,r in self.pack.scene['regions'].items()}
        self.npc_ceiling=self.pack.scene['playable_bounds_mp'][1]
        for i,p in self.pack.templates.items():self._add_actor(int(i),p,0.,kind='static')
        self.solids=self._solid_rects();self.walls=[r for r in self.solids if r[4]==1]
        self.bounds=tuple(self.pack.scene['bounds_mp']);self.batch.clear()

    def start(self):
        if self.owns_bridge:self.bridge.start()
        self.prepare();self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True)
        self._thread.start();self.log(self.MAP_ID+' live session started')

    def stop(self,stop_bridge=True):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=1)
        if stop_bridge and self.owns_bridge:self.bridge.stop()

    def _run(self):
        dt=1/self.HZ;deadline=time.monotonic()
        while not self._stop.is_set():
            deadline+=dt
            try:self.step(dt)
            except Exception:
                import traceback
                self.log(self.MAP_ID+' tick failed: '+traceback.format_exc());self._stop.set();break
            self._stop.wait(max(0.,deadline-time.monotonic()))
            if deadline<time.monotonic()-dt:deadline=time.monotonic()

    def _available_spawn_species(self,spec):
        return spec['species_weights']

    def _species_props(self,species):
        props=super()._species_props(species)
        if props is not None and self.hp_factor!=1. and props.get('hitpoints',0)>0:
            hp=props['hitpoints']
            props['hitpoints']=max(1,round(hp*self.hp_factor))
            props['maxhitpoints']=max(1,round(props.get('maxhitpoints',hp)*self.hp_factor))
        return props

    def _choose_spawn_species(self,spec,allowed=None):
        names=[n for n in spec['species_weights']if allowed is None or n in allowed]
        return self.rng.choices(names,weights=[spec['species_weights'][n]for n in names],k=1)[0]

    def _batch_region(self,spec,count):
        if count<=1 or spec.get('kind')!='mixed':return None
        available=self._available_spawn_species(spec)
        floor=self.pack.scene['playable_bounds_mp'][3]
        eligible=[]
        for identity,r in self.pack.scene['regions'].items():
            pool=spec['region_species'].get(identity,r['species'])
            allowed={n for n in pool if n not in ('heavy','sniper')and available.get(n,0)>0}
            if not allowed:continue
            if hasattr(self,'roster')and sum(available.get(n,0)for n in allowed)<count:continue
            rect=r['rect_mp']
            if count>=4 and (abs(rect[1]-floor)>256 or rect[2]<100000):continue
            eligible.append((identity,r,allowed,sum(pool.get(n,0)for n in allowed)))
        if not eligible:raise ValueError('No compatible recorded batch region')
        return self.rng.choices(eligible,weights=[r[3]for r in eligible],k=1)[0]

    def _spawn_batch(self,event):
        pattern=event.get('pattern_wave',min(event['wave'],self.pack.waves['wave_count']))
        spec=self.pack.waves['waves'][pattern-1]
        grouped=self._batch_region(spec,event['count'])
        for _ in range(event['count']):
            name=self._choose_spawn_species(spec,grouped[2]if grouped else None)
            eligible=[]
            for i,r in self.pack.scene['regions'].items():
                observed=spec['region_species'].get(i,{})
                frequency=observed.get(name,0)
                if frequency:eligible.append((i,r,frequency))

            if not eligible:
                eligible=[(i,r,r['species'][name])for i,r in self.pack.scene['regions'].items()if r['species'].get(name)]
            region=(grouped[0],grouped[1],1)if grouped else self.rng.choices(eligible,weights=[r[2]for r in eligible],k=1)[0]
            identity,rule,_=region;rx,feet,rw,_=rule['rect_mp'];props=self.pack.species_props(name)
            center=self.rng.uniform(rx,rx+rw)
            lo,_,hi,_=self.pack.scene['playable_bounds_mp']
            x=max(lo,min(hi-props['wmp'],center-props['wmp']/2));y=feet-props['hmp']
            vid='dumb.largeteleportfxenemy'if name=='heavy'else 'dumb.teleportfxenemy'
            fx=copy.deepcopy(self.pack.effects[vid]);fx.update(xmp=round(x+props['wmp']/2-fx['wmp']/2),ymp=feet-fx['hmp'])
            self._next_id+=1;row=self._add_actor(self._next_id,fx,event['at'],kind='fx')
            row['expire']=event['at']+fx['lifetime']/1000
            self.batch.create(row['objid'],self.pack.wire_props(fx),creatorid=int(identity),offset=self._offset(event['at']))
            self.batch.sound(int(identity),'dumb.teleportenemy',offset=self._offset(event['at']))
            self._record('wave_fx',event['at'],wave=event['wave'],species=name,region=identity,
                pattern_wave=pattern,batch_count=event['count'],feet_y=feet,x=round(x))
            self._enqueue(event['at']+self.pack.waves['spawn_delay_s'],'npc_spawn',species=name,position=(x,y))

    def _controller_event(self,event):
        kind=event['kind'];at=event['at']
        if kind=='title':self.batch.general.append(dict(offset_=self._offset(at),worldtitle_=dict(title_=event['text'],x_=50,y_=20)))
        elif kind=='spawn_batch':self._spawn_batch(event)
        self._record(kind,at,**{k:v for k,v in event.items()if k not in ('kind','at')})

    def _teleport_fx(self,event):
        pads={p['source']:p['destination']for p in self.pack.scene['telepads']}
        source,destination=event.get('source'),event.get('destination')
        if pads.get(source)!=destination:return
        for i in (source,destination):
            p=self.pack.templates[str(i)];fx=dict(xmp=p.get('xmp',0)+3328,ymp=p['ymp']+3328,
                wmp=17920,hmp=38400,z=-5,lifetime=594,vid='dumb.teleportfx')
            self._next_id+=1;row=self._add_actor(self._next_id,fx,self.room_time,kind='fx')
            row['expire']=self.room_time+.594;self.batch.create(row['objid'],self.pack.wire_props(fx))
            self.batch.modify(i,'VID',stringval='dumb.telepadtrigger')
            self.actors[i]['vid']='dumb.telepadtrigger'
            self._enqueue(self.room_time+.264,'pad_reset',objid=i)
        self.batch.sound(source,'dumb.teleport',offset=0)
        self._record('teleport',self.room_time,source=source,destination=destination)

    def _feedback(self,event):
        if not isinstance(event,dict):return
        if event.get('kind')=='teleport':self._teleport_fx(event);return
        if int(event.get('ownerid',self.worldid))!=self.worldid:return
        row=self.actors.get(int(event.get('victim')or 0))
        if row is None or row['kind']not in ('npc','projectile','fx'):return
        if event.get('kind')=='damage'and event.get('value',0)>0 and row['hp']is not None:
            self._note_npc_damage(row,self.room_time)
            self.counts['native_damage']+=1;row['hp']-=int(event['value'])
            if row['hp']<=0:self._retire(row['objid'],DESTROY_HITPOINTS,self.room_time)
            else:self.batch.modify(row['objid'],'HITPOINTS',intval=row['hp'])
        elif event.get('kind')=='destroy':
            reason=int(event.get('reason')or 1)
            if row['vid']=='dumb.drone'and reason==DESTROY_LIFETIME and row['expire']is None:return
            self.counts['native_destroy']+=1;self._retire(row['objid'],reason,self.room_time,native=True)

    def step(self,dt):
        if not math.isfinite(dt)or dt<0:raise ValueError('dt must be finite and nonnegative')
        state,_=self.bridge.snapshot()
        if state is not None:self.player=state
        self._frame_start=self.room_time
        if self.controller is None and state and state.alive and state.age()<2:
            self.controller=self.CONTROLLER_CLASS(self.pack,self.seed)
            self.room_time=self._frame_start=0.
            for event in self.controller.begin():self._controller_event(event)
        for event in self.bridge.poll_events():self._feedback(event)
        if self.controller:
            start,end=self.room_time,self.room_time+dt
            for event in self.controller.step(dt):self._enqueue(event['at'],'controller',event=event)
            while self.queue and self.queue[0][0]<=end+1e-9:
                at,_,event=heapq.heappop(self.queue)
                if event['kind']=='controller':self._controller_event(event['event'])
                elif event['kind']=='npc_spawn':
                    identity=self._spawn_npc(event['species'],at,event['position']);self.controller.npc_created(identity)
                elif event['kind']=='pad_reset':
                    identity=event['objid'];self.actors[identity]['vid']='dumb.telepad'
                    self.batch.modify(identity,'VID',stringval='dumb.telepad',offset=self._offset(at))
            self._step_dynamics(start,end);self.room_time=end;self.phase=self.controller.state
        self.supplies.flush();self._wire_ms+=dt*1000;target=round(self._wire_ms)
        self.batch.dt=target-self._sent_ms;self._sent_ms=target
        self.server._send_raw(self.channel,12,self.batch.encode(),'dumb-wave-live',record=False)
        self.frames+=1;self.batch.clear()

    def status(self):
        return dict(map=self.MAP_ID,phase=self.phase,time=round(self.room_time,3),frames=self.frames,
            controller=self.controller.status()if self.controller else None,
            npcs=sum(r['kind']=='npc'for r in self.actors.values()),counts=dict(self.counts))
