"""Continuous upper-platform observation through natural melee busy frames."""
import unittest

from test_sc_ground_platform_regressions import creature, host, player, terrain


class ScSkeletonPlatformDwellTests(unittest.TestCase):
    def skeleton(self):
        h = host(objects={90: terrain(-1000, 800, 3000, 1),
            80: terrain(80, 672, 400), 1: creature('sc.skeleton', 0, 672)})
        h.players_provider[7] = player(100, 672, pose=5)
        h.step(.05)
        self.assertIsNotNone(h.enemies.units[1].get('jump_candidate'))
        h.step(.05)
        self.assertGreater(h.enemies.units[1]['busy'], h.room_time)
        self.assertTrue(h.enemies.units[1]['grounded'])
        return h

    def assert_snapshot(self, h):
        for actual, expected in zip(h.instance.snapshot.position((1, 1)), h.actors[1]['pos']):
            self.assertAlmostEqual(actual, expected, delta=1.1)

    def test_leaving_during_melee_invalidates_contact_and_return_restarts_full_dwell(self):
        for returns in (False, True):
            with self.subTest(returns=returns):
                h = self.skeleton()
                u = h.enemies.units[1]
                h.players_provider[7] = player(100, 500, pose=3, grounded=False)
                for _ in range(8):
                    h.step(.05)
                    self.assertIsNone(u.get('jump_candidate'))
                    self.assertTrue(u['grounded'])
                if returns:
                    h.players_provider[7] = player(100, 672, pose=5)
                h.step(.05)
                self.assertTrue(u['grounded'])
                if returns:
                    self.assertAlmostEqual(u['jump_candidate_at'], h.room_time)
                for _ in range(2):
                    h.step(.05)
                    self.assertTrue(u['grounded'])
                h.step(.051)
                self.assertEqual(u['grounded'], not returns)
                self.assert_snapshot(h)

    def test_continuous_target_contact_keeps_its_original_time_through_melee(self):
        h = self.skeleton()
        u = h.enemies.units[1]
        first_contact = u['jump_candidate_at']
        for _ in range(8):
            h.step(.05)
            self.assertEqual(u['jump_candidate_at'], first_contact)
            self.assertTrue(u['grounded'])
        h.step(.05)
        self.assertFalse(u['grounded'])
        self.assertEqual(u['jump_candidate_at'], first_contact)
        self.assert_snapshot(h)


if __name__ == '__main__':
    unittest.main()
