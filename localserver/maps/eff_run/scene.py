"""Self-contained EFF native content with existing Forest encounter capsules."""
import copy
import json
from pathlib import Path
from ... import pb_layout
from ..fireforest.scene import ForestPack
from ..jungle_common.scene import normalize
from tools.make_create_world import wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/eff_run'

class EffRunPack:
    def __init__(self,root=None,generate=None,*,third_mode=None):
        self.root=Path(root or ROOT)
        self.manifest=json.loads((self.root/'content.json').read_text(encoding='utf8'))
        self.dynamic_templates=json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.dynamic_variants=json.loads((self.root/'dynamic-template-variants.json').read_text(encoding='utf8'))
        self._rooms={role:json.loads((self.root/path).read_text(encoding='utf8')) for role,path in self.manifest['rooms'].items()}
        enabled=self.root/'modules/enabled.json'
        config=json.loads(enabled.read_text(encoding='utf8')) if enabled.exists() else {}
        self.generate_enabled=config.get('enabled') is True if generate is None else bool(generate)
        self.generator=None
        self.third_generator=None
        self.third_mode=third_mode
        self.fifth_generator=None
        if self.generate_enabled:
            from .generator import EffModuleGenerator
            self.generator=EffModuleGenerator(self.root)
            if config.get('content_version')!=self.generator.pool['content_version']:
                raise ValueError('Forest module enabled/pool version differs')
        if self.generate_enabled or third_mode is not None:
            from .third_generator import EffThirdGenerator
            self.third_generator=EffThirdGenerator(self.root)
        if self.generate_enabled:
            fifth_config=self.root/'fifth-modules/enabled.json'
            if fifth_config.exists() and json.loads(fifth_config.read_text(encoding='utf8')).get('enabled') is True:
                from .fifth_generator import EffFifthGenerator
                self.fifth_generator=EffFifthGenerator(self.root)
    @property
    def room_names(self):return tuple(self._rooms)
    def props(self,vid):return copy.deepcopy(self.dynamic_templates[vid])
    def props_variants(self,vid):return copy.deepcopy(self.dynamic_variants[vid])
    def encounter_pack(self,role,seed=0):
        if role not in ('mid','boss'):return None
        return ForestPack(role,self.root/'encounters'/role,seed=seed)
    @staticmethod
    def _set_objects(room):
        objects=room['objects']
        room['world']['worldstate_'].pop('clientworld_',None)

        room['world']['worldstate_']['serverworld_']['obj_']=[dict(objid_=oid,props_=normalize('Properties',p))
            for oid,p in sorted(objects.items())]
        room['world']['worldstate_']['serverworld_']['ownerid_']=1;room['world']['worldid_']=2
        room['world']['spawnobj_']=dict(worldid_=1,objid_=room['metadata']['spawn_marker_id'])
    def room(self,role,seed=0):
        room=copy.deepcopy(self._rooms[role])
        for field in ('objects','creators','links','gate_pairs'):room[field]={int(k):v for k,v in room.get(field,{}).items()}

        if self.generator is not None:room=self.generator.generate(room,seed)
        if role=='rock_ascent' and self.third_generator is not None:
            room=self.third_generator.generate(room,seed,self.third_mode)
        if role in ('mid','boss'):
            pack=self.encounter_pack(role,seed);room['objects']={int(k):copy.deepcopy(p) for k,p in pack.templates.items()}
            native_spawn=pack.scene['spawn_id'];p=room['objects'][native_spawn];w,h=room['metadata']['spawn_footprint_mp']
            room['spawn']=[p['xmp']-w//2,p['ymp']-h];room['entries']={key:room['spawn'][:] for key in room['entries']}
            room['anchors']=copy.deepcopy(room['entries']);marker=max(room['objects'])+1
            room['objects'][marker]=dict(xmp=p['xmp'],ymp=p['ymp'],draw=False,spawn=True,wmp=0,hmp=0)
            room['metadata'].update(spawn_marker_id=marker,max_objid=marker,dynamic_id_start=marker+1000)
            target='home' if role=='boss' else 'exploration5'
            room['links']={pack.scene['portal_id']:dict(destination=target,entry='default',interaction='interact',
                requires=role+'_defeated',external=role=='boss',evidence='SIM adapter to existing Forest victory portal')}
            room['metadata']['encounter_scene']=copy.deepcopy(pack.scene)
        room['world']['worldprops_']['seed_']=int(seed)&0x7fffffff
        room['metadata']['runtime_seed']=int(seed)&0x7fffffff
        if role in ('exploration5','boss'):
            from .walls import collar,cells,TILE
            w,h=room['metadata']['spawn_footprint_mp']
            anchors=[(point[0]+w/2,point[1]+h/2) for point in room['entries'].values()]
            anchors.append((room['spawn'][0]+w/2,room['spawn'][1]+h/2))
            anchors.extend((p.get('xmp',0)+p.get('wmp',0)/2,p.get('ymp',0)+p.get('hmp',0)/2)
                for oid,p in room['objects'].items() if oid in room['links'])
            if role=='exploration5':

                focus={(int(p['xmp']+p['wmp'])//TILE-1,int(p['ymp'])//TILE)
                       for oid,p in room['objects'].items() if oid in (25,190)}
            else:
                focus=set().union(*(cells(p) for p in room['objects'].values() if p.get('vid')=='forest.rockspawner'))
                roofs=[p for p in room['objects'].values() if p.get('vid')=='forest.ground' and p.get('hmp')==TILE]
                roof=min(roofs,key=lambda p:(p['ymp'],-p['wmp']))
                focus.add(((roof['xmp']+roof['wmp'])//TILE-1,roof['ymp']//TILE))
            room['objects'],room['metadata']['native_seam_collar']=collar(room['objects'],anchors,focus=focus)
            room['metadata'].update(max_objid=max(room['objects']),dynamic_id_start=max(room['objects'])+1000)

        if role=='exploration5' and self.fifth_generator is not None and self.fifth_generator.default_enabled:
            room=self.fifth_generator.generate(room,seed)
        self._set_objects(room)
        return room
    def world_blob(self,role,seed=0,objects=None,spawn=None):
        room=self.room(role,seed)
        if objects is not None:room['objects']={int(k):copy.deepcopy(v) for k,v in objects.items()}
        point=list(spawn or room['spawn']);w,h=room['metadata']['spawn_footprint_mp'];marker=room['metadata']['spawn_marker_id']
        if marker in room['objects'] and not room['objects'][marker].get('spawn'):marker=max(room['objects'])+1
        room['metadata']['spawn_marker_id']=marker
        room['objects'][marker]=dict(xmp=round(point[0]+w/2),ymp=round(point[1]+h),draw=False,spawn=True,wmp=0,hmp=0)
        self._set_objects(room)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld',room['world']))
