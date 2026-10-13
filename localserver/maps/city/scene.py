'Compact fixed City map packs, with no runtime capture dependency.'
import copy
import json
from pathlib import Path
from ... import pb_layout

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

class CityPack:
    def __init__(self, kind='mid', root=None):
        self.kind = kind
        self.root = Path(root or Path(__file__).resolve().parents[3] / ('maps/city_' + kind))
        for name in ('map', 'scene', 'templates', 'dynamic-templates', 'entities','emitters'):
            setattr(self, name.replace('-', '_'), json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.createworld_hex = str(self.root / 'createworld.hex')
        self.solids = [self.rect(p) for p in self.templates.values()
                       if p.get('terraintype') == 1 and p.get('vid') not in ('city.barricade', 'city.sbarricade')]
        self.ledges = [self.rect(p) for p in self.templates.values() if p.get('terraintype') == 2]

    @staticmethod
    def rect(p):
        return tuple(p.get(k, 0) / 256 for k in ('xmp', 'ymp', 'wmp', 'hmp'))

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))
