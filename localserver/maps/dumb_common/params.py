"""Calibrated shared NPC/shot rules; no boss-stage or wave scheduling."""

DRONE_FAN_COUNT = 10

DRONE_FAN_DEGREES = 36.0

HEAVY_ANIM_POINTS = {'rocketbullet': (154, -72), 'rocketbulletap': (130, -69)}

HEAVY_MAIN_ACTIONS = {'rocketbullet': {'anim': 9,
                  'time_ms': 4950,
                  'emit_s': 2.046,
                  'initial_speed': 3906.25,
                  'accel': 195312.5,
                  'max_speed': 195312.5},
 'rocketbulletap': {'anim': 10,
                    'time_ms': 5544,
                    'emit_s': 3.036,
                    'initial_speed': 2929.6875,
                    'accel': 39062.5,
                    'max_speed': 78125},
 'beam': {'anim': 16, 'time_ms': 1782, 'emit_s': None}}

HEAVY_MUZZLE_MP = {'munitions': (0, 8192), 'rocketbullet': (24064, 54272), 'rocketbulletap': (17920, 55040)}

HEAVY_SHELL_INTERVAL_S = 2.07

LIGHT_BACKHOP_VX = 126000.0

LIGHT_BACKHOP_VY = -144000.0

LIGHT_SHOT_GAPS_S = (0.0, 0.36, 0.76)

MISSILE_FLIGHT = {'rocketeer': {'initial_speed': 15625.0, 'acceleration': 39062.5, 'turn_degrees': 24.0},
 'erocketeer': {'initial_speed': 20000.0, 'acceleration': 85000.0, 'turn_degrees': 8.0}}

MISSILE_VOLLEY_FIRST_GAP_S = 0.87

MISSILE_VOLLEY_GAP_STEP_S = 0.1

MISSILE_VOLLEY_MIN_GAP_S = 0.25

MISSILE_VOLLEY_SHOTS = 8

MUNITIONS_REBOUND_SPEED = (80000.0, 140000.0)

NPC_GRAVITY = 340000

PROJECTILES = {'munitionshrapnel': {'vid': 'dumb.munitionshrapnel',
                      'damage': 25,
                      'blocktype': 4,
                      'lifetime_ms': 500,
                      'speed': 39000,
                      'wmp': 1280,
                      'hmp': 1280,
                      'z': 8},
 'bullet': {'vid': 'dumb.bullet',
            'damage': 30,
            'blocktype': 4,
            'lifetime_ms': 4000,
            'speed': 88000,
            'wmp': 2560,
            'hmp': 2560,
            'z': 9},
 'missile': {'vid': 'dumb.missile',
             'damage': 30,
             'blocktype': 4,
             'lifetime_ms': 4400,
             'speed': 75000,
             'accel': 120000,
             'wmp': 6144,
             'hmp': 4096,
             'rotate': True,
             'trail': 'dumb.missilesmoke',
             'z': 8},
 'drone': {'vid': 'dumb.drone',
           'damage': 35,
           'blocktype': 2,
           'lifetime_ms': None,
           'speed': 34000,
           'wmp': 16384,
           'hmp': 16384,
           'hp': 300},
 'droneshrapnel': {'vid': 'dumb.droneshrapnel',
                   'damage': 10,
                   'blocktype': 4,
                   'lifetime_ms': 3000,
                   'speed': 39000,
                   'wmp': 5120,
                   'hmp': 5120,
                   'z': 8},
 'munitions': {'vid': 'dumb.munitions',
               'damage': 30,
               'blocktype': 2,
               'lifetime_ms': 3000,
               'speed': 58500,
               'wmp': 4096,
               'hmp': 4096,
               'z': 10},
 'laserbullet': {'vid': 'dumb.laserbullet',
                 'damage': 40,
                 'blocktype': 4,
                 'lifetime_ms': None,
                 'speed': 122000,
                 'wmp': 9216,
                 'hmp': 4096},
 'tankmunitions': {'vid': 'dumb.tankmunitions',
                   'damage': 50,
                   'blocktype': 3,
                   'lifetime_ms': 3000,
                   'speed': 0,
                   'wmp': 12288,
                   'hmp': 12288,
                   'z': 8},
 'rocketbullet': {'vid': 'dumb.rocketbullet',
                  'damage': 50,
                  'blocktype': 4,
                  'lifetime_ms': 1500,
                  'speed': 90000,
                  'wmp': 10240,
                  'hmp': 3072,
                  'z': 9},
 'rocketbulletap': {'vid': 'dumb.rocketbulletap',
                    'damage': 25,
                    'blocktype': 4,
                    'lifetime_ms': None,
                    'speed': 90000,
                    'wmp': 10240,
                    'hmp': 3072,
                    'armor_pierce': 100,
                    'z': 9},
 'boundingproj': {'vid': 'dumb.boundingproj',
                  'damage': 25,
                  'blocktype': 1,
                  'lifetime_ms': 4500,
                  'speed': 78000,
                  'wmp': 3840,
                  'hmp': 3840}}

