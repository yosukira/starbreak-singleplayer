"""SC ghost transitions across targeting, collision and damage states."""
import unittest

from test_sc_enemies import Host
from localserver.maps.sc_run.abilities import center


class GhostRegressions(unittest.TestCase):
    def host(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=392, blocktype=2))
        h.player_at(100, 0)
        h.enemies.prepare()
        return h

    def test_first_manifest_keeps_authored_collision_until_ghost_and_burst(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0), changes=dict(hitpoints=None, blocktype=2))
        h.enemies.prepare()
        h.advance(.85)
        self.assertEqual(h.actors[1]['hp'], 750)
        self.assertEqual(h.actors[1]['props']['blocktype'], 2)

    def test_ghost_preserves_injury_and_returns_without_an_attack_if_target_leaves(self):
        h = self.host()
        u = h.enemies.units[1]
        h.enemies._assassin_ghost(1, u)
        self.assertIsNone(h.actors[1]['hp'])
        self.assertIsNone(h.actors[1]['props']['hitpoints'])
        h.players.clear()
        h.advance(.4)
        self.assertEqual(h.actors[1]['hp'], 392)
        self.assertEqual(h.actors[1]['vid'], 'sc.assassin')
        self.assertEqual(h.enemies.counts['bursts'], 0)

    def test_ghost_keeps_far_target_and_does_not_burst_after_a_random_leg_budget(self):
        h = self.host()
        h.player_at(6000, 0)
        u = h.enemies.units[1]
        u.update(active=True, target=1, target_point=center(h.players[1]))
        h.enemies._assassin_ghost(1, u)
        h.advance(8.)
        self.assertEqual(u['target'], 1)
        self.assertEqual(h.actors[1]['vid'], 'sc.eassassin')
        self.assertGreater(u['ghost_moves'], 4)
        self.assertEqual(h.enemies.counts['bursts'], 0)

    def test_ghost_passes_wall_and_only_bursts_near_target_outside_it(self):
        h = self.host()
        h.add(90, 'sc.wall', (300*256, -256*256), changes=dict(wmp=100*256, hmp=768*256, terraintype=1))
        h.player_at(850, 0)
        u = h.enemies.units[1]
        u.update(active=True, target=1, target_point=center(h.players[1]))
        h.enemies._assassin_ghost(1, u)
        for _ in range(100):
            h.advance(.05)
            if h.enemies.counts['bursts']:
                break
        self.assertEqual(h.enemies.counts['bursts'], 1)
        self.assertGreater(h.actors[1]['pos'][0], 400*256)
        self.assertEqual(h.actors[1]['hp'], 392)

    def test_visible_body_can_start_next_ghost_within_recorded_short_interval(self):
        h = self.host()
        u = h.enemies.units[1]
        u.update(active=True, target=1, target_point=center(h.players[1]), saved_hp=392)
        h.enemies._assassin_burst(1, u)
        h.advance(1.85)
        self.assertEqual(h.actors[1]['vid'], 'sc.eassassin')


if __name__ == '__main__':
    unittest.main()
