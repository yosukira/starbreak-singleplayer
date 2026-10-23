"""SC ghost launch regressions against owned native velocity/endpoint samples."""
import math
import unittest
from types import SimpleNamespace

from test_sc_enemies import Host, props
from localserver.maps.sc_run.abilities import center
from localserver.maps.sc_run.physics import clear, geometry, rect
from localserver.maps.run_common.actors import ActorSession
from localserver.maps.sc_run.enemies import ScEnemies
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.live.wire import DeltaBatch


def actor_session():
    """Actual shared actor clock with a sink for its encoded Delta frames."""
    sent = []
    layouts = DeltaBatch().layouts
    server = SimpleNamespace(layouts=layouts,
        _send_raw=lambda channel, kind, payload, label='', record=False: sent.append((kind, payload)))
    instance = SimpleNamespace(seed=17, recipe=dict(metadata={}, creators={}),
                               snapshot=SimpleNamespace(objects={}), flags={})
    bridge = SimpleNamespace(snapshot=lambda: (None, {}), poll_events=lambda: [])
    mechanics = lambda host: SimpleNamespace(prepare=lambda: None, step=lambda dt: None)
    host = ActorSession(server, None, SimpleNamespace(), instance, bridge,
        SimpleNamespace(props=props), map_id='sc_run', enemies_factory=ScEnemies,
        mechanics_factory=mechanics, presentation_factory=ScPresentation)
    return host, sent


