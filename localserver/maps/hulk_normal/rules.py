"""Original normal-tier fields without mutating the Hard constants module."""
import copy
import json
import os

from ...live import constants
from ...live.enemies import BEHAVIOUR

class HulkNormalRules(object):
    def __init__(self):
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))))
        with open(os.path.join(root, 'maps', 'hulk_normal', 'rules.json'), encoding='utf8') as stream:
            data = json.load(stream)
        self.UNIT_STATS = copy.deepcopy(constants.UNIT_STATS)
        self.BEHAVIOUR = copy.deepcopy(BEHAVIOUR)
        for vid, stats in data['units'].items():
            self.UNIT_STATS[vid].update({k:v for k,v in stats.items() if k != 'size'})
            if vid in self.BEHAVIOUR:
                self.BEHAVIOUR[vid].update(stats)
        self.HORIZONTAL_SPEEDS = {vid:{mode:{int(d):v for d,v in directions.items()}
            for mode,directions in modes.items()} for vid,modes in data['horizontalSpeeds'].items()}
        self.SPAWN_TABLE = tuple(sorted(data['spawnTable'].items()))
        self.SPAWN_PORT_DELAYS = {int(p):v for p,v in data['portalDelays'].items()}
        self.ENEMY_SHOTS = data['enemyShots']
        self.BURST_PROPS = data['burstProps']
        self.GEN_HP = data['genHp']
        self.INTRO_LOADER_VIDS = ('hulk.scavengerloader',)
        self.LOADER_B_VARIANT = 0
        self.LOADER_SPAWN_TOTAL = 1
        self.SPAWN_INITIAL_WAVE = 15
        self.SPAWN_INITIAL_INCLUDE_BOMBS = True
        self.SPAWN_OPENING_BOMBS = 0
        self.TURRET_COUNT_RANGES = {'upper':(1,5), 'lower':(1,5), 'front':(1,2)}
        self.TURRET_MISSILE_CHANCE = 0.0
        self.TURRET_MISSILE_FLOOR = 0
        self.CSBULLET_SPEED = data['bossBullet']['speed']
        self.CSBULLET_DAMAGE = data['bossBullet']['damage']
        self.SCATTERSHOT_SPEED = data['bossScatter']['speed']
        self.SCATTERSHOT_DAMAGE = data['bossScatter']['damage']

    def __getattr__(self, name):
        return getattr(constants, name)
