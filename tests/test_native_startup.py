import json
import os
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import tempfile
import unittest
from unittest import mock
from urllib.request import Request,urlopen

from localserver.server import LocalServer
from plugins._native_replay_hooks import shared_hook_target
from tools.native_recording import NativeRecording
from tools.native_playback import native_battle_view
from test_native_recording import manifest


class NativeStartupTests(unittest.TestCase):
    def test_selected_practice_world_keeps_earlier_verified_operations_but_ends_before_the_next_map(self):
        ops=[]
        for world in range(3):
            ops.extend([['loop',0],['message',10,'data'],['return','message'],['step',1000],['state',[world]],['loop',1]])
        self.assertEqual(native_battle_view(ops,2),dict(start=6,end=12,offset=1.,duration=1.))
        self.assertEqual(native_battle_view(ops,3),dict(start=12,end=18,offset=2.,duration=1.))
        with self.assertRaisesRegex(ValueError,'missing'):native_battle_view(ops,4)

    def test_view_stops_before_next_zone_resource_loading(self):
        ops=[['loop',0],['message',10,'world'],['step',1000],['loop',1],
             ['loop',0],['step',1000],['loop',1],
             ['loop',0],['message',21,'pack'],['step',1000],['loop',1],
             ['loop',0],['message',10,'next'],['loop',1]]
        self.assertEqual(native_battle_view(ops,1),dict(start=0,end=7,offset=0.,duration=2.))

    def test_existing_known_hook_must_preserve_verified_prologue_and_exact_return(self):
        target,address=0x400000,0x700000
        expected=bytes.fromhex('5589e583ec788d45f88b550c')
        original=expected[:6]+b'\xe9'+struct.pack('<i',target+6-(address+11))+b'\x90'*6
        memory={target:b'\xe9'+b'\x00'*11,address:original}
        ffi=NS(NULL=None,cast=lambda kind,value:value,buffer=lambda p,n:memory[p][:n])
        owner=NS(update_input_hook=1)
        refs=NS(manager=NS(_active={'plugin_combat_assist':NS(native=owner)}))
        lib=NS(subhook_is_installed=lambda _:True,subhook_get_trampoline=lambda _:address)
        self.assertEqual(shared_hook_target(ffi,lib,refs,'WorldClient::updateInput',target,expected),(address,original[:11],owner))
        memory[address]=expected[:6]+b'\xe9'+struct.pack('<i',target+7-(address+11))+b'\x90'*6
        with self.assertRaisesRegex(ValueError,'does not preserve'):shared_hook_target(ffi,lib,refs,'WorldClient::updateInput',target,expected)
        with self.assertRaisesRegex(ValueError,'conflicts'):shared_hook_target(ffi,lib,NS(),'WorldClient::updateInput',target,expected)

    def test_existing_send_observer_keeps_both_hooks_and_selects_the_exact_trampoline(self):
        target,address=0x400000,0x700000
        expected=bytes.fromhex('5589e583ec788d45f88b550c')
        original=expected[:6]+b'\xe9'+struct.pack('<i',target+6-(address+11))+b'\x90'*6
        unrelated=expected[:6]+b'\xe9'+struct.pack('<i',target+7-(address+111))+b'\x90'*6
        memory={target:b'\xe9'+b'\x00'*11,address:original,address+100:unrelated}
        ffi=NS(NULL=None,cast=lambda kind,value:value,buffer=lambda p,n:memory[p][:n])
        owner=NS(hooks=[1,2]);refs=NS(manager=NS(_active={'plugin_combat_assist':NS(research_wire=owner)}))
        lib=mock.Mock();lib.subhook_is_installed.return_value=True
        lib.subhook_get_trampoline.side_effect=lambda handle:address+100 if handle==1 else address
        self.assertEqual(shared_hook_target(ffi,lib,refs,'ServerConnection::sendMessage',target,expected),(address,original[:11],owner))
        lib.subhook_remove.assert_not_called();lib.subhook_install.assert_not_called()
        owner.hooks=[1]
        with self.assertRaisesRegex(ValueError,'conflicts'):shared_hook_target(ffi,lib,refs,'ServerConnection::sendMessage',target,expected)

    def test_editor_practice_queues_a_zone_reload_without_a_launcher_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'practice.json'
            with mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
                recording=NativeRecording(directory,'owned.exe',practice_path=path)
            try:
                request=Request(recording.config['url'].replace('/chunks','/command'),data=json.dumps(dict(action='practice',map='hulk_mid',shell=2)).encode(),headers={'Authorization':'Bearer '+recording.token})
                with urlopen(request) as response:self.assertTrue(json.load(response)['starting'])
                self.assertIsNone(recording.restart_request)
                self.assertFalse(path.exists(),'Wait for the native client before requesting the new world')
                control=Request(recording.config['url'].replace('/chunks','/control?serial=0'),headers={'Authorization':'Bearer '+recording.token})
                with urlopen(control) as response:resume=json.load(response)['resume']
                acknowledgement=Request(recording.config['url'].replace('/chunks','/feedback'),data=json.dumps(dict(serial=resume['serial'],resume=resume['id'])).encode(),headers={'Authorization':'Bearer '+recording.token})
                with urlopen(acknowledgement) as response:json.load(response)
                value=json.loads(path.read_text());self.assertEqual((value['map'],value['shell']),('hulk_mid',2))
                server=LocalServer.__new__(LocalServer);server.options=NS(map='hulk_mid');server._restart_in_progress=False
                server._live_world_sequence=2
                server._ground_ack_ready=lambda _:True;server.request_live_map_switch=mock.Mock()
                def restart(*args,**kwargs):server._restart_in_progress=True
                server.request_live_restart=mock.Mock(side_effect=restart)
                with mock.patch.dict(os.environ,SB_NATIVE_PRACTICE_PATH=str(path)):
                    self.assertTrue(server._apply_native_practice('channel',NS(bridge='bridge')))
                    server.request_live_restart.assert_called_once_with('channel',mock.ANY,2)
                    self.assertFalse(server._apply_native_practice('channel',NS(bridge='bridge')))
                    value.update(id='next-request',map='hulk_normal',shell=3);path.write_text(json.dumps(value));server._restart_in_progress=False
                    server.request_live_map_switch.side_effect=restart
                    self.assertTrue(server._apply_native_practice('channel',NS(bridge='bridge')))
                    server.request_live_map_switch.assert_called_once_with('channel',mock.ANY,'hulk_normal',player_type=3)
            finally:recording.close()

    def test_every_native_restart_rotates_attempt_before_creating_world(self):
        server=LocalServer.__new__(LocalServer);server.options=NS(map='egy_hulk')
        server._restart_in_progress=False;server._ground_ack_ready=lambda bridge:True
        server.reload_class_loadouts_if_changed=mock.Mock()
        session=NS(bridge=mock.Mock(),player_type=1)
        with mock.patch.dict(os.environ,SB_NATIVE_CONTROL_PATH='owned-control.json'),mock.patch('tools.native_playback.NativeControlClient') as client:
            server.request_live_restart('channel',session,3)
            server.request_live_restart('channel',session,3)
            client.assert_called_once_with('owned-control.json')
            client.return_value.call.assert_called_once_with('/command',dict(action='practice',map='egy_hulk',shell=3))
            server.reload_class_loadouts_if_changed.assert_not_called()
            self.assertFalse(server._restart_in_progress)


if __name__=='__main__':unittest.main()
