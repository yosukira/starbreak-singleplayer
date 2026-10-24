"""Vermin behaviour through ActorSession and the decoded native snapshot."""
import math
import unittest

from test_sc_ground_platform_regressions import creature, host, player, terrain
from localserver.maps.sc_run.physics import clear as geometry_clear

MP = 256


class ScVerminUserFixes(unittest.TestCase):
    def room(self, facing=0, wall=False):
        objects = {1: dict(creature('sc.vermin', 100, 112), facing=facing),
                   80: terrain(-2000, 160, 4000, 1)}
        if wall:
            objects[81] = dict(terrain(220, -100, 48, 1), hmp=260*MP)
        h = host(objects=objects)
        h.mechanics.states[1]['next_hop'] = math.inf
        h.mechanics.states[1]['next_jump'] = math.inf
        return h

    def assert_wire(self, h, oid):
        for actual, simulated in zip(h.instance.snapshot.position((1, oid)), h.actors[oid]['pos']):
            self.assertAlmostEqual(actual, simulated, delta=1.1)

    def test_acquisition_and_player_crossing_never_change_direction(self):
        h = self.room(facing=180)
        h.mechanics.config['vermin_lunge_x_px'] = 0
        for i in range(60):
            h.players_provider[7] = player(450 if i % 2 else -450, 160)
            h.step(.05)
            self.assertEqual(h.mechanics.states[1]['direction'], -1)
            self.assertEqual(h.actors[1]['props']['facing'], 180)
            self.assertFalse(h.mechanics.states[1]['body'].get('blocked'))
            self.assert_wire(h, 1)

    def test_actual_wall_contact_turns_once_in_patrol_and_chase(self):
        for aggro in (False, True):
            with self.subTest(aggro=aggro):
                h = self.room(wall=True)
                h.mechanics.config['vermin_lunge_x_px'] = 0
                if aggro:
                    h.players_provider[7] = player(450, 160)
                for _ in range(20):
                    h.step(.05)
                    state = h.mechanics.states[1]
                    if state['body'].get('blocked'):
                        break
                self.assertTrue(state['body']['blocked'])
                self.assertEqual(state['direction'], -1)
                self.assertEqual(h.actors[1]['pos'][0], 172*MP)
                h.step(.05)
                self.assertLess(h.actors[1]['pos'][0], 172*MP)
                self.assertEqual(state['direction'], -1)
                self.assertFalse(state['body']['blocked'])
                self.assert_wire(h, 1)

    def test_forward_attack_uses_fast_short_native_air_arc_and_contact_damage(self):
        for facing, velocity in ((0, 156000), (180, -157000)):
            with self.subTest(facing=facing):
                h = self.room(facing=facing)
                h.players_provider[7] = player(300 if facing == 0 else -100, 160)
                h.step(.01)
                initial = h.actors[1]['pos'][:]
                h.step(.01)
                self.assertAlmostEqual(h.actors[1]['pos'][0]-initial[0], velocity*.01)
                self.assertEqual(h.actors[1]['props']['pose'], 3)
                self.assertEqual(h.actors[1]['props']['damage'], 20)
                self.assertNotIn('animprops', h.actors[1]['props'])
                state = h.mechanics.states[1]
                deadline = state['next_attack']
                apex, saw_fall, landed = initial[1], False, False
                # Crossing the target after launch cannot steer the attack.
                h.players_provider[7] = player(-100 if facing == 0 else 300, 160)
                for _ in range(80):
                    h.step(.01)
                    row = h.actors[1]
                    apex = min(apex, row['pos'][1])
                    if not state['body']['grounded'] and state['body']['vy'] > 0:
                        saw_fall = True
                        self.assertEqual(row['props']['pose'], 1)
                    landed |= state['body']['grounded']
                    self.assertEqual(state['direction'], -1 if facing else 1)
                    self.assert_wire(h, 1)
                self.assertAlmostEqual((initial[1]-apex)/MP, 52.94, delta=.2)
                self.assertTrue(saw_fall and landed)
                self.assertEqual(state['next_attack'], deadline)
                self.assertEqual(len([e for e in h.events if e['kind'] == 'sc_vermin_lunge']), 1)

    def test_authored_body_in_a_thick_compound_wall_finds_an_opening(self):
        objects = {1: creature('sc.vermin', 100, 112),
                   80: terrain(-500, 160, 1500, 1),
                   81: dict(terrain(0, -100, 256, 1), hmp=260*MP),
                   82: dict(terrain(-96, -100, 96, 1), hmp=260*MP)}
        h = host(objects=objects)
        row, state = h.actors[1], h.mechanics.states[1]
        self.assertGreater(abs(row['pos'][0]-100*MP), 32*MP)
        self.assertTrue(geometry_clear(h, (*row['pos'], row['props']['wmp'], row['props']['hmp']), (1,)))
        self.assertEqual((state['body']['x'], state['body']['y']), tuple(row['pos']))
        h.step(0.)
        self.assert_wire(h, 1)
        h.step(.05)
        self.assertTrue(geometry_clear(h, (*row['pos'], row['props']['wmp'], row['props']['hmp']), (1,)))
        self.assert_wire(h, 1)

    def test_hive_birth_is_clear_and_a_sealed_hive_waits(self):
        for sealed in (False, True):
            with self.subTest(sealed=sealed):
                wall = (dict(terrain(-100, -100, 600, 1), hmp=500*MP) if sealed else
                        dict(terrain(160, 144, 16, 1), hmp=48*MP))
                h = host(objects={1: creature('sc.hive', 0, 0), 81: wall})
                h.players_provider[7] = player(300, 192)
                h.mechanics.states[1]['next'] = 0.
                h.step(.01)
                children = h.mechanics.states[1]['children']
                if sealed:
                    self.assertFalse(children)
                    self.assertGreater(h.mechanics.states[1]['next'], h.room_time)
                else:
                    self.assertEqual(len(children), 1)
                    oid = next(iter(children))
                    row = h.actors[oid]
                    self.assertEqual(row['creator'], 1)
                    self.assertTrue(geometry_clear(h, (*row['pos'], row['props']['wmp'], row['props']['hmp']), (oid,)))
                    self.assert_wire(h, oid)
                    h.mechanics.feedback(dict(kind='damage', victim=oid, value=100, seq=1))
                    self.assertNotIn(oid, h.actors)
                    self.assertFalse(h.enemies.respawns)
                    h.mechanics.states[1]['next'] = h.room_time
                    h.step(.01)
                    self.assertNotIn(oid, h.mechanics.states[1]['children'])
                    self.assertEqual(len(h.mechanics.states[1]['children']), 1)


if __name__ == '__main__':
    unittest.main()
