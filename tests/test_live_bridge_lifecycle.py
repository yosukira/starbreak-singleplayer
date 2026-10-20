import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from plugins import hardlab_live


class LiveBridgeLifecycleTests(unittest.TestCase):
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
