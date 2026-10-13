'Live formation, contact and split rules; independent of runtime frames.'
import math
import random
from ..dumb_common.physics import sweep_contact
from .scene import DumbMidPack

def formation(shape,count):
    d=16384
    if shape=='square':return [(0,0),(d,0),(0,d),(d,d)]
    if shape=='triangle':return [(0,d),(d,0),(2*d,d)]
    return [(i*d,0)if shape=='horizontal'else(0,i*d)for i in range(count)]

def triangle_path(start,finish,speed=78125.):
    'Derived original rule: 1/8, 1/3, full distance at 3/8, 1, 2 speed.'
    distance=math.dist(start,finish)
    fractions=(.125,1/3,1.)
    durations=(distance/(3*speed),distance*5/(24*speed),distance/(3*speed))
    return [dict(xmp=math.floor((start[0]+(finish[0]-start[0])*f)/256)*256,
                 ymp=math.floor((start[1]+(finish[1]-start[1])*f)/256)*256,
                 dt=max(1,int(seconds*1000)))for f,seconds in zip(fractions,durations)]

def shake_path(origin,index):
    'Two 50-ms legs; all 120 runtime shake pulses match this geometry.'
    x,y=(math.floor(v/256)*256 for v in origin)
    return [dict(xmp=x-256-index*1024,ymp=math.floor((y-index*100)/256)*256,dt=50),
            dict(xmp=x,ymp=y,dt=50)]