PROJECTILE_POSE = {'bullet': 2,
 'missile': 2,
 'laserbullet': 2,
 'tankmunitions': 2,
 'munitions': 3,
 'munitionshrapnel': 3,
 'droneshrapnel': 2,
 'drone': 2,
 'boundingproj': 3}

PROJECTILE_SOUNDS = {'munitions': 'dumb.munitionsgen',
 'rocketbullet': 'dumb.rocketgen',
 'bullet': 'dumb.bulletgen',
 'missile': 'dumb.missilegen',
 'boundingproj': 'dumb.boundinggen'}

ROCKETEER_BACKHOP_VX = 78000.0

ROCKETEER_BACKHOP_VY = -135000.0

ROCKETEER_FLIGHT_HEIGHT = 35200.0
ROCKETEER_TAKEOFF_S = .8
ROCKETEER_LIFT_SPEED = 20000.0
ROCKETEER_LIFT_ACCEL = 60000.0

ROCKETEER_GROUND_REST_S = (1.5, 2.0)

ROCKETEER_LAND_AFTER_LAST_ANIM_S = 0.56

SPECIES = {'heavy': {'shooter': True,
           'walk_speed': 19500,
           'acquire_mp': 128000,
           'aggro_mp': 208000,
           'cadence_s': 1.97,
           'projectiles': ['munitions', 'rocketbullet', 'rocketbulletap'],
           'armored': True,
           'contact': True},
 'trooper': {'shooter': True,
             'walk_speed': 39000,
             'aggro_mp': 42000,
             'vertical_mp': 12288,
             'cadence_s': 1.25,
             'projectiles': ['bullet'],
             'level_shot': True},
 'rocketeer': {'shooter': True,
               'walk_speed': 39000,
               'acquire_mp': 160000,
               'aggro_mp': 280000,
               'vertical_mp': 42000,
               'cadence_s': 0.66,
               'projectiles': ['bullet', 'missile']},
 'erocketeer': {'shooter': True,
                'walk_speed': 78000,
                'acquire_mp': 160000,
                'aggro_mp': 280000,
                'vertical_mp': 32768,
                'cadence_s': 0.66,
                'projectiles': ['missile'],
                'flier': True,
                'armored': True},
 'sniper': {'shooter': True,
            'walk_speed': 0,
            'acquire_mp': 160000,
            'aggro_mp': 300000,
            'vertical_mp': 42000,
            'cadence_s': 2.0,
            'projectiles': ['drone'],
            'max_pets': 3},
 'light': {'shooter': False,
           'walk_speed': 127000,
           'aggro_mp': 999999,
           'vertical_mp': 12288,
           'contact': True,
           'bounds_bounce': True},
 'drone': {'shooter': False, 'walk_speed': 39000, 'aggro_mp': 999999, 'vertical_mp': 32768},
 'elight': {'shooter': False,
            'walk_speed': 157000,
            'aggro_mp': 999999,
            'vertical_mp': 33000,
            'contact': True,
            'armored': True}}
