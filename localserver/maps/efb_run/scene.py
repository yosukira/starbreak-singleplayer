'Compiled EFB room API; no capture reads and no encounter policy here.'
import copy
import json
from pathlib import Path
from ... import pb_layout
from ..ice_boss.scene import normalize
from tools.make_create_world import wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/efb_run'

class EfbRunPack:
    def __init__(self,root=None,generate=None):
        self.root=Path(root or ROOT)
        self.manifest=json.loads((self.root/'scene.json').read_text(encoding='utf8'))
        self.dynamic_templates=json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.dynamic_template_variants=json.loads((self.root/'dynamic-template-variants.json').read_text(encoding='utf8'))
        self._rooms={role:[json.loads((self.root/p).read_text(encoding='utf8')) for p in paths] for role,paths in self.manifest['rooms'].items()}
        enabled_path=self.root/'modules/enabled.json'
        config=json.loads(enabled_path.read_text(encoding='utf8')) if enabled_path.exists() else {}
        flat_enabled=bool(config.get('enabled',False)) if generate is None else bool(generate)
        xy_path=self.root/'modules/xy-enabled.json'
        self.xy_config=json.loads(xy_path.read_text(encoding='utf8')) if xy_path.exists() else {}
        self.xy_generate_enabled=bool(self.xy_config.get('enabled',False)) and (generate is None or bool(generate))
        tower_path=self.root/'modules/tower-enabled.json'
        self.tower_config=json.loads(tower_path.read_text(encoding='utf8')) if tower_path.exists() else {}
        self.tower_generate_enabled=bool(self.tower_config.get('enabled',False)) and (generate is None or bool(generate))
        self.generate_enabled=flat_enabled or self.xy_generate_enabled or self.tower_generate_enabled
        self.generator=None;self.xy_generator=None;self.tower_generator=None
        if flat_enabled:
            from .generator import EfbModuleGenerator
            self.generator=EfbModuleGenerator(self.root)
            if config.get('content_version')!=self.generator.data['content_version']:
                raise ValueError('Ice module enabled/pool version differs')
        if self.xy_generate_enabled:
            from .sloped_generator import EfbSlopedGenerator,ROLES
            self.xy_roles=tuple(self.xy_config.get('roles',sorted(ROLES)))
            if not self.xy_roles or not set(self.xy_roles).issubset(ROLES):
                raise ValueError('Ice XY configuration includes a protected/unproved role')
            self.xy_generator=EfbSlopedGenerator(self.root)
            if self.xy_config.get('content_version')!=self.xy_generator.data['content_version']:
                raise ValueError('Ice XY enabled/pool version differs')
        if self.tower_generate_enabled:
            from .tower_generator import EfbTowerGenerator
            if self.tower_config.get('roles')!=['sector_3']:
                raise ValueError('Ice tower configuration includes a protected/unproved role')
            self.tower_generator=EfbTowerGenerator(self.root)
            if self.tower_config.get('content_version')!=self.tower_generator.data['content_version']:
                raise ValueError('Ice tower enabled/pool version differs')

    @property
    def room_names(self):return tuple(self.manifest['route'])

    def room(self,role,seed=0):
        rows=self._rooms[role];r=copy.deepcopy(rows[int(seed)%len(rows)])
        for field in ('objects','creators','links','switches'):r[field]={int(k):v for k,v in r[field].items()}

        if self.tower_generator and role=='sector_3':
            r=self.tower_generator.generate(r,seed)
            from .generator import digest
            g=r['metadata']['generation']
            g['component_versions']['activation_config']=digest(self.tower_config)
            g['content_version']=digest(g['component_versions'])
            g.update(activation='modules/tower-enabled.json',normal_full_pool_route_verified=False,
                playtest_status='Two whole native tower layers reordered locally; normal tower/run traversal awaits user playtest')
            return r
        if self.xy_generator and role in self.xy_roles:
            r=self.xy_generator.generate(r,seed)
            from .generator import digest
            g=r['metadata']['generation']
            g['component_versions']['activation_config']=digest(self.xy_config)
            g['content_version']=digest(g['component_versions'])
            g.update(activation='modules/xy-enabled.json',normal_full_pool_route_verified=False,
                playtest_status='User playtest pending; static/native loading and one accepted node do not prove the whole pool or run')
            return r
        return self.generator.generate(r,seed) if self.generator else r

    @staticmethod
    def _set_objects(room):

        room['world']['worldstate_']['serverworld_']['obj_']=[dict(objid_=int(oid),props_=normalize('Properties',p))
            for oid,p in sorted(room['objects'].items())]
        room['world']['worldstate_'].pop('clientworld_',None)
        room['world']['worldid_']=2

    def props(self,vid):return copy.deepcopy(self.dynamic_templates[vid])
    def props_variants(self,vid):return copy.deepcopy(self.dynamic_template_variants.get(vid,{}).get('variants',[]))

    def world_blob(self,role,seed=0,objects=None,spawn=None):
        r=self.room(role,seed);doc=r['world'];chosen=copy.deepcopy(r['objects'] if objects is None else objects)
        marker=r['metadata']['spawn_marker_id'];point=r['spawn'] if spawn is None else spawn;w,h=r['metadata']['spawn_footprint_mp']
        chosen[marker]=dict(xmp=round(point[0]+w/2),ymp=round(point[1]+h),draw=False,spawn=True)
        r['objects']=chosen;self._set_objects(r)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',doc))
