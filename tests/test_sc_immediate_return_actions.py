"""A short revisit cancels departed attacks just like a longer absence."""
import unittest

from test_sc_ground_platform_regressions import host
from test_sc_platform_return import floating_objects, return_to_room


class ScImmediateReturnActionTests(unittest.TestCase):
    def test_short_revisit_cannot_emit_damage_after_its_warning_was_removed(self):
        for elapsed in (0., .05, .1):
            with self.subTest(elapsed=elapsed):
                h = host('scmid13')
                oid = next(i for i, row in h.actors.items() if row['vid'] == 'sc.tank')
                point = h.actors[oid]['pos']
                h.enemies._laser(oid, (point[0]+300000, point[1]))
                h.step(.01)
                self.assertTrue(any(e['kind'] == 'laser_damage' for e in h.enemies.pending))
                self.assertTrue(any(r['vid'] == 'sc.aim' for r in h.actors.values()))
                before = h.room_time
                return_to_room(h, elapsed)
                self.assertAlmostEqual(h.room_time, before+elapsed)
                self.assertFalse(any(r['vid'] in ('sc.aim', 'sc.aimend') for r in h.actors.values()))
                self.assertFalse(any(e['kind'] == 'laser_damage' for e in h.enemies.pending))
                h.step(1.01)
                volumes = [oid for oid, row in h.actors.items()
                           if row['vid'] == '' and row['props'].get('damage') == 35]
                self.assertFalse(volumes, 'A discarded warning cannot leave a delayed invisible beam')
                self.assertFalse(any(row['props'].get('vid_', '') == ''
                    and row['props'].get('damage_') == 35 for row in h.instance.snapshot.objects.values()))

    def test_zero_elapsed_return_does_not_repeat_the_previous_platform_carry(self):
        h = host(objects=floating_objects('sc.tank'))
        h.step(.05)
        self.assertTrue(h.mechanics.platform_deltas)
        before = tuple(h.actors[1]['pos'])
        return_to_room(h, 0.)
        self.assertEqual(tuple(h.actors[1]['pos']), before)
        for actual, expected in zip(h.instance.snapshot.position((1, 1)), before):
            self.assertAlmostEqual(actual, expected, delta=1.1)


if __name__ == '__main__':
    unittest.main()
