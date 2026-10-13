"""Dumb boss content pack; no other map's AI is imported."""
import copy
import json
from pathlib import Path

from ... import pb_layout

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / 'maps/dumb_boss'
from ..dumb_common.scene import normalize, motion_position, VELOCITY_TO_MP_S

class DumbBossPack:
    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT)
        for name in ('map', 'scene', 'templates', 'entities', 'programs'):
            setattr(self, name, json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.createworld_hex = str(self.root / 'createworld.hex')
        self.boss_id = int(self.scene['boss_id'])
        behaviour = self.root / 'evidence/behaviour.json'
        self.behaviour = (json.loads(behaviour.read_text(encoding='utf8'))
                          if behaviour.exists() else {})
        self._species = {}
        archetypes = self.root / 'archetypes.json'
        self.archetypes = (json.loads(archetypes.read_text(encoding='utf8'))
                           if archetypes.exists() else {})
        points = self.root / 'animation-points.json'
        self.animations = (json.loads(points.read_text(encoding='utf8'))['vids']
                           if points.exists() else {})

        self.anim_points = dict(self.animations)
        timing=self.root/'evidence/wave-timing.json'
        self.wave_timing=json.loads(timing.read_text(encoding='utf8'))if timing.exists()else {}

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))

    def species_props(self, species):
        'runtime entity props for a species: sizes, HP, damage, armour.'
        key = str(species).replace('dumb.', '')
        if key in self._species:
            return copy.deepcopy(self._species[key])
        want = 'dumb.' + key
        if want in self.archetypes:
            return copy.deepcopy(self.archetypes[want])
        found = None
        for profile in self.programs.get('profiles', {}).values():
            props = profile.get('props') or {}
            if props.get('vid') != want or not profile.get('npc'):
                continue
            found = props
            if key == 'heavy':
                break
        if found is None:
            return None
        props = copy.deepcopy(found)
        for field in ('move', 'lifetime'):
            props.pop(field, None)
        self._species[key] = props
        return copy.deepcopy(props)

    def death_commands(self):
        return [dict(row) for row in (self.programs.get('death') or [])]

    def projectile_props(self, key):
        return copy.deepcopy(self.archetypes.get('dumb.'+key, {}))

    def emit_points(self, vid, slot, position, size, facing=0):
        """WorldObject::getPoints (0x44e450): center X, feet Y, native px."""
        table=self.animations.get(vid,[])
        if not 0<=slot<len(table):
            raise ValueError('invalid animation %s/%s'%(vid,slot))
        x,y=position;w,h=size
        for point in table[slot]['points']:
            flip=1 if facing!=180 else -1
            yield dict(x=x+w/2+flip*point['x']*256,y=y+h+point['y']*256,
                       angle=point['angle'] if flip==1 else (540-point['angle'])%360,
                       time=point['time'])
