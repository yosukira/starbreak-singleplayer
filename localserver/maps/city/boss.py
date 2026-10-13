"""Elite Vanquished Boss: live waves, independent transports and three claw rounds."""
import math
from .common import CityController, center, approach, overlap

class CityBossController(CityController):
    KIND='boss'

    def prepare(self):
        super().prepare()
        self.opening_spawned=0; self.next_opening=0.; self.stage_at=0.; self.tanks=[]
        self.next_copter=math.inf; self.next_formation=math.inf; self.next_tank_fly=math.inf
        self.locked_target=None; self.ramp_at=None; self.final_at=None; self.claws=[]
        self.motion=None; self.round=0; self.last_burst=0.; self.special_final=False

    def on_start(self):
        self.emit('opening_wave',quota=self.pack.entities['encounter']['opening_count'])

    def enter(self,state):
        self.state=state; self.stage_at=self.time; self.emit('stage',state=state,round=self.round)

    def gated_damage(self,a,value):
        r=self.pack.entities['encounter']; kind=a['kind']
        if kind=='tank':
            if a['timer'] is None:
                a['timer']=self.time; self.emit('tank_timer',id=a['id'])
            a['damage_taken']=min(r['tank_hp'],a['damage_taken']+value)
            a['hp']=max(1,r['tank_hp']-a['damage_taken'])
            self.props(a,HITPOINTS=round(a['hp']))
        elif kind=='claw':
            floor=max(0,r['claw_floor_start']*(1-(self.time-a['born'])/r['claw_min_s']))
            a['hp']=max(floor,a['hp']-value)
            self.props(a,HITPOINTS=max(1,math.ceil(a['hp'])))
            if a['hp']<=0:
                self.world_sound('dronedeath')
                self.remove(a,'INTERNAL'); self.emit('claw_dead',id=a['id'],round=self.round)
        elif kind=='body' and self.state not in ('exploding','death_fall','done'):
            if self.ramp_at is None: floor=r['boss_opening_floor']
            else: floor=max(0,r['boss_opening_floor']*(1-(self.time-self.ramp_at)/r['boss_ramp_s']))
            if self.round<3: floor=max(1,floor)
            a['hp']=max(floor,a['hp']-value)
            self.props(a,HITPOINTS=max(1,math.ceil(a['hp'])))

    def begin_tanks(self):
        r=self.pack.entities['encounter']; self.enter('tank_entry')
        for side in range(2):
            a=self.new('tank','city.transport',self.pack.scene['tank_starts'][side],hp=r['tank_hp'],facing=0 if side==0 else 180,
                       side=side,timer=None,damage_taken=0,roll_at=self.time,roll_slot=6,
                       next_attack=self.time+r['tank_entry_s']+2,
                       properties=dict(hitpoints=r['tank_hp'],removetype=0,z=65))
            self.tanks.append(a['id'])
            for _ in range(4): self.spawn_formation(side,initial=True)

    def tanks_step(self):
        r=self.pack.entities['encounter']
        for oid in self.tanks:
            a=self.actors.get(oid)
            if not a or a['kind']!='tank': continue
            side=a['side']
            if self.state=='tank_entry':
                t=min(1,(self.time-self.stage_at)/r['tank_entry_s'])

                start,end=self.pack.scene['tank_starts'][side],self.pack.scene['tank_ends'][side]
                a['pos']=[start[i]+(end[i]-start[i])*t for i in (0,1)]
                if self.time>=a['roll_at']:
                    self.anim(a,a['roll_slot'],2.508 if a['roll_slot']==6 else 2.706)
                    self.sound(a,'transproll'); a['roll_slot']=13-a['roll_slot']; a['roll_at']=self.time+2.5
                    self.schedule(1.88,'tank_slam',owner=oid)
            if self.state=='tanks' and self.time>=a['next_attack']:
                self.anim(a,2,2.112); self.schedule(1.056,'tank_missile',owner=oid)
                sign=1 if side==0 else -1
                front=a['pos'][0]+(a['size'][0]-35 if side==0 else 35)
                if sum(b['kind']=='infantry' for b in self.actors.values())<self.pack.entities['npc']['ground_cap']:
                    self.spawn_infantry([front,850],vid=self.rng.choice(('city.baller','city.gunner')),facing=a['facing'])
                a['next_attack']=self.time+r['tank_spawn_period_s']*self.rng.uniform(.88,1.12)
            if self.state=='tanks' and a['timer'] is not None and self.time-a['timer']>=r['tank_min_s'] and a['damage_taken']>=r['tank_hp']:
                self.world_sound('mechdeath')
                self.emit('destroy_once',id=oid,reason='INTERNAL')
                a['kind']='tank_wreck'; a['expiry']=self.time+3.234
                self.emit('tank_dead',id=oid,elapsed=self.time-a['timer'])
        if self.state=='tank_entry' and self.time-self.stage_at>=r['tank_entry_s']:
            self.enter('tanks')
            for oid in self.tanks:
                if oid in self.actors: self.anim(self.actors[oid],0,.594);self.world_sound('transpslam')
            self.next_copter=self.time; self.next_formation=self.time+1.; self.next_tank_fly=self.time+1.
        living=[self.actors[i] for i in self.tanks if i in self.actors and self.actors[i]['kind']=='tank']
        if self.state!='tanks': return
        if living:
            if self.time>=self.next_copter:
                needed=r['copter_cap']-sum(a['kind']=='copter' for a in self.actors.values())
                for _ in range(max(0,needed)):
                    self.new('copter','city.copter',[self.rng.uniform(-600,1700),-1248],next_attack=self.time+3)
                self.next_copter=self.time+r['copter_respawn_s']
            if self.time>=self.next_formation:
                for a in living: self.spawn_formation(a['side'])
                self.next_formation=self.time+r['formation_period_s']
            if self.time>=self.next_tank_fly:
                for a in living:
                    if sum(b['kind']=='flyer' for b in self.actors.values())<self.pack.entities['npc']['fly_cap']:
                        self.spawn_flyer([a['pos'][0]+a['size'][0]/2,a['pos'][1]-90])
                self.next_tank_fly=self.time+3.6
        else:
            self.enter('clear_adds')

    def tank_slam(self,event):
        a=self.actors.get(event['owner'])
        if a and a['kind']=='tank': self.sound(a,'transpslam')

    def tank_missile(self,event):
        a=self.actors.get(event['owner'])
        if a and a['kind']=='tank':self.launch_missile(a,True)

    def spawn_body(self):
        r=self.pack.entities['encounter']
        self.boss=self.new('body','city.grandoverseer',self.pack.scene['boss_start'],hp=r['boss_hp'],
                           properties=dict(removetype=0,armor=r['boss_armor'],soundprefix='city.mech'))
        self.boss['anchors']={}
        for side,dx in ((-1,75),(1,475)):
            anchor=self.new('anchor','',[self.boss['pos'][0]+dx,self.boss['pos'][1]+150],owner=self.boss['id'],
                offset=[dx,150],properties=dict(move=dict(relativeto=dict(objid=self.boss['id']),to=dict(xmp=dx*256,ymp=150*256))))
            self.boss['anchors'][side]=anchor['id']; self.boss['children'].append(anchor['id'])
        self.round=1; self.phase=1; self.enter('body_entry')
        self.world_sound('lab.bulkhead')

    def make_claws(self):
        r=self.pack.entities['encounter']; b=self.boss; c=center(b); self.claws=[]
        for side in (-1,1):
            a=self.new('claw','city.pincerleft' if side<0 else 'city.pincerright',[c[0]+side*140-125,c[1]-125],
                       hp=r['claw_hp'],side=side,vel=[side*110,130],properties=dict(removetype=0))
            self.claws.append(a['id'])
            offset=[155 if side<0 else 95,100]
            anchor=self.new('anchor','',[a['pos'][i]+offset[i] for i in (0,1)],owner=a['id'],offset=offset,
                properties=dict(move=dict(relativeto=dict(objid=a['id']),to=dict(xmp=offset[0]*256,ymp=offset[1]*256))))
            a['anchor']=anchor['id']; a['children'].append(anchor['id']); a['links']=[]

            count=36 if self.round==1 else 20
            for n in range(count):
                arm=self.new('arm','city.pincerarm',b['pos'],owner=a['id'])
                a['links'].append(arm['id']);a['children'].append(arm['id'])
            a['links_configured']=False
            self.arm_links(a)
        self.emit('claws_spawn',round=self.round,ids=list(self.claws))

    def claw_step(self,dt):
        b=self.boss; bc=center(b)
        for oid in self.claws:
            a=self.actors.get(oid)
            if not a: continue
            old=list(a['pos']);w,h=a['size'];side=a['side']
            players=[p for p in self.players.values() if (p[0]-bc[0])*side>=0]
            target=min(players,key=lambda p:math.dist(center(a),p)) if players else [bc[0]+side*450,b['pos'][1]+650]
            if self.round==1:
                a['vel'][0]=approach(a['vel'][0],max(-230,min(230,(target[0]-center(a)[0])*.22)),120*dt)
                previous=list(a['vel']);self.move_actor(a,dt,650,platforms=False)
                a['vel'][1]=min(650,a['vel'][1])
                if (a['blocked'][1] and previous[1]>0) or any(a.get('contact_ids',())):
                    a['vel'][1]=-self.rng.uniform(360,480)
                    self.sound(a,'transpslam');self.emit('claw_bounce',id=oid)
            else:

                desired=[bc[0]+side*max(180,min(820,abs(target[0]-bc[0]))),
                         max(b['pos'][1]-180,min(b['pos'][1]+850,target[1]))]
                c=center(a);delta=[desired[i]-c[i] for i in (0,1)];length=max(1,math.hypot(*delta))
                speed=min(300,length*.8)
                a['vel']=[approach(a['vel'][i],delta[i]/length*speed,280*dt) for i in (0,1)]
                body_delta=b.get('step_delta',[0,0]);a['pos']=[a['pos'][i]+body_delta[i] for i in (0,1)]
                self.move_actor(a,dt)
            if (center(a)[0]-bc[0])*side<80:
                a['pos'][0]=bc[0]+side*80-125;a['vel'][0]=side*abs(a['vel'][0])
            reach=1600 if self.round==1 else 950
            if abs(center(a)[0]-bc[0])>reach:
                a['vel'][0]=-side*160
            self.pose(a,2 if abs(a['vel'][0])>abs(a['vel'][1]) else 3)
            self.arm_links(a)

    def arm_links(self,a):
        b=self.boss; root=self.actors[b['anchors'][a['side']]]; tip=self.actors[a['anchor']]
        root['pos']=[b['pos'][i]+root['offset'][i] for i in (0,1)]
        tip['pos']=[a['pos'][i]+tip['offset'][i] for i in (0,1)]
        count=len(a['links']);changed=not a.get('links_configured')
        for n,oid in enumerate(a['links'],1):
            arm=self.actors[oid]; pct=round(100*n/(count+1)); t=pct/100
            arm['pos']=[root['pos'][i]+(tip['pos'][i]-root['pos'][i])*t-48 for i in (0,1)]
            if changed:
                self.props(arm,MOVE=('Properties_Move',dict(relativeto_=dict(objid_=root['id']),
                    lerp_=dict(target_=dict(objid_=tip['id']),percentage_=pct,center_=True))))
        a['links_configured']=True

    def collapse(self):
        b=self.boss; self.enter('falling')
        self.props(b,HITBOXDAMAGE=0,DAMAGE=0); self.anim(b,2,1.254)
        b['vel']=[0,0]
        if self.ramp_at is None: self.ramp_at=self.time
        self.emit('body_collapse',round=self.round)

    def choose_locked_target(self):
        if self.locked_target not in self.players:
            self.locked_target=min(self.players,key=lambda k:(self.players[k][1],str(k)),default=None)
            self.emit('target_locked',target=str(self.locked_target))
        return self.players.get(self.locked_target)

    def body_attack(self):
        b=self.boss

        mode=b.get('next_mode','bomb'); b['next_mode']='shot' if mode=='bomb' else 'bomb'
        self.emit('body_attack',mode=mode)
        if mode=='bomb':
            self.anim(b,6,1.254)
            for n in range(8):
                self.schedule(1.254+n*.330,'body_emit',mode='bomb',index=n)
            b['next_attack']=self.time+4.3
        else:
            self.anim(b,3,1.188)
            for n in range(10): self.schedule(1.188+n*.396,'body_emit',mode='shot',index=n)
            b['next_attack']=self.time+5.4

    def body_emit(self,e):
        if self.state!='airborne' or self.round<2: return
        b=self.boss; c=center(b); x=c[0]+(-130 if e['index']%2==0 else 130)
        if e['mode']=='bomb':
            self.anim(b,7,.330)
            self.schedule(.066,'body_round',mode='bomb')
        else:
            self.anim(b,4,.396)
            self.schedule(.066,'body_round',mode='shot')

    def body_round(self,e):
        if self.state!='airborne' or self.round<2:return
        b=self.boss
        if e['mode']=='bomb':
            bomb=self.fire(b,'city.overseerb',pos=self.emit_point(b,7),direction=[0,1],kind='bomb',life=2)
            bomb['vel']=[self.rng.uniform(-70,70),180]
        else:
            rules=self.pack.entities['projectile']

            low=max(1,min(179,rules['body_down_min_deg']))
            high=max(low,min(179,rules['body_down_max_deg']))
            round_offset=self.rng.uniform(-rules['body_round_spread_deg'],rules['body_round_spread_deg'])
            angles=[]
            for i,p in enumerate(self.pack.emitters[b['vid']]['4']):
                degrees=max(low,min(high,p['angle']+round_offset+self.rng.uniform(-rules['body_shot_spread_deg'],rules['body_shot_spread_deg'])))
                angle=math.radians(degrees);angles.append(degrees)
                self.fire(b,'city.oshot',pos=self.emit_point(b,4,i),direction=[math.cos(angle),math.sin(angle)])
            self.emit('body_downward_round',angles=angles)

    def final_ready(self):
        r=self.pack.entities['encounter']
        return (self.round>=3 and self.boss['hp']<=0 and self.final_at is not None and
                self.time-self.final_at>=r['final_min_s']-1e-8 and self.ramp_at is not None and
                self.time-self.ramp_at>=r['boss_ramp_s']-1e-8)

    def explode(self):
        if self.state=='exploding': return
        self.enter('exploding'); self.last_burst=self.time-.5
        self.props(self.boss,HITBOXDAMAGE=0,DAMAGE=0,INVULNERABLE=True)
        for oid in self.claws:
            if oid in self.actors: self.remove(self.actors[oid])

    def body_step(self,dt):
        r=self.pack.entities['encounter']; b=self.boss
        if self.state=='body_entry' and self.time-self.stage_at>=2.05:
            self.world_sound('lab.bulkheadup');self.make_claws(); self.enter('airborne')
        if self.state=='airborne':
            before=list(b['pos'])
            if self.round>=2 and not self.special_final:
                p=self.choose_locked_target()
                if p:
                    desired=[max(-800,min(1900,p[0]-b['size'][0]/2)),max(-1248,min(180,p[1]-750))]
                    b['pos']=[approach(b['pos'][i],desired[i],(200 if i==0 else 180)*dt) for i in (0,1)]
                if self.time>=b['next_attack']: self.body_attack()
            b['step_delta']=[b['pos'][i]-before[i] for i in (0,1)];self.claw_step(dt)
            living=any(i in self.actors for i in self.claws)
            if self.final_ready(): self.explode()
            elif not living and not self.special_final:
                self.special_final=self.round>=3; self.collapse()
        elif self.state in ('falling','death_fall'):
            previous_y=b['pos'][1];b['vel'][1]=r['fall_speed'];b['pos'][1]+=b['vel'][1]*dt
            body_rect=[b['pos'][0],previous_y,b['size'][0],b['size'][1]+b['pos'][1]-previous_y]
            for a in list(self.actors.values()):
                if a['kind']=='cargo' and overlap(body_rect,a['pos']+a['size']):
                    self.remove(a,'HITPOINTS'); self.emit('cargo_crushed',id=a['id'])

            if b['pos'][1]>=self.pack.scene['boss_ground_y']:
                b['pos'][1]=self.pack.scene['boss_ground_y']; b['vel'][1]=0
                self.sound(b,'bigslam'); self.emit('shake',magnitude=8,duration=500)
                if self.state=='death_fall':
                    self.new('corpse','city.bosscorpse',b['pos']); self.remove(b,'BLOCKED'); self.victory()
                else:
                    self.enter('resting'); self.anim(b,1,r['ground_rest_s']); self.sound(b,'lab.bulkhead')
        elif self.state=='resting' and self.time-self.stage_at>=r['ground_rest_s']:
            self.enter('waking'); self.anim(b,6,r['wake_s']); self.sound(b,'lab.bulkheadup')
            self.props(b,HITBOXDAMAGE=45)
        elif self.state=='waking' and self.time-self.stage_at>=r['wake_s']:
            self.enter('rising')
            target=[416,self.pack.scene['boss_hover_y']] if self.special_final else [b['pos'][0],self.pack.scene['boss_hover_y']]
            self.motion=dict(start=list(b['pos']),end=target,at=self.time)
        elif self.state=='rising':
            t=min(1,(self.time-self.stage_at)/r['rise_s']); m=self.motion
            b['pos']=[m['start'][i]+(m['end'][i]-m['start'][i])*t for i in (0,1)]
            if t>=1:
                self.enter('airborne'); b['next_attack']=self.time+.3
                if self.special_final:
                    self.emit('final_relaunch_ready')
                    if self.final_ready(): self.explode()
                else:
                    self.round+=1; self.phase=self.round
                    self.choose_locked_target(); self.make_claws()
                    if self.round==3: self.final_at=self.time
        elif self.state=='exploding':
            if self.time-self.last_burst>=.4:
                self.anim(b,5,.528); self.sound(b,'bossx'); self.last_burst=self.time
                self.new('fx','city.cpop',[b['pos'][0]+self.rng.uniform(0,400),b['pos'][1]+self.rng.uniform(0,400)],life=.875)
            if self.time-self.stage_at>=2.4:
                self.enter('death_fall'); b['vel']=[0,0]

    def encounter_step(self,dt):
        r=self.pack.entities['encounter']
        if self.state=='opening':
            if self.opening_spawned<r['opening_count'] and self.time>=self.next_opening and sum(a['kind']=='flyer' for a in self.actors.values())<r['opening_cap']:
                self.spawn_flyer(); self.opening_spawned+=1
                interval=self.rng.uniform(1.55,1.95) if self.opening_spawned<6 else self.rng.uniform(.75,1.75)
                self.next_opening=self.time+interval
            if self.opening_spawned==r['opening_count'] and not any(a['kind']=='flyer' for a in self.actors.values()):
                self.begin_tanks()
        elif self.state in ('tank_entry','tanks'): self.tanks_step()
        elif self.state=='clear_adds':
            if not any(a['kind'] in ('flyer','infantry','skiff','hawk','copter','tank_wreck') for a in self.actors.values()):
                self.spawn_body()
        elif self.boss: self.body_step(dt)

    def status(self):
        result=super().status()
        result.update(round=self.round,target=str(self.locked_target),opening_spawned=self.opening_spawned,
                      tank_timers={i:self.actors[i].get('timer') for i in self.tanks if i in self.actors},
                      claws={i:self.actors[i]['hp'] for i in self.claws if i in self.actors},special_final=self.special_final)
        return result
