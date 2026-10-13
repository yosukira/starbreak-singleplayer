"""Native Hulk mid host: live decisions, original sprites and resolved hits."""
import math
import random
import threading
import time
from pathlib import Path
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from .controller import HulkMidController
from .scene import HulkMidPack,normalize

def roll_create_world(blob,seed):
    return HulkMidPack().rolled_world(blob,seed)

def sweep(start,end,size,rect):
    if all(c-s<a<c+w for a,c,w,s in zip(start,rect[:2],rect[2:],size)):

        return None
    lo,hi=0.,1.
    for a,b,c,w,s in zip(start,end,rect[:2],rect[2:],size):
        delta=b-a
        if abs(delta)<1e-10:

            if a<=c-s or a>=c+w:return None
            continue
        x,y=(c-s-a)/delta,(c+w-a)/delta
        lo=max(lo,min(x,y));hi=min(hi,max(x,y))
        if lo>hi:return None
    return lo if 0<=lo<=1 else None

class HulkMidLiveSession:
    HZ=60.
    def __init__(self,server,channel,options,room=None,log=None,bridge=None,pack=None,seed=None,players=None):
        self.server,self.channel,self.options=server,channel,options
        self.log=log or (lambda x:None)
        self.layouts=getattr(server,'layouts',None) or pb_layout.default_layouts()
        path=Path(getattr(options,'create_world_hex','') or '').parent
        self.pack=pack or HulkMidPack(path if (path/'entities.json').exists() else None)
        chosen=getattr(options,'room_roll_seed',None)
        self.seed=int(chosen if chosen is not None else seed if seed is not None else random.SystemRandom().randrange(1<<30))
        self.worldid=int(getattr(options,'worldid',1) or 1)
        self.bridge=bridge or PlayerBridge(int(getattr(options,'bridge_port',21550) or 21550),log=self.log)
        self.owns_bridge=bridge is None
        self.players_provider=players
        self.batch=DeltaBatch(self.layouts,self.worldid)
        self._stop,self._thread=threading.Event(),None
        self.player_type=1
        self.prepare()

    def prepare(self):
        self.supplies=SupplyDrops(self, 'hulk_mid')
        self.batch.clear();self.rng=random.Random(self.seed)
        self.room_time=0.;self.phase='waiting';self.player=None;self.controller=None
        self.objects,self.traps=self.pack.rolled_objects(self.seed)
        self.actors={};self.retired=set();self.events=[];self.fans={};self.frames=0
        self.counts=dict(lasers=0,minis=0,trails=0,native_damage=0,native_destroy=0)
        self._next_id=1000;self._wire_ms=0.;self._sent_ms=0;self.bootstrapped=False
        self.players={}
        self._frame_start=0.
        for oid,tr in self.traps.items():
            tr.update(hp=800,next_shot=0.,pos=[self.objects[oid]['xmp'],self.objects[oid]['ymp']])
        self.fixed_solids=[tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp')) for p in self.objects.values()
            if p.get('vid')=='hulk.wall']
        self.ledges=[tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp')) for p in self.objects.values()
            if p.get('vid')=='hulk.ledge']
        self._tick_solids=self.fixed_solids

    def start(self):
        if self.owns_bridge:self.bridge.start()
        self.prepare();self._stop.clear()
        self._thread=threading.Thread(target=self._run,daemon=True);self._thread.start()
        self.log('hulk_mid live session started (fixed room, seeded traps, independent seekers)')

    def stop(self,stop_bridge=True):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:self.bridge.stop()

    def _run(self):
        dt=1/self.HZ;deadline=time.monotonic()
        while not self._stop.is_set():
            deadline+=dt
            try:self.step(dt)
            except Exception:
                import traceback
                self.log('hulk_mid tick failed: '+traceback.format_exc());self._stop.set();break
            self._stop.wait(max(0.,deadline-time.monotonic()))
            if deadline<time.monotonic()-dt:deadline=time.monotonic()

    def record(self,kind,**values):
        self.events.append(dict(kind=kind,at=self.room_time,**values))
        if len(self.events)>3000:self.events=self.events[-2000:]

    def _create(self,vid,pos,creator=0,changes=None):
        self._next_id+=1;oid=self._next_id
        props=self.pack.props(vid);props.update(xmp=round(pos[0]),ymp=round(pos[1]))
        props.update(changes or {})
        self.batch.create(oid,normalize('Properties',props,self.layouts),creatorid=creator,offset=self._offset())
        self.actors[oid]=dict(id=oid,vid=vid,pos=list(pos),props=props,vel=[0.,0.],born=self.room_time,
            expire=self.room_time+props['lifetime']/1000 if 'lifetime' in props else None,next_sync=0.,pose=None,hp=props.get('hitpoints'))
        return oid

    def native_retirement_capability(self,oid):
        """Roles from this host's actual spawn sites, never a client flag."""
        if type(oid)is not int or oid<=0 or oid in self.retired:return None
        row=self.actors.get(oid)
        if row is None:return None
        if row.get('kind')=='boss_death':role='fx'
        elif row.get('kind')=='mini' or self.controller is not None and oid in self.controller.bosses:role='npc'
        elif row['vid']=='hulk.plasmahazard':role='projectile'
        elif row['vid'] in ('hulk.bossspawn','hulk.bossemitpoint'):role='fx'
        else:return None
        expiry=row.get('expire')
        if type(expiry)not in (int,float) or not math.isfinite(expiry) or expiry<0:expiry=None
        return dict(ownerid=self.worldid,objid=oid,role=role,
            expires_at=expiry,room_time=self.room_time,
            lifetime_due=expiry is not None and self.room_time>=expiry)

    def _retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        row=self.actors.get(oid)
        if row and row.get('kind')=='mini' and is_hp_death(reason):
            self.supplies.killed(row['pos'][0]+row['props'].get('wmp',0)/2.,
                                 row['pos'][1]+row['props'].get('hmp',0)/2.)
        self.retired.add(oid);self.actors.pop(oid,None)
        if not native or getattr(self.bridge,'shared_room',False):
            self.batch.destroy(oid,ownerid=self.worldid,reason=reason,offset=self._offset())

    def _offset(self):
        return max(0,round((self.room_time-self._frame_start)*1000))

    def _sync(self,row):
        if self.room_time+1e-9<row['next_sync']:return
        row['next_sync']=self.room_time+.05
        self.batch.move_to(row['id'],round(row['pos'][0]),round(row['pos'][1]),dt=50,offset=self._offset())
        pose=1 if math.hypot(*row['vel'])<1 else 2 if row['vel'][0]>=0 else 3
        if pose!=row['pose']:
            row['pose']=pose;self.batch.modify(row['id'],'POSE',intval=pose,offset=self._offset())

    def _motion(self,pos,vel,size,dt,blockers):
        end=[pos[j]+vel[j]*dt for j in range(2)]
        hit=min((v for rect in blockers if (v:=sweep(pos,end,size,rect)) is not None),default=None)
        if hit is not None:
            end=[pos[j]+(end[j]-pos[j])*max(0.,hit-1e-5) for j in range(2)]
        return end,hit

    def _laser(self,pos,angle,damage,creator=0):
        rad=math.radians(angle);speed=self.pack.entities['laser']['speed_px_s']*256
        vel=[math.cos(rad)*speed,math.sin(rad)*speed]
        wire=[round(v*256/1000) for v in vel]
        vel=[v*1000/256 for v in wire]
        oid=self._create('hulk.plasmahazard',pos,creator,dict(damage=damage,facing=round(angle)%360,
            blocktype=0,lifetime=-1,boundsworld=False,
            move={'vel':dict(xvel=wire[0],yvel=wire[1])}))
        self.actors[oid]['expire']=None
        self.actors[oid]['vel']=vel;self.counts['lasers']+=1
        return oid

    def _boss_death(self,oid):
        row=self.actors.get(oid)
        if row is None or row.get('kind')=='boss_death':return
        rule=self.pack.entities['boss']
        duration_ms=int(rule['death_duration_ms'])
        row.update(kind='boss_death',vel=[0.,0.],next_sync=math.inf,
                   expire=self.room_time+duration_ms/1000.)
        row['props'].update(damage=0,hitboxdamage=0,blocktype=0,invulnerable=True)

        self.batch.move_to(oid,round(row['pos'][0]),round(row['pos'][1]),dt=0,offset=self._offset())
        self.batch.modify(oid,'DAMAGE',intval=0,offset=self._offset())
        self.batch.modify(oid,'HITBOXDAMAGE',intval=0,offset=self._offset())
        self.batch.modify(oid,'BLOCKTYPE',intval=0,offset=self._offset())
        self.batch.modify(oid,'INVULNERABLE',boolval=True,offset=self._offset())
        self.batch.modify(oid,'POSE',intval=1,offset=self._offset())
        self.batch.animprops(oid,int(rule['death_animid']),duration_ms,offset=self._offset())
        self.record('boss_dead',id=oid,position=list(row['pos']),animation=rule['death_animid'],duration_ms=duration_ms)

    def _action(self,e):
        kind,oid=e['kind'],e.get('id')
        if kind=='move':
            b=self.controller.bosses[oid]
            b['pos'],hit=self._motion(b['pos'],e['velocity'],[32768,32768],e['dt'],self._tick_solids)
            if hit is not None:b['vel']=[0.,0.]
            self.actors[oid]['pos']=list(b['pos']);self.actors[oid]['vel']=list(b['vel']);self._sync(self.actors[oid])
        elif kind=='trail':
            self._create('hulk.bossspawn',e['pos'],oid);self.counts['trails']+=1
        elif kind=='summon':
            self.batch.animprops(oid,1,1848,offset=self._offset())
            for helper in self.fans.pop(oid,[]):self._retire(helper)
            self.record(kind,id=oid,round=e['round'])
        elif kind=='mini':
            mid=self._create('hulk.miniseeker',e['pos'],oid)
            row=self.actors[mid];angle=math.radians(e['angle']);r=self.pack.entities['mini']
            row.update(kind='mini',launch_until=self.room_time+r['launch_s'],
                vel=[math.cos(angle)*r['launch_px_s']*256,math.sin(angle)*r['launch_px_s']*256],
                activated=False,target=None,cycle_origin=self.room_time,next_trail=self.room_time+r['trail_period_s'])
            self.counts['minis']+=1;self.record(kind,id=mid,boss=oid,index=e['index'])
        elif kind=='fan_prepare':
            center=[v+16384 for v in e['pos']];helpers=[]
            for angle in (e['angle']-45,e['angle'],e['angle']+45):
                rad=math.radians(angle);p=[center[j]+25600*(math.cos(rad) if j==0 else math.sin(rad))-4096 for j in range(2)]
                helper=self._create('hulk.bossemitpoint',p,oid,dict(facing=round(angle)%360))
                self.actors[helper]['angle']=angle;helpers.append(helper)
            self.fans[oid]=helpers;self.batch.sound(oid,'hulk.seekertelegraph',offset=self._offset())
            self.record(kind,id=oid)
        elif kind=='fan_release':
            for helper in self.fans.pop(oid,[]):
                row=self.actors.get(helper)
                if row:self._laser(row['pos'],row['angle'],50,oid);self._retire(helper)
            self.record(kind,id=oid)
        elif kind=='boss_dead':
            for helper in self.fans.pop(oid,[]):self._retire(helper)
            self._boss_death(oid)
        elif kind=='activate':self.record(kind,id=oid,player=e['player'])

    def _feedback(self,e):
        if not isinstance(e,dict) or ('ownerid' in e and int(e['ownerid'] or 0)!=self.worldid):return
        oid=int(e.get('victim') or 0);kind=e.get('kind')
        if oid in self.retired:return
        if kind=='damage':
            value=int(e.get('value') or 0)
            if value<=0:return
            self.counts['native_damage']+=1
            if self.controller and oid in self.controller.bosses:
                player=e.get('player_id',next(iter(self.players),None))
                for action in self.controller.add_damage(value,oid,player):self._action(action)
                return
            row=self.traps.get(oid) or self.actors.get(oid)
            if row is not None and row.get('hp') is not None:
                row['hp']-=value
                if row['hp']<=0:
                    if oid in self.traps:self.traps.pop(oid)
                    self._retire(oid,'HITPOINTS')
                else:
                    row.get('props',self.objects.get(oid,{}))['hitpoints']=row['hp']
                    self.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self._offset())
        elif kind=='destroy':
            self.counts['native_destroy']+=1

            if self.controller and oid in self.controller.bosses:return
            row=self.actors.get(oid)
            if row and row['vid']=='hulk.plasmahazard' and int(e.get('reason') or 0) in (2,5,6):

                heading=math.degrees(math.atan2(row['vel'][1],row['vel'][0]))
                self._laser(row['pos'],heading,row['props']['damage'])
                self._retire(oid,native=True)
                return
            if oid in self.traps:self.traps.pop(oid)
            if oid in self.actors or oid in self.objects:self._retire(oid,int(e.get('reason') or 1),native=True)

    def _step_traps(self):
        r=self.pack.entities['traps']
        for oid,tr in self.traps.items():
            if self.room_time<tr['next_shot']:continue
            tr['next_shot']=self.room_time+(r['period_s'] if tr['side'] in ('top','bottom') else r['side_period_s'])
            x,y,w,h=tr['body_bounds_mp']
            for angle in tr['directions']:
                pos={0:[x+w,y+(h-8192)/2],180:[x-8192,y+(h-8192)/2],
                    90:[x+(w-8192)/2,y+h],270:[x+(w-8192)/2,y-8192]}[angle]
                self._laser(pos,angle,40,oid)
            self.record('trap_fire',id=oid,directions=list(tr['directions']))

    def _step_actors(self,dt):
        r=self.pack.entities['mini']
        for oid,row in list(self.actors.items()):
            if row.get('kind')=='boss_death':
                if self.room_time+1e-9>=row['expire']:

                    self._retire(oid,-1)
                    self.record('boss_death_finish',id=oid)
                continue
            if row['expire'] is not None and self.room_time>=row['expire']:
                self._retire(oid,'LIFETIME');continue
            if row.get('kind')=='mini':
                center=[v+8192 for v in row['pos']]
                target=min(self.players,key=lambda k:math.dist(self.players[k]['center'],center),default=None)
                if self.room_time>=row['launch_until']:
                    if target is not None and not row['activated'] and math.dist(self.players[target]['center'],center)<=r['activation_px']*256:
                        row.update(activated=True,cycle_origin=self.room_time)
                    row['target']=target;row['vel']=[0.,0.]
                    if row['activated'] and target is not None:
                        cycle=(self.room_time-row['cycle_origin'])%r['motion_period_s']
                        speed=0 if cycle<r['pause_s'] else r['fast_px_s'] if cycle<r['pause_s']+r['dash_s'] else r['slow_px_s']
                        dx,dy=(self.players[target]['center'][j]-center[j] for j in range(2));length=math.hypot(dx,dy)
                        if length:row['vel']=[speed*256*dx/length,speed*256*dy/length]
                row['pos'],hit=self._motion(row['pos'],row['vel'],[16384,16384],dt,self._tick_solids)
                if hit is not None:row['vel']=[0.,0.]
                if row['activated'] and self.room_time>=row['next_trail']:
                    row['next_trail']=self.room_time+r['trail_period_s'];self._create('hulk.minispawn',row['pos'],oid)
                self._sync(row)
            elif row['vid']=='hulk.plasmahazard':
                row['pos']=[row['pos'][j]+row['vel'][j]*dt for j in range(2)]

    def _victory(self):
        self.phase='done'

        exit_id=self.pack.scene['exit_id']
        entry_id=self.pack.scene['entry_id']
        self.batch.modify(entry_id,'DRAW',boolval=True,offset=self._offset())
        self.batch.modify(entry_id,'INTERACT',intval=1,offset=self._offset())
        self.batch.modify(exit_id,'VID',stringval='hulk.switch3',offset=self._offset())
        self.batch.modify(exit_id,'INTERACT',intval=1,offset=self._offset())
        known=getattr(self.server,'item_types',None)
        pool=[t for t in (135,131,272,254,149,174,50) if known is None or t in known]
        types=self.rng.sample(pool,min(3,len(pool)))
        if types:self.batch.extra['createloot_']=dict(props_=dict(zip(('xmp_','ymp_'),self.pack.scene['reward_mp'])),itemprops_=[{'type_':t} for t in types])
        self.record('victory',reward_types=types)

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        self._frame_start=self.room_time
        state,_=self.bridge.snapshot()
        self.player=state
        if self.players_provider:self.players=self.players_provider() if callable(self.players_provider) else self.players_provider
        elif state and state.alive and state.age()<2.:
            self.players={(state.worldid,state.objid):dict(center=state.center(),alive=True)}
        else:self.players={}
        if not self.bootstrapped and self.players:
            self.controller=HulkMidController(self.pack,self.seed);self.phase='fighting';self.bootstrapped=True
            for b in self.controller.bosses.values():
                p=self.pack.props('hulk.seeker');p.update(xmp=round(b['pos'][0]),ymp=round(b['pos'][1]))
                self.batch.create(b['id'],normalize('Properties',p,self.layouts))
                self.actors[b['id']]=dict(id=b['id'],vid='hulk.seeker',pos=list(b['pos']),vel=[0.,0.],props=p,expire=None,next_sync=0.,pose=None)
            self.record('fight_start')
        if self.controller:self.controller.set_players(self.players)
        for e in self.bridge.poll_events():self._feedback(e)
        if self.controller and self.room_time==0.:self._step_traps()
        end=self.room_time+dt if self.controller else self.room_time
        while self.room_time<end-1e-9:
            step=min(1/self.HZ,end-self.room_time);self.room_time+=step

            self._tick_solids=self.fixed_solids+[tuple(self.objects[i].get(k,0) for k in ('xmp','ymp','wmp','hmp')) for i in self.traps if self.objects[i].get('terraintype')==1]
            for e in self.controller.step(step):self._action(e)
            if self.phase!='done':self._step_traps()
            self._step_actors(step)
            if self.controller.state=='done' and self.phase!='done':self._victory()
        self._wire_ms+=dt*1000;ms=round(self._wire_ms)
        self.batch.dt=ms-self._sent_ms;self._sent_ms=ms
        self.server._send_raw(self.channel,12,self.batch.encode(),'hulk-mid-live',record=False)
        self.batch.clear();self.frames+=1
        self.supplies.flush()

    def status(self):
        return dict(map='hulk_mid',phase=self.phase,time=self.room_time,seed=self.seed,
            controller=self.controller.status() if self.controller else None,
            actors=len(self.actors),traps=len(self.traps),counts=self.counts.copy(),frames=self.frames)
