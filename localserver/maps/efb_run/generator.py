"""Native Ice corridor assembly, including each module's population.

Complete compiled source objects translate as a unit. Native slopes, fixed
nest segments, door caps and tower skeletons are retained, never cropped or
mirrored. Original grammar/branching probabilities have not been recovered.
"""
import copy
import hashlib
import json
import random
from pathlib import Path

from ..eub_run.generator import digest, rect, overlaps, refs, relocate, face, supports

TILE = 16384
FOOTPRINTS = ((10240, 20480), (15360, 21504))
FIXED_ROLES = frozenset(('mid', 'boss'))

def geometry_sha(objects):
    return digest(sorted((p.get('vid', ''), *rect(p), p.get('shapetype', 0),
        p.get('terraintype', 0)) for p in objects.values() if p.get('terraintype') in (1, 2)))

def base_version(root):
    root = Path(root)
    scene = json.loads((root/'scene.json').read_text(encoding='utf8'))
    return digest(dict(scene=scene, rooms={path:json.loads((root/path).read_text(encoding='utf8'))
        for paths in scene['rooms'].values() for path in paths}))

def arrival_groups(room):
    groups = []
    for name, proof in room['metadata']['entry_support'].items():
        anchor = int(proof['anchor_objid'])
        point = room['entries'][name]
        body = dict(xmp=point[0], ymp=point[1],
            wmp=FOOTPRINTS[0][0], hmp=FOOTPRINTS[0][1])
        groups.append({anchor, *supports(room['objects'], body)})
    return groups

def validate(room, original=None):
    objects = room['objects']
    for oid, p in objects.items():
        if any(r not in objects for r in refs(p)):
            raise ValueError('Ice module lost object reference: '+str(oid))
    if any(i not in objects or parent not in objects for i, parent in room['creators'].items()):
        raise ValueError('Ice module lost creator relationship')
    if not set(room['links']).issubset(objects):raise ValueError('Ice module lost navigation body')
    for name, point in room['entries'].items():
        for width, height in FOOTPRINTS:
            body = (point[0]+(FOOTPRINTS[0][0]-width)/2,
                point[1]+FOOTPRINTS[0][1]-height, width, height)
            if any(overlaps(body, rect(p)) for p in objects.values()
                   if p.get('terraintype') == 1):
                raise ValueError('Ice arrival blocked: '+name)
    marker = objects[room['metadata']['spawn_marker_id']]
    if (marker['xmp'], marker['ymp']) != (room['spawn'][0]+FOOTPRINTS[0][0]/2,
                                        room['spawn'][1]+FOOTPRINTS[0][1]):
        raise ValueError('Ice spawn marker/arrival disagree')
    if original is not None:
        for oid, p in objects.items():
            if p.get('ownertype') != 2 or p.get('hitpoints', 0) <= 0:continue
            before = original['objects'].get(oid)
            if before and supports(original['objects'], before, oid) and not supports(objects, p, oid):
                raise ValueError('Ice creature lost source feet support: '+str(oid))
            for sid, q in objects.items():
                if q.get('terraintype') != 1 or not overlaps(rect(p), rect(q)):continue
                old_wall = original['objects'].get(sid)
                if (before is None or old_wall is None or
                        not overlaps(rect(before), rect(old_wall))):

                    source = room['metadata'].get('generation', {}).get('added_source', {})
                    pair = source.get(str(oid)), source.get(str(sid))
                    if not (pair[0] and pair[1] and pair[0]['module'] == pair[1]['module']
                            and overlaps(pair[0]['rect'], pair[1]['rect'])):
                        raise ValueError('New Ice wall intersects creature: '+str((oid, sid)))
    return True

