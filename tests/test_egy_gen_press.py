"""EGY gen panel presses must survive a late frame or a restarted counter."""

from types import SimpleNamespace
import unittest

from localserver import game_settings, pb_layout, paths
from localserver.live.bridge import PlayerBridge
from localserver.live.room import BossRoom
from localserver.live.session import LiveSession
from localserver.server import parse_args


class InteractSequenceTests(unittest.TestCase):
    def bridge(self):
        messages = []
        bridge = PlayerBridge(0, log=messages.append)
        return bridge, messages

    def test_a_repeated_packet_number_counts_once(self):
        bridge, _ = self.bridge()
        self.assertTrue(bridge._accept_interaction({'interact_edge': True}, 7))
        self.assertFalse(bridge._accept_interaction({'interact_edge': True}, 7))
        self.assertFalse(bridge._accept_interaction({}, 8))

    def test_a_restarted_counter_keeps_presses_alive(self):
        bridge, messages = self.bridge()
        bridge._interaction_seq = 5000
        self.assertTrue(bridge._accept_interaction({'interact_edge': True}, 3))
        self.assertFalse(bridge._accept_interaction({'interact_edge': True}, 3))
        self.assertTrue(bridge._accept_interaction({'interact_edge': True}, 4))
        self.assertTrue(any('restarted' in message for message in messages))

    def test_a_reordered_packet_is_not_a_restart(self):
        bridge, messages = self.bridge()
        bridge._interaction_seq = 40
        self.assertFalse(bridge._accept_interaction({'interact_edge': True}, 38))
        self.assertFalse(any('restarted' in message for message in messages))


class GenPanelPressTests(unittest.TestCase):
    def session(self):
        source = paths.ROOT / 'data/catalogue/real_hulkboss_createworld_flags.hex'
        options = parse_args(['--map', 'egy_hulk', '--create-world-hex', str(source)])
        options.live_seed = 23
        options.player_x, options.player_y = 0, 0
        server = SimpleNamespace(layouts=pb_layout.default_layouts(),
                                 _worldid=lambda: 1, class_loadouts={})
        document = game_settings.normalize({'map': 'egy_hulk',
                                            'skip_intro': {'egy_hulk': True}})
        with game_settings.event_snapshot(document):
            session = LiveSession(server, None, options,
                                  BossRoom.from_capture(str(source), options.boss_templates))
            session.prepare()
        return session

    def lit_panel(self, session):
        gen = session.gens
        gen.enabled = True
        gen.finished = False
        gen.panel_state = 'lit'
        gen.panel_timer = 0.0
        gen.panel_interactable = False
        gen.pending_press = 0.0
        gen.active_panel, gen.active_gen = gen.pair()
        return gen

    def press_on(self, session, panel):
        x, y, width, height = panel.rect()
        player = SimpleNamespace(rect=lambda: (x, y, width, height),
                                 alive=True, raw={})
        session.combat_players = lambda: (player,)
        session._frame_interactions = (player,)
        return player

    def test_a_press_just_before_the_panel_accepts_input_is_kept(self):
        session = self.session()
        gen = self.lit_panel(session)
        panel = session.room.object(gen.active_panel)
        self.press_on(session, panel)
        gen.step(0.1)
        self.assertEqual(gen.panel_state, 'lit')
        self.assertGreater(gen.pending_press, 0.0)
        gen.step(0.5)
        self.assertEqual(gen.panel_state, 'activating')

    def test_a_press_off_the_panel_is_not_kept(self):
        session = self.session()
        gen = self.lit_panel(session)
        panel = session.room.object(gen.active_panel)
        x, y, width, height = panel.rect()
        far = SimpleNamespace(rect=lambda: (x + 1000000, y + 1000000, width, height),
                              alive=True, raw={})
        session.combat_players = lambda: (far,)
        session._frame_interactions = (far,)
        gen.step(0.6)
        self.assertEqual(gen.panel_state, 'lit')
        self.assertEqual(gen.pending_press, 0.0)


if __name__ == '__main__':
    unittest.main()
