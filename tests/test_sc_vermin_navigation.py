import unittest

from test_sc_mechanics import Host, MP, body


class ScVerminNavigationTests(unittest.TestCase):
    @staticmethod
    def room(*, floor_kind=2, upper=None, lower=None, facing=0, extra=None):
        objects = {1: body('sc.vermin', x=100, y=112, w=48, h=48, facing=facing, hitpoints=100),
                   2: body('sc.tledge', x=-2000, y=160, w=4000, h=32, terraintype=floor_kind)}
        if upper is not None:
            objects[3] = body('sc.tledge', x=-2000, y=upper, w=4000, h=32, terraintype=2)
        if lower is not None:
            objects[4] = body('sc.tledge', x=-2000, y=lower, w=4000, h=32, terraintype=1)
        objects.update(extra or {})
        return Host(objects, dict(mechanics=dict(vermin_hop_s=0., vermin_jump_cooldown_s=0.)))

    def test_signed_fixed_point_walking_matches_both_native_directions(self):
        for facing, velocity in ((0, 78000.), (180, -79000.)):
            with self.subTest(facing=facing):
                h = self.room(facing=facing)
                h.tick(.01)
                start = h.actors[1]['pos'][0]
                h.tick(.1)
                self.assertAlmostEqual(h.actors[1]['pos'][0]-start, velocity*.1)
                self.assertEqual(h.actors[1]['props']['pose'], 5)

    def test_idle_has_no_fabricated_jump_without_a_reachable_landing(self):
        h = self.room(floor_kind=1)
        for _ in range(40):
            h.tick(.05)
            self.assertEqual(h.actors[1]['pos'][1], 112*MP)
            self.assertEqual(h.mechanics.states[1]['body']['vy'], 0.)

    def test_idle_reaches_the_upper_row_and_uses_native_rise_and_fall_poses(self):
        h = self.room(upper=-32)
        saw_rise = saw_fall = False
        landed = False
        for _ in range(120):
            h.tick(.02)
            state = h.mechanics.states[1]['body']
            if state['vy'] < 0:
                saw_rise = True
                self.assertEqual(h.actors[1]['props']['pose'], 3)
            if not state.get('grounded') and state['vy'] > 0:
                saw_fall = True
                self.assertEqual(h.actors[1]['props']['pose'], 1)
            if state.get('grounded') and state.get('support') == 3:
                landed = True
                break
        self.assertTrue(saw_rise and saw_fall and landed)
        self.assertEqual(h.actors[1]['pos'][1], -80*MP)

    def test_two_jump_modes_are_selected_by_the_landing_height(self):
        for top, expected in ((128, 375), (-32, 750)):
            with self.subTest(top=top):
                h = self.room(upper=top)
                h.tick(.01)
                routes = h.mechanics._vermin_routes(1, h.mechanics.states[1]['body'])
                up = next(r for r in routes if r['action'] == 'up')
                self.assertEqual(up['speed']/MP, expected)

    def test_idle_candidates_include_only_reachable_up_and_down_landings(self):
        h = self.room(upper=-32, lower=352)
        h.tick(.01)
        routes = h.mechanics._vermin_routes(1, h.mechanics.states[1]['body'])
        self.assertEqual({r['action'] for r in routes}, {'up', 'down'})
        self.assertEqual(next(r for r in routes if r['action'] == 'down')['landing'], 4)

    def test_target_below_drops_only_through_current_one_way_support(self):
        h = self.room(lower=352)
        h.player_at(112, 400)
        h.tick(.01)
        start_x = h.actors[1]['pos'][0]
        h.tick(.01)
        self.assertEqual(h.mechanics.states[1]['body'].get('drop_support'), 2)
        self.assertGreater(h.actors[1]['pos'][1], 112*MP)
        self.assertEqual(h.actors[1]['props']['pose'], 1)
        for _ in range(100):
            h.tick(.02)
        self.assertEqual(h.actors[1]['pos'][0], start_x)
        self.assertEqual(h.actors[1]['pos'][1], 304*MP)
        self.assertEqual(h.mechanics.states[1]['body'].get('support'), 4)

    def test_solid_support_never_allows_a_drop_to_a_lower_player(self):
        h = self.room(floor_kind=1, lower=352)
        h.player_at(112, 400)
        for _ in range(80):
            h.tick(.02)
        self.assertEqual(h.actors[1]['pos'][1], 112*MP)
        self.assertNotIn('drop_support', h.mechanics.states[1]['body'])

    def test_a_solid_ceiling_rejects_the_upper_landing(self):
        h = self.room(upper=-32, extra={5: body('sc.twall', x=-2000, y=24, w=4000, h=32, terraintype=1)})
        for _ in range(30):
            h.tick(.02)
        self.assertEqual(h.actors[1]['pos'][1], 112*MP)

    def test_a_blocked_chase_has_no_jump_without_a_reachable_landing(self):
        h = self.room(floor_kind=1, extra={5: body('sc.twall', x=200, y=-100, w=32, h=260, terraintype=1)})
        h.player_at(500, -200)
        for _ in range(60):
            h.tick(.02)
            self.assertEqual(h.actors[1]['pos'][1], 112*MP)

    def test_drop_filter_does_not_skip_the_next_one_way_floor(self):
        h = self.room(lower=544, extra={5: body('sc.tledge', x=-2000, y=352, w=4000, h=32, terraintype=2)})
        h.player_at(112, 600)
        h.tick(.01)
        h.mechanics.config['vermin_jump_cooldown_s'] = 10.
        for _ in range(90):
            h.tick(.02)
        self.assertEqual(h.actors[1]['pos'][1], 304*MP)
        self.assertEqual(h.mechanics.states[1]['body'].get('support'), 5)


if __name__ == '__main__':
    unittest.main()
