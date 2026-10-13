'Static lab_hard (ETL) scene pack.'

import json
import os

from ... import pb_layout
from .terrain import thicken_boundary_templates

HERE = os.path.dirname(os.path.abspath(__file__))
LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
DEFAULT_ROOT = os.path.join(LAB_ROOT, 'maps', 'lab_hard')

_LAYOUTS = None

def layouts():
    global _LAYOUTS
    if _LAYOUTS is None:
        _LAYOUTS = pb_layout.default_layouts()
    return _LAYOUTS

def normalize_props(type_name, value):
    """Add the protobuf trailing underscores the wire encoder expects.

    The extracted templates use the decoded (no-suffix) field names; the
    encoder looks fields up by their real name.  Same convention as the
    Hulk room loader, kept here so the map pack does not import it.
    """
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

def _strip_wire_suffixes(value):
    """Turn CreateWorld props_ keys into the pack's plain key names."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = key[:-1] if key.endswith('_') else key
            out[name] = _strip_wire_suffixes(item)
        return out
    if isinstance(value, list):
        return [_strip_wire_suffixes(item) for item in value]
    return value

class LabHardPack(object):
    'Local battle simulator runtime.'

    TERRAIN_VIDS = ('lab.ledge', 'lab.wall', 'lab.crushwall')

    def __init__(self, root=None):
        self.root = os.path.abspath(root or DEFAULT_ROOT)
        self.scene = _load(os.path.join(self.root, 'scene.json'))
        self.templates = _load(os.path.join(self.root, 'templates.json'))
        self.phase_doc = _load(os.path.join(self.root, 'phases.json'))
        self.cadence = _load(os.path.join(self.root, 'cadence.json'))
        self.dynamic_templates = _load(
            os.path.join(self.root, 'dynamic-templates.json'))
        evidence = os.path.join(self.root, 'rules')
        self.gas_mode = _load(os.path.join(evidence, 'gas-mode.json'))
        line_path=os.path.join(evidence,'line-gas.json')
        self.line_gas=_load(line_path) if os.path.exists(line_path) else {}
        policy_path=os.path.join(evidence,'attack-policy.json')
        self.attack_policy=_load(policy_path) if os.path.exists(policy_path) else {}
        seeker_path=os.path.join(evidence,'seeker-policy.json')
        self.seeker_policy=_load(seeker_path) if os.path.exists(seeker_path) else {}
        self.add_mechanics = _load(
            os.path.join(evidence, 'add-mechanics.json'))
        self.claw_motion = _load(os.path.join(evidence, 'claw-motion.json'))
        self.arm_motion = _load(os.path.join(evidence, 'arm-motion.json'))
        movement_path = os.path.join(evidence, 'boss-movement.json')
        self.boss_movement = (_load(movement_path)
                              if os.path.exists(movement_path) else {})
        special_path = os.path.join(evidence, 'special-gas.json')
        self.special_gas = (_load(special_path)
                            if os.path.exists(special_path) else {})

        death_path = os.path.join(evidence, 'death-sequence.json')
        self.death_sequence = _load(death_path) if os.path.exists(death_path)\
            else {}
        self.createworld_hex = os.path.join(self.root, 'createworld.hex')
        self.floor_hex = os.path.join(self.root, 'createworld-floor.hex')
        thicken_boundary_templates(self.templates)

    def apply_room(self, room):
        'Replace collidable terrain with a runtime CreateWorld variant.'
        if room is None:
            return self
        grouped = {}
        for item in (getattr(room, 'objects', None) or {}).values():
            vid = getattr(item, 'vid', None)
            if vid in self.TERRAIN_VIDS:
                grouped.setdefault(vid, []).append(item)
        if not grouped:
            return self
        inventory = self.scene.setdefault('inventory', {})
        for vid, items in grouped.items():
            items.sort(key=lambda row: row.objid)
            inventory[vid] = [row.objid for row in items]
            for row in items:
                self.templates[str(row.objid)] = _strip_wire_suffixes(row.props)
        crushwalls = grouped.get('lab.crushwall') or []
        if crushwalls:
            self.scene['walls'] = [
                dict(_strip_wire_suffixes(row.props), objid=row.objid)
                for row in crushwalls]
        thicken_boundary_templates(self.templates)
        return self

    @property
    def boss_objid(self):
        return int(self.scene['boss']['objid'])

    @property
    def fight_start(self):
        return float(self.phase_doc['phases'][0]['start'])

    @property
    def triple_spread(self):
        """Measured triple-volley shape (offsets + aim lag)."""
        return self.phase_doc.get('triple_spread') or {}

    @property
    def bounds(self):
        return list(self.scene['bounds'])

    def object_ids(self, vid):
        return list(self.scene['inventory'].get(vid) or [])

    @property
    def claw_ids(self):
        return self.object_ids('lab.armclaw')

    @property
    def segment_ids(self):
        return self.object_ids('lab.barmsegment')

    @property
    def wall_ids(self):
        return self.object_ids('lab.crushwall')

    @property
    def gas(self):
        return self.gas_mode

    def anchor_props(self, objid):
        """Static arena object an anchored add hangs from (objid -> props)."""
        return self.template(objid)

    def add_spawns(self):
        'runtime anchored-add recipes (one per runtime create).'
        return list(self.add_mechanics.get('anchored_spawns') or [])

    def add_waves(self):
        'Anchored-add recipes grouped into their runtime 36-add batches.'
        recipes = self.add_spawns()
        waves = []
        for row in recipes:
            when = float(row.get('at') or 0.0)
            if waves and when - waves[-1]['at'] <= 2.0:
                waves[-1]['recipes'].append(row)
            else:
                waves.append({'at': when, 'recipes': [row]})
        return waves

    def seeker_spawns(self):
        'runtime lab.seeker creates, phase-relative and arm-linked.'
        assigned = []
        for row in self.arm_motion.get('wall_pulls') or []:
            if row.get('phase_index') is None:
                continue
            base = (float(row.get('phase_at') or 0.0)
                    + float(row.get('anim_lead_s') or 0.0)
                    + float(row.get('freeze_s') or 0.0))
            for seeker in row.get('seekers') or []:
                assigned.append(dict(
                    phase_index=int(row['phase_index']),
                    phase_at=base + float(seeker.get('delay_s') or 0.0),
                    props=dict(seeker.get('props') or {}),
                    claw=int(row.get('claw') or 0),
                    wall=int(row.get('wall') or 0),
                    arm_segment=int(seeker.get('arm_segment') or 0),
                    arm_base=int(seeker.get('arm_base') or 0),
                    arm_claw=int(seeker.get('arm_claw') or 0),
                    arm_fraction=float(seeker.get('arm_fraction') or 0.0)))
        if assigned:
            return assigned
        return list((self.add_mechanics.get('seeker') or {}).get('spawns') or [])

    def seeker_waves(self):
        'runtime lab.seeker spawn waves with their runtime fire cadence.'
        seeker = self.add_mechanics.get('seeker') or {}
        spawns = seeker.get('spawns') or []
        waves = []
        for row in spawns:
            when = float(row.get('at') or 0.0)
            if waves and when - waves[-1]['at'] <= 0.5:
                waves[-1]['spawns'].append(row)
            else:
                waves.append({'at': when, 'spawns': [row]})
        for wave in waves:
            wave['first_shot_delay_s'] = seeker.get('first_shot_delay_s')
            wave['shot_period_s'] = seeker.get('shot_period_s')
        return waves

    @property
    def seeker_params(self):
        return self.add_mechanics.get('seeker') or {}

    @property
    def gas_wall_id(self):
        return self.gas_mode.get('gas', {}).get('wall')

    def template(self, objid):
        return dict(self.templates.get(str(objid)) or {})

    def templates_for(self, vid):
        return dict((oid, self.template(oid)) for oid in self.object_ids(vid))

    def template_for_vid(self, vid):
        for objid, props in self.templates.items():
            if props.get('vid') == vid:
                return objid, dict(props)
        return None, {}

    def wire_props(self, vid):
        """Create-props for a dynamic object of ``vid``, encoder ready."""
        props = dict(self.dynamic_templates.get(vid) or {})
        if not props:
            _, props = self.template_for_vid(vid)
        for key in ('move', 'move_'):
            props.pop(key, None)
        return normalize_props('Properties', props)

    def wire_props_for(self, objid):
        return normalize_props('Properties', self.template(objid))

    @property
    def play_bounds(self):
        'runtime inner room rectangle, before any walls move inward.'
        return self.bounds
