"""Reuse the SC scene/kinematics; keep Elite rules and initial wire separate."""
import copy
import json
from pathlib import Path

from ..sc_boss.scene import ScBossPack, DEFAULT_ROOT as BASE_ROOT
from ..sc_boss.difficulty import nova_groups, scale_stats, scale_move, scaled_int

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / 'maps/sc_boss_elite'

def load_rules(root=None):
    root = Path(root or DEFAULT_ROOT)
    rules = json.loads((BASE_ROOT / 'entities.json').read_text(encoding='utf8'))
    overrides = json.loads((root / 'entities.json').read_text(encoding='utf8'))
    modifiers = overrides['difficulty']
    rules['difficulty'] = copy.deepcopy(modifiers)
    rules['intro_wave_period_s'] /= modifiers['squid_spawn_rate']
    for stage in rules['stages']:
        stage['hp'] = scaled_int(stage['hp'], modifiers['enemy_hp'])
        stage['wave_period_s'] /= modifiers['squid_spawn_rate']
        stage['squid_cap'] += modifiers['squid_cap_bonus']
    squid = rules['squid']
    squid['shoot_period_s'] /= modifiers.get('squid_fire_rate', 1.)
    squid['hp'] = scaled_int(squid['hp'], modifiers['enemy_hp'])
    squid['damage'] = scaled_int(20, modifiers['enemy_damage'])
    squid['movement_speed_multiplier'] = modifiers['squid_speed']
    squid['shot_count_weights'] = dict(modifiers['squid_shot_count_weights'])
    squid['spread_step_degrees'] = modifiers['squid_spread_step_degrees']
    for key in ('accel_x_mp_s2', 'accel_y_mp_s2'):
        squid[key] *= modifiers['squid_speed']
    rules['source_policy']['elite'] = overrides['policy']
    return rules

def initial_props(identity, props, modifiers):

    hp = 1. if str(identity) == '6' else modifiers['enemy_hp']
    result = scale_stats(props, hp, modifiers['enemy_damage'])
    if str(identity) == '6':
        result['armor'] = modifiers['boss_armor']
    if str(identity) in ('13', '14') and result.get('move'):
        result['move'] = scale_move(result['move'], modifiers['squid_speed'])
    return result

class EliteScBossPack(ScBossPack):
    def __init__(self, root=None):
        super().__init__(BASE_ROOT)
        self.root = Path(root or DEFAULT_ROOT)
        self.entities = load_rules(self.root)
        modifiers = self.entities['difficulty']
        self.scene['id'] = 'sc_boss_elite'
        self.templates = {key: initial_props(key, props, modifiers)
                          for key, props in self.templates.items()}

        for flight in self.programs['flights']:
            if flight['props'].get('move'):
                flight['props']['move'] = scale_move(flight['props']['move'], modifiers['squid_speed'])
        for flight in self.spawn_patterns.get('flights', []):
            if flight['props'].get('move'):
                flight['props']['move'] = scale_move(flight['props']['move'], modifiers['squid_speed'])
        self.nova_groups = {}
        for key, timing in self._timing_cache.items():
            if key[-1] == 'nova':
                self.nova_groups[id(timing)] = nova_groups(timing['attacks'])
        self.createworld_hex = str(self.root / 'createworld.hex')
