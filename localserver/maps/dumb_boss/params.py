import math

"""Live Dumb Hard parameters measured from the F6 recordings.

Packet properties and motion measurements come from the evidence reports.
Acquisition radii are practice-map calibrations: captures constrain them but
do not expose the server's exact thresholds. Nothing replays a trajectory.
"""

PROJECTILES = {
    'munitionshrapnel': dict(vid='dumb.munitionshrapnel', damage=25, blocktype=4,
                             lifetime_ms=500, speed=25000, wmp=6144, hmp=6144, z=8),
    'bullet': dict(vid='dumb.bullet', damage=30, blocktype=4, lifetime_ms=4000,
                   speed=88000, wmp=2560, hmp=2560, z=9),
    'missile': dict(vid='dumb.missile', damage=30, blocktype=4, lifetime_ms=4400,
                    speed=75000, accel=120000, wmp=6144, hmp=4096, rotate=True,
                    trail='dumb.missilesmoke', z=8),
    'drone': dict(vid='dumb.drone', damage=35, blocktype=2, lifetime_ms=None,
                  speed=34000, wmp=8192, hmp=6144, hp=60),
    'droneshrapnel': dict(vid='dumb.droneshrapnel', damage=10, blocktype=4,
                          lifetime_ms=3000, speed=38000, wmp=3072, hmp=3072, z=8),
    'munitions': dict(vid='dumb.munitions', damage=30, blocktype=2, lifetime_ms=3000,
                      speed=58500, wmp=8192, hmp=8192, z=10),

    'laserbullet': dict(vid='dumb.laserbullet', damage=40, blocktype=4, lifetime_ms=None,
                        speed=122000, wmp=9216, hmp=4096),
    'tankmunitions': dict(vid='dumb.tankmunitions', damage=50, blocktype=3, lifetime_ms=3000,
                          speed=0, wmp=12288, hmp=12288, z=8),
    'rocketbullet': dict(vid='dumb.rocketbullet', damage=50, blocktype=4, lifetime_ms=1500,
                         speed=90000, wmp=10240, hmp=3072, z=9),
    'rocketbulletap': dict(vid='dumb.rocketbulletap', damage=25, blocktype=4, lifetime_ms=None,
                           speed=90000, wmp=10240, hmp=3072, armor_pierce=100, z=9),
    'boundingproj': dict(vid='dumb.boundingproj', damage=25, blocktype=1, lifetime_ms=4500,
                         speed=78000, wmp=4096, hmp=4096),
}

SPECIES = {
    'heavy': dict(shooter=True, walk_speed=21600, aggro_mp=280000,
                  cadence_s=1.97, projectiles=['munitions', 'rocketbullet', 'rocketbulletap'],
                  armored=True, contact=True),
    'trooper': dict(shooter=True, walk_speed=17160, aggro_mp=42000, vertical_mp=12288,
                    cadence_s=1.25, projectiles=['bullet'], level_shot=True),

    'rocketeer': dict(shooter=True, walk_speed=34320, aggro_mp=220000, vertical_mp=42000,
                      cadence_s=0.66, projectiles=['bullet', 'missile']),

    'erocketeer': dict(shooter=True, walk_speed=34320, aggro_mp=999999, vertical_mp=32768,
                       cadence_s=0.66, projectiles=['missile'], flier=True, armored=True),

    'sniper': dict(shooter=True, walk_speed=0, aggro_mp=200000, vertical_mp=42000,
                   cadence_s=2., projectiles=['drone'], max_pets=3),
    'light': dict(shooter=False, walk_speed=70000, aggro_mp=999999, vertical_mp=12288,
                  contact=True, bounds_bounce=True),
    'drone': dict(shooter=False, walk_speed=34000, aggro_mp=999999, vertical_mp=32768),
    'elight': dict(shooter=False, walk_speed=70000, aggro_mp=999999, vertical_mp=33000,
                   contact=True, armored=True),
}

REGIONS = {
    'left_mid':    dict(rect=[16384, 147456, 65536, 8192],  allowed=['trooper', 'rocketeer', 'erocketeer']),
    'left_low':    dict(rect=[40960, 221184, 49152, 8192],  allowed=['trooper']),
    'left_upper':  dict(rect=[8192, 106496, 65536, 8192],   allowed=['trooper', 'sniper']),
    'right_upper': dict(rect=[303104, 106496, 65536, 8192], allowed=['trooper', 'sniper']),
    'right_mid':   dict(rect=[294912, 147456, 65536, 8192], allowed=['trooper', 'rocketeer', 'erocketeer']),
    'right_low':   dict(rect=[294912, 221184, 49152, 8192], allowed=['trooper']),
    'ground':      dict(rect=[16384, 262144, 319488, 16384], allowed=['trooper', 'rocketeer', 'erocketeer', 'light', 'elight']),
    'left_high':   dict(rect=[73728, 65536, 24576, 8192],   allowed=['sniper']),
    'right_high':  dict(rect=[286720, 65536, 24576, 8192],  allowed=['sniper']),
    'center_heavy': dict(rect=[155648, 262144, 73728, 16384], allowed=['heavy']),
}

