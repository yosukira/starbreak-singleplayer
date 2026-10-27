import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from plugins import hardlab_live


class LiveBridgeLifecycleTests(unittest.TestCase):
    def test_character_holds_do_not_override_original_gameplay_bindings(self):
        from plugins._hardlab_livebridge import LiveBridge
        bridge=object.__new__(LiveBridge)
        bridge.ffi=NS(NULL=None);bridge._hold_since={};bridge._hold_fired=set()
        states=bytearray(512);states[95]=1
        bridge.key_states=lambda:(states,len(states))
        rows=[NS(primary=0,secondary=0,tertiary=0) for _ in range(14)]
        rows[5].secondary=95  # KP7 attack, conflicting with the WF alias
        bridge.input_mapping=lambda:NS(cik=rows)
        bridge._keyboard_scancode=lambda code:code
        with mock.patch('plugins._hardlab_livebridge.time.time',return_value=100.) as clock:
            self.assertEqual(bridge.character_holds(),[])
            clock.return_value=110.
            self.assertEqual(bridge.character_holds(),[])
            states[95]=0;states[36]=1
            self.assertEqual(bridge.character_holds(),[])
            clock.return_value=113.
            self.assertEqual(bridge.character_holds()[0]['type'],1)
            rows[5].secondary=0;states[36]=0
            bridge.character_holds();states[95]=1;bridge.character_holds()
            clock.return_value=116.
            self.assertEqual(bridge.character_holds()[0]['type'],1)

    def plugin(self):
        plugin=object.__new__(hardlab_live.Plugin)
        plugin.session=True;plugin.active=True;plugin.native_capture=None
        plugin.config=NS(keep_time_reserve=False,diagnostics_file='')
        plugin.key_reader=mock.Mock(document=dict(hold_seconds=2))
        plugin.key_reader.refresh.return_value=False
        plugin.keybindings=mock.Mock();plugin.keybindings.scancode.return_value=0;plugin.keybindings.shortcuts.return_value=[]
        plugin.bridge=mock.Mock(sent=0,seq=0)
        plugin.bridge.ground_restore_attached.return_value=False
        plugin.bridge.interact_edge.return_value=(False,False)
        plugin.bridge.interaction_blocked.return_value=False
        plugin.bridge.character_holds.return_value=[]
        plugin.bridge.link_ok.return_value=True
        plugin.bridge.build_datagram.return_value=dict(seq=1,player=dict(xmp=100,ymp=200,hp=120),events=[])
        plugin.bridge.send.side_effect=lambda _:setattr(plugin.bridge,'sent',plugin.bridge.sent+1)
        plugin._pending_interact_edge=False;plugin._teleport_latched=False
        plugin._teleport_test_due=lambda now:False
        plugin.next_send=0;plugin.interval=1/60;plugin.last_diagnostics=0
        return plugin

    def test_editor_and_recorder_keys_do_not_stop_feedback(self):
        plugin=self.plugin()
        # Exercise the live update with the previous hotkey edge asserted.
        # F8 is SDL scancode 65; F6 is 63. Neither owns the bridge lifecycle.
        plugin._edge=mock.Mock(return_value=True)
        plugin._toggle=mock.Mock(side_effect=AssertionError('key disconnected telemetry'))
        with mock.patch.dict(hardlab_live.os.environ,{'HARDLAB_LIVE_DIAGNOSTICS':''}):
            for scan in (63,65,53):
                states=bytearray(512);states[scan]=1
                plugin.bridge.key_states.return_value=(states,len(states));plugin.next_send=0
                plugin._after_update_live()
        self.assertTrue(plugin.active)
        self.assertEqual(plugin.bridge.send.call_count,3)
        plugin._edge.assert_not_called();plugin._toggle.assert_not_called()

    def test_dormant_or_explicitly_inactive_plugin_sends_nothing(self):
        for session,active in ((False,True),(True,False)):
            plugin=self.plugin();plugin.session=session;plugin.active=active
            plugin._after_update_live();plugin.bridge.send.assert_not_called()

    def test_unload_still_releases_owned_hooks(self):
        plugin=self.plugin();plugin.native_capture=mock.Mock()
        plugin.onUnload();plugin.native_capture.close.assert_called_once();plugin.bridge.uninstall.assert_called_once()
        ast.parse(Path('plugins/hardlab_live.py').read_text(),feature_version=(3,5))

    def test_native_input_reapply_waits_for_a_settled_room(self):
        from plugins._hardlab_livebridge import LiveBridge,RoomInputHandoff
        b=object.__new__(LiveBridge)
        b.enabled=True;b.input_context=dict(map='sc_run',room_token=2,room_seed=33,room_ready=False,room_handoff=True)
        b._network_frame={};b._network_frame_id=0;b._input_handoff=RoomInputHandoff()
        cw=NS(asWorld=NS(props=NS(seed=33)),player=object(),total=0,inputBuffer=0)
        b._current_world=lambda:cw;b.ffi=NS(NULL=None,cast=lambda typ,value:id(value))
        b.refs=NS(WorldClient=NS(overlay=None,clientWorld=cw))
        b.refs.GameClient=NS(worldClient=b.refs.WorldClient)
        b.physical_input_mask=lambda:1;b.input_handoffs=0
        b._native_set_input=mock.Mock(side_effect=lambda mask:setattr(cw,'total',mask))
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic',return_value=100.) as clock:
            for _ in range(20):b.begin_native_frame();self.assertFalse(b.reapply_room_input())
            b.input_context['room_ready']=True;b.begin_native_frame()
            self.assertFalse(b.reapply_room_input())
            # A fast display cannot turn three frames into a settled handoff.
            for _ in range(10):b.begin_native_frame()
            clock.return_value=100.01;self.assertFalse(b.reapply_room_input())
            b._native_set_input.assert_not_called()
            clock.return_value=100.06
            self.assertTrue(b.reapply_room_input())
            b._native_set_input.assert_called_once_with(1)
            b.input_context.update(room_token=3,room_seed=34);b.begin_native_frame()
            self.assertFalse(b.reapply_room_input())

    def handoff_bridge(self):
        from plugins._hardlab_livebridge import LiveBridge,RoomInputHandoff
        b=object.__new__(LiveBridge)
        b.enabled=True;b.input_context=dict(map='sc_run',room_token=2,room_seed=33,
                                           room_ready=True,room_handoff=True)
        b._network_frame={};b._network_frame_id=0;b._input_handoff=RoomInputHandoff()
        cw=NS(asWorld=NS(props=NS(seed=33)),player=object(),total=0,inputBuffer=0)
        b.ffi=NS(NULL=None,cast=lambda typ,value:id(value))
        b.refs=NS(WorldClient=NS(overlay=None,clientWorld=cw))
        b.refs.GameClient=NS(worldClient=b.refs.WorldClient)
        b._current_world=lambda:b.refs.WorldClient.clientWorld
        b.physical_input_mask=lambda:1;b.input_handoffs=0
        b._native_set_input=mock.Mock(side_effect=lambda mask:setattr(b._current_world(),'total',mask))
        return b,cw

    def settle_handoff(self,b,clock,at=100.):
        clock.return_value=at
        for _ in range(b.REAPPLY_SETTLE_FRAMES+1):b.begin_native_frame()
        clock.return_value=at+.06

    def test_ack_changed_after_frame_start_cannot_spend_old_context_stability(self):
        b,_cw=self.handoff_bridge()
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic') as clock:
            self.settle_handoff(b,clock)
            # ground_restore's ACK drain runs after begin_native_frame. A new
            # ready room cannot inherit the old room's elapsed time/frames.
            b.input_context['room_token']=3
            self.assertFalse(b.reapply_room_input())
            b._native_set_input.assert_not_called()
            b.begin_native_frame()
            self.assertEqual(b._context_frames,0)

    def test_replaced_live_owner_is_checked_before_cached_world_is_dereferenced(self):
        b,_cw=self.handoff_bridge()
        class RetiredClient:
            @property
            def clientWorld(self):
                raise AssertionError('Dereferenced freed WorldClient')
        b.refs.WorldClient=RetiredClient()
        # GameClient already owns the new world; SBPE's cached child is old.
        b.refs.GameClient.worldClient=object()
        self.assertIsNone(b._room_input_identity())
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'):
            b.begin_native_frame()
            self.assertFalse(b.reapply_room_input())
        b._native_set_input.assert_not_called()

    def test_ready_ack_does_not_count_frames_without_a_native_world(self):
        b,cw=self.handoff_bridge();b.refs.WorldClient.clientWorld=None
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic') as clock:
            self.settle_handoff(b,clock)
            b.refs.WorldClient.clientWorld=cw
            self.assertFalse(b.reapply_room_input())
            b.begin_native_frame()
            self.assertEqual(b._context_frames,0)
            b._native_set_input.assert_not_called()
            self.settle_handoff(b,clock,at=101.)
            self.assertTrue(b.reapply_room_input())

    def test_replacement_native_world_or_player_requires_new_settled_frames(self):
        for replace_world in (True,False):
            b,cw=self.handoff_bridge()
            with self.subTest(replace_world=replace_world),mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic') as clock:
                self.settle_handoff(b,clock)
                if replace_world:
                    b.refs.WorldClient.clientWorld=NS(asWorld=cw.asWorld,player=object(),total=0,inputBuffer=0)
                else:cw.player=object()
                self.assertFalse(b.reapply_room_input())
                b._native_set_input.assert_not_called()
                b.begin_native_frame()
                self.assertEqual(b._context_frames,0)

    def test_native_mapping_callback_cannot_apply_mask_to_a_replacement_world(self):
        b,cw=self.handoff_bridge()
        def physical():
            b.refs.WorldClient.clientWorld=NS(asWorld=cw.asWorld,player=object(),total=0,inputBuffer=0)
            return 1
        b.physical_input_mask=physical
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic') as clock:
            self.settle_handoff(b,clock)
            self.assertFalse(b.reapply_room_input())
            b._native_set_input.assert_not_called()

    def test_native_setter_does_not_dereference_freed_previous_world(self):
        class World:
            retired=False
            asWorld=NS(props=NS(seed=33))
            player=object()
            inputBuffer=0
            @property
            def total(self):
                if self.retired:raise AssertionError('Read freed cached ClientWorld')
                return 0
        b,_cw=self.handoff_bridge();cw=World();b.refs.WorldClient.clientWorld=cw
        def replace(mask):
            cw.retired=True
            b.refs.WorldClient.clientWorld=NS(asWorld=cw.asWorld,player=object(),total=mask,inputBuffer=0)
        b._native_set_input.side_effect=replace
        with mock.patch.dict('os.environ',HARDLAB_LIVE_SESSION='1'),mock.patch('plugins._hardlab_livebridge.time.monotonic') as clock:
            self.settle_handoff(b,clock)
            self.assertFalse(b.reapply_room_input())
            self.assertEqual(b.input_handoffs,0)
            self.assertTrue(b._input_handoff.pending)

    def test_input_restore_exception_keeps_feedback_and_reports_once(self):
        plugin=self.plugin()
        plugin.bridge.reapply_room_input.side_effect=RuntimeError('unsupported input adapter')
        with mock.patch.object(hardlab_live.LOG,'warning') as warning:
            for _ in range(2):
                plugin.next_send=0
                plugin._after_update_live()
            self.assertEqual(plugin.bridge.send.call_count,2)
            self.assertEqual(plugin.last_stage,'sent')
            self.assertEqual(plugin.input_reapply_error,'RuntimeError: unsupported input adapter')
            warning.assert_called_once()
        plugin.bridge.reapply_room_input.side_effect=None
        plugin.bridge.reapply_room_input.return_value=True
        plugin.next_send=0
        plugin._after_update_live()
        self.assertIsNone(plugin.input_reapply_error)
        self.assertEqual(plugin.bridge.send.call_count,3)

    def test_replay_progress_covers_reconstruction_and_restores_canvas_coordinates(self):
        plugin=object.__new__(hardlab_live.Plugin);plugin.session=True
        plugin.config=NS(hud_visible=False)
        plugin.native_capture=NS(delivery=None,player=NS(status=lambda:dict(state='preparing',progress=.4)))
        plugin.refs=NS(windowW=1920,windowH=1080,canvasW_=[1366],canvasH_=[768],XDL_FillRect=mock.Mock())
        plugin.txt=NS(screenCoords=False,draw=mock.Mock(side_effect=RuntimeError('draw failed')))
        with mock.patch.object(hardlab_live,'lib',NS(BLENDMODE_BLEND=1)):plugin.onPresent()
        plugin.refs.XDL_FillRect.assert_called_once_with(0,0,1920,1080,0xff0b141d,1)
        self.assertIn('40%',plugin.txt.text)
        self.assertEqual((plugin.refs.canvasW_[0],plugin.refs.canvasH_[0],plugin.txt.screenCoords),(1366,768,False))
