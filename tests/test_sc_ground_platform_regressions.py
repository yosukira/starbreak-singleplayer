"""Ground decisions through ActorSession, real room terrain and wire snapshots."""
import copy
import math
import unittest
from types import SimpleNamespace

from localserver.pb_layout import default_layouts
from localserver.world_snapshot import WorldSnapshot, decode_delta
from localserver.maps.hulk_mid.scene import normalize
from localserver.maps.run_common.actors import ActorSession
from localserver.maps.sc_run.enemies import ScEnemies
from localserver.maps.sc_run.mechanics import ScMechanics
from localserver.maps.sc_run.physics import surface
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.maps.sc_run.scene import ScRunPack


def host(template=None, objects=None, *, seed=0):
    pack = ScRunPack()
    recipe = pack.content.recipe(template, seed) if template else dict(
        metadata={}, creators={}, objects=objects,
        world=dict(worldstate_=dict(serverworld_=dict(ownerid_=1))))
    layouts = default_layouts()
    document = copy.deepcopy(recipe['world'])
    document['worldstate_']['serverworld_']['obj_'] = [
        dict(objid_=oid, props_=normalize('Properties', p, layouts))
        for oid, p in recipe['objects'].items()]
    document['worldstate_']['clientworld_'] = []
    snapshot = WorldSnapshot(layouts, document)
    instance = SimpleNamespace(seed=7, role=template or 'ground', recipe=recipe,
                               snapshot=snapshot, flags={})
    sent = []

    def send(_channel, kind, payload, label='', record=False):
        if kind == 12:
            message = decode_delta(layouts, payload)
            snapshot.apply(message)
            sent.append(message)

    server = SimpleNamespace(layouts=layouts, _send_raw=send)
    bridge = SimpleNamespace(snapshot=lambda: (None, {}), poll_events=lambda: [])
    runtime = ActorSession(server, None, SimpleNamespace(), instance, bridge, pack,
        map_id='sc_run', enemies_factory=ScEnemies, mechanics_factory=ScMechanics,
        presentation_factory=ScPresentation, players={})
    runtime.sent = sent
    return runtime


def terrain(x, y, w, kind=2):
    return dict(vid='sc.ledge' if kind == 2 else 'sc.wall', xmp=x*256, ymp=y*256,
                wmp=w*256, hmp=16*256, terraintype=kind)


def creature(vid, x, y):
    p = ScRunPack().props(vid)
    p.update(vid=vid, xmp=x*256, ymp=y*256)
    return p


def player(x, foot_y, **extra):
    return dict(xmp=x*256, ymp=(foot_y-80)*256, wmp=30*256, hmp=80*256,
                alive=True, **extra)


