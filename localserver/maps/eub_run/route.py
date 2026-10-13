"""USER-constrained EUB routes over compiled typed native room sources.

One-based stage numbering is explicit. Exact route probabilities/weights are
local SIM choices, never claims about the original server generator.
"""
import copy
import hashlib
import json
import random
from pathlib import Path

from ..run_common.run import room_seed
from .generator import digest,geometry_sha

DEFAULT_POLICY=dict(lengths=[8,10,12],extra_event_probability=.1,
    wave_position_weights={3:1,5:8,7:1,9:1},mid_position_weights={3:1,5:1,7:8,9:1},
    extra_wave_probability=.5)

class EubRoutePlanner:
    def __init__(self,root,policy=None,min_each=1):
        self.root=Path(root);self.pool=json.loads((self.root/'route-pool.json').read_text(encoding='utf8'))
        semantic={k:v for k,v in self.pool.items() if k!='content_version'}
        if digest(semantic)!=self.pool['content_version']:raise ValueError('EUB typed room pool fingerprint mismatch')
        base=json.loads((self.root/'scene.json').read_text(encoding='utf8'))
        if base['content_version']!=self.pool['base_content_version']:raise ValueError('EUB typed room pool/base version mismatch')
        self.policy=copy.deepcopy(DEFAULT_POLICY if policy is None else policy)
        if min_each!=1:raise ValueError('USER confirmed at least one Wave and one Mid: min_each must be1')
        self.min_each=min_each
        if set(self.policy['lengths'])!={8,10,12}:raise ValueError('USER EUB preboss lengths must be8/10/12')
        for kind,peak in (('wave',5),('mid',7)):
            weights=self.policy[kind+'_position_weights']
            if set(weights)!={3,5,7,9} or not all(v>0 for v in weights.values()):raise ValueError('EUB event slot policy invalid')
            if weights[peak]<=max(v for k,v in weights.items() if k!=peak):raise ValueError('USER EUB event preference lost')
        if not 0<=self.policy['extra_event_probability']<.5:raise ValueError('Third EUB event must be a rare SIM possibility')

    @staticmethod
    def _choose(rng,positions,weights):return rng.choices(positions,weights=[weights[p] for p in positions],k=1)[0]

    def plan(self,seed):
        seed=int(seed);rng=random.Random('eub-typed-route-v1:'+str(seed));length=rng.choice(self.policy['lengths'])
        slots=[p for p in (3,5,7,9) if p<=length];events={};count=3 if rng.random()<self.policy['extra_event_probability'] else 2
        wave=self._choose(rng,slots,self.policy['wave_position_weights']);events[wave]='wave'
        mid=self._choose(rng,[p for p in slots if p!=wave],self.policy['mid_position_weights']);events[mid]='mid'
        if count==3:
            kind='wave' if rng.random()<self.policy['extra_wave_probability'] else 'mid'
            remaining=[p for p in slots if p not in events]
            if remaining:events[self._choose(rng,remaining,self.policy[kind+'_position_weights'])]=kind
        nodes=[]
        for number in range(1,length+1):
            kind=('entrance' if number==1 else 'approach' if number==length else events[number] if number in events
                  else 'fixed-pattern' if number>=3 and number%2 else 'exploration')
            role='entry' if number==1 else 'room_%02d_%s'%(number,kind.replace('-','_'))
            choices=self.pool['types'][kind]['sources'];source=rng.choice(choices)
            nodes.append(dict(role=role,number=number,native_floor=number-1,kind=kind,source_role=source,
                seed=room_seed(seed,role),encounter_kind='wave_chamber' if kind=='wave' else kind if kind=='mid' else None,
                encounter_key='encounter:'+role if kind in ('wave','mid') else None,
                evidence='USER type/slot/count constraints; D compiled source room; SIM exact probability/template choice'))
        nodes.append(dict(role='boss',number=length+1,native_floor=length,kind='boss',source_role='boss',seed=room_seed(seed,'boss'),encounter_kind='boss',encounter_key='encounter:boss',evidence='D existing native Boss; SIM per-run instance'))
        for n,node in enumerate(nodes):node.update(previous=nodes[n-1]['role'] if n else None,following=nodes[n+1]['role'] if n+1<len(nodes) else None)
        plan=dict(schema='eub-typed-route-v1',seed=seed,preboss_count=length,event_count=len(events),
            route=[n['role'] for n in nodes],nodes={n['role']:n for n in nodes},pool_version=self.pool['content_version'],
            policy=dict(self.policy,min_each=self.min_each),policy_evidence=dict(USER='One-based3/5/7/9 event slots;5Wave preference/7Mid preference;usually2/rare3;at least one of each confirmed;preboss8/10/12;other odd>=3 fixed patterns',
            SIM='Uniform length selection, weighted slot/type choice,10percent third event, equal third-event kind; min_each=1 is USER-confirmed rather than an inferred restriction'),
            limits='Typed route recombines existing native source rooms. Only exploration sources with verified corridor recipes get module assembly; fixed pattern source coverage currently two laser-room variants, not recovered full original pattern set.')
        self.validate(plan);plan['content_version']=digest(dict(pool=self.pool['content_version'],policy=plan['policy'],adapter=(Path(__file__).read_text(encoding='utf8'))))
        return plan

    def validate(self,plan):
        nodes=list(plan['nodes'].values());pre=[n for n in nodes if n['kind']!='boss'];events=[n for n in pre if n['kind'] in ('wave','mid')]
        if len(pre) not in (8,10,12) or len(events) not in (2,3):raise ValueError('EUB USER route count invariant')
        if {n['kind'] for n in events}!={'wave','mid'}:raise ValueError('Confirmed min_each policy requires both event kinds')
        if any(n['number'] not in (3,5,7,9) for n in events):raise ValueError('EUB encounter placed outside USER slots')
        if any(n['kind'] not in ('wave','mid','fixed-pattern') for n in pre if n['number']>=3 and n['number']%2):raise ValueError('Odd EUB non-event slot is not a fixed native pattern')
        if len(set(plan['route']))!=len(plan['route']):raise ValueError('EUB per-instance role reused')
        keys=[n['encounter_key'] for n in nodes if n['encounter_key']]
        if len(keys)!=len(set(keys)):raise ValueError('EUB encounter completion key reused')
        return True

    def materialize(self,plan,role,pack):
        node=plan['nodes'][role];source=node['source_role']
        if digest(pack._rooms[source][0])!=self.pool['sources'][source]['full_recipe_sha']:raise ValueError('EUB typed source recipe fingerprint mismatch')
        room=pack.room(source,node['seed'])
        original_route=pack.manifest['route'];index=original_route.index(source)
        old_previous=original_route[index-1] if index else None;old_following=original_route[index+1] if index+1<len(original_route) else None
        aliases={old_previous:node['previous'],old_following:node['following']}
        for link in room['links'].values():
            if link.get('external'):
                if node['encounter_key']:link['requires']=node['encounter_key']
                continue
            destination=aliases.get(link['destination'])
            if destination is None:raise ValueError('Native room navigation cannot fit typed route node '+role)
            link.update(destination=destination,entry=role,requires=node['encounter_key'])
        for field in ('entries','anchors'):
            room[field]={('default' if key=='default' else aliases.get(key,key)):value for key,value in room[field].items()}
        room['metadata']['entry_support']={('default' if key=='default' else aliases.get(key,key)):value for key,value in room['metadata']['entry_support'].items()}
        source_variant=room['variant'];room.update(role=role,source_role=source,variant=source_variant+'-instance-'+role)
        room['metadata'].update(route_node=copy.deepcopy(node),content_kind=node['kind'],encounter_kind=node['encounter_kind'],encounter_key=node['encounter_key'],
            route_version=plan['content_version'],source_geometry_sha=self.pool['sources'][source]['permanent_geometry_sha'],source_variant=source_variant,
            source_native_floor=room['metadata']['floor'],floor=node['native_floor'],native_floor=node['native_floor'],user_room_number=node['number'],
            source_reuse='D captured source '+source+'; SIM independent local instance, no event replay')
        room['world']['worldprops_'].update(floor_=node['native_floor'],seed_=node['seed'])
        generation=room['metadata'].setdefault('generation',dict(mode='capture-native-route-instance',limits='Typed route source reuse; no geometric module assembly for this instance'))
        components=generation.setdefault('component_versions',dict(base=self.pool['base_content_version']))
        components['route']=plan['content_version'];generation.update(content_version=digest(components),seed=node['seed'],geometry_sha=geometry_sha(room['objects']))
        return room

