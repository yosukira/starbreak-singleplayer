'runtime Hulk Hard constants used by the live controller.'

import os

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CAPTURE_DIR = os.path.join(LAB_ROOT, 'data', 'catalogue')

LEDGE_IDS = (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20, 21, 23, 25,
             26, 27, 29, 30, 31)
GEN_IDS = (33, 35, 37, 39, 41)
PANEL_IDS = (34, 36, 38, 40, 42)
TURRET_IDS = tuple(range(43, 60)) + (62,) + tuple(range(1000, 1032))

EXTRA_TURRET_ID = 62

PORTAL_IDS = (19, 22, 24, 28, 32)
SHIP_ID = 5
ARENA_FLOOR_ID = 1

BOSS_DOOR_IDS = (70, 71)

BOSS_PART_IDS = (LEDGE_IDS + GEN_IDS + PANEL_IDS + TURRET_IDS + PORTAL_IDS)

BOSS_ENTRANCE_TIMELINE_PATH = os.path.join(
    CAPTURE_DIR, '..', 'mechanics', 'boss-entrance-timeline.json')

LOADER_EMIT_POINTS_DIR = os.path.join(CAPTURE_DIR, '..', 'mechanics')

GEN_PANEL_ORDER = ((42, 41), (34, 33), (40, 39), (36, 35), (38, 37))

GEN_PANEL_WEIGHTS = (1.5, 1.0, 1.0, 1.0, 1.0)

INTRO_LOADER_IDS = (64, 65, 66)
INTRO_LOADER_VIDS = ('hulk.scavengerloader', 'hulk.eloader')
INTRO_LOADER_MIN = 1
INTRO_LOADER_MAX = 4

LOADER_POSE_STAND = 4
LOADER_POSE_WALK = 5
LOADER_POSE_RUN = 6

SCAVENGER_JUMP_SPEED = 185000
SHREDDER_JUMP_SPEED = 215000
SHREDDER_ENERGY_JUMP_SPEED = 205000

SHREDDER_PURSUIT_JUMP_INTERVAL = 4.2

ENGINEER_RESUMMON_SECONDS = 4.58

SHREDDER_CLIMB_SPEED = 49000.0
SHREDDER_CLIMB_MAX_HEIGHT = 245000

SHREDDER_CLIMB_TOP_HOLD = 1.33

SHREDDER_CLIMB_DISMOUNT_HOLD = .60

NAPALM_FALL_CAP = 160000.0

NAPALM_GRAVITY = 340000.0
SHIP_RETURN_LEAD = 1.3
SHIP_RETURN_TAIL = 1.8
SHIP_RETURN_CHECK_INTERVAL = 0.10
SHIP_RETURN_JUMP_COOLDOWN = 0.75

SCAVENGER_DWELL_QUANTILES = ((.10, .92), (.25, 2.43), (.50, 5.44),
                             (.75, 9.97), (.90, 17.03))
SCAVENGER_DWELL_MINIMUM = .25

SHREDDER_DWELL_QUANTILES = ((.10, .73), (.25, 1.19), (.50, 2.16),
                            (.75, 4.08), (.90, 8.35))
SHREDDER_DWELL_MINIMUM = .25

SCAVENGER_CROUCH_SECONDS = .60

DECK_EXIT_ROLL_PROBABILITY = .35

SHREDDER_DECK_MIN_RIM_TURNS = 2

SCAVENGER_RUNOFF_PROBABILITY = .12
SHREDDER_RUNOFF_PROBABILITY = .38

CROUCH_VIDS = frozenset(('hulk.scavengergunman', 'hulk.scavengershotgunner',
                         'hulk.scavengermissiler', 'hulk.scavengercommando',
                         'hulk.rpgscavenger', 'hulk.scavengerengineer',
                         'hulk.scavengershredder',
                         'hulk.scavengershredderenergy'))
SHIP_DIAGONAL_PROBABILITY = 0.8

SCAVENGER_AGGRO_RANGE = 1000 * 256

SCAVENGER_ACTIVATION_RANGE = 1500 * 256

LOADER_WALK_SPEED = 12000

LOADER_DECK_PATROL_WIDTH = 64 * 256
LOADER_RUN_SPEED = 59000
LOADER_RUN_DISTANCE = 700 * 256