STAGE_GATES = [15000, 15000, 20000, 20000, 20000]
ATTACK_WINDOW_S = [30., 30., 35., 35., 40.]
WAVE_WINDOW_S = [15.74, 18.07, 11.25, 13.35]
WAVE_SPAWNS = [33, 35, 24, 38]
WAVE_SPAWN_RATE = 2.15
DESCEND_S = 6.553
ASCEND_S = 6.553
RECALL_S = 1.2
ARENA_CENTER = (143360, 106496)
ARENA_CENTER_HEAVY = (335872, 187904)
HEAVY_WALK_SPEED = 21600

ELEVATOR_CEILING_MP = 0
GRID_DWELL_S = 1.1
ARENA_TOP = (139264, -149504)
GRID_SPEED = 39000
BOSS_ARMOR = 75
BOSS_LASER_INTERVAL_S = 0.5
BOSS_DROP_INTERVAL_S = 0.9
BOSS_LOB_INTERVAL_S = 1.2

TELEPORT_FX = dict(vid='dumb.teleportfxenemy', wmp=17920, hmp=38400, lifetime_ms=1452,
                   delay_s=0.77)

def region_choices(behaviour):
    """Weighted region table: (name, rect, species weights, weight)."""
    rows = []
    wave = (behaviour or {}).get('wave') or {}
    spawns = wave.get('spawns_per_region') or {}
    for name, spec in REGIONS.items():
        weight = float(spawns.get(name, 1.0)) or 1.0
        rows.append(dict(name=name, rect=list(spec['rect']),
                         allowed=list(spec['allowed']), weight=weight))
    return rows

def species_weights(behaviour, region, allowed):
    spec = ((behaviour or {}).get('regions') or {}).get(region) or {}
    weights = spec.get('weights') or {}
    rows = [(name, float(weights.get(name, 1.0)) or 1.0) for name in allowed]
    total = sum(w for _n, w in rows) or 1.0
    return [(name, w / total) for name, w in rows]

BOSS_VID_BACK = 'dumb.bossshieldback'
BOSS_PROP11_HIDDEN = 61
BOSS_PROP11_ACTIVE = 9

LASER_TICK_S = 0.15
LASER_CHARGE_S = 1.32
LASER_SHOTS_ARMORED = 13
LASER_SHOTS_EXPOSED = 13
DROP_CHARGE_S = 0.45
DROP_TICK_S = 0.26
DROP_SHOTS = 7
LOB_TICK_S = 0.46
LOB_SHOTS = 4

LOB_VX_UNITS = (15102.0, 29985.0)
LOB_VY_UNITS = (12500.0, 25000.0)
LOB_ACCEL_UNITS = (10.0, 50.0)
LOB_VELOCITY_MP = 3.90625
LOB_ACCEL_MP = 3906.25

DROP_MOUTHS_MP = (-7424.0, 7424.0)
BOSS_COOLDOWN_S = (2.4, 3.0)

LASER_SLOTS_ARMORED = (1, 2, 3)
LASER_SLOTS_EXPOSED = (6, 7, 8)
LASER_CHARGE_SLOTS_ARMORED = (4, 5, 6)
LASER_CHARGE_SLOTS_EXPOSED = (4, 5)

LASER_VOLLEYS_ARMORED = ((1, 4), (2, 6), (3, 5))
LASER_VOLLEYS_EXPOSED = ((6, 3), (7, 4), (8, 5))

CHANNEL_INTERVAL_S = {'laser': 4.3, 'drop': 8.0, 'lob': 6.1}
CHANNEL_FIRST_S = {'laser': 1.2, 'drop': 2.0, 'lob': 3.0}
HEAVY_BEAM_ANIM = (16, 1782)
TROOPER_SHOT_ANIM = (2, 528)

ROCKETEER_SHOT_ANIM = (1, 1056)
ROCKETEER_MISSILE_ANIM = (3, 660)
EROCKETEER_SHOT_ANIM = (5, 1056)
SNIPER_SHOT_ANIM = (2, 1056)
HEAVY_STEP_ANIM = (0, 1848)
HEAVY_STEP_ANIM_B = (1, 1848)

