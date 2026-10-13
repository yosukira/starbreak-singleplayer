"""Map packs for the live simulator.

Runtime modules (wire / room / motion / projectiles / bridge) stay map
agnostic.  A map pack owns its static scene, its measured constants and its
per-tick controller:

    localserver/maps/egy_hulk/   existing Hulk Hard fight (wrapped LiveSession)
    localserver/maps/lab_hard/   ETL / labHard Zomputer boss room
    localserver/maps/lab_mid/    ETL / labHard5 mid-boss room

The seam is deliberately small so the working Hulk controller can stay
untouched until the lab_hard skeleton runs.
"""

class MapController(object):
    'Per-tick behaviour of one map.'

    name = 'unnamed'

    def prepare(self):
        """Reset to the state the client sees when the room loads."""

    def step(self, dt):
        """Advance one tick and return a list of semantic events."""
        raise NotImplementedError

    def add_damage(self, value, objid=None):
        """Feed damage reported by the player for this map's boss."""

    def status(self):
        return {}

_REGISTRY = {}

def _ice_boss(**kwargs):
    from .ice_boss.controller import IceBossController
    return IceBossController(**kwargs)

def register(map_id, factory):
    _REGISTRY[map_id] = factory
    return factory

def available():
    return sorted(_REGISTRY)

def create(map_id, *args, **kwargs):
    if map_id not in _REGISTRY:
        raise KeyError('unknown map %r (available: %s)'
                       % (map_id, ', '.join(available()) or 'none'))
    return _REGISTRY[map_id](*args, **kwargs)

def _lab_hard(**kwargs):
    from .lab_hard.controller import LabHardBossController
    return LabHardBossController(**kwargs)

def _lab_mid(**kwargs):
    from .lab_mid.controller import LabMidController
    return LabMidController(**kwargs)

def _sc_hive(**kwargs):
    from .sc_hive.controller import ScHiveController
    return ScHiveController(**kwargs)

register('lab_hard', _lab_hard)
register('lab_mid', _lab_mid)
register('sc_hive', _sc_hive)

def _sc_bonesaw(**kwargs):
    from .sc_bonesaw.controller import BonesawController
    return BonesawController(**kwargs)

register('sc_bonesaw', _sc_bonesaw)

def _sc_patriarch(**kwargs):
    from .sc_patriarch.controller import PatriarchController
    return PatriarchController(**kwargs)

register('sc_patriarch', _sc_patriarch)

def _sc_boss(**kwargs):
    from .sc_boss.controller import ScBossController
    return ScBossController(**kwargs)

register('sc_boss', _sc_boss)

def _sc_boss_elite(**kwargs):
    from .sc_boss_elite.controller import EliteScBossController
    return EliteScBossController(**kwargs)

register('sc_boss_elite', _sc_boss_elite)

def _dumb_boss(**kwargs):
    from .dumb_boss.controller import DumbBossController
    return DumbBossController(**kwargs)

register('dumb_boss', _dumb_boss)

def _dumb_wave(**kwargs):
    from .dumb_wave.controller import DumbWaveController
    return DumbWaveController(**kwargs)

register('dumb_wave',_dumb_wave)

def _jungle_boss(**kwargs):
    from .jungle_boss.controller import JungleBossController
    return JungleBossController(**kwargs)

register('jungle_boss', _jungle_boss)

def _jungle_mid(**kwargs):
    from .jungle_mid.controller import JungleMidController
    return JungleMidController(**kwargs)

register('jungle_mid', _jungle_mid)

def _hulk_mid(**kwargs):
    from .hulk_mid.controller import HulkMidController
    return HulkMidController(**kwargs)

register('hulk_mid', _hulk_mid)

def _cave_boss(**kwargs):
    from .cave_boss.controller import CaveBossController
    return CaveBossController(**kwargs)

register('cave_boss', _cave_boss)

register('ice_boss', _ice_boss)

def _ice_mid(**kwargs):
    from .ice_mid.controller import IceMidController
    return IceMidController(**kwargs)

register('ice_mid', _ice_mid)

def _forest_mid(**kwargs):
    from .fireforest.controller import ForestController
    return ForestController(kind='mid', **kwargs)

def _forest_boss(**kwargs):
    from .fireforest.controller import ForestController
    return ForestController(kind='boss', **kwargs)

register('forest_mid', _forest_mid)
register('forest_boss', _forest_boss)

def _city_mid(**kwargs):
    from .city.mid import CityMidController
    return CityMidController(**kwargs)

def _city_boss(**kwargs):
    from .city.boss import CityBossController
    return CityBossController(**kwargs)

register('city_mid', _city_mid)
register('city_boss', _city_boss)

def _dumb_normal_wave(**kwargs):
    from .dumb_wave.controller import DumbWaveController
    from .dumb_normal_wave.scene import DumbNormalWavePack
    kwargs.setdefault('pack',DumbNormalWavePack())
    return DumbWaveController(**kwargs)

def _dumb_mid(**kwargs):
    from .dumb_mid.controller import DumbMidController
    return DumbMidController(**kwargs)

register('dumb_normal_wave',_dumb_normal_wave)
register('dumb_mid',_dumb_mid)
