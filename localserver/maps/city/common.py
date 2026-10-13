'Live City actors, finite projectiles and collision; no runtime timeline playback.'
import copy
import math
import random
from .. import MapController
from .scene import CityPack
from .collision import CollisionMixin,Grid,motion_bounds
from .npcs import NpcMixin

def center(a):
    return [a['pos'][0]+a['size'][0]/2, a['pos'][1]+a['size'][1]/2]

def overlap(a, b):
    return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]

def sweep(start, end, size, box):
    lo, hi = 0., 1.
    for p,q,w,r,s in zip(start,end,size,box[:2],box[2:]):
        d=q-p
        if abs(d)<1e-9:
            if p<=r-w or p>=r+s: return None
        else:
            a,b=(r-w-p)/d,(r+s-p)/d
            lo,hi=max(lo,min(a,b)),min(hi,max(a,b))
            if hi<lo: return None
    return lo if hi>=0 and lo<=1 else None

def approach(a, b, amount):
    return a+max(-amount,min(amount,b-a))

class CityController(CollisionMixin,NpcMixin,MapController):
    KIND='mid'

    def __init__(self, pack=None, seed=0):
        self.pack=pack or CityPack(self.KIND)
        self.kind=self.pack.kind; self.name='city_'+self.kind; self.seed=seed
        self.prepare()

    def prepare(self):
        self.rng=random.Random(self.seed); self.time=0.; self.state='waiting'; self.phase=0
        self.actors={}; self.events=[]; self.pending=[]; self.players={}; self.retired=set()
        self.seen_damage=set(); self.next_id=2000; self.next_wire=0.; self.kills=0
        self.tick_boxes=[]; self.tick_surfaces=[]; self.boss=None
        self.next_worm=self.pack.entities['worm']['first_s']; self.worm=None
        self.contact_cooldowns={};self.next_contacts=0.
        self.solid_grid=Grid([(b,0) for b in self.pack.solids]);self.platform_grid=Grid([(b,0) for b in self.pack.ledges])
        for i,p in self.pack.templates.items():
            if int(i) in self.pack.scene['cargo_ids'] or int(i) in self.pack.scene['sentry_ids']:
                a=self.make('cargo' if int(i) in self.pack.scene['cargo_ids'] else 'sentry', p, int(i))
                a['next_attack']=self.rng.uniform(.1,.8)
                self.actors[a['id']]=a

    def emit(self,kind,**data): self.events.append(dict(kind=kind,at=self.time,**data))
    def drain(self):
        result,self.events=self.events,[]
        return result
    def start(self):
        if self.state=='waiting':
            self.state='opening'; self.on_start()
    def on_start(self): pass
    def encounter_step(self,dt): pass

    def set_players(self, players):
        self.players={key:[float(v)/256 for v in p['center']] for key,p in players.items() if p.get('alive',True)}

    def target(self,a,distance=math.inf):
        c=center(a)
        values=sorted(((math.dist(c,p),str(key),p) for key,p in self.players.items()))
        return values[0][2] if values and values[0][0]<=distance else None

    def face(self,a,dx):
        facing=0 if dx>=0 else 180
        if a['facing']!=facing:
            a['facing']=facing; self.emit('props',id=a['id'],values=dict(FACING=facing))
    def pose(self,a,pose):
        if a['pose']!=pose:
            a['pose']=pose; self.emit('props',id=a['id'],values=dict(POSE=pose))
    def anim(self,a,slot,duration): self.emit('anim',id=a['id'],anim=slot,duration=duration)
    def sound(self,a,name): self.emit('sound',id=a['id'],name=name if '.' in name else 'city.'+name)
    def world_sound(self,name): self.emit('sound',id=0,name=name if '.' in name else 'city.'+name)
    def props(self,a,**values): self.emit('props',id=a['id'],values=values)

    def make(self,kind,p,oid):
        return dict(id=oid,kind=kind,vid=p.get('vid',''),pos=list(self.pack.rect(p)[:2]),
                    size=list(self.pack.rect(p)[2:]),vel=[0.,0.],hp=p.get('hitpoints'),
                    born=self.time,expiry=math.inf,owner=0,facing=p.get('facing',0),pose=p.get('pose',1),
                    children=[],next_attack=self.time+1.,next_jump=0.,grounded=False,properties={},wire_y=p.get('ymp',0)/256)

    def new(self,kind,vid,pos,vel=(0.,0.),life=math.inf,**extra):
        p=self.pack.props(vid); a=self.make(kind,p,self.next_id); self.next_id+=1
        a.update(pos=list(pos),vel=list(vel),expiry=self.time+life)
        a.update(extra);a['wire_y']=a['pos'][1]; self.actors[a['id']]=a
        self.emit('create',actor=copy.deepcopy(a))
        return a

    def remove(self,a,reason='INTERNAL'):
        if a['id'] in self.retired: return
        self.retired.add(a['id']); self.actors.pop(a['id'],None)
        self.emit('remove',actor=copy.deepcopy(a),reason=reason,quiet=a['kind'] in ('arm','anchor','claw','worm','wormpart'))
        for oid in list(a['children']):
            if oid in self.actors: self.remove(self.actors[oid])
        if a['kind'] in ('flyer','infantry','skiff','hawk','copter') and reason=='HITPOINTS':
            self.kills+=1
        if a['kind']=='copter' and reason=='HITPOINTS':
            self.new('wreck','city.ccopter',a['pos'],vel=[a['vel'][0],80],life=5.,facing=a['facing'])
        if a['kind']=='skiff':

            for rider in list(self.actors.values()):
                if rider.get('carrier')==a['id']:
                    rider['carrier']=None; rider['vel']=[a['vel'][0]*.4,0]
                    self.props(rider,MOVE=('Properties_Move',{})); self.pose(rider,1)
        if a['kind']=='infantry' and a.get('hawk') in self.actors:
            self.remove(self.actors[a['hawk']])

    def add_damage(self,value,objid=None,player_id=None,event_id=None):
        if not math.isfinite(value) or value<0 or self.state in ('waiting','done'): return []
        if event_id is not None:
            key=(player_id,event_id)
            if key in self.seen_damage: return []
            self.seen_damage.add(key)
        a=self.actors.get(objid)
        if not a: return []
        if a['kind'] in ('clone','ghostshot'):
            self.world_sound('hologramdeath' if a['kind']=='clone' else 'hologramzap')
            self.emit('hologram_dissipate',id=a['id'],value=value)
            self.remove(a,'INTERNAL')
        elif value>0 and a['kind'] in ('midboss','body','claw','tank'):
            if a['kind']=='midboss':
                source=self.players.get(player_id)
                if source is None:source=self.target(a)
                a['hit_source']=list(source) if source is not None else None
            self.gated_damage(a,value)
        elif value>0 and a['hp'] is not None and a['hp']>0 and a['kind'] not in ('arm','hammer','chain'):
            a['hp']=max(0,a['hp']-value)
            if a['hp']==0: self.remove(a,'HITPOINTS')
            else: self.props(a,HITPOINTS=round(a['hp']))
        return self.drain()

    def gated_damage(self,a,value): pass

    def native_destroy(self,objid,reason):
        a=self.actors.get(objid)
        if not a: return []
        if a['kind'] in ('shot','missile','bomb','ghostshot') and reason in (3,4,5,6):
            if reason in (5,6) and a['kind'] in ('missile','bomb'): self.impact(a)
            else: self.remove(a,{3:'SHIELD',4:'DAMAGES',5:'BLOCKED',6:'LIFETIME'}[reason])
        elif a['kind'] in ('clone',) and reason in (2,3,4):
            self.world_sound('hologramdeath'); self.remove(a,'INTERNAL')
        elif a['kind'] in ('cargo','flyer','infantry','skiff','hawk','copter','sentry') and reason==2:
            self.remove(a,'HITPOINTS')

        return self.drain()

    def schedule(self,delay,callback,**data):
        self.pending.append(dict(at=self.time+delay,callback=callback,**data))

    def fire(self,owner,vid,target=None,direction=None,pos=None,kind='shot',**extra):
        p=self.pack.props(vid); size=self.pack.rect(p)[2:]
        origin=pos or center(owner)
        if direction is None:
            target=target or self.target(owner) or [origin[0],origin[1]+300]
            dx,dy=target[0]-origin[0],target[1]-origin[1]; length=max(.001,math.hypot(dx,dy))
            direction=[dx/length,dy/length]
        short=vid.split('.')[-1]
        speed=extra.pop('speed',self.pack.entities['projectile'].get(short+'_speed',183.11))
        life=extra.pop('life',p.get('lifetime',5000)/1000)
        properties=extra.setdefault('properties',{})
        if kind in ('shot','ghostshot'):
            properties['move']=dict(vel=dict(xvel=round(direction[0]*speed*65536/1000),yvel=round(direction[1]*speed*65536/1000)))
            extra['wire_linear']=True
        if kind=='bomb':properties.update(blocktype=0,removetype=0)
        shot=self.new(kind,vid,[origin[0]-size[0]/2,origin[1]-size[1]/2],
                      [d*speed for d in direction],life=life,owner=owner['id'],
                      facing=round(math.degrees(math.atan2(direction[1],direction[0]))) if p.get('rotate') else 0,**extra)
        sounds={'jtshot':'shoot','jsshot':'snipe','csbullet':'sentryshootc','bshot':'mbshoot',
                'gshot':'shoot','oshot':'sentryshootc','overseerb':'bomb','tmissile':'transpmissile','cmissile':'coptermissile'}
        if short in sounds: self.sound(shot,sounds[short])
        return shot

    def pending_fire(self,event):
        a=self.actors.get(event['owner'])
        if not a or self.state=='done': return
        self.fire(a,event['vid'],target=event.get('target'),direction=event.get('direction'),kind=event.get('shotkind','shot'))

    def impact(self,a):
        if a['id'] in self.retired: return
        if a['kind']=='missile':

            p=self.pack.entities['projectile']; target=self.target(a)
            angle=math.atan2(target[1]-center(a)[1],target[0]-center(a)[0]) if target else a.get('heading',math.pi/2)
            large=a.get('large',False)
            count=p['flak_large_count'] if large else p['flak_count'];vectors=[]
            for _ in range(count):
                if large:

                    velocity=[self.rng.uniform(-p['flak_large_x_speed'],p['flak_large_x_speed']),
                              self.rng.uniform(p['flak_large_y_min'],p['flak_large_y_max'])]
                else:

                    t=angle+math.radians(self.rng.uniform(-p['flak_small_half_angle'],p['flak_small_half_angle']))
                    speed=p['flak_speed']*self.rng.uniform(.6,1.5)
                    velocity=[math.cos(t)*speed,math.sin(t)*speed]
                speed=math.hypot(*velocity)
                self.fire(a,'city.cflak',direction=[v/speed for v in velocity],speed=speed)
                vectors.append(velocity)
            self.world_sound('transpmissilex' if a.get('large') else 'coptermissilex')
            self.emit('cluster_split',id=a['id'],count=count,vid=a['vid'],velocities=vectors)
        elif a['kind']=='bomb':
            self.new('blast','city.overseerbx',[a['pos'][0]-24,a['pos'][1]-24],life=.5)
            self.world_sound('bombx'); self.emit('bomb_explode',id=a['id'])
        self.remove(a,'BLOCKED')

    def spawn_flyer(self,pos=None):
        vid=self.rng.choice(('city.jtroop','city.jsnipe'))
        pos=pos or [self.rng.uniform(-1800,2800),-1248]
        return self.new('flyer',vid,pos,next_attack=self.time+self.rng.uniform(.05,.5),mode='approach',mode_until=self.time+.05)

    def spawn_infantry(self,pos,carrier=None,vid=None,facing=0):
        vid=vid or self.rng.choice(('city.baller','city.gunner','city.petter'))
        a=self.new('infantry',vid,pos,carrier=carrier,facing=facing)
        self.anim(a,7 if vid=='city.baller' else 8,.66)
        if vid=='city.baller':
            hammer=self.new('hammer','city.bball',[pos[0]-32,pos[1]+20],owner=a['id'])
            a['hammer']=hammer['id']; a['children'].append(hammer['id'])
            for _ in range(3):
                link=self.new('chain','city.bchain',pos,owner=a['id']); a['children'].append(link['id'])
        if vid=='city.petter':
            hawk=self.new('hawk','city.hawk',[pos[0]-24,pos[1]],owner=a['id'])
            a['hawk']=hawk['id']; self.anim(hawk,1,.264)
        return a

    def spawn_formation(self,side,initial=False):
        if sum(a['kind']=='skiff' for a in self.actors.values())>=self.pack.entities['npc']['formation_cap']: return
        x=-2176 if side==0 else 3232
        y=self.rng.uniform(25,352)
        stop_x,edge=self.skiff_stop(side,y)
        carrier=self.new('skiff','city.skiff',[x,y],facing=0 if side==0 else 180,
                         vel=[0,0],stop_x=stop_x,platform_edge=edge,
                         entering=True,
                         max_speed=self.rng.uniform(390.625,453.125),arrived=False,
                         next_attack=self.time,depart=self.time+(self.rng.uniform(0,9) if initial else .66))
        self.spawn_infantry([x+36,y-20],carrier=carrier['id'],facing=carrier['facing'])
        return carrier

    def ground_physics(self,a,dt):
        return self.move_actor(a,dt,self.pack.entities['npc']['gravity_px_s2'],platforms=True)

    def hammer_step(self,a):
        h=self.actors.get(a.get('hammer'))
        if not h: return
        active=a.get('hammer_start',math.inf)<=self.time<=a.get('hammer_until',-1)
        sign=1 if a['facing']==0 else -1
        distance=180 if active else 35
        c=center(a); h['pos']=[c[0]+sign*distance-16,c[1]-16]
        if h.get('active')!=active:
            h['active']=active
            self.props(h,TERRAINTYPE=1 if active else 0,DAMAGE=15 if active else 0)
            if active: self.emit('hammer_extended',id=h['id'])
        links=[self.actors[i] for i in a['children'] if i in self.actors and self.actors[i]['kind']=='chain']
        for n,b in enumerate(links,1): b['pos']=[c[0]+sign*distance*n/4-6,c[1]-6]

    def launch_missile(self,a,large):
        origin=center(a)
        if large:origin=self.emit_point(a,2)
        elif self.pack.emitters.get(a['vid'],{}).get('1'):origin=self.emit_point(a,1)
        m=self.fire(a,'city.tmissile' if large else 'city.cmissile',pos=origin,direction=[0,-1 if large else 1],kind='missile',
                    large=large,armed=not large,heading=-math.pi/2 if large else math.pi/2,
                    properties=dict(damage=0,blocktype=0) if large else {})
        m['vel']=[0,-10 if large else 10]
        if large: self.props(m,DAMAGE=0,BLOCKTYPE=0)
        return m

    def projectiles_step(self,dt):
        r=self.pack.entities['projectile']
        for a in list(self.actors.values()):
            if a['id'] in self.retired: continue
            kind=a['kind']
            if kind=='wreck':
                a['vel'][1]+=350*dt
                a['pos']=[a['pos'][i]+a['vel'][i]*dt for i in (0,1)]
            if self.time>=a['expiry']:
                if kind in ('missile','bomb'): self.impact(a)
                else: self.remove(a,'LIFETIME')
                continue
            if kind not in ('shot','ghostshot','missile','bomb'): continue
            if kind=='missile':
                target=self.target(a)
                if target:
                    c=center(a); angle=math.atan2(target[1]-c[1],target[0]-c[0]); diff=(angle-a['heading']+math.pi)%math.tau-math.pi
                    a['heading']+=max(-math.radians(r['missile_turn_deg_s'])*dt,min(math.radians(r['missile_turn_deg_s'])*dt,diff))
                    previous_dy=a.get('target_dy',target[1]-c[1]);a['target_dy']=target[1]-c[1]
                    close=math.dist(c,target)<110
                    crossed=previous_dy*a['target_dy']<=0 and abs(target[0]-c[0])<600
                    aligned=abs(a['target_dy'])<36 and abs(target[0]-c[0])<420
                    if self.time-a['born']>.10 and (close or crossed or aligned):
                        self.impact(a);continue
                speed=min(r['missile_max_speed'],10+(self.time-a['born'])*r['missile_accel'])
                a['vel']=[math.cos(a['heading'])*speed,math.sin(a['heading'])*speed]
                if not a['armed'] and self.time-a['born']>=r['large_arm_s']:
                    a['armed']=True; self.props(a,DAMAGE=15,BLOCKTYPE=4); self.emit('missile_armed',id=a['id'])
                if not a['large'] and self.time>=a.get('next_trail',0):
                    self.new('blast','city.cmissilet',a['pos'],life=1.8)
                    a['next_trail']=self.time+.18
            if kind=='bomb': a['vel'][1]+=r['bomb_gravity']*dt
            old=list(a['pos']); new=[old[i]+a['vel'][i]*dt for i in (0,1)]

            collide=kind=='bomb' or (kind=='missile' and a['armed']) or (kind=='shot' and a['vid']!='city.bshot')
            hits=[]
            if collide:
                for box,oid in self.solid_grid.query(motion_bounds(old,new,a['size'])):
                    if oid and oid not in self.actors:continue
                    t=sweep(old,new,a['size'],box)
                    if t is not None: hits.append((t,oid,box))
            if hits:
                t,oid,box=min(hits); a['pos']=[old[i]+(new[i]-old[i])*t for i in (0,1)]
                cargo=self.actors.get(oid)
                if cargo and cargo['kind']=='cargo':
                    self.cargo_damage(oid,self.pack.dynamic_templates[a['vid']].get('damage',15),a)
                if kind=='bomb':

                    axis=1 if old[1]+a['size'][1]<=box[1]+2 or old[1]>=box[1]+box[3]-2 else 0
                    sign=1 if a['vel'][axis]>0 else -1
                    a['pos'][axis]-=sign*.2;a['vel'][axis]*=-.55
                    self.emit('bomb_bounce',id=a['id'])
                elif kind=='missile':self.impact(a)
                else: self.remove(a,'BLOCKED')
            else: a['pos']=new

    def worm_step(self,dt):
        r=self.pack.entities['worm']
        if self.worm is None and self.time>=self.next_worm:
            targets=list(self.players.values())
            target=self.rng.choice(targets) if targets else [800,900]
            x=max(-650,min(2500 if self.kind=='mid' else 1600,target[0]+self.rng.uniform(-180,180)))
            head=self.new('worm','city.whead',[x,1088],vel=[0,-r['launch_speed']],hp=None,facing=270,pose=3,
                          trail=[[x,1088+i*8] for i in range(60,-1,-1)])
            self.worm=head['id']
            for n in range(r['segments']):
                a=self.new('wormpart','city.wbody',[x,1088+(n+1)*r['spacing']],hp=None,facing=270,pose=3,owner=head['id'])
                head['children'].append(a['id'])
            self.emit('worm_eruption',id=head['id'],x=x)
        head=self.actors.get(self.worm)
        if not head: return

        head['vel'][1]+=r['gravity']*dt
        head['pos']=[head['pos'][i]+head['vel'][i]*dt for i in (0,1)]

        target=self.target(head)
        if target and head['pos'][1]<750:
            head['vel'][0]=approach(head['vel'][0],max(-180,min(180,(target[0]-center(head)[0])*.25)),75*dt)
        trail=head['trail']; trail.append(list(head['pos']))
        if len(trail)>500: del trail[:100]
        head['facing']=round(math.degrees(math.atan2(head['vel'][1],head['vel'][0])))
        for n,oid in enumerate(head['children'],1):
            a=self.actors.get(oid)
            if not a: continue
            remaining=n*r['spacing']; last=trail[-1]
            for point in reversed(trail[:-1]):
                distance=math.dist(last,point)
                if distance>=remaining:
                    ratio=remaining/max(.001,distance)
                    a['pos']=[last[i]+(point[i]-last[i])*ratio for i in (0,1)]
                    a['facing']=round(math.degrees(math.atan2(last[1]-point[1],last[0]-point[0])))
                    break
                remaining-=distance; last=point
        if head['vel'][1]>0 and head['pos'][1]>1650:
            self.remove(head); self.worm=None; self.next_worm=self.time+self.rng.uniform(r['wait_min_s'],r['wait_max_s'])

    def victory(self):
        if self.state=='done': return
        self.state='done'; self.pending=[]
        for a in list(self.actors.values()):
            if a['kind'] not in ('cargo','corpse','cart'): self.remove(a)
        self.emit('victory')

    def step(self,dt):
        if not math.isfinite(dt) or dt<0: raise ValueError('invalid dt')
        if self.state=='waiting': return self.drain()
        end=self.time+dt
        while self.time<end-1e-9:
            step=min(1/60,end-self.time); self.time+=step
            if self.kind=='boss':self.cargo_step(step)

            cargo=[(a['pos']+a['size'],a['id']) for a in self.actors.values() if a['kind']=='cargo']
            self.tick_boxes_with_ids=[(b,0) for b in self.pack.solids]+cargo
            self.tick_boxes=[b for b,_ in self.tick_boxes_with_ids]
            self.solid_grid=Grid(self.tick_boxes_with_ids)
            carts=[a['pos']+a['size'] for a in self.actors.values() if a['kind']=='cart']
            self.tick_surfaces=self.tick_boxes+self.pack.ledges+carts
            self.platform_grid=Grid([(b,0) for b in self.pack.ledges+carts])
            due=sorted((e for e in self.pending if e['at']<=self.time+1e-9),key=lambda e:e['at'])
            self.pending=[e for e in self.pending if e['at']>self.time+1e-9]
            for event in due: getattr(self,event['callback'])(event)
            if self.state!='done':
                self.encounter_step(step); self.npcs_step(step); self.worm_step(step)
            self.projectiles_step(step)
            if self.state!='done' and self.time>=self.next_contacts:
                self.next_contacts=self.time+.10;self.cargo_contacts()
            if self.time>=self.next_wire:
                self.next_wire=self.time+.05
                for a in self.actors.values():
                    if a['kind']=='cargo' and a.get('wire_y',a['pos'][1])!=a['pos'][1]:
                        self.emit('move',id=a['id'],pos=list(a['pos']))
                    if a['kind']=='cargo':a['wire_y']=a['pos'][1]
                    if a['kind'] not in ('sentry','cargo','blast','fx','cart','arm','anchor','tank_wreck') and not a.get('wire_linear'):
                        self.emit('move',id=a['id'],pos=list(a['pos']))
                    if a['kind']=='missile': self.props(a,FACING=round(math.degrees(a['heading'])))
                    if a['kind'] in ('worm','wormpart') and a.get('wire_facing')!=a['facing']:
                        a['wire_facing']=a['facing'];self.props(a,FACING=a['facing'])
        return self.drain()

    def status(self):
        from collections import Counter
        return dict(time=self.time,state=self.state,phase=self.phase,kills=self.kills,
                    boss_hp=self.boss['hp'] if self.boss else None,counts=dict(Counter(a['kind'] for a in self.actors.values())))