LOADER_ATTACK_ANIM = 4
LOADER_ATTACK_MS = 990
LOADER_ATTACK_RANGE = 620 * 256
LOADER_ATTACK_COOLDOWN = 0.6

LOADER_PURSUIT = True
LOADER_BEHAVIOUR = {
    'hulk.scavengerloader': {
        'attack_anim': 4, 'attack_ms': 990,
        'aggro_range': 2400 * 256,
        'attack_range': 120 * 256,
        'run_range': 900 * 256,
        'walk_speed': LOADER_WALK_SPEED,
        'run_speed': LOADER_RUN_SPEED,

        'attack_cooldown': 1.1,
        'emit': None,
        'leash': 0,
    },
    'hulk.eloader': {
        'attack_anim': 4, 'attack_ms': 2574,
        'aggro_range': 3400 * 256,
        'attack_range': 600 * 256,
        'run_range': 1500 * 256,
        'walk_speed': LOADER_WALK_SPEED,
        'run_speed': LOADER_RUN_SPEED,
        'attack_cooldown': 1.4,
        'emit': {'vid': 'hulk.flame', 'count': 10,
                 'points': 'eloader-flame-points.json'},

        'emit_move': {'vel_': {
            'xvel_': -12500, 'yvel_': 0, 'xaccel_': -4, 'yaccel_': 0,
            'minxvel_': -12500, 'minyvel_': -12500,
            'maxxvel_': 0, 'maxyvel_': 0}},
        'leash': 0,
    },
}

REVEAL_FALLBACK_SECONDS = 25.36

LEDGE_DEPLOY_DELAY = 13.77
LEDGE_ANIM_MS = 1782
LEDGE_TERRAIN_DELAY = 1.80
GEN_LEDGE_DELAY = 0.05
LEDGE_RETRACT_SECONDS = 6.8
LEDGE_EXTEND_ANIM_MS = 1782

PANEL_FIRST_DELAY = 20.43
PANEL_LIT_DURATION = 15.1
PANEL_LIT_INTERVAL = 15.1
PANEL_NEXT_AFTER_DESTROY = 15.12
PANEL_ACTIVATION_DELAY = 0.52
PANEL_INTERACTION_DELAY = 0.50

PANEL_OPEN_DELAY = 0.05
GEN_ARM_DELAY = 0.93

GEN_WINDOW_SECONDS = 3.0
GEN_DISARM_DELAY = 0.93
GEN_HP = 2000
GEN_TERRAIN_TYPE = 1
LEDGE_TERRAIN_TYPE = 2
DEFAULT_ARMOR = 75

CSBULLET_TURRETS = (44, 46, 48, 49, 52, 54, 55, 56, 57, 58, 59)
SCATTERSHOT_TURRETS = (50, 51, 53)
BOSS_MISSILE_TURRETS = (43, 45, 47)

FIXED_FORWARD_TURRETS = (50, 51, 52, 53)

CSBULLET_PERIOD = 4.159
SCATTERSHOT_PERIOD = 7.168
BOSS_MISSILE_PERIOD = 9.663
TURRET_FIRST_VOLLEY = 12.10

TURRET_TURN_PER_SECOND = 100.0

TURRET_ACQUIRE_DELAY = 10.0

TURRET_LOCK_RANGE = 1000 * 256

TURRET_CHARGE_SECONDS = {'csbullet': 1.079, 'scattershot': 1.583,
                         'bossmissile': 2.0235}

CSBULLET_SPEED = 22000 * 1000 / 256
CSBULLET_DAMAGE = 45
SCATTERSHOT_SPEED = 16000 * 1000 / 256
SCATTERSHOT_DAMAGE = 35
SCATTERSHOT_PELLETS = 6
SCATTERSHOT_SPREAD_DEG = 40.0
BOSS_MISSILE_SPEED = 6000 * 1000 / 256
BOSS_MISSILE_TURN_DEG = 100.0
BOSS_MISSILE_ACCEL = 60000

BOSS_MISSILE_MAX_SPEED = 117187.5
BOSS_MISSILE_DAMAGE = 40
BOSS_MISSILE_LIFETIME = 40.0

MISSILE_LOCK_RANGE = 400 * 256
MISSILE_LOCK_CONE_DEG = 180.0
MISSILE_STEER_SECONDS = .05
MISSILE_VELOCITY_QUANTUM = 1000.0

