'Independent SC Boss encounter: native courses plus live damage/targets.'
import copy
import bisect
import collections
import heapq
import math
import os
import random
import threading
import time

from ... import pb_layout
from ...live.bridge import PlayerBridge, PlayerState
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from .controller import ScBossController
from .scene import ScBossPack, normalize_props
from .motion import NativeMotion, translate, profile_position, MP_PER_WIRE_VELOCITY_SECOND
from .collision import sweep,stop_at_face,overlaps
from .choreography import compile_course,MAX_PLATFORM_SPEED_PX_S
from .difficulty import actor_profile,scale_move,scaled_int
from .plinko import roll_velocity

def nearest_player(players, origin, radius=None):
    from ...participants import nearest
    return nearest(players, origin, radius)

class ScBossLiveSession:
    MAP_ID='sc_boss'
    CONTROLLER_CLASS=ScBossController
    HZ=60.
    POSITION_SYNC_S=.05

    HEAD_HP_ORIGIN=1

    UNLIMITED_SHOTS=frozenset(('scboss.sshot','scboss.dshot','scboss.rshot','scboss.plinkos'))

    BLOCKED_SHOTS=frozenset(('scboss.dshot','scboss.sshot'))

    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None):
        self.server,self.channel,self.options,self.room=server,channel,options,room
        self.log=log or (lambda message:None)
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        root=os.path.dirname(getattr(options,'create_world_hex','') or '')
        self.pack=pack or ScBossPack(root or None)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None
        self.batch=DeltaBatch(self.layouts,self.worldid)
        self.player_type=1
        configured_seed=getattr(options,'live_seed',None)
        self.seed=random.SystemRandom().getrandbits(64) if configured_seed is None else int(configured_seed)
        self._stop=threading.Event()
        self._thread=None
        self.prepare()

    def prepare(self):
        self.supplies=SupplyDrops(self, self.MAP_ID)

        self._rng=random.Random(self.seed)
        self.batch.clear()
        self.phase='waiting'
        self.room_time=0.
        self.controller=None
        self.player=None
        self.players=[]
        self.actors={}
        self.platforms={}
        self.squids={}
        self.events=[]
        self.queue=[]
        self._sequence=0
        self._next_objid=1000
        self._wire_ms=0.
        self._sent_ms=0
        self.head_id=6
        self.head=NativeMotion(self.pack.templates['6']['xmp'],self.pack.templates['6']['ymp'])
        self._next_head=0.
        self.course_index=None
        self.course_age=0.
        self.generation=0
        self.platform_state='initial'
        self._fine_loop_specs=None
        self.flow_role='intro'
        self.current_mode='normal'
        self._second_rise_complete=False
        self._final_unlock_at=math.inf
        self._active_platform_window_end=math.inf
        self._formation_epoch=0
        self.flow_side='D';self.platform_transition_until=0.
        self._course_serial=0
        self._next_bombs=None
        self._final_portal_ids=[]
        self._final_portal_offset=None
        self._surface_cache=None
        self._prepared_course=None
        self._done_at=None
        self._pending_births=0
        self._scream_serial=None
        self._initial_scream=False
        self._formations_fired=set()
        self._second_shatters=[]
        self._second_index=0
        self._course_end=0.

        eye_templates=[
            (4.74,{8:.665,9:.648,10:0.,11:.633}),
            (3.96,{8:.439,9:.942,10:0.,11:.687}),
            (3.91,{8:.049,9:.458,10:0.,11:.762}),
            (7.58,{8:.334,9:.476,10:0.,11:.024}),
            (3.92,{8:.161,9:.439,10:0.,11:.602}),
            (3.87,{8:.749,9:.925,10:0.,11:.814}),
            (3.90,{8:.597,9:.200,10:0.,11:.308}),
        ]
        period,phases=self._rng.choice(eye_templates)
        period/=(self.pack.entities.get('difficulty') or {}).get('eye_rate',1.)
        self._next_eye_shot={eye:1.01+frac*period for eye,frac in phases.items()}
        self._eye_shot_period=period
        self._charge_shots={}
        self.frames=0

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self.preload_course()
        self._thread=threading.Thread(target=self._run,daemon=True)
        self._thread.start()
        self.log('sc_boss live session started (waiting for player)')

    def stop(self,stop_bridge=True):
        self._stop.set()
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        period=1./self.HZ
        previous=time.monotonic()
        while not self._stop.is_set():
            started=time.monotonic()
            elapsed=max(0.,started-previous)
            previous=started
            try:
                self.advance_elapsed(elapsed)
            except Exception:
                import traceback
                self.log('sc_boss tick failed: '+traceback.format_exc())
                self._stop.set()
                break
            wait=period-(time.monotonic()-started)
            if wait>0:
                self._stop.wait(wait)

    def advance_elapsed(self,elapsed):
        """Keep enemy-world time at wall time even when an update overruns.

        Catch up in bounded physics steps, with one wire send. Sending every
        catch-up step would repeat the blocking I/O which caused the debt.
        Each substep keeps its own offset within that Delta.
        """
        if not math.isfinite(elapsed) or elapsed<0:raise ValueError('invalid elapsed time')
        remaining=elapsed
        while True:
            dt=min(1./self.HZ,remaining)
            offset=int(round(self._wire_ms))-self._sent_ms
            ng,no=len(self.batch.general),len(self.batch.objects)
            self.step(dt,flush=False)
            for updates,start in ((self.batch.general,ng),(self.batch.objects,no)):
                for update in updates[start:]:update['offset_']=update.get('offset_',0)+offset
            remaining-=dt
            if remaining<=1e-9:break
        self._flush_delta()

    def _event(self,kind,**values):
        self.events.append(dict(kind=kind,at=round(self.room_time,6),**values))

    def _alloc(self):
        self._next_objid+=1
        return self._next_objid

    def _schedule(self,at,kind,*args,course=None):
        self._sequence+=1
        heapq.heappush(self.queue,(at,self._sequence,kind,args,course))

    def _targets(self):
        from ...participants import from_bridge
        return list(from_bridge(self.bridge, self.player))

    def preload_course(self):
        """Compile the first randomized platform course while loading."""
        if getattr(self.options,'force_course',None) is None and self._prepared_course is None:
            self._prepared_course=self._synthesize_course()

    def step(self,dt,flush=True):
        if not math.isfinite(dt) or dt<0:
            raise ValueError('dt must be finite and non-negative')
        state,_keys=self.bridge.snapshot()
        if state:
            self.player=state
        self.players=self._targets()
        if self.phase=='waiting' and self.player:

            self.preload_course()
            self.controller=self.CONTROLLER_CLASS(self.pack.entities)
            self.phase='intro'
            for i in (13,14):
                p=self.pack.templates[str(i)]

                self.squids[i]=self._squid_row(i,p['xmp'],p['ymp'],born=-.445)
            self._event('fight_start')
        if self.controller:

            for event in self.bridge.poll_events():
                self._feedback(event)
            self.controller.set_squid_count(len(self.squids)+self._pending_births)
            if self.controller.stage==5 and self.platform_state=='final_move':
                self.controller.stage_started+=dt
            events=self.controller.step(dt)
            self.room_time=self.controller.time
            for event in events:
                self._handle(event)
            if self.controller.state!='done':
                self._scene_step()
            self._drain()
            self._actors_step(dt)
            self.phase=self.controller.state
        self._wire_ms+=dt*1000
        self.frames+=1
        if flush:self._flush_delta()

    def _flush_delta(self):
        ms=int(round(self._wire_ms))
        self.batch.dt=ms-self._sent_ms
        self._sent_ms=ms
        self.server._send_raw(self.channel,12,self.batch.encode(),'scboss-live',record=False)
        self.batch.clear()
        self.supplies.flush()

    def native_retirement_capability(self,objid):
        """Classify only this stage's actors; head/squids stay server-owned."""
        if type(objid)is not int or objid<=0:return None
        if objid==self.head_id or objid in self.squids:
            return dict(ownerid=self.worldid,objid=objid,role='npc',
                expires_at=None,room_time=self.room_time,lifetime_due=False)
        row=self.actors.get(objid)
        if row is None or objid in self.platforms:return None
        props=row['props'];hp=row.get('hp')
        def number(value):
            value=value.get('value')if type(value)is dict else value
            return value if type(value)is int else 0
        if number(props.get('terraintype')) or number(props.get('interact')):return None
        if type(hp)is int and hp>=0:role='npc'
        elif number(props.get('ownertype'))==2 and (number(props.get('damage'))>0 or number(props.get('hitboxdamage'))>0):role='projectile'
        else:role='fx'
        expiry=row.get('expiry')
        if type(expiry)not in (int,float) or not math.isfinite(expiry) or expiry<0:expiry=None
        return dict(ownerid=self.worldid,objid=objid,role=role,
            expires_at=expiry,room_time=self.room_time,
            lifetime_due=expiry is not None and self.room_time>=expiry)

    def _feedback(self,event):
        if not isinstance(event,dict):
            return
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.worldid:
            return
        victim=int(event.get('victim') or 0)
        kind=event.get('kind')
        value=int(event.get('value') or 0)
        if kind=='damage' and value>0:
            if victim==self.head_id and self.controller.state!='done':

                if not (self.controller.stage==4 and self.controller.ready and not self._final_transition_ready()):
                    self.controller.add_damage(value,6)

                self.batch.modify(self.head_id,'HITPOINTS',
                                  intval=self.HEAD_HP_ORIGIN-int(self.controller.damage))
                self._event('head_hit',value=value,stage=self.controller.stage,hp=self.controller.hp)
            elif victim in self.squids:
                s=self.squids[victim]
                if not s['active']:
                    return
                if s['hp']==0 and self.room_time>=s['active_at']+.5:
                    self._remove(victim,'HITPOINTS')
                else:
                    s['hp']=max(0,s['hp']-value)
                    if s['hp']==0 and self.room_time>=s['active_at']+.5:
                        self._remove(victim,'HITPOINTS')
                    else:
                        self.batch.modify(victim,'HITPOINTS',intval=max(1,s['hp']))
            elif victim in self.actors and self.actors[victim].get('hp') is not None:
                a=self.actors[victim]
                a['hp']-=value
                if a['hp']<=0:
                    self._remove(victim,'HITPOINTS')
                else:
                    a['props']['hitpoints']=a['hp']
                    self.batch.modify(victim,'HITPOINTS',intval=a['hp'])
        elif kind=='destroy':
            reason=int(event.get('reason') or 1)
            if victim==self.head_id and self.controller.state!='done':

                p=self.pack.wire_props(6)
                x,y=self.head.position(self.room_time)
                p.update(xmp_=int(x),ymp_=int(y))
                self.head_id=self._alloc()
                self.batch.create(self.head_id,p)
                self._event('head_repair',previous=victim,objid=self.head_id,reason=reason)
            elif victim in self.squids:

                s=self.squids[victim]
                if s['active'] and self.room_time<s['active_at']+.5:
                    self._repair_squid(victim)
                else:
                    self._remove(victim,reason)
            elif victim in self.actors:
                self._remove(victim,reason)
            elif victim in self.platforms or victim in (1,2,3,4,5,7,8,9,10,11,12):
                self._event('unexpected_scenery_destroy',objid=victim,reason=reason)

    def _final_transition_ready(self):
        if not self._second_rise_complete or self.room_time<self._final_unlock_at:return False
        if self.flow_side=='U' and self.room_time<self._active_platform_window_end:return False
        return True

    def _handle(self,event):
        kind=event['kind']
        if kind=='summon_pair':
            self.batch.animprops(7,4,2904)
            self._pending_births+=2
            self._schedule(self.room_time+.924,'summon_pair')
        elif kind=='stage_start':
            self._event('stage_start',stage=event['stage'],hp=event['hp'],duration_s=event['duration_s'])
            if event['stage']==5:
                self._lock_final()
        elif kind=='boss_destroy':
            self._course_serial+=1
            for portal in self._final_portal_ids:self._remove(portal,'INTERNAL')
            self._final_portal_ids=[]
            for shot in list(self._charge_shots.values()):
                if shot in self.actors:
                    self._remove(shot,'INTERNAL')
            self._charge_shots.clear()
            for i in [self.head_id,7,8,9,10,11]:
                self.batch.destroy(i,ownerid=self.worldid,reason='INTERNAL')
            self._done_at=self.room_time
            self._next_bombs=None
            self._event('boss_destroy')

    def _drain(self):
        while self.queue and self.queue[0][0]<=self.room_time+1e-9:
            at,_seq,kind,args,course=heapq.heappop(self.queue)
            if course is not None and course!=self._course_serial:
                continue
            if kind=='command':
                objid,command,dx,dy=args
                if objid in self.actors or objid in self.platforms:
                    self._command(objid,command,dx,dy,at)
            elif kind=='create':
                if self.controller.state!='done':
                    self._profile_actor(*args,at=at)
            elif kind=='create_platform':

                if self.controller.state!='done':
                    profile=args[0]
                    row=self._profile_actor(profile,at=at,platform=True,course=course)
                    if profile.get('end_t'):
                        self._schedule(at+max(.05,profile['end_t']-profile['t']),
                                       'remove',row['objid'],'INTERNAL')
            elif kind=='head_command':
                if self.controller.state!='done':
                    self._command(self.head_id,args[0],0,0,at)
            elif kind=='summon_pair':
                self._pending_births=max(0,self._pending_births-2)
                if (self.controller.state!='done' and len(self.squids)+2<=self.controller.rule['squid_cap']
                        and not (self.controller.stage==5 and len(self.squids)>=8)):
                    self._spawn_pair(at)
            elif kind=='squid_shots':
                self._squid_shots(args[0],at)
            elif kind=='remove':
                self._remove(args[0],args[1])
            elif kind=='hopper_birth':
                if self.controller.state!='done' and self.controller.stage!=5:
                    self._formation_hopper(*args)
            elif kind=='platform_stable':
                if self.controller.state!='done' and self.controller.stage!=5 and self.current_mode!='nova':
                    self._platform_stable(*args)
            elif kind=='vortex_birth':
                if self.controller.state!='done' and self.controller.stage!=5:
                    self._vortex_birth(*args)
            elif kind=='lane_begin':
                row=self.actors.get(args[0])
                if row is not None:
                    row['lane_start']=row['motion'].position(at)
            elif kind=='lane_exit':

                row=self.actors.get(args[0])
                if row is not None:
                    target,span=args[1],args[2]
                    start=row.pop('lane_start',None)
                    x,y=row['motion'].position(at)
                    if start is None:
                        start=(x,y)
                    dx,dy=target[0]-start[0],target[1]-start[1]
                    if span>0. and (dx or dy):
                        vel=dict(xvel=int(round(dx/span/1000.*256.)),
                                 yvel=int(round(dy/span/1000.*256.)))
                        self._command(args[0],
                            {'t':0.,'modifyprops':{'property':41,'decoded':{'vel':vel}}},
                            0,0,at)
            elif kind=='eye_shot':
                if self.controller.state!='done':
                    self._eye_shot(args[0],at)
            elif kind=='rshot_pad':

                row=self.actors.get(args[0])
                if row is not None and not row.get('recalled'):
                    target,span=args[1],args[2]
                    move={'to':dict(xmp=int(target[0]),ymp=int(target[1]),
                                    dt=int(span*1000))}
                    row['motion'].set(move,at)
                    self.batch.modify(args[0],'MOVE',
                        message=('Properties_Move',normalize_props('Properties_Move',move)))
            elif kind=='rshot_anim':

                row=self.actors.get(args[0])
                if row is not None:
                    self.batch.modify(args[0],13,intval=184)
                    self.batch.animprops(args[0],1,264)
            elif kind=='rshot_sound':
                if args[0] in self.actors:
                    self.batch.sound(args[0],'scboss.rshot')
            elif kind=='rshot_recalled':
                row=self.actors.get(args[0])
                if row is not None:
                    row['recalled']=True
            elif kind=='rshot_recall':
                if self.controller.state!='done':
                    row=self.actors.get(args[0])
                    if row is not None:
                        target,span=args[1],args[2]
                        move={'to':dict(xmp=int(target[0]),ymp=int(target[1]),
                                        dt=int(span*1000))}
                        row['motion'].set(move,at)
                        self.batch.modify(args[0],'MOVE',
                            message=('Properties_Move',normalize_props('Properties_Move',move)))
            elif kind=='formation':
                if self.controller.state!='done' and self.controller.stage!=5:
                    self._platform_formed(args[0])
            elif kind=='flow':
                entry=args[0];self.flow_role=entry['role'];self.current_mode=entry.get('mode','normal')
                self.flow_side=entry['side']
                self.platform_transition_until=entry.get('settle_at',self.course_age+entry.get('settle_t',entry['t']))
                self._active_platform_window_end=self.course_age+entry.get('end_t',entry['t'])
                self._formation_epoch+=1
                self._event('platform_flow',role=self.flow_role,side=entry['side'],mode=self.current_mode,
                            generation=self.generation)
                if self.generation==2:
                    self._schedule(entry.get('settle_at',at),'fine_settled',entry,course=course)
            elif kind=='fine_settled':
                entry=args[0]
                self._second_rise_complete=True
                self._platform_formed(entry['side'],entry.get('mode','normal'))
            elif kind=='final_locked':
                self.platform_state='final';self.flow_role='final';self._next_bombs=at+6.2
                self._event('platform_lock',pieces=len(self.platforms))
            elif kind=='platform_orbit':
                objid,epoch,move=args;row=self.platforms.get(objid)
                if row and row.get('motion_epoch')==epoch:
                    row['motion'].set(move,at)
                    self.batch.modify(objid,'MOVE',message=('Properties_Move',normalize_props('Properties_Move',move)))

    def _command(self,objid,c,dx,dy,at):
        row=self.actors.get(objid) or self.platforms.get(objid)
        if c.get('playsound'):
            self.batch.sound(objid,c['playsound']['soundname'])
        if 'movetoxmp' in c or 'movetoymp' in c:
            x=c.get('movetoxmp');y=c.get('movetoymp')
            x=None if x is None else int(round(x+dx))
            y=None if y is None else int(round(y+dy))
            duration=c.get('movetodt')
            if objid in self.platforms:
                sx,sy=row['motion'].position(at)
                distance=math.hypot((x if x is not None else sx)-sx,(y if y is not None else sy)-sy)
                interval=max(17,int((at-row.get('last_position_at',at-.05))*1000.))
                duration=max(int(duration or interval),int(math.ceil(distance/(MAX_PLATFORM_SPEED_PX_S*256.)*1000.)))
                row['last_position_at']=at;row['motion_epoch']=row.get('motion_epoch',0)+1
            self.batch.move_to(objid,x,y,dt=duration)
            if row:

                sx,sy=row['motion'].position(at)
                row['motion'].set({'to': {'xmp': x if x is not None else sx,
                                           'ymp': y if y is not None else sy,
                                           'dt': max(1,int(duration or 1)),
                                           'start': {'xmp': sx,'ymp': sy}}},at)
        m=c.get('modifyprops')
        if not m:
            return
        prop=m['property']
        if row and row['vid'] in self.UNLIMITED_SHOTS and prop==14:
            return
        if prop in (41,49):
            v=m.get('decoded') or {}
            if prop==41:
                v=translate(v,dx,dy)
                if row:
                    if objid in self.platforms:
                        v=self._platform_move(row,v,at)

                    wp=v.get('waypoint')
                    if wp and wp.get('points') and not wp.get('loop'):
                        x,y=row['motion'].position(at)
                        p0=wp['points'][0]
                        tx,ty=p0.get('xmp'),p0.get('ymp')
                        if tx is not None and ty is not None:
                            dist=math.hypot(tx-x,ty-y)/256.
                            if dist>100.:
                                dt=max(int(p0.get('dt',1) or 1),
                                       min(6000,int(dist/450.*1000.)))
                                if dt!=int(p0.get('dt',1) or 1):
                                    v=dict(v,waypoint=dict(wp,points=[
                                        dict(p0,dt=dt)]+[dict(q) for q in wp['points'][1:]]))
                    row['motion'].set(v,at)
            self.batch.modify(objid,prop,message=('Properties_Move' if prop==41 else 'AnimationProperties',
                normalize_props('Properties_Move' if prop==41 else 'AnimationProperties',v)))
        else:
            self.batch.modify(objid,prop,**{k:v for k,v in m.items() if k in ('intval','boolval','stringval')})
            if row and prop==14:

                row['native_lifetime']=True
                row['expiry']=at+max(0,m.get('intval',0))/1000.

    def _platform_move(self,row,move,at):
        """Validate every native lane from the actual live origin."""
        row['motion_epoch']=row.get('motion_epoch',0)+1
        x,y=row['motion'].position(at);move=copy.deepcopy(move)
        if move.get('elliptical'):
            tx,ty=self._ellipse_entry_point(move['elliptical']);distance=math.hypot(tx-x,ty-y)
            if distance>256.:
                dt=max(1500,int(math.ceil(distance/(MAX_PLATFORM_SPEED_PX_S*256.)*1000.)))
                self._schedule(at+dt/1000.,'platform_orbit',row['objid'],row['motion_epoch'],move,
                               course=self._course_serial)
                return dict(to=dict(xmp=int(tx),ymp=int(ty),dt=dt,start=dict(xmp=int(x),ymp=int(y))))
            return move
        if move.get('to'):
            p=move['to'];distance=math.hypot(p.get('xmp',x)-x,p.get('ymp',y)-y)
            p['dt']=max(int(p.get('dt',1)),int(math.ceil(distance/(MAX_PLATFORM_SPEED_PX_S*256.)*1000.)))
            p['start']=dict(xmp=int(x),ymp=int(y));p['pos']=0
        wp=move.get('waypoint')
        if wp:
            for p in wp.get('points',[]):
                tx,ty=p.get('xmp',x),p.get('ymp',y)
                p['dt']=max(int(p.get('dt',1)),int(math.ceil(math.hypot(tx-x,ty-y)/(MAX_PLATFORM_SPEED_PX_S*256.)*1000.)))
                x,y=tx,ty
        return move

    @staticmethod
    def _continuous_lane(move):
        if not move:
            return False
        if move.get('vel') or move.get('elliptical'):
            return True
        return bool((move.get('waypoint') or {}).get('loop'))

    @staticmethod
    def _last_finite_lane(commands):
        """(start_s, target, span_s) of the last finite MOVE to lane."""
        found=None
        for c in commands:
            m=c.get('modifyprops') or {}
            if m.get('property')!=41:
                continue
            dec=m.get('decoded') or {}
            to=dec.get('to')
            if not to or not to.get('dt'):
                continue
            span=float(to['dt'])/1000.
            found=(float(c.get('t',0.)),(int(to.get('xmp',0)),int(to.get('ymp',0))),span)
        return found

    @staticmethod
    def _lane_end_seconds(commands):
        """When the last finite MOVE lane stops, in profile-relative seconds."""
        end=None
        for c in commands:
            m=c.get('modifyprops') or {}
            if m.get('property')!=41:
                continue
            dec=m.get('decoded') or {}
            if dec.get('to'):
                span=max(0.,float(dec['to'].get('dt',0) or 0))/1000.
            elif (dec.get('waypoint') or {}).get('points'):
                wp=dec['waypoint']
                if wp.get('loop'):
                    return None
                span=sum(max(1.,float(p.get('dt',1) or 1)) for p in wp['points'])/1000.
            else:
                continue
            candidate=float(c.get('t',0.))+span
            end=candidate if end is None else max(end,candidate)
        return end

    def _carry_last_velocity(self,profile,row,at):
        """Keep an unsampled-tail projectile flying at its last measured speed.

        Native MOVE velocities are raw*256; one raw unit is 1/256 px/ms, so a
        displacement of ``v*ms/256`` mp is exactly ``v/256`` px per ms.  The
        field value that reproduces the sampled speed is therefore
        ``dmp/dt_ms*256``.
        """
        commands=profile.get('commands') or []
        moves=[(c.get('modifyprops') or {}).get('decoded') or {} for c in commands
               if (c.get('modifyprops') or {}).get('property')==41]
        samples=[(c.get('t',0.),c.get('movetoxmp'),c.get('movetoymp'))
                 for c in commands
                 if c.get('movetoxmp') is not None and c.get('movetoymp') is not None]
        if len(samples)<2:
            return
        last_motion=next((c for c in reversed(commands) if c.get('modifyprops',{}).get('property')==41),None)
        if (last_motion and last_motion['t']>=samples[-1][0]
                and self._continuous_lane(last_motion['modifyprops'].get('decoded') or {})):
            return
        (t0,x0,y0),(t1,x1,y1)=samples[-2],samples[-1]
        span=(t1-t0)*1000.
        if span<=0.:
            return
        vx=(x1-x0)*256./span
        vy=(y1-y0)*256./span
        if not (vx or vy):
            return
        row['bounds']=True
        row['carry_t']=t1
        self._schedule(at+t1,'command',row['objid'],
            {'t':t1,'modifyprops':{'property':41,'decoded':{'vel':dict(
                xvel=int(round(vx)),yvel=int(round(vy)))}}},0,0)

    def _profile_actor(self,profile,dx=0,dy=0,at=None,platform=False,course=None):
        profile=actor_profile(profile,self.pack.entities,platform=platform)
        at=self.room_time if at is None else at
        p=translate(profile['props'],dx,dy)
        if not platform and profile['vid'] in self.UNLIMITED_SHOTS:
            p['lifetime']=-1
        objid=self._alloc()
        row=dict(objid=objid,vid=profile['vid'],born=at,props=p,
                 motion=NativeMotion(p['xmp'],p['ymp']),hp=p.get('hitpoints'))
        if not platform and row['vid'] in ('scboss.dshot','scboss.rshot','scboss.gshot','scboss.plinkos'):
            target=nearest_player(self.players,(p['xmp']+p['wmp']/2,p['ymp']+p['hmp']/2))
            self._event('boss_target',vid=row['vid'],target=target.objid if target else None)

        if p.get('move'):
            row['motion'].set(p['move'],at)
        end=profile.get('observed_end') or {}
        end_age=None
        if end.get('t') and profile.get('source_born') is not None:
            end_age=end['t']-profile['source_born']
        if p.get('lifetime',-1)>=0:

            row['expiry']=at+p['lifetime']/1000.
        elif row['vid']=='scboss.gshot' and end_age and end.get('reason')==1:

            row['expiry']=at+end_age
        if not platform and row['vid'] in self.UNLIMITED_SHOTS:

            row['bounds']=True
            row['_carry_pending']=(profile,at)
        if row['vid']=='scboss.hopper':
            direction=1 if p.get('facing',0)==0 else -1

            row.update(hp=p.get('hitpoints',500),vx=0.,vy=0.,next_jump=at+self._hopper_first_delay(),
                       grounded=False,direction=direction,turns=0)

            row['expiry']=at+max(10.,min(100.,end.get('t',profile['source_born']+30.)-profile['source_born']))

            doors=[a for a in self.actors.values() if a['vid']=='scboss.vortex'
                   and a['props'].get('lifetime')==5000
                   and abs(a['props']['xmp']+8192-p['xmp'])<1024
                   and abs(a['props']['ymp']+4096-p['ymp'])<1024]
            if doors:
                door=min(doors,key=lambda a:abs(at-a['born']-1.32))
                row['portal_id']=door['objid'];self._remove(door['objid'],'INTERNAL')

        if row['vid']=='scboss.bomb':
            row['fall_at']=at+next(c['t'] for c in profile['commands']
                if c.get('modifyprops',{}).get('property')==41 and
                not c['modifyprops'].get('decoded'))
            row['falling']=False
        if row['vid']=='scboss.plinkos':
            samples=[c for c in profile['commands'] if c.get('movetoxmp') is not None
                     and c.get('movetoymp') is not None and 0<=c['t']<=.25/self._projectile_speed()]
            vx,vy=23000.,16750.
            if len(samples)>=2:
                first,last=samples[0],samples[-1];span=last['t']-first['t']
                if span>0:vx=(last['movetoxmp']-first['movetoxmp'])/span
            speed=self._projectile_speed()
            row.update(vx=max(-76000.*speed,min(76000.*speed,vx)),vy=vy*speed,x=float(p['xmp']),y=float(p['ymp']))
            row.pop('expiry',None);row.pop('_carry_pending',None)
        (self.platforms if platform else self.actors)[objid]=row
        self.batch.create(objid,normalize_props('Properties',p))
        if p.get('animprops'):
            ap=p['animprops']
            self.batch.animprops(objid,ap.get('animid',0),ap.get('time',0),ap.get('loop',False))
        self._event('actor_create',objid=objid,vid=row['vid'],platform=platform)
        if row['vid']!='scboss.hopper':
            for c in profile['commands']:
                if platform and c['t']>self._course_duration+1e-9:
                    continue
                if row['vid']=='scboss.bomb' and at+c['t']>=row['fall_at']:
                    continue
                if row['vid']=='scboss.plinkos' and ('movetoxmp' in c or 'movetoymp' in c
                        or c.get('modifyprops',{}).get('property') in (14,41)):
                    continue

                self._schedule(at+c['t'],'command',objid,c,dx,dy,
                               course=course if platform else None)
        pending=row.pop('_carry_pending',None)
        if pending is not None:
            profile,when=pending
            lane=self._last_finite_lane(profile.get('commands') or ())
            if row['vid']=='scboss.rshot':

                self._plan_rshot_recall(profile,row,at,dx,dy,lane)
            else:

                self._carry_last_velocity(profile,row,when)
                if lane is not None:
                    lane_t,target,span=lane
                    target=(int(target[0]+dx),int(target[1]+dy))
                    self._schedule(when+lane_t,'lane_begin',objid)
                    self._schedule(when+lane_t+span,'lane_exit',objid,target,span)
        return row

    def _formation_end_after(self,when):
        """Absolute time the current direction window ends (platform moves on)."""
        course=getattr(self,'_course_plan',None)
        if not course:
            return None
        forms=course.get('formations') or []
        base=self.course_age
        for i,f in enumerate(forms):
            lo=base+f['t']
            if i+1>=len(forms):
                break
            hi=base+forms[i+1]['t']
            if lo<=when<hi:
                return hi
        return None

    def _rshot_recall_lane(self,profile):
        "The runtime recall: a property-41 MOVE 'to' back to the muzzle."
        for c in reversed(profile.get('commands') or ()):
            m=c.get('modifyprops') or {}
            to=(m.get('decoded') or {}).get('to')
            if m.get('property')==41 and to:
                return c.get('t',0.),to
        return None

    def _plan_rshot_recall(self,profile,row,at,dx,dy,lane):
        muzzle=(int(row['props']['xmp']),int(row['props']['ymp']))
        spec=(self.pack.entities or {}).get('rshot') or {}
        recall=self._rshot_recall_lane(profile)
        cut_off=recall is None
        stall_t=None
        if recall is not None:
            t,to=recall
            start=at+t
            span=max(.05,to.get('dt',4200)/1000.)
        else:

            samples=[(c.get('t',0.),c['movetoxmp'],c.get('movetoymp'))
                     for c in profile.get('commands') or ()
                     if c.get('movetoxmp') is not None]
            if not samples:
                return
            x0,y0=float(muzzle[0]),float(muzzle[1])
            ft,fx,fy=max(samples,key=lambda q:math.hypot(q[1]-x0,q[2]-y0))
            far=math.hypot(fx-x0,fy-y0)/256.
            standard=float(spec.get('outbound_median_px',1118.))
            if far<standard*.92 and far>1.:
                ux,uy=(fx-x0)/(far*256.),(fy-y0)/(far*256.)
                tx,ty=int(x0+ux*standard*256.),int(y0+uy*standard*256.)
                pad_s=1.2/self._projectile_speed()
                self._schedule(at+ft,'rshot_pad',row['objid'],(tx,ty),pad_s)
                stall_t=ft+pad_s
            else:
                stall_t=ft
            dwell=self._weighted_seconds(spec.get('dwell_s_weights'),6.34)
            span=self._rng.uniform(*spec.get('return_s',(4.09,6.38)))/self._projectile_speed()
            start=at+stall_t+dwell

        slot_end=self._formation_end_after(at)
        if slot_end is not None:
            start=min(start,slot_end-span-0.55)
        if cut_off:

            start=max(start,at+stall_t+1.79)

            self._schedule(max(at,start-0.41),'rshot_anim',row['objid'])
            self._schedule(start,'rshot_recall',row['objid'],muzzle,span)
            self._schedule(start,'rshot_sound',row['objid'])

        self._schedule(start+span,'rshot_recalled',row['objid'])
        self._schedule(start+span+.06,'remove',row['objid'],'LIFETIME')

    def _weighted_seconds(self,table,fallback):
        if not table:
            return float(fallback)
        keys=[float(k) for k in table]
        return self._rng.choices(keys,weights=[table[k] for k in table],k=1)[0]

    def _scene_step(self):
        t=self.room_time

        while True:
            due=[i for i,v in self._next_eye_shot.items() if t>=v]
            if not due:
                break
            for i in due:
                fire=self._next_eye_shot[i]
                self.batch.animprops(i,1,2112)

                self._charge_shots[i]=self._charge_eye_shot(i,self.room_time)
                self._event('eye_shoot_anim',objid=i,shot=self._charge_shots[i])

                self._schedule(self.room_time+1.056,'eye_shot',i)
                self._next_eye_shot[i]=fire+self._eye_shot_period

        if t>=self._next_head:
            idle=self.pack.programs['head_idle']
            tick=t%30.
            command=min(idle,key=lambda c:abs(c['t']-tick))
            move=command['move']
            self.head.set(move,t)
            self.batch.modify(self.head_id,'MOVE',message=('Properties_Move',normalize_props('Properties_Move',move)))
            hx,hy=self.head.position(t)
            for i in (8,9,10,11):
                p=self.pack.templates[str(i)]
                base=self.pack.templates['6']
                self.batch.move_to(i,int(hx+p['xmp']-base['xmp']),int(hy+p['ymp']-base['ymp']),dt=50)
            self._next_head=t+.05
        for eye,shot in list(self._charge_shots.items()):
            row=self.actors.get(shot)
            if row and row.get('charging'):
                x,y=self._eye_origin(eye,t)
                row['motion'].correct(x-2560,y-2560,t)
                self.batch.move_to(shot,int(x-2560),int(y-2560),dt=50)
        if self.controller.stage==5:
            if self._next_bombs is not None and t>=self._next_bombs:
                self._bomb_wave()
                self._next_bombs+=8.7
            return
        if not self._initial_scream and t>=self.pack.scene['shatter_s']-1.065:
            self._initial_scream=True
            self._shatter_scream()
        if self.platform_state=='initial' and t>=self.pack.scene['split_s']:

            self.batch.sound(12,'scboss.fracture')
            self.batch.destroy(12,ownerid=self.worldid,reason='INTERNAL')
            self.platform_state='split'
            self._course_duration=20.
            for p in self.pack.programs['prefix']:
                row=self._profile_actor(p,at=t,platform=True)
                self.batch.sound(row['objid'],'scboss.fracture')
            self._event('platform_split')
        if self.platform_state=='split' and t>=self.pack.scene['shatter_s']:

            self._begin_course(self._choose_course(),shatter=True)
            self._plan_second_shatter()
        elif self.platform_state=='orbit':
            if self.generation==1:
                self._step_second_shatter()
            elif self.generation==2 and t>=self._course_end:
                if self.controller.stage>=5 or self.controller.state=='done':

                    self.platform_state='final'
                else:

                    self._loop_fine()

    SLOT_TRANSITION_S=1.5

    @staticmethod
    def _evaluate_profile_position(profile,at):
        "Where a runtime profile's own MOVE stream sits at time ``at``."
        return profile_position(profile,at)

    def _slot_plan(self):
        'Roll every direction slot independently from the 29-run mixes.'
        payloads=self.pack.programs.get('slot_payloads') or {}
        mixes=(self.pack.entities or {}).get('slot_mix') or {}
        cycle=(self.pack.entities or {}).get('slot_side_cycle') or ['U','R','D','L','U','R']
        plan=[]
        for slot,side in enumerate(cycle):
            modes={m:w for m,w in (mixes.get(str(slot)) or {}).items()
                   if payloads.get(str(slot),{}).get(m)}
            if not modes:
                continue
            keys=list(modes)
            mode=self._rng.choices(keys,weights=[modes[k] for k in keys],k=1)[0]
            ci,start,end=self._rng.choice(payloads[str(slot)][mode])
            entry=dict(slot=slot,side=side,mode=mode,course=int(ci),
                       start=float(start),end=float(end))
            if mode=='nova':

                weights=(self.pack.entities.get('nova') or {}).get('ring_weights') or {'1':1.}
                keys2=list(weights)
                entry['rings']=int(self._rng.choices(keys2,weights=[weights[k] for k in keys2],k=1)[0])
            plan.append(entry)
        return plan

    @staticmethod
    def _group_by_size(rows):
        out={}
        for row in rows:
            out.setdefault(row['vid'],[]).append(row)
        return out

    @staticmethod
    def _angle_sorted(rows):
        xs=[r['props']['xmp'] for r in rows]
        ys=[r['props']['ymp'] for r in rows]
        cx,cy=sum(xs)/len(xs),sum(ys)/len(ys)
        return sorted(rows,key=lambda r:math.atan2(r['props']['ymp']-cy,
                                                   r['props']['xmp']-cx))

    @staticmethod
    def _angle_of(row,cx,cy):
        p=row['props']
        return math.atan2(p['ymp']+p.get('hmp',0)/2-cy,
                          p['xmp']+p.get('wmp',0)/2-cx)

    def _match_slot_pieces(self,ours,theirs,repeat=False):
        '1:1 angle-ordered mapping over the whole fragment set.'
        if not ours or not theirs:
            return []
        a=self._angle_sorted(ours); b=self._angle_sorted(theirs)
        axs=[r['props']['xmp'] for r in a]; ays=[r['props']['ymp'] for r in a]
        acx,acy=sum(axs)/len(axs),sum(ays)/len(ays)
        bxs=[r['props']['xmp'] for r in b]; bys=[r['props']['ymp'] for r in b]
        bcx,bcy=sum(bxs)/len(bxs),sum(bys)/len(bys)
        bangs=[self._angle_of(r,bcx,bcy) for r in b]
        pairs=[]
        for i,row in enumerate(a):
            if i<len(b):
                pairs.append((row,b[i]))
                continue
            if not repeat:
                break
            ai=self._angle_of(row,acx,acy)
            j=min(range(len(b)),
                  key=lambda k:abs((ai-bangs[k]+math.pi)%(2*math.pi)-math.pi))
            pairs.append((row,b[j]))
        return pairs

    def _slot_piece_commands(self,ours,theirs,slot,lane=None):
        'The runtime window commands for one piece, with a lead-in hop.'
        start,end=slot['start'],slot['end']
        first=next((c for c in theirs.get('commands') or ()
                    if start<=c.get('t',0.)<end),None)
        lead=None
        if first is None or first.get('t',0.)>start+0.05:

            x,y=self._evaluate_profile_position(theirs,start)

            dt=self.SLOT_TRANSITION_S
            lead=dict(t=start,modifyprops=dict(property=41,decoded=dict(
                waypoint=dict(points=[dict(xmp=int(x),ymp=int(y),dt=int(dt*1000))]))))
        out=[] if lead is None else [lead]

        target=lane if lane is not None else\
            (slot.get('orbit_map') or {}).get(theirs.get('source_id'))
        lo=start-6.0
        for c in theirs.get('commands') or ():
            t=c.get('t',0.)
            if t<lo or t>=end:
                continue
            if t<start:
                mp=c.get('modifyprops') or {}
                dec=mp.get('decoded') or {}
                if mp.get('property')!=41:
                    continue
                if dec.get('waypoint'):
                    tail=self._lane_tail(dec,start-t)
                    if tail is None:
                        continue
                    dec=tail
                elif dec.get('elliptical') and target:
                    dec=self._retarget_orbit(dec,target)
                c=dict(t=start,modifyprops=dict(property=41,decoded=dec))
            elif target:
                mp=c.get('modifyprops') or {}
                dec=mp.get('decoded') or {}
                if mp.get('property')==41 and dec.get('elliptical'):
                    c=dict(c,modifyprops=dict(property=41,
                            decoded=self._retarget_orbit(dec,target)))
            mp=c.get('modifyprops') or {}
            dec=mp.get('decoded') or {}
            if mp.get('property')==41 and dec.get('elliptical'):

                e=dec['elliptical']
                t0=max(float(c.get('t',start)),start+self.SLOT_TRANSITION_S)
                px,py=self._ellipse_entry_point(e)
                out.append(dict(t=round(t0-self.SLOT_TRANSITION_S,6),
                                movetoxmp=int(round(px)),movetoymp=int(round(py)),
                                movetodt=int(self.SLOT_TRANSITION_S*1000)))
                c=dict(c,t=round(t0,6))
            out.append(copy.deepcopy(c))
        return out

    @staticmethod
    def _ellipse_entry_point(e):
        """Absolute position of an ellipse lane at the instant it starts."""
        period=max(1,int(e.get('period',10000) or 10000))
        rad=2*math.pi*float(e.get('pos',0) or 0)/period
        a=float(e.get('radiusamp',0) or 0)*math.cos(rad)
        b=float(e.get('radiusbmp',0) or 0)*math.sin(rad)
        ang=math.radians(float(e.get('angle',0) or 0))
        return (float(e.get('xmp',0) or 0)+a*math.cos(ang)-b*math.sin(ang),
                float(e.get('ymp',0) or 0)+a*math.sin(ang)+b*math.cos(ang))

    @staticmethod
    def _retarget_orbit(decoded,target):
        e=dict(decoded['elliptical'])
        e['xmp']=int(target['xmp'])
        e['ymp']=int(target['ymp'])
        e['radiusamp']=e['radiusbmp']=int(target['radius'])
        e['period']=max(1,int(target['period']))
        if target.get('pos') is not None:
            e['pos']=int(target['pos'])%e['period']
        return {'elliptical':e}

    @staticmethod
    def _lane_tail(decoded,elapsed_s):
        """Remaining waypoints of a lane that already ran ``elapsed_s``."""
        waypoint=decoded.get('waypoint')
        if not waypoint:
            return decoded
        spent=0.
        tail=[]
        for pt in waypoint.get('points') or []:
            dt=max(0.,pt.get('dt',0)/1000.)
            if spent+dt<=elapsed_s:
                spent+=dt
                continue
            tail.append(pt)
            spent+=dt
        if not tail:
            return None
        return dict(waypoint=dict({k:v for k,v in waypoint.items() if k!='points'},
                                  points=tail))

    def _payload_rings(self,course_index,start,end):
        """Orbit (radius, period, centre) of every piece in a Nova window."""
        found={}
        for q in self.pack.programs['courses'][course_index]['platforms']:
            for c in q.get('commands') or ():
                t=c.get('t',0.)
                if not (start<=t<end):
                    continue
                mp=c.get('modifyprops') or {}
                if mp.get('property')!=41:
                    continue
                e=(mp.get('decoded') or {}).get('elliptical')
                if e:
                    found[q.get('source_id')]=(int(e.get('radiusamp',0)),
                                               int(e.get('period',1)),
                                               int(e.get('xmp',0)),int(e.get('ymp',0)))
                    break
        return found

    def _nova_ring_center(self,radius,base,side):
        """Sample the current side's usable band around the Boss."""
        head=self.pack.templates['6'];bounds=self.pack.scene['bounds']
        hx,hy=head['xmp'],head['ymp'];hr=hx+head['wmp'];hb=hy+head['hmp']
        reach=max(max(r for r,p,cx,cy in base),
                  int((self.pack.entities.get('nova') or {}).get('single_min_radius_px',256)*256))
        gap=int((self.pack.entities.get('nova') or {}).get('gap_px',24)*256)
        left=min(cx-r for r,p,cx,cy in base);right=max(cx+r for r,p,cx,cy in base)
        top=min(cy-r for r,p,cx,cy in base);bottom=max(cy+r for r,p,cx,cy in base)
        if side not in ('U','R','D','L'):
            dx=(sum(q[2] for q in base)/len(base)-(hx+hr)/2)/max(1,head['wmp'])
            dy=(sum(q[3] for q in base)/len(base)-(hy+hb)/2)/max(1,head['hmp'])
            side=('R' if dx>=0 else 'L') if abs(dx)>abs(dy) else ('D' if dy>=0 else 'U')
        fragments=[p['props'] for c in self.pack.programs['courses'] for p in c['platforms']]
        width=max(p['wmp'] for p in fragments);height=max(p['hmp'] for p in fragments)
        if side in ('U','D'):
            left=min(left,hx-reach);right=max(right,hr+reach)
            if side=='U':top=min(top,hy-2*reach-gap);bottom=hy-gap-height
            else:top=hb+gap;bottom=max(bottom,hb+2*reach+gap)
        else:
            top=min(top,hy-reach);bottom=max(bottom,hb+reach)
            if side=='L':left=min(left,hx-2*reach-gap);right=hx-gap-width
            else:left=hr+gap;right=max(right,hr+2*reach+gap)
        left=max(left,bounds['x']*256)+radius
        right=min(right,(bounds['x']+bounds['w'])*256-width)-radius
        top=max(top,bounds['y']*256)+radius
        bottom=min(bottom,(bounds['y']+bounds['h'])*256-height)-radius
        if left>right or top>bottom:
            return base[-1][2:]
        return (self._rng.randint(int(math.ceil(left)),int(math.floor(right))),
                self._rng.randint(int(math.ceil(top)),int(math.floor(bottom))))

    def _orbit_targets(self,rings,count,side=None):
        'Target (centre,radius,period) per ring, anchored on the capture.'
        base=sorted({(r,p,cx,cy) for r,p,cx,cy in rings.values()},key=lambda q:q[0])
        if not base:
            return None
        if count==1:

            r,p,cx,cy=max(base,key=lambda q:q[0])
            minimum=int((self.pack.entities.get('nova') or {}).get('single_min_radius_px',256)*256)
            if r<minimum:p=int(round(p*minimum/max(1,r)));r=minimum
            return [(r,p,cx,cy)]
        targets=base[:count]
        missing=count-len(targets)
        if missing:
            # Keep supplementation with normal radii and freely placed centres
            # on this side, rather than compounding the previous ring's size.
            r,p,cx,cy=base[-1]
            rules=self.pack.entities.get('nova') or {}
            low,high=sorted(rules.get('radius_px',[88,130]))
            high=max(1.,min(high,r/256.-rules.get('gap_px',24)))
            low=min(low,high)
            for i in range(missing):
                radius=int(round((high if missing==1 else
                    high-(high-low)*i/(missing-1))*256))
                period=max(1,int(round(p*radius/max(1,r))))
                nx,ny=self._nova_ring_center(radius,base,side)
                targets.append((radius,period,nx,ny))
        return targets

    def _assign_nova_rings(self,slot):
        'Round-robin the payload pieces over the rolled ring count.'
        rings=self._payload_rings(slot['course'],slot['start'],slot['end'])
        if not rings:
            return
        targets=self._orbit_targets(rings,slot.get('rings',len({r for r,_c,_x,_y in rings.values()})),
                                    side=slot.get('side'))
        if not targets:
            return
        order=sorted(rings,key=lambda sid:(rings[sid][0],rings[sid][1],sid))
        buckets={i:[] for i in range(len(targets))}
        for i,sid in enumerate(order):
            buckets[i%len(targets)].append(sid)
        mapping={}
        for i,sids in buckets.items():
            r,p,cx,cy=targets[i]
            for j,sid in enumerate(sids):
                mapping[sid]=dict(xmp=cx,ymp=cy,radius=r,period=p,
                                  pos=int(round(p*j/max(1,len(sids)))))
        slot['orbit_map']=mapping

    def _synthesize_course(self):
        """Assemble one course out of independently rolled direction slots."""
        plan=self._slot_plan()
        courses=self.pack.programs['courses']
        if not plan:
            return courses[self.course_index]
        return compile_course(self,plan)

    def _choose_course(self):
        'Choose a runtime course.'
        count=len(self.pack.programs['courses'])
        if count<=1:
            return 0
        late=self.controller and self.controller.stage>=3
        table=(self.pack.entities or {}).get('course_weights') or {}
        weights=table.get('late' if late else 'early')
        if not weights or len(weights)!=count:
            weights=[1]*count
            for i in range(count):
                weights[i]=(1+i if late else count-i)
        return self._rng.choices(range(count),weights=weights,k=1)[0]

    def _shatter_scream(self):
        hx,hy=self.head.position(self.room_time);base=self.pack.templates['6']
        for p in self.pack.programs['shatter_screams']:
            self._profile_actor(p,dx=hx-base['xmp'],dy=hy-base['ymp'])
        self.batch.animprops(self.head_id,3,1254)

    def _platform_formed(self,side,mode=None):
        mode=mode or self.current_mode
        self._event('platform_formed',side=side,generation=self.generation,mode=mode)
        if self.generation<2 or self.controller.stage==5:
            return
        if mode=='nova':return

        self._schedule(self.room_time+.45,'platform_stable',side,self._formation_epoch,
                       course=self._course_serial)

    def _platform_stable(self,side,epoch=None):
        if epoch is not None and epoch!=self._formation_epoch:return
        self._event('platform_stable',side=side,generation=self.generation)
        self._schedule(self.room_time+.01,'vortex_birth',side,self._formation_epoch,
                       course=self._course_serial)

    def _formation_row(self,n):
        """The platform slot that hopper ``n`` (and its portal) uses."""
        rows=sorted(self.platforms.values(),key=lambda p:p['motion'].position(self.room_time)[0])
        if not rows:
            return None
        count=self.pack.entities['hopper']['formation_count']
        return rows[min(len(rows)-1,(n+1)*len(rows)//(count+1))]

    def _vortex_birth(self,side,epoch=None):
        if epoch is not None and epoch!=self._formation_epoch:return
        if self.current_mode=='nova' or self.controller.stage==5:return

        spec=self.pack.entities['hopper']
        count=spec['formation_count']
        complete_wave=max(5.,float(spec.get('first_hopper_s',1.32))+(count-1)*spec['formation_spacing_s'])
        if self.room_time+complete_wave>self._active_platform_window_end:return
        portals=0
        spawn_points=[]
        rows=sorted(self.platforms.values(),key=lambda p:p['motion'].position(self.room_time)[0])
        if not rows:return
        for n in range(count):
            row=rows[min(len(rows)-1,int((n+1)*len(rows)/(count+1)))]
            x,y=row['motion'].position(self.room_time)
            q=copy.deepcopy(self.pack.dynamic_templates['scboss.vortex'])
            q.update(xmp=int(x+(row['props']['wmp']-q['wmp'])/2),ymp=int(y-q['hmp']-8192))
            portal=self._profile_actor(dict(vid='scboss.vortex',props=q,commands=[],source_born=0.),at=self.room_time)

            self.batch.animprops(portal['objid'],3,1188)
            portals+=1
            spawn_points.append(((q['xmp']+8192,q['ymp']+4096),portal['objid']))
        self._event('vortex_create',side=side,portals=portals)
        delay=float(spec.get('first_hopper_s',1.32))
        emitted=0
        for n in range(count):
            if self._rng.random()>=spec.get('portal_spawn_probability',.8):continue
            emitted+=1
            self._schedule(self.room_time+delay+n*spec['formation_spacing_s'],'hopper_birth',n,
                           spawn_points[n][0],self._formation_epoch,spawn_points[n][1],
                           course=self._course_serial)
        self._event('hopper_wave',side=side,count=emitted)

        self._schedule(self.room_time+spec.get('wave_period_s',8.),'vortex_birth',side,
                       self._formation_epoch,course=self._course_serial)

    def _formation_hopper(self,n,point=None,epoch=None,portal_id=None):
        if epoch is not None and epoch!=self._formation_epoch:return
        if portal_id is not None and portal_id not in self.actors:return
        template=self.pack.programs['hopper_template']
        q=copy.deepcopy(template);q['commands']=[];q.pop('observed_end',None)
        p=q['props']
        if point:p.update(xmp=int(point[0]),ymp=int(point[1]))
        else:
            row=self._formation_row(n)
            if row is None:return
            x,y=row['motion'].position(self.room_time)
            p.update(xmp=int(x+(row['props']['wmp']-p['wmp'])/2),ymp=int(y-p['hmp']-4096))
        q['source_born']=0.
        actor=self._profile_actor(q)
        if portal_id is not None:
            actor['portal_id']=portal_id
            self._remove(portal_id,'INTERNAL')

    def _begin_course(self,index,shatter=True):
        'Local battle simulator runtime.'
        self._course_serial+=1
        serial=self._course_serial
        self.course_index=index
        self.course_age=self.room_time
        forced=getattr(self.options,'force_course',None)
        if forced is None:
            course=self._prepared_course or self._synthesize_course()
            self._prepared_course=None
        else:
            course=self.pack.programs['courses'][index]
        self._course_plan=course
        self._course_duration=course['duration_s']
        self._course_end=self.course_age+self._course_duration
        self._scream_serial=None
        old=list(self.platforms.values())
        self.platform_state='orbit'
        self.generation=1
        self.batch.sound(7,'scboss.fracture')
        self.batch.shake(12,700)
        for p in old:
            self._remove(p['objid'],'INTERNAL')
        for p in course['platforms']:
            self._profile_actor(copy.deepcopy(p),at=self.room_time,
                                platform=True,course=serial)
        self._event('platform_shatter',generation=1,pieces=len(self.platforms))
        for a in course['attacks']:
            if a['vid']=='scboss.scream':
                continue
            self._schedule(self.course_age+a['t'],'create',a,0,0,course=serial)
        for c in course['head']:
            self._schedule(self.course_age+c['t'],'head_command',c,course=serial)
        for f in course['formations']:
            entry=dict(f,role=f.get('role','coarse_'+f['side']),mode=f.get('mode','normal'))
            self._schedule(self.course_age+f['t'],'flow',entry,course=serial)
        self._event('course_start',index=index,duration_s=round(self._course_duration,3))

    def _plan_second_shatter(self):
        'Second fracture: the next runtime D formation while in P3/P4.'
        course=getattr(self,'_course_plan',None)
        if not course:
            course=self.pack.programs['courses'][self.course_index]
        base=self.course_age
        if course.get('second_shatter_s') is not None:
            self._second_shatters=[base+course['second_shatter_s']]
        else:

            forms=course.get('formations') or [];seen=[]
            for f in forms:
                if not seen and f['side']!='U':continue
                seen.append(f['side'])
            self._second_shatters=[self._course_end] if len(seen)>=6 else []
        self._second_index=0

    def _step_second_shatter(self):
        t=self.room_time
        while self._second_index<len(self._second_shatters):
            due=self._second_shatters[self._second_index]
            if t<due:
                return
            if self.controller.stage in (3,4):
                self._begin_fine()
                return
            self._second_index+=1

        if t>=self._course_end and self.generation==1:

            if self.controller.stage in (3,4):self._begin_fine()
            else:self._repeat_coarse()

    def _repeat_coarse(self):
        course=self._synthesize_course();first_end=course['formations'][1]['t']
        self._course_serial+=1;serial=self._course_serial
        self.course_age=self.room_time-first_end;self._course_plan=course
        self._course_duration=course['duration_s'];self._course_end=self.course_age+self._course_duration
        for ours,theirs in self._match_slot_pieces(list(self.platforms.values()),course['platforms'],repeat=True):
            for c in theirs['commands']:
                if c['t']>=first_end:self._schedule(self.course_age+c['t'],'command',ours['objid'],c,0,0,course=serial)
        for a in course['attacks']:
            if a['t']>=first_end:self._schedule(self.course_age+a['t'],'create',a,0,0,course=serial)
        for f in course['formations'][1:]:self._schedule(self.course_age+f['t'],'flow',f,course=serial)
        self._plan_second_shatter();self._event('coarse_cycle',duration_s=self._course_end-self.room_time)

    def _begin_fine(self):
        'Local battle simulator runtime.'
        self.generation=2
        self._course_serial+=1
        serial=self._course_serial
        programs=self.pack.programs
        survivors=programs['fine_platforms']
        self._fine_loop_specs=list(survivors)
        debris=programs.get('fine_debris') or ()
        kills=programs.get('fine_kills') or ()
        carry=programs.get('fine_survivors') or ()
        self.batch.sound(7,'scboss.fracture')
        self.batch.shake(12,700)
        end=0.
        for q in list(survivors)+list(debris):
            for c in q.get('commands',()):
                end=max(end,c.get('t',0))
            end=max(end,q.get('end_t',0))

        normal=[a['t'] for a in programs.get('fine_attacks',()) if a['vid']=='scboss.dshot']
        last_normal=max(normal,default=29.235)
        departures=[q['t']+c['t'] for q in survivors for c in q['commands']
                    if c.get('modifyprops',{}).get('property')==41 and q['t']+c['t']>last_normal]
        self._course_duration=min(departures,default=31.609)
        self._shatter_scream()
        old=list(self.platforms.values())
        keep=[]
        for spot in carry:
            pool=[p for p in old if p not in keep]
            if not pool:
                break
            keep.append(min(pool,key=lambda p:self._nearest_gap(p,spot)))

        for row,spot in zip(keep,carry):
            for c in spot.get('commands',()):
                if c['t']<self._course_duration:
                    self._schedule(self.room_time+c['t'],'command',row['objid'],c,0,0,course=serial)
        retire=[p for p in old if p not in keep]
        schedule=[]
        for k in kills:
            if not retire:
                break
            row=min(retire,key=lambda p:self._nearest_gap(p,k))
            retire.remove(row)
            schedule.append((k['t'],row))
        tail=(kills[-1]['t']+.55) if kills else 1.5
        for row in retire:
            schedule.append((tail,row))
        for when,row in schedule:
            self._schedule(self.room_time+when,'remove',row['objid'],'INTERNAL',
                           course=serial)
        for q in survivors:
            self._schedule(self.room_time+q['t'],'create_platform',q,0,0,
                           course=serial)
        for q in debris:
            self._schedule(self.room_time+q['t'],'create_platform',q,0,0,
                           course=serial)
        for a in programs.get('fine_attacks',()):
            if a['vid'] in ('scboss.scream','scboss.vortex','scboss.hopper'):
                continue
            if a['t']>=self._course_duration:continue
            self._schedule(self.room_time+a['t'],'create',a,0,0,course=serial)
        self.flow_role='rise2';self.current_mode='normal';self.flow_side='U';self._formation_epoch+=1
        settled=min(normal,default=12.549)-.32
        self.platform_transition_until=self.room_time+settled
        self._schedule(self.room_time+settled,'fine_settled',dict(side='U',mode='normal'),course=serial)
        self.platform_state='orbit'
        self._course_end=self.room_time+self._course_duration
        self._active_platform_window_end=self._course_end
        self._final_unlock_at=math.inf
        self._event('platform_shatter',generation=2,pieces=len(survivors),
                    debris=len(debris),retire=len(schedule),carryover=len(keep))

    def _nearest_gap(self,row,spot):
        x,y=row['motion'].position(self.room_time)
        return (x-spot['xmp'])**2+(y-spot['ymp'])**2

    def _loop_fine(self):
        'Run one more second-generation cycle on the live fragments.'
        specs=self._fine_loop_specs or []
        if not specs:
            self.platform_state='final'
            return
        rolled=self._slot_plan()
        plan=[rolled[5],rolled[2],rolled[3],rolled[4]] if len(rolled)==6 else rolled[1:5]

        if not plan:return
        live=[]
        for row in self.platforms.values():
            x,y=row['motion'].position(self.room_time)
            p=dict(row['props'],xmp=int(x),ymp=int(y))
            live.append(dict(vid=row['vid'],props=p,commands=[],source_id=row['objid'],source_born=0.))
        course=compile_course(self,plan,live_pieces=live,initial=False,exits=False)
        right=course['formations'][0]
        if math.isinf(self._final_unlock_at):
            self._final_unlock_at=self.room_time+right['attack_t']+(right['end_t']-right['attack_t'])*.5
        self._course_serial+=1;serial=self._course_serial
        byid={p['source_id']:p for p in course['platforms']}
        for objid,row in self.platforms.items():
            for c in byid[objid]['commands']:
                self._schedule(self.room_time+c['t'],'command',objid,c,0,0,course=serial)
        for a in course['attacks']:
            if a['vid'] in ('scboss.hopper','scboss.vortex'):continue
            self._schedule(self.room_time+a['t'],'create',a,0,0,course=serial)
        for f in course['formations']:
            entry=dict(f,settle_at=self.room_time+f['settle_t'])
            self._schedule(self.room_time+f['t'],'flow',entry,course=serial)
        self._course_duration=course['duration_s'];self._course_end=self.room_time+self._course_duration
        self._course_plan=course;self.course_age=self.room_time
        self._event('fine_cycle',pieces=len(live),duration_s=round(self._course_duration,1),includes_l=True)
        return

    def _fine_left_segment(self):
        """The measured left arrangement of the second-generation cycle."""
        data=getattr(self.pack,'fine_directions',None) or {}
        entry=(data.get('directions') or {}).get('L')
        if not entry:
            return None
        pieces=[dict(vid=q['vid'],
                     props=dict(vid=q['vid'],xmp=int(q['xmp']),ymp=int(q['ymp']),
                                wmp=int(q.get('wmp') or 0),hmp=int(q.get('hmp') or 0)))
                for q in entry.get('pieces') or ()]
        if len(pieces)<10:
            return None
        return dict(pieces=pieces,duration_s=float(entry.get('duration_s') or 30.),
                    source=entry.get('source'))

    def _lock_final(self):
        self._course_serial+=1
        self.platform_state='final_move';self.flow_role='final_down';self._formation_epoch+=1
        final=self.pack.programs['final_platforms']
        old=list(self.platforms.values())
        old.sort(key=lambda r:r['motion'].position(self.room_time)[0])
        targets=sorted(final,key=lambda p:p['xmp'])
        for p in targets[len(old):]:
            q=dict(p)
            if old:
                x,y=old[-1]['motion'].position(self.room_time);q.update(xmp=int(x),ymp=int(y))
            old.append(self._profile_actor(dict(vid=p['vid'],props=q,commands=[],source_born=0.),platform=True))
        for p in old[len(targets):]:self._remove(p['objid'],'INTERNAL')
        old=old[:len(targets)]
        distance=max((math.dist(r['motion'].position(self.room_time),(p['xmp'],p['ymp']))
                      for r,p in zip(old,targets)),default=0.)
        duration=max(1.5,distance/(MAX_PLATFORM_SPEED_PX_S*256.))
        for row,p in zip(old,targets):
            row['props'].update(vid=p['vid'],wmp=p['wmp'],hmp=p['hmp']);row['vid']=p['vid']
            self.batch.props(row['objid'],dict(VID=p['vid'],WMP=p['wmp'],HMP=p['hmp']))
            c=dict(modifyprops=dict(property=41,decoded=dict(to=dict(xmp=p['xmp'],ymp=p['ymp'],dt=int(math.ceil(duration*1000.))))))
            self._command(row['objid'],c,0,0,self.room_time)
        self._next_bombs=None
        self._schedule(self.room_time+duration,'final_locked',course=self._course_serial)
        self._event('platform_final_move',duration_s=duration,pieces=len(old))

    def _bomb_wave(self):
        target=nearest_player(self.players,self.head.position(self.room_time))

        bombs=self.pack.programs['final_bombs']
        portal_data=self.pack.final_portals
        offset=0
        portals=portal_data.get('portals',[])
        if self.platforms and portals:
            left=min(r['motion'].position(self.room_time)[0] for r in self.platforms.values())
            right=max(r['motion'].position(self.room_time)[0]+r['props']['wmp'] for r in self.platforms.values())
            a=min(p['props']['xmp'] for p in portals);b=max(p['props']['xmp']+p['props']['wmp'] for p in portals)
            offset=(left+right-a-b)/2.
        if self._final_portal_offset is None:self._final_portal_offset=offset
        else:offset=self._final_portal_offset
        if not self._final_portal_ids:
            for p in portal_data.get('portals',[]):
                row=self._profile_actor(p,dx=offset)
                self._final_portal_ids.append(row['objid'])
        delay=portal_data.get('bomb_delay_s',0.)
        for p in bombs:
            self._schedule(self.room_time+delay,'create',p,offset,0)
        self._event('bomb_portals',columns=len(portal_data.get('portals',[])),delay_s=delay)
        self._event('bomb_wave',columns=len(bombs),target=target.objid if target else None)

    def _squid_row(self,objid,x,y,born):

        return dict(objid=objid,x=float(x),y=float(y),home_x=float(x),home_y=float(y),born=born,
            active_at=born+2.,move_at=born+2.+2.32,active=False,hp=self.pack.entities['squid'].get('hp',1500),
            next_shoot=born+2.+1.314,
            phase=(objid%7)*.4,motion=NativeMotion(x,y),wmp=24576.,hmp=24576.,
            vx=0.,vy=0.,gait=None,leg=None)

    def _spawn_pair(self,at):
        flights=self.pack.programs['flights']
        n=self._rng.randrange(0,len(flights)//2)*2
        hx,hy=self.head.position(at)
        base=self.pack.templates['6']
        dx,dy=hx-base['xmp'],hy-base['ymp']
        chosen=flights[n:n+2]
        patterns=self.pack.spawn_patterns
        if patterns.get('flights'):
            sector=self.flow_side
            if sector=='LR':sector=self._rng.choice(('L','R'))
            if at<self.platform_transition_until and self.platforms:
                xy=[p['motion'].position(at) for p in self.platforms.values()]
                cx=sum(x for x,y in xy)/len(xy)-hx-61440;cy=sum(y for x,y in xy)/len(xy)-hy-107520
                actual=('R' if cx>0 else 'L') if abs(cx)>=abs(cy) else ('D' if cy>0 else 'U')
                sector=self._rng.choice((actual,sector))
            weights=patterns['counts'].get(sector) or patterns['counts']['U']
            sectors=list(weights)
            chosen=[]
            for emitter in range(2):
                desired=self._rng.choices(sectors,weights=[weights[k] for k in sectors],k=1)[0]
                pool=[f for f in patterns['flights'] if f['side']==desired]
                half=[f for f in pool if (f['props']['xmp']<364544)==(emitter==0)]
                if half:pool=half
                if pool:chosen.append(dict(props=self._rng.choice(pool)['props']))
        for profile in chosen:
            p=translate(profile['props'],dx,dy)
            i=self._alloc()
            self.batch.create(i,normalize_props('Properties',p))
            if p.get('move'):
                self.batch.modify(i,'MOVE',message=('Properties_Move',normalize_props('Properties_Move',p['move'])))
            self.batch.sound(i,'scboss.summon')
            row=self._squid_row(i,p['xmp'],p['ymp'],at)
            if p.get('move'):
                row['motion'].set(p['move'],at)
            self.squids[i]=row
            self._event('squid_birth',objid=i)

    def _repair_squid(self,victim):
        s=self.squids.pop(victim)
        i=self._alloc()
        p=self.pack.wire_props('scboss.summons')
        p.update(xmp_=int(s['x']),ymp_=int(s['y']),hitpoints_=max(1,s['hp']),invulnerable_=False,
                 damage_=self.pack.entities['squid'].get('damage',20))
        self.batch.create(i,p)
        s['objid']=i
        self.squids[i]=s

    def _squid_shots(self,objid,at):
        s=self.squids.get(objid)
        if not s or not s['active'] or self.controller.state=='done':
            return
        spec=self.pack.entities['squid']
        origin=(s['x']+12288,s['y']+12288)

        target=nearest_player(self.players,origin,spec.get('aggro_radius_mp'))
        if target is None:
            return
        tx,ty=target.center()
        angle=math.atan2(ty-origin[1],tx-origin[0])

        spread=spec.get('spread_degrees',(0.,))
        weights=spec.get('shot_count_weights')
        if weights:
            counts=sorted(int(n) for n in weights)
            count=self._rng.choices(counts,weights=[weights[str(n)] for n in counts],k=1)[0]
            spacing=spec.get('spread_step_degrees',12.)
            spread=[(n-(count-1)/2.)*spacing for n in range(count)]
        for degrees in spread:
            turn=math.radians(degrees)
            p=copy.deepcopy(self.pack.dynamic_templates['scboss.sshot'])
            p.update(xmp=int(origin[0]-2560),ymp=int(origin[1]-2560),
                pose=1,draw=True,animprops=dict(animid=5,time=528),
                move=dict(vel=dict(xvel=int(math.cos(angle+turn)*spec['shot_speed_mp_s']/MP_PER_WIRE_VELOCITY_SECOND),
                                   yvel=int(math.sin(angle+turn)*spec['shot_speed_mp_s']/MP_PER_WIRE_VELOCITY_SECOND))))
            q=dict(vid='scboss.sshot',props=p,commands=[],source_born=0.)
            self._profile_actor(q,at=at)
            self.batch.sound(self._next_objid,'scboss.sshot')
        self._event('squid_shoot',objid=objid,target=target.objid,shots=len(spread))

    def _next_leg(self,s,t,spec,target):
        """Lock a new heading for one leg.

        The first leg after the vertical-only window is vertical; later legs
        either keep going up/down or lock the player's *current* position and
        travel that way for a while.  Accelerations are applied in
        _squid_move, so the change of heading is a smooth turn.
        """
        gait=s['gait'];vertical=t<gait['hold_until']
        curve=self._rng.choice(self.pack.squid_calibration['speed_curves'])
        duration=curve['duration']
        destination=None
        if vertical:
            duration=min(duration,gait['hold_until']-t)
            ux,uy=0.,gait['sign']
        elif target:
            destination=target.center()
            destination=self._squid_destination(s,destination)
            dx,dy=destination[0]-(s['x']+12288),destination[1]-(s['y']+12288)
            distance=math.hypot(dx,dy)
            ux,uy=(dx/distance,dy/distance) if distance>16*256 else (0.,0.)
        else:
            ux,uy=0.,0.
        speeds=curve['speeds']
        s['leg']=dict(until=t+duration,start=t,duration=duration,curve=curve,
                      ux=ux,uy=uy,target=destination,origin=(s['x']+12288,s['y']+12288),
                      times=[v[0] for v in speeds],vx=0.,vy=0.)
        self._event('squid_leg',objid=s['objid'],target=target.objid if target and not vertical else None,
                    direction=[round(ux,6),round(uy,6)],duration_s=round(duration,6),vertical_only=vertical)

    def _squid_move(self,objid,s,t,dt,spec,target):
        """Acceleration-limited legs; a heading is held for one leg only."""
        if t>=s['leg']['until']:
            self._next_leg(s,t,spec,target)
        leg=s['leg']
        points=leg['curve']['speeds'];age=max(0.,t-leg['start'])
        index=min(len(points)-1,bisect.bisect_right(leg['times'],age))
        if index:
            a,b=points[index-1],points[index]
            f=max(0.,min(1.,(age-a[0])/max(1e-6,b[0]-a[0])))
            speed=a[1]+(b[1]-a[1])*f
        else:speed=points[0][1]
        speed=min(speed,175*256.)*spec.get('movement_speed_multiplier',1.)
        if leg['target']:
            dx=leg['target'][0]-(s['x']+12288);dy=leg['target'][1]-(s['y']+12288)
            remaining=dx*leg['ux']+dy*leg['uy']
            if remaining<=16*256:
                speed=0.
                leg['until']=min(leg['until'],t+.1)
            else:
                speed=min(speed,math.sqrt(2*min(spec['accel_x_mp_s2'],spec['accel_y_mp_s2'])*(remaining-16*256)))
        leg['vx'],leg['vy']=leg['ux']*speed,leg['uy']*speed
        step_x=spec.get('accel_x_mp_s2',150.*256)*dt
        step_y=spec.get('accel_y_mp_s2',400.*256)*dt
        s['vx']+=max(-step_x,min(step_x,leg['vx']-s['vx']))
        s['vy']+=max(-step_y,min(step_y,leg['vy']-s['vy']))
        nx=s['x']+s['vx']*dt
        ny=s['y']+s['vy']*dt
        nx,ny,blocked=self._squid_solid_collision(s,nx,ny)
        if blocked=='x':
            s['vx']=0.
            leg['until']=min(leg['until'],t+.15)
        elif blocked=='y':
            s['vy']=0.
            leg['vy']=0.
            leg['until']=min(leg['until'],t+.15)
        s['x'],s['y']=nx,ny
        s['home_x'],s['home_y']=nx,ny
        s['motion'].correct(nx,ny,t)
        self._sync_actor_position(s,nx,ny,force=bool(blocked and blocked!=s.get('last_blocked')))
        s['last_blocked']=blocked

    def _sync_actor_position(self,row,x,y,force=False):

        if force or self.room_time+1e-9>=row.get('next_position_sync',-math.inf):
            self.batch.move_to(row['objid'],int(x),int(y),dt=50)
            row['next_position_sync']=self.room_time+self.POSITION_SYNC_S

    def _actors_step(self,dt):
        t=self.room_time
        spec=self.pack.entities['squid']
        for i,s in list(self.squids.items()):
            if not s['active']:

                nx,ny=s['motion'].position(t)
                nx,ny,blocked=self._squid_solid_collision(s,nx,ny)
                if blocked:
                    s['motion'].correct(nx,ny,t)
                    self.batch.modify(i,'MOVE',message=('Properties_Move',{}))
                    self.batch.move_to(i,int(nx),int(ny),dt=50)
                s['x'],s['y']=nx,ny
            if not s['active'] and t>=s['active_at']:
                s['active']=True
                s['x'],s['y']=s['motion'].position(t)
                s['home_x']=s['x']
                s['home_y']=s['y']

                s['move_at']=t+self._rng.uniform(2.26,2.40)
                target=nearest_player(self.players,(s['x']+12288,s['y']+12288),
                                      spec.get('aggro_radius_mp'))

                if target is not None:
                    sign=1. if target.center()[1]>s['y']+12288 else -1.
                else:
                    sign=self._rng.choice((-1.,1.))
                s['gait']=dict(start=s['move_at'],
                    hold_until=s['move_at']+self._rng.uniform(3.3,4.0),
                    sign=sign)
                s['vx']=0.
                s['vy']=0.
                s['leg']=dict(until=s['move_at'],vx=0.,vy=0.)
                self._next_leg(s,s['move_at'],spec,target)

                s['motion'].correct(s['x'],s['y'],t)
                self.batch.modify(i,'MOVE',message=('Properties_Move',{}))
                self.batch.modify(i,'VID',stringval='scboss.summons')

                self.batch.props(i,dict(OWNERTYPE=2,DAMAGE=spec.get('damage',20),HITPOINTS=spec.get('hp',1500),
                                        INVULNERABLE=False,SOUNDPREFIX='scboss.summons'))
                self.batch.animprops(i,1,1254)
                self._event('squid_active',objid=i)
            if s['active']:

                target=nearest_player(self.players,(s['x']+12288,s['y']+12288),
                                      spec.get('aggro_radius_mp'))
                if t>=s.get('move_at',0.):
                    self._squid_move(i,s,t,dt,spec,target)
                if t>=s['next_shoot']:
                    s['next_shoot']=t+spec['shoot_period_s']
                    if target:
                        self.batch.animprops(i,2,1254)
                        self._schedule(t+spec['shoot_emit_s'],'squid_shots',i)
                if self._done_at is not None and t>=self._done_at+20.:
                    self._remove(i,'LIFETIME')
        boxes=None
        for i,a in list(self.actors.items()):
            if a.get('expiry',math.inf)<=t:
                self._remove(i,'LIFETIME')
            elif a['vid']=='scboss.plinkos':
                self._plinko_step(a,dt)
            elif a['vid']=='scboss.bits':
                self._bits_step(a,dt)
            elif a['vid'] in self.BLOCKED_SHOTS and not a.get('charging'):
                if boxes is None:

                    boxes=self._solid_boxes()
                self._projectile_blocked(a,dt,boxes)
            elif a.get('bounds'):

                b=self.pack.scene['bounds']
                x,y=a['motion'].position(t)
                margin=4096.*256.
                left,top=b['x']*256.-margin,b['y']*256.-margin
                right,bottom=(b['x']+b['w'])*256.+margin,(b['y']+b['h'])*256.+margin
                if x<left or x>right or y<top or y>bottom:
                    self._remove(i,'LIFETIME')
            elif a['vid']=='scboss.hopper':
                self._hopper_step(a,dt)
            elif a['vid']=='scboss.bomb':
                self._bomb_step(a,dt)

    def _plinko_step(self,a,dt):
        """One-way pegs support/bounce the ball while it rolls to their edge."""
        speed=self._projectile_speed()
        x,y=a['x'],a['y'];p=a['props'];g=340000.*speed*speed;vy=a['vy']
        surfaces=self._platform_surfaces(dt)
        support=next((q for q in surfaces if q[0]==a.get('support')),None)
        if support:
            _,box,old,_=support
            if x+p['wmp']>old[0] and x<old[0]+old[2] and abs(y+p['hmp']-old[1])<512:
                x+=box[0]-old[0];y+=box[1]-old[1]
        a.pop('support',None)
        if abs(a['vx'])<6000.*speed:a['vx']=math.copysign(6000.*speed,a['vx'] or 1.)
        nx=x+a['vx']*dt;ny=y+vy*dt+.5*g*dt*dt
        a['vy']=min(180000.*speed,vy+g*dt)
        hits=[];rolling_changed=False
        for ident,box,old,terrain in surfaces:
            if terrain==2:

                before=y+p['hmp']-old[1];after=ny+p['hmp']-box[1]
                if before>512 or after<0 or after<=before:continue
                f=max(0.,min(1.,-before/(after-before)))
                cx=x+(nx-x)*f;px=old[0]+(box[0]-old[0])*f
                if cx+p['wmp']>px and cx<px+box[2]:hits.append((f,ident,box,(0,-1)))
            elif terrain==1:
                hit=sweep((x,y),(nx,ny),(p['wmp'],p['hmp']),box)
                if hit:hits.append((hit[0],ident,box,hit[1]))
        if hits:
            f,ident,box,normal=min(hits,key=lambda q:q[0])
            cx,cy=stop_at_face((x+(nx-x)*f,y+(ny-y)*f),(p['wmp'],p['hmp']),box,normal)
            rest=dt*(1-f)
            if normal[1]<0:
                rebound=max(0.,vy+g*dt*f)*.625

                a['vy']=-rebound if rebound>=12000*speed else 0.
                if a['vy']==0.:
                    velocity=roll_velocity(cx,p['wmp'],a['vx'],surfaces,box[1],speed)
                    rolling_changed=abs(velocity-a['vx'])>1.
                    a['vx']=velocity
                    nx=cx+a['vx']*rest
                else:nx=x+a['vx']*dt
                ny=cy-1.+a['vy']*rest
                if a['vy']==0. and nx+p['wmp']>box[0] and nx<box[0]+box[2]:a['support']=ident
            elif normal[1]>0:
                a['vy']=abs(a['vy'])*.625;nx=x+a['vx']*dt;ny=cy+1.+a['vy']*rest
            else:
                a['vx']=-a['vx']*.8;nx=cx+normal[0]+a['vx']*rest;ny=y+vy*dt+.5*g*dt*dt
            if self.room_time>=a.get('bounce_notice_at',-math.inf):
                a['bounce_notice_at']=self.room_time+.15
                self._event('plinko_bounce',objid=a['objid'],normal=list(normal))
                self.batch.sound(a['objid'],'scboss.pshotp')
        a['x'],a['y']=nx,ny;a['motion'].correct(nx,ny,self.room_time)
        self._sync_actor_position(a,nx,ny,force=bool(hits and (abs(a['vy'])>=12000.*speed or rolling_changed)))

        b=self.pack.scene['bounds']
        if ny>(b['y']+b['h']+192)*256:
            self._remove(a['objid'],'INTERNAL')

    def _platform_surfaces(self,dt=None):
        dt=1/self.HZ if dt is None else dt
        key=(self.room_time,len(self.platforms),self._course_serial,dt)
        if self._surface_cache is not None and self._surface_cache[0]==key:return self._surface_cache[1]
        rows=[]
        for ident,row in self.platforms.items():
            p=row['props'];terrain=p.get('terraintype',0)
            if terrain not in (1,2):continue
            x,y=row['motion'].position(self.room_time)
            ox,oy=row['motion'].position(max(0.,self.room_time-dt))
            rows.append((ident,(x,y,p['wmp'],p['hmp']),(ox,oy,p['wmp'],p['hmp']),terrain))
        self._surface_cache=(key,rows)
        return rows

    def _solid_boxes(self):
        """Every surface a blocktype-4 projectile is stopped by."""
        boxes=[]
        for ident in ('1','2','3','4'):
            p=self.pack.templates.get(ident)
            if p and p.get('terraintype')==1:
                boxes.append((p['xmp'],p['ymp'],p.get('wmp',0),p.get('hmp',0)))
        if self.platform_state=='initial':
            base=self.pack.templates.get('12')
            if base:
                boxes.append((base['xmp'],base['ymp'],base.get('wmp',0),base.get('hmp',0)))
        for platform in self.platforms.values():

            if platform['props'].get('terraintype')!=1:
                continue
            px,py=platform['motion'].position(self.room_time)
            boxes.append((px,py,platform['props'].get('wmp',0),
                          platform['props'].get('hmp',0)))
        return boxes

    def _projectile_blocked(self,a,dt,boxes=None):
        'Retire a projectile on the surface it actually hits.'

        if a['vid'] not in self.BLOCKED_SHOTS:
            return False
        if self.room_time-a['born']<.15:
            return False

        w=a['props'].get('wmp',8192); h=a['props'].get('hmp',8192)
        now=self.room_time
        x1,y1=a['motion'].position(now)
        x0,y0=a['motion'].position(max(a['born'],now-dt))
        if boxes is None:
            boxes=self._solid_boxes()
        hits=[(hit[0],box,hit[1]) for box in boxes
              for hit in [sweep((x0,y0),(x1,y1),(w,h),box)] if hit]
        if hits:
            fraction,box,normal=min(hits,key=lambda v:v[0])
            point=(x0+(x1-x0)*fraction,y0+(y1-y0)*fraction)
            x,y=stop_at_face(point,(w,h),box,normal)
            a['motion'].correct(x,y,now)
            self.batch.move_to(a['objid'],int(x),int(y))
            self._remove(a['objid'],'BLOCKED')
            return True
        return False

    def _hopper_first_delay(self):
        spec=self.pack.entities.get('hopper') or {}
        lo,hi=(spec.get('first_jump_s') or (0.56,1.44))
        return self._rng.uniform(float(lo),float(hi))

    def _hopper_jump_period(self):
        """One measured hop interval (122 hops: min .68, med 1.52, max 5.55)."""
        spec=self.pack.entities.get('hopper') or {}
        table=spec.get('jump_period_weights') or {}
        if table:
            keys=[float(k) for k in table]
            return self._rng.choices(keys,weights=[table[k] for k in table],k=1)[0]
        return float(spec.get('jump_period_s',1.5))

    def _hopper_step(self,a,dt):
        x,y=a['motion'].position(self.room_time)
        was_grounded=a['grounded'];jumped=False
        p=a['props']
        target=nearest_player(self.players,(x+p['wmp']/2,y+p['hmp']/2))
        spec=self.pack.entities['hopper']
        direction=a['direction']
        if a['grounded'] and self.room_time>=a['next_jump']:
            desired=(1 if target.center()[0]>x+p['wmp']/2 else -1) if target else direction
            if desired!=direction and a['turns']<spec['max_direction_changes']:
                direction=desired;a['direction']=direction;a['turns']+=1
                self._event('hopper_turn',objid=a['objid'],turns=a['turns'],target=target.objid)
            a.update(vy=-spec['jump_mp_s'],grounded=False,
                     next_jump=self.room_time+self._hopper_jump_period())
            jumped=True
            self.batch.animprops(a['objid'],5,330)
        a['vx']=direction*spec['speed_mp_s']
        a['vy']=min(160000.,a['vy']+spec['gravity_mp_s2']*dt)
        a['grounded']=False
        nx,ny=x+a['vx']*dt,y+a['vy']*dt
        if a['vy']>=0:
            for _,box,_,terrain in self._platform_surfaces(dt):
                px,py,w,_=box
                if nx+p['wmp']>px and nx<px+w and y+p['hmp']<=py and ny+p['hmp']>=py:
                    ny=py-p['hmp']
                    a['vy']=0.
                    a['grounded']=True
                    break
        a['motion'].correct(nx,ny,self.room_time)
        self._sync_actor_position(a,nx,ny,force=jumped or was_grounded!=a['grounded'])
        facing=0 if direction>=0 else 180;pose=5 if a['grounded'] else 3
        if a.get('sent_facing')!=facing:
            self.batch.modify(a['objid'],'FACING',intval=facing);a['sent_facing']=facing
        if a.get('sent_pose')!=pose:
            self.batch.modify(a['objid'],'POSE',intval=pose);a['sent_pose']=pose
        if ny>1007616:
            self._remove(a['objid'],'BLOCKED')

    def _squid_solid_collision(self,s,nx,ny):
        "Block a squid's anchor against solid (terrain type 1) ledges."
        ox,oy=s['x'],s['y']
        blocked=None
        w,h=s.get('wmp',24576.),s.get('hmp',24576.)
        boxes=[]
        for platform in self.platforms.values():

            if platform['props'].get('terraintype')!=1:
                continue
            px,py=platform['motion'].position(self.room_time)
            boxes.append((px,py,platform['props'].get('wmp',0),
                          platform['props'].get('hmp',0)))
        if self.platform_state=='initial':

            base=self.pack.templates.get('12')
            if base:
                boxes.append((base['xmp'],base['ymp'],base['wmp'],base['hmp']))
        for box in boxes:
            hit=sweep((ox,oy),(nx,ny),(w,h),box)
            if hit is None:continue
            fraction,normal=hit

            nx,ny=stop_at_face((nx,ny),(w,h),box,normal)
            blocked='x' if normal[0] else 'y'
        return nx,ny,blocked

    def _squid_destination(self,s,target):
        """Head toward the player through a visible corner when obstructed.

        A solid formation can stand between the squid and a player below
        it. Choose the shorter visible way around that formation, rather
        than a random patrol direction or flying through its body.
        """
        size=(s.get('wmp',24576.),s.get('hmp',24576.));w,h=size
        start=(s['x'],s['y']);end=(target[0]-w/2,target[1]-h/2)
        boxes=self._solid_boxes()
        hits=[(hit[0],box) for box in boxes for hit in [sweep(start,end,size,box)] if hit]
        if not hits:return target
        _,box=min(hits,key=lambda v:v[0]);bx,by,bw,bh=box;gap=512.
        corners=[(bx-w-gap,by-h-gap),(bx+bw+gap,by-h-gap),
                 (bx-w-gap,by+bh+gap),(bx+bw+gap,by+bh+gap)]
        visible=[p for p in corners if not any(sweep(start,p,size,b) for b in boxes)]
        if not visible:return target
        point=min(visible,key=lambda p:math.dist(start,p)+math.dist(p,end))
        return (point[0]+w/2,point[1]+h/2)

    def _eye_origin(self,objid,at):
        p=self.pack.templates[str(objid)]
        base=self.pack.templates['6']
        hx,hy=self.head.position(at)
        ex,ey=hx+p['xmp']-base['xmp'],hy+p['ymp']-base['ymp']
        return (ex+p['wmp']/2,ey+p['hmp']/2)

    def _charge_eye_shot(self,objid,at):
        """A stationary sshot in the eye muzzle until its emit point."""
        origin=self._eye_origin(objid,at)
        q=copy.deepcopy(self.pack.dynamic_templates['scboss.sshot'])
        q.update(xmp=int(origin[0]-2560),ymp=int(origin[1]-2560),
                 hitboxdamage=0,damage=0,ownertype=0,blocktype=0,lifetime=-1,

                 z=7,draw=True,pose=1,animprops=dict(animid=1,time=1056,loop=True))
        row=self._profile_actor(dict(vid='scboss.sshot',props=q,commands=[],source_born=0.),
                                at=at)

        row['expiry']=at+3.
        row['charging']=True
        row['charged_by']=objid
        row['charge_origin']=origin
        return row['objid']

    def _eye_shot(self,objid,at):
        origin=self._eye_origin(objid,at)
        target=nearest_player(self.players,origin)
        if target:
            tx,ty=target.center(); angle=math.atan2(ty-origin[1],tx-origin[0])
        else:
            angle=math.atan2(573440-origin[1],409600-origin[0])
        speed=self._projectile_speed()
        damage=scaled_int(20,(self.pack.entities.get('difficulty') or {}).get('enemy_damage',1.))
        vel={'xvel':int(math.cos(angle)*78000*speed/MP_PER_WIRE_VELOCITY_SECOND),
             'yvel':int(math.sin(angle)*78000*speed/MP_PER_WIRE_VELOCITY_SECOND)}
        shot=self._charge_shots.pop(objid,None)
        row=self.actors.get(shot) if shot else None
        if row is None:
            q=copy.deepcopy(self.pack.dynamic_templates['scboss.sshot'])
            q.update(xmp=int(origin[0]-2560),ymp=int(origin[1]-2560))
            row=self._profile_actor(dict(vid='scboss.sshot',props=q,commands=[],source_born=0.),
                                    at=at)
            shot=row['objid']
        row.pop('charging',None)
        row.pop('expiry',None)
        row['bounds']=True
        x,y=origin[0]-2560,origin[1]-2560
        row['motion'].correct(x,y,at)
        self.batch.move_to(shot,int(x),int(y))
        row['motion'].set({'vel':vel},at)
        self.batch.modify(shot,'MOVE',message=('Properties_Move',normalize_props('Properties_Move',{'vel':vel})))
        self.batch.props(shot,dict(DAMAGE=damage,HITBOXDAMAGE=damage,OWNERTYPE=2,BLOCKTYPE=4,LIFETIME=-1,Z=7))

        row['props'].update(dict(damage=damage,hitboxdamage=damage,ownertype=2,
                                 blocktype=4,lifetime=-1,z=7))
        self.batch.animprops(shot,5,528)
        self.batch.sound(shot,'scboss.sshot')
        self._event('eye_shot',objid=objid,shot=shot)

    def _bomb_step(self,a,dt):
        t=self.room_time
        if not a['falling'] and t>=a['fall_at']:
            a['falling']=True
            move=scale_move(dict(vel=dict(xvel=0,yvel=0,yaccel=87,maxyvel=48000)),self._projectile_speed())
            a['motion'].set(move,a['fall_at'])
            self.batch.modify(a['objid'],'MOVE',message=('Properties_Move',normalize_props('Properties_Move',move)))
        if not a['falling']:return
        x,y=a['motion'].position(t);ox,oy=a['motion'].position(max(a['fall_at'],t-dt));p=a['props']
        impacts=[]
        for platform in self.platforms.values():
            px,py=platform['motion'].position(t)
            if oy+p['hmp']<=py<=y+p['hmp'] and x+p['wmp']>px and x<px+platform['props']['wmp']:
                impacts.append(py)
        if impacts:
            a['motion'].correct(x,min(impacts)-p['hmp'],t)
            self.batch.move_to(a['objid'],int(x),int(min(impacts)-p['hmp']))
            self._remove(a['objid'],'BLOCKED')
        elif y>1007616:
            self._remove(a['objid'],'BLOCKED')

    def _bits_step(self,a,dt):
        if a.get('landed'):return
        t=self.room_time;p=a['props'];x,y=a['motion'].position(t)
        ox,oy=a['motion'].position(max(a['born'],t-dt))
        hits=[]
        for row in self.platforms.values():
            px,py=row['motion'].position(t)
            if oy+p['hmp']<=py<=y+p['hmp'] and x+p['wmp']>px and x<px+row['props']['wmp']:
                hits.append(py)
        if hits:
            y=min(hits)-p['hmp'];a['landed']=True;a['motion'].correct(x,y,t)
            self.batch.modify(a['objid'],'MOVE',message=('Properties_Move',{}))
            self.batch.move_to(a['objid'],int(x),int(y));self.batch.modify(a['objid'],'POSE',intval=4)

    def _remove(self,objid,reason):
        small_enemy=objid in self.squids
        row=self.actors.pop(objid,None) or self.platforms.pop(objid,None) or self.squids.pop(objid,None)
        if row is None:
            return
        if small_enemy and is_hp_death(reason):
            x,y=row['motion'].position(self.room_time)
            props=row.get('props',row)
            self.supplies.killed(x+props.get('wmp',0)/2.,y+props.get('hmp',0)/2.)
        if row.get('vid')=='scboss.bomb' and reason in (3,4,5,'BLOCKED'):

            x,y=row['motion'].position(self.room_time)

            edges=[(r['motion'].position(self.room_time)[0],r['motion'].position(self.room_time)[0]+r['props']['wmp']) for r in self.platforms.values()]
            left=min((a for a,b in edges),default=x-1000*256);right=max((b for a,b in edges),default=x+1000*256)
            for k in range(8):
                p=copy.deepcopy(self.pack.dynamic_templates['scboss.bits'])
                bx=x+(row['props']['wmp']-p['wmp'])/2.;by=y+10*256
                vx=self._rng.uniform(-74000,74000)
                if bx-left<180*256 and vx<0:vx=-vx
                if right-bx<180*256 and vx>0:vx=-vx
                vy=self._rng.uniform(-55000,-35000)
                p.update(xmp=int(bx),ymp=int(by),lifetime=1500,pose=3,
                    move=dict(vel=dict(xvel=int(vx/MP_PER_WIRE_VELOCITY_SECOND),yvel=int(vy/MP_PER_WIRE_VELOCITY_SECOND),yaccel=54,maxyvel=30000)))
                self._profile_actor(dict(vid='scboss.bits',props=p,commands=[],source_born=0.))
            self._event('bomb_bits',parent=objid,pieces=8)
        self.batch.destroy(objid,ownerid=self.worldid,reason=reason)
        self._event('actor_destroy',objid=objid,reason=reason)

    def status(self):
        result=dict(map=self.MAP_ID,phase=self.phase,time=round(self.room_time,3),head_objid=self.head_id,
            squids=len(self.squids),actors=len(self.actors),platforms=len(self.platforms),
            platform_state=self.platform_state,generation=self.generation,course=self.course_index,frames=self.frames)
        if self.controller:
            result['boss']=self.controller.status()
        if self.player:
            result['player']=dict(x=self.player.xmp,y=self.player.ymp,hp=self.player.hp)
        return result

    def _projectile_speed(self):
        return (self.pack.entities.get('difficulty') or {}).get('projectile_speed',1.)
