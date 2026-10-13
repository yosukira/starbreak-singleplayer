'Live ordinary EFB species, with explicit D/FIT/SIM boundaries.'
import math
from collections import Counter
from .abilities import NATIVE,Ownership,Projectiles,rect,center,nearest,players,prop,move,stop_motion,swept
from .mites import MiteAbility,MITES

FAMILIES={**{v:'mite' for v in MITES},'ice.bug':'bug','ice.tunneler':'tunneler',
    'ice.iturret':'turret','ice.spiner':'spiner','ice.yetikong':'yeti','ice.rdino':'dino'}
COMPONENTS={'ice.iturretb','ice.fbody'}

POLICIES={'mite':(1200,900),'bug':(1000,800),'tunneler':(1000,800),
    'turret':(1200,1000),'spiner':(1200,1000),'yeti':(1400,1200),'dino':(1200,900)}

class ExplorationEnemies(Ownership):
    def __init__(self,host):
        super().__init__(host,'_efb_enemy');self.units={};self.components={};self.pending=[];self.now=host.room_time
        self.mites=MiteAbility(self);self.projectiles=Projectiles(host);self.counts=Counter()

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        self.host.record('efb_enemy_policy',evidence='D native species/actions/projectiles/relationships; FIT speeds/anchors; wake/acquire/navigation/cadence SIM')

    def add_actor(self,oid):
        row=self.host.actors.get(oid)
        if not row or row.get('dead') or oid in self.retired:return False
        if oid in self.owned:return True
        vid=row['vid']
        if vid in COMPONENTS:self.own(oid);self.components[oid]=vid;return True
        if vid not in FAMILIES:return False
        self.own(oid);row.pop('_efb_mechanic',None);family=FAMILIES[vid];p=row['props']
        direction=-1 if p.get('facing')==180 else 1
        if 'facing' not in p:direction=self.host.rng.choice((-1,1))
        self.units[oid]=dict(vid=vid,family=family,active=False,home=tuple(row['pos']),direction=direction,
            next=self.now+.8,busy=0.,leg_until=self.now+.8,phase='dormant')
        if vid in MITES:self.mites.add(oid)
        elif family in ('bug','tunneler'):stop_motion(self.host,oid)
        if family=='tunneler':
            x,y,w,h=rect(row)
            spans=[(q['pos'][0],q['pos'][0]+q['props'].get('wmp',0)-w) for q in self.host.actors.values()
                if q['props'].get('terraintype')==1 and q['props'].get('wmp',0)>w and q['pos'][0]<=x
                and q['pos'][0]+q['props'].get('wmp',0)>=x+w and q['pos'][1]<=y+h and q['pos'][1]+q['props'].get('hmp',0)>y]
            self.units[oid]['burrow_span']=min(spans,key=lambda p:p[1]-p[0]) if spans else (x-640*256,x+640*256)
        return True

    def _attack(self,oid,anim,ms):
        row=self.host.actors[oid];self.host.presentation.attack(oid,row['vid'],anim,ms,token=self.now)
        self.host.record('efb_enemy_attack',id=oid,vid=row['vid'],anim=anim,time_ms=ms,
            evidence='D native action and direct child latency; target/choice/cooldown SIM')
        self.counts['attacks']+=1

    def _anchor(self,oid,vid,angle=None):
        row=self.host.actors[oid];x,y,w,h=rect(row);props=self.host.pack.props(vid);facing=row['props'].get('facing',0)
        key=row['vid']+' -> '+vid+' facing'+str(facing);fitted=NATIVE['emissionOffsets'].get(key)
        if fitted:return tuple(row['pos'][i]+fitted['medianOffset'][i] for i in (0,1))
        angle=math.radians(facing if angle is None else angle)
        return x+w/2+math.cos(angle)*2048-props.get('wmp',0)/2,y+h/2+math.sin(angle)*2048-props.get('hmp',0)/2

    def _weapon(self,oid,u,target):
        row=self.host.actors[oid];x,y,w,h=rect(row);family=u['family']
        if self.now>=u['busy'] and u.get('recover_anim'):
            self._attack(oid,0,1122);u.pop('recover_anim',None)
        if target is None or self.now<u['next']:return
        point=center(target);distance=math.dist(point,(x+w/2,y+h/2))
        if distance>POLICIES[family][1]*256:return
        side=0 if point[0]>=x+w/2 else 180
        if family=='turret':
            angle=round(math.degrees(math.atan2(point[1]-y-h/2,point[0]-x-w/2)))%360
            prop(self.host,oid,'facing',angle);self._attack(oid,1,1000)
            self.pending.append(dict(at=self.now+.594,owner=oid,kind='shard',target=point,angle=angle))
            u['next']=self.now+2.2
        elif family=='spiner':
            anim=1 if point[1]<y+h/2-30*256 else 2;duration=924 if anim==1 else 990
            prop(self.host,oid,'facing',side);self._attack(oid,anim,duration)

            self.pending.append(dict(at=self.now+duration/1000,owner=oid,kind='spines',target=point))
            u['next']=self.now+3.
        elif family=='yeti':
            prop(self.host,oid,'facing',side);self._attack(oid,1,1056)
            self.pending.append(dict(at=self.now+.792,owner=oid,kind='throwball',target=point));u['next']=self.now+3.
        elif family=='dino':
            prop(self.host,oid,'facing',side);self._attack(oid,4,1914)
            self.pending.append(dict(at=self.now+.462,owner=oid,kind='flame',target=point))
            u.update(next=self.now+3.1,busy=self.now+1.914,recover_anim=True)

    def _emit(self,event):
        oid=event['owner'];row=self.host.actors.get(oid)
        if row is None:return
        x,y,w,h=rect(row);point=event['target'];kind=event['kind']
        if kind=='spines':
            angle=math.atan2(point[1]-y-h/2,point[0]-x-w/2)
            for index in range(5):
                r=angle+self.host.rng.uniform(-.6,.6)
                origin=(x+self.host.rng.uniform(0,max(0,w-6144)),y+self.host.rng.uniform(-8192,h/3))
                self.projectiles.spawn('ice.spine',origin,(math.cos(r)*97656.25,math.sin(r)*97656.25),oid,
                    gravity=97656.25,ttl=2.5,audible=index==0)
        elif kind=='shard':
            angle=math.radians(event['angle']);origin=self._anchor(oid,'ice.shard',event['angle'])
            self.projectiles.spawn('ice.shard',origin,(math.cos(angle)*39062.5,math.sin(angle)*39062.5),oid)
        elif kind=='throwball':
            origin=self._anchor(oid,'ice.throwball');flight=1.;gravity=180000.
            vx=max(-117000.,min(117000.,point[0]-origin[0]));vy=(point[1]-origin[1]-.5*gravity*flight**2)/flight
            self.projectiles.spawn('ice.throwball',origin,(vx,vy),oid,gravity=gravity,ttl=8.)
        elif kind=='flame':
            origin=self._anchor(oid,'ice.fhead');angle=math.atan2(point[1]-origin[1],point[0]-origin[0])
            head=self.projectiles.spawn('ice.fhead',origin,(math.cos(angle)*58593.75,math.sin(angle)*58593.75),oid,ttl=8.)
            parent=head

            for _ in range(3):
                child=self.host._create('ice.fbody',origin,head,dict(move=dict(relativeto=dict(objid=parent),follow=dict(maxdist=32))))
                self.own(child);self.components[child]='ice.fbody';self.host.actors[child]['_efb_flame_head']=head;parent=child

    def _bug(self,oid,u,dt,target):
        row=self.host.actors[oid];x,y,w,h=rect(row)
        if self.now>=u['leg_until']:
            u['leg_until']=self.now+self.host.rng.uniform(.6,1.1)

            u['vy']=self.host.rng.uniform(-16000.,16000.)
            if y<u['home'][1]-160*256:u['vy']=abs(u['vy'])
            elif y>u['home'][1]+160*256:u['vy']=-abs(u['vy'])
        vy=u.get('vy',0.);vx=u['direction']*math.sqrt(max(0,32500.**2-vy**2));end=(x+vx*dt,y+vy*dt)
        hit=swept(self.host,(x,y),end,(w,h),ignore=(oid,))
        if hit:
            f=max(0,hit[0]-1e-6);end=(x+(end[0]-x)*f,y+(end[1]-y)*f);u['direction']*=-1;u['leg_until']=self.now
        move(self.host,oid,end,dt,3 if abs(vy)>12000 else 2,0 if u['direction']>0 else 180)

    def _tunneler(self,oid,u,dt):
        row=self.host.actors[oid];x,y,w,h=rect(row);lo,hi=u['burrow_span']
        nx=x+u['direction']*48000*dt
        if nx<lo or nx>hi:nx=max(lo,min(hi,nx));u['direction']*=-1

        move(self.host,oid,(nx,u['home'][1]),dt,5,0 if u['direction']>0 else 180)

    def _follow(self):
        for oid in list(self.components):
            row=self.host.actors.get(oid)
            if row is None:self.components.pop(oid,None);continue
            m=row['props'].get('move') or {};parent=m.get('relativeto',{}).get('objid')
            if parent not in self.host.actors:
                if row.get('_efb_flame_head') is not None:self.retire(oid,'INTERNAL')
                continue
            target=[round(v) for v in self.host.actors[parent]['pos']]
            if m.get('follow'):
                old=row['pos'];dx=(int(old[0])>>8)-(int(target[0])>>8);dy=(int(old[1])>>8)-(int(target[1])>>8)
                distance=dx*dx+dy*dy;maximum=m['follow'].get('maxdist',0)
                if distance>maximum*maximum:
                    scale=(maximum<<10)//max(1,math.isqrt(distance))
                    row['pos']=[target[0]+math.trunc(dx*scale/1024)*256,target[1]+math.trunc(dy*scale/1024)*256]
            else:
                to=m.get('to') or {};row['pos']=[target[0]+to.get('xmp',0),target[1]+to.get('ymp',0)]

    def step(self,dt):
        self.now=self.host.room_time
        mechanics=getattr(self.host,'mechanics',None)
        if mechanics and hasattr(mechanics,'pre_physics'):
            mechanics.pre_physics(dt);self.mites.carry(mechanics.platform_deltas)
        active=set()
        for oid,u in list(self.units.items()):
            row=self.host.actors.get(oid)
            if row is None:self.units.pop(oid,None);continue
            x,y,w,h=rect(row);target=nearest(self.host,(x+w/2,y+h/2));gate=POLICIES[u['family']][0]
            if not u['active'] and target is not None and math.dist(center(target),(x+w/2,y+h/2))<=gate*256:
                u['active']=True;self.counts['activations']+=1
                self.host.record('efb_enemy_activated',id=oid,vid=u['vid'],range_px=gate,evidence='SIM species wake, no fitted exact radius')
            if not u['active']:continue
            if u['family']=='mite':active.add(oid)
            elif u['family']=='bug':self._bug(oid,u,dt,target)
            elif u['family']=='tunneler':self._tunneler(oid,u,dt)
            else:self._weapon(oid,u,target)
        self.mites.step(dt,active)
        for event in list(self.pending):
            if self.now+1e-9>=event['at']:self.pending.remove(event);self._emit(event)
        self.projectiles.step(dt);self._follow()

    def retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        self.units.pop(oid,None);self.components.pop(oid,None);self.mites.forget(oid)
        self.pending=[e for e in self.pending if e['owner']!=oid]
        for child,row in list(self.host.actors.items()):
            if child!=oid and row.get('_efb_flame_head')==oid:self.retire(child,'INTERNAL')
        super().retire(oid,reason,native)

    def feedback(self,event):
        if self.projectiles.feedback(event):return True
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        return super().feedback(event,drop=oid in self.units)

    def expire(self,oid):
        if self.projectiles.expire(oid):return True
        if oid not in self.owned:return False
        self.retire(oid,'LIFETIME');return True

    def status(self):
        return dict(alive=len(self.units),families=dict(Counter(u['family'] for u in self.units.values())),
            components=len(self.components),projectiles=len(self.projectiles.shots)+sum(a['kind']!='npc' for a in self.mites.actors.values()),
            counts=dict(self.counts),policyEvidence='D native fields/actions/batches/FOLLOW; existing Ice mite capabilities; FIT speed/anchors; decision gates/navigation SIM')
