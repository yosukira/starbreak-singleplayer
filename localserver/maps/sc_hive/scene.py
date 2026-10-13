"""SC Hive scene and measured parameters; only protobuf is shared."""
import copy
import json
import os

from ... import pb_layout

LAB = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
DEFAULT_ROOT = os.path.join(LAB, 'maps', 'sc_hive')

VELOCITY_TO_MP_S = 1000. / 256.

def normalize_props(type_name, value, layouts=None):
    """Convert recorder field names/enums to the real protobuf schema."""
    layouts = layouts or pb_layout.default_layouts()
    if isinstance(value, dict) and 'value' in value and 'name' in value:
        return value['value']
    if not isinstance(value, dict):
        return value
    fields = dict((row['name'], row) for row in
                  layouts.types.get(type_name, {}).get('fields', []))
    out = {}
    for key, item in value.items():
        name = key if key in fields else key + '_'
        if name not in fields:
            raise ValueError('unknown %s field: %s' % (type_name, key))
        child = fields[name].get('item')
        if child:
            if isinstance(item, list):
                item = [normalize_props(child, row, layouts) for row in item]
            else:
                item = normalize_props(child, item, layouts)
        elif isinstance(item, dict) and 'value' in item:
            item = item['value']
        out[name] = item
    return out

class ScHivePack(object):
    def __init__(self, root=None):
        self.root = os.path.abspath(root or DEFAULT_ROOT)
        for name in ('scene', 'entities', 'templates', 'dynamic-templates', 'motion-profiles'):
            with open(os.path.join(self.root, name + '.json'),
                      encoding='utf-8') as handle:
                setattr(self, name.replace('-', '_'), json.load(handle))
        self.createworld_hex = os.path.join(self.root, 'createworld.hex')

    @property
    def overlay_id(self):
        return int(self.scene['overlay_id'])

    @property
    def hive_id(self):
        return int(self.scene['hive_id'])

    @property
    def spawners(self):
        return self.scene['spawners']

    def template(self, objid):
        return copy.deepcopy(self.templates[str(objid)])

    def spirit_guides(self, spawner):
        """Unshielded boundary flights used to fit the steering window."""
        return self.motion_profiles.get('spawners', {}).get(str(spawner), [])

    def steering_windows(self, spawner):

        return [max(c['at_s'] for c in guide['commands'] if 'facing' in c)
                for guide in self.spirit_guides(spawner)]

    def wire_props(self, identity):
        return normalize_props('Properties',
                               self.template(identity) if isinstance(identity, int)
                               else copy.deepcopy(self.dynamic_templates[identity]))