class GroundPlatformRegressions(unittest.TestCase):
    def assert_snapshot_matches(self, h, oid):
        actual = h.instance.snapshot.position((1, oid))
        for received, simulated in zip(actual, h.actors[oid]['pos']):
            self.assertAlmostEqual(received, simulated, delta=1.1)

    def advance(self, h, seconds, dt=.05):
        for _ in range(math.ceil(seconds/dt)):
            h.step(seconds/math.ceil(seconds/dt))

    def test_real_staircase_floaters_bind_before_activation_and_send_carry(self):
        for template, oid in (('scmid13', 123), ('scmid16', 47), ('scmid17', 233)):
            with self.subTest(template=template):
                h = host(template)
                u, row = h.enemies.units[oid], h.actors[oid]
                support = u['support']
                self.assertTrue(u['grounded'])
                self.assertTrue(h.actors[support]['vid'].startswith('sc.fledge'))
                initial_y = row['pos'][1]
                # The platform already moves while the creature is dormant.
                h.step(.05)
                self.assertFalse(u['active'])
                self.assertNotEqual(row['pos'][1], initial_y)
                self.assert_snapshot_matches(h, oid)
                h.players_provider[7] = player(row['pos'][0]/256, row['pos'][1]/256+500)
                for _ in range(40):
                    h.step(.05)
                    floor = h.actors[support]
                    top = surface((*floor['pos'], floor['props']['wmp'], floor['props']['hmp']),
                                  floor['props'].get('shapetype', 0), row['pos'][0], row['props']['wmp'])
                    self.assertIsNotNone(top)
                    self.assertAlmostEqual(row['pos'][1]+row['props']['hmp'], top, delta=1.1)
                    self.assertEqual(u['support'], support)
                    self.assert_snapshot_matches(h, oid)
                self.assertTrue(u['active'])
                self.assertGreater(h.enemies.counts['attacks'], 0)
                self.assertGreater(u['busy'], h.room_time, 'The snapshot also follows the platform during the laser hold')

    def test_tank_lane_survives_large_slices_lower_neighbor_and_imported_move(self):
        objects = {80: terrain(0, 256, 512), 81: terrain(512, 272, 1024),
                   90: terrain(-1000, 800, 3000, 1), 1: creature('sc.tank', 300, 128)}
        objects[1]['move'] = dict(vel=dict(xvel=10000, yvel=50000))
        h = host(objects=objects)
        h.enemies.units[1]['next'] = 100.
        h.players_provider[7] = player(-300, 700)
        for i in range(30):
            h.players_provider[7]['xmp'] = (-300 if i % 2 else 900)*256
            h.step(.2)
            row = h.actors[1]
            self.assertEqual(row['pos'][1], 128*256)
            self.assertGreaterEqual(row['pos'][0], 0)
            self.assertLessEqual(row['pos'][0]+row['props']['wmp'], 512*256)
            self.assertEqual(h.enemies.units[1]['support'], 80)
            self.assert_snapshot_matches(h, 1)

    def test_tank_crosses_equal_height_seams_on_one_continuous_floor(self):
        h = host(objects={80: terrain(0, 256, 512), 81: terrain(512, 256, 512),
                          1: creature('sc.tank', 300, 128)})
        h.enemies.units[1]['next'] = 100.
        h.players_provider[7] = player(800, 256)
        self.advance(h, 3.)
        self.assertGreater(h.actors[1]['pos'][0], 600*256)
        self.assertEqual(h.actors[1]['pos'][1], 128*256)
        self.assert_snapshot_matches(h, 1)

    def skeleton_host(self, rise=128, wall=False):
        objects = {90: terrain(-1000, 800, 3000, 1),
                   1: creature('sc.skeleton', 0, 672)}
        if not wall:
            objects[80] = terrain(100, 800-rise, 400)
        else:
            objects[81] = dict(terrain(128, 600, 32, 1), hmp=200*256)
        h = host(objects=objects)
        h.enemies.units[1]['next'] = 100.
        return h

    def test_skeleton_wall_and_airborne_player_do_not_request_a_jump(self):
        h = self.skeleton_host(wall=True)
        h.players_provider[7] = player(300, 800, pose=5)
        self.advance(h, 2.)
        self.assertTrue(h.enemies.units[1]['grounded'])
        self.assertFalse(any(e.get('name') == 'sc.skeletonjump' for e in h.events))
        h = self.skeleton_host()
        h.players_provider[7] = player(200, 672, pose=3)
        self.advance(h, 1.)
        self.assertTrue(h.enemies.units[1]['grounded'])
        self.assertFalse(any(e.get('name') == 'sc.skeletonjump' for e in h.events))

    def test_skeleton_confirms_upper_platform_then_uses_a_short_clear_arc(self):
        for rise, landing_limit in ((128, 1.15), (640, 1.7)):
            with self.subTest(rise=rise):
                h = self.skeleton_host(rise)
                h.players_provider[7] = player(200, 800-rise, pose=5)
                self.advance(h, .1)
                self.assertTrue(h.enemies.units[1]['grounded'], 'A new height sample is not an instant jump decision')
                started = None
                landed = None
                for _ in range(60):
                    h.step(.05)
                    u = h.enemies.units[1]
                    if not u['grounded'] and started is None:
                        started = h.room_time
                    if started is not None and u['grounded'] and u['support'] == 80:
                        landed = h.room_time
                        break
                self.assertIsNotNone(started)
                self.assertIsNotNone(landed)
                self.assertLess(landed-started, landing_limit)
                self.assertAlmostEqual(h.actors[1]['pos'][1], (800-rise-128)*256, delta=1.)
                self.assert_snapshot_matches(h, 1)

    def test_golem_wake_and_attack_share_the_475px_body_edge(self):
        h = host(objects={90: terrain(-1000, 256, 3000, 1),
                          1: creature('sc.golem', 0, 0)})
        h.players_provider[7] = player(160+476-15, 168, pose=5)
        self.advance(h, .6)
        self.assertFalse(h.enemies.units[1]['active'])
        self.assertEqual(h.enemies.counts['attacks'], 0)
        h.players_provider[7] = player(160+475-15, 168, pose=5)
        h.step(.05)
        self.assertTrue(h.enemies.units[1]['active'])
        self.assertEqual(h.enemies.counts['attacks'], 1)
        self.assert_snapshot_matches(h, 1)


if __name__ == '__main__':
    unittest.main()
