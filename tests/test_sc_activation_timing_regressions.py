"""Fresh activation, committed monk preparation and urn manifestation timing."""
import unittest

from test_sc_enemies import Host
import test_sc_run as sc_run_test


class ScActivationTimingRegressions(unittest.TestCase):
    def monk_host(self, mode='focused'):
        h = Host(dict(sc_enemy_fit=dict(monk_mode_choices=[mode],
            species={'sc.monk': dict(speed=0.)})))
        h.add(1, 'sc.monk', (0, 0))
        h.enemies.prepare()
        return h

    def test_distant_fresh_monks_keep_their_own_activation_gate(self):
        h = self.monk_host()
        h.add(2, 'sc.monk', (2000*256, 0))
        h.enemies.add_actor(2)
        h.player_at(1400, 0)
        h.advance(3.)
        self.assertFalse(h.enemies.units[1]['active'])
        self.assertEqual(h.actors[1]['pos'], [0, 0])
        self.assertFalse(h.enemies.units[1]['orbs'])
        self.assertTrue(h.enemies.units[2]['active'])
        self.assertTrue(h.enemies.units[2]['orbs'])
        h.player_at(500, 0)
        h.advance(.1)
        self.assertTrue(h.enemies.units[1]['active'])
        self.assertTrue(h.enemies.units[1]['orbs'])
        h.player_at(5000, 0)
        h.advance(.1)
        self.assertTrue(h.enemies.units[1]['active'], 'Leaving range does not reset prior activation')

    def test_orbit_refill_does_not_fire_before_committed_preparation_finishes(self):
        h = self.monk_host()
        h.player_at(100, 0)
        h.advance(2.)
        u = h.enemies.units[1]
        self.assertEqual(len(u['orbs']), 8)
        self.assertTrue(u['monk_firing'])
        self.assertEqual(h.enemies.counts['monk_volleys'], 0)
        prepared_aim = u['monk_aim']
        due = u['monk_prepare_until']
        h.player_at(-400, 0)
        h.advance(due-h.room_time-.05)
        self.assertTrue(all(h.enemies.projectiles.shots[o]['orbit'] is not None for o in u['orbs']))
        self.assertEqual(u['monk_aim'], prepared_aim)
        h.advance(.1)
        fired = [s for s in h.enemies.projectiles.shots.values() if s['orbit'] is None]
        self.assertEqual(len(fired), 8)
        self.assertTrue(all(s['vx'] > 0 for s in fired), 'Prepared aim does not follow a later target reversal')
        self.assertEqual(h.enemies.counts['monk_volleys'], 1)

    def test_full_orbits_wait_for_a_nearby_target_before_starting_preparation(self):
        h = self.monk_host()
        h.enemies.units[1]['active'] = True
        h.advance(10.)
        u = h.enemies.units[1]
        self.assertEqual(len(u['orbs']), 8)
        self.assertFalse(u.get('monk_firing'))
        h.player_at(100, 0)
        h.advance(.7)
        self.assertTrue(u['monk_firing'])
        self.assertGreater(u['monk_prepare_until'], h.room_time+3.)
        self.assertEqual(h.enemies.counts['monk_volleys'], 0)

    def test_refill_and_preparation_form_the_native_seven_to_eight_second_cycle(self):
        h = self.monk_host('radial')
        h.player_at(100, 0)
        releases = []
        for _ in range(300):
            before = h.enemies.counts['monk_volleys']
            h.advance(.05)
            if h.enemies.counts['monk_volleys'] != before:
                releases.append(h.room_time)
            if len(releases) == 2:
                break
        self.assertEqual(len(releases), 2)
        self.assertGreaterEqual(releases[1]-releases[0], 7.6)
        self.assertLess(releases[1]-releases[0], 8.1)

    def test_urn_birth_animation_does_not_add_a_second_manifestation_lock(self):
        h = Host(dict(sc_ghost_forms=['sc.sniper']))
        h.add(1, 'sc.surn', (0, 0))
        h.enemies.prepare()
        h.player_at(50, 0)
        h.advance(.3)
        self.assertGreater(h.enemies.counts['manifests'], 0)
        self.assertTrue(any(u['vid'] == 'sc.sniper' and u['phase'] == 'manifest'
                            for u in h.enemies.units.values()))
        self.assertFalse(any(h.actors[oid]['vid'] == 'sc.sniper' and h.actors[oid]['hp'] is not None
                             for oid in h.enemies.units))
        self.assertTrue(any(r.get('modifyprops_', {}).get('property_') == 49
                            for r in h.batch.objects))

    def test_actual_room_wire_keeps_orbits_until_release_and_cancels_absent_preparation(self):
        helper = sc_run_test.ScRunTests()
        run, clock = helper.make_run()
        role = next(role for role in run.pack.room_names
                    if run.room(role).recipe['metadata']['template'] == 'scmid04')
        wrapper, server = helper.open_room(run, role)
        runtime = wrapper.current
        monk = next(oid for oid, u in runtime.enemies.units.items() if u['vid'] == 'sc.monk')
        u = runtime.enemies.units[monk]
        u.update(active=True, refill_at=runtime.room_time)
        # No native target is injected: build the parent's real orbit objects,
        # then explicitly commit one weapon preparation at the current clock.
        for _ in range(40):
            wrapper.step(.05)
        self.assertEqual(len(u['orbs']), 8)
        runtime.enemies._monk_attack(monk, u, (runtime.actors[monk]['pos'][0]+100*256,
                                                runtime.actors[monk]['pos'][1]), delay=4.)
        orbs = set(u['orbs'])
        server.sent.clear()
        for _ in range(20):
            wrapper.step(.05)
        self.assertTrue(all(runtime.enemies.projectiles.shots[o]['orbit'] is not None for o in orbs))
        for oid in orbs:
            native = wrapper.instance.snapshot.position((1, oid))
            for actual, expected in zip(runtime.actors[oid]['pos'], native):
                self.assertAlmostEqual(actual, expected, delta=1)
        helper.open_room(run, 'entry', server)
        clock.advance(10.)
        run.current, run.entry = role, 'default'
        sc_run_test.ScRunLiveSession.initial_snapshot(server, run, run.room(role))
        self.assertFalse(u['monk_firing'])
        self.assertFalse(any(e['kind'] == 'monk_release' and e['owner'] == monk
                             for e in runtime.enemies.pending))
        self.assertTrue(orbs <= set(runtime.enemies.projectiles.shots))


if __name__ == '__main__':
    unittest.main()
