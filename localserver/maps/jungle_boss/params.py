'Local battle simulator runtime.'

BOSS_HEAD = 66
PART_SPACING = 84830
PART_TICK_S = 0.09
BOSS_HEAD_WMP = 172032
BOSS_TELL_OFFSET_MP = 106496

PHASE_TIME_S = [35.0, 30.0, 40.0, 50.0]

PHASE_GATES = [4130, 8260, 8260, None]

PHASE_POOLS = [4130, 8260, 8260, 20650]

PHASE_SPEED_MP_S = [117504, 146944, 176384, 234496]

PHASE_DWELL_S = [11.3, 9.0, 7.6, 0.0]
SWEEP_X_MP = [-623876, 1036288]
LANE_Y_MP = [115968, 602880]
FIRST_LANE_Y_MP = 373248
MIN_LANE_GAP_MP = 208896

LANE_RANGE_MP = [113920, 624128]
LANE_PLAYER_GROUP_MP = 16384
LANE_PLAYER_CHANCE = 0.5
LANE_SAMPLE = '50% player height / largest player group; 50% uniform legal lanes; hard 816 px gap from sweep 3 [USER]'

SOLO_HP = 41300

COOP_HP_MULTIPLIER = 1

BOSS_BODY_ARMOR = 75
BOSS_DEATH_ANIM = {'jungle.bosshead': 2, 'jungle.bosssegment': 2, 'jungle.bosstail': 1}

BOSS_DEATH_S = 9.4
BOSSX_COUNT = 1528
BOSSX_LIFETIME_MS = 540
BOSSX_DX_PX = [-3966, -3107, -1453, -235, 379, 1183, 2000]
BOSSX_DY_PX = [-75, 50, 205, 388, 570, 1301, 1626]

SNAKE_WAVE_RATE = [0.18, 0.18, 0.30, 0.40]
SNAKE_WAVE_SIZE = [1, 1, 2, 2]
SNAKE_RATE = [0.18, 0.18, 0.60, 0.80]
SNAKE_OPENING_DELAY_S = 25.0
SNAKE_ALIVE_CAP = [17, 17, 17, 17]
SNAKE_HEAD_HP = 525
SNAKE_HEAD_DAMAGE = 25
SNAKE_BODY_HP = 1
SNAKE_LINK_MP = 12288
SNAKE_BODY_COUNT = 8
SNAKE_SPEED_MP_S = 74275
SNAKE_LEG_S = [1.0, 2.0]
SNAKE_POSE_S = 1.10
SNAKE_SPAWN_MARGIN_MP = 140000
SNAKE_SPAWN_MARGIN_RANGE_MP = [131072,147456]
SNAKE_BOTTOM_MARGIN_MP = [8192,16384]

SKITTER_RATE = [0.137, 0.107, 0.076, 0.330]
SKITTER_HP = 75
SKITTER_SPEED_MP_S = 29300
SKITTER_SIDE_S = 1.5
SKITTER_BOTTOM_LEAD_S = 0.175
SKITTER_POD_INTERVAL_S = 4.37
SKITTER_RANGE_MP = 819200
SKITTER_EDGE_X_MP = 5120
SKITTER_TOP_MP = 12288
SKITTER_BOTTOM_MP = 5120
SKITTER_POD_HP = 10
SKITTER_POD_LIFETIME_S = 3.0
SKITTER_POD_SHOTS = [1, 1]
SKITTER_POD_WMP = 10240

FIRESHOT_RATE = [0.278, 0.544, 0.715, 1.389]
FIRESHOT_TIERS = ['jungle.fireshot1', 'jungle.fireshot2', 'jungle.fireshot3']
FIRESHOT_DAMAGE = [40, 20, 10]
FIRESHOT_WMP = [16384, 8192, 4096]
FIRESHOT_SPEED_MP_S = [62500, 31250, 15625]
FIRESHOT_SPLIT_S = 3.0
FIRESHOT_CHILDREN = [6, 5]
FIRESHOT_SPACING_DEG = [60.0, 72.0]
FIRESHOT_AIM_JITTER_DEG = 0.0
FIRESHOT_MUZZLE_MP = [81920, 0]
FIRESHOT_SPEEDUP = 0.0

