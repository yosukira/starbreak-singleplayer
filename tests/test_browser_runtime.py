import base64
import os
import struct
import socket
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from localserver import game_settings,pb_layout,protocol
from tools.browser_runtime import BrowserRuntime,MemoryChannel,PENDING_MAPS


class BrowserRuntimeTests(unittest.TestCase):
    def app(self):
        directory=self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(mock.patch.dict(os.environ))
        self.enterContext(mock.patch.object(game_settings,'PATH',game_settings.PATH))
        version=pb_layout.default_layouts().encode('DataVersion',{})
        app=BrowserRuntime(directory,dict(client='a'*64,adapter='b'*64,data_version=base64.b64encode(version).decode(),version='test'))
        self.addCleanup(app.stop)
        return app

    def test_compatible_playback_does_not_enable_unverified_takeover(self):
        import uuid
        app=self.app();identifier=str(uuid.uuid4())
        app.clients.begin(identifier,'a'*64,'c'*64,dict(content=None),[1366,768])
        app.clients.append(identifier,dict(sequence=0,ops=[['loop',0],['loop',1]],end=True))
        self.assertTrue(app.api('GET','/api/replay/check?id='+identifier)['ready'])
        self.assertFalse(app.boot('?replay='+identifier)['replay']['takeover'])
        for endpoint in ('/api/replay/warm','/api/replay/takeover'):
            with self.assertRaisesRegex(ValueError,'matching replay adapter'):
                app.api('POST',endpoint,dict(id=identifier))
        self.assertEqual(app.records.list(),[])

    def test_native_socket_timeout_still_checks_duration(self):
        from localserver.server import LocalServer
        server=LocalServer.__new__(LocalServer)
        server.options=SimpleNamespace(duration=1)
        channel=mock.Mock()
        channel.read_frame.side_effect=socket.timeout
        with mock.patch('localserver.server.time.time',side_effect=[0,0,2]):
            server._read_loop(channel)
        self.assertEqual(channel.read_frame.call_count,1)

    def test_encrypted_fragmented_transport_and_cipher_epoch(self):
        output=[];channel=MemoryChannel(output.append);received=[]
        key=protocol.random_string(0,64)
        def consume():
            received.append((yield))
            channel.enable_rc4(key)
            while True:received.append((yield))
        receiver=consume();next(receiver)
        def frame(kind,data):return bytes([kind])+struct.pack('>i',len(data))+data
        raw=frame(3,b'connect')+protocol.RC4(key).crypt(frame(22,b'pack')+frame(11,b'world'))
        for byte in raw:channel.feed(bytes([byte]),receiver)
        self.assertEqual(received,[(3,b'connect'),(22,b'pack'),(11,b'world')])
        channel.send_frames(((8,b'joined'),(12,b'update')))
        self.assertEqual(protocol.RC4(key).crypt(output[0]),frame(8,b'joined')+frame(12,b'update'))

    def test_real_handshake_maps_and_ticks_never_start_processes_threads_or_sockets(self):
        app=self.app()
        layouts=pb_layout.default_layouts()
        with mock.patch('socket.socket',side_effect=AssertionError('No network in browser')),\
             mock.patch('threading.Thread.start',side_effect=AssertionError('No nested runtime threads')):
            for map_id in game_settings.ordered_maps():
                if map_id in PENDING_MAPS:continue
                with self.subTest(map=map_id):
                    app.start(dict(map=map_id,shell=1));app.open(1,'ws://localhost/game');self.assertTrue(app.drain())
                    equipped={item['type_'] for item in app.workspace.read()['loadouts']['1']['items_']}
                    self.assertTrue(equipped.issubset(app.server.item_descriptions),'Every equipped/bag item must be described before CreatePlayer')
                    self.assertGreater(len(app.server.item_library),200)
                    connect=layouts.encode('Connect',{'enckey_':0})
                    app.send(1,'ws://localhost/game',base64.b64encode(bytes([3])+struct.pack('>i',len(connect))+connect).decode(),True)
                    cipher=protocol.RC4(protocol.random_string(0,64))
                    for _ in range(len(game_settings.map_info(map_id).get('preload_pvids',[]))+1):
                        app.send(1,'ws://localhost/game',base64.b64encode(cipher.crypt(bytes([22,0,0,0,0]))).decode(),True)
                    app.send(1,'ws://localhost/game',base64.b64encode(cipher.crypt(bytes([11,0,0,0,0]))).decode(),True)
                    self.assertIsNotNone(app.server.live_session)
                    for _ in range(4):app.tick(1/60)
                    app.stop()


    def test_stale_iframe_cannot_stop_a_new_battle(self):
        app=self.app()
        app.start(dict(map='egy_hulk',shell=1));app.open(1,'ws://localhost/game')
        app.start(dict(map='hulk_normal',shell=3));app.open(2,'ws://localhost/game')
        app.close_socket(1,'ws://localhost/game')
        self.assertIsNotNone(app.server)
        self.assertEqual(app.shell,3)
        app.close_socket(2,'ws://localhost/game')
        self.assertIsNone(app.server)

    def test_preferences_and_loadouts_survive_a_new_browser_runtime(self):
        app=self.app();settings=app.preferences.read()
        app.workspace.save_preferences(dict(keys=app.workspace.preferences()['keys'],language='en',query='Motley',item_sort='name'))
        value=app.workspace.read();value['settings']['map']='hulk_normal';app.workspace.save(value)
        app.stop();reopened=BrowserRuntime(app.directory,app.manifest)
        self.addCleanup(reopened.stop)
        self.assertEqual(reopened.workspace.preferences()['query'],'Motley')
        self.assertEqual(reopened.workspace.read()['settings']['map'],'hulk_normal')
        self.assertEqual(reopened.preferences.read(),settings)
