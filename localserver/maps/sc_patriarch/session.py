"""Native wire adapter, viewer-specific platform state and bridge lifecycle."""
import copy
import math
import random
import threading
import time
from pathlib import Path
from ... import pb_layout,participants
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from .controller import PatriarchController
from .scene import PatriarchPack

class PatriarchLiveSession:
    HZ=60.

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None,players=None):
        self.server,self.channel,self.options=server,channel,options;self.log=log or (lambda message:None)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        root=Path(getattr(options,'create_world_hex','') or '').parent
        self.pack=pack or PatriarchPack(root if (root/'entities.json').exists() else None)
        self.seed=seed if seed is not None else random.SystemRandom().randrange(1<<30)
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None;self.players_provider=players
        self.batch=DeltaBatch(self.layouts,self.worldid);self._stop=threading.Event();self._thread=None
        self.prepare()

    def prepare(self):
        self.controller=PatriarchController(self.pack,self.seed);self.batch.clear()
        self.phase='waiting';self.room_time=0.;self.bootstrapped=False;self.events=[];self.retired=set();self.created=set()
        self.frames=self.native_damage=0;self.max_tick_ms=0.;self._wire_ms=0.;self._sent_ms=0
        self._frame_start=0.;self._event_time=0.;self.player=None;self.players={}
        self.viewer_platform={};self.tick_costs=[]

    def start(self):
        if self.owns_bridge:self.bridge.start()
        self.prepare();self._stop.clear();self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
        self.log('sc_patriarch: Star Cathedral Patriarch, live per-player duel')

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
                self.log('sc_patriarch tick failed: '+traceback.format_exc());self._stop.set();break
            deadline+=1/self.HZ
            if deadline<time.monotonic()-1/self.HZ:deadline=time.monotonic()+1/self.HZ

    def record(self,kind,**fields):
        self.events.append(dict(kind=kind,at=self._event_time,**fields))
        if len(self.events)>4000:self.events=self.events[-3000:]

    def terrain_for_viewer(self,worldid):
        if self.controller.state in ('dying','done'):
            return 0
        selected=self.controller.selected
        return 0 if isinstance(selected,tuple) and selected[0]==worldid else 1

    def personalize_delta(self,document,worldid):
        """Canonical room keeps terrain=1; only this viewer's delta changes it."""
        w=document.get('worldupdate_',{})
        if w.get('worldid_')!=self.worldid:return document
        terrain=self.terrain_for_viewer(worldid)
        if self.viewer_platform.get(worldid,1)==terrain:return document
        self.viewer_platform[worldid]=terrain
        document=copy.deepcopy(document)
        update=document['worldupdate_'];offset=max([0]+[u.get('offset_',0) for u in update.get('objectupdates_',[])])
        update.setdefault('objectupdates_',[]).append(dict(objid_=18,offset_=offset,modifyprops_=dict(property_=24,intval_=terrain)))
        return document

    def personalize_world(self,document,worldid):
        document=copy.deepcopy(document);terrain=self.terrain_for_viewer(worldid)
        for o in document['worldstate_']['serverworld_'].get('obj_',[]):
            if o['objid_']==18:o['props_']['terraintype_']=terrain

        self.viewer_platform[worldid]=terrain
        return document

    def _action(self,e):
        self._event_time=e['at'];kind=e['kind'];oid=e.get('id');off=max(0,round((e['at']-self._frame_start)*1000))
        if kind=='props':self.batch.props(oid,e['values'],offset=off)
        elif kind=='anim':self.batch.animprops(oid,e['anim'],round(e['duration']*1000),offset=off)
        elif kind=='sound':self.batch.sound(oid,e['name'],offset=off)
        elif kind=='world_sound':
            self.batch.general.append(dict(offset_=off,playsound_=dict(soundname_=e['name'],
                wx_=round(e['pos'][0]),wy_=round(e['pos'][1]))))
        elif kind in ('body_move','actor_lerp'):
            self.batch.modify(4 if kind=='body_move' else oid,'MOVE',message=('Properties_Move',dict(to_=dict(
                xmp_=round(e['pos'][0]*256),ymp_=round(e['pos'][1]*256),dt_=max(1,round(e['duration']*1000))))),offset=off)
        elif kind=='move':self.batch.move_to(oid,*[round(v*256) for v in e['pos']],dt=50,offset=off)
        elif kind=='create':
            a=e['actor'];p=self.pack.props(a['vid']);p.update(a.get('properties',{}))
            p.update(xmp=round(a['pos'][0]*256),ymp=round(a['pos'][1]*256))
            if a['kind'] in ('bomb','snake','spirit'):
                p['move']=dict(vel=dict(xvel=round(a['vel'][0]*65536/1000),yvel=round(a['vel'][1]*65536/1000)))

            p.pop('lifetime',None)
            self.batch.create(a['id'],self.pack.wire_props(p),creatorid=a.get('creator',4),offset=off)
            self.created.add(a['id'])
        elif kind=='rock_launch':
            v=e['velocity'];n=math.hypot(*v) or 1
            accel=[round(x/n*self.pack.entities['rocks']['acceleration_px_s2']*65536/1e6) for x in v]
            cap=[round(abs(x)*65536/1000) for x in v]
            self.batch.modify(oid,'MOVE',message=('Properties_Move',dict(vel_=dict(xvel_=0,yvel_=0,
                xaccel_=accel[0],yaccel_=accel[1],minxvel_=-cap[0],maxxvel_=cap[0],minyvel_=-cap[1],maxyvel_=cap[1]))),offset=off)
        elif kind=='remove':
            if oid in self.retired:return
            self.retired.add(oid);self.batch.destroy(oid,self.worldid,e.get('reason','INTERNAL'),offset=off)
            self.created.discard(oid)
        elif kind=='selected':
            name=e['name']
            text=name+', face me!'
            payload=self.layouts.encode('Chat',dict(username_='Patriarch',message_=text,filteredmessage_=text,chattype_=2))
            self.server._send_raw(self.channel,20,payload,'patriarch-challenge',record=False)
        elif kind=='victory':
            self.phase='done'
            self.batch.props(17,dict(VID='sc.coredoor',INTERACT=255),offset=off)
            self.batch.props(12,dict(VID='sc.door',INTERACT=255),offset=off)
        if kind not in ('move','props','create','remove'):
            self.record(kind,**{k:v for k,v in e.items() if k not in ('kind','at')})

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0 or objid in self.retired:return None
        if objid==4 or objid in self.controller.turrets.rows:
            return dict(ownerid=self.worldid,objid=objid,role='npc',expires_at=None,
                room_time=self.controller.time,lifetime_due=False)
        actor=self.controller.actors.get(objid)
        if actor is None or objid not in self.created:return None
        role='projectile' if actor['kind'] in ('rock','bomb','snake','spirit') else 'fx'
        expiry=actor.get('until')
        if type(expiry)not in (int,float) or not math.isfinite(expiry) or expiry<0:expiry=None
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
        began=time.perf_counter();self._frame_start=self.controller.time;self._event_time=self._frame_start
        state,_=self.bridge.snapshot();self.player=state
        if self.players_provider is not None:self.players=self.players_provider() if callable(self.players_provider) else self.players_provider
        else:
            self.players={participants.identity(p):dict(center=p.center(),alive=p.alive,life_id=p.raw.get('life_id',0),
                name=p.raw.get('name') or p.raw.get('displayname') or 'Player') for p in participants.from_bridge(self.bridge,state)}

        if callable(getattr(self.bridge,'snapshot_players',None)):
            self.players={participants.identity(p):dict(center=p.center(),alive=p.alive,life_id=p.raw.get('life_id',0),
                name=(self.server.room.peers.get(p.raw.get('player_id'),{}).get('name','Player')
                      if hasattr(self.server,'room') else p.raw.get('name') or 'Player'))
                for p in participants.from_bridge(self.bridge)}
        entry = getattr(self, 'duel_entry', None)
        if entry is not None and not getattr(self.bridge, 'shared_room', False):
            boundary, seed = entry
            ready = (state is not None and state.received_since(boundary)
                     and state.raw.get('room_seed') == seed)
            if not ready:
                self.players = {}
            elif not self.duel_entry_seen:
                # A new CreateWorld must receive its own view, even when a
                # persistent encounter selected this player on an earlier visit.
                self.duel_entry_seen = True
                self.viewer_platform.clear()
        self.controller.set_players(self.players)
        if not self.bootstrapped and self.players:
            self.bootstrapped=True;self.controller.start();self.phase='fighting'
        for e in self.bridge.poll_events():self._feedback(e)
        for e in self.controller.step(dt):self._action(e)
        self.room_time=self.controller.time
        self._wire_ms+=dt*1000;ms=round(self._wire_ms);self.batch.dt=ms-self._sent_ms;self._sent_ms=ms
        message=self.batch.message()
        if (entry is not None and self.duel_entry_seen and state is not None
                and self.controller.selected == participants.identity(state)
                and self.terrain_for_viewer(state.worldid) == 0):
            platform = self.pack.templates['18']
            if (state.ymp < platform['ymp'] + platform['hmp']
                    and self.room_time >= getattr(self, '_platform_retry_at', 0.)):
                # The first change may have been consumed during native room
                # construction. Reconcile until telemetry confirms the fall.
                self.viewer_platform.pop(state.worldid, None)
                self._platform_retry_at = self.room_time + .5
        if not hasattr(self.server,'room') and state is not None:message=self.personalize_delta(message,state.worldid)
        self.server._send_raw(self.channel,12,self.layouts.encode('Delta',message),'patriarch-live',record=False)
        self.batch.clear();self.frames+=1
        cost=(time.perf_counter()-began)*1000;self.max_tick_ms=max(self.max_tick_ms,cost)
        self.tick_costs.append(cost)
        if len(self.tick_costs)>12000:self.tick_costs=self.tick_costs[-6000:]

    def status(self):
        costs=sorted(self.tick_costs)
        return dict(map='sc_patriarch',phase=self.phase,seed=self.seed,time=self.room_time,controller=self.controller.status(),
                    frames=self.frames,native_damage=self.native_damage,max_tick_ms=self.max_tick_ms,
                    p99_tick_ms=costs[min(len(costs)-1,int(len(costs)*.99))] if costs else 0,
                    over_budget=sum(c>1000/60 for c in costs))
