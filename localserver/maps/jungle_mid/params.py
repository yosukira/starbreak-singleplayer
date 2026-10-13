"""EST mid fauna constants. D/FIT: original Jungle properties and motion.

Shared species use the same native kinematics; encounter gates stay in
entities.json and controller.py. Mid hopper explosions retain 20 damage.
"""
HIVE_BIG_CYCLE_S = 3.35
HIVE_CAP = 20
HIVE_RANGE_MP = 700000
HIVE_SMALL_CYCLE_S = 3.85
HIVE_SMALL_HP = 250
HIVE_TOSS_ANIM_MS = 660
HOPPER_BIG_HP = 90
HOPPER_BIRTH_GRAVITY_DELAY_S = [0.0, 0.04]
HOPPER_BIRTH_VY_MP_S = -135000
HOPPER_BOUNCE_VY_MP_S = -55000
HOPPER_CAP = 48
HOPPER_CHASE_SPEED_MP_S = [30000, 90000]
HOPPER_CHASE_VY_MP_S = [-252000, -102000]
HOPPER_DAMAGE = 15
HOPPER_DECISION_S = [2.5, 3.3]
HOPPER_EXPLODE = 'jungle.hopperx'
HOPPER_EXPLODE_DAMAGE = 0
HOPPER_FALL_CAP_MP_S = 160000
HOPPER_FIRST_VY_MP_S = -135000
HOPPER_GRAVITY_MP_S2 = 340000
HOPPER_GROUND_S = 0.05
HOPPER_HIVE_CHANCE = 0.1
HOPPER_HP = 30
HOPPER_RANGE_MP = 128000
HOPPER_SPEED_MP_S = 59000
PINCER_ANIM_ID = 0
PINCER_ANIM_MS = 2500
PINCER_RESET_ANIM_ID = 1
PINCER_RESET_ANIM_MS = 660
PINCER_CD_S = 8.0
PINCER_PASS_ABOVE_MP = 81920
PINCER_PASS_DELAY_S = 1.0
PINCER_PASS_MIN_S = 4.0
PINCER_PASS_TOUCH_MP = 4096
PINCER_PASS_X_PAD_MP = 8192
PINCER_SOUND = 'jungle.pincerattack'
POD_CREATE_ANIM_MS = 396
PREBOSS_HOPPER = True
PROJECTILE_CAP = None
SHOTS = {'fireshot1': {'vid': 'jungle.fireshot1', 'tier': 0, 'terrain': False, 'lifetime_s': 3.0, 'children': 6, 'props': {'wmp': 16384, 'hmp': 16384, 'z': 2, 'blocktype': 4, 'damage': 40, 'removetype': 3, 'rotate': True, 'ownertype': 2}}, 'fireshot2': {'vid': 'jungle.fireshot2', 'tier': 1, 'terrain': False, 'lifetime_s': 3.0, 'children': 5, 'props': {'wmp': 8192, 'hmp': 8192, 'z': 2, 'blocktype': 4, 'damage': 20, 'removetype': 3, 'rotate': True, 'ownertype': 2}}, 'fireshot3': {'vid': 'jungle.fireshot3', 'tier': 2, 'terrain': False, 'lifetime_s': 3.0, 'children': 0, 'props': {'wmp': 4096, 'hmp': 4096, 'z': 2, 'blocktype': 4, 'damage': 10, 'removetype': 3, 'rotate': True, 'ownertype': 2}}, 'swirlbb': {'vid': 'jungle.swirlbb', 'tier': None, 'terrain': False, 'lifetime_s': 10.0, 'children': 0, 'props': {'wmp': 4096, 'hmp': 4096, 'lifetime': 10000, 'damage': 15, 'removetype': 3, 'ownertype': 2}}}
SKITTER_BOTTOM_LEAD_S = 0.175
SKITTER_BOTTOM_MP = 5120
SKITTER_EDGE_X_MP = 5120
SKITTER_HP = 75
SKITTER_POD_HP = 10
SKITTER_POD_INTERVAL_S = 4.37
SKITTER_POD_LIFETIME_S = 3.0
SKITTER_POD_SHOTS = [1, 1]
SKITTER_RANGE_MP = 819200
SKITTER_SIDE_S = 1.5
SKITTER_SPEED_MP_S = 29300
SPAWN_PROPS = {'jungle.snakehead': {'wmp': 12288, 'hmp': 12288, 'z': 2, 'damage': 25, 'hitpoints': 525, 'removetype': 0, 'rotate': True, 'soundprefix': 'jungle.snake'}, 'jungle.snakebody': {'wmp': 12288, 'hmp': 12288, 'z': 3, 'damage': 10, 'hitboxdamage': 25, 'hitpoints': 1, 'invulnerable': True, 'rotate': True, 'soundprefix': 'jungle.snake'}, 'jungle.snaketail': {'wmp': 12288, 'hmp': 12288, 'z': 11, 'damage': 10, 'hitboxdamage': 25, 'hitpoints': 1, 'invulnerable': True, 'rotate': True, 'soundprefix': 'jungle.snake'}, 'jungle.skitterp': {'wmp': 10240, 'hmp': 10240, 'z': 30, 'lifetime': 3000, 'hitpoints': 10}, 'jungle.hopper': {'wmp': 12288, 'hmp': 12288, 'blocktype': 1, 'hitpoints': 90, 'removetype': 7}, 'jungle.hopperhive': {'wmp': 24576, 'hmp': 16384, 'z': 30, 'hitpoints': 250, 'animprops': {'animid': 1, 'time': 1056}}, 'jungle.hopperx': {'wmp': 24576, 'hmp': 24576, 'lifetime': 333, 'damage': 20}, 'jungle.bossx': {'wmp': 16384, 'hmp': 16384, 'z': 9, 'lifetime': 540}, 'jungle.swirlbb': {'wmp': 4096, 'hmp': 4096, 'lifetime': 10000, 'damage': 15, 'removetype': 3}, 'jungle.fireshot1': {'wmp': 16384, 'hmp': 16384, 'z': 2, 'damage': 40, 'blocktype': 4, 'rotate': True}, 'jungle.fireshot2': {'wmp': 8192, 'hmp': 8192, 'z': 2, 'damage': 20, 'blocktype': 4, 'rotate': True}, 'jungle.fireshot3': {'wmp': 4096, 'hmp': 4096, 'z': 2, 'damage': 10, 'blocktype': 4, 'rotate': True}}
SWIRL_AIM_JITTER_DEG = 12.0
SWIRL_SPEED_MP_S = 39168

HOPPER_EXPLODE_DAMAGE = 20
