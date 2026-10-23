"""SC decisions and lifecycle against small live geometry, without a game."""
import copy
import math
import random
import unittest
from types import SimpleNamespace

from localserver.live.wire import DeltaBatch
from localserver.maps.sc_run.enemies import ScEnemies, VITALS
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.maps.sc_run.physics import clear, geometry, ground, sinusoid


SIZES = {'sc.skeleton': (64, 128), 'sc.golem': (160, 256), 'sc.egolem': (160, 256),
         'sc.tank': (128, 128), 'sc.flytank': (220, 150),
         'sc.titan': (224, 192), 'sc.monk': (64, 96),
         'sc.hunter': (96, 64), 'sc.surn': (96, 96),
         'sc.golemb': (48, 48), 'sc.golemw': (64, 64),
         'sc.sliverx': (64, 64), 'sc.monkb': (24, 24), 'sc.wraith': (64, 64)}
LIFETIMES = {'sc.golemb': 5000, 'sc.golemw': 2000, 'sc.burst': 4000,
             'sc.sliver': 10000, 'sc.sliverx': 1000, 'sc.shard': 6000,
             'sc.aim': 2000, 'sc.aimend': 2000, 'sc.wraith': 30000}


def props(vid):
    w, h = SIZES.get(vid, (64, 64) if vid in VITALS else (24, 24))
    p = dict(vid=vid, wmp=w*256, hmp=h*256)
    if vid in VITALS:
        p.update(hitpoints=VITALS[vid], damage=35, ownertype=2, blocktype=1)
    if vid in LIFETIMES:
        p['lifetime'] = LIFETIMES[vid]
    if vid in ('sc.golem', 'sc.egolem', 'sc.golemb'):
        p['terraintype'] = 1
    if vid == 'sc.wraith':
        p.update(hitpoints=75, damage=20, ownertype=2, rotate=True, noleech=True)
    return p


class Host:
    def __init__(self, metadata=None):
        self.actors = {}
        self.batch = DeltaBatch()
        self.rng = random.Random(17)
        self.room_time = 0.
        self.recipe = dict(metadata=metadata or {})
        self.pack = SimpleNamespace(props=props)
        self.players = {}
        self._next_id = 100
        self.retired = {}
        self.events = []
        self.presentation = ScPresentation(self)
        self.enemies = ScEnemies(self)

    def _offset(self):
        return 0

    def record(self, kind, **values):
        self.events.append(dict(kind=kind, **values))

    def _create(self, vid, pos, creator=0, changes=None):
        self._next_id += 1
        return self.add(self._next_id, vid, pos, creator, changes)

    def add(self, oid, vid, pos, creator=0, changes=None):
        p = props(vid)
        p.update(copy.deepcopy(changes or {}))
        self.actors[oid] = dict(id=oid, objid=oid, vid=vid, props=p,
                                pos=list(pos), hp=p.get('hitpoints'),
                                vel=[0., 0.], dead=False, creator=creator)
        return oid

    def _retire(self, oid, reason='INTERNAL', native=False):
        self.retired[oid] = reason
        self.actors.pop(oid, None)
        self.batch.destroy(oid, reason=reason)

    def wire_lifetime_due(self, oid):
        return None

    def player_at(self, x, y, key=1):
        self.players[key] = dict(xmp=x*256, ymp=y*256, wmp=32*256, hmp=64*256, alive=True)

    def floor(self, y=256):
        self.add(90, 'sc.wall', (-2000*256, y*256), changes=dict(wmp=4000*256, hmp=64*256, terraintype=1))

    def advance(self, seconds, dt=.05):
        steps = math.ceil(seconds/dt)
        for _ in range(steps):
            self.room_time += seconds/steps
            self.enemies.step(seconds/steps)


