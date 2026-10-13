'ETL compiled room API; deliberately not a registered playable map.'
import copy
import json
from pathlib import Path

from ... import pb_layout
from ..hulk_mid.scene import normalize
from tools.make_create_world import wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/etl_run'

class EtlRunPack:
    def __init__(self,root=None,generate=None):
        self.root=Path(root or ROOT)
        self.manifest=json.loads((self.root/'scene.json').read_text(encoding='utf8'))
        self.dynamic_templates=json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.dynamic_template_variants=json.loads((self.root/'dynamic-template-variants.json').read_text(encoding='utf8'))
        self._rooms={role:[json.loads((self.root/path).read_text(encoding='utf8')) for path in paths]
                     for role,paths in self.manifest['rooms'].items()}
        enabled_path=self.root/'modules/enabled.json'
        module_config=json.loads(enabled_path.read_text(encoding='utf8')) if enabled_path.exists() else {}
        self.generate_enabled=bool(module_config.get('enabled',False)) if generate is None else bool(generate)
        self.generator=None
        if self.generate_enabled:
            from .generator import EtlModuleGenerator
            self.generator=EtlModuleGenerator(self.root)
            if module_config.get('content_version')!=self.generator.data['content_version']:
                raise ValueError('ETL module enabled/pool version mismatch')

    @property
    def room_names(self):
        return tuple(self._rooms)

    def room(self,role,seed=0):
        variants=self._rooms[role]
        result=copy.deepcopy(variants[int(seed)%len(variants)])
        for field in ('objects','links','switches','creators'):
            result[field]={int(k):v for k,v in result.get(field,{}).items()}
        return self.generator.generate(result,int(seed)) if self.generator else result

    def props(self,vid):
        try:
            return copy.deepcopy(self.dynamic_templates[vid])
        except KeyError as error:
            raise KeyError('ETL compiled dynamic template missing: '+str(vid)) from error

    def props_variants(self,vid):
        return copy.deepcopy(self.dynamic_template_variants.get(vid,{}).get('variants',[]))

    def for_run(self,seed):
        from .route import EtlPlannedPack
        return EtlPlannedPack(self,seed)

    def world_blob(self,role,seed=0,objects=None,spawn=None):
        room=self.room(role,seed)
        document=room['world']
        chosen=copy.deepcopy(room['objects'] if objects is None else {int(k):v for k,v in objects.items()})
        marker=room['metadata']['spawn_marker_id']
        if marker in chosen and chosen[marker]!=room['objects'][marker]:
            marker=max(chosen,default=0)+1
        point=room['spawn'] if spawn is None else spawn
        w,h=room['metadata']['spawn_footprint_mp']
        chosen[marker]=dict(xmp=round(point[0]+w/2),ymp=round(point[1]+h),draw=False,spawn=True)
        serverworld=document['worldstate_']['serverworld_']
        serverworld['obj_']=[]
        for oid,p in sorted(chosen.items()):

            entry=dict(objid_=int(oid),props_=normalize('Properties',p))
            serverworld['obj_'].append(entry)
        serverworld['ownerid_']=1
        document['worldstate_'].pop('clientworld_',None)
        document['worldid_']=2
        document['spawnobj_']=dict(worldid_=1,objid_=marker)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',document))
