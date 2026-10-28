'Seeded live duel rules. Captures provide constants, never a timeline.'
import copy
import math
import random
from .. import MapController
from .scene import PatriarchPack
from .turrets import WallTurrets
from ... import game_settings

def sweep(origin, delta, box, size):
    """First swept AABB contact, including high-speed/thin-platform cases."""
    lo,hi=0.,1.
    for p,d,b,w,s in zip(origin,delta,box[:2],box[2:],size):
        near,far=b-s,b+w
        if abs(d)<1e-10:
            if not near<p<far:return None
        else:
            a,z=(near-p)/d,(far-p)/d
            lo,hi=max(lo,min(a,z)),min(hi,max(a,z))
            if hi<lo:return None
    return lo if hi>=0 and lo<=1 else None

class PatriarchController(MapController):
    name='sc_patriarch'
    STEP=1/120

    def __init__(self,pack=None,seed=0):
        self.pack=pack or PatriarchPack();self.seed=seed
        self.parameters=game_settings.balance('sc_patriarch');self.prepare()

    def prepare(self):
        self.rng=random.Random(self.seed);self.time=self.remainder=0.
        self.state='waiting';self.action='idle';self.until=math.inf
        self.players={};self.selected=None;self.selected_life=None
        self.events=[];self.actors={};self.next_id=2000;self.seen_damage=set()
        self.hp=max(1,round(self.pack.entities['boss']['hp']*self.parameters['enemy_hp']));self.boss_pos=[864.,576.]
        self.body_motion=None;self.facing=180;self.next_sync=0.;self.volley=0
        self.attack_bag=[];self.pending=[];self.rock_queue=[];self.last_attack=None
        self.turrets=WallTurrets(self)

    def emit(self,kind,**data):self.events.append(dict(kind=kind,at=self.time,**copy.deepcopy(data)))
    def drain(self):events,self.events=self.events,[];return events

    def set_players(self,players):
        self.players={k:dict(p,center=[v/256 for v in p['center']]) for k,p in players.items()
                      if p.get('alive',True) and not p.get('spectator',False) and p.get('loaded',True)
                      and all(math.isfinite(v) for v in p['center'])}

    def start(self):
        if self.state!='waiting':return
        self.state='selecting';self.until=self.time+self.pack.entities['boss']['select_delay_s']
        self.emit('activated',id=4)

    def _select(self):
        if not self.players:return False
        self.selected=self.rng.choice(sorted(self.players,key=repr))
        p=self.players[self.selected];self.selected_life=p.get('life_id',0)
        self.emit('selected',player=self.selected,name=p.get('name','Player'),life=self.selected_life)
        self.emit('props',id=4,values=dict(INVULNERABLE=False))
        self.state='fighting';self.action='idle'
        self.turrets.activate()
        self.until=self.time+self.attack_gap(self.pack.entities['boss']['first_attack_delay_s'])
        return True

    def attack_gap(self,seconds):
        return seconds/self.parameters['boss_attack_rate']

    def target(self):
        p=self.players.get(self.selected)
        return p['center'] if p else None

    def _abandon(self):
        self.turrets.stop()
        for oid in list(self.actors):self.remove(oid,'INTERNAL')
        self.pending=[];self.rock_queue=[];self.body_motion=None
        self.boss_pos=[864.,576.];self.emit('body_move',pos=self.boss_pos,duration=.001)
        self.emit('props',id=4,values=dict(INVULNERABLE=True,FACING=180))
        self.emit('selection_reset',player=self.selected)
        self.selected=self.selected_life=None;self.state='selecting';self.action='idle'
        self.until=self.time+self.pack.entities['lifecycle']['reselect_delay_s']

    def add_damage(self,value,objid=None,player_id=None,event_id=None):
        if objid!=4 or self.state!='fighting' or not math.isfinite(value) or value<=0:return []
        if event_id is not None:
            if event_id in self.seen_damage:return []
            self.seen_damage.add(event_id)
        self.hp=max(0,self.hp-int(value));self.emit('props',id=4,values=dict(HITPOINTS=self.hp))
        if self.hp==0:self._die()
        return self.drain()

    def _die(self):
        if self.state in ('dying','done'):return
        self.state='dying';self.until=self.time+self.pack.entities['boss']['death_s']
        self.turrets.stop()
        self.pending=[];self.rock_queue=[];self.body_motion=None
        for oid in list(self.actors):self.remove(oid,'INTERNAL')

        self.emit('sound',id=4,name='sc.patriarchdeath')
        self.emit('remove',id=4,reason='HITPOINTS');self.emit('boss_dying')
        # Captured death updates move both return steps from behind the left
        # wall to its inner edge over 2000 ms (objects 14/15).
        for oid in (14,15):
            step=self.pack.templates[str(oid)]
            self.emit('actor_lerp',id=oid,pos=[self.pack.scene['left'],step['ymp']/256],duration=2.)
        self.emit('selection_reset',player=self.selected);self.selected=self.selected_life=None

    def native_destroy(self,objid,reason):
        if objid==4 and reason==2 and self.hp<=0:self._die()
        a=self.actors.get(objid)
        if a and a['kind'] in ('rock','bomb','snake','spirit') and reason in (3,4,5,6):

            self.remove(objid,{3:'SHIELD',4:'DAMAGES',5:'BLOCKED',6:'LIFETIME'}[reason])
        return self.drain()

    def new(self,vid,pos,kind,vel=(0.,0.),life=12.,**fields):
        oid=self.next_id;self.next_id+=1
        a=dict(id=oid,vid=vid,pos=list(pos),size=[16,16] if kind=='incen' else [32,32],
               kind=kind,vel=list(vel),born=self.time,until=self.time+life,**fields)
        self.actors[oid]=a;self.emit('create',actor=a);return a

    def remove(self,oid,reason='LIFETIME'):
        a=self.actors.pop(oid,None)
        if a:
            if a['kind']=='rock' and reason!='INTERNAL':
                self.emit('world_sound',name='sc.sliverx',pos=[a['pos'][0]+16,a['pos'][1]+16])
            self.emit('remove',id=oid,reason=reason)

    def animate(self,slot,duration):self.emit('anim',id=4,anim=slot,duration=duration)

    def body_move(self,pos,duration):
        self.body_motion=dict(start=self.time,end=self.time+duration,origin=list(self.boss_pos),dest=list(pos))
        self.emit('body_move',pos=pos,duration=duration)

    def choose_attack(self):
        weights=self.pack.entities['boss']['weights']
        choices=[k for k,v in weights.items() if v>0]
        if not choices:return 'charge'
        if len(choices)>1 and self.last_attack in choices:choices.remove(self.last_attack)
        kind=self.rng.choices(choices,[weights[k] for k in choices])[0]
        self.begin_attack(kind);return kind

    def begin_attack(self,kind):
        if self.state!='fighting':return
        self.last_attack=kind;self.action=kind;self.volley=0;self.pending=[]
        self.emit('attack',attack=kind,player=self.selected)
        if kind=='charge':
            r=self.pack.entities['charge'];self.animate(4,r['windup_s'])
            self.until=self.time+r['windup_s'];self.action='charge_ready'
        elif kind=='rocks':
            r=self.pack.entities['rocks'];self.animate(2,r['rise_s']);self.rock_queue=[]
            for i in range(r['count']):
                x=self.rng.uniform(768,944);y=self.rng.uniform(432,640)
                a=self.new('sc.rock',[x,704],'rock',phase='rising',rise_from=704.,rise_to=y,
                           rise_end=self.time+r['rise_s'],target_velocity=[0.,0.],life=r['life_s'])
                self.emit('actor_lerp',id=a['id'],pos=[x,y],duration=r['rise_s'])
                self.rock_queue.append(a['id'])
            self.until=self.time+r['rise_s']+.62
        elif kind=='acid':
            r=self.pack.entities['acid'];self.body_move([864,r['hover_y']],r['rise_s'])
            self.action='acid_rise';self.until=self.time+r['rise_s']/2
        elif kind=='snakes':
            r=self.pack.entities['snakes'];self.animate(6,r['interval_s'])
            self.action='snake_emit';self.until=self.time+r['emit_s']
        else:raise ValueError('unknown attack '+kind)

    def _release_rock(self):
        r=self.pack.entities['rocks']
        while self.rock_queue:
            a=self.actors.get(self.rock_queue.pop())
            if a is None:continue
            target=self.target() or [100,600]
            d=[target[j]-(a['pos'][j]+16) for j in (0,1)];n=math.hypot(*d) or 1
            a['phase']='released';a['vel']=[0.,0.]
            a['target_velocity']=[v/n*r['speed_px_s'] for v in d]
            self.animate(1,1.056);self.emit('sound',id=4,name='sc.patlob')
            self.emit('rock_launch',id=a['id'],target=target,velocity=a['target_velocity'])
            break
        if self.rock_queue:self.until=self.time+self.attack_gap(r['release_interval_s'])
        else:self._rest()

    def _bomb(self):
        r=self.pack.entities['acid']

        origin=[self.boss_pos[0]+4,self.boss_pos[1]+36]

        angle=self.rng.randint(r['throw_angle_min_deg'],r['throw_angle_max_deg'])
        radians=math.radians(angle)
        velocity=[math.cos(radians)*r['speed_px_s'],math.sin(radians)*r['speed_px_s']]
        self.new('sc.bomb',origin,'bomb',vel=velocity,life=6.)
        self.animate(5,.264);self.emit('sound',id=4,name='sc.patdrop')
        self.emit('bomb_throw',angle_deg=angle,origin=origin,velocity=velocity)
        self.volley+=1
        if self.volley<r['count']:self.until=self.time+self.attack_gap(r['interval_s'])
        else:
            self.action='acid_return';self.until=max(self.time,self.body_motion['end'] if self.body_motion else self.time)+r['hold_s']

    def _snakes(self):
        r=self.pack.entities['snakes'];origin=[self.boss_pos[0]-8,self.boss_pos[1]+37]
        shift=self.rng.uniform(-.12,.12)
        for i in range(r['count']):
            a=math.tau*i/r['count']+shift
            velocity=[math.cos(a)*r['speed_px_s'],math.sin(a)*r['speed_px_s']]
            self._snake_segment(origin,velocity)
            for segment in range(1,r['segments']):
                self.pending.append(dict(at=self.time+segment*r['segment_interval_s'],pos=list(origin),vel=velocity))
        self.emit('sound',id=4,name='sc.patshoot');self.emit('snake_volley',count=r['count'])
        self.volley+=1
        if self.volley<r['volleys']:
            self.action='snake_ready';self.until=self.time+max(0.,self.attack_gap(r['interval_s'])-r['emit_s'])
        else:self._rest()

    def _snake_segment(self,origin,velocity):
        r=self.pack.entities['snakes']
        self.new('sc.bomb',origin,'snake',vel=velocity,life=r['life_s'],
                 properties=dict(movetrail='sc.bomb',rotate=True,blocktype=4))

    def splash(self,pos):
        r=self.pack.entities['acid'];count=r['spread_count']
        first=None
        for i in range(count):
            angle=math.radians(i*r['spread_angle_step_deg'])
            velocity=[math.cos(angle)*r['spread_speed_px_s'],math.sin(angle)*r['spread_speed_px_s']]
            a=self.new('sc.incen',[pos[0],704],'incen',vel=velocity,life=r['spread_life_s'],grounded=False,
                       next_motion=self.time+r['spread_update_s'])
            first=first or a['id']
        self.emit('sound',id=first,name='sc.bombx');self.emit('acid_splash',pos=pos,count=count)

    def _rest(self):
        self.action='idle';self.until=self.time+self.attack_gap(self.pack.entities['boss']['recovery_s'])

    def _transition(self):
        if self.action=='idle':self.choose_attack()
        elif self.action=='charge_ready':
            r=self.pack.entities['charge'];self.animate(7,.924);self.emit('sound',id=4,name='sc.patcharge')
            self.emit('props',id=4,values=dict(FACING=180));self.body_move([r['left_x'],576],r['travel_s'])
            self.action='charge_left';self.until=self.time+r['travel_s']
        elif self.action=='charge_left':
            r=self.pack.entities['charge'];self.animate(7,.924);self.emit('sound',id=4,name='sc.patcharge')
            self.emit('props',id=4,values=dict(FACING=0));self.body_move([r['right_x'],576],r['travel_s'])
            self.action='charge_right';self.until=self.time+r['travel_s']
        elif self.action=='charge_right':self.emit('props',id=4,values=dict(FACING=180));self._rest()
        elif self.action=='rocks':self._release_rock()
        elif self.action=='acid_rise':self.action='acid_drop';self._bomb()
        elif self.action=='acid_drop':self._bomb()
        elif self.action=='acid_return':
            r=self.pack.entities['acid'];self.body_move([864,576],r['rise_s']);self.action='acid_land';self.until=self.time+r['rise_s']
        elif self.action=='acid_land':self._rest()
        elif self.action=='snake_ready':
            r=self.pack.entities['snakes'];self.animate(6,r['interval_s']);self.action='snake_emit';self.until=self.time+r['emit_s']
        elif self.action=='snake_emit':self._snakes()

    def _advance_actors(self,dt):
        boxes=self.pack.solids+[(32,224,960,32)]
        for a in list(self.actors.values()):
            if a['id'] not in self.actors:continue
            if self.time>=a['until']:
                self.remove(a['id']);continue
            kind=a['kind'];old=list(a['pos'])
            if kind=='rock':
                if a['phase']=='rising':
                    f=min(1,(self.time-a['born'])/(a['rise_end']-a['born']))
                    a['pos'][1]=a['rise_from']+(a['rise_to']-a['rise_from'])*f
                    if f>=1:a['phase']='waiting'
                    continue
                if a['phase']=='waiting':continue
                r=self.pack.entities['rocks'];goal=a['target_velocity']
                n=math.hypot(*goal) or 1
                for j in (0,1):
                    delta=goal[j]-a['vel'][j];limit=abs(goal[j])/n*r['acceleration_px_s2']*dt
                    a['vel'][j]+=max(-limit,min(limit,delta))
            elif kind=='incen':
                if a['grounded']:continue

                if self.time+1e-9<a['next_motion']:continue
                r=self.pack.entities['acid'];step=r['spread_update_s'];g=r['spread_gravity_px_s2']
                a['pos']=[old[0]+a['vel'][0]*step,old[1]+a['vel'][1]*step+.5*g*step*step]
                a['vel'][1]+=g*step;a['next_motion']+=step
                a['pos'][0]=max(32,min(976,a['pos'][0]))
                if a['pos'][1]>=720:
                    a['pos'][1]=720;a['vel']=[0.,0.];a['grounded']=True
                    self.emit('props',id=a['id'],values=dict(POSE=4))

                self.emit('move',id=a['id'],pos=a['pos'])
                continue
            delta=[v*dt for v in a['vel']]
            hits=[(hit,box) for box in boxes if (hit:=sweep(old,delta,box,a['size'])) is not None]
            if hits:
                hit,box=min(hits,key=lambda h:h[0]);a['pos']=[old[j]+delta[j]*hit for j in (0,1)]
                if kind=='bomb' and delta[1]>0 and box[1]==736:self.splash(a['pos'])
                self.remove(a['id'],'BLOCKED');continue
            a['pos']=[old[j]+delta[j] for j in (0,1)]

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')
        if self.state=='waiting':return self.drain()
        self.remainder+=dt
        while self.remainder+1e-10>=self.STEP:
            self.remainder-=self.STEP;self.time+=self.STEP
            if self.state=='fighting':
                p=self.players.get(self.selected)
                if p is None or p.get('life_id',0)!=self.selected_life:self._abandon()
            if self.body_motion:
                m=self.body_motion;f=min(1,(self.time-m['start'])/(m['end']-m['start']))
                self.boss_pos=[m['origin'][j]+(m['dest'][j]-m['origin'][j])*f for j in (0,1)]
                if f>=1:self.body_motion=None
            if self.time+1e-9>=self.until:
                if self.state=='selecting':
                    if not self._select():self.until=self.time+.1
                elif self.state=='fighting':self._transition()
                elif self.state=='dying':self.state='done';self.emit('victory')
            due=[p for p in self.pending if p['at']<=self.time+1e-9]
            self.pending=[p for p in self.pending if p['at']>self.time+1e-9]
            for p in due:self._snake_segment(p['pos'],p['vel'])
            self.turrets.step(self.STEP)
            self._advance_actors(self.STEP)
        return self.drain()

    def status(self):
        return dict(state=self.state,action=self.action,time=self.time,hp=self.hp,selected=self.selected,
                    selected_life=self.selected_life,body=list(self.boss_pos),actors=len(self.actors),turrets=self.turrets.status())