class EfbModuleGenerator:
    def __init__(self, root):
        self.root = Path(root)
        self.data = json.loads((self.root/'modules/pool.json').read_text(encoding='utf8'))
        if digest({k:v for k, v in self.data.items() if k != 'content_version'}) != self.data['content_version']:
            raise ValueError('Ice module pool fingerprint mismatch')
        if base_version(self.root) != self.data['base_content_version']:
            raise ValueError('Ice module pool/base content differs')
        self.versions = dict(pool=self.data['content_version'], base=self.data['base_content_version'],
            adapter=digest({name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest()
                            for name in ('generator.py', 'scene.py')}))
        for module in self.data['templates'].values():
            for field in ('objects', 'creators'):
                module[field] = {int(k):v for k, v in module[field].items()}

    def generate(self, room, seed, *, rule_index=None, module_ids=None):
        original = copy.deepcopy(room)
        room = copy.deepcopy(room)
        rules = self.data['recipes'].get(room['variant'], [])
        if room['role'] in FIXED_ROLES or not rules:
            room['metadata']['generation'] = dict(mode='retained-native-skeleton',
                reason='Fixed encounter or no complete native interface recovered',
                geometry_changed=False, content_version=self.data['content_version'])
            return room
        seed = int(seed) & 0x7fffffff
        rng = random.Random('efb-native-sequence-v1:%s:%s' % (room['variant'], seed))
        rule = rules[rng.randrange(len(rules)) if rule_index is None else rule_index]
        if module_ids is None:

            count = rng.choice((1, 2, 3))
            lengths = sorted({self.data['templates'][key]['width_mp'] for key in rule['templates']})
            module_ids = []
            for _ in range(count):
                length = rng.choice(lengths)
                module_ids.append(rng.choice([key for key in rule['templates']
                    if self.data['templates'][key]['width_mp'] == length]))
        if not 1 <= len(module_ids) <= 3 or any(key not in rule['templates'] for key in module_ids):
            raise ValueError('Ice sequence contains an unverified module/interface')
        modules = [self.data['templates'][key] for key in module_ids]
        width = sum(m['width_mp'] for m in modules)
        shift = rule['direction']*width
        moving = set(rule['moving_ids'])
        objects, creators = room['objects'], room['creators']
        identity = {oid:oid for oid in objects}
        if (room['metadata']['spawn_marker_id'] in moving or not moving.issubset(objects) or
                any((oid in moving) != (ref in moving) for oid, p in objects.items() for ref in refs(p)) or
                any((oid in moving) != (parent in moving) for oid, parent in creators.items()) or
                any(group & moving and not group.issubset(moving) for group in arrival_groups(room))):
            raise ValueError('Ice graph cut splits source relationships/navigation')
        for oid in moving:objects[oid] = relocate(objects[oid], shift, 0, identity)
        for name, proof in room['metadata']['entry_support'].items():
            if int(proof['anchor_objid']) in moving:
                room['entries'][name][0] += shift
                proof['nativeFeet'][0] += shift
        next_id = max(objects)+1000
        offset = 0
        inserted, added_source = [], {}
        for index, (key, module) in enumerate(zip(module_ids, modules)):
            x = rule['plane_mp']+offset if rule['direction'] > 0 else rule['plane_mp']-offset-module['width_mp']
            dx, dy = x-module['origin'][0], rule['y0_mp']-module['origin'][1]
            mapping = {oid:next_id+n for n, oid in enumerate(sorted(module['objects']))}
            next_id += len(mapping)
            for oid, p in module['objects'].items():
                objects[mapping[oid]] = relocate(p, dx, dy, mapping)
                added_source[str(mapping[oid])] = dict(module=index, source_id=oid, rect=rect(p))
            for oid, parent in module['creators'].items():creators[mapping[oid]] = mapping[parent]
            inserted.append(dict(template=key, source=module['source'], object_id_map=mapping,
                translation_mp=[dx, dy]))
            offset += module['width_mp']
        room['metadata']['generation'] = dict(mode='native-ice-module-sequence', seed=seed,
            base_variant=original['variant'], rule=rule['id'], moving_source_ids=sorted(moving),
            translation_mp=[shift, 0], modules=inserted, added_source=added_source,
            geometry_sha=geometry_sha(objects), content_version=digest(self.versions),
            component_versions=copy.deepcopy(self.versions),
            evidence='D full Ice objects/creatures/devices; FIT native boundary; SIM sequence count/weights',
            limits='Horizontal connectors only; complete original tower grammar and branching remain incomplete')
        room['metadata'].update(max_objid=max(objects), dynamic_id_start=max(objects)+1000,
            structural_wall_ids=[oid for oid, p in objects.items() if p.get('vid') == 'ice.wall'])
        room['variant'] += '-assembled-'+room['metadata']['generation']['geometry_sha'][:12]
        room['world']['worldprops_']['seed_'] = seed
        validate(room, original)
        from .scene import EfbRunPack
        EfbRunPack._set_objects(room)
        return room
