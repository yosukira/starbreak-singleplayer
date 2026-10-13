"""Native EFB moving platforms, snow ambushes, eggs and breakable barriers.

Native fields/actions/outlets are retained. Proximity triggers, species RNG,
rearming and debris choice remain SIM, not reconstructed private watchers.
"""
import math
from collections import Counter
from .physics import TriangleTerrain as Terrain
from .abilities import Ownership,Projectiles,NativeTrack,rect,center,nearest,prop,move,changed
from .mites import MITES

MECHANISMS={'ice.sambush','ice.lambush','ice.miteball','ice.dwall','ice.fwall','ice.cnest',
    'ice.wledge','ice.track','ice.ddebris'}

class ExplorationMechanics(Ownership):
    def __init__(self,host):
        super().__init__(host,'_efb_mechanic');self.states={};self.tracks={};self.pending=[];self.now=host.room_time
        self.projectiles=Projectiles(host);self.projectiles.tag='_efb_mechanic_projectile'
        self._physics_clock=None;self.platform_deltas={}

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        self.host.record('efb_mechanic_policy',evidence='D original waypoints/loop, snow/egg/barrier fields; outlet FIT, trigger/species/debris choice SIM')

    def add_actor(self,oid):
        row=self.host.actors.get(oid)
        if not row or row.get('dead') or row.get('_efb_enemy') or oid in self.retired:return False
        if oid in self.owned:return True
        if row['vid'] not in MECHANISMS:return False
        self.own(oid);vid=row['vid'];self.states[oid]=dict(vid=vid,phase='idle',used=False,child=None,next=0.)
        if vid!='ice.miteball' and row['props'].get('move') and not row['props']['move'].get('relativeto'):
            self.tracks[oid]=NativeTrack(row['pos'],row['props']['move'])
        if vid=='ice.miteball':
            x,y,w,h=rect(row);self.states[oid].update(body=dict(x=x,y=y,w=w,h=h,vy=0.,grounded=False),
                species=self.host.rng.choice(sorted(MITES)),landed_at=None)
            row['props']['move']=dict(vel=dict(xvel=0,yvel=0))
        return True

    def pre_physics(self,dt):
        clock=getattr(self.host,'wire_end_ms',round(self.host.room_time*1000))
        if clock==self._physics_clock:return
        self._physics_clock=clock;self.platform_deltas={}
        for oid,track in list(self.tracks.items()):
            row=self.host.actors.get(oid)
            if row is None:self.tracks.pop(oid,None);continue
            old=row['pos'];new=list(track.position(clock))
            if old!=new:
                row['pos']=new
                if row['props'].get('terraintype') in (1,2):
                    changed(self.host);self.platform_deltas[oid]=(old,new,(row['props'].get('wmp',0),row['props'].get('hmp',0)))

    def _start_warning(self,oid,state):
        row=self.host.actors[oid];vid=row['vid'];state.update(used=True,phase='warning',hide_at=self.now+(1.65 if vid=='ice.sambush' else .9))
        prop(self.host,oid,'draw',True)
        self.host.presentation.attack(oid,vid,1 if vid=='ice.sambush' else 0,1650 if vid=='ice.sambush' else 900,token=self.now)
        self.pending.append(dict(at=self.now+(1.056 if vid=='ice.sambush' else .9),owner=oid,
            kind='egg' if vid=='ice.sambush' else 'barrier'))
        self.host.record('efb_ambush_trigger',id=oid,vid=vid,evidence='D native warning; proximity and once-per-authored-site trigger SIM')

    def _egg(self,owner):
        row=self.host.actors[owner];x,y,w,h=rect(row);facing=row['props'].get('facing',270)
        props=self.host.pack.props('ice.miteball');bw,bh=props['wmp'],props['hmp']
        point=(x+w/2-bw/2,y if facing==90 else y+h-bh)
        child=self.host._create('ice.miteball',point,owner,dict(move=dict(vel=dict(xvel=0,yvel=0))))
        self.add_actor(child);state=self.states[child]
        state['body']['vy']=0. if facing==90 else -117760.
        self.host.presentation.effect_created(owner,'ice.sambush')
        self.states[owner]['child']=child
        self.host.record('efb_egg_born',id=child,site=owner,species=state['species'],
            evidence='D64px/45HP egg and90/270 outlets; up launch FIT, species selection SIM')

    def _barrier(self,owner):
        row=self.host.actors[owner];x,y,w,h=rect(row)

        if row['props'].get('facing',0)!=90:
            self.host.record('efb_ambush_unsupported_orientation',id=owner,vid=row['vid']);return
        child=self.host._create('ice.dwall',(x+8192,y-49152),owner)
        self.add_actor(child);self.states[owner]['child']=child
        self.host.presentation.effect_created(owner,'ice.lambush')
        self.host.record('efb_barrier_born',id=child,site=owner,evidence='D16 exact native outlet/450HP/armor50; watcher trigger SIM')

    def _hatch(self,oid,state):
        row=self.host.actors.get(oid)
        if row is None or state.get('hatched'):return
        state['hatched']=True;x,y,w,h=rect(row);vid=state['species'];p=self.host.pack.props(vid)
        child=self.host._create(vid,(x+(w-p['wmp'])/2,y+h-p['hmp']),oid,dict(hitpoints=row['hp']))
        if hasattr(self.host,'created_at_end'):self.host.created_at_end(child)
        self.host.enemies.add_actor(child)

        a=self.host.enemies.mites.actors[child];a.update(action='emerge',until=self.now+.396,next_attack=self.now+.396)
        self.host.presentation.attack(child,vid,1,396,token=self.now)
        self.host.record('efb_egg_hatched',id=child,egg=oid,hp=row['hp'],ground_wait_s=self.now-state['landed_at'],
            evidence='D creator/hp inheritance and50ms sample settle;396ms emergence reused local Ice ability USER/SIM')
        self.retire(oid,'INTERNAL')

    def step(self,dt):
        self.now=self.host.room_time;self.pre_physics(dt)
        terrain=Terrain({oid:dict(row['props'],xmp=row['pos'][0],ymp=row['pos'][1]) for oid,row in self.host.actors.items()
            if row['props'].get('terraintype') in (1,2)})
        for oid,state in list(self.states.items()):
            row=self.host.actors.get(oid)
            if row is None:self.states.pop(oid,None);continue
            x,y,w,h=rect(row);vid=state['vid'];target=nearest(self.host,(x+w/2,y+h/2))
            if vid in ('ice.sambush','ice.lambush'):
                if not state['used'] and target and math.dist(center(target),(x+w/2,y+h/2))<=480*256:self._start_warning(oid,state)
                if state.get('hide_at') is not None and self.now>=state['hide_at']:prop(self.host,oid,'draw',False);state.pop('hide_at',None)
            elif vid=='ice.miteball':
                b=state['body'];terrain.step(b,dt,0.,180000.)
                move(self.host,oid,(b['x'],b['y']),dt,4 if b.get('grounded') else 3 if b['vy']<0 else 1)
                if b.get('grounded'):
                    if state['landed_at'] is None:state['landed_at']=self.now
                    elif self.now+1e-9>=state['landed_at']+.05:self._hatch(oid,state)
            elif vid=='ice.ddebris' and target and self.now>=state['next']:
                px,py=center(target)
                if x-48*256<=px<=x+w+48*256 and 0<py-y<700*256:
                    self.host.presentation.attack(oid,vid,2,528,token=self.now)
                    self.pending.append(dict(at=self.now+.528,owner=oid,kind='debris'));state['next']=self.now+5.
        for event in list(self.pending):
            if event['at']>self.now+1e-9:continue
            self.pending.remove(event);owner=event['owner'];row=self.host.actors.get(owner)
            if row is None:continue
            if event['kind']=='egg':self._egg(owner)
            elif event['kind']=='barrier':self._barrier(owner)
            else:self.projectiles.spawn('ice.ddebris',row['pos'],(0.,0.),owner,gravity=115200.,ttl=8.)
        self.projectiles.step(dt)

    def retire(self,oid,reason='INTERNAL',native=False):
        self.states.pop(oid,None);self.tracks.pop(oid,None);self.pending=[p for p in self.pending if p['owner']!=oid]
        super().retire(oid,reason,native)

    def feedback(self,event):
        if self.projectiles.feedback(event):return True
        return super().feedback(event,drop=False)

    def expire(self,oid):
        if self.projectiles.expire(oid):return True
        if oid not in self.owned:return False
        self.retire(oid,'LIFETIME');return True

    def interact(self,oid,player=None):return False

    def status(self):
        return dict(objects=len(self.states),movingPlatforms=len(self.tracks),projectiles=len(self.projectiles.shots),
            inventory=dict(Counter(s['vid'] for s in self.states.values())),
            evidence='D native loop platforms, warning/egg/body/450HP wall fields; outlet FIT; private watcher/proximity/RNG/debris cadence SIM',
            unresolved=['cnest is retained native terrain; no creator-proven independent spawning rule assigned'])
