"""Regression examples for SC's moving terrain mechanisms.

The host is already an active room; preloaded rooms do not invoke this step.
"""
import unittest

from test_sc_mechanics import Host, MP, body


class ScMeatballMechanismTests(unittest.TestCase):
    def test_active_room_starts_far_nests_together_without_player_range_gate(self):
        for with_player in (False, True):
            with self.subTest(with_player=with_player):
                # Fresh native scmid03 coordinates, including a nest whose
                # centre is >4400px from the recorded player's centre.
                points = [(2624, 768), (576, -768), (2176, 960), (-192, -704),
                          (-448, -704), (1216, -576), (-960, -704), (2368, 256),
                          (1472, -320), (1728, -64), (2752, 1216), (1216, 960),
                          (320, 640), (64, 640), (1472, 2240), (2112, 1536),
                          (-192, 320), (1600, 2432), (-448, 256), (2496, 2496)]
                h = Host({oid: body('sc.tshooter', x=x, y=y, w=128, h=128, facing=90)
                          for oid, (x, y) in enumerate(points, 1)})
                if with_player:
                    h.player_at(3229, 944)
                h.tick(.01)
                self.assertEqual([h.mechanics.states[i]['phase'] for i in range(1, 21)], ['windup']*20)
                h.tick(.416)
                children = [r for r in h.actors.values() if r['vid'] == 'sc.tshot']
                self.assertEqual(len(children), 20)
                self.assertEqual({r['creator'] for r in children}, set(range(1, 21)))
                for _ in range(20):
                    h.tick(.1)
                self.assertEqual(sum(r['vid'] == 'sc.tshot' for r in h.actors.values()), 20)

    def test_meatball_accelerates_from_rest_at_native_sample_rate(self):
        h = Host({1: body('sc.tshot', w=128, h=128, facing=0)})
        h.tick(.5)
        # Native samples after birth follow roughly .5*300*t^2 px.
        self.assertAlmostEqual(h.actors[1]['pos'][0]/MP, 37.5)
        self.assertAlmostEqual(h.mechanics.states[1]['speed']/MP, 150.)
        self.assertEqual(h.actors[1]['props']['pose'], 2)

    def test_meatball_reaches_observed_speed_without_frame_rate_drift(self):
        positions = []
        for step in (.02, .1):
            h = Host({1: body('sc.tshot', w=128, h=128, facing=90)})
            for _ in range(round(2/step)):
                h.tick(step)
            positions.append(h.actors[1]['pos'][1])
            self.assertAlmostEqual(h.mechanics.states[1]['speed'], 79000.)
            self.assertEqual(h.actors[1]['props']['pose'], 3)
        self.assertLessEqual(abs(positions[0]-positions[1]), 2)
        maximum = 79000/MP
        expected = maximum*2-maximum**2/(2*300)
        self.assertAlmostEqual(positions[0]/MP, expected, delta=.01)

    def test_meatball_stops_at_corridor_end_for_observed_pause(self):
        h = Host({1: body('sc.tshot', w=128, h=128, facing=0),
                  2: body('sc.twall', x=400, y=-200, w=32, h=600, terraintype=1)})
        for _ in range(100):
            h.tick(.05)
            if h.mechanics.states[1].get('wait_until', 0) > h.room_time:
                break
        state = h.mechanics.states[1]
        self.assertEqual(state['speed'], 0.)
        self.assertIsNone(state['heading'])
        self.assertAlmostEqual(h.actors[1]['pos'][0]/MP, 272., delta=.01)
        self.assertEqual(h.actors[1]['props']['pose'], 1)
        stopped = tuple(h.actors[1]['pos'])
        h.tick(.9)
        self.assertEqual(tuple(h.actors[1]['pos']), stopped)


if __name__ == '__main__':
    unittest.main()
