'EFF ordinary species and native projectiles, independent of Boss arenas.'
import copy
import math
from collections import Counter,deque
from types import SimpleNamespace
from ..fireforest.controller import ForestController
from ..sc_boss.motion import NativeMotion
from .physics import ground,terrain_contact,terrain_hit
from .gftseed_profiles import YACCEL as GFT_YACCEL,MAXYVEL as GFT_MAXYVEL,MUZZLES_LEFT,MUZZLES_RIGHT,VELOCITIES_BY_PHASE

SPECIES={'forest.goblinsworder':'sword','forest.gsshield':'shield','forest.goblinthrower':'throw',
 'forest.goblinfirethrower':'firethrow','forest.goblinhopling':'hop','forest.bombcrawler':'crawler',
 'forest.bombdropper':'dropper','forest.largeplant':'plant','forest.goblinhive':'hive',
 'forest.jumper':'jumper','forest.miner':'miner','forest.gobling':'bug','forest.yjball':'frog'}
PROJECTILES={'forest.bcbomb','forest.bdbomb','forest.gtproj','forest.gftseed','forest.lpseed','forest.lpflame','forest.minerrock'}
EFFECTS={'forest.bcbombx','forest.bdbombx','forest.gftfire','forest.gmseedx','forest.firebombx'}

MINER_VELOCITIES=((90000,-97000),(124000,-61000),(107000,-104000),(85000,-94000),
 (114000,-74000),(130000,-72000),(95000,-83000),(83000,-128000),
 (86000,-103000),(135000,-63000),(88000,-126000),(104000,-91000),
 (78000,-95000),(105000,-65000),(80000,-133000),(86000,-106000))

def _seed_departing_side(row,start,end,size,hit):

    if hit['fraction']!=0 or hit['normals']:return False
    x=row['pos'][0];w=row['props'].get('wmp',0)
    return (start[0]+size[0]==x and end[0]<start[0]) or (start[0]==x+w and end[0]>start[0])

class _Fauna(ForestController):
    """Measured existing Forest bug/frog capability with this room's terrain."""
    def __init__(self,owner):
        self.owner=owner;self.time=owner.host.room_time;self.actors={};self.retired=set();self.kills=0;self.events=[]
        source=owner.host.pack.encounter_pack('mid').entities
        rows=list(owner.host.actors.values());xs=[r['pos'][0] for r in rows];ys=[r['pos'][1]+r['props'].get('hmp',0) for r in rows]
        self.pack=SimpleNamespace(entities=copy.deepcopy(source),solids=[],scene=dict(left=min(xs)/256,right=max(xs)/256+1024,floor=max(ys)/256))
        self.players={};self.platform_boxes=[]
    def emit(self,kind,**values):
        host=self.owner.host;oid=values.get('id')
        if kind=='props':host.batch.props(oid,values['values'],offset=host._offset())
        elif kind=='sound':host.presentation.sound(oid,values['name'],self.time)
        elif kind=='remove':self.owner._retire(values['actor']['id'],values['reason'])
    def ground(self,a,dt):

        row=self.owner.host.actors[a['id']];state=self.owner.states[a['id']];was=a['grounded']
        row['pos']=[v*256 for v in a['pos']]
        state.update(vy=a['vel'][1]*256,grounded=a['grounded'],gravity=a['gravity']*256)
        ground(self.owner.host,row,state,dt,a['vel'][0]*256)
        a['pos']=[v/256 for v in row['pos']];a['vel'][1]=state['vy']/256;a['grounded']=state['grounded']
        if a['kind']=='frog' and a['grounded'] and not was:
            a['next_jump']=self.time+self.pack.entities['npc']['frog_rest_s'];self.pose(a,1)
    def step(self,dt):
        host=self.owner.host;self.time=host.room_time;self.players={k:[v/256 for v in point] for k,point in self.owner._players()}
        self.pack.solids=[tuple(v/256 for v in rect[:4]) for rect in host.surfaces() if rect[4]==1 and rect[2]>0 and rect[3]>0]
        self.platform_boxes=[(oid,row['pos'][0]/256,row['pos'][1]/256,row['props']['wmp']/256,row['props']['hmp']/256,
            row.get('_last_rider_y',row['pos'][1])/256) for oid,row in host.actors.items() if row.get('_eff_rock') and row['props'].get('terraintype')==2]
        all_actors=self.actors
        self.actors={oid:a for oid,a in all_actors.items() if self.owner.states.get(oid,{}).get('awake')}
        self.actors_step(dt)
        self.actors={oid:a for oid,a in all_actors.items() if oid in host.actors}
        for oid,a in list(self.actors.items()):
            row=host.actors.get(oid)
            if not row:self.actors.pop(oid,None);continue
            row['pos']=[v*256 for v in a['pos']];row['vel']=[v*256 for v in a['vel']]
            row['props'].update(facing=a['facing'],pose=a['pose'])

