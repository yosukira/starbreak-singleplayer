"""Native Fire Forest transport; bridge damage is already armour-mitigated."""
import math
import random
import threading
import time
from pathlib import Path
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from .controller import ForestController
from .scene import ForestPack

class ForestLiveSession:
    KIND='mid'
    HZ=60.

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None,players=None):
        self.server,self.channel,self.options=server,channel,options
        self.log=log or (lambda message:None)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        self.seed=getattr(options,'room_roll_seed',None)
        if self.seed is None:self.seed=seed if seed is not None else random.SystemRandom().randrange(1<<30)
        root=Path(getattr(options,'create_world_hex','') or '').parent

        self.pack=pack or ForestPack(self.KIND,root if (root/'entities.json').exists() else None,
                                    seed=self.seed if getattr(options,'room_roll_seed',None) is not None else 0)
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None;self.players_provider=players
        self.batch=DeltaBatch(self.layouts,self.worldid)
        self._stop=threading.Event();self._thread=None
        self.prepare()

    def prepare(self):
        self.controller=ForestController(self.pack,self.seed)
        self.supplies=SupplyDrops(self,'forest_'+self.KIND);self.batch.clear()
        self.phase='waiting';self.room_time=0.;self.bootstrapped=False
        self.players={};self.player=None;self.events=[];self.retired=set();self.created=set()
        self._native_expiry={}
        self.native_damage=self.native_destroy=self.frames=0;self.max_tick_ms=0.
        self.max_tick_cpu_ms=0.
        self._wire_ms=0.;self._sent_ms=0;self._frame_start=0.;self._event_time=0.

    def start(self):
        if self.owns_bridge:self.bridge.start()
        self.prepare();self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
        self.log('forest_'+self.KIND+': procedural native terrain and live encounter')

    def stop(self,stop_bridge=True):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:self.bridge.stop()

    def _run(self):
        previous=time.monotonic();deadline=previous+1/self.HZ
        while not self._stop.is_set():
            self._stop.wait(max(0,deadline-time.monotonic()))
            if self._stop.is_set():break
            now=time.monotonic();dt=now-previous;previous=now
            try:self.step(dt)
            except Exception:
                import traceback
                self.log('fireforest tick failed: '+traceback.format_exc());self._stop.set();break
            deadline+=1/self.HZ
            if deadline<time.monotonic()-1/self.HZ:deadline=time.monotonic()+1/self.HZ

    def _offset(self):return max(0,round((self._event_time-self._frame_start)*1000))

    def record(self,kind,**fields):
        self.events.append(dict(kind=kind,at=self._event_time,**fields))
        if len(self.events)>6000:self.events=self.events[-4000:]

    def _action(self,e):
        self._event_time=e['at'];kind=e['kind'];oid=e.get('id');off=self._offset()
        if kind=='props':self.batch.props(oid,e['values'],offset=off)
        elif kind=='anim':self.batch.animprops(oid,e['anim'],round(e['duration']*1000),offset=off)
        elif kind=='sound':self.batch.sound(oid,e['name'],offset=off)
        elif kind=='shake':self.batch.shake(e['magnitude'],e['duration'],offset=off)
        elif kind=='move':self.batch.move_to(oid,*[round(v*256) for v in e['pos']],dt=50,offset=off)
        elif kind=='motion_path':
            points=[dict(xmp_=round(p['pos'][0]*256),ymp_=round(p['pos'][1]*256),dt_=p['dt_ms']) for p in e['points']]
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(waypoint_=dict(points_=points,loop_=False))),offset=off)
        elif kind=='motion_path_end':self.batch.modify(oid,'MOVE',offset=off)
        elif kind=='create':
            a=e['actor'];oid=a['id'];p=self.pack.props(a['vid'])
            p.update(a.get('properties',{}))
            p.update(xmp=round(a['pos'][0]*256),ymp=round(a['pos'][1]*256),facing=a['facing'],pose=a['pose'])
            if a.get('hp') is not None:p['hitpoints']=a['hp']
            if a['kind'] in ('fx','seed','groundseed','part','flame','fireball','firebomb','tail'):
                p.update(lifetime=max(1,round((a['expiry']-a['born'])*1000)) if math.isfinite(a['expiry']) else -1)
            self.created.add(oid)
            lifetime=p.get('lifetime')
            self._native_expiry[oid]=e['at']+lifetime/1000. if type(lifetime)in (int,float) and math.isfinite(lifetime) and lifetime>0 else None
            self.batch.create(oid,self.pack.wire_props(p),creatorid=a.get('owner',0),offset=off)
        elif kind=='platform_accelerate':
            self.batch.props(oid,dict(Z=55,BLOCKTYPE=4),offset=off)
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(vel_=dict(yvel_=-10000,yaccel_=-20,
                minyvel_=-round(e['speed']*65536/1000)))),offset=off)
        elif kind=='remove':
            a=e['actor'];oid=a['id']
            if oid in self.retired:return
            self.retired.add(oid)
            self._native_expiry.pop(oid,None)
            if a['kind'] in ('frog','bug') and e['reason']=='HITPOINTS':
                self.supplies.killed((a['pos'][0]+a['size'][0]/2)*256,(a['pos'][1]+a['size'][1])*256)
            self.batch.destroy(oid,self.worldid,e['reason'],offset=off)
        elif kind=='boss_destroy':
            if oid not in self.retired:
                self.batch.world_sound('forest.goblinmasterdeath' if self.KIND=='mid' else 'forest.goblinmotherdeath',offset=off)
                self.batch.destroy(oid,self.worldid,e['reason'],offset=off);self.retired.add(oid)
        elif kind=='victory':
            self.phase='done'
            self.batch.world_sound('forest.gatepowerdown',offset=off)
            self.batch.destroy(self.pack.scene['gate_id'],self.worldid,'INTERNAL',offset=off)
            self.batch.props(self.pack.scene['portal_id'],dict(INTERACT=255),offset=off)
        if kind not in ('move','props','create','remove'):
            self.record(kind,**{k:v for k,v in e.items() if k not in ('kind','at')})

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0 or objid in self.retired:return None
        if objid==self.controller.boss['id']:
            return dict(ownerid=self.worldid,objid=objid,role='npc',expires_at=None,room_time=self.controller.time,lifetime_due=False)
        a=self.controller.actors.get(objid)
        if a is None or objid not in self.created:return None
        p=self.pack.dynamic_templates[a['vid']]
        if p.get('terraintype') or p.get('interact'):return None
        role='projectile' if a['kind']in ('seed','groundseed','part','flame','fireball','firebomb') else 'npc' if a['kind']in ('frog','bug') else 'fx'
        deadlines=[v for v in (a.get('expiry'),self._native_expiry.get(objid)) if type(v)in (int,float) and math.isfinite(v) and v>=0]
        expiry=min(deadlines)if deadlines else None
        return dict(ownerid=self.worldid,objid=objid,role=role,expires_at=expiry,
                    room_time=self.controller.time,lifetime_due=expiry is not None and self.controller.time>=expiry)

    def _feedback(self,e):
        if not isinstance(e,dict) or ('ownerid' in e and int(e['ownerid'] or 0)!=self.worldid):return
        oid=int(e.get('victim') or 0)
        if oid in self.retired:return
        if e.get('kind')=='damage':
            value=int(e.get('value') or 0)
            if value<=0:return
            self.native_damage+=1
            for action in self.controller.add_damage(value,oid,e.get('player_id'),e.get('event_id')):self._action(action)
        elif e.get('kind')=='destroy':
            self.native_destroy+=1
            for action in self.controller.native_destroy(oid,int(e.get('reason') or 0)):self._action(action)

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        began=time.perf_counter();cpu_began=time.thread_time();self._frame_start=self.controller.time;self._event_time=self._frame_start
        state,_=self.bridge.snapshot();self.player=state
        if self.players_provider is not None:self.players=self.players_provider() if callable(self.players_provider) else self.players_provider
        elif state and state.alive and state.age()<2:self.players={(state.worldid,state.objid):dict(center=state.center(),alive=True)}
        else:self.players={}
        self.controller.set_players(self.players)
        if not self.bootstrapped and self.players:
            self.bootstrapped=True;self.controller.start();self.phase='fighting'
        for e in self.bridge.poll_events():self._feedback(e)
        for e in self.controller.step(dt):self._action(e)
        self.room_time=self.controller.time
        self._wire_ms+=dt*1000;ms=round(self._wire_ms);self.batch.dt=ms-self._sent_ms;self._sent_ms=ms
        self.server._send_raw(self.channel,12,self.batch.encode(),'fireforest-live',record=False)
        self.batch.clear();self.supplies.flush();self.frames+=1
        self.max_tick_ms=max(self.max_tick_ms,(time.perf_counter()-began)*1000)
        self.max_tick_cpu_ms=max(self.max_tick_cpu_ms,(time.thread_time()-cpu_began)*1000)

    def status(self):
        return dict(map='forest_'+self.KIND,phase=self.phase,seed=self.seed,time=self.room_time,controller=self.controller.status(),
                    frames=self.frames,native_damage=self.native_damage,native_destroy=self.native_destroy,max_tick_ms=self.max_tick_ms,max_tick_cpu_ms=self.max_tick_cpu_ms)