PROJECTILE_CULL_MARGIN = 2 * 1024 * 1024

SPAWN_COVER_DELAY = 0.42
SPAWN_PREVIEW_DELAY = 0.50
SPAWN_PREVIEW_FEET = 92160
SPAWN_TABLE = (

    ('hulk.scavengerloader', 56),
    ('hulk.scavengershredder', 26),
    ('hulk.scavengermissiler', 24),
    ('hulk.scavengerengineer', 35),
    ('hulk.scavengergunman', 31),
    ('hulk.scavengershredderenergy', 34),
    ('hulk.scavengershotgunner', 22),
    ('hulk.scavengercommando', 45),
    ('hulk.rpgscavenger', 38),
    ('hulk.pathlighton', 43),
)

LOADER_SPAWN_TOTAL = 56
LOADER_B_VARIANT = 29

SPAWN_REFERENCE_HEIGHT = 23040
SPAWN_DEFAULT_SIZE = (12800, 23040)
SPAWN_SIZES = {
    'hulk.scavengerloader': (20480, 40960),
    'hulk.pathlighton': (8192, 8192),
    'hulk.pathlightarmed': (8192, 8192),
}

SPAWN_FIRST_DELAY = 17.95

SPAWN_PORT_INTERVAL = (4.0, 7.0)
SPAWN_RATE = len(PORTAL_IDS) / (sum(SPAWN_PORT_INTERVAL) / 2.0)
SPAWN_WAVE_INTERVAL = 1.0 / SPAWN_RATE
SPAWN_WAVE_JITTER = (0.80, 1.20)
SPAWN_WAVE_SIZE_WEIGHTS = (45, 13, 5)
SPAWN_INITIAL_WAVE = 13

SPAWN_MAX_ALIVE = 180
SPAWN_WAVE_MIN = 1
SPAWN_WAVE_MAX = 3

BIG_ROBOT_VIDS = ('hulk.scavengerloader', 'hulk.eloader')
BIG_ROBOT_PER_PORT = 2
BIG_ROBOT_MAX_ALIVE = 10

UNIT_STATS = {
    'hulk.scavengerloader': {'hp': 2000, 'armor': 75, 'contact': 35,
                             'speed': 230, 'kind': 'melee'},
    'hulk.scavengershredder': {'hp': 1000, 'armor': 0, 'contact': 40,
                               'speed': 271, 'kind': 'melee'},
    'hulk.scavengershredderenergy': {'hp': 1000, 'armor': 0, 'contact': 40,
                                     'speed': 278, 'kind': 'melee'},
    'hulk.scavengergunman': {'hp': 750, 'armor': 0, 'contact': 30,
                             'speed': 230, 'kind': 'shooter',
                             'shot': 'ngrenade', 'shot_cd': 2.1},
    'hulk.scavengershotgunner': {'hp': 750, 'armor': 0, 'contact': 30,
                                 'speed': 229, 'kind': 'shooter',
                                 'shot': 'sshot', 'shot_cd': 2.1,
                                 'pellets': 3, 'spread': 14.0},
    'hulk.scavengermissiler': {'hp': 750, 'armor': 0, 'contact': 30,
                               'speed': 229, 'kind': 'shooter',
                               'shot': 'smissile', 'shot_cd': 2.1},
    'hulk.scavengercommando': {'hp': 750, 'armor': 0, 'contact': 30,
                               'speed': 230, 'kind': 'shooter',
                               'shot': 'ngrenade', 'shot_cd': 2.1},
    'hulk.scavengerengineer': {'hp': 750, 'armor': 0, 'contact': 30,
                               'speed': 217, 'kind': 'engineer'},
    'hulk.eloader': {'hp': 2000, 'armor': 75, 'contact': 35,
                     'speed': 59000, 'kind': 'melee'},
    'hulk.rpgscavenger': {'hp': 750, 'armor': 0, 'contact': 30,
                         'speed': 78000, 'kind': 'shooter'},
    'hulk.bdrone': {'hp': 500, 'armor': 50, 'contact': 0,
                    'speed': 39000, 'kind': 'shooter'},
    'hulk.sdrone': {'hp': 500, 'armor': 50, 'contact': 0,
                    'speed': 40000, 'kind': 'shooter'},
    'hulk.pathlighton': {'hp': 280, 'armor': 0, 'contact': 0,
                         'speed': 39000, 'kind': 'bomb-rover'},
    'hulk.pathlightarmed': {'hp': 280, 'armor': 0, 'contact': 40,
                            'speed': 39000, 'kind': 'bomb-rover'},
}