HEAVY_POINT_BLANK_MP = 32768
HEAVY_LOB_MP = 98304
HEAVY_FAST_MP = 196608
HEAVY_LEVEL_MP = 12288
MUNITIONS_SPLIT = ('dumb.munitionshrapnel', 8)

HEAVY_MUZZLE_MP = {
    'munitions': (0, 8192),
    'rocketbullet': (24064, 54272),
    'rocketbulletap': (17920, 55040),
}
HEAVY_MUZZLE_FACING = ('rocketbullet', 'rocketbulletap')

def heavy_band(distance_mp):
    'Local battle simulator runtime.'
    if distance_mp < HEAVY_POINT_BLANK_MP:
        return 'slam'
    if distance_mp < HEAVY_LOB_MP:
        return 'munitions'
    if distance_mp < HEAVY_FAST_MP:
        return 'rocketbullet'
    return 'rocketbulletap'

PROJECTILE_POSE = {'bullet': 2, 'missile': 2, 'laserbullet': 2, 'tankmunitions': 2,
                   'munitions': 3, 'munitionshrapnel': 3, 'droneshrapnel': 2,
                   'drone': 2,'boundingproj':3}
POSE_AIR, POSE_FLY_HORIZ, POSE_FLY_VERT = 1, 2, 3
POSE_STAND, POSE_WALK = 4, 5

MISSILE_INITIAL_SPEED = 20000
MISSILE_ACCEL = 30000
MISSILE_MAX_SPEED = 140000

SPECIES['heavy']['walk_speed'] = 19500
SPECIES['trooper']['walk_speed'] = 39000
SPECIES['rocketeer']['walk_speed'] = 39000
SPECIES['erocketeer']['walk_speed'] = 78000
SPECIES['light']['walk_speed'] = 127000
SPECIES['elight']['walk_speed'] = 157000
SPECIES['drone']['walk_speed'] = 39000

SPECIES['sniper']['aggro_mp']=300000
SPECIES['rocketeer']['aggro_mp']=320000
SPECIES['erocketeer']['aggro_mp']=420000
MISSILE_INITIAL_SPEED = 20000
MISSILE_ACCEL = 80000
MISSILE_MAX_SPEED = 160000

MISSILE_FLIGHT = {
    'rocketeer':dict(initial_speed=15625.,acceleration=39062.5,turn_degrees=24.),
    'erocketeer':dict(initial_speed=20000.,acceleration=85000.,turn_degrees=8.),
}

LASER_SPED_MP_S = 121862.7
MUNITIONS_DROP_SPED = 24556.
NPC_GRAVITY = 340000
HEAVY_SHELL_INTERVAL_S = 2.07

HEAVY_ANIM_POINTS = {
    'rocketbullet': (154, -72),
    'rocketbulletap': (130, -69),
}

DRONE_FAN_COUNT = 10
DRONE_FAN_DEGREES = 36.0

MISSILE_VOLLEY_SHOTS = 8
MISSILE_VOLLEY_FIRST_GAP_S = 0.87
MISSILE_VOLLEY_GAP_STEP_S = 0.10
MISSILE_VOLLEY_MIN_GAP_S = 0.25
MISSILE_VOLLEY_COOLDOWN_S = (3.5, 6.0)
HEAVY_MAIN_ACTIONS = {
    'rocketbullet': dict(anim=9, time_ms=4950, emit_s=2.046,
                        initial_speed=3906.25, accel=195312.5, max_speed=195312.5),
    'rocketbulletap': dict(anim=10, time_ms=5544, emit_s=3.036,
                          initial_speed=2929.6875, accel=39062.5, max_speed=78125),
    'beam': dict(anim=16, time_ms=1782, emit_s=None),
}
PROJECTILES['munitions'].update(wmp=4096, hmp=4096)

PROJECTILES['munitionshrapnel'].update(wmp=1280, hmp=1280, speed=39000)
PROJECTILES['droneshrapnel'].update(wmp=5120, hmp=5120, speed=39000)

PROJECTILES['drone'].update(wmp=16384, hmp=16384, hp=300, lifetime_ms=None)
PROJECTILES['boundingproj'].update(wmp=3840, hmp=3840)

BOSS_GRID_X = (8192, 143360, 278528)
BOSS_GRID_Y = (32768, 106496, 180224)
GRID_CRUISE_SPEED = (39062.5,39062.5,97656.25,97656.25,136718.75)
GRID_DWELL_BY_STAGE = (1.1,1.1,1.8,1.8,1.32)
TRANSIT_BY_STAGE = (6.553,6.553,2.621,2.621,2.621)
ARENA_RECALL = (139264,106496)

