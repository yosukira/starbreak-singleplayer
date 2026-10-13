'Static lab_mid (LabHard5) scene pack.'

import json
import os

from ... import pb_layout

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEFAULT_ROOT = os.path.join(LAB_ROOT, 'maps', 'lab_mid')

_LAYOUTS = None

def layouts():
    global _LAYOUTS
    if _LAYOUTS is None:
        _LAYOUTS = pb_layout.default_layouts()
    return _LAYOUTS

def normalize_props(type_name, value):
    """Add the protobuf trailing underscores the wire encoder expects."""
    if not isinstance(value, dict):
        return value
    fields = {}
    for spec in layouts().types.get(type_name, {}).get('fields') or []:
        fields[spec['name']] = spec
    out = {}
    for key, item in value.items():
        name = key
        if name not in fields and name + '_' in fields:
            name = name + '_'
        spec = fields.get(name)
        if spec is not None and spec.get('item'):
            child = spec['item']
            if isinstance(item, dict):
                item = normalize_props(child, item)
            elif isinstance(item, list):
                item = [normalize_props(child, row)
                        if isinstance(row, dict) else row for row in item]
        out[name] = item
    return out

def _load(path):
    handle = open(path, 'r', encoding='utf-8')
    try:
        return json.load(handle)
    finally:
        handle.close()

class LabMidPack(object):
    """Loaded scene + measured mechanics for the ETL mid-boss room."""

    def __init__(self, root=None):
        self.root = os.path.abspath(root or DEFAULT_ROOT)
        self.scene = _load(os.path.join(self.root, 'scene.json'))
        self.mechanics = _load(os.path.join(self.root, 'mechanics.json'))
        self.templates = _load(os.path.join(self.root, 'templates.json'))
        self.dynamic_templates = _load(
            os.path.join(self.root, 'dynamic-templates.json'))
        self.motion = _load(os.path.join(self.root, 'rules', 'motion.json'))
        gates_path = os.path.join(self.root, 'rules', 'time-gates.json')
        self.timegates = _load(gates_path) if os.path.isfile(gates_path) else {}
        zombie_path = os.path.join(self.root, 'rules', 'zombie-motion.json')
        self.zombie_motion = (_load(zombie_path)
                              if os.path.isfile(zombie_path) else {})
        self.createworld_hex = os.path.join(self.root, 'createworld.hex')

    @property
    def boss_objid(self):
        return int(self.scene['boss']['objid'])

    @property
    def bounds(self):
        return list(self.scene['bounds'])

    @property
    def block_ids(self):
        return [int(row['objid']) for row in self.scene['blocks']]

    def block(self, objid):
        for row in self.scene['blocks']:
            if int(row['objid']) == int(objid):
                return dict(row)
        return None

    def object_ids(self, vid):
        return list(self.scene['inventory'].get(vid) or [])

    def template(self, objid):
        return dict(self.templates.get(str(objid)) or {})

    def template_for_vid(self, vid):
        for objid, props in self.templates.items():
            if props.get('vid') == vid:
                return int(objid), dict(props)
        return None, {}

    def wire_props(self, vid):
        props = dict(self.dynamic_templates.get(vid) or {})
        if not props:
            _, props = self.template_for_vid(vid)
        for key in ('move', 'move_'):
            props.pop(key, None)
        return normalize_props('Properties', props)

    def wire_props_for(self, objid):
        return normalize_props('Properties', self.template(objid))
