"""Measured Ice encounters bound before prepare to the actual EFB room.

The Mid capability uses canonical39 internally. This adapter translates only
that body identifier; native ledge39 remains present in the room. No source
geometry is replaced with the older standalone encounter's generated arena.
"""
import copy
from ...live.room import BossRoom
from ...supply import SupplyDrops
from ..ice_mid.controller import IceMidController
from ..ice_mid.session import IceMidLiveSession
from ..ice_boss.session import IceBossLiveSession

def _one(objects,vid):
    ids=[i for i,p in objects.items() if p.get('vid')==vid]
    if len(ids)!=1:raise ValueError('EFB encounter requires one '+vid+': '+str(ids))
    return ids[0]

def bound_pack(level_pack,instance):
    from ..ice_mid.scene import IceMidPack
    from ..ice_boss.scene import IceBossPack
    role=instance.role;pack=IceMidPack(seed=0) if role=='mid' else IceBossPack()
    objects=copy.deepcopy(instance.recipe['objects'])
    pack.templates={str(i):p for i,p in objects.items()}
    pack.dynamic_templates.update(copy.deepcopy(level_pack.dynamic_templates))
    pack.solids=[pack.rect(p) for p in objects.values() if p.get('terraintype')==1]
    pack.ledges=[pack.rect(p) for p in objects.values() if p.get('terraintype')==2]
    pack.surfaces=pack.solids+pack.ledges
    structural=[pack.rect(p) for p in objects.values() if p.get('terraintype')==1 and p.get('boundsworld') is not False]
    if not structural:raise ValueError('EFB encounter has no native structural walls')
    bounds=[min(p[0] for p in structural),min(p[1] for p in structural),
        max(p[0]+p[2] for p in structural),max(p[1]+p[3] for p in structural)]

    layers={}
    for x,y,w,h in structural:
        if w>h:layers[y]=layers.get(y,0)+w
    floor=max(layers,key=layers.get)
    floors=[p for p in structural if p[1]==floor and p[2]>p[3]]
    pack.scene.update(left=min(p[0] for p in floors),right=max(p[0]+p[2] for p in floors),floor=floor,bounds=bounds,
        spawn_id=instance.recipe['metadata']['spawn_marker_id'],source='efb_run:'+role)
    if role=='mid':
        boss=_one(objects,'ice.crossbeast')
        exits=[i for i,v in instance.recipe['links'].items() if v.get('destination')=='sector_3']
        if len(exits)!=1:raise ValueError('EFB Mid forward exit ambiguous')
        pack.scene.update(boss_id=boss,exit_id=exits[0],rows=sorted({p[1] for p in pack.ledges}))
        pack.runs={}
        for x,y,w,h in pack.surfaces:
            if y in pack.scene['rows'] or y==floor:pack.runs.setdefault(y,[]).append((x,x+w))
        for y,spans in pack.runs.items():
            merged=[]
            for a,b in sorted(spans):
                if merged and a<=merged[-1][1]:merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
                else:merged.append((a,b))
            pack.runs[y]=merged
    else:
        pack.scene.update(small_id=_one(objects,'ice.kmite'),large_id=_one(objects,'ice.dinoambush'),
            cover_id=_one(objects,'ice.dinocover'),gate_id=_one(objects,'ice.fwall'),
            portal_id=next(i for i,v in instance.recipe['links'].items() if v.get('external')))
        pack.spikes={i:pack.rect(p) for i,p in objects.items() if p.get('vid')=='ice.ddebris'}
    pack._efb_next_id=max(objects)+1000
    return pack

class BoundMidController(IceMidController):
    def prepare(self):
        self.actual_id=self.pack.scene['boss_id']
        original=self.pack.templates.get('39')
        self.pack.templates['39']=self.pack.templates[str(self.actual_id)]
        try:super().prepare()
        finally:
            if original is None:self.pack.templates.pop('39',None)
            else:self.pack.templates['39']=original
        self.boss['id']=self.actual_id;self._next_id=self.pack._efb_next_id

    def emit(self,kind,**fields):
        if fields.get('id')==39:fields['id']=self.actual_id
        if 'actor' in fields and fields['actor'].get('owner')==39:fields['actor']['owner']=self.actual_id
        super().emit(kind,**fields)

    def add_damage(self,value,objid=None,player_id=None):
        return super().add_damage(value,39 if objid==self.actual_id else None,player_id)

class BoundMidSession(IceMidLiveSession):
    def prepare(self):
        self.controller=BoundMidController(self.pack,self.seed)
        self.supplies=SupplyDrops(self,'efb_run');self.batch.clear()
        self.phase='waiting';self.room_time=0.;self.bootstrapped=False
        self.players={};self.player=None;self.events=[];self.retired=set();self.created=set()
        self.native_damage=self.native_destroy=self.frames=0;self.max_tick_ms=0.
        self._wire_ms=0.;self._sent_ms=0;self._frame_start=0.;self._event_time=0.

    def _action(self,event):
        if event['kind']=='victory':
            self._event_time=event['at'];self.phase='done'
            self.batch.props(self.pack.scene['exit_id'],dict(VID='ice.nextDoor',INTERACT=255),offset=self._offset())
            self.record('victory');return
        if event['kind']=='death_begin':
            self._event_time=event['at'];oid=self.pack.scene['boss_id']
            if oid not in self.retired:
                self.batch.world_sound(self.pack.entities['sounds']['death'],offset=self._offset())
                self.batch.destroy(oid,self.worldid,'INTERNAL',offset=self._offset());self.retired.add(oid)
            self.record('death_begin',id=oid);return
        return IceBossLiveSession._action(self,event)

def create(wrapper,options):
    instance=wrapper.instance;pack=bound_pack(wrapper.run.pack,instance)
    rows=instance.snapshot.document(2)['worldstate_']['serverworld_']['obj_']
    room=BossRoom(copy.deepcopy(rows),{},source='efb_run:'+instance.role)
    cls=BoundMidSession if instance.role=='mid' else IceBossLiveSession
    options.map='ice_mid' if instance.role=='mid' else 'ice_boss'
    runtime=cls(wrapper.proxy,wrapper.channel,options,room,log=wrapper.log,bridge=wrapper.bridge,pack=pack,seed=instance.seed)
    runtime.controller._next_id=pack._efb_next_id
    runtime.supplies=SupplyDrops(runtime,'efb_run')
    return runtime

def finished(runtime,role):
    return runtime.phase=='done' and runtime.controller.state=='done'
