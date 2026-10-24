"""Oct23 capture regressions: platform patrol and committed ghost actions."""
import unittest

from test_sc_enemies import Host


class ScNewCaptureTests(unittest.TestCase):
    def test_golem_soidal_range_calibration_uses_near_body_edge(self):
        from unittest import mock
        for gap, attacks, jumps in ((476, 0, False), (475, 1, False), (449, 1, True)):
            with self.subTest(gap=gap):
                h = Host()
                h.floor(256)
                h.add(1, 'sc.golem', (0, 0))
                h.enemies.prepare()
                h.player_at(160+gap-16, 96)
                u = h.enemies.units[1]
                u.update(active=True, phase='chase', grounded=True, next=0.,
                         target=1, target_point=((160+gap)*256, 128*256))
                with mock.patch.object(h.rng, 'random', return_value=0.):
                    h.enemies._weapons(1, u, h.players[1])
                self.assertEqual(h.enemies.counts['attacks'], attacks)
                self.assertEqual(bool(u.get('slam')), jumps)

    def tank(self):
        h = Host()
        h.add(80, 'sc.ledge', (0, 256*256),
              changes=dict(wmp=400*256, hmp=16*256, terraintype=2))
        h.add(81, 'sc.ledge', (400*256, 272*256),
              changes=dict(wmp=400*256, hmp=16*256, terraintype=2))
        h.add(1, 'sc.tank', (200*256, 128*256))
        h.enemies.prepare()
        u = h.enemies.units[1]
        u.update(active=True, grounded=True, support=80, direction=1, next=100.)
        return h, u

    def test_tank_target_below_does_not_leave_platform_or_flip_on_scan(self):
        h, u = self.tank()
        h.player_at(-100, 500)
        h.advance(.1)
        self.assertEqual(u['direction'], 1, 'Gun target does not steer the patrol')
        xs = []
        for _ in range(160):
            h.advance(.05)
            xs.append(h.actors[1]['pos'][0])
            self.assertAlmostEqual(h.actors[1]['pos'][1], 128*256)
            self.assertEqual(u['support'], 80)
        self.assertGreater(max(xs), 270*256)
        self.assertLess(min(xs), 10*256)

    def test_tank_merges_equal_height_tiles_and_has_no_home_box(self):
        h, u = self.tank()
        h.actors[81]['pos'][1] = 256*256
        h.advance(3.)
        self.assertGreater(h.actors[1]['pos'][0], 600*256)
        self.assertEqual(u['direction'], 1)

    def test_tank_stands_on_platform_narrower_than_its_body(self):
        h, u = self.tank()
        h.actors[80]['props']['wmp'] = 64*256
        h.actors[1]['pos'][0] = 0
        h.advance(1.)
        self.assertEqual(h.actors[1]['pos'][0], 0)
        self.assertEqual(h.actors[1]['pos'][1], 128*256)

    def test_signed_native_walking_speeds(self):
        h = Host()
        for vid, right, left in [('sc.tank', 39000, -40000),
                                 ('sc.skeleton', 54000, -55000),
                                 ('sc.golem', 27000, -28000)]:
            self.assertEqual(h.enemies._ground_speed(vid, 1), right)
            self.assertEqual(h.enemies._ground_speed(vid, -1), left)

    def test_beam_is_in_front_of_platforms_and_foreground_props(self):
        h = Host()
        h.add(1, 'sc.tank', (0, 0))
        h.enemies.prepare()
        h.enemies._laser(1, (500*256, 0))
        visible = [r for r in h.actors.values() if r['vid'] in ('sc.aim', 'sc.aimend')]
        self.assertTrue(visible)
        self.assertTrue(all(-32768 <= r['props']['z'] < -20 for r in visible))

    def test_scout_successful_floor_contact_recovers_without_resetting_cooldown(self):
        h = Host()
        h.floor(400)
        h.add(1, 'sc.scout', (0, 0))
        h.enemies.prepare()
        h.player_at(16, 336)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=1, target_point=(32*256, 368*256), next=0.)
        h.enemies._weapons(1, u, h.players[1])
        deadline = u['next']
        h.advance(.95)
        self.assertEqual(u['scout_phase'], 'recover')
        self.assertLess(u['vy'], 0)
        self.assertEqual(u['next'], deadline)
        self.assertEqual(h.enemies.counts['attacks'], 1)
        # Moving the player above cannot immediately start another dive.
        h.player_at(16, -100)
        h.advance(2.)
        self.assertEqual(h.enemies.counts['attacks'], 1)

    def test_scout_commits_heading_instead_of_steering_mid_dive(self):
        h = Host()
        h.add(1, 'sc.scout', (0, 0))
        h.enemies.prepare()
        h.player_at(300, 500)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=1, target_point=(316*256, 532*256), next=0.)
        h.enemies._weapons(1, u, h.players[1])
        heading = u['dive_heading']
        h.player_at(-300, 500)
        h.advance(.5)
        self.assertEqual(u['dive_heading'], heading)
        self.assertGreater(u['vx'], 0)
        self.assertAlmostEqual((u['vx']**2+u['vy']**2)**.5, 175000.)

    def test_assassin_visible_body_waits_and_ethereal_step_can_cross_long_gap(self):
        h = Host()
        h.add(1, 'sc.assassin', (0, 0))
        h.enemies.prepare()
        h.player_at(1500, 0)
        u = h.enemies.units[1]
        u.update(active=True, phase='chase', target=1, target_point=(1516*256, 32*256),
                 scan=100., next=100.)
        h.advance(.4)
        self.assertEqual(h.actors[1]['pos'], [0, 0])
        h.enemies._assassin_ghost(1, u)
        h.advance(.35)
        motion = u['ghost_motion']
        self.assertGreater(motion['end'][0], 1400*256)
        self.assertLess(motion['duration'], 1.)

    def test_hunter_diagonal_activation_accelerates_and_does_not_reverse_instantly(self):
        h = Host()
        h.add(1, 'sc.hunter', (0, 0))
        h.enemies.prepare()
        h.player_at(1266, 1391)
        u = h.enemies.units[1]
        h.advance(.05)
        self.assertTrue(u['active'])
        self.assertIsNotNone(u['target'])
        self.assertAlmostEqual((u['vx']**2+u['vy']**2)**.5, 4000.)
        h.advance(.5)
        previous = u['vx']
        h.player_at(-1266, -1391)
        u['scan'] = 0.
        h.advance(.05)
        self.assertGreater(u['vx'], 0.)
        self.assertLess(u['vx'], previous)

    def test_monk_patrols_two_dimensions_without_chasing_out_of_its_home_area(self):
        h = Host()
        h.add(1, 'sc.monk', (0, 0))
        h.enemies.prepare()
        h.player_at(1000, 1000)
        u = h.enemies.units[1]
        u.update(active=True, next=100.)
        ys = []
        for index in range(400):
            h.player_at(1000 if index < 200 else -1000, 1000)
            h.advance(.05)
            x, y = h.actors[1]['pos']
            self.assertLessEqual(abs(x), 210*256)
            self.assertLessEqual(abs(y), 210*256)
            ys.append(y)
        self.assertGreater(max(ys)-min(ys), 200*256)


