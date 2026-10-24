"""Room-return and respawn geometry through real actors and reconnect snapshots."""
import unittest
from types import SimpleNamespace

from localserver.maps.sc_run.mechanic_helpers import place
from localserver.maps.sc_run.physics import rect, surface
from localserver.maps.sc_run.session import ScRunLiveSession
from test_sc_ground_platform_regressions import creature, host, terrain


def floating_objects(vid='sc.skeleton'):
    body = creature(vid, 128, 0)
    body['ymp'] = 512*256-body['hmp']
    platform = dict(vid='sc.fledge512', xmp=0, ymp=512*256, wmp=1024*256,
                    hmp=16*256, terraintype=2, move=dict(sin=dict(
                        xmp=0, ymp=512*256, angle=0, vel=0, period=3700,
                        amp=96*256)))
    return {80: platform, 90: terrain(-1000, 1000, 3000, 1), 1: body}


def return_to_room(runtime, elapsed):
    # The genuine reconnect entry point ages the snapshot and room/wire
    # clocks, then applies controller changes before CreateWorld is produced.
    runtime.instance.runtime = runtime
    run = SimpleNamespace(_room_seen={runtime.instance.role: 100.},
                          _clock=lambda: 100.+elapsed, gens=set())
    ScRunLiveSession.initial_snapshot(runtime.server, run, runtime.instance)


class ScPlatformReturnTests(unittest.TestCase):
    def assert_supported_snapshot(self, h, oid, support=80):
        row, u = h.actors[oid], h.enemies.units[oid]
        platform = h.actors[support]
        top = surface(rect(platform), platform['props'].get('shapetype', 0),
                      row['pos'][0], row['props']['wmp'])
        self.assertEqual(u['support'], support)
        self.assertTrue(u['grounded'])
        self.assertAlmostEqual(row['pos'][1]+row['props']['hmp'], top, delta=1.1)
        for actual, expected in zip(h.instance.snapshot.position((1, oid)), row['pos']):
            self.assertAlmostEqual(actual, expected, delta=1.1)

    def test_return_carries_living_ground_bodies_before_the_first_frame(self):
        for vid in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'):
            with self.subTest(vid=vid):
                h = host(objects=floating_objects(vid))
                h.step(.05)
                before = tuple(h.actors[1]['pos'])
                return_to_room(h, 2.7)
                self.assertNotEqual(tuple(h.actors[1]['pos']), before)
                self.assert_supported_snapshot(h, 1)
                h.enemies.units[1]['active'] = True
                h.step(.05)
                self.assert_supported_snapshot(h, 1)

    def test_native_room_floaters_are_carried_in_the_reconnect_snapshot(self):
        h = host('scmid13')
        oid = next(oid for oid, u in h.enemies.units.items() if u['vid'] == 'sc.tank'
                   and h.actors.get(u.get('support'), {}).get('vid', '').startswith('sc.fledge'))
        support = h.enemies.units[oid]['support']
        h.step(.05)
        return_to_room(h, .8)
        self.assert_supported_snapshot(h, oid, support)

    def test_timed_respawn_follows_original_home_platform_phase(self):
        for vid in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'):
            with self.subTest(vid=vid):
                h = host(objects=floating_objects(vid))
                h.step(.05)
                h.feedback(dict(kind='destroy', objid=1, reason='HITPOINTS', seq=1))
                h.step(119.9)
                self.assertEqual(h.enemies.counts['respawns'], 0)
                h.step(.2)
                self.assertEqual(h.enemies.counts['respawns'], 1)
                oid = next(iter(h.enemies.units))
                self.assertNotEqual(oid, 1)
                self.assert_supported_snapshot(h, oid)
                self.assertAlmostEqual(h.actors[oid]['pos'][0]-h.actors[80]['pos'][0], 128*256)

    def test_return_carries_survivor_before_creating_a_due_respawn(self):
        objects = floating_objects()
        objects[2] = creature('sc.golem', 600, 256)
        h = host(objects=objects)
        h.step(.05)
        h.feedback(dict(kind='destroy', objid=1, reason='HITPOINTS', seq=1))
        h.step(.01)
        return_to_room(h, 121.123)
        oid = next(oid for oid in h.enemies.units if oid != 2)
        self.assert_supported_snapshot(h, 2)
        self.assert_supported_snapshot(h, oid)
        self.assertAlmostEqual(h.actors[oid]['pos'][0]-h.actors[80]['pos'][0], 128*256)
        self.assertEqual(h.enemies.counts['respawns'], 1)

    def test_removed_birth_platform_uses_a_legal_floor_below_home(self):
        h = host(objects=floating_objects('sc.tank'))
        h.step(.05)
        h.feedback(dict(kind='destroy', objid=1, reason='HITPOINTS', seq=1))
        h.mechanics.retire(80, 'INTERNAL')
        h.step(120.1)
        oid = next(iter(h.enemies.units))
        self.assert_supported_snapshot(h, oid, 90)

    def test_horizontal_birth_anchor_survives_death_on_a_different_floor(self):
        objects = floating_objects()
        objects[80]['move'] = dict(vel=dict(xvel=10000))
        h = host(objects=objects)
        h.step(1.)
        self.assertGreater(h.actors[80]['pos'][0], 0)
        # The creature can die after leaving its original platform. The
        # current death support must not replace its recorded birth anchor.
        place(h, 1, (-500*256, 1000*256-h.actors[1]['props']['hmp']))
        h.enemies.units[1].update(support=90, grounded=True)
        h.feedback(dict(kind='destroy', objid=1, reason='HITPOINTS', seq=1))
        h.step(120.1)
        oid = next(iter(h.enemies.units))
        self.assert_supported_snapshot(h, oid, 80)
        self.assertAlmostEqual(h.actors[oid]['pos'][0]-h.actors[80]['pos'][0], 128*256)


if __name__ == '__main__':
    unittest.main()