WAVE_INTERVALS_S = (1.,1.,1.,.7)
WAVE_FIRST_FLASH_S = .25
WAVE_BATCH_SIZES = (1,2,3)
WAVE_BATCH_WEIGHTS = (.25,.5,.25)
WAVE_HEAVY_LIMIT = 3

MUNITIONS_REBOUND_SPEED = (80000.,140000.)
PROJECTILE_SOUNDS = {'munitions':'dumb.munitionsgen',
                    'rocketbullet':'dumb.rocketgen',
                    'bullet':'dumb.bulletgen','missile':'dumb.missilegen',
                    'boundingproj':'dumb.boundinggen'}
ROCKETEER_BACKHOP_VX = 78000.
ROCKETEER_BACKHOP_VY = -135000.
ROCKETEER_FLIGHT_HEIGHT = 32768.
ROCKETEER_LAND_AFTER_LAST_ANIM_S = .56
ROCKETEER_GROUND_REST_S = (1.5,2.)
LIGHT_BACKHOP_VX = 126000.
LIGHT_BACKHOP_VY = -144000.
LIGHT_SHOT_GAPS_S = (0.,.36,.76)

BOSS_EYE_FRAMES = {'E': 'dumb.boss6', 'SE': 'dumb.boss3', 'S': 'dumb.boss2',
                   'SW': 'dumb.boss1', 'W': 'dumb.boss4', 'NW': 'dumb.boss7',
                   'N': 'dumb.boss8', 'NE': 'dumb.boss9'}
BOSS_EYE_SECTORS = ((15.0, 'E'), (60.0, 'SE'), (120.0, 'S'), (165.0, 'SW'),
                    (195.0, 'W'), (240.0, 'NW'), (300.0, 'N'), (345.0, 'NE'))
BOSS_EYE_LEAD_S = 0.71
BOSS_EYE_HOLD_S = .08

def boss_eye_frame(dx, dy):
    'Direction sprite for a grid move (runtime sector table).'
    if dx == 0 and dy == 0:
        return None
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    for limit, name in BOSS_EYE_SECTORS:
        if ang < limit:
            return BOSS_EYE_FRAMES[name]
    return BOSS_EYE_FRAMES['E']

from ..dumb_common import params as _npc_params
DRONE_FAN_COUNT = _npc_params.DRONE_FAN_COUNT
DRONE_FAN_DEGREES = _npc_params.DRONE_FAN_DEGREES
HEAVY_ANIM_POINTS = _npc_params.HEAVY_ANIM_POINTS
HEAVY_MAIN_ACTIONS = _npc_params.HEAVY_MAIN_ACTIONS
HEAVY_MUZZLE_MP = _npc_params.HEAVY_MUZZLE_MP
HEAVY_SHELL_INTERVAL_S = _npc_params.HEAVY_SHELL_INTERVAL_S
LIGHT_BACKHOP_VX = _npc_params.LIGHT_BACKHOP_VX
LIGHT_BACKHOP_VY = _npc_params.LIGHT_BACKHOP_VY
LIGHT_SHOT_GAPS_S = _npc_params.LIGHT_SHOT_GAPS_S
MISSILE_FLIGHT = _npc_params.MISSILE_FLIGHT
MISSILE_VOLLEY_FIRST_GAP_S = _npc_params.MISSILE_VOLLEY_FIRST_GAP_S
MISSILE_VOLLEY_GAP_STEP_S = _npc_params.MISSILE_VOLLEY_GAP_STEP_S
MISSILE_VOLLEY_MIN_GAP_S = _npc_params.MISSILE_VOLLEY_MIN_GAP_S
MISSILE_VOLLEY_SHOTS = _npc_params.MISSILE_VOLLEY_SHOTS
MUNITIONS_REBOUND_SPEED = _npc_params.MUNITIONS_REBOUND_SPEED
NPC_GRAVITY = _npc_params.NPC_GRAVITY
PROJECTILES = _npc_params.PROJECTILES
PROJECTILE_POSE = _npc_params.PROJECTILE_POSE
PROJECTILE_SOUNDS = _npc_params.PROJECTILE_SOUNDS
ROCKETEER_BACKHOP_VX = _npc_params.ROCKETEER_BACKHOP_VX
ROCKETEER_BACKHOP_VY = _npc_params.ROCKETEER_BACKHOP_VY
ROCKETEER_FLIGHT_HEIGHT = _npc_params.ROCKETEER_FLIGHT_HEIGHT
ROCKETEER_GROUND_REST_S = _npc_params.ROCKETEER_GROUND_REST_S
ROCKETEER_LAND_AFTER_LAST_ANIM_S = _npc_params.ROCKETEER_LAND_AFTER_LAST_ANIM_S
SPECIES = _npc_params.SPECIES
