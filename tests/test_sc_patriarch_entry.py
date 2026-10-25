"""Full-run Patriarch must select the arrived player and release its gate."""
from types import SimpleNamespace
import unittest

from localserver.live.bridge import PlayerBridge, PlayerState
from localserver.maps.sc_run.session import ScRunLiveSession
import test_sc_run as sc_tests


class PatriarchEntryTests(unittest.TestCase):
    def open_pat(self, stale=False):
        helper = sc_tests.ScRunTests()
        run, _ = helper.make_run()
        run.current = 'mid_patriarch'
        bridge = PlayerBridge(0)
        if stale:
            bridge.state = PlayerState(worldid=2, objid=1, hp=150, maxhp=150,
                                       xmp=10000, ymp=10000, room_seed=123)
        server = sc_tests.Transport(run)
        wrapper = ScRunLiveSession(server, None, SimpleNamespace(player_type=1, bridge_port=0), bridge=bridge)
        return helper, run, wrapper, server

    @staticmethod
    def platform_changes(server):
        return [row['modifyprops_']['intval_']
                for message in server.deltas()
                for row in message.get('worldupdate_', {}).get('objectupdates_', [])
                if row['objid_'] == 18 and row.get('modifyprops_', {}).get('property_') == 24]

    def tick(self, helper, wrapper, seconds, dt=.05, position=None):
        for _ in range(round(seconds / dt)):
            helper.feedback(wrapper, position)
            wrapper.step(dt)

    def test_previous_room_character_never_starts_the_duel_during_loading(self):
        helper, _, wrapper, server = self.open_pat(stale=True)
        for _ in range(100):
            wrapper.step(.05)
        self.assertEqual(wrapper.current.controller.state, 'waiting')
        self.assertIsNone(wrapper.current.controller.selected)
        self.assertNotIn(0, self.platform_changes(server))
        self.tick(helper, wrapper, 3.1)
        self.assertEqual(wrapper.current.controller.selected, (2, 1))
        self.assertIn(0, self.platform_changes(server))

    def test_current_room_seed_alone_does_not_accept_an_old_character(self):
        _, _, wrapper, _ = self.open_pat()
        wrapper.bridge.state = PlayerState(worldid=2, objid=1, hp=150,
            room_seed=wrapper.instance.seed, received_at=wrapper._ready_after - 1)
        for _ in range(70):
            wrapper.step(.05)
        self.assertIsNone(wrapper.current.controller.selected)

    def test_platform_is_reconciled_if_first_open_was_missed_during_world_load(self):
        helper, _, wrapper, server = self.open_pat()
        self.tick(helper, wrapper, 3.1)
        self.assertEqual(wrapper.current.viewer_platform[2], 0)
        server.sent.clear()  # native world reconstructed after the initial change
        self.tick(helper, wrapper, .8)
        self.assertIn(0, self.platform_changes(server))
        server.sent.clear()
        self.tick(helper, wrapper, 1., position=(100 * 256, 600 * 256))
        self.assertNotIn(0, self.platform_changes(server), 'Confirmed fall stops gate retries')

    def test_selection_and_victory_open_both_run_routes_at_different_frame_rates(self):
        for dt in (1 / 30, 1 / 60, 1 / 144, 1 / 240):
            with self.subTest(dt=dt):
                helper, run, wrapper, server = self.open_pat()
                self.tick(helper, wrapper, 3.1, dt)
                runtime = wrapper.current
                self.assertEqual(runtime.controller.selected, (2, 1))
                self.assertIn(0, self.platform_changes(server))
                self.assertFalse(wrapper.instance.flags.get('mid_cleared'))
                self.assertTrue(all(not run.allows(link) for link in wrapper.instance.recipe['links'].values()))
                wrapper.bridge.events.append(dict(kind='damage', ownerid=1, victim=4,
                    value=runtime.controller.hp))
                self.tick(helper, wrapper, 3., dt, position=(500 * 256, 600 * 256))
                self.assertEqual(runtime.controller.state, 'done')
                self.assertTrue(wrapper.instance.flags['mid_cleared'])
                for oid, link in wrapper.instance.recipe['links'].items():
                    self.assertTrue(run.allows(link))
                    props = wrapper.instance.snapshot.properties_at((1, oid))
                    if link['trigger'] == 'interact':
                        self.assertEqual(props['interact_'], 1)
                        player, _ = wrapper.bridge.snapshot()
                        self.assertTrue(wrapper.interact(oid, player, check_range=False))
                        self.assertEqual(server.transitions[-1]['destination'], link['destination'])

    def test_reentry_keeps_encounter_progress_and_publishes_a_fresh_platform_view(self):
        helper, run, wrapper, server = self.open_pat()
        self.tick(helper, wrapper, 3.1)
        runtime = wrapper.current
        runtime.controller.hp -= 1000
        bridge = PlayerBridge(0)
        revisited = ScRunLiveSession(server, None, SimpleNamespace(player_type=1, bridge_port=0), bridge=bridge)
        self.assertIs(revisited.current, runtime)
        server.sent.clear()
        self.tick(helper, revisited, .1)
        self.assertEqual(runtime.controller.hp, 9000)
        self.assertEqual(runtime.controller.selected, (2, 1))
        self.assertIn(0, self.platform_changes(server))


if __name__ == '__main__':
    unittest.main()
