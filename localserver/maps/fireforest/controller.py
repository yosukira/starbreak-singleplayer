"""Real-time Forest encounters: player-dependent decisions, physics and phase gates."""
import copy
import math
import random
from .. import MapController
from .scene import ForestPack

def center(a):
    return [a['pos'][0]+a['size'][0]/2,a['pos'][1]+a['size'][1]/2]

def swept_box(start,end,size,rect):
    """First swept AABB contact, including thin walls crossed between samples."""
    lo,hi=0.,1.
    for p,q,w,r,s in zip(start,end,size,rect[:2],rect[2:]):
        a,b=r-w,r+s;delta=q-p
        if abs(delta)<1e-9:
            if p<=a or p>=b:return None
        else:
            u,v=(a-p)/delta,(b-p)/delta
            lo,hi=max(lo,min(u,v)),min(hi,max(u,v))
            if hi<lo:return None
    return lo if hi>=0 and lo<=1 else None

class ForestController(MapController):
    def __init__(self,pack=None,seed=0,kind='mid'):
        self.pack=pack or ForestPack(kind,seed=seed);self.kind=self.pack.kind
        self.name='forest_'+self.kind;self.seed=seed;self.prepare()

    def prepare(self):
        self.rng=random.Random(self.seed);self.time=0.;self.state='waiting';self.phase=0
        self.players={};self.events=[];self.actors={};self.next_id=2000;self.next_wire=0.
        self.seen_damage=set();self.retired=set();self.pending=[];self.damage=0.;self.timer_start=None
        self.phase_start=0.;self.next_attack=0.;self.action='idle';self.motion=None
        self.next_slam=math.inf;self.next_turn=0.;self.direction=-1;self.next_bug=0.;self.next_fire=0.
        self.death_at=None;self.kills=0;self.boss_pillar=self.pack.scene.get('boss_pillar',0)
        self.spawn_regions=self.pack.scene.get('spawn_regions',self.pack.scene.get('flats',[]))
        self.platform_next=[self.rng.uniform(.1,.6) for _ in self.spawn_regions]
        self.platform_boxes=[];self.tick_boxes=self.pack.solids
        oid=self.pack.scene['boss_id'];p=self.pack.templates[str(oid)]
        self.boss=dict(id=oid,vid=p['vid'],pos=list(self.pack.rect(p)[:2]),size=list(self.pack.rect(p)[2:]),
                       facing=p.get('facing',0),pose=p.get('pose',1))

    def emit(self,kind,**data):self.events.append(dict(kind=kind,at=self.time,**data))
    def drain(self):events,self.events=self.events,[];return events
    def start(self):
        if self.state=='waiting':self.state='dormant'

    def set_players(self,players):
        self.players={k:[float(x)/256 for x in v['center']] for k,v in players.items() if v.get('alive',True)}

    def target(self,a,distance=math.inf):
        c=center(a);choices=[(math.dist(c,p),p) for p in self.players.values()]
        if not choices:return None
        dist,p=min(choices,key=lambda q:q[0])
        return p if dist<=distance else None

    def face(self,a,sign):
        facing=0 if sign>=0 else 180
        if a['facing']!=facing:
            a['facing']=facing;self.emit('props',id=a['id'],values=dict(FACING=facing))

    def pose(self,a,value):
        if a['pose']!=value:
            a['pose']=value;self.emit('props',id=a['id'],values=dict(POSE=value))

    def anim(self,a,slot,duration):self.emit('anim',id=a['id'],anim=slot,duration=duration)
    def sound(self,a,name):self.emit('sound',id=a['id'],name='forest.'+name)

    def new(self,kind,vid,pos,vel=(0.,0.),life=math.inf,**extra):
        p=self.pack.props(vid);oid=self.next_id;self.next_id+=1
        a=dict(id=oid,kind=kind,vid=vid,pos=list(pos),size=list(self.pack.rect(p)[2:]),vel=list(vel),
               hp=p.get('hitpoints'),born=self.time,expiry=self.time+life,grounded=False,
               facing=0,pose=1,next_jump=0.,owner=self.boss['id'],gravity=0.,children=[],patrol_sign=self.rng.choice((-1,1)) if kind=='bug' else 1)
        if kind in ('seed','groundseed','fireball'):a['facing']=180 if vel[0]<0 else 0
        if kind=='firebomb':a['facing']=int(math.degrees(math.atan2(vel[1],vel[0]))+180)%360
        a.update(extra);self.actors[oid]=a;self.emit('create',actor=copy.deepcopy(a));return a

    def remove(self,a,reason='INTERNAL'):
        if a['id'] in self.retired:return
        self.retired.add(a['id']);self.actors.pop(a['id'],None)
        self.emit('remove',actor=copy.deepcopy(a),reason=reason)
        for oid in a.get('children',[]):
            if oid in self.actors:self.remove(self.actors[oid],reason='INTERNAL')
        if a['kind'] in ('frog','bug') and reason=='HITPOINTS':self.kills+=1

    def add_damage(self,value,objid=None,player_id=None,event_id=None):
        if not math.isfinite(value) or value<=0:return []
        if event_id is not None:
            key=(player_id,event_id)
            if key in self.seen_damage:return []
            self.seen_damage.add(key)
        if objid==self.boss['id']:
            if self.state not in ('dormant','fighting') or self.action in ('cocoon','death_seek','death_burst','death_fall'):return []

            self.damage=min(self.pack.entities['boss']['required_damage'],self.damage+value)
            if self.timer_start is None:self.timer_start=self.time;self.emit('timer_started',phase=self.phase)
        elif objid in self.actors:
            a=self.actors[objid]
            if a['kind'] in ('frog','bug'):
                a['hp']-=value
                if a['hp']<=0:self.remove(a,'HITPOINTS')
        return self.drain()

    def native_destroy(self,objid,reason):
        a=self.actors.get(objid)
        if a:
            if a['kind'] in ('frog','bug') and reason==2:self.remove(a,'HITPOINTS')
            elif a['kind'] in ('seed','groundseed','part','flame','fireball','firebomb') and reason in (1,2,3,4,5,6,7):
                if reason==5:self.impact(a)
                else:self.remove(a,{1:'INTERNAL',2:'HITPOINTS',3:'SHIELD',4:'DAMAGES',6:'LIFETIME',7:'CANCEL'}[reason])
        return self.drain()

    def schedule(self,delay,kind,**data):self.pending.append(dict(at=self.time+delay,kind=kind,**data))

    def ready(self):
        if self.damage<self.pack.entities['boss']['required_damage'] or self.timer_start is None:return False
        minimum=self.pack.entities['boss']['min_s'] if self.kind=='mid' else self.pack.entities['boss']['phase_min_s'][self.phase]
        return self.time-self.timer_start>=minimum-1e-8

    def activate(self):
        self.state='fighting';self.next_attack=self.time+.25;self.emit('activated')

    def mid_attack(self):
        r=self.pack.entities['boss'];p=self.target(self.boss);c=center(self.boss)
        distance=math.dist(c,p) if p else math.inf
        mode='frog' if distance>r['mid_medium_px'] else ('groundseed' if distance<=r['mid_near_px'] else 'seed')
        if p:self.face(self.boss,p[0]-c[0])
        self.anim(self.boss,2 if mode=='frog' else 3,2.178)
        self.emit('mid_attack',mode=mode,distance=distance if math.isfinite(distance) else None)
        for i in range(3):self.schedule(.660+i*.330,'mid_emit',mode=mode,index=i)
        self.next_attack=self.time+r['mid_attack_period_s']

    def mid_emit(self,e):
        b=self.boss;sign=1 if b['facing']==0 else -1
        offsets=[(17,39),(20,57),(28,32)];dx,dy=offsets[e['index']]
        pos=[b['pos'][0]+(b['size'][0]-dx-32 if sign>0 else dx),b['pos'][1]+dy]
        mode=e['mode'];r=self.pack.entities['projectile']
        if mode=='frog':
            if sum(a['kind']=='frog' for a in self.actors.values())>=self.pack.entities['npc']['frog_cap']:return
            self.new('frog','forest.yjball',pos,(sign*200,-190),gravity=700.)
        elif mode=='seed':

            up=self.rng.uniform(320,410)
            a=self.new('seed','forest.gmseed',pos,(sign*self.rng.uniform(190,270),-up),
                       life=up/r['seed_gravity_px_s2']+self.rng.uniform(.12,.35),gravity=r['seed_gravity_px_s2'])
            self.sound(a,'gthrow')
        else:
            a=self.new('groundseed','forest.mbseed',pos,(sign*self.rng.uniform(65,110),-175),life=8,gravity=228.8818359375)
            self.sound(a,'gthrow')

    def count_bugs(self):return sum(a['kind']=='bug' for a in self.actors.values())

    def spawn_platform(self,region):
        r=self.pack.entities['platforms'];x,y,w,h=self.spawn_regions[region]

        size=self.rng.choice(((8,8),(8,8),(12,12),(12,12),(48,48),(48,64),(64,48),(64,64)))
        vid='forest.rock%dx%d'%size;speed=self.rng.uniform(r['speed_min_px_s'],r['speed_max_px_s'])
        a=self.new('platform',vid,[self.rng.uniform(x+8,x+w-size[0]-8),y+6],life=r['life_s'],
                   region=region,rise_speed=speed,origin_y=y+6,accelerated=False,previous_y=y+6,
                   owner=0,
                   properties=dict(move=dict(vel=dict(yvel=0,yaccel=-5,minyvel=-round(speed*65536/1000)))))
        a['ceiling']=max((sy+sh for sx,sy,sw,sh in self.pack.solids
                          if a['pos'][0]+size[0]>sx and a['pos'][0]<sx+sw and sy+sh<y-128),default=-1600)
        self.emit('platform_spawn',id=a['id'],region=region,solid=size[0]>=48,speed=speed)
        return a

    def platforms_step(self):
        if self.kind!='boss':return
        r=self.pack.entities['platforms']
        if self.state!='waiting':
            for i,when in enumerate(self.platform_next):
                if self.time>=when:
                    if sum(a['kind']=='platform' and a['region']==i for a in self.actors.values())<r['cap_per_region']:
                        self.spawn_platform(i)
                    self.platform_next[i]=self.time+r['interval_s']*self.rng.uniform(.75,1.25)
        self.platform_boxes=[]
        for a in list(self.actors.values()):
            if a['kind']!='platform':continue
            age=self.time-a['born'];a['previous_y']=a['pos'][1]
            first=min(age,2);y=a['origin_y']+r['initial_accel_px_s2']*first*first/2
            v=r['initial_accel_px_s2']*first
            if age>=2:
                t=age-2;reach=max(0,(-a['rise_speed']-v)/r['accel_px_s2']);t1=min(t,reach)
                y+=v*t1+r['accel_px_s2']*t1*t1/2-a['rise_speed']*max(0,t-reach)
                if not a['accelerated']:
                    a['accelerated']=True
                    self.emit('platform_accelerate',id=a['id'],speed=a['rise_speed'])

                if y<=a['ceiling']:
                    self.remove(a,'BLOCKED');continue
            a['pos'][1]=y
            if a['size'][0]>=48:self.platform_boxes.append((a['id'],a['pos'][0],y,*a['size'],a['previous_y']))

        self.tick_boxes=self.pack.solids

    def bugs(self,air=False):
        cap=self.pack.entities['npc']['bug_cap'];b=self.boss
        for dx in (72,152):
            if self.count_bugs()>=cap:break

            self.new('bug','forest.gobling',[b['pos'][0]+dx,b['pos'][1]+b['size'][1]-35],
                     (0,0),gravity=self.pack.entities['npc']['gravity_px_s2'])
        self.emit('bug_drop',air=air,count=self.count_bugs())

    def fire(self,air=False,slot=2):
        b=self.boss;r=self.pack.entities['projectile'];target=self.target(b)
        pos=[b['pos'][0]+(240 if b['facing']==0 else 48),b['pos'][1]+(180 if air else 128)]
        if air:
            p=target or [pos[0],pos[1]+500];dx,dy=p[0]-pos[0],max(100,p[1]-pos[1]);n=math.hypot(dx,dy)
            self.new('firebomb','forest.firebomb',pos,(dx/n*r['firebomb_speed_px_s'],dy/n*r['firebomb_speed_px_s']),life=12)
        else:
            dx=(target[0]-pos[0]) if target else (-500 if b['facing']==180 else 500)
            speed=r['fireball_speed_px_s']
            vx=max(-380,min(380,dx/2+self.rng.uniform(-80,80)));vy=-math.sqrt(max(0,speed*speed-vx*vx))
            a=self.new('fireball','forest.fireball',pos,(vx,vy),life=12,gravity=r['fireball_gravity_px_s2'],
                       max_fall=r['fireball_max_fall_px_s'])
            self.sound(a,'gmlaunch')
            owner=a['id']
            for size in (28,24,20,16,12):
                child=self.new('tail','forest.fbtail'+str(size),pos,life=12,follow=owner)
                a['children'].append(child['id']);owner=child['id']
        self.emit('boss_fire',air=air)

    def choose_pillar(self,adjacent=False):
        pillars=self.pack.scene['pillars'];p=self.target(self.boss)
        candidates=[i for i in range(len(pillars)) if not adjacent or abs(i-self.boss_pillar)==1]
        if not candidates:return self.boss_pillar
        if p:
            candidates.sort(key=lambda i:abs(pillars[i][0]+pillars[i][2]/2-p[0]))
            return candidates[0] if self.rng.random()<self.pack.entities['boss']['jump_near_weight'] else candidates[-1]
        return self.rng.choice(candidates)

    def begin_motion(self,destination,duration,mode,arc=0):
        if mode in ('slam_down','slam_up'):
            r=self.pack.entities['boss'];down=mode=='slam_down'
            start=r['slam_start_px_s'] if down else r['slam_up_start_px_s']
            increment=r['slam_step_px_s'] if down else r['slam_up_step_px_s']
            distance=math.dist(self.boss['pos'],destination);origin=list(self.boss['pos'])
            points=[];cumulative=0
            for i in range(10):
                milliseconds=max(1,round(1000*distance/10/(start+increment*i)))
                cumulative+=milliseconds/1000
                pos=[origin[j]+(destination[j]-origin[j])*(i+1)/10 for j in (0,1)]
                points.append(dict(pos=pos,dt_ms=milliseconds,until=cumulative))
            self.motion=dict(start=self.time,origin=origin,destination=list(destination),duration=cumulative,mode=mode,arc=0,points=points)
            self.action=mode;self.pose(self.boss,1)
            self.emit('motion_path',id=self.boss['id'],points=copy.deepcopy(points),mode=mode,duration=cumulative)
            return
        self.motion=dict(start=self.time,origin=list(self.boss['pos']),destination=list(destination),duration=duration,mode=mode,arc=arc)
        self.action=mode;self.pose(self.boss,1)

    def ground_attack(self):
        b=self.boss;r=self.pack.entities['boss'];p=self.target(b)
        if p:self.face(b,p[0]-center(b)[0])
        choices=['lob','jump']+(['bugs'] if self.count_bugs()<self.pack.entities['npc']['bug_cap'] else [])
        mode=self.rng.choice(choices)
        if mode=='jump':
            index=self.choose_pillar(adjacent=True);px,py,pw=self.pack.scene['pillars'][index]
            self.face(b,px-64-b['pos'][0]);self.anim(b,8,r['jump_windup_s'])
            self.schedule(r['jump_windup_s'],'jump',index=index)
            self.next_attack=self.time+r['jump_windup_s']+r['jump_s']+r['ground_recovery_s']
        elif mode=='bugs':
            self.anim(b,1,1.518);self.schedule(.528,'bugs');self.next_attack=self.time+1.518
        else:
            slots=[2,3,4,5];self.rng.shuffle(slots)
            for i,slot in enumerate(slots):self.schedule(i*.69,'lob',slot=slot)
            self.next_attack=self.time+4*.69+r['ground_recovery_s']
        self.emit('ground_attack',mode=mode)

    def phase_transition(self):
        b=self.boss;self.pending=[]
        if self.phase==0:
            self.action='cocoon';self.until=self.time+self.pack.entities['boss']['cocoon_s'];self.motion=None
            b['vid']='forest.goblinmotherf';self.damage=0;self.timer_start=None
            self.emit('props',id=b['id'],values=dict(VID=b['vid'],INVULNERABLE=True,HITPOINTS=1000,POSE=1))
            self.anim(b,1,self.pack.entities['boss']['cocoon_s']);self.emit('cocoon')
        elif self.phase==1:
            self.phase=2;self.phase_start=self.time;self.timer_start=self.time;self.damage=0
            self.emit('props',id=b['id'],values=dict(HITPOINTS=1000))
            self.begin_motion([b['pos'][0],self.flight_height(2)],2.2,'rise_high')
            self.emit('phase',phase=2);self.next_slam=self.time+7
        else:self.begin_death()

    def flight_height(self,phase):

        return min(p[1] for p in self.pack.scene['pillars'])-320-(96 if phase==1 else 416)

    def begin_flight(self):
        b=self.boss;self.phase=1;self.phase_start=self.time;self.timer_start=self.time;self.damage=0
        self.emit('props',id=b['id'],values=dict(INVULNERABLE=False,HITPOINTS=1000,POSE=1))
        duration=max(.4,abs(self.flight_height(1)-b['pos'][1])/91.552734375)
        self.begin_motion([b['pos'][0],self.flight_height(1)],duration,'rise_low')
        self.next_bug=self.time+.99;self.next_fire=self.time+2.0;self.next_turn=self.time+duration
        self.emit('phase',phase=1)

    def move_boss(self,dt):
        b=self.boss
        if not self.motion:return
        m=self.motion;u=min(1,(self.time-m['start'])/m['duration'])
        if 'points' in m:
            elapsed=self.time-m['start'];last=m['origin'];at=0.
            for point in m['points']:
                if elapsed<=point['until']:
                    f=min(1,max(0,(elapsed-at)/(point['until']-at)))
                    b['pos']=[last[j]+(point['pos'][j]-last[j])*f for j in (0,1)];break
                last=point['pos'];at=point['until']
        else:
            b['pos']=[m['origin'][i]+(m['destination'][i]-m['origin'][i])*u for i in (0,1)]
            b['pos'][1]-=m['arc']*4*u*(1-u)
        if u<1-1e-8:return
        b['pos']=m['destination'];self.motion=None;mode=m['mode']
        if 'points' in m:self.emit('motion_path_end',id=b['id'])
        if mode=='jump':self.action='idle';self.pose(b,4);self.sound(b,'gmland')
        elif mode in ('rise_low','rise_high','slam_up'):
            self.action='flying';self.next_turn=self.time;self.pose(b,1)
        elif mode=='slam_down':
            self.sound(b,'gmland');self.emit('shake',magnitude=3,duration=300);self.emit('slam_land',pillar=self.slam_pillar)
            self.begin_motion([b['pos'][0],self.flight_height(2)],2.4,'slam_up')
        elif mode=='death_seek':
            self.action='death_burst';self.until=self.time+.594;self.anim(b,4,.594);self.emit('death_burst')
        elif mode=='death_fall':self.finish_death()

    def air_step(self,dt):
        r=self.pack.entities['boss'];b=self.boss
        if self.time>=self.next_fire:
            self.anim(b,2,1.188);self.schedule(.792,'fire',air=True)
            self.next_fire=self.time+r['air_attack_s']
        if self.time>=self.next_bug:
            if self.count_bugs()<self.pack.entities['npc']['bug_cap']:
                self.anim(b,3,1.188);self.schedule(.990,'bugs',air=True)
            self.next_bug=self.time+r['air_attack_s']
        if self.action!='flying':return
        if self.time>=self.next_turn:
            p=self.target(b);sign=1 if p and p[0]>center(b)[0] else -1

            self.direction=-self.direction
            duration=r['flight_leg_s']*self.rng.uniform(.75,1.15)
            if self.direction==sign:duration*=1.15
            self.next_turn=self.time+duration;self.face(b,self.direction)
        speed=r['low_speed_px_s'] if self.phase==1 else r['high_speed_px_s']
        b['pos'][0]+=self.direction*speed*dt
        lo,hi=self.pack.scene['left'],self.pack.scene['right']-320
        if b['pos'][0]<lo or b['pos'][0]>hi:
            b['pos'][0]=max(lo,min(hi,b['pos'][0]));self.next_turn=self.time
        if self.phase==2 and self.time>=self.next_slam:
            self.slam_pillar=self.choose_pillar();px,py,pw=self.pack.scene['pillars'][self.slam_pillar]
            self.begin_motion([px-64,py-320],1.45,'slam_down')
            self.next_slam=self.time+r['slam_period_s'];self.emit('slam_begin',pillar=self.slam_pillar)

    def begin_death(self):
        self.pending=[];self.state='dying';self.emit('props',id=self.boss['id'],values=dict(INVULNERABLE=True,DAMAGE=0,HITBOXDAMAGE=0))
        for a in list(self.actors.values()):
            if a['kind']!='platform':self.remove(a)
        self.emit('death_begin',phase=self.phase)
        if self.kind=='mid':
            self.emit('boss_destroy',id=self.boss['id'],reason='INTERNAL');self.death_at=self.time+.924;self.action='mid_death'
        else:
            index=self.choose_pillar();px,py,pw=self.pack.scene['pillars'][index];self.death_landing=[px-64,py-320]
            self.begin_motion([px-64,py-416],1.2,'death_seek')

    def finish_death(self):
        self.emit('boss_destroy',id=self.boss['id'],reason='BLOCKED')
        self.new('corpse','forest.bosscorpse',self.death_landing,owner=0)
        self.death_at=self.time+.858;self.action='death_wait'

    def dispatch(self,e):
        kind=e['kind']
        if kind=='mid_emit':self.mid_emit(e)
        elif kind=='bugs':self.bugs(e.get('air',False))
        elif kind=='lob':self.anim(self.boss,e['slot'],.660);self.schedule(.132,'fire',air=False,slot=e['slot'])
        elif kind=='fire':self.fire(e.get('air',False),e.get('slot',2))
        elif kind=='jump':
            previous=self.boss_pillar;self.boss_pillar=e['index'];px,py,pw=self.pack.scene['pillars'][self.boss_pillar]
            self.begin_motion([px-64,py-320],self.pack.entities['boss']['jump_s'],'jump',arc=192)
            self.sound(self.boss,'gmjump');self.emit('jump',source=previous,destination=self.boss_pillar)

    def impact(self,a):
        if a['id'] not in self.actors:return
        c=center(a);kind=a['kind'];r=self.pack.entities['projectile'];self.remove(a,'BLOCKED')
        if kind=='seed':
            self.new('fx','forest.gmseedx',[c[0]-32,c[1]-32],life=.25)
            base=self.rng.random()*math.tau
            for i in range(5):
                angle=base+i*math.tau/5
                self.new('part','forest.gmseedpart',[c[0]-10,c[1]-10],
                         [math.cos(angle)*r['part_speed_px_s'],math.sin(angle)*r['part_speed_px_s']],life=r['part_life_s'])
            self.emit('seed_split',id=a['id'],parts=5)
        elif kind=='groundseed':
            for sign in (-1,1):
                f=self.new('flame','forest.lpflame',[c[0]-16,c[1]-32],(sign*r['ground_fire_speed_px_s'],-60),
                           life=r['ground_fire_life_s'],gravity=350.)
                self.face(f,sign)
            self.sound(f,'lpseedsplit');self.emit('ground_split',id=a['id'])
        elif kind in ('fireball','firebomb'):
            fx=self.new('fx','forest.firebombx',[c[0]-48,c[1]-48],life=r['explosion_s'])
            self.sound(fx,'firex')
            self.emit('fire_explosion',id=a['id'])

    def ground(self,a,dt):
        x,y=a['pos'];w,h=a['size'];vx,vy=a['vel'];nx=x+vx*dt
        if a.get('riding') is not None:
            support=self.actors.get(a['riding'])
            if support and x+w>support['pos'][0] and x<support['pos'][0]+support['size'][0]:
                y+=support['pos'][1]-support['previous_y']
            else:a['riding']=None
        for sx,sy,sw,sh in self.pack.solids:
            if y+h>sy+.01 and y<sy+sh-.01:
                if vx>0 and x+w<=sx+.01 and nx+w>sx:nx=sx-w
                if vx<0 and x>=sx+sw-.01 and nx<sx+sw:nx=sx+sw
        vy+=a['gravity']*dt;ny=y+vy*dt;was_grounded=a['grounded'];a['grounded']=False
        if vy>=0:
            floors=[sy for sx,sy,sw,sh in self.pack.solids if nx+w>sx+.01 and nx<sx+sw-.01 and y+h<=sy+.1 and ny+h>=sy]
            platforms=[(sy,oid) for oid,sx,sy,sw,sh,old in self.platform_boxes
                       if nx+w>sx+.01 and nx<sx+sw-.01 and y+h<=old+.5 and ny+h>=sy]
            if platforms:
                py,oid=min(platforms)
                if not floors or py<=min(floors):floors=[py];a['riding']=oid
            if floors:ny=min(floors)-h;vy=0.;a['grounded']=True
        else:
            ceilings=[sy+sh for sx,sy,sw,sh in self.pack.solids if nx+w>sx+.01 and nx<sx+sw-.01 and y>=sy+sh-.1 and ny<=sy+sh]
            if ceilings:ny=max(ceilings);vy=0.
        a['pos']=[nx,ny];a['vel'][1]=vy
        self.resolve_embedded(a)
        if a['kind']=='frog' and a['grounded'] and not was_grounded:
            a['next_jump']=self.time+self.pack.entities['npc']['frog_rest_s'];self.pose(a,1)

    def resolve_embedded(self,a):
        """Recover an overlapping birth/diagonal corner without trapping a hopper."""
        x,y=a['pos'];w,h=a['size']
        def overlaps(pos,box):
            sx,sy,sw,sh=box
            return pos[0]+w>sx+.05 and pos[0]<sx+sw-.05 and pos[1]+h>sy+.05 and pos[1]<sy+sh-.05
        contacts=[box for box in self.pack.solids if overlaps(a['pos'],box)]
        if not contacts:return
        candidates=[]

        for sx,sy,sw,sh in self.pack.solids:
            if x+w>sx and x<sx+sw:candidates.extend(([x,sy-h],[x,sy+sh]))
            if y+h>sy and y<sy+sh:candidates.extend(([sx-w,y],[sx+sw,y]))
        valid=[p for p in candidates if self.pack.scene['left']<=p[0]<=self.pack.scene['right']-w
               and not any(overlaps(p,b) for b in self.pack.solids)]
        if not valid:return
        p=min(valid,key=lambda p:math.dist(p,[x,y]));a['pos']=list(p)
        if p[1]!=y:
            a['vel'][1]=0
            a['grounded']=p[1]<y
        if p[0]!=x:a['vel'][0]=0
        self.emit('terrain_recovery',id=a['id'],pos=list(p))

    def actors_step(self,dt):
        for a in list(self.actors.values()):
            if a['id'] not in self.actors:continue
            kind=a['kind']
            if self.time>=a['expiry']:
                if kind=='seed':self.impact(a)
                else:self.remove(a,'LIFETIME')
                continue
            if kind in ('frog','bug'):
                r=self.pack.entities['npc'];p=self.target(a,r['acquire_px'])
                if a['grounded']:
                    a['vel'][0]=0
                    if p:
                        sign=1 if p[0]>center(a)[0] else -1;self.face(a,sign)
                        if kind=='frog' and self.time>=a['next_jump']:
                            a['vel']=[sign*r['speed_px_s'],-r['frog_jump_px_s']];self.pose(a,3);self.sound(a,'jumperjump')
                        elif kind=='bug':a['vel'][0]=sign*r['speed_px_s']
                    elif kind=='bug':
                        a['vel'][0]=a['patrol_sign']*r['speed_px_s']
                        self.face(a,a['patrol_sign'])
                elif kind=='bug' or p is None:a['vel'][0]=0
                old_x=a['pos'][0];intended=a['vel'][0]
                self.ground(a,dt)
                if kind=='bug' and a['grounded'] and p is None and abs(intended)>0 and abs(a['pos'][0]-old_x)<abs(intended*dt)*.1:
                    a['patrol_sign']*=-1
            elif kind=='flame':self.ground(a,dt)
            elif kind=='tail':
                owner=self.actors.get(a['follow'])
                if not owner:self.remove(a);continue
                target=center(owner);c=center(a);d=math.dist(c,target)
                if d>20:a['pos']=[a['pos'][i]+(target[i]-c[i])*(d-20)/d for i in (0,1)]
            elif kind in ('seed','groundseed','part','fireball','firebomb'):
                a['vel'][1]+=a['gravity']*dt
                if 'max_fall' in a:a['vel'][1]=min(a['max_fall'],a['vel'][1])
                end=[a['pos'][i]+a['vel'][i]*dt for i in (0,1)]
                hits=[h for box in self.tick_boxes if (h:=swept_box(a['pos'],end,a['size'],box)) is not None]
                if hits:
                    u=min(hits);a['pos']=[a['pos'][i]+(end[i]-a['pos'][i])*max(0,u-.001) for i in (0,1)];self.impact(a)
                else:a['pos']=end
            if a['pos'][1]>self.pack.scene['floor']+512:self.remove(a,'LIFETIME')

    def tick(self,dt):
        self.time+=dt
        self.platforms_step()
        if self.kind=='mid':self.tick_boxes=self.pack.solids
        if self.state=='dormant' and self.target(self.boss,self.pack.entities['boss']['acquire_px']):self.activate()
        self.move_boss(dt)
        if self.state=='fighting':
            if self.kind=='mid':
                if self.ready():self.begin_death()
                elif self.time>=self.next_attack:self.mid_attack()
            elif self.action=='cocoon':
                if self.time>=self.until:self.begin_flight()
            elif self.ready() and self.action not in ('jump','slam_down','slam_up'):
                self.phase_transition()
            elif self.phase==0:
                if self.time>=self.next_attack and self.motion is None:self.ground_attack()
            else:self.air_step(dt)
        if self.state=='dying':
            if self.action=='death_burst' and self.time>=self.until:
                self.begin_motion(self.death_landing,.55,'death_fall')
            if self.death_at is not None and self.time>=self.death_at:
                self.state='done';self.emit('victory')
        ready=[e for e in self.pending if e['at']<=self.time+1e-9]
        self.pending=[e for e in self.pending if e['at']>self.time+1e-9]
        for e in sorted(ready,key=lambda e:e['at']):
            if self.state=='fighting' and self.action!='cocoon':self.dispatch(e)
        self.actors_step(dt)
        if self.time>=self.next_wire:
            self.next_wire=self.time+.05
            if (self.state not in ('dying','done') or self.action in ('death_seek','death_fall')) and not (self.motion and 'points' in self.motion):
                self.emit('move',id=self.boss['id'],pos=list(self.boss['pos']))
            for a in self.actors.values():
                if a['kind'] not in ('fx','corpse','platform'):self.emit('move',id=a['id'],pos=list(a['pos']))

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        if self.state=='waiting':return self.drain()
        while dt>1e-9:
            chunk=min(dt,1/120);self.tick(chunk);dt-=chunk
        return self.drain()

    def status(self):
        return dict(state=self.state,phase=self.phase,action=self.action,time=self.time,damage=self.damage,
                    timer_start=self.timer_start,boss=copy.deepcopy(self.boss),bugs=self.count_bugs(),
                    actors=len(self.actors),kills=self.kills,boss_pillar=self.boss_pillar)
