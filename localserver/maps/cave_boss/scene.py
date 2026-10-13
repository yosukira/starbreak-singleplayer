'Local battle simulator runtime.'
import copy
import json
from pathlib import Path
from ... import pb_layout

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / 'maps/cave_boss'

def normalize(type_name, value, layouts=None):
    layouts = layouts or pb_layout.default_layouts()
    if isinstance(value, dict) and 'value' in value and 'name' in value:
        return value['value']
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

class CaveBossPack:
    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT)
        for name in ('map', 'scene', 'templates', 'dynamic-templates', 'entities'):
            attr = name.replace('-', '_')
            setattr(self, attr, json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.createworld_hex = str(self.root / 'createworld.hex')
        exterior = set(self.scene.get('exterior_ids', []))
        self.solids = [tuple(p.get(k, 0) / 256 for k in ('xmp', 'ymp', 'wmp', 'hmp'))
                       for i,p in self.templates.items() if p.get('vid') == 'cave.wall' or int(i) in exterior]
        self.ledges = [tuple(p.get(k, 0) / 256 for k in ('xmp', 'ymp', 'wmp', 'hmp'))
                       for p in self.templates.values() if p.get('vid') == 'cave.ledge']
        self.surfaces = self.solids + self.ledges
        self.scales = {int(i): p for i, p in self.templates.items()
                       if p.get('vid', '').startswith('cave.scale')}
        self.body_parts = [int(i) for i, p in self.templates.items()
                           if p.get('move', {}).get('relativeto', {}).get('objid') == self.scene['boss_id']]

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))
