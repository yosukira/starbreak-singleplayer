"""Elite Vanquished Mid: devices, conveyors, reactive jumping and holograms."""
import math
from .common import CityController, center

class CityMidController(CityController):
    KIND='mid'

    def prepare(self):
        super().prepare()
        self.next_cart=[math.inf]*4; self.lane_unlocked=[False]*4; self.next_flyer=.05; self.boss_started=None
        self.motion=None; self.next_clone=math.inf; self.reposition=False

    def on_start(self):
        self.state='devices'
        self.emit('devices_started')

    def gated_damage(self,a,value):
        if a['kind']!='midboss' or self.state in ('dying','done'): return
        r=self.pack.entities['mid']; age=self.time-self.boss_started
        floor=max(0.,r['hp']*(1-age/r['total_min_s']))

        a['hp']=max(floor,a['hp']-value)
        self.props(a,HITPOINTS=max(1,math.ceil(a['hp'])))
        if self.phase==1 and not self.reposition and a.get('landed_once') and self.time>=a['next_jump'] and a['hp']>r['split_hp']:
            self.jump_away()
        if self.phase==2 and age>=r['total_min_s']-1e-8 and a['hp']<=0:
            self.state='dying'; self.death_at=self.time+3.102
            self.world_sound('mbdeath'); self.remove(a,'INTERNAL'); self.emit('mid_death')

    def jump(self,destination,duration):
        b=self.boss; self.motion=dict(start=list(b['pos']),end=list(destination),at=self.time,duration=duration)
        self.pose(b,1); self.sound(b,'mbjump'); b['grounded']=False

    def leap_area(self,t,duration):
        """Cruise, then an exponential tail calibrated to observed braking."""
        r=self.pack.entities['mid'];brake=min(r['jump_brake_s'],duration/2)
        start=duration-brake
        if t<=start:return max(0,t)
        drag=r['jump_drag_s_inv']
        return start+(1-math.exp(-drag*(min(t,duration)-start)))/drag

    def leap_vertical_speed(self,dy,duration):
        gravity=781.25;terminal=self.pack.entities['mid']['fall_terminal_speed']
        vy=dy/duration-gravity*duration/2
        if vy+gravity*duration>terminal:
            vy=terminal-math.sqrt(max(0,2*gravity*(terminal*duration-dy)))
        return vy

    def jump_away(self,a=None):
        a=a or self.boss;gravity=781.25;r=self.pack.entities['mid'];c=center(a);candidates=[]
        source=a.get('hit_source') or self.target(a)
        sign=(1 if c[0]>=source[0] else -1) if source else self.rng.choice((-1,1))
        spacing=r['cart_period_s']*r['cart_speed_px_s']
        minimum=spacing*.75
        available=(2289-a['size'][0]-a['pos'][0]) if sign>0 else a['pos'][0]-48
        edge_limited=available<minimum
        required=max(0,available-40) if edge_limited else minimum
        vertical=self.rng.choice((-1,1))
        for cart in self.actors.values():
            if cart['kind']!='cart' or cart['id']==a.get('attached'):continue
            dy=cart['pos'][1]-a['size'][1]-a['pos'][1]
            if abs(dy)<64:continue
            vy=-math.sqrt(2*gravity*(max(0,-dy)+55)) if dy<0 else 0
            duration=max(.5,(-vy+math.sqrt(max(0,vy*vy+2*gravity*dy)))/gravity)

            for _ in range(12):
                x=cart['pos'][0]+40+cart['vel'][0]*duration-a['size'][0]/2

                area=self.leap_area(duration,duration)
                duration+=max(0,(abs(x-a['pos'][0])-r['jump_speed_px_s']*area)/
                              max(1,r['jump_speed_px_s']-sign*cart['vel'][0]))
            x=cart['pos'][0]+40+cart['vel'][0]*duration-a['size'][0]/2
            vx=(x-a['pos'][0])/self.leap_area(duration,duration)
            if sign*(x-a['pos'][0])<required or abs(vx)>r['jump_speed_px_s']+1:continue
            if cart['expiry']<=self.time+duration or duration>3.5:continue
            if not 103<=x+a['size'][0]/2<=2289:continue
            vy=self.leap_vertical_speed(dy,duration)
            candidates.append((cart,duration,vx,vy,1 if dy>=0 else -1))
        if not candidates:

            x=max(48,min(2289-a['size'][0],a['pos'][0]+sign*spacing))
            x=max(x,a['pos'][0]) if sign>0 else min(x,a['pos'][0])
            ys=[p['start'][1]-a['size'][1] for p in self.pack.scene['tracks']]
            choices=[y for y in ys if (y-a['pos'][1])*vertical>30]
            y=self.rng.choice(choices or ys);duration=max(1.,abs(x-a['pos'][0])/r['jump_speed_px_s']+r['jump_brake_s'])
            a['vel']=[(x-a['pos'][0])/self.leap_area(duration,duration),self.leap_vertical_speed(y-a['pos'][1],duration)]
            a['jump_target']=None
        else:
            closeness=lambda row:abs(abs(row[2]*self.leap_area(row[1],row[1]))-spacing)
            best=min(closeness(row) for row in candidates)
            adjacent=[row for row in candidates if closeness(row)<=best+40]
            preferred=[row for row in adjacent if row[4]==vertical]
            cart,duration,vx,vy,vertical=self.rng.choice(preferred or adjacent)
            a['vel']=[vx,vy];a['jump_target']=cart['id']
            x=a['pos'][0]+vx*self.leap_area(duration,duration);y=cart['pos'][1]-a['size'][1]
        a['hurt_leap']=True;a['jump_end']=self.time+duration
        a['leap_start']=self.time;a['leap_origin']=list(a['pos']);a['leap_landing']=[x,y];a['leap_duration']=duration
        a['attached']=None;a['grounded']=False;a['next_jump']=self.time+duration+.2
        a['pos'][1]-=.2;self.pose(a,1)
        self.sound(a,'mbjump');self.emit('hurt_jump',target=a['jump_target'],source=source,start=list(a['pos']),
            landing=[x,y],direction=sign,vertical=vertical,minimum=minimum,requested_spacing=spacing,duration=duration,edge_limited=edge_limited)

    def walk_platforms(self,a,dt):
        from .common import approach
        attached=self.actors.get(a.get('attached'))
        if attached:
            a['pos'][0]+=attached['vel'][0]*dt;a['pos'][1]=attached['pos'][1]-a['size'][1]
            a['vel']=[0,0];a['grounded']=True
            return
        target=self.actors.get(a.get('jump_target'))
        if target and not a.get('hurt_leap'):
            remaining=max(.12,a.get('jump_end',self.time)-self.time)
            x=target['pos'][0]+40+target['vel'][0]*remaining-a['size'][0]/2
            limit=self.pack.entities['mid']['jump_speed_px_s']
            desired=max(-limit,min(limit,(x-a['pos'][0])/remaining))
            a['vel'][0]=approach(a['vel'][0],desired,900*dt)
        was_grounded=a['grounded'];old_bottom=a['pos'][1]+a['size'][1]
        leap=a.get('hurt_leap',False)
        if leap:
            duration=a['leap_duration'];elapsed=min(duration,self.time-a['leap_start'])
            progress=self.leap_area(elapsed,duration)/self.leap_area(duration,duration)
            landing_x=(target['pos'][0]+40+target['vel'][0]*max(0,a['jump_end']-self.time)-a['size'][0]/2) if target else a['leap_landing'][0]
            ideal=a['leap_origin'][0]+(landing_x-a['leap_origin'][0])*progress
            a['vel'][0]=(ideal-a['pos'][0])/max(.0001,dt)

        a['vel'][1]=min(a['vel'][1],self.pack.entities['mid']['fall_terminal_speed']-781.25*dt)
        self.move_actor(a,dt,781.25,platforms=not leap)
        if leap and target and a['vel'][1]>=0 and old_bottom<=target['pos'][1]+1 and a['pos'][1]+a['size'][1]>=target['pos'][1]:
            if a['pos'][0]+a['size'][0]>target['pos'][0] and a['pos'][0]<target['pos'][0]+target['size'][0]:
                a['pos'][1]=target['pos'][1]-a['size'][1];a['grounded']=True;a['vel'][1]=0
        if leap and self.time>a['jump_end']+.2:a['hurt_leap']=False
        if a['grounded']:
            support=next((v for v in self.actors.values() if v['kind']=='cart' and
                abs(a['pos'][1]+a['size'][1]-v['pos'][1])<2 and a['pos'][0]+a['size'][0]>v['pos'][0] and a['pos'][0]<v['pos'][0]+80),None)
            a['attached']=support['id'] if support else None;a['jump_target']=None;a['hurt_leap']=False;a['landed_once']=True
            a['vel'][0]=0;self.pose(a,4)
            if not was_grounded and a['kind']=='midboss':self.sound(a,'mbland')
        elif self.time>=a.get('jump_end',0):a['vel'][0]=approach(a['vel'][0],0,500*dt)
        if a['pos'][1]>1056:
            if a['kind']=='clone':self.remove(a,'INTERNAL')
            else:self.jump_away(a)

    def start_mid_volley(self,a):
        target=self.target(a)
        if not target:return
        self.face(a,target[0]-center(a)[0]);slot=7 if a['kind']=='clone' else 5
        self.anim(a,slot,.924)
        for index,p in enumerate(self.pack.emitters[a['vid']][str(slot)]):
            self.schedule(p['time']/1000,'mid_emit',owner=a['id'],slot=slot,index=index)
        a['next_attack']=self.time+.94

    def mid_emit(self,e):
        a=self.actors.get(e['owner'])
        if not a:return
        ghost=a['kind']=='clone'
        origin=self.emit_point(a,e['slot'],e['index']);target=self.target(a) or [origin[0],origin[1]+200]
        angle=math.atan2(target[1]-origin[1],target[0]-origin[0])+math.radians(self.rng.randint(-5,5))
        self.fire(a,'city.hbshot' if ghost else 'city.bshot',pos=origin,direction=[math.cos(angle),math.sin(angle)],
            kind='ghostshot' if ghost else 'shot',**(dict(hp=1,properties=dict(damage=0,hitboxdamage=0,hitpoints=1,invulnerable=True,trigger=1,removetype=0)) if ghost else {}))

    def spawn_cart(self,lane):
        r=self.pack.entities['mid']; path=self.pack.scene['tracks'][lane]
        start,end=path['start'],path['end']; distance=abs(end[0]-start[0])
        evil=self.rng.random()<r['evil_chance']
        a=self.new('cart','city.evilcart' if evil else 'city.cart',start,
                   vel=[r['cart_speed_px_s']*(1 if end[0]>start[0] else -1),0],
                   life=distance/r['cart_speed_px_s'],lane=lane)

        a['native_path']=dict(start=list(start),end=list(end),duration=distance/r['cart_speed_px_s'])
        self.emit('cart_path',id=a['id'],path=a['native_path'])

    def clone(self):
        b=self.boss;c=center(b);r=self.pack.entities['mid']
        angle=self.rng.uniform(-math.pi,math.pi);radius=self.rng.uniform(r['clone_min_radius'],r['clone_max_radius'])
        p=self.pack.rect(self.pack.props('city.hologram'));pos=[c[0]+math.cos(angle)*radius-p[2]/2,c[1]+math.sin(angle)*radius-p[3]/2]
        a=self.new('clone','city.hologram',pos,life=math.inf,hp=1,facing=b['facing'],
                   next_attack=self.time+self.rng.uniform(.8,2.0),
                   wander_until=0.,
                   properties=dict(hitpoints=1,damage=0,hitboxdamage=0,invulnerable=True,trigger=1,removetype=0,blocktype=0))
        self.anim(a,5,.528); self.sound(b,'hologram')
        self.emit('hologram_spawn',id=a['id'],hp=b['hp'])

    def clone_step(self,a,dt):
        from .common import approach
        r=self.pack.entities['mid'];bc=center(self.boss)
        if self.time>=a['wander_until']:
            angle=self.rng.uniform(-math.pi,math.pi);radius=self.rng.uniform(r['clone_min_radius'],r['clone_max_radius'])
            a['wander_offset']=[math.cos(angle)*radius,math.sin(angle)*radius]
            a['wander_until']=self.time+self.rng.uniform(.55,1.35)
        c=center(a);goal=[bc[i]+a['wander_offset'][i] for i in (0,1)]
        delta=[goal[i]-c[i] for i in (0,1)]
        for other in self.actors.values():
            if other['kind']!='clone' or other['id']==a['id']:continue
            oc=center(other);d=[c[i]-oc[i] for i in (0,1)];length=math.hypot(*d)
            if 1<length<160:
                for i in (0,1):delta[i]+=d[i]/length*(160-length)*2
        length=max(1,math.hypot(*delta));speed=min(r['clone_fly_speed'],length*2)
        for i in (0,1):
            a['vel'][i]=approach(a['vel'][i],delta[i]/length*speed,600*dt)
            a['pos'][i]+=a['vel'][i]*dt

        c=center(a);dx,dy=c[0]-bc[0],c[1]-bc[1];distance=math.hypot(dx,dy)
        if distance<r['clone_min_radius']:
            if distance<.01:dx,dy=a['wander_offset'];distance=math.hypot(dx,dy)
            a['pos']=[bc[0]+dx/distance*r['clone_min_radius']-a['size'][0]/2,
                      bc[1]+dy/distance*r['clone_min_radius']-a['size'][1]/2]
        a['grounded']=False;self.pose(a,1)

    def encounter_step(self,dt):
        r=self.pack.entities['mid']
        if self.state=='dying':
            if self.time>=self.death_at:
                self.victory()
            return
        for lane,oid in enumerate(self.pack.scene['sentry_ids']):
            if oid not in self.actors and not self.lane_unlocked[lane]:
                self.lane_unlocked[lane]=True;self.next_cart[lane]=self.time+self.rng.uniform(.6,1.5)
                self.emit('lane_unlocked',lane=lane)
            if self.time>=self.next_cart[lane]:
                self.spawn_cart(lane); self.next_cart[lane]=self.time+r['cart_period_s']*self.rng.uniform(.9,1.1)
        for a in self.actors.values():
            if a['kind']=='cart': a['pos'][0]+=a['vel'][0]*dt
        if self.time>=self.next_flyer:
            if sum(a['kind']=='flyer' for a in self.actors.values())<r['npc_cap']:
                self.spawn_flyer(self.rng.choice(([-1304,-1229],[3624,-1197])))
            self.next_flyer=self.time+(r['npc_period_before_s'] if self.state=='devices' else r['npc_period_after_s'])
        sentries=[a for a in self.actors.values() if a['kind']=='sentry']
        for a in sentries:
            target=self.target(a,r['sentry_range_px'])
            if target and self.time>=a['next_attack']:
                self.face(a,target[0]-center(a)[0]); self.anim(a,3,.99); self.sound(a,'sentryshoott')
                self.schedule(.594,'npc_emit',owner=a['id'],slot=3,index=0,vid='city.csbullet',spread=0)
                a['next_attack']=self.time+r['sentry_period_s']
        if self.state=='devices' and not sentries:
            self.boss=self.new('midboss','city.boss',[1161,-1195],hp=r['hp'],next_attack=self.time+.15,
                               properties=dict(removetype=0))
            self.boss_started=self.time; self.phase=1; self.state='fighting'
            self.motion=None
            self.next_clone=self.time+2.5; self.emit('mid_boss_spawn')
        b=self.boss
        if not b: return
        if self.motion:
            m=self.motion; t=min(1,(self.time-m['at'])/m['duration'])
            b['pos']=[m['start'][i]+(m['end'][i]-m['start'][i])*t for i in (0,1)]
            if not m.get('drop'): b['pos'][1]-=230*4*t*(1-t)
            if t>=1:
                self.motion=None; self.pose(b,4); self.sound(b,'mbland')
                b['grounded']=True
                if self.state=='center_jump':
                    self.state='fighting'; self.phase=2; self.emit('mid_phase_two')
        elif self.phase==1:
            self.walk_platforms(b,dt)
        if self.phase==1 and b['hp']<=r['split_hp'] and self.time-self.boss_started>=r['first_min_s'] and not self.reposition:
            self.reposition=True; self.state='center_jump'
            self.jump([self.pack.scene['mid_center'][0]+self.rng.uniform(-18,18),-118],1.45); self.emit('mid_center_return')
        if self.time>=b['next_attack']:
            self.start_mid_volley(b)
        if self.time>=self.next_clone:
            if sum(a['kind']=='clone' for a in self.actors.values())<24: self.clone()
            fraction=max(0,min(1,b['hp']/r['hp']))
            self.next_clone=self.time+r['clone_period_low_s']+(r['clone_period_full_s']-r['clone_period_low_s'])*fraction
        for a in list(self.actors.values()):
            if a['kind']=='clone':
                self.clone_step(a,dt)
                if a['id'] in self.actors and self.time>=a['next_attack']:self.start_mid_volley(a)
