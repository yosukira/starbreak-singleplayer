"""Native protocol/bridge adapter for the Bonesaw controller."""
import math
import random
import threading
import time
from pathlib import Path
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from .controller import BonesawController
from .scene import BonesawPack

class BonesawLiveSession:
    HZ=60.

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None,players=None):
        self.server,self.channel,self.options=server,channel,options;self.log=log or (lambda message:None)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        root=Path(getattr(options,'create_world_hex','') or '').parent
        self.pack=pack or BonesawPack(root if (root/'entities.json').exists() else None)
        self.seed=seed if seed is not None else random.SystemRandom().randrange(1<<30)
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None;self.players_provider=players
        self.batch=DeltaBatch(self.layouts,self.worldid);self._stop=threading.Event();self._thread=None
        self.prepare()

    def prepare(self):
        self.controller=BonesawController(self.pack,self.seed);self.batch.clear()
        self.phase='waiting';self.room_time=0.;self.bootstrapped=False;self.events=[];self.retired=set();self.created={}
        self.frames=0;self.native_damage=0;self.max_tick_ms=0.;self._wire_ms=0.;self._sent_ms=0
        self._frame_start=0.;self._event_time=0.;self.player=None;self.players={}

    def start(self):
        if self.owns_bridge:self.bridge.start()
        self.prepare();self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
        self.log('sc_bonesaw: Star Cathedral Bonesaw, live independent bodies and native lasers')

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
                self.log('sc_bonesaw tick failed: '+traceback.format_exc());self._stop.set();break
            deadline+=1/self.HZ
            if deadline<time.monotonic()-1/self.HZ:deadline=time.monotonic()+1/self.HZ

    def record(self,kind,**fields):
        self.events.append(dict(kind=kind,at=self._event_time,**fields))
        if len(self.events)>6000:self.events=self.events[-4000:]

    def _action(self,e):
        self._event_time=e['at'];kind=e['kind'];oid=e.get('id');off=max(0,round((e['at']-self._frame_start)*1000))
        if kind=='props':self.batch.props(oid,e['values'],offset=off)
        elif kind=='anim':self.batch.animprops(oid,e['anim'],round(e['duration']*1000),offset=off)
        elif kind=='sound':self.batch.sound(oid,e['name'],offset=off)
        elif kind=='body_move':
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(to_=dict(xmp_=round(e['x']*256),ymp_=round(e['y']*256),dt_=round(e['duration']*1000)))),offset=off)
        elif kind=='move':self.batch.move_to(oid,*[round(x*256) for x in e['pos']],dt=50,offset=off)
        elif kind=='create':
            a=e['actor'];p=self.pack.props(a['vid']) if a['vid'] else {}
            p.update(a.get('properties',{}));p.update(xmp=round(a['pos'][0]*256),ymp=round(a['pos'][1]*256))
            for key in ('facing','pose'):
                if key in a:p[key]=a[key]
            if a.get('attach'):

                p['move']=dict(relativeto=dict(objid=a['attach']),to=dict(
                    xmp=round((32+a['relative'][0]-p['wmp']/512)*256),
                    ymp=round((32+a['relative'][1]-p['hmp']/512)*256)))
            self.batch.create(a['id'],self.pack.wire_props(p),creatorid=a['owner'],offset=off)
            lifetime=p.get('lifetime')
            self.created[a['id']]=e['at']+lifetime/1000 if type(lifetime)is int and lifetime>0 else None
        elif kind in ('remove','retire_turret'):
            if oid in self.retired:return
            self.retired.add(oid);self.batch.destroy(oid,self.worldid,e.get('reason','INTERNAL'),offset=off)
            self.created.pop(oid,None)
        elif kind=='boss_dying':
            b=self.controller.bosses[oid]
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(to_=dict(xmp_=round(b['x']*256),ymp_=round(b['y']*256),dt_=1))),offset=off)
            self.batch.props(oid,dict(INVULNERABLE=True,DAMAGE=0),offset=off)
        elif kind=='boss_dead':
            self.batch.props(oid,dict(VID='sc.wplatformdest',OWNERTYPE=0,TERRAINTYPE=0,DAMAGE=0,HITPOINTS=-1),offset=off)
        elif kind=='victory':
            self.phase='done'
            self.batch.props(self.pack.scene['exit_id'],dict(VID='sc.coredoor',INTERACT=255),offset=off)
            self.batch.props(self.pack.scene['entry_id'],dict(VID='sc.door',INTERACT=255),offset=off)
        if kind not in ('move','props','create','remove'):
            self.record(kind,**{k:v for k,v in e.items() if k not in ('kind','at')})

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0 or objid in self.retired:return None
        if objid in self.controller.bosses or objid in self.controller.turrets:
            return dict(ownerid=self.worldid,objid=objid,role='npc',expires_at=None,
                room_time=self.controller.time,lifetime_due=False)
        actor=self.controller.actors.get(objid)
        if actor is None or objid not in self.created:return None
        role='projectile' if actor['kind']=='spirit' else 'fx'
        deadlines=[v for v in (actor.get('until'),self.created[objid])
            if type(v)in (int,float) and math.isfinite(v) and v>=0]
        expiry=min(deadlines) if deadlines else None
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
            for a in self.controller.add_damage(value,oid,e.get('player_id'),e.get('event_id')):self._action(a)
        elif e.get('kind')=='destroy':
            for a in self.controller.native_destroy(oid,int(e.get('reason') or 0)):self._action(a)

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        began=time.perf_counter();self._frame_start=self.controller.time;self._event_time=self._frame_start
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
        self.server._send_raw(self.channel,12,self.batch.encode(),'sc-bonesaw-live',record=False)
        self.batch.clear();self.frames+=1;self.max_tick_ms=max(self.max_tick_ms,(time.perf_counter()-began)*1000)

    def status(self):
        return dict(map='sc_bonesaw',phase=self.phase,seed=self.seed,time=self.room_time,controller=self.controller.status(),
                    frames=self.frames,native_damage=self.native_damage,max_tick_ms=self.max_tick_ms)