ENEMY_STANDOFF = 260 * 256

SCAVENGER_ATTACK_RANGE = {
    'hulk.scavengergunman': 550 * 256,
    'hulk.scavengershotgunner': 575 * 256,
    'hulk.scavengermissiler': 550 * 256,
    'hulk.scavengercommando': 600 * 256,
    'hulk.rpgscavenger': 550 * 256,
}

ENEMY_SHOTS = {

    'sgmbullet': {'vid': 'hulk.sgmbullet', 'damage': 35, 'speed': 117187.5,
                  'lifetime': 4.5, 'size': 2560},
    'sshot': {'vid': 'hulk.sshot', 'damage': 35, 'speed': 70312.5,
              'lifetime': 4.0, 'size': 2048, 'burst_vid': 'hulk.sshotx'},

    'smissile': {'vid': 'hulk.smissile', 'damage': 35, 'speed': 7500 * 1000 / 256,
                 'lifetime': 40.0, 'size': 4096, 'homing': True,
                 'accel': 60000, 'max_speed': 136718.75, 'turn_rate': 100,
                 'burst_vid': 'hulk.smissilex'},

    'ngrenade': {'vid': 'hulk.ngrenade', 'damage': 30, 'speed': 92129,
                 'lifetime': 1.5, 'size': 6144, 'kind': 'falling',
                 'gravity': 97656.25, 'burst_vid': 'hulk.napalm'},
    'boundinggrenade': {'vid': 'hulk.boundinggrenade', 'damage': 35,
                        'speed': 135500, 'lifetime': 8.0, 'size': 4096,
                        'kind': 'bouncing', 'gravity': 120000,
                        'burst_vid': 'hulk.grenadex'},

    'cannonshot': {'vid': 'hulk.dronebomb', 'damage': 55, 'speed': 0,
                   'lifetime': 0.5, 'size': 8192, 'kind': 'falling',
                   'gravity': 340000, 'burst_vid': 'hulk.napalm'},
    'rpg': {'vid': 'hulk.rpg', 'damage': 30, 'speed': 39062.5,
            'lifetime': 0.0, 'size': 12288, 'height': 6144, 'accel': 46875,
            'max_speed': 156250, 'burst_vid': 'hulk.napalm'},
    'flame': {'vid': 'hulk.flame', 'damage': 40, 'speed': 48828.125,
              'lifetime': 1.75, 'size': 16384},
}

SCAVENGER_FIRE_COUNT_RANGE = (10, 15)
SCAVENGER_FIRE_SPREAD = 15000 * 1000 / 256

DRONE_IMPACT_PROPS = {
    'vid_': 'hulk.cannonshotx', 'damage_': 30, 'wmp_': 24576,
    'hmp_': 32768, 'lifetime_': 500, 'ownertype_': 2,
}

COMBAT_SOUND_FALLBACKS = {
    'hulk.eloader': 'hulk.scavengerloader',
    'hulk.bdrone': 'hulk.miniseeker',
    'hulk.sdrone': 'hulk.miniseeker',
}

GRENADE_THROW_XVEL = 70000
GRENADE_THROW_YVEL = -120000
GRENADE_APEX_YVEL = -25000
GRENADE_FALL_CAP = 160000
GRENADE_BOUNCE_YVEL = 140000
GRENADE_BOUNCE_GRAVITY = 340000

from .. import game_settings as _supply_settings
SUPPLY_DROP_CHANCE, SUPPLY_DROP_TABLE = _supply_settings.supply_rates({})

DEFAULT_BRIDGE_PORT = 21550
PLAYER_WIDTH = 10 * 1024
PLAYER_HEIGHT = 20 * 1024
PLAYER_INVULN = 1.5

DEATH_SEQUENCE_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics', 'death-presentation.json')
DEATH_SHAKE_MAGNITUDE = 10
DEATH_SHAKE_DURATION = 2000
DEATH_REWARD_DELAY = 4.6

DAMAGE_FX_DEFAULT = 'pskein2fx'
