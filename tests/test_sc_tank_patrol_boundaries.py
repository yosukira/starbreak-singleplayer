"""Tank patrol boundaries through actual ActorSession and native geometry."""
import unittest

from test_sc_ground_platform_regressions import creature, host, player, terrain


class ScTankPatrolBoundaryTests(unittest.TestCase):
    def test_endpoint_and_wall_contact_reverse_once_without_suppressing_normal_wall_turn(self):
        for wall_x, start_x in ((510, 380), (400, 270)):
            with self.subTest(wall_x=wall_x):
                h = host(objects={80: terrain(0, 256, 512),
                    81: dict(terrain(wall_x, 0, 32, 1), hmp=256*256),
                    1: creature('sc.tank', start_x, 128)})
                u = h.enemies.units[1]
                u.update(active=True, next=100., direction=1)
                h.players_provider[7] = player(800, 256)
                h.step(.05)
                contact = h.actors[1]['pos'][0]
                self.assertTrue(u['blocked'])
                self.assertEqual(u['direction'], -1)
                for _ in range(8):
                    h.step(.05)
                self.assertLess(h.actors[1]['pos'][0], contact-40*256)
                self.assertEqual(u['support'], 80)
                self.assertAlmostEqual(h.actors[1]['pos'][1], 128*256)
                self.assertAlmostEqual(h.instance.snapshot.position((1, 1))[0], h.actors[1]['pos'][0], delta=1.1)

    def test_moving_birth_platform_does_not_merge_with_touching_static_floor(self):
        # A non-fledge VID deliberately verifies that motion/track identity,
        # rather than the sprite name, prevents the temporary surface merge.
        moving = dict(terrain(0, 256, 256), vid='sc.ledge',
                      move=dict(sin=dict(xmp=0, ymp=256*256, vel=10000,
                                         angle=0, period=2000, amp=0)))
        h = host(objects={80: moving, 81: terrain(256, 256, 512),
                          1: creature('sc.tank', 120, 128)})
        u = h.enemies.units[1]
        u.update(active=True, next=100., direction=1)
        for _ in range(30):
            h.step(.05)
            self.assertEqual(set(u['patrol_surfaces']), {80})
            self.assertEqual(u['support'], 80)
            relative_x = h.actors[1]['pos'][0]-h.actors[80]['pos'][0]
            self.assertGreaterEqual(relative_x, -1.1)
            self.assertLessEqual(relative_x, 128*256+1.1)
            for actual, expected in zip(h.instance.snapshot.position((1, 1)), h.actors[1]['pos']):
                self.assertAlmostEqual(actual, expected, delta=1.1)


if __name__ == '__main__':
    unittest.main()