class ScNewPlatformStreamTests(unittest.TestCase):
    def test_prompt_independent_dense_stream_and_reentry_reconciliation(self):
        from test_sc_mechanics import Host as MechanismHost, body
        from localserver.maps.sc_run.catalogue import ScContent
        content = ScContent()
        profile = content.recipe('scmid01')['metadata']['sc_mledge_stream']
        h = MechanismHost({1: body('sc.mledge64', w=64, h=96, terraintype=2)},
                          dict(sc_mledge_stream=profile))
        h.pack.props = content.props
        self.assertFalse(h.actors, 'Captured resident stock is not fresh-room stock')
        h.tick(2.)
        self.assertFalse(h.actors)
        h.player_at(0, 0)
        h.tick(.05)
        self.assertEqual(len(h.actors), 2)
        for row in h.actors.values():
            vx = row['props']['move']['vel']['xvel']
            if vx > 0:
                self.assertEqual(row['pos'][0]+row['props']['wmp'], 303104)
            else:
                self.assertEqual(row['pos'][0], 1646592)
        for _ in range(600):
            h.tick(.1)
        self.assertGreater(len(h.actors), 45, 'Independent births remove the old 38-body density cap')
        self.assertLess(len(h.actors), 130)
        h.players.clear()
        h.player = None
        h.room_time += 90.
        h.mechanics.resume(90.)
        self.assertGreater(len(h.actors), 45, 'Re-entry restores the ongoing stream rather than emptying it')
        self.assertLess(len(h.actors), 130, 'Expired offline births are never restored')


if __name__ == '__main__':
    unittest.main()
