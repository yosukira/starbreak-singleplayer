"""Independent, seeded Bonesaw rules driven by current players and damage."""
import math
import random
from .. import MapController
from .scene import BonesawPack
from ... import game_settings

def ray_box(origin, direction, rect):
    lo, hi = 0., math.inf
    for p, d, a, size in zip(origin, direction, rect[:2], rect[2:]):
        if abs(d)<1e-10:
            if p<a or p>a+size:return None
        else:
            near,far=(a-p)/d,(a+size-p)/d
            lo,hi=max(lo,min(near,far)),min(hi,max(near,far))
            if hi<lo:return None
    return lo if hi>=0 else None

class BonesawController(MapController):
    name='sc_bonesaw'

    def __init__(self,pack=None,seed=0):
        self.pack=pack or BonesawPack();self.seed=seed
        self.parameters=game_settings.balance('sc_bonesaw')
        self.custom_attack_rates=self.parameters['laser_rate']!=1. or self.parameters['bullet_rate']!=1.
        self.prepare()

    def prepare(self):
        self.rng=random.Random(self.seed);self.time=0.;self.state='waiting'
        self.players={};self.events=[];self.actors={};self.next_id=2000;self.next_wire=0.
        self.seen_damage=set();self.bosses={};self.turrets={}
        for oid in self.pack.scene['boss_ids']:
            p=self.pack.templates[str(oid)]
            self.bosses[oid]=dict(id=oid,x=p['xmp']/256,y=p['ymp']/256,damage=0,timer_start=None,
                action='rest',until=0.,move_start=0.,move_from=448.,move_to=-32.,dead=False,
                next_saw=0.,saw_release=None,saws=0)
        for oid in self.pack.scene['turret_ids']:
            p=self.pack.templates[str(oid)];owner=p['move']['relativeto']['objid']
            rel=p['move']['to']
            self.turrets[oid]=dict(id=oid,owner=owner,offset=[rel['xmp']/256,rel['ymp']/256],
                normal=p['facing'],angle=float(p['facing']),next=0.,attack=None,target=None)
            if self.custom_attack_rates:
                self.turrets[oid]['due']={}
                self.turrets[oid]['cadence_rng']={kind:random.Random(f'{self.seed}:{oid}:{kind}')
                                                for kind in ('laser','spirit')}

    def emit(self,kind,**data):self.events.append(dict(kind=kind,at=self.time,**data))
    def drain(self):events,self.events=self.events,[];return events

    def start(self):
        if self.state!='waiting':return
        self.state='fighting'
        for b in self.bosses.values():
            b['until']=self.time+self.rng.uniform(4,6)
            b['next_saw']=self.time+self.rng.uniform(.3,2)
        for t in self.turrets.values():t['next']=self.time+self.rng.uniform(.5,4)
        if self.custom_attack_rates:
            for t in self.turrets.values():
                for kind in ('laser','spirit'):self._schedule_attack(t,kind,initial=True)

    def _schedule_attack(self,t,kind,initial=False):
        """One due time per attack kind; occupied guns never build a queue.

        Baseline probability converts the old mixed cooldown into each kind's
        interval. Independent RNG streams keep the other kind's sampled gaps
        stable when only one frequency changes. Warnings/active windows stay
        unchanged, so a busy/unaligned gun can fire later than its due time.
        """
        r=self.pack.entities['turret'];laser=kind=='laser'
        weight=r['laser_chance'] if laser else 1.-r['laser_chance']
        if weight<=0:t['due'][kind]=math.inf;return
        factor=self.parameters['laser_rate' if laser else 'bullet_rate']
        low,high=(.5,4) if initial else (r['cooldown_min_s'],r['cooldown_max_s'])
        t['due'][kind]=self.time+t['cadence_rng'][kind].uniform(low,high)/(weight*factor)

    def set_players(self,players):
        self.players={k:dict(v,center=[float(x)/256 for x in v['center']])
                      for k,v in players.items() if v.get('alive',True)}

    def add_damage(self,value,objid=None,player_id=None,event_id=None):
        b=self.bosses.get(objid)
        if not b or b['dead'] or b['action']=='dying' or self.state!='fighting' or not math.isfinite(value) or value<=0:return []
        if event_id is not None:
            if event_id in self.seen_damage:return []
            self.seen_damage.add(event_id)
        r=self.pack.entities['boss']
        b['damage']=min(r['required_damage'],b['damage']+value)
        if b['timer_start'] is None and b['damage']>=r['timer_damage']:
            b['timer_start']=self.time;self.emit('timer_started',id=objid,damage=b['damage'])
        return self.drain()

    def turret_center(self,t):
        b=self.bosses[t['owner']]
        return [b['x']+t['offset'][0]+32,b['y']+t['offset'][1]+32]

    def target(self,t):
        origin=self.turret_center(t);r=self.pack.entities['turret'];choices=[]
        for key,p in self.players.items():
            x,y=p['center']

            if t['owner']==28 and x>self.bosses[29]['x']+256:continue
            if t['owner']==29 and x<self.bosses[28]['x']:continue
            dx,dy=x-origin[0],y-origin[1];distance=math.hypot(dx,dy)
            angle=math.degrees(math.atan2(dy,dx))%360
            if distance>r['range_px'] or abs((angle-t['normal']+180)%360-180)>r['arc_deg']/2:continue
            direction=[dx/distance,dy/distance] if distance else [1,0]

            if any((hit:=ray_box(origin,direction,(b['x'],b['y'],256,256))) is not None and hit<distance
                   for b in self.bosses.values() if not b['dead']):continue
            choices.append((distance,key,angle))
        return min(choices,key=lambda p:p[0]) if choices else None

    def new_actor(self,vid,pos,owner=0,**fields):
        oid=self.next_id;self.next_id+=1
        a=dict(id=oid,vid=vid,pos=list(pos),owner=owner,born=self.time,kind='effect',until=math.inf,**fields)
        self.actors[oid]=a;self.emit('create',actor=dict(a));return a

    def remove(self,oid,reason='INTERNAL'):
        a=self.actors.pop(oid,None)
        if a:self.emit('remove',id=oid,reason=reason)

    def native_destroy(self,objid,reason):
        a=self.actors.get(objid)

        if a and a['kind']=='spirit' and reason in (3,4,5,6):self.remove(objid,'SHIELD' if reason==3 else 'BLOCKED')
        return self.drain()

    def beam_end(self,t,angle):
        origin=self.turret_center(t);direction=[math.cos(math.radians(angle)),math.sin(math.radians(angle))]
        boxes=self.pack.solids+[(b['x'],b['y'],256,256) for b in self.bosses.values() if not b['dead']]
        distances=[d for box in boxes if (d:=ray_box(origin,direction,box)) is not None and d>1e-6]
        return origin,direction,min(distances,default=2400.)

    def begin_laser(self,t):
        r=self.pack.entities['turret'];origin,direction,length=self.beam_end(t,t['angle'])
        t['attack']=dict(kind='laser',release=self.time+r['warning_s'],end=self.time+r['warning_s']+r['active_s'],
            angle=t['angle'],length=length,active=False,ids=[])
        for d in range(32,max(33,int(length)),32):
            a=self.new_actor('sc.aim',[origin[j]+direction[j]*d-32 for j in (0,1)],t['owner'],
                facing=round(t['angle']),
                attach=t['id'],relative=[direction[j]*d for j in (0,1)])
            a['until']=t['attack']['end'];a['kind']='beam';t['attack']['ids'].append(a['id'])
            self.emit('anim',id=a['id'],anim=1,duration=.264)
        a=self.new_actor('sc.aimend',[origin[j]+direction[j]*length-32 for j in (0,1)],t['owner'],
            facing=round(t['angle']),attach=t['id'],relative=[direction[j]*length for j in (0,1)])
        a['until']=t['attack']['end'];a['kind']='beam';t['attack']['ids'].append(a['id'])
        self.emit('sound',id=t['id'],name='sc.cturret2')
        self.emit('laser_warning',id=t['id'],angle=t['angle'],length=length,target=t['target'])

    def fire_laser(self,t):
        attack=t['attack'];attack['active']=True
        origin,direction,length=self.beam_end(t,attack['angle'])
        length=min(length,attack['length'])
        for d in range(32,max(33,int(length)),32):
            a=self.new_actor('',[origin[j]+direction[j]*d-8 for j in (0,1)],t['owner'],
                attach=t['id'],relative=[direction[j]*d for j in (0,1)],
                properties=dict(wmp=4096,hmp=4096,damage=35,draw=False,lifetime=round((attack['end']-self.time)*1000)))
            a['until']=attack['end'];a['kind']='beam';attack['ids'].append(a['id'])
        self.emit('laser_active',id=t['id'],duration=attack['end']-self.time)

    def cancel_attack(self,t):
        attack=t['attack']
        if attack:
            for oid in attack.get('ids',[]):self.remove(oid)
        t['attack']=None

    def begin_death(self,b):
        b.update(action='dying',until=self.time+self.pack.entities['boss']['death_s'],saw_release=None)
        self.emit('boss_dying',id=b['id']);self.emit('anim',id=b['id'],anim=1,duration=.528)
        for t in self.turrets.values():
            if t['owner']==b['id']:self.cancel_attack(t);self.emit('retire_turret',id=t['id'])
        for _ in range(12):
            a=self.new_actor('sc.xfx',[b['x']+self.rng.uniform(-40,200),b['y']+self.rng.uniform(-40,200)],b['id'])
            a['until']=self.time+1.2

    def _boss_step(self,b,dt):
        r=self.pack.entities['boss'];s=self.pack.entities['saw']
        if b['dead']:return
        if b['action']=='dying':
            if self.time+1e-8>=b['until']:
                b['dead']=True;self.emit('boss_dead',id=b['id'])
            return
        if b['timer_start'] is not None and b['damage']>=r['required_damage'] and self.time-b['timer_start']+1e-8>=r['min_s']:
            self.begin_death(b);return
        if b['action']=='move':
            f=min(1.,(self.time-b['move_start'])/b['travel'])
            b['y']=b['move_from']+(b['move_to']-b['move_from'])*f
            if f>=1:
                b['action']='rest';b['until']=self.time+self.rng.uniform(r['dwell_min_s'],r['dwell_max_s'])
        if b['saw_release'] is not None and self.time+1e-8>=b['saw_release']:
            b['saw_release']=None;b['saws']+=1

            direction=-1 if b['id']==28 else 1
            a=self.new_actor('sc.saw',[b['x']+(131 if direction<0 else 29),b['y']+115],b['id'],
                direction=direction,edge='launch',facing=0,pose=2)
            a['kind']='saw';self.emit('sound',id=a['id'],name='sc.saw')
            self.emit('saw_release',id=b['id'],actor_id=a['id'],count=b['saws'])
        if b['action']=='rest':
            cap=s['cap'] if b['damage']>=r['fast_damage'] else s['early_cap']
            if self.time>=b['next_saw'] and b['saw_release'] is None and b['saws']<cap and b['until']-self.time>s['windup_s']:
                b['next_saw']=self.time+s['cooldown_s']
                if self.rng.random()<s['stationary_chance']:
                    b['saw_release']=self.time+s['windup_s'];self.emit('anim',id=b['id'],anim=3,duration=.594)
            if self.time+1e-8>=b['until'] and b['saw_release'] is None:
                b.update(action='move',move_start=self.time,move_from=b['y'],
                    move_to=r['top_y'] if b['y']>200 else r['bottom_y'],
                    travel=r['fast_travel_s'] if b['damage']>=r['fast_damage'] else r['travel_s'])
                self.emit('body_move',id=b['id'],x=b['x'],y=b['move_to'],duration=b['travel'])

    def _turret_step(self,t,dt):
        b=self.bosses[t['owner']]
        if b['dead'] or b['action']=='dying':return
        r=self.pack.entities['turret'];attack=t['attack']
        if attack:
            if self.time+1e-8>=attack['release'] and not attack.get('active'):
                if attack['kind']=='laser':self.fire_laser(t)
                else:
                    attack['active']=True;angle=attack['angle'];origin=self.turret_center(t)
                    direction=[math.cos(math.radians(angle)),math.sin(math.radians(angle))]
                    a=self.new_actor('sc.spirit',[origin[j]+direction[j]*54-16 for j in (0,1)],t['owner'],
                        facing=round(angle),pose=3,velocity=[d*r['spirit_speed_px_s'] for d in direction])
                    a['kind']='spirit';a['until']=self.time+r['spirit_life_s']
                    self.emit('sound',id=a['id'],name='sc.tbullet')
            if self.time+1e-8>=attack['end']:self.cancel_attack(t)
            return
        target=self.target(t)
        if target:
            _,t['target'],angle=target
            delta=(angle-t['angle']+180)%360-180
            t['angle']=(t['angle']+max(-r['turn_deg_s']*dt,min(r['turn_deg_s']*dt,delta)))%360
            due=min(t['due'],key=lambda kind:t['due'][kind]) if self.custom_attack_rates else None
            ready=self.time>=t['due'][due] if due is not None else self.time>=t['next']
            if ready and abs(delta)<8:
                if due is None:

                    t['next']=self.time+self.rng.uniform(r['cooldown_min_s'],r['cooldown_max_s'])
                    laser=self.rng.random()<r['laser_chance']
                else:
                    self._schedule_attack(t,due);laser=due=='laser'
                if laser:self.begin_laser(t)
                else:
                    t['attack']=dict(kind='spirit',release=self.time+r['spirit_windup_s'],end=self.time+.66,angle=t['angle'],active=False)
                    self.emit('sound',id=t['id'],name='sc.cturret1');self.emit('anim',id=t['id'],anim=0,duration=.66)

    def _saw_step(self,a,dt):
        s=self.pack.scene;x,y=a['pos'];speed=self.pack.entities['saw']['speed_px_s']*self.parameters['saw_speed']

        left,right,top,bottom=s['left'],s['right']-96,s['top'],s['bottom']-96
        budget=speed*dt
        while budget>1e-9:
            edge=a['edge']
            if edge=='launch':target=[left,y] if a['direction']<0 else [right,y];after='left' if a['direction']<0 else 'right'
            elif edge=='left':target=[left,top];after='top'
            elif edge=='top':target=[right,top];after='right'
            elif edge=='right':target=[right,bottom];after='bottom'
            else:target=[left,bottom];after='left'
            dist=math.hypot(target[0]-x,target[1]-y)
            if dist<=budget+1e-9:
                x,y=target;budget-=dist;a['edge']=after
                self.emit('props',id=a['id'],values=dict(POSE=3 if after in ('left','right') else 2))
            else:
                x+=(target[0]-x)*budget/dist;y+=(target[1]-y)*budget/dist;budget=0
        a['pos']=[x,y]

    def _step(self,dt):
        self.time+=dt
        if self.state=='waiting':return
        for b in self.bosses.values():self._boss_step(b,dt)
        if self.state!='done' and all(b['dead'] for b in self.bosses.values()):
            self.state='done'
            for oid in list(self.actors):self.remove(oid)
            self.emit('victory');return
        for t in self.turrets.values():self._turret_step(t,dt)

        boxes=self.pack.solids+[(b['x'],b['y'],256,256) for b in self.bosses.values() if not b['dead']]
        for a in list(self.actors.values()):
            if self.time>=a['until']:self.remove(a['id'],'LIFETIME');continue
            if a['kind']=='saw':self._saw_step(a,dt)
            elif a['kind']=='spirit':
                vx,vy=a['velocity'];distance=math.hypot(vx,vy)*dt
                direction=[vx*dt/distance,vy*dt/distance] if distance else [1,0]
                origin=[a['pos'][0]+16,a['pos'][1]+16]

                if any((hit:=ray_box(origin,direction,(x-16,y-16,w+32,h+32))) is not None and hit<=distance
                       for x,y,w,h in boxes):self.remove(a['id'],'BLOCKED');continue
                a['pos']=[a['pos'][0]+vx*dt,a['pos'][1]+vy*dt]
        if self.time+1e-8>=self.next_wire:
            self.next_wire=self.time+.05
            for t in self.turrets.values():
                if not self.bosses[t['owner']]['dead'] and self.bosses[t['owner']]['action']!='dying':
                    self.emit('props',id=t['id'],values=dict(FACING=round(t['angle'])))
            for a in self.actors.values():
                if a['kind'] in ('saw','spirit'):self.emit('move',id=a['id'],pos=list(a['pos']))

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')

        while dt>1e-9:
            delta=min(dt,1/120);self._step(delta);dt-=delta
        return self.drain()

    def status(self):
        return dict(time=self.time,state=self.state,bosses={i:{k:b[k] for k in ('damage','timer_start','action','dead','saws','x','y')}
                    for i,b in self.bosses.items()},actors=len(self.actors),saws=sum(a['kind']=='saw' for a in self.actors.values()))
