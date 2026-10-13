"""D native fields; FIT measured actions; SIM unobserved decision gates."""
COMMON={'trooper','sniper','rocketeer','erocketeer','heavy','light','elight','drone'}
SPECIAL={'shieldturret','towersegment','robosweeper','tank'}
USER=dict(spawner_capacity=2)
SIM=dict(activation_px=800.,shield_cycle_s=2.75,tower_cycle_s=3.5,
    sweeper_shot_s=2.,sweeper_shot_speed=70000.,
    turret_cooldown_s=3.,spawner_retry_s=.5,spawner_species=('heavy','trooper','rocketeer','light'),
    laser_cycle_s=5.2)

SWEEPER_SPEEDS_MP_S={1:68000.,-1:69000.}

SWEEPER_SHOT_SPEED_MP_S=78000.

LIGHT_SHOT_GAPS_S=(0.,.36,.72)
SHIELD=dict(windup_s=.562,shots=8,gap_s=.1,speed=49190.,amplitude=7669.55,period_s=.5,
    wall_after_volleys=2,wall_lifetime_ms=3000)

TOWER_FLIGHTS=((58000.,-664.37,324222.58),(30000.,-1018.58,141520.04),
               (46000.,-497.23,164159.91),(56000.,-710.82,157326.49),(34000.,-508.51,198810.20))

LASER=dict(windup_s=1.39,active_s=1.905,lifetime_ms=3630,damage=40,telegraph_delay_ms=50)
