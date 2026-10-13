'Live ice encounter. All positions in pixels, all decisions use current players.'
import copy
import math
import random
from .scene import IceBossPack

def overlap(a, b):
    return a[0] < b[0]+b[2]-.001 and a[0]+a[2] > b[0]+.001 and a[1] < b[1]+b[3]-.001 and a[1]+a[3] > b[1]+.001

def center(a):
    return [a['pos'][0]+a['size'][0]/2, a['pos'][1]+a['size'][1]/2]

class IceBossController:
    name = 'ice_boss'
    STEP = 1/120

    def __init__(self, pack=None, seed=0):
        self.pack = pack or IceBossPack(seed=seed)
        self.seed = seed
        self.prepare()

    def prepare(self):
        self.rng = random.Random(self.seed)
        self.time = self.remainder = 0.
        self.events = []; self.players = {}; self.actors = {}; self.retired = set()
        self._next_id = 20000
        self.state = 'waiting'; self.engaged = 0.; self.wave = self.volley = 0
        self.combat_phase = 'approach'; self.phase_started = None; self.phase_damage = 0
        self.phase_history = []; self.large_next_horn = math.inf
        self.next_horn = math.inf
        self.pending = []; self.fallen = set(); self.spike_wave = 0; self.next_sync = 0.
        self.small = self._static(self.pack.scene['small_id'])
        self.large = self._static(self.pack.scene['large_id'])
        self.small.update(action='idle', until=0., attack_at=0., throw_at=0., jump_at=0., aggro=False)
        self.large.update(action='hidden', until=0., side=-1, on_platform=False)
        self.damage_received = 0; self.shots = 0; self.npc_kills = 0

    def _static(self, oid):
        p = self.pack.templates[str(oid)]
        return dict(id=oid, vid=p['vid'], pos=list(self.pack.rect(p)[:2]), size=list(self.pack.rect(p)[2:]),
                    vel=[0.,0.], facing=p.get('facing',180), pose=p.get('pose',4), hp=p.get('hitpoints'), grounded=True)

    def emit(self, kind, **fields):
        self.events.append(dict(kind=kind, at=self.time, **copy.deepcopy(fields)))

    def drain(self):
        events, self.events = self.events, []
        return events

    def set_players(self, players):
        self.players = {k:[v/256 for v in p['center']] for k,p in players.items() if p.get('alive', True)}

    def start(self):
        if self.state == 'waiting':
            self.state = 'small'; self.emit('start')

    def phase_elapsed(self):
        return 0. if self.phase_started is None else self.time - self.phase_started

    def phase_requirements(self):
        p = self.pack.entities
        return {'body': (p['small']['body_damage'], p['small']['body_min_s']),
                'horn': (p['small']['horn_damage'], p['small']['horn_min_s']),
                'large': (p['large']['required_damage'], p['large']['min_s'])}.get(self.combat_phase, (0, 0))

    def phase_ready(self):
        damage, duration = self.phase_requirements()
        return self.combat_phase in ('body', 'horn', 'large') and self.phase_damage >= damage and self.phase_elapsed() + 1e-8 >= duration

    def _enter_phase(self, name):
        if self.phase_started is not None:
            self.phase_history.append(dict(phase=self.combat_phase, start=self.phase_started, end=self.time,
                                           damage=self.phase_damage, elapsed=self.phase_elapsed()))
        self.combat_phase = name
        self.phase_started = self.time
        self.phase_damage = 0
        if name == 'horn':
            self.next_horn = self.time + self.pack.entities['small']['first_horn_s']
        elif name == 'large':
            self.large_next_horn = self.time + self.pack.entities['large']['summon_first_s']
        self.emit('combat_phase', phase=name, required_damage=self.phase_requirements()[0],
                  min_s=self.phase_requirements()[1])

    def _horn(self, actor, summon=True, animate=True):
        actor['vel'][0] = 0
        if animate:
            self.anim(actor, 9, self.pack.entities['small']['horn_s'])
        self.emit('sound', id=actor['id'], name='ice.horn')
        if summon:
            self.pending.append((self.time+self.pack.entities['small']['summon_delay_s'], 'wave', None))
        self.emit('horn', id=actor['id'], summon=summon, phase=self.combat_phase)

    def _small_phase_tick(self):
        if self.combat_phase == 'body' and self.phase_ready():
            self._enter_phase('horn')
        if self.combat_phase == 'horn' and self.phase_ready():
            self._enter_phase('transition')
            self.state = 'final_horn'
            self.small['aggro'] = False
            self.deadline = self.time+self.pack.entities['small']['horn_s']
            self._horn(self.small, summon=False)
            return
        if self.combat_phase == 'horn' and self.time+1e-8 >= self.next_horn:
            self.next_horn += self.pack.entities['small']['horn_interval_s']
            self.wave += 1
            self.small.update(action='horn', until=self.time+self.pack.entities['small']['horn_s'])
            self._horn(self.small)

    def target(self, a, distance=math.inf, side=0):
        c = center(a)
        candidates = [(math.dist(c,p), p) for p in self.players.values() if not side or (p[0]-c[0])*side>=0]
        if not candidates:
            return None
        dist, p = min(candidates, key=lambda x:x[0])
        return p if dist <= distance else None

    def facing(self, a, sign):
        value = 0 if sign >= 0 else 180
        if a['facing'] != value:
            a['facing'] = value; self.emit('props', id=a['id'], values=dict(FACING=value))

    def pose(self, a, value):
        if a['pose'] != value:
            a['pose'] = value; self.emit('props', id=a['id'], values=dict(POSE=value))

    def anim(self, a, anim, duration):
        self.emit('anim', id=a['id'], anim=anim, duration=duration)

    def _new(self, kind, vid, pos, **fields):
        self._next_id += 1
        p = self.pack.props(vid)
        a = dict(id=self._next_id, kind=kind, vid=vid, pos=list(pos), size=list(self.pack.rect(p)[2:]),
                 vel=[0.,0.], hp=p.get('hitpoints'), facing=0, pose=1, grounded=False,
                 born=self.time, expiry=math.inf)
        a.update(fields)
        self.actors[a['id']] = a
        self.emit('create', actor=a)
        return a

    def remove(self, a, reason='INTERNAL'):
        if a['id'] in self.retired:
            return
        self.retired.add(a['id']); self.actors.pop(a['id'],None)
        self.emit('remove', actor=a, reason=reason)
        if a['kind']=='npc' and reason=='HITPOINTS':
            self.npc_kills += 1

    def ground(self, a, dt, gravity=1400., platforms=True):
        x,y = a['pos']; w,h = a['size']; vx,vy = a['vel']
        nx = max(self.pack.scene['left'], min(self.pack.scene['right']-w, x+vx*dt))
        for sx,sy,sw,sh in self.pack.solids:
            if y+h > sy+.05 and y < sy+sh-.05:
                if vx>0 and x+w<=sx+.05 and nx+w>sx: nx=sx-w
                if vx<0 and x>=sx+sw-.05 and nx<sx+sw: nx=sx+sw
        vy += gravity*dt; ny=y+vy*dt; a['grounded']=False
        surfaces=self.pack.surfaces if platforms else self.pack.solids
        if vy>=0:
            floors=[sy for sx,sy,sw,sh in surfaces if nx+w>sx+.05 and nx<sx+sw-.05 and y+h<=sy+.1 and ny+h>=sy]
            if floors:
                ny=min(floors)-h; vy=0.; a['grounded']=True
        else:
            ceilings=[sy+sh for sx,sy,sw,sh in self.pack.solids if nx+w>sx and nx<sx+sw and y>=sy+sh-.05 and ny<sy+sh]
            if ceilings: ny=max(ceilings); vy=0.
        a['pos']=[nx,ny]; a['vel'][1]=vy

    def _small_tick(self, dt):
        a=self.small; p=self.pack.entities['small']
        target=self.target(a,p['release_px'] if a['aggro'] else p['acquire_px'])
        if self.combat_phase == 'approach' and target:
            self._enter_phase('body')
        self._small_phase_tick()
        if self.state != 'small':
            return
        busy=a['action'] in ('windup','charge','jump','horn') and self.time<a['until']
        if not busy:
            a['aggro']=target is not None
        if a['aggro']:
            self.engaged+=dt
        if a['action']=='horn':
            a['vel'][0]=0
            if self.time>=a['until']:
                a['action']='idle'
        elif a['action']=='windup':
            a['vel'][0]=0
            if self.time>=a['until']:
                a.update(action='charge',until=self.time+p['melee_s'])
                a['vel'][0]=a['sign']*p['run_px_s'];self.pose(a,6);self.emit('charge',id=a['id'])
        elif a['action'] in ('charge','jump') and self.time>=a['until']:
            a['action']='idle';a['vel'][0]=0
        if not busy and a['action']=='idle':
            if target:
                sign=1 if target[0]>=center(a)[0] else -1;self.facing(a,sign);a['sign']=sign
                if a['grounded'] and self.time>=a['jump_at'] and (a['pos'][1]+a['size'][1]<self.pack.scene['floor']-10 or target[1]<center(a)[1]-90):
                    a.update(action='jump',until=self.time+1.25,jump_at=self.time+6.)
                    a['vel']=[sign*p['run_px_s'],-p['jump_px_s']];self.pose(a,3)
                elif self.time>=a['attack_at']:
                    a.update(action='windup',until=self.time+p['melee_windup_s'],attack_at=self.time+p['melee_cd_s'])
                    self.anim(a,2,p['melee_windup_s'])
                else:
                    self.pose(a,4);a['vel'][0]=0
            else:
                self.pose(a,4);a['vel'][0]=0

        if target and a['action']!='horn' and self.time>=a['throw_at']:
            axe=self.rng.random()<.3
            used = sum(row['kind']=='disc' for row in self.actors.values()) + sum(kind=='disc' for _,kind,_ in self.pending)
            if axe or used < self.pack.entities['projectile']['disc_cap']:
                self.anim(a,6 if axe else 5,.99)
                self.pending.append((self.time+(.462 if axe else p['throw_windup_s']), 'axe' if axe else 'disc',a['id']))
            a['throw_at']=self.time+p['throw_cd_s']
        self.ground(a,dt,p['gravity_px_s2'])
        if a['grounded'] and a['action']=='jump' and self.time+1.<a['until']:
            a['until']=self.time

    def _wave(self):
        p=self.pack.entities['small'];limit=self.pack.entities['npc']['cap']
        available=limit-sum(a['kind'] in ('npc','egg') or (bool(a.get('warning')) and not a.get('egg_spawned'))
                            for a in self.actors.values())
        platforms=[s for s in self.pack.surfaces if s[2]>=64 and 64<=s[1]<=self.pack.scene['floor']]
        species=['ice.maxethrower','ice.mrangthrower','ice.msworder','ice.mcharger']
        count=min(p['wave_size'],available)
        for i in range(count):
            for attempt in range(100):
                sx,sy,sw,sh=self.rng.choice(platforms)
                x=self.rng.uniform(max(sx,self.pack.scene['left']),min(sx+sw-64,self.pack.scene['right']-64))
                if not any(overlap((x,sy-230,64,230),solid) for solid in self.pack.solids):break
            else:continue
            npc=self.pack.entities['npc']
            fx=self._new('fx','ice.sambush',[x-32,sy-128], warning=True, egg_spawned=False,
                         egg_pos=[x,sy-64], species=species[i%4], landing=sy,
                         spawn_at=self.time+npc['spawn_warning_s'])
            fx['expiry']=self.time+npc['spawn_fx_s']
            self.anim(fx,1,npc['spawn_fx_s'])
            self.emit('spawn_warning',id=fx['id'],species=species[i%4],pos=fx['pos'])
        self.emit('wave',number=self.wave,count=count)

    def _spawn_egg(self, warning):
        warning['egg_spawned'] = True
        self.emit('sound', id=warning['id'], name='ice.smalla')
        egg = self._new('egg', 'ice.miteball', warning['egg_pos'], species=warning['species'],
                        landing=warning['landing'],
                        hp=45, vel=[0, -460], pose=3)
        self.emit('egg_emerge', id=egg['id'], warning_id=warning['id'], species=warning['species'])

    def _select_fire_target(self):
        origin = center(self.large)
        sides = []
        for sign in (-1, 1):
            candidates = [(key, pos) for key, pos in self.players.items()
                          if (pos[0] < origin[0]) == (sign == -1)]
            if candidates:
                sides.append(min(candidates, key=lambda pair: math.dist(origin, pair[1])))
        if not sides:
            return None
        if len(sides) == 1:
            return sides[0][0]
        return sides[0 if self.rng.random() < .5 else 1][0]

    def _fire_target(self, actor):
        key = actor.get('target_key')
        if key not in self.players:
            key = self._select_fire_target()
            actor['target_key'] = key
        return self.players.get(key)

    def _projectile(self, owner, kind):
        p=self.pack.entities['projectile'];sign=1 if owner['facing']==0 else -1
        if kind == 'disc' and sum(a['kind']=='disc' for a in self.actors.values()) >= p['disc_cap']:
            return None
        vid={'disc':'ice.trang','fire':'ice.tfireball','axe':'ice.maxe','rang':'ice.mrang'}[kind]
        w,h=self.pack.rect(self.pack.dynamic_templates[vid])[2:]

        anchors=self.pack.entities.get('emit',{})
        dx,dy=anchors.get(owner['vid']+':'+kind,[owner['size'][0]/2+12,-owner['size'][1]*.62])
        x=owner['pos'][0]+owner['size'][0]/2+sign*dx-w/2
        y=owner['pos'][1]+owner['size'][1]+dy-h/2
        a=self._new(kind,vid,[x,y],owner=owner['id'],sign=sign)
        a['expiry']=self.time+p[{'disc':'disc_life_s','fire':'fire_life_s','axe':'axe_life_s','rang':'axe_life_s'}[kind]]
        a['vel']=[sign*p[{'disc':'disc_px_s','fire':'fire_px_s','axe':'axe_px_s','rang':'rang_px_s'}[kind]],-p['axe_up_px_s'] if kind=='axe' else 0]
        if kind == 'fire':
            a['target_key'] = self._select_fire_target()
            self._home_fire(a)
        self.facing(a,sign);self.shots+=1
        if kind in ('axe','rang'):self.emit('sound',id=a['id'],name='ice.gen'+kind)
        self.emit('shot',id=a['id'],species=kind,owner=owner['id'],target=a.get('target_key'))
        return a

    def _home_fire(self, actor):
        target = self._fire_target(actor)
        if target is not None:
            c = center(actor)
            dx, dy = target[0]-c[0], target[1]-c[1]
            distance = math.hypot(dx, dy)
            if distance > 1e-8:
                speed = self.pack.entities['projectile']['fire_px_s']
                actor['vel'] = [speed*dx/distance, speed*dy/distance]

    def _npc_tick(self,a,dt):
        p=self.pack.entities['npc'];target=self.target(a,p['release_px'] if a.get('aggro') else p['acquire_px'])
        busy=self.time<a.get('until',0)
        if busy:
            if a.get('action')!='charge':a['vel'][0]=0
        else:
            a['aggro']=target is not None;a['vel'][0]=0;a['action']='idle'

            self.pose(a,4)
            if target:
                sign=1 if target[0]>=center(a)[0] else -1;self.facing(a,sign)
                dist=math.dist(center(a),target)
                if self.time>=a.get('next_attack',0):
                    a['next_attack']=self.time+p['attack_cd_s'];vid=a['vid']
                    if vid in ('ice.maxethrower','ice.mrangthrower'):

                        self.anim(a,3,2.64);a.update(until=self.time+2.64,action='throw')
                        self.pending.append((self.time+1.782,'axe' if vid=='ice.maxethrower' else 'rang',a['id']))
                    elif vid=='ice.msworder':
                        dx=abs(target[0]-center(a)[0]);dy=target[1]-center(a)[1]

                        angle=0 if abs(dy)<=p['spear_level_tolerance_px'] else math.degrees(math.atan2(dy,dx))
                        anim=5 if angle < -30 else 6 if angle>30 else 4
                        self.anim(a,anim,.99);self.emit('sound',id=a['id'],name='ice.msswing')
                        a.update(until=self.time+.99,action='spear');self.emit('spear',id=a['id'],anim=anim,facing=a['facing'],target=target)
                    elif vid=='ice.mcharger':
                        a.update(until=self.time+p['charge_s'],action='charge');a['vel'][0]=sign*p['charge_px_s'];self.pose(a,5)
        self.ground(a,dt)
        if not a['grounded']:self.pose(a,1)

    def _choose_side(self):
        c=center(self.large)[0]
        left=any(p[0]<c for p in self.players.values());right=any(p[0]>=c for p in self.players.values())
        if left and right:return -self.large['side']
        return -1 if left else 1 if right else self.large['side']

    def full_platform(self):
        a=self.large;x,y=a['pos'];w,h=a['size']

        candidates={py for px,py,pw,ph in self.pack.ledges if 0 < y+h-py <= 360}
        for py in sorted(candidates, reverse=True):
            spans=sorted((px,px+pw) for px,yy,pw,ph in self.pack.ledges if abs(yy-py)<.01)
            end=x
            for lo,hi in spans:
                if lo>end+.01:break
                if hi>=end:end=hi
                if end>=x+w:
                    landing=[x,py-h,w,h]
                    if not any(overlap(landing,s) for s in self.pack.solids):return py
        return None

    def _big_state(self, action, duration):
        a=self.large;a.update(action=action,until=self.time+duration)
        self.emit('large_state',action=action,volley=self.volley,side=a['side'])

    def _begin_volley(self):
        a=self.large;p=self.pack.entities['large']
        self._big_state('fire',p['shots']*p['shot_period_s'])
        a.update(shots_left=p['shots'],next_shot=self.time)
        self.pose(a,4)

    def _jump(self, up, height=None):
        a=self.large;p=self.pack.entities['large']
        a['jump_start']=list(a['pos']);a['jump_end']=[a['pos'][0],(height if up else self.pack.scene['floor'])-a['size'][1]]
        a['jump_began']=self.time;self._big_state('jump_up' if up else 'jump_down',p['jump_s'])
        self.pose(a,1);self.emit('shake',magnitude=8,duration=500);self._fall_spikes()

    def _begin_move(self, allow_platform=True, choose_side=True):
        a=self.large;p=self.pack.entities['large']
        side=self._choose_side() if choose_side else a['side']
        if side!=a['side']:
            a['side']=side;self.anim(a,5,p['turn_s']);self._big_state('turn',p['turn_s']);return
        platform=self.full_platform() if allow_platform else None
        if platform is not None and not a['on_platform']:
            self._jump(True,platform);return
        self.facing(a,side);self.pose(a,5);self._big_state('walk',p['walk_s']);a['spike_at']=self.time

    def _large_tick(self,dt):
        a=self.large;p=self.pack.entities['large'];action=a['action']
        if self.combat_phase == 'large':
            if self.phase_ready() and action not in ('jump_up', 'jump_down'):
                self._start_death()
                return
            if not self.phase_ready() and self.time+1e-8 >= self.large_next_horn:
                self.large_next_horn += p['summon_interval_s']
                self._horn(a, animate=False)
        if action=='rise':
            if self.time>=a['until']:
                a['vid']='ice.cgdino';a['hp']=1
                self.emit('props',id=a['id'],values=dict(VID=a['vid'],DRAW=True,HITPOINTS=1,HITBOXDAMAGE=30,BLOCKTYPE=1))
                self._enter_phase('large')
                a['side']=self._choose_side();self.facing(a,a['side']);self._begin_move()
        elif action=='turn':
            if self.time>=a['until']:
                self.facing(a,a['side']);self._begin_move(choose_side=False)
        elif action=='walk':
            margin=p['edge_clearance_px'];lo=self.pack.scene['left']+margin;hi=self.pack.scene['right']-a['size'][0]-margin
            a['pos'][0]=max(lo,min(hi,a['pos'][0]+a['side']*p['speed_px_s']*dt))
            if self.time>=a['spike_at']:
                self._fall_spikes();self.emit('shake',magnitude=5,duration=250);a['spike_at']=self.time+.55
            if self.time>=a['until']:self._begin_volley()
        elif action in ('jump_up','jump_down'):
            t=min(1.,(self.time-a['jump_began'])/p['jump_s'])
            a['pos'][1]=a['jump_start'][1]+(a['jump_end'][1]-a['jump_start'][1])*t-100*math.sin(math.pi*t)
            if self.time>=a['until']:
                a['pos']=a['jump_end'];a['on_platform']=action=='jump_up'
                self.emit('shake',magnitude=9,duration=500)
                if a['on_platform']:self._begin_volley()
                else:self._begin_move(allow_platform=False)
        elif action=='fire':
            if a['shots_left'] and self.time>=a['next_shot']:
                self.anim(a,7,.99);self.pending.append((self.time+p['windup_s'],'fire',a['id']))
                a['shots_left']-=1;a['next_shot']+=p['shot_period_s']
            if self.time>=a['until']:
                self.volley+=1;self.anim(a,6,p['recover_s']);self._big_state('recover',p['recover_s'])
        elif action=='recover' and self.time>=a['until']:
            if a['on_platform']:self._jump(False)
            else:self._begin_move()

    def _start_death(self):
        if self.state in ('death', 'done'):
            return
        a = self.large
        self._enter_phase('complete')
        self.state = 'death'
        self._big_state('death', self.pack.entities['large']['death_s'])
        self.emit('props', id=a['id'], values=dict(HITBOXDAMAGE=0, DAMAGE=0, INVULNERABLE=True))

        self.emit('death_begin', id=a['id'])
        self.pending = []
        for row in list(self.actors.values()):
            self.remove(row)
        self.emit('ceiling_safe')

    def _fall_spikes(self):
        cap=self.pack.entities['projectile']['debris_cap']
        if sum(a['kind']=='debris' for a in self.actors.values())>=cap:return
        active_sources={a['source'] for a in self.actors.values() if a['kind']=='debris'}
        candidates=[i for i in self.pack.spikes if i not in active_sources]
        free = cap-sum(a['kind']=='debris' for a in self.actors.values())
        for oid in self.rng.sample(candidates,min(8,len(candidates),free)):
            self.fallen.add(oid);x,y,w,h=self.pack.spikes[oid]
            a=self._new('debris','ice.ddebris',[x,y],source=oid)
            a['expiry']=self.time+8.;a['fall_at']=self.time+.528
            self.anim(dict(id=oid),2,.528)
            self.emit('props',id=a['id'],values=dict(DRAW=False,DAMAGE=0))
        self.spike_wave+=1

    def _flying_tick(self,a,dt):
        kind=a['kind'];p=self.pack.entities['projectile'];x,y=a['pos'];w,h=a['size']
        if self.time>=a['expiry']:self.remove(a,'LIFETIME');return
        if kind in ('fire','disc'):
            if kind == 'fire':
                self._home_fire(a)
            else:
                target=self.target(a)
                speed=p['disc_px_s']
                angle=math.atan2(a['vel'][1],a['vel'][0])
                if target:
                    c=center(a);wanted=math.atan2(target[1]-c[1],target[0]-c[0]);delta=(wanted-angle+math.pi)%(2*math.pi)-math.pi
                    turn=math.radians(p['disc_turn_deg_s'])*dt
                    angle+=max(-turn,min(turn,delta))
                a['vel']=[math.cos(angle)*speed,math.sin(angle)*speed]
        elif kind=='rang':
            a['vel'][0]-=a['sign']*p['rang_accel_px_s2']*dt
            if self.time-a['born']>2*p['rang_px_s']/p['rang_accel_px_s2']:
                self.remove(a);return
        elif kind=='axe':
            if a.get('stuck'):return
            self.ground(a,dt,p['axe_gravity_px_s2'])
            if a['grounded']:
                a['stuck']=True;a['vel']=[0,0];self.pose(a,4);self.emit('axe_stuck',id=a['id'],pos=a['pos'])
            return
        elif kind=='debris':
            if self.time<a['fall_at']:return
            if not a.get('released'):
                a['released']=True
                self.emit('props',id=a['id'],values=dict(DRAW=True,DAMAGE=10))
            a['vel'][1]+=p['debris_gravity_px_s2']*dt
        nx=x+a['vel'][0]*dt;ny=y+a['vel'][1]*dt

        sweep=(min(x,nx),min(y,ny),w+abs(nx-x),h+abs(ny-y))
        if kind in ('fire','disc','rang','debris') and any(overlap(sweep,s) for s in self.pack.solids):
            self.remove(a,'BLOCKED');return
        a['pos']=[nx,ny]

    def _tick(self,dt):
        self.time+=dt
        if self.state=='small':self._small_tick(dt)
        elif self.state=='final_horn':
            self.ground(self.small,dt,self.pack.entities['small']['gravity_px_s2'])
            if self.time+1e-8 >= self.deadline:
                self.state='center';self.small['action']='idle';self.emit('transition_to_center')
        elif self.state=='center':
            a=self.small;target=self.large['pos'][0]+self.large['size'][0]/2-a['size'][0]/2
            delta=target-a['pos'][0];sign=1 if delta>0 else -1;self.facing(a,sign);self.pose(a,5)
            a['vel'][0]=sign*self.pack.entities['small']['run_px_s'] if abs(delta)>5 else 0
            self.ground(a,dt,platforms=False)
            if abs(delta)<=5:
                self.state='summon';self.deadline=self.time
        elif self.state=='summon' and self.time>=self.deadline:
            self.state='large';self.emit('retire_small',id=self.small['id'])
            self.emit('props',id=self.large['id'],values=dict(DRAW=True))
            self.anim(self.large,0,self.pack.entities['large']['rise_s'])
            self._big_state('rise',self.pack.entities['large']['rise_s'])
            self._fall_spikes();self.emit('shake',magnitude=12,duration=2600)
        elif self.state=='large':self._large_tick(dt)
        elif self.state=='death' and self.time>=self.large['until']:
            self.state='done';self.emit('victory')
        pending,self.pending=self.pending,[]
        for when,kind,oid in pending:
            if when>self.time+1e-9:self.pending.append((when,kind,oid));continue
            if kind=='wave':self._wave();continue
            owner=self.small if oid==self.small['id'] and self.state in ('small','center') else self.large if oid==self.large['id'] and self.state=='large' else self.actors.get(oid)
            if owner and (owner.get('kind')!='npc' or self.target(owner,self.pack.entities['npc']['release_px'])):
                self._projectile(owner,kind)
        for a in list(self.actors.values()):
            if a['kind']=='npc':self._npc_tick(a,dt)
            elif a['kind']=='egg':
                self.ground(a,dt,700)
                if a['grounded'] and 'landed_at' not in a:
                    a['landed_at']=self.time
                if a['grounded'] and self.time+1e-8>=a['landed_at']+self.pack.entities['npc']['landing_settle_s']:
                    pos=[a['pos'][0]+7,a['pos'][1]+a['size'][1]-110]
                    duration=self.pack.entities['npc']['emerge_s']
                    child=self._new('npc',a['species'],pos,hp=a['hp'],pose=1,grounded=a['grounded'],
                                    owner=a['id'],action='emerge',until=self.time+duration,next_attack=self.time+duration)
                    self.anim(child,1,duration)
                    self.emit('hatch',id=child['id'],egg_id=a['id'],landed_at=a['landed_at'],
                              ground_wait_s=self.time-a['landed_at'],emerge_s=duration)
                    self.remove(a)
            else:
                if a.get('warning') and not a['egg_spawned'] and self.time+1e-8 >= a['spawn_at']:
                    self._spawn_egg(a)
                self._flying_tick(a,dt)
        if self.time>=self.next_sync:
            self.next_sync+=.05
            visible=([self.small] if self.state in ('small','final_horn','center','summon') else [self.large] if self.state=='large' else [])
            for a in visible+list(self.actors.values()):
                self.emit('move',id=a['id'],pos=a['pos'])
                if a.get('kind') in ('disc','fire'):
                    facing=round(math.degrees(math.atan2(a['vel'][1],a['vel'][0])))%360
                    self.emit('props',id=a['id'],values=dict(FACING=facing))

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        if self.state=='waiting':return self.drain()
        self.remainder+=dt
        while self.remainder+1e-10>=self.STEP:
            self.remainder-=self.STEP;self._tick(self.STEP)
        return self.drain()

    def add_damage(self,value,objid=None,player_id=None):
        if value<=0:return []
        if objid in (self.small['id'],self.large['id']):
            if (objid==self.small['id'] and self.state=='small') or (objid==self.large['id'] and self.state=='large' and self.large['action']!='rise'):
                if objid == self.small['id'] and self.combat_phase == 'approach':
                    self._enter_phase('body')
                required, _ = self.phase_requirements()
                self.phase_damage += min(value, max(0, required-self.phase_damage))
                self.damage_received+=value;self.emit('props',id=objid,values=dict(HITPOINTS=1))
            return self.drain()
        a=self.actors.get(objid)
        if a and a.get('hp',0)>0:
            a['hp']=max(0,a['hp']-value)
            if a['hp']==0:self.remove(a,'HITPOINTS')
            else:self.emit('props',id=objid,values=dict(HITPOINTS=a['hp']))
        return self.drain()

    def native_destroy(self,oid,reason):
        a=self.actors.get(oid)
        if a:self.remove(a,'HITPOINTS' if reason==2 else 'BLOCKED' if reason==5 else 'INTERNAL')
        return self.drain()

    def status(self):
        required, duration = self.phase_requirements()
        return dict(state=self.state,time=round(self.time,3),layout=self.pack.scene['layout'],wave=self.wave,
                    volley=self.volley,large_action=self.large['action'],small_action=self.small['action'],
                    small_pos=self.small['pos'],large_pos=self.large['pos'],actors=len(self.actors),
                    damage=self.damage_received,shots=self.shots,kills=self.npc_kills,fallen_spikes=len(self.fallen),
                    combat_phase=self.combat_phase,phase_elapsed=round(self.phase_elapsed(),3),
                    phase_damage=self.phase_damage,required_damage=required,min_s=duration,
                    damage_ready=self.phase_damage>=required,time_ready=self.phase_elapsed()+1e-8>=duration,
                    phase_history=copy.deepcopy(self.phase_history))
