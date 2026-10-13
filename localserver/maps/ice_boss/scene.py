"""Native Ice content and seeded layout selection, independent of recordings."""
import copy
import json
import random
from pathlib import Path
from ... import pb_layout
from tools.make_create_world import unwrap_decompress, wrap_compress

ROOT = Path(__file__).resolve().parents[3] / 'maps/ice_boss'

def choose_room_seed(previous_seed=None, root=None):
    'Keep the three runtime layouts random, without consecutive repeats.'
    variants = json.loads((Path(root or ROOT) / 'layouts.json').read_text(encoding='utf8'))
    count = len(variants)
    previous = random.Random(int(previous_seed)).randrange(count) if previous_seed is not None else None
    seed = random.SystemRandom().randrange(1 << 30)
    while count > 1 and random.Random(seed).randrange(count) == previous:
        seed = (seed + 1) % (1 << 30)
    return seed

def normalize(type_name, value, layouts=None):
    layouts = layouts or pb_layout.default_layouts()
    if not isinstance(value, dict):
        return value
    fields = {f['name']: f for f in layouts.types[type_name]['fields']}
    result = {}
    for key, item in value.items():
        name = key if key in fields else key + '_'
        if name not in fields:
            raise ValueError('unknown %s field %s' % (type_name, key))
        child = fields[name].get('item')
        if child:
            item = ([normalize(child, row, layouts) for row in item] if isinstance(item, list)
                    else normalize(child, item, layouts))
        result[name] = item
    return result

class IceBossPack:
    def __init__(self, root=None, seed=None):
        self.root = Path(root or ROOT)
        for name in ('map', 'scene', 'templates', 'dynamic-templates', 'entities', 'layouts'):
            setattr(self, name.replace('-', '_'), json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.createworld_hex = str(self.root / 'createworld.hex')
        if seed is not None:
            variant = random.Random(int(seed)).choice(self.layouts)
            self.templates = copy.deepcopy(variant['templates'])
            self.scene.update(copy.deepcopy(variant['scene']))
        self.solids = [self.rect(p) for p in self.templates.values() if p.get('terraintype') == 1]
        self.ledges = [self.rect(p) for p in self.templates.values() if p.get('terraintype') == 2]
        self.surfaces = self.solids + self.ledges
        self.spikes = {int(i): self.rect(p) for i, p in self.templates.items() if p.get('vid') == 'ice.ddebris'}

    @staticmethod
    def rect(p):
        return tuple(p.get(k, 0) / 256 for k in ('xmp', 'ymp', 'wmp', 'hmp'))

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))

def roll_create_world(blob, seed):
    pack = IceBossPack(seed=seed)
    layouts = pb_layout.default_layouts()
    msg = layouts.decode('CreateWorld', unwrap_decompress(blob))
    msg['worldprops_']['seed_'] = int(seed)
    msg['worldstate_']['serverworld_']['obj_'] = [dict(objid_=int(i), props_=pack.wire_props(p))
                                              for i, p in pack.templates.items()]
    return wrap_compress(layouts.encode('CreateWorld', msg))
