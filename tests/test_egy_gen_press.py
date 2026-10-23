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

    def test_moving_ship_uses_the_panel_seen_at_the_press(self):
        from localserver.live.bridge import PlayerState
        from localserver.replay import player_document, restore_player
        session = self.session(); gen = self.lit_panel(session)
        panel = session.room.object(gen.active_panel)
        rect = list(panel.rect())
        sample = dict(seed=123,objid=1,worldid=2,rect=rect,
                      panels=[dict(objid=gen.active_panel,rect=rect)])
        press = PlayerState(objid=1,worldid=2,room_seed=123,hp=150,alive=True,
                            xmp=rect[0],ymp=rect[1],wmp=rect[2],hmp=rect[3],gen_press=sample)
        # Ship and latest player may have advanced while the press was queued.
        session.room.move_object(session.rules.SHIP_ID,
            session.room.ship.position()[0]+50000,session.room.ship.position()[1]+30000)
        session.combat_players=lambda:(press,)
        session._frame_interactions=(restore_player(player_document(press)),)
        gen.step(.6)
        self.assertEqual(gen.panel_state,'activating')

    def test_same_frame_sample_does_not_expand_reach_or_accept_another_panel(self):
        from localserver.live.bridge import PlayerState
        for wrong in ('range','panel','seed','size'):
            session=self.session();gen=self.lit_panel(session);rect=list(session.room.object(gen.active_panel).rect())
            sample=dict(seed=123,objid=1,worldid=2,rect=rect[:],panels=[dict(objid=gen.active_panel,rect=rect[:])])
            if wrong=='range':sample['rect'][0]+=100000
            if wrong=='panel':sample['panels'][0]['objid']+=2
            if wrong=='seed':sample['seed']=124
            if wrong=='size':sample['panels'][0]['rect'][2]+=1
            press=PlayerState(objid=1,worldid=2,room_seed=123,hp=150,alive=True,xmp=rect[0],ymp=rect[1],wmp=rect[2],hmp=rect[3],gen_press=sample)
            session.combat_players=lambda:(press,);session._frame_interactions=(press,)
            gen.step(.6)
            self.assertEqual(gen.panel_state,'lit',wrong)

    def test_legacy_moving_press_retains_old_replay_result(self):
        session=self.session();gen=self.lit_panel(session)
        self.press_on(session,session.room.object(gen.active_panel))
        session.room.move_object(session.rules.SHIP_ID,session.room.ship.position()[0]+50000,session.room.ship.position()[1])
        gen.step(.6)
        self.assertEqual(gen.panel_state,'lit','Old journals without same-frame geometry retain their verified result')

    def test_native_sample_uses_resolved_world_props_and_latches_values(self):
        from plugins._hardlab_livebridge import LiveBridge
        NS=SimpleNamespace
        panel=NS(objId=34,props=NS(vid='hulk.accesspanelon',xmp=50000,ymp=30000,wmp=5120,hmp=5120))
        body=NS(objId=1,props=NS(xmp=50000,ymp=30000,wmp=7680,hmp=17920))
        native=NS(asWorldObject=body,attached=NS(worldId=1,objId=5))
        cw=NS(asWorld=NS(props=NS(seed=123)),player=NS(asClientObject=NS(asNativeObject=native)),
              serverSubWorld=object(),mySubWorld=NS(asNativeSubWorld=NS(asSubWorldImpl=NS(asSubWorld=NS(worldId=2)))))
        bridge=object.__new__(LiveBridge);bridge._current_world=lambda:cw
        bridge.ffi=NS(NULL=None,cast=lambda kind,value:value)
        bridge.util=NS(worldobjects=lambda sub:[panel],getstr=lambda text:text)
        bridge.capture_gen_press();body.props.xmp+=50000;panel.props.xmp+=50000
        self.assertEqual(bridge._gen_press['rect'][0],50000)
        self.assertEqual(bridge._gen_press['panels'][0]['rect'][0],50000)
        self.assertEqual(bridge._gen_press['worldid'],2)


if __name__ == '__main__':
    unittest.main()