class DumbMidController:
    def __init__(self,pack=None,seed=73):
        self.pack=pack or DumbMidPack();self.config=self.pack.betty;self.rng=random.Random(seed)
        self.time=0.;self.state='waiting';self.events=[];self.parts={};self.group=[];self.fragments=set()
        self.cycle=0;self.stage=4;self.until=math.inf;self.next_id=100
        self.player=(192512,110000);self.shape=None;self.geometry=[];self.complete=False

    def emit(self,kind,**values):self.events.append(dict(kind=kind,at=self.time,**values))
    def take_events(self):result,self.events=self.events,[];return result

    @property
    def damage(self):
        """Diagnostic sum only: progression checks every block separately."""
        return sum(self.parts[i]['damage'] for i in self.group)

    def remaining_damage(self):
        goal=self.config['piece_damage_gate']
        return {i:max(0,goal-self.parts[i]['damage']) for i in self.group}

    def _piece(self,identity,tier,pos,invulnerable=False,angle=None):
        size=[32768,16384,8192][tier];hp=self.config['split_hp'][tier]
        if tier==0:
            velocity=self._scatter_velocity()
        else:
            angle=self.rng.uniform(0,2*math.pi)if angle is None else angle
            speed=self.rng.uniform(*self.config['fragment_speed_ranges_mp_s'][str(tier)])
            velocity=[math.cos(angle)*speed,math.sin(angle)*speed]
        protection=self.config['fragment_scatter_grace_s']if tier>0 else 0.
        invulnerable=invulnerable or protection>0
        p=dict(id=identity,tier=tier,pos=list(pos),size=size,hp=hp,invulnerable=invulnerable,
            vel=velocity,pose=1,damage=0.,
            vulnerable_at=self.time+protection if protection else None)
        self.parts[identity]=p
        self.emit('create_piece',identity=identity,tier=tier,pos=list(pos),velocity=list(velocity),invulnerable=invulnerable,hp=hp)
        return p

    def _scatter_velocity(self):

        return [self.rng.choice((-1,1))*self.config['scatter_axis_speed'] for _ in (0,1)]

    def begin(self):
        self.group=[20,21,22,23]
        for identity in self.group:
            self._piece(identity,0,(self.rng.uniform(40000,310000),self.rng.uniform(-10000,70000)),True)
        self._assemble()
        return self.take_events()

    def _assemble(self):
        self.state='assembling';self.stage=len(self.group)
        self.rng.shuffle(self.group)
        self.shape=self.rng.choice(self.config['shape_pools'][str(self.stage)])
        self.offsets=formation(self.shape,self.stage)
        self.size=[32768+max(o[j]for o in self.offsets)for j in (0,1)]
        lo,top,hi,floor=self.pack.scene['playable_bounds_mp']
        self.anchor=[self.rng.uniform(lo+16384,hi-self.size[0]-16384),self.rng.uniform(top+8192,40000)]
        self.assembly={}
        for index,(identity,offset)in enumerate(zip(self.group,self.offsets)):
            p=self.parts[identity];p['invulnerable']=True
            target=[self.anchor[j]+offset[j]for j in (0,1)]
            delay=self.config['initial_assemble_delay_s']if self.cycle==0 else self.config['assemble_first_delay_s']
            self.assembly[identity]=dict(target=target,scheduled=self.time+delay+index*self.config['assemble_stagger_s'],
                start=None,at=None,duration=None)
            self.emit('shield',identity=identity,enabled=True)
        self.until=math.inf
        self.emit('formation',shape=self.shape,count=self.stage,cycle=self.cycle)

    def _assembly_motion(self):
        for identity,plan in self.assembly.items():
            p=self.parts[identity]
            if plan['start']is None:
                if self.time<plan['scheduled']:continue

                plan['start']=list(p['pos']);plan['at']=self.time
                milliseconds=max(1,int(math.dist(p['pos'],plan['target'])/self.config['assemble_speed']*1000))
                plan['duration']=milliseconds/1000.
                self.emit('assembly_path',identity=identity,start=plan['start'],
                    points=[dict(xmp=round(plan['target'][0]),ymp=round(plan['target'][1]),dt=milliseconds)])
            p['pose']=1
            fraction=min(1.,(self.time-plan['at'])/plan['duration'])
            p['pos']=[a+(b-a)*fraction for a,b in zip(plan['start'],plan['target'])]
        if all(p['start']is not None for p in self.assembly.values()):
            self.until=max(p['at']+p['duration']for p in self.assembly.values())+self.config['assemble_settle_s']
        if self.time>=self.until:self._activate()

    def _activate(self):
        self.state='active';self.active_start=self.time
        duration=self.config['introduction_active_s']if self.cycle==0 else self.config['active_s']
        self.until=self.time+duration;self.direction=1 if self.player[0]>=self.anchor[0]+self.size[0]/2 else -1
        self.velocity=[self.direction*self.config['speed'],self.rng.choice((-1,1))*self.config['speed']]
        self.mode='lead';self.mode_until=self.time+(3. if self.shape=='triangle'else 2.1)
        self.home_y=self.anchor[1];self.jump_vy=0.;self.ball_due=math.inf
        self.attacks=0;self.fall_vy=0.;self.first_drop=True
        for identity in self.group:

            p=self.parts[identity];p['invulnerable']=False
            p['damage']=0.;p['hp']=self.config['piece_damage_gate']
            self.emit('shield',identity=identity,enabled=False)
            self.emit('hp',identity=identity,value=p['hp'])
        self.emit('active',shape=self.shape,count=self.stage,damage=self.damage,duration=duration)

    def _sweep(self,pos,size,vel,dt,bounce=True):
        remaining=dt;pos=list(pos);vel=list(vel);hit=set()
        for _ in range(6):
            end=[pos[j]+vel[j]*remaining for j in (0,1)]
            contacts=[c for rect in self.geometry if(c:=sweep_contact(pos,end,size,rect))is not None]
            if not contacts:pos=end;break
            fraction=min(c[0]for c in contacts)
            axes={axis for t,normal in contacts if abs(t-fraction)<1e-8 for axis in normal}
            pos=[pos[j]+(end[j]-pos[j])*fraction for j in (0,1)]
            for axis in axes:
                hit.add(axis);vel[axis]=-vel[axis]if bounce else 0.
                if vel[axis]:pos[axis]+=math.copysign(.001,vel[axis])
            remaining*=1-fraction
            if remaining<1e-9:break
        return pos,vel,hit

    def _triangle_travel(self):
        lo,_,hi,_=self.pack.scene['playable_bounds_mp']
        target=[self.rng.uniform(lo+16384,hi-self.size[0]-16384),self.home_y]
        self.travel_start=list(self.anchor);self.travel_at=self.time
        self.travel_points=triangle_path(self.anchor,target,self.config['triangle_cruise_speed'])
        self.travel_duration=sum(p['dt']for p in self.travel_points)/1000.
        self.mode='triangle_move';self.drop_at=None
        self.emit('triangle_path',start=list(self.anchor),points=self.travel_points,
            members=list(self.group),offsets=list(self.offsets))

    def _formation_motion(self,dt):
        speed=self.config['speed'];shape=self.shape;pose=3
        if shape=='square' or (shape=='triangle'and self.mode=='lead'):
            self.anchor,self.velocity,_=self._sweep(self.anchor,self.size,self.velocity,dt)
            self.direction=1 if self.velocity[0]>0 else -1
            if shape=='triangle'and self.time>=self.mode_until:
                self._triangle_travel()
        elif shape=='triangle':
            pose=1
            if self.mode=='triangle_pause'and self.drop_at is not None and self.time>=self.drop_at:
                self.emit('balls',count=self.rng.randint(*self.config['triangle_drop_count']),x=self.anchor[0],y=self.anchor[1]+24576,width=self.size[0])
                self.attacks+=1;self.drop_at=None
            if self.mode=='triangle_pause'and self.time>=self.mode_until:
                self._triangle_travel()
            if self.mode=='triangle_move':
                from ..dumb_common.scene import motion_position
                elapsed=self.time-self.travel_at
                self.anchor=list(motion_position(self.travel_start,dict(waypoint=dict(points=self.travel_points)),elapsed))
                if elapsed>=self.travel_duration:
                    self.mode='triangle_pause';self.mode_until=self.time+self.config['triangle_pause_s']
                    self.drop_at=self.time+self.config['triangle_drop_delay_s']
                    self.emit('triangle_arrive',position=list(self.anchor))
        elif shape=='horizontal':
            if self.mode=='lead':
                self.anchor,v,hit=self._sweep(self.anchor,self.size,[self.direction*speed,0],dt,False)
                pose=2
                if 0 in hit:

                    self.direction*=-1
                    self.mode='pause';self.mode_until=self.time+self.config['horizontal_repeat_pause_s']
                    self.emit('horizontal_wall_drop',position=list(self.anchor))
                elif self.time>=self.mode_until:
                    self.mode='pause'
                    self.mode_until=self.time+(self.config['horizontal_first_pause_s']if self.first_drop else self.config['horizontal_repeat_pause_s'])
            elif self.mode=='pause':
                pose=1
                if self.time>=self.mode_until:
                    self.mode='fall';self.fall_vy=0.;self.first_drop=False
                    self.emit('horizontal_drop',position=list(self.anchor))
            elif self.mode=='fall':
                velocity=min(self.config['terminal_speed'],self.fall_vy+.5*self.config['gravity']*dt)
                self.fall_vy=min(self.config['terminal_speed'],self.fall_vy+self.config['gravity']*dt)
                self.anchor,v,hit=self._sweep(self.anchor,self.size,[0,velocity],dt,False)
                if 1 in hit:
                    self.mode='ground';self.mode_until=self.time+self.config['horizontal_ground_windup_s']
                    self.rise_target=max(self.pack.scene['playable_bounds_mp'][1],self.anchor[1]-self.config['horizontal_rise_height'])
                    self.emit('sound',name='dumb.bettysquash')
                    self.emit('horizontal_landed',position=list(self.anchor))
            elif self.mode=='ground':
                pose=1
                if self.time>=self.mode_until:self.mode='rise'
            elif self.mode=='rise':
                self.anchor[1]=max(self.rise_target,self.anchor[1]-speed*dt)
                if self.anchor[1]<=self.rise_target:
                    self.attacks+=1;self.mode='air_pause';self.mode_until=self.time+.15
                    self.emit('horizontal_cycle_complete',count=self.attacks,position=list(self.anchor))
            elif self.mode=='air_pause':
                pose=1
                if self.time>=self.mode_until:
                    self.mode='lead';self.mode_until=self.time+self.rng.uniform(*self.config['horizontal_traverse_s'])
        else:
            if self.mode=='lead':
                self.anchor,self.velocity,_=self._sweep(self.anchor,self.size,[0,self.velocity[1]],dt)
                if self.time>=self.mode_until:self.mode='pause';self.mode_until=self.time+self.rng.uniform(1.,1.5)
            elif self.mode=='pause':
                pose=1
                if self.time>=self.mode_until:
                    self.mode='hop';self.jump_vy=-self.config['jump_speed'];self.emit('sound',name='dumb.bettypogo')
            elif self.mode=='hop':
                self.jump_vy=min(self.config['terminal_speed'],self.jump_vy+self.config['gravity']*dt)
                before_vy=self.jump_vy
                self.anchor,v,hit=self._sweep(self.anchor,self.size,[self.direction*speed,self.jump_vy],dt,False)
                if 0 in hit:self.direction*=-1
                if 1 in hit:
                    if before_vy>0:
                        self.jump_vy=-self.config['jump_speed'];self.emit('sound',name='dumb.bettypogo')
                        self.attacks+=1
                    else:self.jump_vy=0.
        for identity,offset in zip(self.group,self.offsets):
            p=self.parts[identity];p['pos']=[self.anchor[j]+offset[j]for j in (0,1)];p['pose']=pose

    def _can_finish_attack(self):
        if self.time<self.until:return False
        minimum=self.config['minimum_attacks'].get(self.shape,0)
        if self.attacks<minimum:return False
        if self.shape=='horizontal'and self.mode not in('lead','air_pause'):return False
        if self.shape=='triangle'and (self.mode!='triangle_pause' or self.drop_at is not None):return False
        x,y=self.anchor;w,h=self.size
        if y+h>self.pack.scene['playable_bounds_mp'][3]-self.config['split_floor_clearance']:return False

        clearance=min((sy-y-h for sx,sy,sw,sh in self.geometry
            if x+w>sx and x<sx+sw and sy>=y+h-1),default=100000)
        return clearance>=self.config['split_floor_clearance']

    def _scatter(self):
        remaining_damage=self.remaining_damage()
        ready=bool(remaining_damage) and all(left==0 for left in remaining_damage.values())
        detached=[]
        if ready:
            detached=list(self.group)if len(self.group)==2 else [self.rng.choice(self.group)]
            for identity in detached:
                self.group.remove(identity);p=self.parts[identity];p['invulnerable']=False;p['hp']=2100
                self.fragments.add(identity);self.emit('shield',identity=identity,enabled=False)
                self.emit('hp',identity=identity,value=2100)
        self.cycle+=1
        for identity in self.group:
            self.parts[identity]['invulnerable']=True;self.emit('shield',identity=identity,enabled=True)
        for identity in detached+list(self.group):
            self.parts[identity]['vel']=self._scatter_velocity()
        self.emit('scatter',remaining=len(self.group),detached=detached,damage_met=ready,
            remaining_damage=remaining_damage)
        self.state='scatter'if self.group else 'fragments';self.until=self.time+self.config['scatter_s']

    def damage_piece(self,identity,value):
        p=self.parts.get(identity)
        if p is None or p['invulnerable'] or value<=0:return
        if identity in self.group:
            goal=self.config['piece_damage_gate'];before=p['damage']
            p['damage']=min(goal,before+value)
            if before<goal and p['damage']==goal:
                self.emit('block_drained',identity=identity)

            if p['damage']<goal:p['hp']=goal-p['damage']
            else:p['hp']=self.config['depleted_native_hp']
            self.emit('hp',identity=identity,value=p['hp'])
            return
        p['hp']-=value
        if p['hp']>0:self.emit('hp',identity=identity,value=p['hp']);return
        self.parts.pop(identity);self.fragments.discard(identity);self.emit('destroy_piece',identity=identity)
        tier=p['tier']
        if tier<2:
            count=self.config['split_counts'][tier];newtier=tier+1;size=[32768,16384,8192][newtier]
            for _ in range(count):
                self.next_id+=1;offset=(p['size']-size)/2

                child=self._piece(self.next_id,newtier,[p['pos'][j]+offset for j in (0,1)])
                self.fragments.add(child['id'])
            if tier==0:self.emit('sound',name='dumb.bettysplit')
        self.emit('piece_killed',identity=identity,tier=tier,position=list(p['pos']),size=p['size'])

    def step(self,dt,player=None,geometry=None):
        if not math.isfinite(dt)or dt<0:raise ValueError('dt must be finite and nonnegative')
        if player is not None:self.player=player
        if geometry is not None:self.geometry=geometry
        if not self.geometry:self.geometry=[tuple(p.get(k,0)for k in ('xmp','ymp','wmp','hmp'))for p in self.pack.templates.values()if p.get('terraintype')in(1,2)]
        end=self.time+dt
        while self.time<end-1e-9:
            tick=min(1/120,end-self.time);self.time+=tick
            for p in self.parts.values():
                if p['vulnerable_at']is not None and self.time>=p['vulnerable_at']:
                    p['vulnerable_at']=None;p['invulnerable']=False
                    self.emit('shield',identity=p['id'],enabled=False)
            waiting=[i for i in self.group if self.state=='assembling'and self.cycle>0 and self.assembly[i]['start']is None]
            for identity in list(self.fragments)+(self.group if self.state=='scatter'else waiting):
                p=self.parts[identity];p['pos'],p['vel'],_=self._sweep(p['pos'],[p['size']]*2,p['vel'],tick)
                p['pose']=2 if abs(p['vel'][0])>abs(p['vel'][1])else 3
            if self.state=='assembling':
                self._assembly_motion()
            elif self.state=='active':
                self._formation_motion(tick)
                if self._can_finish_attack():
                    self.state='shake';self.until=self.time+self.config['shake_s'];self.shake_start=self.time
                    self.shake_index=0;self.shake_at=self.time;self.shake_points=None
                    self.emit('breakup_ready',shape=self.shape,attacks=self.attacks,position=list(self.anchor),size=list(self.size))
            elif self.state=='shake':
                from ..dumb_common.scene import motion_position
                if self.shake_index<self.config['shake_pulses']and self.time>=self.shake_at:
                    self.shake_points=shake_path(self.anchor,self.shake_index)
                    lo,top,hi,floor=self.pack.scene['playable_bounds_mp']
                    for point in self.shake_points:
                        point['xmp']=max(lo,min(hi-self.size[0],point['xmp']))
                        point['ymp']=max(top,min(floor-self.size[1],point['ymp']))
                    self.shake_move_at=self.time
                    self.emit('shake_path',start=list(self.anchor),points=self.shake_points,
                        members=list(self.group),offsets=list(self.offsets),pulse=self.shake_index)
                    self.shake_index+=1
                    self.shake_at=self.shake_start+self.shake_index*self.config['shake_s']/self.config['shake_pulses']
                if self.shake_points:
                    pos=motion_position(self.anchor,dict(waypoint=dict(points=self.shake_points)),self.time-self.shake_move_at)
                    for identity,offset in zip(self.group,self.offsets):
                        self.parts[identity]['pos']=[pos[j]+offset[j]for j in (0,1)];self.parts[identity]['pose']=1
                if self.time>=self.until:self._scatter()
            elif self.state=='scatter'and self.time>=self.until:self._assemble()
            if self.state=='fragments'and not self.fragments:
                self.state='done';self.complete=True;self.emit('complete')
        return self.take_events()

    def status(self):
        return dict(state=self.state,time=round(self.time,3),formation=self.shape,big_sections=len(self.group),
            cycle=self.cycle,round_damage=round(self.damage),remaining_damage=self.remaining_damage(),
            fragments=len(self.fragments),complete=self.complete)
