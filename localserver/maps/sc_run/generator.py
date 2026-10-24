"""Seeded SC rooms assembled from complete compiled native corridor pieces.

Only verified equal collision/material collars are joined. No source wall is
cut, stretched, mirrored or sealed with invented terrain. The source room's
branches and every original object remain present; native pieces lengthen a
compatible corridor together with their creatures and devices.
"""
import copy
import hashlib
import json
import random
from pathlib import Path

from ..eub_run.generator import face, refs, relocate, rect
from ..dumb_common.scene import normalize

ROOT = Path(__file__).resolve().parents[3] / 'maps/sc_run'
SCHEMA = 'sc-native-corridor-assembly-v1'
ID_MAPS = ('sc_surn_counts', 'sc_mledge_velocity', 'sc_skeleton_source_ids')


def digest(value):
    def clean(v):
        if isinstance(v, dict):
            return {str(k): clean(x) for k, x in v.items()}
        if isinstance(v, (tuple, list)):
            return [clean(x) for x in v]
        return v
    return hashlib.sha256(json.dumps(clean(value), sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest()


def geometry_sha(objects):
    return digest(sorted((p.get('vid', ''), *rect(p), p.get('shapetype', 0),
                          p.get('terraintype', 0), p.get('z', 0), p.get('isinternal', False))
                         for p in objects.values()
                         if p.get('terraintype') in (1, 2) and p.get('ownertype') != 2))


def collar(objects, plane, side):
    # SC carries separate draw-only wall rectangles around its thin native
    # collision strips. Verify those collars as well as collision so inserted
    # pieces retain wall depth and do not expose mismatched background cuts.
    probe = {oid: dict(p, vid='@render/'+p['vid'], terraintype=2)
             if p.get('vid') == 'sc.wall' and p.get('terraintype') not in (1, 2)
             else p for oid, p in objects.items()}
    terrain = [p for p in probe.values() if p.get('terraintype') in (1, 2)]
    if not terrain:
        return [], 0
    y0 = min(p.get('ymp', 0) for p in terrain)
    y1 = max(p.get('ymp', 0)+p.get('hmp', 0) for p in terrain)
    rows = face(probe, plane, side, y0, y1)
    if not rows:
        return [], 0
    top = min(r[1] for r in rows)
    return [list((r[0], r[1]-top, r[2], r[3]-top, *r[4:])) for r in rows], top+y0


def cloud_points(value):
    if isinstance(value, list):
        return [p for item in value for p in cloud_points(item)]
    if not isinstance(value, dict):
        return []
    out = []
    for k, item in value.items():
        if k in ('pos_mp', 'point_mp') and isinstance(item, list) and len(item) == 2:
            out.append(item)
        elif k == 'bounds_mp' and isinstance(item, list) and len(item) == 4:
            out.extend((item[:2], item[2:]))
        else:
            out.extend(cloud_points(item))
    return out


def translate_cloud(value, dx, dy):
    result = copy.deepcopy(value)
    def walk(v):
        if isinstance(v, list):
            for item in v:
                walk(item)
        elif isinstance(v, dict):
            for k, item in v.items():
                if k in ('pos_mp', 'point_mp') and isinstance(item, list) and len(item) == 2:
                    item[0] += dx; item[1] += dy
                elif k == 'bounds_mp' and isinstance(item, list) and len(item) == 4:
                    item[0] += dx; item[2] += dx
                    item[1] += dy; item[3] += dy
                else:
                    walk(item)
    walk(result)
    return result


def navigation_groups(room):
    groups = []
    for p in room['metadata'].get('ports', []):
        groups.append({int(p['id']), *([int(p['trigger_id'])] if p.get('trigger_id') is not None else [])})
    for oid, switch in room.get('switches', {}).items():
        groups.append({int(oid), *map(int, switch.get('targets', []))})
    return groups


def closed_partition(room, chosen):
    objects = room['objects']
    if not chosen.issubset(objects):
        return False
    if any((oid in chosen) != (ref in chosen) for oid, p in objects.items() for ref in refs(p)):
        return False
    if any((int(oid) in chosen) != (int(parent) in chosen) for oid, parent in room.get('creators', {}).items()):
        return False
    return not any(g & chosen and not g.issubset(chosen) for g in navigation_groups(room))


def validate(room, original=None):
    objects = room['objects']; meta = room['metadata']
    for oid, p in objects.items():
        if not set(refs(p)).issubset(objects):
            raise ValueError('SC module has dangling object reference: '+str(oid))
    if any(int(i) not in objects or int(parent) not in objects
           for i, parent in room.get('creators', {}).items()):
        raise ValueError('SC module has dangling creator')
    for field in ID_MAPS:
        if any(int(oid) not in objects for oid in meta.get(field, {})):
            raise ValueError('SC module has dangling '+field)
    for g in navigation_groups(room):
        if not g.issubset(objects):
            raise ValueError('SC module lost navigation assembly')
    if any(int(i) not in objects for i in room.get('links', {})):
        raise ValueError('SC module lost linked door')
    marker = objects[int(meta['spawn_marker_id'])]
    w, h = meta['spawn_footprint_mp']
    if [marker.get('xmp', 0)-w/2, marker.get('ymp', 0)-h] != list(room['spawn']):
        raise ValueError('SC spawn marker and arrival disagree')
    for port in meta.get('ports', []):
        p = objects[int(port['id'])]; x, y, w, h = rect(p)
        if port['at'] != [x+w/2, y+h/2] or port['bounds'] != [x, y, x+w, y+h]:
            raise ValueError('SC translated door metadata disagrees with native body')
        if port['entry'] not in room['entries']:
            raise ValueError('SC translated door has no arrival')
    if original:
        if not set(original['objects']).issubset(objects):
            raise ValueError('SC generator removed original objects')
        if set(original.get('links', {})) != set(room.get('links', {})):
            raise ValueError('SC generator changed navigation topology')
    return True


class ScRoomGenerator:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.data = json.loads((self.root/'modules/pool.json').read_text(encoding='utf8'))
        semantic = {k: v for k, v in self.data.items() if k != 'content_version'}
        if digest(semantic) != self.data['content_version']:
            raise ValueError('SC compiled module pool fingerprint mismatch')
        for template in self.data['templates'].values():
            for field in ('objects', 'creators'):
                template[field] = {int(k): v for k, v in template.get(field, {}).items()}
        self._cache = {}

    def generate(self, room, seed, *, rule_index=None, count=None, template_ids=None):
        key = None
        if rule_index is None and count is None and template_ids is None:
            key = (int(seed), digest(room))
            if key in self._cache:
                return copy.deepcopy(self._cache[key])
        result = self._generate(room, seed, rule_index=rule_index, count=count, template_ids=template_ids)
        if key is not None:
            if len(self._cache) >= 128:
                self._cache.pop(next(iter(self._cache)))
            self._cache[key] = copy.deepcopy(result)
        return result

    def _generate(self, room, seed, *, rule_index=None, count=None, template_ids=None):
        room = copy.deepcopy(room); meta = room['metadata']; descriptor = meta['template']
        previous = meta.get('generation', {})
        if previous.get('schema') == SCHEMA:
            if previous.get('seed') == int(seed):
                return room
            raise ValueError('SC generation requires the original room recipe')
        rules = self.data['recipes'].get(descriptor, [])
        if not rules:
            meta['generation'] = dict(schema=SCHEMA, mode='unchanged-native', seed=int(seed),
                reason='Fixed/special room or no verified compatible native corridor partition',
                content_version=self.data['content_version'])
            return room
        if geometry_sha(room['objects']) != self.data['base_geometry'][descriptor]:
            raise ValueError('SC room geometry changed; recompile verified module interfaces: '+descriptor)
        original = copy.deepcopy(room); rng = random.Random(SCHEMA+':'+descriptor+':'+str(int(seed)))
        rule = rules[rng.randrange(len(rules)) if rule_index is None else int(rule_index)]
        repeats = rng.choice(rule['allowed_counts']) if count is None else int(count)
        if repeats not in rule['allowed_counts']:
            raise ValueError('SC repetition count outside verified native assembly policy')
        chosen = [rng.choice(rule['templates']) for _ in range(repeats)] if template_ids is None else list(template_ids)
        if len(chosen) != repeats or any(t not in rule['templates'] for t in chosen):
            raise ValueError('SC template/collar pair not verified')
        objects = room['objects']; plane = rule['plane_mp']
        if any(p.get('xmp', 0) < plane < p.get('xmp', 0)+p.get('wmp', 0) for p in objects.values()):
            raise ValueError('SC seam would cut a native object')
        moving = {oid for oid, p in objects.items() if p.get('xmp', 0) >= plane}
        if not closed_partition(room, moving):
            raise ValueError('SC seam splits a native reference/creator/navigation group')
        for profile in meta.get('sc_wraith_emitters', []):
            xs = [p[0] for p in cloud_points(profile)]
            if xs and min(xs) < plane <= max(xs):
                raise ValueError('SC seam splits an emitter coordinate cloud')
        profile, top = collar(objects, plane, 'left')
        if (profile != rule['profile'] or collar(objects, plane, 'right') != (profile, top)):
            raise ValueError('SC live recipe no longer matches compiled native collar')
        width = sum(self.data['templates'][t]['width_mp'] for t in chosen)
        identity = {oid: oid for oid in objects}
        for oid in moving:
            objects[oid] = relocate(objects[oid], width, 0, identity)
        for port in meta.get('ports', []):
            if int(port['id']) in moving:
                port['at'][0] += width; port['bounds'][0] += width; port['bounds'][2] += width
                room['entries'][port['entry']][0] += width
        if int(meta['spawn_marker_id']) in moving:
            room['spawn'][0] += width
            if 'default' in room['entries']:
                room['entries']['default'][0] += width
        meta['sc_wraith_emitters'] = [translate_cloud(p, width, 0)
            if cloud_points(p) and min(x[0] for x in cloud_points(p)) >= plane else p
            for p in meta.get('sc_wraith_emitters', [])]
        next_id = max(objects)+1000; cursor = plane; bridges = []
        for name in chosen:
            template = self.data['templates'][name]
            mapping = {oid: next_id+n for n, oid in enumerate(sorted(template['objects']))}
            next_id += len(mapping)
            dx, dy = cursor-template['origin'][0], top-template['origin'][1]
            for oid, p in template['objects'].items():
                objects[mapping[oid]] = relocate(p, dx, dy, mapping)
                if p.get('vid') == 'sc.skeleton':
                    # Preserve the source identity used by the local variant
                    # FIT; a new wire ID must not reroll the module's mix.
                    meta.setdefault('sc_skeleton_source_ids', {})[mapping[oid]] = oid
            for oid, parent in template['creators'].items():
                room.setdefault('creators', {})[mapping[oid]] = mapping[int(parent)]
            for field in ID_MAPS:
                for oid, value in template.get('metadata', {}).get(field, {}).items():
                    meta.setdefault(field, {})[mapping[int(oid)]] = copy.deepcopy(value)
            meta['sc_wraith_emitters'].extend(translate_cloud(p, dx, dy)
                for p in template.get('metadata', {}).get('sc_wraith_emitters', []))
            bridges.append(dict(template=name, object_id_map=mapping, translation_mp=[dx, dy]))
            cursor += template['width_mp']
        terrain = [rect(p) for p in objects.values() if p.get('terraintype') in (1, 2)]
        bounds = [min(p[0] for p in terrain), min(p[1] for p in terrain),
                  max(p[0]+p[2] for p in terrain), max(p[1]+p[3] for p in terrain)]
        meta.update(bounds=bounds, object_count=len(objects), max_objid=max(objects), dynamic_id_start=max(objects)+1000)
        # Decoration.bounds_ values in compiled SC content are normalized
        # scattering margins, not world-coordinate clipping rectangles.
        # Preserve their complete native definitions; the new native terrain
        # supplies the expanded spatial extent to the renderer.
        room['world']['worldprops_']['seed_'] = int(seed) & 0x7fffffff
        meta['generation'] = dict(schema=SCHEMA, mode='native-interface-assembly', seed=int(seed),
            content_version=self.data['content_version'], geometry_sha=geometry_sha(objects),
            rule=rule['id'], plane_mp=plane, moving_source_ids=sorted(moving), translation_mp=[width, 0],
            repeats=repeats, bridges=bridges,
            evidence='Complete native SC pieces and equal 64px collision/material collars; local seeded selection/repetition',
            limits='Retained native branches; source grammar, probabilities and retail RNG are unknown.')
        validate(room, original)
        state = room['world']['worldstate_']
        state.pop('clientworld_', None)
        state['serverworld_']['obj_'] = [dict(objid_=int(oid), props_=normalize('Properties', p))
                                       for oid, p in sorted(objects.items())]
        room['world']['spawnobj_'] = dict(worldid_=1, objid_=int(meta['spawn_marker_id']))
        return room


# Keep the adapter discoverable beside the other native module generators.
ScModuleGenerator = ScRoomGenerator
