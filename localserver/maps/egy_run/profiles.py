'Ordinary EGY policies, deliberately separate from boss-deck AI.'
import copy

SIM = dict(activation_px=1800., acquisition_px=800.,retention_px=1800.,attack_px=480.,
           activation_latched=True, activation_requires_los=False,attack_requires_los=True,
           edge_margin_px=0.,
           loader_throw_cooldown_s=3.0, canister_lifetime_s=2.5,
           charge_settle_s=.4, charge_max_s=1.5,
           wall_retreat_px=280., wall_clear_px=420., wall_offset_px=64.,
           wall_hp=350, wall_lifetime_s=8., wall_per_owner=1, wall_total_cap=12,
           wall_build_cooldown_s=5., wall_build_s=.528,
           mini_activation_px=600., mini_pause_s=.35, mini_dash_s=.5,
           mini_period_s=1.8, mini_fast_px_s=650., mini_slow_px_s=260.,
           mini_trail_s=.8,
           engineer_resummon_s=4.58,

           drone_fire_counts=(2,3),drone_fire_spread_mp_s=30000.,

           drone_wall_hold_s=.10,

           drone_wall_height_policy='preserve-vertical/upward-only')

FAMILIES = {
    'hulk.scavengergunman': 'ranged',
    'hulk.scavengershotgunner': 'ranged',
    'hulk.scavengermissiler': 'ranged',
    'hulk.scavengercommando': 'ranged',
    'hulk.rpgscavenger': 'ranged',
    'hulk.scavengershredder': 'melee',
    'hulk.scavengershredderenergy': 'melee',
    'hulk.scavengerloader': 'loader',
    'hulk.eloader': 'eloader',
    'hulk.scavengerwaller': 'waller',
    'hulk.scavengerwallerenergy': 'waller',
    'hulk.scavengerengineer': 'engineer',
    'hulk.bdrone': 'drone', 'hulk.sdrone': 'drone',
    'hulk.miniseeker': 'mini',
}

EDGE_MODES = {vid:'turn' for vid in FAMILIES}

PURSUIT_JUMPS = {vid:dict(min_height_px=40.,max_dx_px=400.,target_support_tolerance_px=32.,
                         apex_clearance_px=24.,evidence='Boss pursuit gates SIM; jump_speed FIT reused')
                for vid in ('hulk.scavengershredder','hulk.scavengershredderenergy')}

WALL_CLIMBS = {vid:dict(short_obstacle_px=96.,evidence='D ordinary POSE2/49000mp_s/8@5346/10@528; short obstacle<=96px FIT; Boss high-target/cap SIM')
               for vid in PURSUIT_JUMPS}

DRONE_DESCENT_SPEED_MP_S=39000.

MELEE_GROUND_HEADING = {vid:dict(rearm='grounded-unacquired-patrol',
    evidence='D125 phase heading; five compatible firstRUN; entry timing/right mirror/rearm FIT/SIM')
    for vid in PURSUIT_JUMPS}

OBSERVED_CROUCH_ATTACKS = {
    'hulk.scavengergunman':dict(anim=13,time_ms=1320,emit='hulk.sgmbullet',
        points=[dict(type='POINT_EMIT',timeMs=924,x=62,y=121,angle=0)],
        ground_pose=7,weight=1,recovery=.744,
        evidence='D gunman13/POSE7 + catalogue924; selection/recovery SIM'),
    'hulk.scavengercommando':dict(anim=10,time_ms=1320,emit='hulk.sgmbullet',
        points=[dict(type='POINT_EMIT',timeMs=924,x=52,y=153,angle=315)],
        ground_pose=7,weight=1,recovery=.594,
        evidence='D commando10/POSE7/child883/bulletgen + catalogue924; selection/recovery SIM'),
}

EDGE_HOPS={vid:dict(mode=mode,velocity_mp_s=velocity,min_target_below_px=128.,
    approach_px=16.,overhang_px=4.,cooldown_s=4.2,
    post_hop_walk_off=vid in ('hulk.scavengermissiler','hulk.rpgscavenger'),
    post_hop_evidence='D32/34 WALK5 then passivePOSE1 left~20k; sameledge/heading phase FIT/SIM; mirrored right policy SIM',
    evidence='D native ledge hop; ballistic speed FIT; lower-target/choice/latch/cooldown SIM')
    for vid,mode,velocity in (
        ('hulk.scavengermissiler','vertical',185000.),
        ('hulk.rpgscavenger','vertical',185000.),
        ('hulk.scavengershredder','forward',215000.),
        ('hulk.scavengershredderenergy','forward',215000.))}

LOADER_THROWS = [
    dict(anim=7,time_ms=1782,emit='hulk.seekercanister',weight=1,recovery=.4,
         points=[dict(timeMs=1254,x=58,y=309,angle=0)]),
    dict(anim=8,time_ms=1980,emit='hulk.seekercanister',weight=1,recovery=.4,
         points=[dict(timeMs=1188,x=4,y=413,angle=315)]),
]
WALLER_LOBS = [
    dict(anim=8,time_ms=1056,weight=3,recovery=.6,
         points=[dict(timeMs=726,x=30,y=241,angle=290)]),
    dict(anim=9,time_ms=1320,weight=1,recovery=.6,
         points=[dict(timeMs=990,x=30,y=245,angle=290)]),
]