class ScGhostProjectileMotionTests(unittest.TestCase):
    def test_sliver_native_wire_speed_and_open_space_muzzle(self):
        h = Host()
        # A left-facing native body and its 130-degree shot have this exact
        # relative muzzle; the MOVE components integrate to -63/+74mp/ms.
        h.add(1, 'sc.sniper', (1202987, 168795), changes=dict(facing=180))
        h.enemies._emit(dict(owner=1, kind='sliver', angle=math.radians(130)))
        oid, shot = next(iter(h.enemies.projectiles.shots.items()))
        self.assertEqual(tuple(h.actors[oid]['pos']), (1186091, 166491))
        self.assertEqual((shot['vx'], shot['vy']), (-63000, 74000))
        self.assertEqual(h.actors[oid]['creator'], 1)
        h.room_time = .05
        h.enemies.projectiles.step(.05, [])
        self.assertEqual(tuple(h.actors[oid]['pos']), (1182941, 170191))

    def test_sliver_birth_is_clipped_before_a_near_wall(self):
        h = Host()
        h.add(1, 'sc.sniper', (0, 0), changes=dict(facing=0))
        h.add(90, 'sc.wall', (64*256, -128*256),
              changes=dict(wmp=64*256, hmp=256*256, terraintype=1))
        h.enemies.terrain = geometry(h)
        h.enemies._emit(dict(owner=1, kind='sliver', angle=0.))
        oid = next(iter(h.enemies.projectiles.shots))
        self.assertTrue(clear(h, rect(h.actors[oid]), (oid,)))
        self.assertLessEqual(h.actors[oid]['pos'][0]+24*256, 64*256+1)
        self.assertGreater(center(h.actors[oid])[0], center(h.actors[1])[0])

    def test_shard_open_space_birth_and_short_free_fall_match_native_path(self):
        h = Host()
        h.add(1, 'sc.bomber', (318001, 19703), changes=dict(facing=180))
        h.actors[1]['vel'] = [8000., 56000.]
        h.enemies._emit(dict(owner=1, kind='shard'))
        oid, shot = next(iter(h.enemies.projectiles.shots.items()))
        self.assertEqual(tuple(h.actors[oid]['pos']), (322865, 37879))
        self.assertEqual((shot['vx'], shot['vy']), (8000., 0.))
        # Native integer endpoints are not a continuous parabola. Check the
        # local gravity fit against three observed positions within one pixel.
        for x, y in ((323265, 38029), (323665, 38579), (324065, 39529)):
            h.room_time += .05
            h.enemies.projectiles.step(.05, [])
            self.assertEqual(h.actors[oid]['pos'][0], x)
            self.assertLess(abs(h.actors[oid]['pos'][1]-y), 256)

    def test_shard_keeps_fast_parent_horizontal_motion_without_vertical_inheritance(self):
        for vx in (-59000., 58000.):
            with self.subTest(vx=vx):
                h = Host()
                h.add(1, 'sc.bomber', (0, 0))
                h.actors[1]['vel'] = [vx, -57000.]
                h.enemies._emit(dict(owner=1, kind='shard'))
                shot = next(iter(h.enemies.projectiles.shots.values()))
                self.assertEqual((shot['vx'], shot['vy']), (vx, 0.))

    def test_shard_birth_above_floor_and_adoption_share_the_gravity_fit(self):
        h = Host()
        h.floor(64)
        h.add(1, 'sc.bomber', (0, 0))
        h.enemies.terrain = geometry(h)
        h.enemies._emit(dict(owner=1, kind='shard'))
        oid, dynamic = next(iter(h.enemies.projectiles.shots.items()))
        self.assertTrue(clear(h, rect(h.actors[oid]), (oid,)))
        self.assertAlmostEqual(center(h.actors[oid])[1], 52*256, delta=1.)
        h.add(2, 'sc.shard', (0, -100000))
        h.enemies.add_actor(2)
        self.assertEqual(h.enemies.projectiles.shots[2]['gravity'], dynamic['gravity'])

    def test_assassin_eight_slow_straight_shots_can_share_directions(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=271))
        h.enemies.prepare()
        u = h.enemies.units[1]
        h.enemies._assassin_burst(1, u)
        shots = h.enemies.projectiles.shots
        self.assertEqual(len(shots), 8)
        octants = []
        for oid, shot in shots.items():
            vx, vy = shot['vx'], shot['vy']
            self.assertTrue(all(-16000 <= v <= 15000 and v % 1000 == 0 for v in (vx, vy)))
            self.assertLessEqual(math.hypot(vx, vy), 22628.)
            self.assertEqual(h.actors[oid]['creator'], 0)
            octants.append(round(math.atan2(vy, vx)*4/math.pi) % 8)
        self.assertLess(len(set(octants)), 8)
        origins = {oid: tuple(h.actors[oid]['pos']) for oid in shots}
        h.room_time = .5
        h.enemies.projectiles.step(.5, [])
        for oid, shot in shots.items():
            self.assertEqual(tuple(h.actors[oid]['pos']),
                             (origins[oid][0]+shot['vx']*.5, origins[oid][1]+shot['vy']*.5))
        self.assertEqual(h.actors[1]['hp'], 271)

    def test_real_actor_session_burst_wire_birth_already_has_one_stationary_slice(self):
        h, sent = actor_session()
        parent = h._create('sc.assassin', (0, 0), changes=dict(hitpoints=271))
        h.enemies.add_actor(parent)
        player = dict(xmp=100*256, ymp=0, wmp=32*256, hmp=64*256, alive=True)
        h.players_provider = {1: player}
        u = h.enemies.units[parent]
        u.update(target=1, target_point=center(player))
        h.enemies._assassin_ghost(parent, u)
        u['ghost_at'] = .05
        u['ghost_moves'] = 1
        h.step(.05)
        ids = set(h.enemies.projectiles.shots)
        self.assertEqual(len(ids), 8)
        origins = {oid: tuple(h.actors[oid]['pos']) for oid in ids}
        self.assertEqual(sent[0][0], 12)
        first = h.layouts.decode('Delta', sent[0][1])['worldupdate_']
        creates = [r for r in first['generalupdates_'] if r.get('create_', {}).get('objid_') in ids]
        self.assertEqual(len(creates), 8)
        self.assertTrue(all(r.get('offset_', 0) == 0 for r in creates))
        self.assertEqual(first['dt_'], 50)
        self.assertFalse([r for r in first['objectupdates_'] if r['objid_'] in ids and 'movetoxmp_' in r])
        h.step(.05)
        second = h.layouts.decode('Delta', sent[1][1])['worldupdate_']
        for r in second['objectupdates_']:
            oid = r['objid_']
            if oid not in ids or 'movetoxmp_' not in r:
                continue
            shot = h.enemies.projectiles.shots[oid]
            # Birth is wire t=0; the first target is t=100, with only 50ms
            # displacement. Another explicit delay would double the pause.
            target_t = first['dt_']+r.get('offset_',0)+r.get('dt_',second['dt_'])
            self.assertEqual(target_t, 100)
            self.assertEqual((r['movetoxmp_'], r['movetoymp_']),
                             (origins[oid][0]+shot['vx']*.05, origins[oid][1]+shot['vy']*.05))
        self.assertEqual(sum(r['objid_'] in ids and 'movetoxmp_' in r for r in second['objectupdates_']), 8)

    def test_shard_survives_static_landing_with_its_own_horizontal_velocity(self):
        h, _ = actor_session()
        h._create('sc.wall', (106496, 196608),
                  changes=dict(wmp=245760, hmp=8192, terraintype=1))
        oid = h.enemies.projectiles.spawn('sc.shard', (276346, 190000),
                                          (-9000., 0.), 0, gravity=156000.)
        h.step(.1)
        self.assertIn(oid, h.actors)
        self.assertEqual(h.actors[oid]['pos'][1], 190464)
        self.assertEqual(h.actors[oid]['props']['pose'], 5)
        self.assertEqual(h.enemies.projectiles.shots[oid]['vx'], -9000.)
        old = h.actors[oid]['pos'][0]
        h.step(.05)
        self.assertEqual(h.actors[oid]['pos'][0], old-450)
        self.assertEqual(h.actors[oid]['pos'][1], 190464)

    def test_shard_adds_moving_support_displacement_to_its_own_air_velocity(self):
        h, _ = actor_session()
        platform = h._create('sc.mledge128', (319408, 91392),
            changes=dict(wmp=32768, hmp=24576, terraintype=2))
        oid = h.enemies.projectiles.spawn('sc.shard', (329313, 85248),
                                          (8000., 0.), 0, gravity=156000.)
        h.step(.05)
        self.assertEqual(h.enemies.projectiles.shots[oid]['support'], platform)

        def advance_platform(dt):
            row = h.actors[platform]
            old = tuple(row['pos'])
            row['pos'][0] -= 23000*dt
            h.mechanics.platform_deltas = {platform: (old, tuple(row['pos']), (32768, 24576))}

        h.mechanics.pre_physics = advance_platform
        old = h.actors[oid]['pos'][0]
        h.step(.05)
        self.assertEqual(h.actors[oid]['pos'][0], old-750.)
        self.assertEqual(h.actors[oid]['pos'][1], 85248)
        self.assertEqual(h.enemies.projectiles.shots[oid]['vx'], 8000.)
        self.assertEqual(h.enemies.projectiles.shots[oid]['support'], platform)

    def test_shard_leaves_edge_falls_again_and_expires_at_authored_six_seconds(self):
        h, sent = actor_session()
        h._create('sc.mledge128', (0, 0),
                  changes=dict(wmp=100*256, hmp=24*256, terraintype=2))
        oid = h.enemies.projectiles.spawn('sc.shard', (90*256, -24*256),
                                          (8000., 0.), 0, gravity=156000.)
        h.step(.05)
        self.assertEqual(h.actors[oid]['props']['pose'], 5)
        h.step(.3)
        self.assertIn(oid, h.actors)
        self.assertEqual(h.actors[oid]['props']['pose'], 1)
        self.assertFalse(h.enemies.projectiles.shots[oid]['grounded'])
        self.assertGreater(h.enemies.projectiles.shots[oid]['vy'], 0.)
        self.assertEqual(h.enemies.projectiles.shots[oid]['vx'], 8000.)
        h.step(5.64)
        self.assertIn(oid, h.actors)
        h.step(.01)
        self.assertNotIn(oid, h.actors)
        world = h.layouts.decode('Delta', sent[-1][1])['worldupdate_']
        self.assertIn(dict(ownerid_=1, objid_=oid, reason_=6),
                      [r['destroy_'] for r in world['generalupdates_'] if 'destroy_' in r])


if __name__ == '__main__':
    unittest.main()