SWIRL_VID = 'jungle.swirlbb'
SWIRL_DAMAGE = 15
SWIRL_SPEED_MP_S = 39168
SWIRL_WMP = 4096
SWIRL_LIFETIME_MS = 10000
SWIRL_AIM_JITTER_DEG = 12.0

HOPPER_HP = 30
HOPPER_DAMAGE = 15
HOPPER_BIG_HP = 90
HOPPER_SPEED_MP_S = 59000
HOPPER_CHASE_SPEED_MP_S = [30000, 90000]
HOPPER_RANGE_MP = 128000
HOPPER_DECISION_S = [2.5, 3.3]
HOPPER_BIRTH_VY_MP_S = -135000
HOPPER_BIRTH_GRAVITY_DELAY_S = [0., .04]
HOPPER_FIRST_VY_MP_S = -135000
HOPPER_BOUNCE_VY_MP_S = -55000
HOPPER_CHASE_VY_MP_S = [-252000, -102000]
HOPPER_LARGE_HOP_CHANCE = .52
HOPPER_GRAVITY_MP_S2 = 340000
HOPPER_FALL_CAP_MP_S = 160000
HOPPER_GROUND_S = 0.05
HOPPER_HIVE_CHANCE = 0.10
HOPPER_EXPLODE = 'jungle.hopperx'

HOPPER_EXPLODE_DAMAGE = 0
HIVE_HP = 1125
HIVE_SMALL_HP = 250
HIVE_BIG_CYCLE_S = 3.35
HIVE_SMALL_CYCLE_S = 3.85
HIVE_TOSS_ANIM_MS = 660
HIVE_RANGE_MP = 700000

PINCER_COUNT_RANGE = [2, 10]
PINCER_SPRITE_W_MP = 34560
PINCER_SPRITE_H_MP = 34560
PINCER_LEFT_FACING = 0
PINCER_RIGHT_FACING = 180

PINCER_CD_S = 8.0
PINCER_PASS_MIN_S = 4.0
PINCER_PASS_DELAY_S = 1.0
PINCER_PASS_X_PAD_MP = 8192
PINCER_PASS_ABOVE_MP = 81920
PINCER_PASS_TOUCH_MP = 4096
PINCER_ANIM_MS = 2500
PINCER_ANIM_ID = 0
PINCER_HIT_WINDOW_MS = [396, 2244]
PINCER_DAMAGE = 25

PINCER_Z = 40
PINCER_SOUND = 'jungle.pincerattack'

PREBOSS_HOPPER = True

HIVE_PLATFORM_RANGE = [1, 4]
HIVE_COUNT_PER_PLATFORM = [1,3]

HIVE_OFFSET_MP = 20480
HIVE_FIRST_RELX_MP = 8192
HIVE_STEP_MP = 40960

SKITTER_INIT_RANGE = [4, 16]
SNAKE_INIT_RANGE = [1, 4]
SNAKE_SLOT_MP = [
    (110592, 360448), (-118784, 401408), (372736, 405504),
    (-245760, 585728), (532480, 614400),
]

SKITTER_PASS_CHANCE = [0.21, 0.26, 0.18, 0.22]

SKITTER_PASS_Y_INSET_MP = 43008

PHASE_SOUND = 'jungle.bossscream'
PHASE_SHAKE = [6, 1500]
DEATH_SHAKE = [8, -1]

