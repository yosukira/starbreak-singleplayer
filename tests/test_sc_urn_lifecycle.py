"""SC surn production is independent from creature activation and targeting."""
import unittest

from test_sc_enemies import Host


class ScUrnLifecycleTests(unittest.TestCase):
    def make_urn(self, count=3):
        h = Host(dict(sc_surn_counts={'1': count}, sc_ghost_forms=['sc.sniper']))
        h.add(1, 'sc.surn', (0, 0))
        h.enemies.prepare()
        return h

    def test_fresh_urn_emits_one_three_child_batch_without_player_telemetry(self):
        h = self.make_urn()
        h.advance(.1)
        self.assertFalse(h.enemies.units, 'The emission cue precedes its child')
        h.advance(.65)
        urn = h.enemies.urns[1]
        self.assertEqual(len(h.enemies.units), 3)
        self.assertEqual(set(urn['slots']), {0, 1, 2})
        children = set(urn['slots'].values())
        h.advance(8.)
        self.assertEqual(set(h.enemies.units), children, 'Living slots never refill on a timer')
        self.assertFalse(urn['due'])

    def test_explicit_seven_child_urn_works_with_every_player_far_away(self):
        h = self.make_urn(7)
        h.player_at(-8000, 0, key=7)
        h.player_at(8000, 0, key=9)
        h.advance(1.65)
        self.assertEqual(len(h.enemies.units), 7)
        self.assertEqual(set(h.enemies.urns[1]['slots']), set(range(7)))
        self.assertTrue(all(h.actors[oid]['creator'] == 1 for oid in h.enemies.units))
        self.assertTrue(all(u['target'] is None for u in h.enemies.units.values()))

    def test_birth_uses_current_urn_socket_after_its_animation_started(self):
        h = self.make_urn()
        h.enemies._urn_step(0., {})
        event = h.enemies.pending[0]
        h.actors[1]['pos'] = [320*256, 500*256]
        h.enemies.now = event['at']
        h.enemies._emit(event)
        child = h.enemies.urns[1]['slots'][0]
        self.assertEqual(h.actors[child]['pos'], [321*256, 482*256])
        self.assertEqual(h.enemies.units[child]['lineage'], (1, 0))

    def test_prepare_and_repeated_room_return_keep_the_same_live_slots(self):
        h = self.make_urn()
        h.advance(.75)
        children = dict(h.enemies.urns[1]['slots'])
        for elapsed in (30., 120., 1.):
            h.enemies.prepare()
            h.room_time += elapsed
            h.enemies.resume(elapsed)
            h.advance(.3)
            self.assertEqual(h.enemies.urns[1]['slots'], children)
            self.assertEqual(set(h.enemies.units), set(children.values()))

    def test_interrupted_birth_return_fills_each_reserved_slot_once(self):
        h = self.make_urn()
        h.advance(.05)
        self.assertEqual(h.enemies.urns[1]['slots'], {0: None})
        h.room_time += 3.
        h.enemies.resume(3.)
        h.advance(.75)
        children = dict(h.enemies.urns[1]['slots'])
        self.assertEqual(len(h.enemies.units), 3)
        h.enemies.resume(0.)
        h.advance(.75)
        self.assertEqual(h.enemies.urns[1]['slots'], children)

    def test_actual_manifest_keeps_slot_until_death_then_refills_once_after_120s(self):
        h = self.make_urn()
        h.player_at(100, 0)
        h.advance(3.5)
        bodies = [oid for oid, row in h.actors.items()
                  if row['vid'] == 'sc.sniper' and row['hp'] == 750]
        self.assertEqual(len(bodies), 3)
        victim = bodies[0]
        urn_id, slot = h.enemies.units[victim]['lineage']
        urn = h.enemies.urns[urn_id]
        self.assertEqual(urn['slots'][slot], victim)
        h.enemies.feedback(dict(kind='damage', victim=victim, value=750, seq=81))
        due = urn['due'][slot]
        self.assertAlmostEqual(due, h.room_time+120.)
        h.enemies.feedback(dict(kind='destroy', objid=victim, reason=2, seq=82))
        self.assertEqual(urn['due'][slot], due, 'Duplicate termination cannot reset a deadline')
        self.assertFalse(h.enemies.respawns, 'A known urn child must not also use direct body respawn')
        survivors = dict(urn['slots'])
        h.players.clear()
        h.room_time = due-.01
        h.enemies.resume(119.99)
        h.enemies.step(0.)
        self.assertNotIn(slot, urn['slots'])
        h.room_time = due
        h.enemies.resume(.01)
        h.advance(.2)
        replacement = urn['slots'][slot]
        self.assertNotEqual(replacement, victim)
        self.assertEqual(h.actors[replacement]['vid'], 'sc.hunter')
        self.assertEqual(h.enemies.units[replacement]['lineage'], (urn_id, slot))
        self.assertEqual({s: oid for s, oid in urn['slots'].items() if s != slot}, survivors)
        h.enemies.resume(0.)
        h.advance(1.)
        self.assertEqual(urn['slots'][slot], replacement)
        self.assertEqual(len(h.enemies.units), 3)

    def test_initial_snapshot_without_creator_does_not_claim_the_nearest_urn(self):
        h = self.make_urn()
        h.add(2, 'sc.surn', (1000*256, 0))
        h.add(20, 'sc.hunter', (1*256, -18*256))
        h.add(21, 'sc.sniper', (1001*256, -18*256))
        h.enemies.prepare()
        self.assertNotIn('lineage', h.enemies.units[20])
        self.assertNotIn('lineage', h.enemies.units[21])
        self.assertFalse(h.enemies.urns[1]['slots'])
        self.assertFalse(h.enemies.urns[2]['slots'])

    def test_explicit_existing_child_occupies_its_urn_without_adding_an_extra_slot(self):
        h = self.make_urn()
        h.add(20, 'sc.hunter', (5000*256, 0), creator=1)
        h.enemies.prepare()
        self.assertEqual(h.enemies.units[20]['lineage'], (1, 0))
        h.advance(.75)
        self.assertEqual(len(h.enemies.units), 3)
        self.assertEqual(h.enemies.urns[1]['slots'][0], 20)


if __name__ == '__main__':
    unittest.main()