class ExplorationEnemies:
    def __init__(self,host):
        self.host=host;self.owned=set();self.states={};self.shots={};self.effects=set();self.pending=[]
        self.counts=Counter();self.retired=set();self.seen=set();self.order=deque();self.event_refs=deque(maxlen=4096)
        self.fauna=None
    def prepare(self):
        self.fauna=_Fauna(self)
        for oid in list(self.host.actors):self.add_actor(oid)
        self.host.record('eff_enemy_policy',evidence='FIT native asymmetric walk/run; SIM wake1800/acquire/routing/cadences/caps; original Properties retained')
    def _players(self):
        supplied=getattr(self.host,'players',{})
        for key,p in supplied.items():
            if isinstance(p,dict):
                if p.get('alive',True):yield key,p['center']
            elif getattr(p,'alive',True):yield key,p.center()
        if not supplied and getattr(self.host,'player',None) and self.host.player.alive:yield 0,self.host.player.center()
    def target(self,row,maximum=math.inf):
        w,h=row['props'].get('wmp',0),row['props'].get('hmp',0);center=(row['pos'][0]+w/2,row['pos'][1]+h/2)
        values=[(math.dist(center,p),p) for _,p in self._players()]
        if not values:return None
        distance,point=min(values,key=lambda v:v[0]);return point if distance<=maximum else None
    def add_actor(self,oid):
        row=self.host.actors.get(oid)
        if row is None or oid in self.owned:return
        vid=row['vid']
        if vid in PROJECTILES:
            self.owned.add(oid);row['_eff_driven']=True
            model=NativeMotion(*row['pos']);model.move=copy.deepcopy(row['props'].get('move',{}));model.at=row.get('born',self.host.room_time)
            self.shots[oid]=dict(model=model,vid=vid,opened=False,pose_started=False);return
        if vid in EFFECTS:self.owned.add(oid);self.effects.add(oid);return
        species=SPECIES.get(vid)
        if not species:return
        self.owned.add(oid);row['_eff_driven']=True
        self.states[oid]=dict(species=species,dir=-1 if row['props'].get('facing',0)==180 else 1,
            awake=False,next=self.host.room_time+.2,grounded=False,vy=0.,home=tuple(row['pos']),
            leg_until=0.,pending_jump=None,children=set())
        if species in ('bug','frog','jumper'):
            kind='frog' if species=='jumper' else species
            self.fauna.actors[oid]=dict(id=oid,kind=kind,vid=vid,pos=[v/256 for v in row['pos']],
                size=[row['props']['wmp']/256,row['props']['hmp']/256],vel=[0.,0.],hp=row['hp'],born=self.host.room_time,
                expiry=math.inf,grounded=False,facing=row['props'].get('facing',0),pose=row['props'].get('pose',1),
                next_jump=0.,gravity=self.fauna.pack.entities['npc']['frog_gravity_px_s2'] if kind=='frog' else self.fauna.pack.entities['npc']['gravity_px_s2'],
                children=[],patrol_sign=self.states[oid]['dir'],owner=0)
        if species=='shield' and not any((r['props'].get('move') or {}).get('relativeto',{}).get('objid')==oid for r in self.host.actors.values()):
            right=self.states[oid]['dir']>0
            child=self.host._create('',row['pos'],creator=oid,changes=dict(move=dict(relativeto=dict(objid=oid),to=dict(xmp=15104 if right else -6912,ymp=0 if right else 1024))))
            if getattr(self.host,'_in_step',False) and row.get('_wire_born_ms')==getattr(self.host,'wire_end_ms',-1):self.host.created_at_end(child)
    def _face(self,row,state,direction):
        state['dir']=direction;facing=0 if direction>0 else 180
        if row['props'].get('facing',0)==facing:return
        row['props']['facing']=facing;self.host.batch.modify(row['id'],'FACING',intval=facing,offset=self.host._offset())
        if state['species']=='shield':
            for oid,child in self.host.actors.items():
                if (child['props'].get('move') or {}).get('relativeto',{}).get('objid')!=row['id']:continue
                move=dict(relativeto=dict(objid=row['id']),to=dict(xmp=15104 if direction>0 else -6912,ymp=0 if direction>0 else 1024))
                child['props']['move']=move;self.host.batch.modify(oid,'MOVE',message=('Properties_Move',self.host.normalize('Properties_Move',move)),offset=self.host._offset())
    def _pose(self,row,value):
        if row['props'].get('pose',0)!=value:
            row['props']['pose']=value;self.host.batch.modify(row['id'],'POSE',intval=value,offset=self.host._offset())
    def _schedule(self,row,delay,kind,**values):self.pending.append(dict(at=self.host.room_time+delay,parent=row['id'],kind=kind,**values))
    def _shot(self,parent,vid,vel,point=None,changes=None):
        p=parent['props'];point=point or (parent['pos'][0]+p.get('wmp',0)/2,parent['pos'][1]+p.get('hmp',0)/2)
        changes=copy.deepcopy(changes or {})
        if vid!='forest.gftseed':changes['move']=dict(vel=copy.deepcopy(vel))
        if vid in ('forest.bdbomb','forest.lpseed','forest.minerrock'):changes.setdefault('lifetime',12000 if vid=='forest.minerrock' else 6000)
        oid=self.host._create(vid,point,creator=parent['id'],changes=changes);self.add_actor(oid);self.counts['projectiles']+=1
        if vid=='forest.gftseed':

            row=self.host.actors[oid]

            row['props'].pop('facing',None)
            self.host._pending_creates[oid]['create_']['props_'].pop('facing_',None)
            if vel.get('xvel',0)<0:
                row['props']['facing']=180;self.host.batch.modify(oid,'FACING',intval=180,offset=self.host._offset())
            self.host.created_at_end(oid);shot=self.shots[oid]
            shot['model'].at=self.host.actors[oid]['born'];shot['model'].move=dict(vel=copy.deepcopy(vel))
        return oid
    def _emit(self,event):
        parent=self.host.actors.get(event['parent'])
        if not parent or parent['id'] in self.retired:return
        state=self.states[parent['id']];direction=state['dir'];kind=event['kind']
        if kind=='throw':self._shot(parent,'forest.gtproj',dict(xvel=direction*10000,yvel=0))
        elif kind=='firethrow':
            phase=int(event.get('phase',0));vx,vy=self.host.rng.choice(VELOCITIES_BY_PHASE[phase])

            vx=vx if direction>0 else -vx-256
            dx,dy=(MUZZLES_RIGHT if direction>0 else MUZZLES_LEFT)[phase]
            self._shot(parent,'forest.gftseed',dict(xvel=vx,yvel=vy,yaccel=GFT_YACCEL,maxyvel=GFT_MAXYVEL),
                (parent['pos'][0]+dx,parent['pos'][1]+dy))
        elif kind=='drop':
            target=self.target(parent);point=(parent['pos'][0]+parent['props']['wmp']/2,parent['pos'][1]+parent['props']['hmp'])
            dx,dy=(target[0]-point[0],max(1,target[1]-point[1])) if target else (0,1)
            distance=math.hypot(dx,dy);self._shot(parent,'forest.bdbomb',dict(xvel=round(dx/distance*30000),yvel=round(dy/distance*30000)),point)
        elif kind=='hive':
            live=[oid for oid in state['children'] if oid in self.host.actors]
            if len(live)>=6:return
            vid=self.host.rng.choice(('forest.goblinsworder','forest.goblinthrower','forest.goblinfirethrower','forest.goblinhopling'))
            p=self.host.pack.props(vid);x=parent['pos'][0]+parent['props']['wmp']/2-p['wmp']/2;y=parent['pos'][1]+parent['props']['hmp']-p['hmp']
            oid=self.host._create(vid,(x,y),creator=parent['id']);self.host.created_at_end(oid);self.add_actor(oid);state['children'].add(oid)
            self.host.presentation.sound(oid,'forest.goblinhivespawn',oid);self.counts['hive_births']+=1
    def _impact(self,oid,reason='BLOCKED',effect_point=None):
        shot=self.shots.pop(oid,None);row=self.host.actors.get(oid)
        if not shot or row is None:return
        vid=shot['vid'];center=[row['pos'][j]+row['props'].get(('wmp','hmp')[j],0)/2 for j in (0,1)]
        effect={'forest.bcbomb':'forest.bcbombx','forest.bdbomb':'forest.bdbombx','forest.gftseed':'forest.gftfire'}.get(vid)
        if effect:
            p=self.host.pack.props(effect);child=self.host._create(effect,effect_point or [center[0]-p['wmp']/2,center[1]-p['hmp']],creator=oid)
            self.host.created_at_end(child);self.owned.add(child);self.effects.add(child)
        elif vid=='forest.lpseed':
            for sign in (-1,1):
                child=self._shot(row,'forest.lpflame',dict(xvel=sign*14848,yvel=-3932,yaccel=72),center)
                self.host.created_at_end(child)
                self.shots[child]['model'].at=self.host.actors[child].get('born',self.host.room_time)
            self.host.presentation.sound(oid,'forest.lpseedsplit',oid)
        self._retire(oid,reason,drop=False)
    def _retire(self,oid,reason,drop=False):
        if oid in self.retired:return
        row=self.host.actors.get(oid);self.retired.add(oid);self.states.pop(oid,None);self.shots.pop(oid,None);self.effects.discard(oid)
        if self.fauna:self.fauna.actors.pop(oid,None)
        if row is not None and drop:
            self.host.supplies.killed(row['pos'][0]+row['props'].get('wmp',0)/2,row['pos'][1]+row['props'].get('hmp',0))
        children=[key for key,r in self.host.actors.items() if not r['vid'] and (r['props'].get('move') or {}).get('relativeto',{}).get('objid')==oid]
        for key in children:self.host._retire(key,'INTERNAL')
        self.host._retire(oid,reason);self.counts['retired']+=1
    def step(self,dt):
        at=self.host.room_time
        mechanics=getattr(self.host,'mechanics',None)
        if mechanics:mechanics.advance_movers(dt)
        for oid,state in list(self.states.items()):
            row=self.host.actors.get(oid)
            if row is None:self.states.pop(oid,None);continue
            species=state['species'];target=self.target(row,1800*256)
            if target is not None:state['awake']=True
            if not state['awake'] or species in ('bug','frog','jumper'):continue
            nearby=self.target(row,760*256);direction=1 if nearby and nearby[0]>row['pos'][0]+row['props']['wmp']/2 else -1
            if nearby and species in ('sword','shield'):self._face(row,state,direction)
            vx=state['dir']*(19000 if state['dir']>0 else 20000)
            if species in ('sword','shield') and nearby:vx=97000 if state['dir']>0 else -98000
            if species=='crawler':vx=39000 if state['dir']>0 else -40000
            if species=='hop':vx=70000 if state['dir']>0 else -71000
            if species=='miner':

                vx=117000 if state['dir']>0 else -118000
                if at<state.get('mining_until',0.):vx=0
            if species in ('plant','hive'):vx=0
            if species=='dropper':
                if at>=state['leg_until']:
                    state['leg_until']=at+2.;point=nearby or state['home'];dx=point[0]-row['pos'][0];dy=point[1]-180*256-row['pos'][1];distance=max(1,math.hypot(dx,dy))
                    state['fly_vel']=(dx/distance*58000,dy/distance*58000)
                row['vel']=list(state['fly_vel']);row['pos']=[row['pos'][i]+row['vel'][i]*dt for i in (0,1)]
            elif species not in ('plant','hive'):
                if state['pending_jump'] is not None and at>=state['pending_jump']:
                    state['vy']=-150000 if species=='hop' else -99840;state['grounded']=False;state['pending_jump']=None
                blocked=ground(self.host,row,state,dt,vx,avoid_edges=species in ('throw','firethrow','crawler','miner'))
                if blocked or state.get('edge'):self._face(row,state,-state['dir'])
                mining=species=='miner' and at<state.get('mining_until',0.)
                self._pose(row,3 if not state['grounded'] and state['vy']<0 else 1 if not state['grounded'] else 4 if mining else 5 if nearby else 4)
            if nearby and at>=state['next']:
                if species in ('throw','firethrow'):
                    self._face(row,state,direction);self.host.presentation.action(oid,species,at)
                    for phase,delay in enumerate((.396,.594,.759)):self._schedule(row,delay,species,phase=phase)
                    state['next']=at+2.3
                elif species=='crawler':

                    self._shot(row,'forest.bcbomb',dict(xvel=state['dir']*20784,yvel=-12000,yaccel=40));state['next']=at+.71
                elif species=='dropper':
                    self.host.presentation.action(oid,'drop',at)

                    for delay in (.1,.88,1.295):self._schedule(row,delay,'drop')
                    state['next']=at+2.4
                elif species=='plant':
                    vxseed=self.host.rng.randint(-5000,5000)
                    self._shot(row,'forest.lpseed',dict(xvel=vxseed,yvel=-round(math.sqrt(30000**2-vxseed**2)),yaccel=30),
                        (row['pos'][0]+row['props']['wmp']/2-4096,row['pos'][1]-4096));state['next']=at+1.05
                elif species=='hive':
                    self.host.presentation.action(oid,'hive',at);self._schedule(row,1.2,'hive');state['next']=at+3.2
                elif species in ('hop','jumper') and state['grounded']:
                    self._face(row,state,direction);state['pending_jump']=at+(.528 if species=='hop' else 0)
                    if species=='hop':self.host.presentation.action(oid,'hop',at)
                    state['next']=at+2.4
                elif species=='miner':
                    self.host.presentation.action(oid,'miner',at)

                    for xvel,yvel in self.host.rng.sample(MINER_VELOCITIES,4):
                        self._shot(row,'forest.minerrock',dict(xvel=round(-state['dir']*xvel*256/1000),
                            yvel=round(yvel*256/1000),yaccel=round(340000*256/1000000)),row['pos'])

                    state['mining_until']=at+.6;state['next']=at+.515
            if species not in ('plant','hive'):
                self.host.batch.move_to(oid,round(row['pos'][0]),round(row['pos'][1]),dt=max(1,round(dt*1000)),offset=self.host._offset())
        self.fauna.step(dt)
        for oid in list(self.fauna.actors):
            row=self.host.actors.get(oid)
            if row:self.host.batch.move_to(oid,round(row['pos'][0]),round(row['pos'][1]),dt=max(1,round(dt*1000)),offset=self.host._offset())
        due=[event for event in self.pending if event['at']<=at];self.pending=[event for event in self.pending if event['at']>at]
        for event in due:self._emit(event)
        for oid,shot in list(self.shots.items()):
            row=self.host.actors.get(oid)
            if row is None:self.shots.pop(oid,None);continue
            if self.host.wire_lifetime_due(oid):self.expire(oid);continue
            native_at=getattr(self.host,'wire_end_ms',round(at*1000))/1000.
            if shot['vid']=='forest.gftseed' and native_at<=row['born']:continue
            end=list(shot['model'].position(native_at));old=row['pos'];size=(row['props']['wmp'],row['props']['hmp'])
            solids=[r for key,r in self.host.actors.items() if key!=oid and key not in self.shots and r['props'].get('terraintype')==1]
            details=[(terrain_hit(r,old,end,size),r) for r in solids] if shot['vid']=='forest.gftseed' else []
            details=[(h,r) for h,r in details if h is not None and not _seed_departing_side(r,old,end,size,h)]
            detail=min(((h,r) for h,r in details if h is not None),key=lambda pair:pair[0]['fraction'],default=None)
            contact=(detail[0]['fraction'] if detail else None) if shot['vid']=='forest.gftseed' else min(
                (hit for r in solids if (hit:=terrain_contact(r,old,end,size)) is not None),default=None)
            row['pos']=end;row['vel']=[(end[i]-old[i])/dt if dt else 0. for i in (0,1)]
            if shot['vid']=='forest.gftseed':
                if not shot['pose_started']:self._pose(row,3);shot['pose_started']=True
                if detail and detail[0]['axes']==('x',) and detail[0]['normals']:
                    fraction=detail[0]['fraction'];v=shot['model'].move['vel'];ms=max(0,(native_at-shot['model'].at)*1000)
                    velocity=min(v.get('maxyvel',math.inf),v.get('yvel',0)+v.get('yaccel',0)*ms)
                    x=old[0]+(end[0]-old[0])*fraction;row['pos']=[x,end[1]]
                    reflected=-v.get('xvel',0)-256
                    shot['model'].origin=tuple(row['pos']);shot['model'].at=native_at
                    shot['model'].move=dict(vel=dict(xvel=reflected,yvel=velocity,yaccel=GFT_YACCEL,maxyvel=GFT_MAXYVEL))
                    facing=0 if reflected>0 else 180;row['props']['facing']=facing
                    self.host.batch.modify(oid,'FACING',intval=facing,offset=self.host._offset());self.counts['gftseed_side_bounces']+=1
                    contact=None
                if contact is None:
                    self.host.batch.move_to(oid,*map(round,row['pos']),dt=max(1,round(dt*1000)),offset=self.host._offset())
                    continue
            if shot['vid']=='forest.lpseed' and not shot['opened'] and at-row.get('born',at)>=.8:
                self.host.presentation.action(oid,'seed_open',oid);shot['opened']=True
            if contact is not None:
                row['pos']=[old[i]+(end[i]-old[i])*max(0,contact-.001) for i in (0,1)]
                if shot['vid']=='forest.minerrock':
                    self.shots.pop(oid,None);row['props'].pop('move',None);self.host.batch.modify(oid,'MOVE',message=('Properties_Move',{}),offset=self.host._offset())
                    self.host.batch.move_to(oid,*map(round,row['pos']),dt=1,offset=self.host._offset())
                elif shot['vid']=='forest.gftseed' and detail[0]['axes']==('y',) and detail[0]['normals']==((0.,-1.),):

                    fire=self.host.pack.props('forest.gftfire');floor=detail[1]['pos'][1]
                    self._impact(oid,effect_point=[math.floor(end[0]/256)*256+(size[0]-fire['wmp'])/2,floor-fire['hmp']])
                else:self._impact(oid)
    def expire(self,oid):
        if oid not in self.owned:return False
        if oid in self.shots and self.shots[oid]['vid']=='forest.bcbomb':self._impact(oid,'LIFETIME')
        else:self._retire(oid,'LIFETIME')
        return True
    def feedback(self,event):
        if not isinstance(event,dict) or event.get('kind') not in ('damage','destroy'):return False
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.host.worldid:return False
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        if oid in self.retired:return True
        token=next(((k,event[k]) for k in ('event_id','seq','native_t','source_t_ms') if k in event),None)
        key=(str(token),oid,event['kind'],event.get('value'),event.get('reason')) if token else ('object',id(event))
        if key in self.seen:return True
        self.seen.add(key);self.order.append(key);self.event_refs.append(event)
        if len(self.order)>4096:self.seen.discard(self.order.popleft())
        row=self.host.actors.get(oid)
        if row is None:return True
        if row['props'].get('invulnerable'):return True
        reason=event.get('reason',2)
        if event['kind']=='damage':
            value=int(event.get('value',event.get('val',0)) or 0)
            if value<=0 or row['hp'] is None:return True
            row['hp']=max(0,row['hp']-value);row['props']['hitpoints']=row['hp']
            self.host.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self.host._offset())
            if row['hp']>0:return True
            reason=2
        if oid in self.shots:
            if self.shots[oid]['vid']=='forest.gftseed' and reason in (6,'LIFETIME'):self._retire(oid,reason)
            elif reason in (5,6,'BLOCKED','LIFETIME'):self._impact(oid,reason)
            else:self._retire(oid,reason)
        else:self._retire(oid,reason,drop=oid in self.states and (reason==2 or str(reason).upper()=='HITPOINTS'))
        return True
    def interact(self,oid,player):return False
    def status(self):return dict(species=dict(Counter(s['species'] for s in self.states.values())),projectiles=len(self.shots),
        counts=dict(self.counts),evidence='D templates/slots/sounds and native MOVE; FIT asymmetric speeds; SIM perception/navigation/cooldowns/launch selection')