class ScEnemyTests(unittest.TestCase):
    @staticmethod
    def wall_emitter(angle=141, point=(1035008, 260864)):
        return dict(angle=angle, births=[dict(pos_mp=list(point), offset=82)],
                    sin=dict(vel=8000, period=750, amp=10000), interval_s=[.6, .6])

    def test_damage_on_a_body_without_hp_is_ignored_once(self):
        # A damage report the server cannot apply must never look like an
        # "unkillable" creature without a log line naming the actor.
        h = Host()
        h.floor(256)
        h.add(1, 'sc.golem', (0, 0), changes=dict(hitpoints=None))
        h.enemies.prepare()
        h.player_at(0, 0)
        for seq in (1, 2, 3):
            self.assertTrue(h.enemies.feedback(dict(kind='damage', victim=1, value=500, seq=seq)))
        self.assertIn(1, h.actors)
        self.assertIn(1, h.enemies._damage_ignored)

    def test_wall_emitter_crosses_its_wall_in_the_native_fixed_direction(self):
        emitter = self.wall_emitter()
        h = Host(dict(sc_wraith_emitters=[emitter]))
        h.add(80, 'sc.wall', (1030000, 250000), changes=dict(wmp=32000, hmp=32000,
                                                               terraintype=1, blocktype=1))
        h.player_at(6000, 0)
        h.enemies.prepare()
        h.advance(.1)
        oid, shot = next(iter(h.enemies.projectiles.shots.items()))
        self.assertEqual(shot['origin'], tuple(emitter['births'][0]['pos_mp']))
        # Native MT12 uses the same props position and sin origin even when
        # offset is nonzero; the next motion slice applies the lateral phase.
        self.assertEqual(h.actors[oid]['pos'], emitter['births'][0]['pos_mp'])
        self.assertEqual(shot['native']['sin'], dict(emitter['sin'], angle=141, offset=82,
            xmp=1035008, ymp=260864, pos=0))
        self.assertEqual(h.actors[oid]['creator'], 0)
        self.assertFalse(h.enemies.units)
        h.player_at(-6000, 0)
        h.advance(.2)
        self.assertIn(oid, h.actors, 'Wall births must not immediately collide away')
        expected = sinusoid(shot['native'], shot['origin'], h.room_time-shot['born'])
        for actual, wanted in zip(h.actors[oid]['pos'], expected):
            self.assertAlmostEqual(actual, wanted)
        self.assertLess(h.actors[oid]['pos'][0], shot['origin'][0])
        self.assertEqual(shot['native']['sin']['angle'], 141)

    def test_wall_emitter_repeats_without_creatures_and_does_not_aim_at_players(self):
        profiles = [self.wall_emitter(), self.wall_emitter(38, (1130752, 285952))]
        hosts = [Host(dict(sc_wraith_emitters=profiles)) for _ in range(2)]
        for h, x in zip(hosts, (-7000, 7000)):
            h.player_at(x, 0)
            h.enemies.prepare()
            h.advance(1.35)
        self.assertEqual([h.enemies.counts['wraith_emissions'] for h in hosts], [6, 6])
        self.assertEqual([s['native'] for s in hosts[0].enemies.projectiles.shots.values()],
                         [s['native'] for s in hosts[1].enemies.projectiles.shots.values()])
        self.assertEqual([r['pos'] for r in hosts[0].actors.values()],
                         [r['pos'] for r in hosts[1].actors.values()])

    def test_wall_emitter_return_and_delayed_frame_never_drain_a_backlog(self):
        h = Host(dict(sc_wraith_emitters=[self.wall_emitter()]))
        h.enemies.prepare()
        h.advance(.1)
        self.assertEqual(h.enemies.counts['wraith_emissions'], 1)
        h.room_time += 120.
        h.enemies.resume(120.)
        self.assertFalse(h.enemies.projectiles.shots)
        h.enemies.step(0.)
        h.advance(.59)
        self.assertEqual(h.enemies.counts['wraith_emissions'], 1)
        h.advance(.02)
        self.assertEqual(h.enemies.counts['wraith_emissions'], 2)
        h.room_time += 60.
        h.enemies.step(60.)
        self.assertEqual(h.enemies.counts['wraith_emissions'], 3)

    def test_wake_acquire_scan_are_independent_and_walls_do_not_block_target(self):
        h = Host(dict(sc_enemy_fit=dict(species={'sc.skeleton': dict(wake_px=1400, acquire_px=400, scan_s=1.)})))
        h.floor()
        h.add(1, 'sc.skeleton', (0, 128*256))
        h.add(80, 'sc.wall', (120*256, 0), changes=dict(wmp=32*256, hmp=256*256, terraintype=1))
        h.enemies.prepare()
        h.player_at(1000, 128)
        h.advance(.05)
        u = h.enemies.units[1]
        self.assertTrue(u['active'])
        self.assertIsNone(u['target'])
        h.player_at(250, 128)
        h.advance(1.05)
        self.assertEqual(u['target'], 1, 'A wall is not an acquisition gate')
        direction = u['direction']
        h.player_at(-200, 128)
        h.advance(.1)
        self.assertEqual(u['direction'], direction, 'No instantaneous facing change between searches')
        h.advance(1.)
        self.assertEqual(u['direction'], -1)

    def test_damage_does_not_wake_and_fixed_death_delay_does_not_reset_on_reentry(self):
        h = Host()
        h.add(1, 'sc.skeleton', (0, 0))
        h.enemies.prepare()
        h.enemies.feedback(dict(kind='damage', victim=1, value=10, event_id=1))
        self.assertFalse(h.enemies.units[1]['active'])
        h.enemies.feedback(dict(kind='damage', victim=1, value=2000, event_id=2))
        self.assertNotIn(1, h.actors)
        h.room_time = 119.9
        h.enemies.resume(119.9)
        self.assertFalse(h.enemies.units)
        h.room_time = 120.
        h.enemies.resume(.1)
        self.assertEqual(len(h.enemies.units), 1)
        new = next(iter(h.enemies.units))
        self.assertEqual(h.actors[new]['hp'], 1000)

    def test_death_cancels_unreleased_shots_but_keeps_airborne_projectiles(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0))
        h.enemies.prepare()
        old = h.enemies.projectiles.spawn('sc.sliver', (10000, 0), (25000., 0.), 1)
        h.enemies._queue(1, .4, 'sliver', angle=0.)
        h.enemies.feedback(dict(kind='damage', victim=1, value=1000, event_id=1))
        h.advance(.7)
        self.assertIn(old, h.enemies.projectiles.shots)
        self.assertEqual(len(h.enemies.projectiles.shots), 1)
        self.assertGreater(h.actors[old]['pos'][0], 10000)

    def test_sniper_five_locked_angles_and_sliver_second_damage_actor(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=1, target_point=(600*256, 32*256), next=0)
        h.enemies._weapons(1, u, object())
        self.assertEqual(len(h.enemies.pending), 5)
        angles = [round(math.degrees(e['angle']), 6) for e in h.enemies.pending]
        self.assertEqual(angles, [0., 5., -5., 10., -10.])
        h.advance(.75)
        slivers = [oid for oid, s in h.enemies.projectiles.shots.items() if s['vid'] == 'sc.sliver']
        self.assertEqual(len(slivers), 5)
        h.enemies.feedback(dict(kind='destroy', objid=slivers[0], reason=5))
        bursts = [oid for oid, s in h.enemies.projectiles.shots.items() if s['vid'] == 'sc.sliverx']
        self.assertEqual(len(bursts), 1)
        self.assertEqual(h.actors[bursts[0]]['creator'], slivers[0])
        # The wider blast may overlap the wall that stopped its parent. It
        # has no BLOCKTYPE and must persist for its native damage lifetime.
        point = h.actors[bursts[0]]['pos']
        h.add(80, 'sc.wall', point, changes=dict(wmp=64*256, hmp=64*256, terraintype=1))
        h.advance(.2)
        self.assertIn(bursts[0], h.actors)

    def test_assassin_wire_hp_clear_preserves_injury_and_emits_eight_on_burst(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=271))
        h.enemies.prepare()
        u = h.enemies.units[1]
        h.enemies._assassin_ghost(1, u)
        self.assertEqual(h.actors[1]['vid'], 'sc.eassassin')
        self.assertIsNone(h.actors[1]['hp'])
        clears = [q['modifyprops_'] for q in h.batch.objects if q.get('modifyprops_', {}).get('property_') == 19]
        self.assertEqual(clears[-1], {'property_': 19})
        h.enemies._assassin_burst(1, u)
        self.assertEqual(h.actors[1]['hp'], 271)
        self.assertEqual(h.actors[1]['vid'], 'sc.assassin')
        self.assertEqual(len(h.enemies.projectiles.shots), 8)

    def test_hunter_never_manifests_in_wall_and_preserves_center_when_clear(self):
        h = Host(dict(sc_ghost_forms=['sc.titan']))
        h.add(1, 'sc.hunter', (0, 0))
        h.add(80, 'sc.wall', (-50*256, -50*256), changes=dict(wmp=200*256, hmp=200*256, terraintype=1))
        h.enemies.prepare()
        u = h.enemies.units[1]
        target = dict(center=(48*256, 32*256))
        h.enemies._hunter(1, u, .05, target)
        self.assertIn(1, h.actors)
        h.actors.pop(80)
        h.enemies._hunter(1, u, .05, target)
        self.assertNotIn(1, h.actors)
        new = next(oid for oid, row in h.actors.items() if row['vid'] == 'sc.titan')
        self.assertEqual(h.actors[new]['pos'], [-64*256., -64*256.])
        self.assertEqual(h.enemies.units[new]['phase'], 'manifest')
        self.assertIsNone(h.actors[new]['hp'])
        h.advance(1.65)
        self.assertEqual(h.actors[new]['hp'], 2000)

    def test_urn_three_or_seven_slots_delayed_birth_and_120_second_lineage_refill(self):
        h = Host(dict(sc_surn_counts={'1': 3}, sc_ghost_forms=['sc.sniper']))
        h.add(1, 'sc.surn', (0, 0))
        h.enemies.prepare()
        h.player_at(1000, 0)
        h.advance(.1)
        self.assertFalse(h.enemies.units, '264ms animation is not an instant birth')
        h.advance(.85)
        self.assertEqual(len(h.enemies.units), 3)
        old = next(iter(h.enemies.units))
        urn, slot = h.enemies.units[old]['lineage']
        # Exercise the actual transformed body death, not an unkillable hunter.
        h.enemies.units[old]['vid'] = 'sc.sniper'
        h.actors[old]['hp'] = 750
        h.enemies.feedback(dict(kind='damage', victim=old, value=750, event_id=2))
        self.assertAlmostEqual(h.enemies.urns[urn]['due'][slot], h.room_time+120.)
        h.room_time += 119.9
        h.enemies.resume(119.9)
        self.assertNotIn(slot, h.enemies.urns[urn]['slots'])
        h.advance(.4)
        self.assertIn(slot, h.enemies.urns[urn]['slots'])
        self.assertEqual(len(h.enemies.urns[urn]['slots']), 3)
        with self.assertRaises(ValueError):
            bad = Host(dict(sc_surn_counts={'1': 5}))
            bad.add(1, 'sc.surn', (0, 0))
            bad.enemies.prepare()

    def test_monk_finishes_parent_sequence_after_players_leave_then_refills(self):
        h = Host(dict(sc_enemy_fit=dict(monk_mode_choices=['sequential'])))
        h.add(1, 'sc.monk', (0, 0))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u['active'] = True
        h.advance(2.)
        self.assertEqual(len(u['orbs']), 8)
        h.enemies._monk_attack(1, u, (100*256, 0))
        self.assertEqual(len(h.enemies.pending), 8)
        h.players.clear()
        h.advance(1.8)
        self.assertFalse(u['monk_firing'])
        self.assertEqual(len(u['orbs']), 0)
        released = [s for s in h.enemies.projectiles.shots.values() if s['orbit'] is None]
        self.assertEqual(len(released), 8)
        h.advance(3.9)
        self.assertEqual(len(u['orbs']), 8)

    def test_thrown_ball_collides_with_the_golem_that_threw_it(self):
        # USER: the Cathedral ball is native terrain and collides with the
        # creature that threw it, instead of passing through that body.
        h = Host()
        h.floor(256)
        h.add(1, 'sc.golem', (0, 0))
        h.enemies.prepare()
        ball = h.enemies.projectiles.spawn('sc.golemb', (400*256, 0), (-136000., 0.), 1,
                                           gravity=200000., bounce=True)
        for _ in range(40):
            h.advance(.05)
            if h.enemies.projectiles.shots.get(ball, {}).get('vx', -1.) > 0:
                break
        self.assertGreater(h.enemies.projectiles.shots[ball]['vx'], 0.)

    def test_golem_landing_creates_opposite_waves_and_titan_has_no_weapon(self):
        h = Host()
        h.floor(256)
        h.add(1, 'sc.golem', (0, 0))
        h.add(2, 'sc.titan', (500*256, 64*256))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u.update(active=True, phase='patrol', grounded=True, vy=-90000., slam=True, busy=.92)
        h.advance(1.)
        waves = [s for s in h.enemies.projectiles.shots.values() if s['vid'] == 'sc.golemw']
        self.assertEqual(len(waves), 2)
        self.assertEqual(sorted(s['vx'] for s in waves), [-148500., 148500.])
        titan = h.enemies.units[2]
        titan.update(next=0, busy=0, phase='chase', target_point=(520*256, 64*256))
        h.enemies._weapons(2, titan, object())
        self.assertEqual(h.enemies.pending, [])

    def test_ground_one_way_drop_is_separate_from_solid_floor(self):
        h = Host()
        h.add(80, 'sc.ledge', (-100*256, 128*256), changes=dict(wmp=300*256, hmp=16*256, terraintype=2))
        h.add(81, 'sc.wall', (-100*256, 300*256), changes=dict(wmp=300*256, hmp=16*256, terraintype=1))
        h.add(1, 'sc.skeleton', (0, 0))
        state = dict(vy=0., grounded=True, support=80)
        terrain = geometry(h)
        self.assertEqual(ground(terrain, h.actors[1], state, .05, 0, 200000.)[1], 0.)
        point = ground(terrain, h.actors[1], state, .05, 0, 200000., drop=True)
        self.assertGreater(point[1], 0.)
        self.assertTrue(clear(h, (0, 0, 64*256, 128*256), (1,)))

    def test_reaudit_golem_release_uses_variant_socket_and_locked_horizontal_side(self):
        for variant, direction, socket in ((9, 1, (180, -192)), (9, -1, (-180, -192)),
                                           (11, 1, (96, 74)), (11, -1, (-96, 74))):
            with self.subTest(variant=variant, direction=direction):
                h = Host()
                h.add(1, 'sc.golem', (100*256, 200*256))
                h.enemies.prepare()
                h.enemies.units[1]['direction'] = -direction
                # A moving target above/below must not pitch the locked shot.
                h.enemies._emit(dict(owner=1, kind='golemb', variant=variant,
                                     direction=direction, angle=math.pi/3))
                ball, shot = next(iter(h.enemies.projectiles.shots.items()))
                p = h.actors[ball]['props']
                centre = (h.actors[ball]['pos'][0]+p['wmp']/2,
                          h.actors[ball]['pos'][1]+p['hmp']/2)
                self.assertEqual(centre, ((180+socket[0])*256, (328+socket[1])*256))
                self.assertEqual((shot['vx'], shot['vy']),
                                 (136000. if direction > 0 else -137000., 0.))

    def test_reaudit_tank_moves_beyond_birth_patrol_fit(self):
        h = Host()
        h.floor(256)
        h.add(1, 'sc.tank', (0, 128*256))
        h.enemies.prepare()
        h.player_at(800, 160, key=7)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', grounded=True, support=90,
                 target=7, target_point=(816*256, 192*256), next=100., scan=100.)
        h.advance(2.)
        self.assertGreater(h.actors[1]['pos'][0], 150*256)
        self.assertEqual(u['direction'], 1)

    def test_reaudit_tank_attack_stays_still_far_from_birth(self):
        h = Host()
        h.floor(256)
        h.add(1, 'sc.tank', (0, 128*256))
        h.enemies.prepare()
        h.actors[1]['pos'][0] = 600*256
        u = h.enemies.units[1]
        u.update(active=True, phase='patrol', grounded=True, support=90,
                 busy=2., lock=2., direction=1)
        h.advance(.4)
        self.assertEqual(h.actors[1]['pos'][0], 600*256,
                         'An attack lock must not be overridden by a homeward turn')
        self.assertEqual(u['direction'], 1)

    @staticmethod
    def reaudit_platform_host(rise, *, ceiling=False, wake=1600):
        h = Host(dict(sc_enemy_fit=dict(species={'sc.skeleton': dict(wake_px=wake)})))
        h.floor(800)
        h.add(80, 'sc.ledge', (180*256, (800-rise)*256),
              changes=dict(wmp=320*256, hmp=16*256, terraintype=2))
        if ceiling:
            h.add(81, 'sc.wall', (-1000*256, 500*256),
                  changes=dict(wmp=2000*256, hmp=64*256, terraintype=1))
        h.add(1, 'sc.skeleton', (0, 672*256))
        h.enemies.prepare()
        h.player_at(300, 800-rise-64, key=7)
        u = h.enemies.units[1]
        u.update(grounded=True, support=90, next=100.)
        return h, u

    def test_reaudit_skeleton_selects_low_or_high_reachable_platform_jump(self):
        launches = {}
        for rise in (128, 640):
            with self.subTest(rise=rise):
                h, u = self.reaudit_platform_host(rise)
                h.advance(.05)
                self.assertFalse(u['grounded'])
                launches[rise] = u['vy']
                landed = False
                for _ in range(65):
                    h.advance(.05)
                    if u.get('grounded') and u.get('support') == 80:
                        landed = True
                        break
                self.assertTrue(landed, 'The jump must reach its planned platform')
                self.assertAlmostEqual(h.actors[1]['pos'][1], (800-rise-128)*256)
        self.assertGreater(launches[128], -200000.)
        self.assertLess(launches[640], -240000.)

    def test_reaudit_skeleton_rejects_high_landing_blocked_by_ceiling(self):
        h, u = self.reaudit_platform_host(640, ceiling=True)
        h.advance(.1)
        self.assertTrue(u['grounded'])
        self.assertEqual(h.actors[1]['pos'][1], 672*256)
        self.assertFalse(any(q.get('name') == 'sc.skeletonjump' for q in h.events))

    def test_reaudit_dormant_hit_does_not_start_platform_navigation(self):
        h, u = self.reaudit_platform_host(640, wake=0)
        h.enemies.feedback(dict(kind='damage', victim=1, value=10, event_id='dormant-platform'))
        h.advance(.3)
        self.assertFalse(u['active'])
        self.assertEqual(h.actors[1]['pos'], [0, 672*256])
        self.assertNotIn('jump_vx', u)
        self.assertEqual(h.actors[1]['hp'], 990)

    def test_reaudit_skeleton_still_drops_toward_lower_platform(self):
        h = Host()
        h.floor(800)
        h.add(80, 'sc.ledge', (-100*256, 256*256),
              changes=dict(wmp=500*256, hmp=16*256, terraintype=2))
        h.add(1, 'sc.skeleton', (0, 128*256))
        h.enemies.prepare()
        h.player_at(100, 736, key=7)
        u = h.enemies.units[1]
        u.update(grounded=True, support=80, next=100.)
        h.advance(.4)
        self.assertGreater(h.actors[1]['pos'][1], 128*256)
        self.assertNotIn('jump_vx', u)

    def test_reaudit_flytank_search_does_not_override_heading_between_decisions(self):
        h = Host(dict(sc_enemy_fit=dict(steer_leg_s=(1., 1.),
                                        species={'sc.flytank': dict(scan_s=.05)})))
        h.add(1, 'sc.flytank', (0, 0), changes=dict(facing=0))
        h.enemies.prepare()
        h.player_at(-400, 0, key=7)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7, next=100.,
                 direction=1, steer_at=1., hover_x=0, hover_y=0)
        h.advance(.2)
        self.assertGreater(h.actors[1]['pos'][0], 0.)
        self.assertEqual(u['direction'], 1)
        self.assertEqual(h.actors[1]['props']['facing'], 0)
        h.advance(.85)
        self.assertEqual(u['direction'], -1)
        self.assertEqual(h.actors[1]['props']['facing'], 180)

    def test_reaudit_flytank_boundary_wins_over_same_slice_player_steering(self):
        h = Host()
        h.add(1, 'sc.flytank', (0, 0))
        h.enemies.prepare()
        h.player_at(600, 0, key=7)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7, next=100., direction=1,
                 steer_at=0., hover_x=0, hover_y=0)
        h.actors[1]['pos'][0] = 180*256
        h.advance(.05)
        self.assertEqual(u['direction'], -1)
        self.assertLess(h.actors[1]['pos'][0], 180*256)
        self.assertEqual(h.actors[1]['pos'][1], 0)

    def test_reaudit_flytank_provisional_range_covers_later_attack_context(self):
        h = Host()
        h.add(1, 'sc.flytank', (0, 0))
        h.enemies.prepare()
        # Local FIT acceptance case, not a claim about a native range cutoff.
        h.player_at(832, 43, key=7)
        h.advance(.15)
        self.assertEqual(h.enemies.counts['attacks'], 1)
        self.assertEqual(h.enemies.units[1]['target'], 7)

    def test_resume_expires_shots_and_cancels_backlog_without_emitting(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0))
        h.enemies.prepare()
        h.enemies._queue(1, .4, 'sliver', angle=0.)
        shot = h.enemies.projectiles.spawn('sc.sliver', (0, 0), (25000., 0.), 1)
        h.room_time = 30.
        h.enemies.resume(30.)
        self.assertEqual(h.enemies.pending, [])
        self.assertNotIn(shot, h.actors)
        self.assertEqual(h.enemies.projectiles.shots, {})

    def test_guardian_callback_is_hp_only_and_reward_does_not_repeat_after_respawn(self):
        h = Host(dict(chest_guardian=1))
        h.add(1, 'sc.egolem', (0, 0))
        h.enemies.prepare()
        self.assertEqual(h.enemies.units[1]['vid'], 'sc.egolem')
        self.assertEqual(h.actors[1]['hp'], 10000)
        calls, drops = [], []
        h.mechanics = SimpleNamespace(guardian_defeated=lambda oid: calls.append(oid))
        h.supplies = SimpleNamespace(killed=lambda *pos: drops.append(pos))
        h.enemies.feedback(dict(kind='destroy', objid=1, reason=2, event_id=1))
        h.room_time = 120.
        h.enemies.resume(120.)
        new = next(iter(h.enemies.units))
        self.assertEqual(h.actors[new]['vid'], 'sc.egolem')
        self.assertEqual(h.actors[new]['hp'], 10000)
        h.enemies.feedback(dict(kind='destroy', objid=new, reason=2, event_id=2))
        self.assertEqual(calls, [1, 1])  # mechanism owns persistent unlock dedupe
        self.assertEqual(len(drops), 1)

    def test_departed_target_is_forgotten_during_scan_and_attack_lock(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7, target_point=(-300*256, 0),
                 direction=1, scan=10., lock=10., next=10.)
        # No player 7 is present. Its old point lies left; the retained patrol
        # direction is right, so positions distinguish stale chase from patrol.
        h.advance(.05)
        self.assertEqual(u['phase'], 'patrol')
        self.assertIsNone(u['target'])
        self.assertIsNone(u['target_point'])
        self.assertGreater(h.actors[1]['pos'][0], 0.)
        self.assertEqual(u['direction'], 1, 'Losing a target does not unlock an attack-facing change')

    def test_range_loss_uses_search_deadline_then_returns_to_patrol(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0))
        h.enemies.prepare()
        h.player_at(5000, 0, key=7)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7, target_point=(300*256, 0), scan=.5, next=10.)
        h.advance(.1)
        self.assertEqual(u['target'], 7, 'A distant existing player is evaluated on the search timer')
        h.advance(.45)
        self.assertEqual(u['phase'], 'patrol')
        self.assertIsNone(u['target'])
        self.assertIsNone(u['target_point'])

    def test_resume_does_not_skip_incomplete_or_newborn_manifest(self):
        for newborn in (False, True):
            with self.subTest(newborn=newborn):
                h = Host()
                h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=750 if newborn else None))
                h.enemies.prepare()
                if newborn:
                    h.enemies.feedback(dict(kind='damage', victim=1, value=750, event_id=1))
                    h.room_time = 120.
                    h.enemies.resume(120.)
                    oid = next(iter(h.enemies.units))
                else:
                    h.room_time = .2
                    h.enemies.resume(.2)
                    oid = 1
                deadline = h.enemies.units[oid]['busy']
                self.assertGreater(deadline, h.room_time)
                h.advance(.05)
                self.assertEqual(h.enemies.units[oid]['phase'], 'manifest')
                self.assertIsNone(h.actors[oid]['hp'])
                self.assertEqual(h.actors[oid]['props']['damage'], 0)
                h.advance(deadline-h.room_time+.01)
                self.assertEqual(h.actors[oid]['hp'], 750)

    def test_resume_clears_native_attack_animation_and_dive_without_shortening_cooldown(self):
        from localserver.maps.hulk_mid.scene import normalize
        from localserver.world_snapshot import WorldSnapshot, decode_delta
        h = Host()
        h.add(1, 'sc.skeleton', (0, 0))
        h.add(2, 'sc.scout', (500*256, 0))
        h.enemies.prepare()
        sword, scout = h.enemies.units[1], h.enemies.units[2]
        sword.update(active=True, phase='chase', target=7, target_point=(100*256, 0), grounded=True)
        h.enemies._attack(1, sword, 6, 594, window=.405, cooldown=2.2)
        h.actors[1]['props']['pose'] = 4
        h.enemies._laser(1, (400*256, 0))
        self.assertTrue(h.enemies.effects)
        scout.update(active=True, phase='chase', target=7, target_point=(0, 0), dive_until=.8)
        snapshot = WorldSnapshot(h.batch.layouts, dict(worldstate_=dict(serverworld_=dict(ownerid_=1,
            obj_=[dict(objid_=1, props_=normalize('Properties', h.actors[1]['props'], h.batch.layouts))]))))
        h.batch.clear()
        h.room_time = .2
        h.enemies.resume(.2)
        self.assertGreaterEqual(sword['next'], 2.2)
        self.assertEqual(sword['busy'], 0.)
        self.assertEqual(sword['phase'], 'patrol')
        self.assertIsNone(sword['target_point'])
        self.assertLessEqual(scout.get('dive_until', 0), h.room_time)
        self.assertFalse(h.enemies.effects, 'Canceled laser telegraphs cannot linger into the return snapshot')
        snapshot.apply(decode_delta(h.batch.layouts, h.batch.encode()))
        actual = snapshot.properties_at((1, 1))
        self.assertNotIn('animprops_', actual, 'The reconnect snapshot must not retain a native attack hitbox')
        self.assertEqual(actual['pose_'], 5)
        self.assertFalse(any('playsound_' in q for q in h.batch.objects), 'Cancellation must not replay attack audio')

    def test_monk_resume_cancels_unreleased_queue_but_keeps_released_orbs_and_cooldown(self):
        h = Host(dict(sc_enemy_fit=dict(monk_mode_choices=['sequential'])))
        h.add(1, 'sc.monk', (0, 0))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u['active'] = True
        h.advance(2.)
        h.enemies._monk_attack(1, u, (300*256, 0))
        h.advance(.3)
        released = {oid for oid, shot in h.enemies.projectiles.shots.items() if shot['orbit'] is None}
        self.assertEqual(len(released), 2)
        h.room_time += .2
        h.enemies.resume(.2)
        self.assertFalse(u['monk_firing'])
        self.assertEqual(h.enemies.pending, [])
        h.player_at(400, 0)
        h.advance(3.)
        self.assertEqual(h.enemies.counts['monk_volleys'], 1, 'Return must not replay the interrupted volley')
        self.assertTrue(released <= set(h.enemies.projectiles.shots), 'Released shots keep their independent lifecycle')
        self.assertEqual(len(u['orbs']), 8)

    def test_resume_cancels_old_assassin_burst_and_restores_injured_body_silently(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=271))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7, target_point=(200*256, 0))
        h.enemies._assassin_ghost(1, u)
        h.batch.clear()
        h.room_time = .2
        h.enemies.resume(.2)
        self.assertEqual(h.actors[1]['vid'], 'sc.assassin')
        self.assertEqual(h.actors[1]['hp'], 271)
        self.assertEqual(u['phase'], 'patrol')
        self.assertFalse(any('playsound_' in q for q in h.batch.objects))
        h.advance(1.)
        self.assertEqual(h.enemies.counts['bursts'], 0)
        self.assertFalse(h.enemies.projectiles.shots)

    def test_hunter_keeps_chosen_player_and_passes_through_wall_before_manifest(self):
        h = Host(dict(sc_ghost_forms=['sc.sniper']))
        h.add(1, 'sc.hunter', (0, 0))
        h.add(80, 'sc.wall', (100*256, -100*256), changes=dict(wmp=200*256, hmp=300*256, terraintype=1))
        h.enemies.prepare()
        h.player_at(500, 0, key=7)
        h.player_at(20, 0, key=8)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=7)
        h.advance(1.)
        self.assertEqual(u['target'], 7, 'A closer player must not steal the Hunter lock')
        self.assertIn(1, h.actors)
        self.assertGreater(h.actors[1]['pos'][0], 100*256, 'The Hunter must enter/pass wall terrain')
        h.advance(1.5)
        self.assertNotIn(1, h.actors)
        corporeal = next(oid for oid in h.enemies.units)
        self.assertEqual(h.actors[corporeal]['vid'], 'sc.sniper')
        self.assertEqual(h.enemies.units[corporeal]['target'], 7)
        self.assertTrue(clear(h, (*h.actors[corporeal]['pos'], 64*256, 64*256), (corporeal,)))

    def test_urn_produces_and_replenishes_dead_lineage_with_player_far_away(self):
        h = Host(dict(sc_surn_counts={'1': 3}, sc_ghost_forms=['sc.sniper']))
        h.add(1, 'sc.surn', (0, 0))
        h.enemies.prepare()
        h.player_at(4000, 0)
        h.advance(.5)
        self.assertTrue(h.enemies.units, 'Room production is independent from creature targeting range')
        self.assertTrue(all(row['vid'] == 'sc.hunter' for oid, row in h.actors.items() if oid in h.enemies.units))
        h.player_at(100, 0)
        h.advance(3.5)
        bodies = [oid for oid, row in h.actors.items() if row['vid'] == 'sc.sniper' and row['hp'] == 750]
        self.assertEqual(len(bodies), 3, 'Exercise real urn birth, Hunter movement and corporeal manifestation')
        victim = bodies[0]
        urn, slot = h.enemies.units[victim]['lineage']
        h.enemies.feedback(dict(kind='damage', victim=victim, value=750, event_id=31))
        h.player_at(4000, 0)
        h.room_time += 120.
        h.enemies.resume(120.)
        h.advance(.4)
        child = h.enemies.urns[urn]['slots'].get(slot)
        self.assertIsNotNone(child, 'Creature targeting range must not gate later replenishment')
        self.assertEqual(h.actors[child]['vid'], 'sc.hunter')
        self.assertEqual(len(h.enemies.urns[urn]['slots']), 3)


if __name__ == '__main__':
    unittest.main()
