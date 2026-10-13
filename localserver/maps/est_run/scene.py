'EST content under construction: native open platforms, no capture reads.'
import copy
import json
from pathlib import Path

from ... import pb_layout
from ..jungle_common.scene import normalize, strip_underscores
from ..jungle_mid.scene import JungleMidPack
from ..jungle_boss.scene import JungleBossPack
from tools.make_create_world import unwrap_decompress, wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/est_run'

def reference_ids(value):
    if isinstance(value,list):return [r for v in value for r in reference_ids(v)]
    if not isinstance(value,dict):return []
    result=[int(value['objid'])] if 'objid' in value else []
    for item in value.values():result.extend(reference_ids(item))
    return result

def prune_rolled_anchors(pack,rolled):
    """Existing rolls remove old fauna but can leave their anonymous helpers."""
    if not rolled:return rolled
    layouts=pb_layout.default_layouts()
    world=layouts.decode('CreateWorld',unwrap_decompress(bytes.fromhex(rolled)))
    objects={o['objid_']:strip_underscores(o['props_']) for o in world['worldstate_']['serverworld_']['obj_']}
    removed=[]
    while True:
        bad=[oid for oid,p in objects.items() if any(r not in objects for r in reference_ids(p))]
        if not bad:break
        for oid in bad:
            p=objects[oid]
            if p.get('vid') or p.get('draw',True):
                raise ValueError('named encounter object has dangling native reference: '+str((oid,p)))
            removed.append(oid);objects.pop(oid)
    world['worldstate_']['serverworld_']['obj_']=[dict(objid_=oid,props_=normalize('Properties',p))
                                                for oid,p in sorted(objects.items())]
    pack.run_objects=copy.deepcopy(objects)
    pack.orphaned_anchors=removed
    return wrap_compress(layouts.encode('CreateWorld',world)).hex()

class CompiledMidPack(JungleMidPack):
    """Existing Mid implementation with a self-contained embedded capsule."""
    def __init__(self,root):
        self.root=Path(root)
        data=json.loads((self.root/'pack.json').read_text(encoding='utf8'))
        for key,value in data.items():setattr(self,key,value)
        self.createworld_hex=str(self.root/'baseline.hex')

    def rolled_world(self,seed):return prune_rolled_anchors(self,super().rolled_world(seed))

class CompiledBossPack(JungleBossPack):
    def __init__(self,root):
        self.root=Path(root)
        data=json.loads((self.root/'pack.json').read_text(encoding='utf8'))
        for key,value in data.items():setattr(self,key,value)
        self.createworld_hex=str(self.root/'baseline.hex')

    def rolled_world(self,seed):return prune_rolled_anchors(self,super().rolled_world(seed))

class EstRunPack:
    def __init__(self,root=None,generate=None):
        self.root=Path(root or ROOT)
        self.manifest=json.loads((self.root/'content.json').read_text(encoding='utf8'))
        self.dynamic_templates=json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.dynamic_variants=json.loads((self.root/'dynamic-template-variants.json').read_text(encoding='utf8'))
        self._rooms={role:json.loads((self.root/path).read_text(encoding='utf8'))
                     for role,path in self.manifest['rooms'].items()}
        self.mid_pack=CompiledMidPack(self.root/'encounters/mid')
        self.boss_pack=CompiledBossPack(self.root/'encounters/boss')
        enabled_path=self.root/'modules/enabled.json'
        config=json.loads(enabled_path.read_text(encoding='utf8')) if enabled_path.exists() else {}
        self.generation_enabled=bool(config.get('enabled',False) if generate is None else generate)
        self.generator=None
        if self.generation_enabled:
            from .generator import EstOpenModuleGenerator
            self.generator=EstOpenModuleGenerator(self.root,config.get('pool_file','pool.json'))
            if config.get('content_version') and config['content_version']!=self.generator.data['content_version']:
                raise ValueError('EST published module config and content versions differ')

    @property
    def room_names(self):return tuple(self._rooms)

    def encounter_pack(self,role):
        return self.mid_pack if role=='mid' else self.boss_pack if role=='boss' else None

    def room(self,role,seed=0):
        result=copy.deepcopy(self._rooms[role])
        for field in ('objects','links','creators','gate_pairs'):
            result[field]={int(key):value for key,value in result.get(field,{}).items()}
        if role in ('mid','boss'):
            pack=self.encounter_pack(role)
            rolled=pack.rolled_world(int(seed)&0x7fffffff)
            if not rolled:raise ValueError('existing encounter roll failed: '+role)
            world=pb_layout.default_layouts().decode('CreateWorld',unwrap_decompress(bytes.fromhex(rolled)))
            result['objects']={o['objid_']:strip_underscores(o['props_'])
                               for o in world['worldstate_']['serverworld_']['obj_']}
            marker=max(result['objects'])+1
            width,height=result['metadata']['spawn_footprint_mp']
            result['objects'][marker]=dict(xmp=result['spawn'][0]+width//2,ymp=result['spawn'][1]+height,
                                           wmp=0,hmp=0,draw=False,spawn=True)
            result['metadata'].update(spawn_marker_id=marker,max_objid=marker,dynamic_id_start=marker+1000,
                                      encounter_roll_seed=int(seed)&0x7fffffff,
                                      orphaned_rolled_anchor_ids=pack.orphaned_anchors)
            result['world']=world
            self._set_objects(result['world'],result['objects'],result)
        elif self.generator:
            result=self.generator.generate(result,seed)
            result['metadata']['generation'].update(native_verified=False,manual_verification_pending=True)
            self._set_objects(result['world'],result['objects'],result)
        return result

    @staticmethod
    def _set_objects(world,objects,room):

        rows=[]
        for oid,p in sorted(objects.items()):
            row=dict(objid_=int(oid),props_=normalize('Properties',p))
            rows.append(row)
        world['worldstate_'].pop('clientworld_',None)
        world['worldstate_']['serverworld_']['obj_']=rows
        world['worldstate_']['serverworld_']['ownerid_']=1
        world['worldid_']=2
        world['spawnobj_']=dict(worldid_=1,objid_=room['metadata']['spawn_marker_id'])

    def world_blob(self,role,seed=0,objects=None,spawn=None):
        room=self.room(role,seed)
        chosen=copy.deepcopy(room['objects'] if objects is None else objects)
        chosen={int(k):v for k,v in chosen.items()}
        marker=room['metadata']['spawn_marker_id']
        if marker in chosen and not chosen[marker].get('spawn'):
            marker=max(chosen)+1
        room['metadata']['spawn_marker_id']=marker
        point=list(room['spawn'] if spawn is None else spawn)
        width,height=room['metadata']['spawn_footprint_mp']
        chosen[marker]=dict(xmp=round(point[0]+width/2),ymp=round(point[1]+height),wmp=0,hmp=0,draw=False,spawn=True)
        self._set_objects(room['world'],chosen,room)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',room['world']))

    def props(self,vid):return copy.deepcopy(self.dynamic_templates[vid])

    def props_variants(self,vid):return copy.deepcopy(self.dynamic_variants[vid])
