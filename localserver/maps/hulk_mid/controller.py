'Independent damage/time gates and targeting. No capture timelines.'
import math
import random
from .. import MapController
from .scene import HulkMidPack

class HulkMidController(MapController):
    name='hulk_mid'
    def __init__(self,pack=None,seed=0):
        self.pack=pack or HulkMidPack()
        self.seed=seed
        self.rule=self.pack.entities['boss']
        self.prepare()

    def prepare(self):
        self.rng=random.Random(self.seed)
        self.time=0.;self.state='waiting';self.players={};self.events=[]
        self.bosses={b['id']:dict(id=b['id'],pos=[float(b['xmp']),float(b['ymp'])],vel=[0.,0.],
            activated=False,activator=None,target=None,damage_by_player={},first_hit=None,
            phase=1,damage=0,late_damage=0,action='idle',summons=0,dead=False,
            next_fan=math.inf,fan_release=None,next_summon=math.inf,summon_start=None,
            mini_index=0,next_mini=math.inf,next_trail=.82,cycle_origin=0.) for b in self.pack.scene['boss_spawns']}

    def set_players(self,players):
        self.players={k:v for k,v in players.items() if v.get('alive',True)}

    def emit(self,kind,**values):
        self.events.append(dict(kind=kind,at=self.time,**values))

    def drain(self):
        events,self.events=self.events,[]
        return events

    def target(self,b):
        candidates=[(damage,k) for k,damage in b['damage_by_player'].items() if k in self.players]
        if candidates:b['target']=max(candidates,key=lambda p:(p[0],p[1]==b['target']))[1]
        elif b['activator'] in self.players:b['target']=b['activator']
        else:b['target']=min(self.players,key=lambda k:math.dist(self.players[k]['center'],[v+16384 for v in b['pos']]),default=None)
        return self.players.get(b['target'])

    def add_damage(self,value,objid=None,player_id=None):
        b=self.bosses.get(objid)
        if not b or b['dead'] or value<=0:return []
        player_id=player_id if player_id is not None else next(iter(self.players),None)
        b['damage_by_player'][player_id]=b['damage_by_player'].get(player_id,0)+value
        if b['first_hit'] is None:b['first_hit']=self.time
        if not b['activated']:self.activate(b,player_id)
        field,cap=('damage',self.rule['first_damage']) if b['phase']==1 else ('late_damage',self.rule['remaining_damage'])
        b[field]=min(cap,b[field]+value)
        return self.drain()

    def activate(self,b,player_id):
        b.update(activated=True,activator=player_id,target=player_id,action='chase',
            cycle_origin=self.time,next_fan=self.time+self.rule['fan_period_s'])
        self.emit('activate',id=b['id'],player=player_id)

    def _summon(self,b):
        if not any(b['vel']):
            b['vel']=list(b.get('last_velocity',[0.,0.]))
        speed=math.hypot(*b['vel'])
        heading=[v/speed for v in b['vel']] if speed else [0.,0.]

        start=min(speed/256,self.rule['late_slow_px_s'])
        b.update(action='summon',summon_start=self.time,mini_index=0,
            summon_heading=heading,summon_initial_speed=start,
            next_mini=self.time+self.rule['summon_animation_s'],fan_release=None,
            next_fan=self.time+self.rule['summon_period_s']/2,
            next_summon=self.time+self.rule['summon_period_s'])
        b['summons']+=1
        self.emit('summon',id=b['id'],round=b['summons'])

    def step(self,dt):
        if not math.isfinite(dt) or dt<0:raise ValueError('invalid dt')

        self.time+=dt
        self.state='fighting'
        r=self.rule
        for b in self.bosses.values():
            if b['dead']:continue
            center=[v+16384 for v in b['pos']]
            if not b['activated']:
                near=[k for k,p in self.players.items() if max(abs(p['center'][j]-center[j]) for j in range(2))<=r['activation_px']*256]
                if near:self.activate(b,min(near,key=lambda k:math.dist(self.players[k]['center'],center)))
            if self.time>=b['next_trail']:
                jitter=r.get('trail_jitter',0.)
                b['next_trail']+=r['trail_period_s']*self.rng.uniform(1-jitter,1+jitter)
                self.emit('trail',id=b['id'],pos=list(b['pos']))
            target=self.target(b)
            if not b['activated'] or target is None:
                b['vel']=[0.,0.];continue
            elapsed=self.time-b['first_hit'] if b['first_hit'] is not None else 0
            if b['phase']==2 and b['late_damage']>=r['remaining_damage'] and elapsed>=r['death_time_s']:
                b.update(dead=True,action='dead',fan_release=None,next_mini=math.inf)
                self.emit('boss_dead',id=b['id']);continue
            gated=b['phase']==1 and b['damage']>=r['first_damage'] and elapsed>=r['first_time_s']
            if b['action']=='chase' and b['fan_release'] is None and (gated or (b['phase']==2 and self.time>=b['next_summon'])):
                b['phase']=2;self._summon(b)
            if b['action']=='summon':
                target_speed=min(b['summon_initial_speed'],r['summon_drift_px_s'])
                progress=min(1.,(self.time-b['summon_start'])/r['summon_brake_s'])
                speed=b['summon_initial_speed']+(target_speed-b['summon_initial_speed'])*progress
                b['vel']=[v*speed*256 for v in b['summon_heading']]
                if self.time>=b['next_mini'] and b['mini_index']<4:
                    n=b['mini_index'];b['mini_index']+=1;b['next_mini']+=r['mini_spacing_s']
                    self.emit('mini',id=b['id'],index=n,pos=[v+8192 for v in b['pos']],angle=n*90.)
                if b['mini_index']==4 and self.time>=b['next_mini']:
                    b.update(action='chase',cycle_origin=self.time)
            else:
                cycle=(self.time-b['cycle_origin'])%r['motion_period_s']
                speed=0 if cycle<r['pause_s'] else (
                    (r['late_fast_px_s'] if b['phase']==2 else r['fast_px_s']) if cycle<r['pause_s']+r['dash_s'] else
                    (r['late_slow_px_s'] if b['phase']==2 else r['slow_px_s']))
                dx,dy=(target['center'][j]-center[j] for j in range(2));norm=math.hypot(dx,dy)
                b['vel']=[speed*256*dx/norm,speed*256*dy/norm] if norm else [0.,0.]
                if speed:b['last_velocity']=list(b['vel'])
                if self.time>=b['next_fan']:
                    b['next_fan']=self.time+r['fan_period_s']
                    if norm<=r['fan_range_px']*256:
                        b['fan_release']=self.time+r['fan_windup_s']
                        b['fan_angle']=math.degrees(math.atan2(dy,dx))
                        self.emit('fan_prepare',id=b['id'],angle=b['fan_angle'],pos=list(b['pos']))
                if b['fan_release'] is not None and self.time>=b['fan_release']:
                    self.emit('fan_release',id=b['id']);b['fan_release']=None
            self.emit('move',id=b['id'],velocity=list(b['vel']),dt=dt)
        if all(b['dead'] for b in self.bosses.values()):
            if self.state!='done':self.state='done'
        return self.drain()

    def status(self):
        return dict(time=self.time,state=self.state,bosses={i:{k:b[k] for k in (
            'activated','target','first_hit','phase','damage','late_damage','action','summons','dead')} for i,b in self.bosses.items()})
