"""Local reordering of two complete native Ice tower layers.

Both layers occur once. No primitive, population, door or spawn is removed,
resized or mirrored. Native top collars have an identical full64px material
and collision profile; each bottom contact was observed against that profile.
The order is SIM and normal traversal remains a user playtest.
"""
import copy
import hashlib
import json
from pathlib import Path
import random

from .generator import base_version, digest, rect, refs, relocate, geometry_sha, arrival_groups
from .sloped_generator import source_signature, validate as validate_arrivals, polygon_overlap, support_measure

ROLE = 'sector_3'
ROOT = Path(__file__).resolve().parents[3]/'maps/efb_run'

def validate(room, original, translations):
    objects = room['objects']
    if set(objects) != set(original['objects']):
        raise ValueError('Tower must preserve every original object exactly once')
    for field in ('creators', 'links', 'switches', 'entries', 'spawn'):
        if room[field] != original[field]:
            raise ValueError('Tower changed fixed navigation/creator metadata: '+field)
    if room['metadata']['entry_support'] != original['metadata']['entry_support']:
        raise ValueError('Tower changed arrival support metadata')
    identity = {i:i for i in objects}
    for oid, before in original['objects'].items():
        dy = translations.get(oid, 0)
        if objects[oid] != relocate(before, 0, dy, identity):
            raise ValueError('Tower changed full source Properties beyond translation: '+str(oid))
        if before.get('ownertype') == 2 and before.get('hitpoints', 0) > 0:
            after = objects[oid]
            if support_measure(objects, rect(after)) != support_measure(original['objects'], rect(before)):
                raise ValueError('Tower creature lost its original feet support: '+str(oid))
            for sid, wall in objects.items():
                if wall.get('terraintype') != 1:
                    continue
                x,y,w,h = rect(after); sx,sy,sw,sh = rect(wall)
                if not (x < sx+sw and x+w > sx and y < sy+sh and y+h > sy):
                    continue
                if polygon_overlap(wall, rect(after)) and not polygon_overlap(original['objects'][sid], rect(before)):
                    raise ValueError('Tower created a foreign wall/body intersection: '+str((oid,sid)))
    validate_arrivals(room, original)
    return True

class EfbTowerGenerator:
    def __init__(self, root=None):
        self.root = Path(root or ROOT)
        self.data = json.loads((self.root/'modules/tower-pool.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.data.items() if k != 'content_version'}) != self.data['content_version']:
            raise ValueError('Tower pool fingerprint mismatch')
        if base_version(self.root) != self.data['base_content_version']:
            raise ValueError('Tower pool source content changed')
        for layer in self.data['layers'].values():
            for field in ('objects', 'creators'):
                layer[field] = {int(k):v for k,v in layer[field].items()}
        directory = Path(__file__).parent
        self.versions = dict(pool=self.data['content_version'], base=self.data['base_content_version'],
            pure_relocation=hashlib.sha256((directory.parent/'eub_run/generator.py').read_bytes()).hexdigest(),
            adapter=digest({name:hashlib.sha256((directory/name).read_bytes()).hexdigest()
                for name in ('tower_generator.py','scene.py','generator.py','sloped_generator.py')}))

    def generate(self, room, seed=0, *, order=None):
        if room['role'] != ROLE or source_signature(room) != self.data['source_signature']:
            raise ValueError('Tower requires the unchanged original sector_3 recipe')
        original = copy.deepcopy(room)
        room = copy.deepcopy(room)
        seed = int(seed) & 0x7fffffff
        rng = random.Random('efb-native-tower-v1:'+original['variant']+':'+str(seed))
        order = list(rng.choice(self.data['allowed_orders'])) if order is None else list(order)
        if order not in self.data['allowed_orders']:
            raise ValueError('Tower order has not been compiled with complete native interfaces')
        layers = self.data['layers']
        groups = {key:set(layer['objects']) for key,layer in layers.items()}
        affiliation = {oid:key for key,group in groups.items() for oid in group}
        protected = set(room['links']) | set(room['switches']) | {room['metadata']['spawn_marker_id']}
        for proof in room['metadata']['entry_support'].values():
            protected.update(int(proof[k]) for k in ('anchor_objid','support_objid') if proof.get(k) is not None)
        if protected & set(affiliation):
            raise ValueError('Tower layer contains a fixed navigation/arrival object')
        for oid,p in room['objects'].items():
            for target in refs(p):
                if target and affiliation.get(oid) != affiliation.get(target):
                    raise ValueError('Tower cut splits an object reference')
        if any(affiliation.get(i) != affiliation.get(parent) for i,parent in room['creators'].items()):
            raise ValueError('Tower cut splits creator family')
        for group in arrival_groups(room):
            if group & set(affiliation):
                raise ValueError('Tower layer moves original arrival/support group')
        identity = {i:i for i in room['objects']}
        cursor = self.data['bottom_mp']
        translations, placed = {}, []
        for key in order:
            layer = layers[key]
            dy = cursor-layer['bottom_mp']
            for oid,p in layer['objects'].items():
                if original['objects'].get(oid) != p:
                    raise ValueError('Tower layer full source Properties changed')
                room['objects'][oid] = relocate(p, 0, dy, identity)
                translations[oid] = dy
            placed.append(dict(layer=key, source_ids=sorted(layer['objects']), translation_mp=[0,dy],
                source=copy.deepcopy(layer['source']), top_collar=layer['top_collar']['signature'],
                bottom_contact_evidence=copy.deepcopy(layer['bottom_contact_evidence'])))
            cursor -= layer['height_mp']
        if cursor != self.data['top_mp']:
            raise ValueError('Tower changes the original fixed total height')
        components = dict(self.versions, source_recipe=source_signature(original))
        g = dict(mode='native-ice-tower-layer-reorder', seed=seed, base_variant=original['variant'],
            modules=placed, order=order, observed_order=self.data['observed_order'],
            geometry_sha=geometry_sha(room['objects']), geometry_changed=order != self.data['observed_order'],
            content_version=digest(components), component_versions=components,
            evidence='D two complete original layers/Props/observed64px contact collars; FIT compatible interface substitution; SIM equal ordering',
            native_verified=False, manual_verification_pending=True,
            limits='Two layers once each in one640px region; whole tower grammar/length/weights and normal combat traversal remain unverified')
        room['metadata']['generation'] = g
        room['metadata'].update(source_object_count=len(original['objects']), object_count=len(room['objects']))
        room['variant'] += '-tower-'+g['geometry_sha'][:12]
        room['world']['worldprops_']['seed_'] = seed
        validate(room, original, translations)
        from .scene import EfbRunPack
        EfbRunPack._set_objects(room)
        return room
