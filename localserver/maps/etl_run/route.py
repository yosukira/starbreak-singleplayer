"""USER-constrained ETL routes over the current confirmed native room sources.

One-based2/4 use the measured fixed-pattern pool. The observed five-room
prefix plus Mid is retained; Mid has2..4 exploration rooms before Boss.
Only template/length choice is SIM. This is not modular terrain generation.
"""
import copy
import hashlib
import json
import random
from pathlib import Path
from ..run_common.run import LevelRun,room_seed

def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()

class EtlRoutePlanner:
    def __init__(self,root):
        self.root=Path(root)
        self.pool=json.loads((self.root/'route-pool.json').read_text(encoding='utf8'))
        self.policy=json.loads((self.root/'route-policy.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.pool.items() if k!='content_version'})!=self.pool['content_version']:
            raise ValueError('ETL typed route pool fingerprint mismatch')
        if self.policy['pool_version']!=self.pool['content_version']:raise ValueError('ETL route policy/source version mismatch')
        if self.policy['post_mid_counts']!=[2,3,4]:raise ValueError('USER ETL Mid must precede2..4 exploration rooms')
        if self.policy['fixed_pattern_positions']!=[2,4]:raise ValueError('USER ETL fixed patterns must occupy rooms2/4')
        if self.policy['fixed_pattern_sources']!=['bulkhead_1','bulkhead_2']:raise ValueError('ETL fixed source pool is not the measured pair')

    def plan(self,seed):
        seed=int(seed);rng=random.Random('etl-typed-route-v1:'+str(seed));post=rng.choice(self.policy['post_mid_counts'])
        specs=[('entry','entrance','entry'),
            ('room_02_fixed_pattern','fixed-pattern',rng.choice(self.policy['fixed_pattern_sources'])),
            ('room_03_exploration','exploration','sector_2'),
            ('room_04_fixed_pattern','fixed-pattern',rng.choice(self.policy['fixed_pattern_sources'])),
            ('room_05_exploration','exploration','sector_3'),('mid','mid','mid')]
        for i in range(post):
            number=7+i;last=i==post-1
            kind='approach' if last else 'exploration'
            specs.append(('room_%02d_%s'%(number,kind),kind,'final_approach' if last else rng.choice(self.policy['post_mid_sources'])))
        specs.append(('boss','boss','boss'));nodes=[]
        for number,(role,kind,source) in enumerate(specs,1):
            encounter='bulkhead_switch' if kind=='fixed-pattern' else kind if kind in ('mid','boss') else None
            nodes.append(dict(role=role,number=number,native_floor=number-1,kind=kind,source_role=source,
                seed=room_seed(seed,role),encounter_kind=encounter,encounter_key='encounter:'+role if encounter else None,
                evidence='USER2/4 fixed patterns and2..4 maps afterMid; D current compiled native source; SIM exact length/template choices'))
        for i,n in enumerate(nodes):n.update(previous=nodes[i-1]['role'] if i else None,following=nodes[i+1]['role'] if i+1<len(nodes) else None)
        plan=dict(schema='etl-typed-route-v1',seed=seed,post_mid_count=post,route=[n['role'] for n in nodes],
            nodes={n['role']:n for n in nodes},pool_version=self.pool['content_version'],
            evidence=dict(USER='One-based rooms2/4 fixed-pattern pool; Mid then2..4 maps beforeBoss',
                D='Current recorded prefix1..5 andMid6 retained; all room geometry/Props from this confirmed run',
                SIM='Uniform2/3/4 post-Mid count; equal fixed-pattern pool choice; seeded independent sector4/5 reuse; final native approach retained'),
            limits='Existing native whole-room sources are recombined. This typed route does not claim recovered original weights or complete modular generation.')
        self.validate(plan)
        plan['content_version']=digest(dict(pool=self.pool['content_version'],policy=self.policy,
            adapter=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()))
        return plan

    @staticmethod
    def validate(plan):
        rows=list(plan['nodes'].values());mid=next(i for i,n in enumerate(rows) if n['kind']=='mid')
        if mid!=5 or len(rows)-mid-2 not in (2,3,4):raise ValueError('ETL route Mid/prefix/post-Mid invariant')
        if {n['number'] for n in rows if n['kind']=='fixed-pattern'}!={2,4}:raise ValueError('ETL fixed slot invariant')
        if rows[0]['kind']!='entrance' or rows[-1]['kind']!='boss' or rows[-2]['kind']!='approach':raise ValueError('ETL route cap invariant')
        if len(set(plan['route']))!=len(rows):raise ValueError('ETL node roles must be unique')
        keys=[n['encounter_key'] for n in rows if n['encounter_key']]
        if len(set(keys))!=len(keys):raise ValueError('ETL completion keys must be unique')
        return True

    def materialize(self,plan,role,pack):
        node=plan['nodes'][role];source=node['source_role'];baseline=pack._rooms[source][0]
        if digest(baseline)!=self.pool['sources'][source]['recipe_sha']:raise ValueError('ETL typed route source changed: '+source)
        room=pack.room(source,node['seed']);original=pack.manifest['route'];index=original.index(source)
        aliases={original[index-1] if index else None:node['previous'],
            original[index+1] if index+1<len(original) else None:node['following']}
        for link in room['links'].values():
            if link.get('external'):
                if link.get('requires') and node['encounter_key']:link['requires']=node['encounter_key']
                continue
            old=link['destination'];destination=aliases.get(old)
            if destination is None:raise ValueError('ETL native door does not fit route node '+role+': '+str(old))
            link.update(destination=destination,entry=role)
            if link.get('requires')=='bulkhead_switch':link['requires']=node['encounter_key']
            elif link.get('requires')=='mid_cleared':link['requires']='encounter:mid'
        for field in ('entries','anchors'):
            if field in room:room[field]={('default' if k=='default' else aliases.get(k,k)):v for k,v in room[field].items()}
        metadata=room['metadata']
        for field in ('entry_details','entry_support'):
            if field in metadata:metadata[field]={('default' if k=='default' else aliases.get(k,k)):v for k,v in metadata[field].items()}
        source_props=copy.deepcopy(room['world']['worldprops_'])
        room.update(role=role,source_role=source,variant=self.pool['sources'][source]['recipe_sha'][:16]+'-'+role)
        metadata.update(source_role=source,route_node=copy.deepcopy(node),content_kind=node['kind'],
            encounter_kind=node['encounter_kind'],encounter_key=node['encounter_key'],route_version=plan['content_version'],
            source_worldprops=source_props,source_native_seed=source_props.get('seed_'),source_native_floor=source_props.get('floor_'),
            local_seed=node['seed'],native_floor=node['native_floor'],user_room_number=node['number'],
            source_recipe_sha=self.pool['sources'][source]['recipe_sha'])
        generation=metadata.setdefault('generation',dict(mode='typed-native-room-route',geometry_changed=False,
            evidence='SIM room selection; all original native body/terrain/creator/MOVE fields retained'))
        generation['typed_route_version']=plan['content_version']
        room['world']['worldprops_'].update(seed_=node['seed'],floor_=node['native_floor'])
        return room

class EtlPlannedPack:
    def __init__(self,base,seed):
        self.base=base;self.root=base.root;self.planner=EtlRoutePlanner(base.root);self.plan=self.planner.plan(seed)
        if getattr(base,'generator',None):
            self.plan['content_version']=digest(dict(route=self.plan['content_version'],modules=base.generator.data['content_version']))
        self.dynamic_templates=base.dynamic_templates;self.dynamic_template_variants=base.dynamic_template_variants
        self.manifest=copy.deepcopy(base.manifest);self.manifest.update(route=self.plan['route'],typed_route=self.plan,
            role_kinds={r:n['kind'] for r,n in self.plan['nodes'].items()},content_version=self.plan['content_version'])
        self._rooms={}

    @property
    def room_names(self):return tuple(self.plan['route'])
    def props(self,vid):return self.base.props(vid)
    def props_variants(self,vid):return self.base.props_variants(vid)
    def room(self,role,seed=0):
        if seed and int(seed)!=self.plan['nodes'][role]['seed']:raise ValueError('ETL planned room seed mismatch')
        if role not in self._rooms:self._rooms[role]=self.planner.materialize(self.plan,role,self.base)
        return copy.deepcopy(self._rooms[role])
    def world_blob(self,role,seed=0,objects=None,spawn=None):
        from .scene import EtlRunPack
        return EtlRunPack.world_blob(self,role,seed,objects,spawn)

class EtlLevelRun(LevelRun):
    def __init__(self,pack,seed=None,*,typed=None):
        super().__init__(pack,seed)
        config_path=pack.root/'route-policy.json'
        config=json.loads(config_path.read_text(encoding='utf8')) if config_path.exists() else {}
        self.typed=isinstance(pack,EtlPlannedPack) or (bool(config.get('enabled',False)) if typed is None else bool(typed))
        if self.typed and not isinstance(pack,EtlPlannedPack):self.pack=pack.for_run(self.seed)
