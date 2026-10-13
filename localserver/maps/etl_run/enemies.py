'Live ETL exploration species; native capabilities and explicit SIM gates.'
import copy
import math
from collections import Counter,deque
from types import SimpleNamespace

from ..lab_hard.session import LabHardLiveSession
from ..etl_motion import NativeMotion
from .abilities import (NATIVE,Ownership,Projectiles,body,body_center,center,players,
    nearest,terrain,solid_rows,swept,visible,move,set_prop,follow)
from .presentation import projectile_top_left

FAMILIES={
    'lab.seeker':'seeker','lab.charger':'charger','lab.swoopingmold':'mold',
    'lab.rovingturretbase':'rover','lab.rovingturret':'mounted',
    'lab.epturret':'pulse','lab.epturretup':'pulse','lab.epturretdown':'pulse',
    'lab.shambler':'shambler','lab.stonehead':'stone','lab.tumble':'tumble',
    'lab.vcritspot':'vine','lab.eatercritspot':'eater','lab.flowerdropper':'flower',
    'lab.plantsnake':'snake','lab.clingvineup':'cling','lab.clingvinedown':'cling',
}
COMPONENTS={'lab.cshield','lab.stonenose','lab.vflapup','lab.vflapdown','lab.dflapup','lab.dflapdown',
            'lab.eaterbase','lab.eatersegments','lab.eaterleavesup','lab.eaterleavesdown','lab.brambleshield'}
GROUND_SPEED={'charger':68000.,'rover':39780.,'shambler':19000.,'stone':29160.,'tumble':78000.}

POLICIES={
    'seeker':dict(wake=1200,acquire=900,attack=700,period=2.355),
    'charger':dict(wake=1200,acquire=800,attack=120,period=2.4),
    'mold':dict(wake=1200,acquire=700,attack=250,period=2.4),
    'rover':dict(wake=1200,acquire=900,attack=700,period=4.6),
    'mounted':dict(wake=1200,acquire=900,attack=700,period=4.6),
    'pulse':dict(wake=1000,acquire=850,attack=850,period=6.6),
    'shambler':dict(wake=1100,acquire=750,attack=100,period=2.2),
    'stone':dict(wake=1100,acquire=700,attack=120,period=3.),
    'tumble':dict(wake=1100,acquire=800,attack=600,period=6.),
    'vine':dict(wake=1200,acquire=900,attack=600,period=4.),
    'eater':dict(wake=1200,acquire=1000,attack=900,period=3.2),
    'flower':dict(wake=1000,acquire=800,attack=600,period=4.),
    'snake':dict(wake=1000,acquire=700,attack=250,period=4.),
    'cling':dict(wake=1000,acquire=600,attack=250,period=8.),
}

class _SeekerBatch:
    def __init__(self,controller):self.controller=controller
    def move_to(self,*args,**kwargs):pass
    def animprops(self,oid,anim,time_ms):self.controller.attack(oid,anim,time_ms)

class _SeekerAbility:
    _step=LabHardLiveSession._step_seekers
    _push_seeker_out_of_walls=LabHardLiveSession._push_seeker_out_of_walls
    SEEKER_SPED=LabHardLiveSession.SEEKER_SPED
    SEEKER_MOVE_STEP=LabHardLiveSession.SEEKER_MOVE_STEP
    SEEKER_MOVE_MS=LabHardLiveSession.SEEKER_MOVE_MS

    def __init__(self,controller):
        self.controller=controller;self.seekers={};self.batch=_SeekerBatch(controller)
        self.pack=SimpleNamespace(play_bounds=(-10**9,-10**9,10**9,10**9),
            seeker_policy=dict(fit=NATIVE['seeker_policy'],fire_recoil=NATIVE['seeker_recoil']))
        self.room_time=0.

    def combat_players(self):
        result=[]
        for index,p in enumerate(players(self.controller.host)):
            point=center(p)
            result.append(SimpleNamespace(alive=True,center=lambda point=point:point,identity=index,
                worldid=2,objid=index+1,raw={},hp=100,maxhp=100))
        return result

    def _lagged_player_center(self,delay,player):
        history=self.controller.history
        prior=next((row for row in reversed(history) if row[0]<=self.room_time-delay),None)
        return prior[1].get(player.identity) if prior else player.center()

    def _terrain_groups(self):
        rows=solid_rows(self.controller.host)
        if rows is not getattr(self,'_solid_rows',None):
            self._solid_rows=rows
            self._solid_boxes=[(oid,(x,y,x+w,y+h)) for oid,(x,y,w,h) in rows]
        return [],self._solid_boxes

    def _event(self,kind,**values):self.controller.host.record('etl_'+kind,evidence='lab_hard native ability FIT; exploration gates SIM',**values)

    def _spawn_seeker_bullet(self,oid,seeker,px,py):

        self.controller.pending.append(dict(at=self.room_time,owner=oid,vid='lab.sbullet',
            target=(px,py),speed=LabHardLiveSession.SBULLET_SPED))