TEMPLATES = {
 'hulk.sgmbullet':dict(wmp=2560,hmp=2560,lifetime=4500,blocktype=4,ownertype=2,damage=35,removetype=3),
 'hulk.sshot':dict(wmp=2048,hmp=2048,lifetime=4000,blocktype=4,ownertype=2,damage=35,removetype=3),
 'hulk.sshotx':dict(wmp=5120,hmp=5120,lifetime=500,ownertype=2,damage=40),
 'hulk.smissile':dict(wmp=4096,hmp=4096,lifetime=40000,blocktype=4,ownertype=2,damage=35,removetype=3),
 'hulk.smissilex':dict(wmp=12800,hmp=12800,lifetime=500,ownertype=2,damage=40),
 'hulk.rpg':dict(wmp=12288,hmp=6144,blocktype=4,ownertype=2,damage=30,armorpierce=100),
 'hulk.ngrenade':dict(wmp=6144,hmp=6144,lifetime=1500,blocktype=4,ownertype=2,damage=30,armorpierce=100),
 'hulk.boundinggrenade':dict(wmp=4096,hmp=4096,lifetime=8000,blocktype=1,ownertype=2,damage=35,removetype=3),
 'hulk.grenadex':dict(wmp=16384,hmp=16384,lifetime=333,blocktype=0,ownertype=2,damage=45,removetype=3),
 'hulk.grenadepulse':dict(wmp=16384,hmp=16384,lifetime=400,ownertype=2,hitboxdamage=35),
 'hulk.napalm':dict(wmp=6144,hmp=6144,lifetime=3000,blocktype=1,ownertype=2,damage=25),
 'hulk.cannonshot':dict(wmp=8192,hmp=8192,lifetime=3000,blocktype=4,ownertype=2,damage=55,removetype=3),

 'hulk.dronebomb':dict(wmp=8192,hmp=8192,lifetime=500,blocktype=4,ownertype=2,damage=55,removetype=3),
 'hulk.cannonshotenergy':dict(wmp=8192,hmp=8192,lifetime=8000,blocktype=1,ownertype=2,damage=35,removetype=3),
 'hulk.cannonshotx':dict(wmp=24576,hmp=32768,lifetime=500,ownertype=2,damage=30),
 'hulk.cannonshotenergyx':dict(wmp=16384,hmp=24576,lifetime=500,ownertype=2,damage=30,removetype=7),
 'hulk.seekercanister':dict(wmp=16384,hmp=24576,blocktype=4,damage=35),
 'hulk.miniseeker':dict(wmp=16384,hmp=16384,blocktype=2,ownertype=2,damage=40,hitpoints=350),
 'hulk.minispawn':dict(wmp=16384,hmp=16384,z=11,lifetime=3000,blocktype=2,ownertype=2,damage=40),
 'hulk.sdrone':dict(wmp=12288,hmp=16384,blocktype=2,ownertype=2,hitpoints=500,armor=50),
 'hulk.bdrone':dict(wmp=12288,hmp=16384,blocktype=2,ownertype=2,hitpoints=500,armor=50),
 'hulk.scavengershredder':dict(wmp=12800,hmp=23040,z=5,blocktype=1,ownertype=2,damage=40,hitpoints=1000,soundprefix='hulk.lizard'),
 'hulk.scavengershredderenergy':dict(wmp=12800,hmp=23040,z=5,blocktype=1,ownertype=2,damage=40,hitpoints=1000,soundprefix='hulk.elizard'),
 'hulk.flame':dict(wmp=16384,hmp=16384,lifetime=1750,blocktype=4,ownertype=2,damage=40,rotate=True),

 'hulk.wallsegment':dict(wmp=8192,hmp=24576,terraintype=1,blocktype=1,
                         ownertype=2,hitpoints=350,damage=0,lifetime=8000),
}
for _vid,_props in TEMPLATES.items():
    _props['vid'] = _vid

def profile(vid):
    result = copy.deepcopy(SIM)
    result.update(family=FAMILIES[vid], edge_mode=EDGE_MODES[vid], evidence='SIM')
    result['pursuit_jump']=copy.deepcopy(PURSUIT_JUMPS.get(vid))
    result['wall_climb']=copy.deepcopy(WALL_CLIMBS.get(vid))
    result['edge_hop']=copy.deepcopy(EDGE_HOPS.get(vid))
    result['ground_pursuit_heading']=copy.deepcopy(MELEE_GROUND_HEADING.get(vid))

    from ...live.constants import SCAVENGER_ATTACK_RANGE
    if vid in SCAVENGER_ATTACK_RANGE:
        result['attack_px']=SCAVENGER_ATTACK_RANGE[vid]/256.
        result['attack_evidence']='Boss USER/FIT reused; ordinary exact gate unknown'
    if result['family']=='melee': result.update(attack_px=90.)
    if result['family']=='engineer': result.update(attack_px=90.)
    if result['family']=='drone':
        result.update(activation_px=1000.,drone_descent_speed_mp_s=DRONE_DESCENT_SPEED_MP_S)

    if result['family'] in ('loader','eloader','waller'): result.update(attack_px=600.)
    if result['family'] in ('loader','eloader'):
        result.update(survivor_vid='hulk.scavengershredder',
            survivor_evidence='D-ordinary-loader' if result['family']=='loader' else 'SIM-eloader; Boss pilot capability reused, ordinary death sample absent')
    return result
