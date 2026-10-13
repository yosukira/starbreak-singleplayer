'Compiled EGY rooms and native Properties; no capture reads at runtime.'
import copy
import json
from pathlib import Path

from ... import pb_layout
from ..hulk_mid.scene import HulkMidPack, normalize
from .generator import EgyModuleGenerator
from .arrivals import choose_safe_arrival
from tools.make_create_world import wrap_compress

ROOT = Path(__file__).resolve().parents[3] / 'maps/egy_run'

def plain(value):
    """Decode canonical wire field names, including nested messages."""
    if isinstance(value, list):
        return [plain(item) for item in value]
    if not isinstance(value, dict):
        return value
    if 'value' in value and 'name' in value:
        return value['value']
    return {key[:-1] if key.endswith('_') else key: plain(item)
            for key, item in value.items()}

class EgyRunPack:
    def __init__(self, root=None, generate=None, *, connection_file=None, native_library=None):
        self.root = Path(root or ROOT)
        self.manifest = json.loads((self.root / 'scene.json').read_text(encoding='utf8'))
        self.dynamic_templates = json.loads((self.root / 'dynamic-templates.json').read_text(encoding='utf8'))
        variants_path = self.root / 'dynamic-template-variants.json'
        self.dynamic_template_variants = (json.loads(variants_path.read_text(encoding='utf8'))
                                          if variants_path.exists() else {})
        self.mid_pack = HulkMidPack(self.root / 'encounters/mid')
        enable_path = self.root/'modules/enabled.json'
        enable_data = json.loads(enable_path.read_text(encoding='utf8')) if enable_path.exists() else {}
        enabled = enable_data.get('enabled', False)
        self.generate = enabled if generate is None else bool(generate)
        self.generator = EgyModuleGenerator(self.root, pool_file=enable_data.get('pool_file') if generate is None else None,
            connection_file=connection_file,native_library=native_library) if self.generate else None
        if generate is None and self.generate and enable_data.get('content_version') != self.generator.data['content_version']:
            raise ValueError('EGY enabled manifest and compiled pool versions differ; unpublished content is not loaded')
        self._rooms = {}
        for role, variants in self.manifest['rooms'].items():
            self._rooms[role] = [json.loads((self.root / name).read_text(encoding='utf8'))
                                 for name in variants]
        self._capture_rooms=dict(self._rooms)
        self.library_manifest=None
        self.library_root=None
        if self.generate and self.generator.connection_config.get('use_native_library'):
            library_root=self.root/(self.generator.connections.data.get('library_directory') or 'native-room-library')
            self.library_root=library_root
            self.library_manifest=json.loads((library_root/'manifest.json').read_text(encoding='utf8'))
            expected=self.generator.connections.data.get('library_version')
            if self.library_manifest['content_version'] != expected:
                raise ValueError('EGY native library and connection recipe versions differ')
            for role,paths in self.library_manifest['rooms'].items():
                eligible=set(self.generator.connections.data['library_eligible'].get(role,[]))
                chosen=[]
                for path in paths:
                    room=json.loads((library_root/path).read_text(encoding='utf8'))
                    if room['variant'] in eligible:
                        room['metadata']['library_content_version']=expected
                        chosen.append(room)
                if not chosen:
                    raise ValueError('EGY role has no verified native library assembly base: '+role)
                self._rooms[role]=chosen
            extra=json.loads((library_root/self.library_manifest['dynamic_templates']).read_text(encoding='utf8'))
            for vid,props in extra.items():
                self.dynamic_templates.setdefault(vid,props)
            self.manifest['active_generation_sources']={role:len(rooms) for role,rooms in self._rooms.items()}
            self.manifest['generation_policy']='Verified native skeleton subset with native connector assembly; source skeleton retained, original server RNG unknown.'

    @property
    def room_names(self):
        return tuple(self._rooms)

    def room(self, role, seed=0, generated=None):
        use_generator = self.generate if generated is None else bool(generated)
        variants = (self._rooms if use_generator else self._capture_rooms)[role]

        result = copy.deepcopy(variants[int(seed) % len(variants)])
        for field in ('objects', 'links', 'switches', 'creators'):
            result[field] = {int(key): value for key, value in result.get(field, {}).items()}
        if role == 'mid':
            objects, traps = self.mid_pack.rolled_objects(int(seed))
            for props in objects.values():
                if props.get('vid') == 'hulk.wall' and props.get('terraintype') == 1:
                    props.update(isinternal=True, boundsworld=True)
                    props.setdefault('draw', True)
                    props.setdefault('z', 60)
            marker = result['metadata']['spawn_marker_id']
            objects[marker] = result['objects'][marker]
            result['objects'] = objects
            result['traps'] = traps
            result['metadata']['dynamic_id_start'] = max(objects) + 1000
            result['metadata']['max_objid'] = max(objects)
            result['world']['worldstate_']['serverworld_']['obj_'] = self._wire_objects(result, objects)
        if use_generator and role not in ('mid','boss'):
            generator = self.generator or EgyModuleGenerator(self.root)
            result = generator.generate(result, int(seed))
            for name,point in result['entries'].items():
                safe=choose_safe_arrival(result['objects'],point,nominal=tuple(result['metadata']['spawn_footprint_mp']))
                if safe is None:
                    raise ValueError('EGY generated room has no IC-safe arrival '+role+'/'+name)
                result['entries'][name]=safe['point']
                result['anchors'][name]=safe['point'][:]
                result['metadata']['entry_support'][name].update(support_objid=safe['support_objid'],feet_y=safe['feet_y'],
                    checked_arrival_footprints_mp=safe['checked_arrival_footprints_mp'])
            result['spawn']=result['entries']['default'][:]
            marker=result['objects'][result['metadata']['spawn_marker_id']]
            width,height=result['metadata']['spawn_footprint_mp']
            marker.update(xmp=result['spawn'][0]+width//2,ymp=result['spawn'][1]+height)
            result['world']['worldstate_']['serverworld_']['obj_'] = self._wire_objects(result, result['objects'])
        return result

    def _wire_objects(self, room, objects):
        layouts = pb_layout.default_layouts()

        rows = []
        for oid, props in sorted(objects.items(), key=lambda item: int(item[0])):
            row = dict(objid_=int(oid), props_=normalize('Properties', props, layouts))
            rows.append(row)
        return rows

    def world_blob(self, role, seed=0, objects=None, spawn=None, generated=None):
        room = self.room(role, seed, generated=generated)
        document = room['world']
        chosen = copy.deepcopy(room['objects'] if objects is None else objects)
        chosen = {int(key): value for key, value in chosen.items()}
        marker = int(room['metadata']['spawn_marker_id'])

        expected = room['objects'][marker]
        if marker in chosen and chosen[marker] != expected and not chosen[marker].get('spawn'):
            marker = max(chosen, default=0) + 1
        anchor = copy.deepcopy(expected)
        point = list(room['spawn'] if spawn is None else spawn)
        width, height = room['metadata']['spawn_footprint_mp']
        anchor.update(xmp=int(round(point[0]+width/2)), ymp=int(round(point[1]+height)),
                      wmp=0, hmp=0, draw=False, spawn=True)
        chosen[marker] = anchor
        document['worldstate_']['serverworld_']['obj_'] = self._wire_objects(room, chosen)
        document['worldstate_'].pop('clientworld_', None)
        document['worldstate_']['serverworld_']['ownerid_'] = 1
        document['worldid_'] = 2
        document['spawnobj_'] = dict(worldid_=1, objid_=marker)
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld', document))

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def props_variants(self, vid):
        return copy.deepcopy(self.dynamic_template_variants[vid])

    def library_room(self,role,seed=0):
        """Explicit unassembled native-library diagnostic, including excluded bases."""
        root=self.library_root or self.root/'native-room-library'
        if self.library_root is None:
            published=json.loads((self.root/'modules/connections-enabled.json').read_text(encoding='utf8'))
            if published.get('use_native_library'):
                connections=json.loads((self.root/'modules'/published['connections_file']).read_text(encoding='utf8'))
                root=self.root/(connections.get('library_directory') or 'native-room-library')
        manifest=json.loads((root/'manifest.json').read_text(encoding='utf8'))
        paths=manifest['rooms'][role]
        room=json.loads((root/paths[int(seed)%len(paths)]).read_text(encoding='utf8'))
        for field in ('objects','links','switches','creators'):
            room[field]={int(key):value for key,value in room.get(field,{}).items()}
        room['metadata']['library_content_version']=manifest['content_version']
        room['metadata']['generation']=dict(mode='capture-native-diagnostic',
            limits='Whole native room only. This diagnostic does not claim verified module assembly.')
        return room