FIRESHOT_RUN_GAP_S = 0.36
FIRESHOT_RUN_LEN = [1, 3, 5, 7]
FIRESHOT_EMPTY_CHANCE = [0., 0., 0., 0.]
FIRESHOT_COOLDOWN_S = 3.6
FIRESHOT_AIM_CHANCE = [1., .75, .59, 0.]
FIRESHOT_BURST_SIZE = [(1,1),(4,8),(5,8),(8,12)]

SNAKE_HEAD_ANIM_MS = 924
SNAKE_POSE_MS = 924
SKITTER_DESTROY_ANIM_MS = 594
POD_CREATE_ANIM_MS = 396
HOPPER_DESTROY_ANIM_MS = 594
FIRESHOT_DESTROY_MS = [528, 462, 396]

def phase_index(phase):
    return max(0, min(3, int(phase) - 1))

def phase_speed(phase):
    return PHASE_SPEED_MP_S[phase_index(phase)]

def phase_dwell(phase):
    return PHASE_DWELL_S[phase_index(phase)]

SHOTS = {
    'fireshot1': dict(vid='jungle.fireshot1', tier=0, terrain=False, lifetime_s=3.0, children=6,
                      props=dict(wmp=16384, hmp=16384, z=2, blocktype=4, damage=40,
                                 removetype=3, rotate=True, ownertype=2)),
    'fireshot2': dict(vid='jungle.fireshot2', tier=1, terrain=False, lifetime_s=3.0, children=5,
                      props=dict(wmp=8192, hmp=8192, z=2, blocktype=4, damage=20,
                                 removetype=3, rotate=True, ownertype=2)),
    'fireshot3': dict(vid='jungle.fireshot3', tier=2, terrain=False, lifetime_s=3.0, children=0,
                      props=dict(wmp=4096, hmp=4096, z=2, blocktype=4, damage=10,
                                 removetype=3, rotate=True, ownertype=2)),
    'swirlbb': dict(vid='jungle.swirlbb', tier=None, terrain=False, lifetime_s=10.0, children=0,
                    props=dict(wmp=4096, hmp=4096, lifetime=10000, damage=15, removetype=3, ownertype=2)),
}

SPAWN_PROPS = {
    'jungle.snakehead': dict(wmp=12288, hmp=12288, z=2, damage=25, hitpoints=525,
                             removetype=0, rotate=True, soundprefix='jungle.snake'),
    'jungle.snakebody': dict(wmp=12288, hmp=12288, z=3, damage=10, hitboxdamage=25,
                             hitpoints=1, invulnerable=True, rotate=True,
                             soundprefix='jungle.snake'),
    'jungle.snaketail': dict(wmp=12288, hmp=12288, z=11, damage=10, hitboxdamage=25,
                             hitpoints=1, invulnerable=True, rotate=True,
                             soundprefix='jungle.snake'),
    'jungle.skitterp': dict(wmp=10240, hmp=10240, z=30, lifetime=3000, hitpoints=10),
    'jungle.hopper': dict(wmp=12288, hmp=12288, blocktype=1, hitpoints=90, removetype=7),
    'jungle.hopperhive': dict(wmp=24576, hmp=16384, z=30, hitpoints=250,
                              animprops=dict(animid=1, time=1056)),
    'jungle.hopperx': dict(wmp=24576, hmp=24576, lifetime=333, damage=20),
    'jungle.bossx': dict(wmp=16384, hmp=16384, z=9, lifetime=540),
    'jungle.swirlbb': dict(wmp=4096, hmp=4096, lifetime=10000, damage=15,
                           removetype=3),
    'jungle.fireshot1': dict(wmp=16384, hmp=16384, z=2, damage=40, blocktype=4, rotate=True),
    'jungle.fireshot2': dict(wmp=8192, hmp=8192, z=2, damage=20, blocktype=4, rotate=True),
    'jungle.fireshot3': dict(wmp=4096, hmp=4096, z=2, damage=10, blocktype=4, rotate=True),
}

HOPPER_CAP = 48
HIVE_CAP = 20
NPC_CAP = 96

PROJECTILE_CAP = None