class ExplorationEnemies(Ownership):
    def __init__(self,host):
        super().__init__(host,'_etl_enemy');self.units={};self.components={};self.history=deque(maxlen=120)
        self.projectiles=Projectiles(host);self.pending=[];self.counts=Counter();self.now=host.room_time
        self.seeker=_SeekerAbility(self);self.ground=None;self.component_deaths={}

    def _parent(self,oid):
        row=self.host.actors[oid];move_=row['props'].get('move') or {}
        return move_.get('relativeto',{}).get('objid',row.get('creator',getattr(self.host,'recipe',{}).get('creators',{}).get(oid)))

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        for oid in self.components:
            parent=self._parent(oid);row=self.host.actors[oid]
            if parent in self.host.actors:
                row['_etl_component_parent']=parent
                row['_etl_component_offset']=[row['pos'][i]-self.host.actors[parent]['pos'][i] for i in (0,1)]
        self.host.record('etl_enemy_policies',evidence='SIM activation/acquisition/recovery decisions; D native actions/props; FIT native ability speeds',families=sorted(set(FAMILIES.values())))

    def add_actor(self,oid):
        row=self.host.actors.get(oid)
        if not row or row.get('dead') or oid in self.retired:return False
        if oid in self.owned:return True
        vid=row['vid']
        if vid in COMPONENTS:
            self.own(oid);self.components[oid]=vid;return True
        if vid not in FAMILIES:return False
        family=FAMILIES[vid];b=body(row);direction=-1 if row['props'].get('facing')==180 else 1
        self.own(oid);row.pop('_etl_mechanic',None)
        self.units[oid]=dict(vid=vid,family=family,body=b,home=tuple(row['pos']),active=False,
            direction=direction,next_attack=self.now+.8,busy=0.,recovery=0.,phase='dormant',
            phase_end=0.,velocity=(0.,0.),jump_after=self.now+.8,track=NativeMotion(row['pos']),leg=0)
        if family=='seeker':
            self.seeker.seekers[oid]=dict(x=b['x'],y=b['y'],wmp=b['w'],hmp=b['h'],next_move=self.now,
                next_shot=self.now+LabHardLiveSession.SEEKER_FIRE_DELAY,period=LabHardLiveSession.SEEKER_FIRE_PERIOD,
                mode='approach',pause_until=0.,last_shot=-999.)
        elif family in GROUND_SPEED:
            b['grounded']=False

        if not (row['props'].get('move') or {}).get('relativeto'):
            row['props']['move']=dict(vel=dict(xvel=0,yvel=0))
            self.host.batch.modify(oid,'MOVE',message=('Properties_Move',dict(vel_=dict(xvel_=0,yvel_=0))),offset=self.host._offset())
        return True

    def state(self,oid,name):
        row=self.host.actors.get(oid)
        if row and row.get('_etl_state')!=name:
            row['_etl_state']=name;self.host.record('etl_enemy_state',id=oid,vid=row['vid'],state=name)

    def attack(self,oid,anim,duration):
        unit=self.units[oid];unit['busy']=self.now+duration/1000
        unit['recovery']=unit['busy']+.4
        if getattr(self.host,'presentation',None):self.host.presentation.attack(oid,unit['vid'],anim,duration,token=self.now)
        else:self.host.batch.animprops(oid,anim,duration,offset=self.host._offset())
        self.host.record('etl_enemy_attack',id=oid,vid=unit['vid'],anim=anim,duration_ms=duration,evidence='D native action; selection/recovery SIM')
        self.counts['attacks']+=1

    def spawn_projectile(self,*args,**kwargs):return self.projectiles.spawn(*args,**kwargs)

    def _schedule(self,oid,vid,delay,target,speed,**extra):
        self.pending.append(dict(owner=oid,vid=vid,at=self.now+delay,target=tuple(target),speed=speed,**extra))

    def _ground(self,oid,u,dt,target,acquired):
        b=u['body'];family=u['family'];speed=GROUND_SPEED[family]
        vx=u['direction']*speed
        if self.now<u['recovery']:vx=0.
        if family=='shambler' and target is not None and acquired:
            px,py=center(target);cx,cy=body_center(b)
            if abs(px-cx)<=100*256 and abs(py-cy)<b['h'] and self.now>=u['next_attack']:
                self.attack(oid,4,1320);u['next_attack']=self.now+2.2;vx=0.

        if family=='charger' and acquired and target is not None and b.get('grounded') and self.now>=u['jump_after']:
            px,py=center(target)
            if py<body_center(b)[1]-80*256 and self.ground.landing_above(b,py,200*256):
                b['vy']=-185000.;b['grounded']=False;u['jump_after']=self.now+2.4
                self.host.record('etl_charger_hop',id=oid,evidence='D charger ground/air capability; trigger/launch SIM')
        before=(b['x'],b['y']);self.ground.step(b,dt,vx,340000.)
        if b.get('blocked'):u['direction']*=-1
        if b.get('grounded') and vx:

            if self.ground.support(b['x']+u['direction']*speed*dt,b['y'],b['w'],b['h']) is None:u['direction']*=-1
        pose=5 if b.get('grounded') and vx else 4 if b.get('grounded') else 3 if b['vy']<0 else 1
        self.state(oid,'attack-recover' if self.now<u['recovery'] else 'ground-patrol')
        move(self.host,oid,(b['x'],b['y']),dt,pose,0 if u['direction']>0 else 180)

    def _mold(self,oid,u,dt,target,acquired):
        b=u['body'];phase=u['phase'];home=u['home'];vx=u['direction']*78000.;vy=0.
        if phase not in ('dive','return') and acquired and target is not None and self.now>=u['next_attack']:
            px,py=center(target);cx,cy=body_center(b)
            if py>cy+80*256 and abs(px-cx)<250*256:
                u.update(phase='dive',phase_end=self.now+1.0,velocity=(u['direction']*78000.,79000.))
                u['next_attack']=self.now+POLICIES['mold']['period']
                if getattr(self.host,'presentation',None):self.host.presentation.sound(oid,'lab.swoopingmolddrop',self.now)
        if u['phase']=='dive':
            vx,vy=u['velocity']
            if self.now>=u['phase_end']:u['phase']='return'
        elif u['phase']=='return':
            vy=max(-79000.,min(79000.,(home[1]-b['y'])/max(dt,1e-6)))
            if abs(home[1]-b['y'])<256:
                u['phase']='patrol';u['next_attack']=max(u['next_attack'],self.now+.8)
        old=(b['x'],b['y']);end=(b['x']+vx*dt,b['y']+vy*dt)
        hit=swept(self.host,old,end,(b['w'],b['h']),ignore=(oid,))
        if hit:
            f=max(0.,hit[0]-1e-6);end=tuple(a+(z-a)*f for a,z in zip(old,end));u['direction']*=-1
            if u['phase']=='dive':u['phase']='return'
        b['x'],b['y']=end;self.state(oid,'mold-'+u['phase'])
        move(self.host,oid,end,dt,3 if vy else 2,0 if vx>=0 else 180)

    def _weapon(self,oid,u,dt,target,acquired):
        b=u['body'];family=u['family'];distance=math.dist(center(target),body_center(b)) if target is not None else math.inf
        if target is not None and acquired and self.now>=u['next_attack'] and distance<=POLICIES[family]['attack']*256:
            point=center(target)
            if family=='pulse':
                self.attack(oid,4,2508)
                angle=270 if u['vid'].endswith('up') else 90 if u['vid'].endswith('down') else 0 if point[0]>=body_center(b)[0] else 180
                self.pending.append(dict(owner=oid,vid='lab.electricity',at=self.now+1.980,angle=angle,speed=0.,target=point,lifetime=2.25))
                u['phase_end']=self.now+4.55;u['phase']='pulse-hold'
            elif family=='mounted':
                set_prop(self.host,oid,'facing',round(math.degrees(math.atan2(point[1]-body_center(b)[1],point[0]-body_center(b)[0])))%360)
                self.attack(oid,3,2970)

                u['phase']='rapid';u['phase_end']=self.now+2.970;u['burst_left']=20
            elif family=='eater':
                self.attack(oid,1,1848);self._schedule(oid,'lab.seed',1.452,point,110000.)
                offset=max(-240*256,min(240*256,point[0]-body_center(b)[0]))
                u['track'].set(dict(waypoint=dict(points=[dict(xmp=round(u['home'][0]+offset),ymp=round(u['home'][1]),dt=2500)])),self.now)
                u['phase']='extend';u['phase_end']=self.now+2.5
            elif family=='flower':
                self._schedule(oid,'lab.explodingflower',.1,point,0.,gravity=340000.,lifetime=5.,impact='lab.explodingflowerx')
            elif family=='snake':
                self.attack(oid,1,2970);u['phase']='strike';u['phase_end']=self.now+2.970
                u['velocity']=(39000. if point[0]>=body_center(b)[0] else -39000.,0.)
            u['next_attack']=math.inf if family=='mounted' else self.now+POLICIES[family]['period']
        if family=='mounted' and u['phase']=='rapid' and self.now>=u['phase_end']:
            if u.get('burst_left',0)>0 and target is not None:
                self.attack(oid,5,264);self._schedule(oid,'lab.bullet',0.,center(target),78000.)
                u['burst_left']-=1;u['phase_end']+=.103
            else:
                self.attack(oid,4,2970);u['phase']='rover-recover';u['phase_end']=self.now+2.970
                u['next_attack']=u['phase_end']+.4
        if family=='mounted' and u['phase']=='rover-recover' and self.now>=u['phase_end']:u['phase']='patrol'
        if family=='pulse' and u['phase']=='pulse-hold' and self.now>=u['phase_end']:
            if u['vid']=='lab.epturret':
                u['leg']^=1
                u['track'].set(dict(to=dict(xmp=u['home'][0],ymp=u['home'][1]+32768*u['leg'],dt=1677)),self.now)
                u['phase']='pulse-slide'
                if getattr(self.host,'presentation',None):self.host.presentation.sound(oid,'lab.epmove',self.now)
            else:u['phase']='patrol'
        if family in ('pulse','eater'):
            position=u['track'].position(self.now);b['x'],b['y']=position
            if family=='eater' and u['phase']=='extend' and self.now>=u['phase_end']:
                u['track'].set(dict(to=dict(xmp=u['home'][0],ymp=u['home'][1],dt=2500)),self.now);u['phase']='return'
            move(self.host,oid,position,dt)
        elif family=='snake' and u['phase']=='strike':
            vx=u['velocity'][0];end=(b['x']+vx*dt,b['y'])
            if self.now>=u['phase_end'] or swept(self.host,(b['x'],b['y']),end,(b['w'],b['h']),ignore=(oid,)):
                u['phase']='return'
            else:b['x'],b['y']=end
            move(self.host,oid,(b['x'],b['y']),dt,2,0 if vx>0 else 180)
        elif family=='snake' and u['phase']=='return':
            b['x']+=max(-39000*dt,min(39000*dt,u['home'][0]-b['x']))
            move(self.host,oid,(b['x'],b['y']),dt,4)
        self.state(oid,family+'-'+u['phase'])

    def _follow_components(self):
        for _ in range(3):
            for oid in list(self.components)+[i for i,u in self.units.items() if u['family']=='mounted']:
                row=self.host.actors.get(oid)
                if not row:continue
                if row['vid'] in ('lab.cshield','lab.brambleshield'):
                    parent=self._parent(oid);owner=self.units.get(parent)
                    expected='charger' if row['vid']=='lab.cshield' else 'tumble'
                    if owner and owner['family']==expected:
                        offset=owner['body']['w'] if owner['direction']>0 else -row['props']['wmp']
                        movement=row['props'].get('move') or {}
                        if (movement.get('to') or {}).get('xmp')!=offset:
                            movement=dict(relativeto=dict(objid=parent),to=dict(xmp=offset,ymp=0))
                            row['props']['move']=movement
                            self.host.batch.modify(oid,'MOVE',message=('Properties_Move',self.host.normalize('Properties_Move',movement)),offset=self.host._offset())
                if follow(self.host,oid):
                    if oid in self.units:
                        self.units[oid]['body']['x'],self.units[oid]['body']['y']=row['pos']
                    continue
                parent=row.get('_etl_component_parent')
                if parent in self.host.actors:
                    offset=row['_etl_component_offset'];row['pos']=[self.host.actors[parent]['pos'][i]+offset[i] for i in (0,1)]

    def step(self,dt):
        self.now=self.host.room_time;self.ground=terrain(self.host)
        for oid,end in list(self.component_deaths.items()):
            if self.now+1e-9>=end:self.retire(oid,'INTERNAL');self.component_deaths.pop(oid,None)
        self.history.append((self.now,{i:center(p) for i,p in enumerate(players(self.host))}))
        active_seekers={}
        for oid,u in list(self.units.items()):
            row=self.host.actors.get(oid)
            if row is None:self.units.pop(oid,None);continue
            target=nearest(self.host,body_center(u['body']));p=POLICIES[u['family']]
            distance=math.dist(center(target),body_center(u['body'])) if target is not None else math.inf
            if not u['active'] and distance<=p['wake']*256:
                u['active']=True;self.counts['activations']+=1
                self.host.record('etl_enemy_activated',id=oid,vid=u['vid'],range_px=p['wake'],evidence='SIM species gate')
                if u['family']=='seeker':self.seeker.seekers[oid]['next_shot']=self.now+LabHardLiveSession.SEEKER_FIRE_DELAY
            acquired=target is not None and distance<=p['acquire']*256
            if not u['active']:
                if u['family'] in GROUND_SPEED:
                    self.ground.step(u['body'],dt,0.,340000.)
                    move(self.host,oid,(u['body']['x'],u['body']['y']),dt,4 if u['body'].get('grounded') else 1)
                self.state(oid,'dormant');continue
            if u['family']=='seeker':
                if target is not None:active_seekers[oid]=self.seeker.seekers[oid]
            elif u['family'] in GROUND_SPEED:self._ground(oid,u,dt,target,acquired)
            elif u['family']=='mold':self._mold(oid,u,dt,target,acquired)
            elif u['family']=='vine':

                x=u['home'][0]+math.sin(self.now/2.)*16*256;y=u['home'][1]+math.sin(self.now/3.)*32*256
                u['body']['x'],u['body']['y']=x,y;move(self.host,oid,(x,y),dt);self.state(oid,'vine-sway')
            else:self._weapon(oid,u,dt,target,acquired)
        all_seekers=self.seeker.seekers;self.seeker.seekers=active_seekers;self.seeker.room_time=self.now
        try:self.seeker._step(dt)
        finally:self.seeker.seekers=all_seekers
        for oid,s in active_seekers.items():
            row=self.host.actors.get(oid)
            if row:
                old=row['pos'];row['pos']=[s['x'],s['y']];row['vel']=[(row['pos'][i]-old[i])/dt if dt else 0. for i in (0,1)]
                interval=getattr(self.host,'_slice_ms',round(dt*1000))
                if tuple(old)!=tuple(row['pos']):self.host.batch.move_to(oid,round(s['x']),round(s['y']),dt=max(1,interval),offset=self.host._offset())
                self.units[oid]['body'].update(x=s['x'],y=s['y'])
                set_prop(self.host,oid,'pose',3 if abs(row['vel'][1])>1 else 2 if abs(row['vel'][0])>1 else 1)
                self.state(oid,'seeker-'+s.get('mode','approach'))
        self._follow_components()
        for event in list(self.pending):
            if event['at']>self.now+1e-9:continue
            self.pending.remove(event);owner=event['owner'];row=self.host.actors.get(owner)
            if row is None or owner in self.retired:continue
            x,y,w,h=(*row['pos'],row['props'].get('wmp',0),row['props'].get('hmp',0));origin=(x+w/2,y+h/2)
            point=event['target'];angle=math.radians(event['angle']) if 'angle' in event else math.atan2(point[1]-origin[1],point[0]-origin[0])
            speed=event['speed'];props=self.host.pack.props(event['vid'])
            position=projectile_top_left(row,props)
            if position is None:
                position=(origin[0]-props.get('wmp',0)/2,origin[1]-props.get('hmp',0)/2)

            velocity=(0.,0.) if speed==0 and event['vid']=='lab.electricity' else (math.cos(angle)*speed,math.sin(angle)*speed)
            self.projectiles.spawn(event['vid'],position,velocity,owner,
                gravity=event.get('gravity',0.),lifetime=event.get('lifetime'),impact=event.get('impact'))
        self.projectiles.step(dt)

    def retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        source=self.host.actors.get(oid)
        if source and source['vid']=='lab.brambleshield' and reason in (2,'HITPOINTS','hitpoints'):
            follow(self.host,oid)
            x,y,w,h=(*source['pos'],source['props'].get('wmp',0),source['props'].get('hmp',0))

            for _ in range(10):
                angle=self.host.rng.uniform(0.,2*math.pi)
                self.projectiles.spawn('lab.bramble',(x+w/2,y+h/2),
                    (math.cos(angle)*78000.,math.sin(angle)*78000.),oid,lifetime=60.,impact='lab.brambled')
            self.host.record('etl_bramble_shield_death',id=oid,count=10,
                evidence='D same incoming packet HP Destroy/10 Creates; center/speed FIT; angle RNG SIM')
        for child,row in list(self.host.actors.items()):
            if child==oid:continue
            parent=row.get('_etl_component_parent')
            if parent is None and (child in self.components or child in self.units):parent=self._parent(child)
            referenced=((row['props'].get('move') or {}).get('lerp') or {}).get('target',{}).get('objid')==oid
            if (parent==oid or referenced) and (child in self.components or child in self.units):
                if row['vid'] in ('lab.vflapup','lab.vflapdown'):

                    set_prop(self.host,child,'vid','lab.dflapup' if row['vid'].endswith('up') else 'lab.dflapdown')
                    set_prop(self.host,child,'damage',0)
                    row.pop('_etl_component_parent',None)
                    row['props']['move']=dict(to=dict(xmp=round(row['pos'][0]),ymp=round(row['pos'][1])))
                    self.host.batch.modify(child,'MOVE',message=('Properties_Move',self.host.normalize('Properties_Move',row['props']['move'])),offset=self.host._offset())
                    self.component_deaths[child]=self.host.room_time+.924
                else:self.retire(child,'INTERNAL')
        self.units.pop(oid,None);self.components.pop(oid,None);self.seeker.seekers.pop(oid,None)
        self.pending=[e for e in self.pending if e['owner']!=oid]
        super().retire(oid,reason,native)

    def feedback(self,event):
        if self.projectiles.feedback(event):return True
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        return super().feedback(event,drop=oid in self.units and self.units[oid]['family']!='mounted')

    def expire(self,oid):
        if self.projectiles.expire(oid):return True
        if oid not in self.owned:return False
        self.retire(oid,'LIFETIME');return True

    def status(self):
        return dict(alive=len(self.units),components=len(self.components),projectiles=len(self.projectiles.shots),
            families=dict(Counter(u['family'] for u in self.units.values())),counts=dict(self.counts),
            policyEvidence='D fields/actions/sounds; lab_hard seeker FIT capability; species activation/choice/navigation SIM')
