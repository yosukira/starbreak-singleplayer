"""City protocol host: native artwork/audio/physics, authoritative live encounters."""
import math
from pathlib import Path
import random
import threading
import time
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ...native_death_policy import native_hp_prediction
from .scene import CityPack
from .mid import CityMidController
from .boss import CityBossController

class CityLiveSession:
    KIND='mid'
    HZ=60.

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None,players=None):
        self.server,self.channel,self.options=server,channel,options
        self.log=log or (lambda message:None)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        self.seed=seed if seed is not None else random.SystemRandom().randrange(1<<30)
        root=Path(getattr(options,'create_world_hex','') or '').parent
        self.pack=pack or CityPack(self.KIND,root if (root/'entities.json').exists() else None)
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None; self.players_provider=players
        self.batch=DeltaBatch(self.layouts,self.worldid)
        self._stop=threading.Event(); self._thread=None
        self.prepare()

    def prepare(self):
        self.controller=(CityMidController if self.KIND=='mid' else CityBossController)(self.pack,self.seed)
        self.supplies=SupplyDrops(self,'city_'+self.KIND); self.batch.clear()
        self.phase='waiting'; self.room_time=0.; self.bootstrapped=False
        self.players={}; self.player=None; self.events=[]; self.retired=set(); self.created=set()
        self._native_expiry={}
        self.native_damage=self.native_destroy=self.frames=0; self.max_tick_ms=0.
        self.tick_samples=[];self.total_payload_bytes=0;self.max_payload_bytes=0;self.peak_actors=0;self.ticks_over_budget=0
        self._wire_ms=0.; self._sent_ms=0; self._frame_start=0.; self._event_time=0.

    def start(self):
        if self.owns_bridge: self.bridge.start()
        self.prepare(); self._stop.clear(); self._thread=threading.Thread(target=self._run,daemon=True); self._thread.start()
        self.log('city_'+self.KIND+': Elite Vanquished live encounter with original City assets')

    def stop(self,stop_bridge=True):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread(): self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge: self.bridge.stop()

    def _run(self):
        previous=time.monotonic(); deadline=previous+1/self.HZ
        while not self._stop.is_set():
            self._stop.wait(max(0,deadline-time.monotonic()))
            if self._stop.is_set(): break
            now=time.monotonic(); dt=now-previous; previous=now
            try: self.step(dt)
            except Exception:
                import traceback
                self.log('city tick failed: '+traceback.format_exc()); self._stop.set(); break
            deadline+=1/self.HZ
            if deadline<time.monotonic()-1/self.HZ: deadline=time.monotonic()+1/self.HZ

    def _offset(self): return max(0,round((self._event_time-self._frame_start)*1000))

    def record(self,kind,**fields):
        self.events.append(dict(kind=kind,at=self._event_time,**fields))
        if len(self.events)>12000: self.events=self.events[-9000:]

    def _action(self,e):
        self._event_time=e['at']; kind=e['kind']; oid=e.get('id'); off=self._offset()
        if kind=='props': self.batch.props(oid,e['values'],offset=off)
        elif kind=='anim': self.batch.animprops(oid,e['anim'],round(e['duration']*1000),offset=off)
        elif kind=='sound':
            if oid: self.batch.sound(oid,e['name'],offset=off)
            else: self.batch.world_sound(e['name'],offset=off)
        elif kind=='shake': self.batch.shake(e['magnitude'],e['duration'],offset=off)
        elif kind=='damage_visual':
            self.batch.damage(self.worldid,oid,e['value'],mitigated=True,offset=off)
        elif kind=='move': self.batch.move_to(oid,*[round(v*256) for v in e['pos']],dt=50,offset=off)
        elif kind=='create':
            a=e['actor']; oid=a['id']; p=self.pack.props(a['vid'])
            p.update(a.get('properties',{}))
            p.update(xmp=round(a['pos'][0]*256),ymp=round(a['pos'][1]*256),facing=a['facing'],pose=a['pose'],
                     lifetime=max(1,round((a['expiry']-a['born'])*1000)) if math.isfinite(a['expiry']) else -1)
            if a.get('hp') is not None: p['hitpoints']=max(1,math.ceil(a['hp'])) if a['hp']>=0 else a['hp']
            if a['kind'] in ('worm','wormpart'):
                for key in ('hitpoints','invulnerable','killachievement'): p.pop(key,None)
            if a['kind']=='anchor':
                p.pop('xmp',None); p.pop('ymp',None)
            if a['kind'] in ('midboss','body','tank','claw','flyer','infantry','skiff','hawk','copter','clone') and not (
                    getattr(self.bridge,'shared_room',False) and native_hp_prediction(p,'city_'+self.KIND)):
                p['removetype']=0
            self.created.add(oid)
            lifetime=p.get('lifetime')
            self._native_expiry[oid]=e['at']+lifetime/1000. if type(lifetime)in (int,float) and math.isfinite(lifetime) and lifetime>0 else None
            self.batch.create(oid,self.pack.wire_props(p),creatorid=a.get('owner',0),offset=off)
        elif kind=='cart_path':
            path=e['path']
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(waypoint_=dict(points_=[dict(
                xmp_=round(path['end'][0]*256),ymp_=round(path['end'][1]*256),dt_=round(path['duration']*1000))]))),offset=off)
        elif kind=='destroy_once':
            if oid not in self.retired:
                self._native_expiry.pop(oid,None)
                self.retired.add(oid);self.batch.destroy(oid,self.worldid,e['reason'],offset=off)
        elif kind=='remove':
            a=e['actor']; oid=a['id']
            if oid in self.retired: return
            self.retired.add(oid)
            self._native_expiry.pop(oid,None)
            if a['kind'] in ('flyer','infantry','skiff','hawk','copter') and e['reason']=='HITPOINTS':
                self.supplies.killed((a['pos'][0]+a['size'][0]/2)*256,(a['pos'][1]+a['size'][1])*256)
            if e.get('quiet'):

                self.batch.props(oid,dict(DRAW=False,VID=''),offset=off)
                self.batch.destroy(oid,self.worldid,'CANCEL',offset=off+1)
            else:self.batch.destroy(oid,self.worldid,e['reason'],offset=off)
        elif kind=='victory':
            self.phase='done'
            portal=self.pack.scene['portal_id']
            if self.KIND=='mid': self.batch.props(portal,dict(VID='city.nextDoor'),offset=off)
            self.batch.props(portal,dict(INTERACT=1),offset=off)
        if kind not in ('move','props','create','remove','cart_path'):
            self.record(kind,**{k:v for k,v in e.items() if k not in ('kind','at')})

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0 or objid in self.retired:return None
        a=self.controller.actors.get(objid)
        if a is None or (objid not in self.created and str(objid)not in self.pack.templates):return None
        p=self.pack.dynamic_templates.get(a['vid'],self.pack.templates.get(str(objid),{}))
        p=dict(p,**a.get('properties',{}))
        if p.get('terraintype') or p.get('interact'):return None
        role='projectile' if a['kind']in ('shot','missile','bomb','ghostshot') else 'npc' if a.get('hp')is not None or a['kind']in ('worm','wormpart','arm','chain','hammer') else 'fx'
        deadlines=[v for v in (a.get('expiry'),self._native_expiry.get(objid)) if type(v)in (int,float) and math.isfinite(v) and v>=0]
        expiry=min(deadlines)if deadlines else None
        return dict(ownerid=self.worldid,objid=objid,role=role,expires_at=expiry,
                    room_time=self.controller.time,lifetime_due=expiry is not None and self.controller.time>=expiry)

    def _feedback(self,e):
        if not isinstance(e,dict) or ('ownerid' in e and int(e['ownerid'] or 0)!=self.worldid): return
        oid=int(e.get('victim') or 0)
        if oid in self.retired: return
        if e.get('kind')=='damage':
            value=int(e.get('value') or 0)
            if value<0: return
            self.native_damage+=1
            player_id=e.get('player_id')
            if player_id is None and self.player is not None:
                player_id=(self.player.worldid,self.player.objid)
            for action in self.controller.add_damage(value,oid,player_id,e.get('event_id')): self._action(action)
        elif e.get('kind')=='destroy':
            self.native_destroy+=1
            for action in self.controller.native_destroy(oid,int(e.get('reason') or 0)): self._action(action)

    def step(self,dt):
        if not math.isfinite(dt) or dt<0: raise ValueError('invalid dt')
        began=time.perf_counter(); self._frame_start=self.controller.time; self._event_time=self._frame_start
        state,_=self.bridge.snapshot(); self.player=state
        if self.players_provider is not None:
            self.players=self.players_provider() if callable(self.players_provider) else self.players_provider
        elif state and state.alive and state.age()<2:
            self.players={(state.worldid,state.objid):dict(center=state.center(),alive=True)}
        else: self.players={}
        self.controller.set_players(self.players)
        if not self.bootstrapped and self.players:
            self.bootstrapped=True; self.controller.start(); self.phase='fighting'
        for e in self.bridge.poll_events(): self._feedback(e)
        for e in self.controller.step(dt): self._action(e)
        self.room_time=self.controller.time
        self._wire_ms+=dt*1000; ms=round(self._wire_ms); self.batch.dt=ms-self._sent_ms; self._sent_ms=ms
        payload=self.batch.encode();self.total_payload_bytes+=len(payload);self.max_payload_bytes=max(self.max_payload_bytes,len(payload))
        self.server._send_raw(self.channel,12,payload,'city-live',record=False)
        self.batch.clear(); self.supplies.flush(); self.frames+=1
        cost=(time.perf_counter()-began)*1000;self.max_tick_ms=max(self.max_tick_ms,cost)
        self.tick_samples.append(cost)
        if len(self.tick_samples)>12000:self.tick_samples=self.tick_samples[-6000:]
        self.ticks_over_budget+=cost>16.7;self.peak_actors=max(self.peak_actors,len(self.controller.actors))

    def status(self):
        return dict(map='city_'+self.KIND,phase=self.phase,seed=self.seed,time=self.room_time,controller=self.controller.status(),
                    frames=self.frames,native_damage=self.native_damage,native_destroy=self.native_destroy,max_tick_ms=self.max_tick_ms,
                    tick_p99_ms=sorted(self.tick_samples)[int(.99*(len(self.tick_samples)-1))] if self.tick_samples else 0,
                    ticks_over_budget=self.ticks_over_budget,peak_actors=self.peak_actors,
                    payload_bytes=self.total_payload_bytes,max_payload_bytes=self.max_payload_bytes)
