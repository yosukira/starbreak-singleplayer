"""Authored nested Jump geometry stays consistent with native world snapshots."""
import unittest

import test_sc_run as run_test
from localserver.maps.sc_boss.motion import NativeMotion
from localserver.maps.sc_run.mechanics import JUMP_HAZARDS


class ScNativeGeometryTests(unittest.TestCase):
    def test_ellipse_missing_second_radius_is_circle_but_explicit_zero_is_not(self):
        motion = NativeMotion(0, 0)
        motion.move = dict(elliptical=dict(xmp=1000, ymp=2000, radiusamp=100, period=4000))
        self.assertAlmostEqual(motion.position(1.)[0], 1000)
        self.assertAlmostEqual(motion.position(1.)[1], 2100)
        motion.move['elliptical']['radiusbmp'] = 0
        self.assertAlmostEqual(motion.position(1.)[1], 2000)
        motion.move['elliptical']['radiusbmp'] = -100
        self.assertAlmostEqual(motion.position(1.)[1], 1900)

    def open_jump(self):
        helper = run_test.ScRunTests()
        run, clock = helper.make_run(seed=23)
        wrapper, server = helper.open_room(run, 'mid_jump')
        return helper, run, clock, wrapper, server

    def test_nested_solid_peg_follows_both_orbits_on_server_and_wire(self):
        _helper, _run, _clock, wrapper, _server = self.open_jump()
        runtime = wrapper.current
        # These authored IDs form wheel256 -> wheel128 -> solid peg. At one
        # second the first orbit is a quarter turn, the second a half turn.
        self.assertGreater(runtime.actors[12]['pos'][0], 200000)
        wrapper.step(.999)
        self.assertAlmostEqual(runtime.room_time, 1.)
        base = runtime.actors[10]['pos']
        expected = (base[0]+16384, base[1]+32768+12288)
        for actual, value in zip(runtime.actors[12]['pos'], expected):
            self.assertAlmostEqual(actual, value, delta=1)
        for actual, value in zip(wrapper.instance.snapshot.position((1, 12)), expected):
            self.assertAlmostEqual(actual, value, delta=1)
        self.assertIn(12, runtime.mechanics.platform_deltas)
        self.assertIn('relativeto', runtime.actors[12]['props']['move'])

    def test_wheel_facing_uses_each_authored_signed_period_without_tick_rounding(self):
        _helper, _run, _clock, wrapper, _server = self.open_jump()
        wrapper.step(.409)
        # Original same-clock frame endpoint at 410ms, six differently sized
        # and directed wheels. A shared +90deg/s cannot produce these values.
        expected = {10: 36, 11: 73, 19: -64, 22: -52, 33: 50, 69: -73}
        for oid, facing in expected.items():
            self.assertEqual(wrapper.current.actors[oid]['props']['facing'], facing)
            self.assertEqual(wrapper.instance.snapshot.properties_at((1, oid))['facing_'], facing)

    def test_unfinished_jump_keeps_turning_while_absent_before_first_return_tick(self):
        helper, run, clock, wrapper, server = self.open_jump()
        wrapper.step(.999)
        runtime = wrapper.current
        previous = tuple(runtime.actors[12]['pos'])
        helper.open_room(run, 'entry', server)
        clock.advance(.5)
        run.current, run.entry = 'mid_jump', 'default'
        run_test.ScRunLiveSession.initial_snapshot(server, run, run.room())
        self.assertAlmostEqual(runtime.room_time, 1.5)
        self.assertNotEqual(tuple(runtime.actors[12]['pos']), previous)
        for actual, value in zip(runtime.actors[12]['pos'], run.room().snapshot.position((1, 12))):
            self.assertAlmostEqual(actual, value, delta=1)
        for oid, facing in ((10, 135), (11, 270)):
            self.assertEqual(runtime.actors[oid]['props']['facing'], facing)
            self.assertEqual(run.room().snapshot.properties_at((1, oid))['facing_'], facing)

    def test_jump_switch_freezes_current_world_positions_and_reentry_keeps_them(self):
        helper, run, clock, wrapper, server = self.open_jump()
        wrapper.step(.999)
        runtime = wrapper.current
        frozen = {oid: tuple(row['pos']) for oid, row in runtime.actors.items()
                  if row['vid'] in JUMP_HAZARDS}
        switch = next(oid for oid, row in runtime.actors.items() if row['vid'] == 'sc.switch')
        row = runtime.actors[switch]
        player = dict(xmp=row['pos'][0], ymp=row['pos'][1], wmp=8192, hmp=16384)
        self.assertTrue(runtime.mechanics.interact(switch, player))
        wrapper.step(.05)
        self.assertTrue(wrapper.instance.flags['mid_cleared'])
        for oid, point in frozen.items():
            self.assertNotIn('move', runtime.actors[oid]['props'])
            for actual, value in zip(wrapper.instance.snapshot.position((1, oid)), point):
                self.assertAlmostEqual(actual, value, delta=1)
        helper.open_room(run, 'entry', server)
        clock.advance(3.)
        run.current, run.entry = 'mid_jump', 'default'
        run_test.ScRunLiveSession.initial_snapshot(server, run, run.room())
        for oid, point in frozen.items():
            self.assertNotIn('move_', run.room().snapshot.properties_at((1, oid)))
            for actual, value in zip(runtime.actors[oid]['pos'], point):
                self.assertAlmostEqual(actual, value, delta=1)
            for actual, value in zip(run.room().snapshot.position((1, oid)), point):
                self.assertAlmostEqual(actual, value, delta=1)


if __name__ == '__main__':
    unittest.main()