class EubPlannedPack:
    """Independent instance pack for run_common: call base.for_run(runSeed).

    The common encounter adapter must select metadata.encounter_kind and use
    metadata.encounter_key. Canonical wave unlocks must be translated by the
    actor host, never broadcast across every reused source role.
    """
    def __init__(self,base,seed,min_each=1):
        self.base=base;self.planner=EubRoutePlanner(base.root,min_each=min_each);self.plan=self.planner.plan(seed)
        self.root=base.root;self.dynamic_templates=base.dynamic_templates;self.dynamic_variants=base.dynamic_variants;self.wave_rules=base.wave_rules
        self.manifest=copy.deepcopy(base.manifest);self.manifest.update(route=self.plan['route'],start_room='entry',
            role_kinds={k:n['kind'] for k,n in self.plan['nodes'].items()},typed_route=self.plan,
            base_content_version=base.manifest['content_version'],content_version=digest(dict(base=base.manifest['content_version'],route=self.plan['content_version'],
                modules=base._generator.data['content_version'] if base._generator else 'capture-native',
                assembly_adapter={name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('generator.py','scene.py')})))
        self._rooms={}

    @property
    def room_names(self):return tuple(self.plan['route'])

    def props(self,vid):return self.base.props(vid)
    def props_variants(self,vid):return self.base.props_variants(vid)

    def room(self,role,seed=0):
        if seed and int(seed)!=self.plan['nodes'][role]['seed']:raise ValueError('EUB room seed differs from typed planned instance')
        if role not in self._rooms:self._rooms[role]=self.planner.materialize(self.plan,role,self.base)
        return copy.deepcopy(self._rooms[role])

    def world_blob(self,role,seed=0,objects=None,spawn=None):
        from ... import pb_layout
        from tools.make_create_world import wrap_compress
        from .scene import EubRunPack
        room=self.room(role);chosen=copy.deepcopy(room['objects'] if objects is None else {int(k):v for k,v in objects.items()})
        marker=room['metadata']['spawn_marker_id']
        if marker in chosen and not chosen[marker].get('spawn'):marker=max(chosen)+1;room['metadata']['spawn_marker_id']=marker
        point=room['spawn'] if spawn is None else spawn;w,h=room['metadata']['spawn_footprint_mp']
        chosen[marker]=dict(xmp=round(point[0]+w/2),ymp=round(point[1]+h),wmp=0,hmp=0,draw=False,spawn=True)
        EubRunPack._set_objects(room['world'],chosen,room)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',room['world']))

    def encounter_room(self,role,seed=0):
        from .scene import EubRunPack
        room=self.room(role)
        if room['metadata']['encounter_kind'] not in ('mid','boss'):raise ValueError('Not an EUB native encounter')
        ids=room['metadata'].get('encounter_managed_initial_ids',[])
        for oid in ids:room['objects'].pop(oid,None);room['creators'].pop(oid,None)
        room['metadata'].update(controller_initialization_removed_ids=list(ids),object_count=len(room['objects']))
        EubRunPack._set_objects(room['world'],room['objects'],room)
        return room

    def encounter_pack(self,role,seed=0):
        room=self.encounter_room(role);kind=room['metadata']['encounter_kind'];pack=self.base.encounter_pack(kind)
        pack.templates=copy.deepcopy(room['objects']);pack.scene.update(bounds_mp=room['metadata']['bounds_mp'],
            doors=[i for i,p in room['objects'].items() if p.get('vid')=='dumb.closedDoor'],elevators=room['mechanics']['elevators'])
        if kind=='mid':pack.scene['telepads']=room['mechanics']['telepad_pairs']
        return pack

    def npc_pack(self,role,seed=0):
        from .scene import EubNpcPack
        return EubNpcPack(self,self.room(role))

from ..run_common.run import LevelRun

class EubLevelRun(LevelRun):
    """Versioned typed routes, configurable separately from module generation."""
    def __init__(self,pack,seed=None,*,typed=None,min_each=1):
        super().__init__(pack,seed)
        config_path=pack.root/'route-enabled.json'
        config=json.loads(config_path.read_text(encoding='utf8')) if config_path.exists() else {}
        self.typed=isinstance(pack,EubPlannedPack) or (bool(config.get('enabled',False)) if typed is None else bool(typed))
        if self.typed and not isinstance(pack,EubPlannedPack):
            if config and config.get('min_each',1)!=1:raise ValueError('USER EUB route requires min_each=1')
            planned=pack.for_run(self.seed,min_each=min_each)
            if config and config.get('content_version')!=planned.planner.pool['content_version']:raise ValueError('EUB typed route pool/enabled version mismatch')
            self.pack=planned
