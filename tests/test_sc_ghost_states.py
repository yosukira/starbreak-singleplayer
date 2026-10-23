"""SC ghost action states against live target and collision changes."""
import unittest

from test_sc_enemies import Host


class GhostStateTests(unittest.TestCase):
    @staticmethod
    def scout_host():
        h = Host()
        h.add(1, 'sc.scout', (0, 0))
        h.player_at(400, 0, key=7)
        h.enemies.prepare()
        return h

    def test_floor_blocked_scout_can_retry_without_waiting_for_airborne_cooldown(self):
        h = self.scout_host()
        h.floor(64)
        h.advance(.2)
        self.assertEqual(h.enemies.counts['attacks'], 1)
        self.assertEqual(h.enemies.units[1]['dive_until'], 0.)
        h.advance(.3)
        self.assertEqual(h.enemies.counts['attacks'], 1,
                         'Floor contact must not create an immediate attack loop')
        h.advance(.2)
        self.assertEqual(h.enemies.counts['attacks'], 2,
                         'A floor-blocked dive can retry in the short observed interval')
        self.assertEqual(h.actors[1]['vid'], 'sc.scout')
        self.assertEqual(h.actors[1]['hp'], 750)

    def test_airborne_scout_keeps_separate_longer_cooldown(self):
        h = self.scout_host()
        h.advance(.7)
        self.assertEqual(h.enemies.counts['attacks'], 1,
                         'Floor retry evidence does not justify rapid airborne dives')

    def test_side_wall_contact_does_not_use_floor_retry(self):
        h = self.scout_host()
        h.add(80, 'sc.wall', (64*256, -300*256),
              changes=dict(wmp=32*256, hmp=2000*256, terraintype=1))
        h.advance(.7)
        self.assertEqual(h.enemies.counts['attacks'], 1,
                         'A side-wall collision has no proven short retry rule')


if __name__ == '__main__':
    unittest.main()
