import http.client
import json
import os
import socket
import shutil
import subprocess
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.web_app import Handler, Prototype, ThreadingHTTPServer, TakeoverJobs
from tools.web_client import ensure_cached_client, patch_client
from localserver import paths
from localserver.server import LocalServer, build_hello, parse_args

ROOT = Path(__file__).resolve().parents[1]


class WebRuntimeTests(unittest.TestCase):
    def test_desktop_native_recordings_never_fall_back_to_browser_on_adapter_failure(self):
        app=Prototype.__new__(Prototype);app.client=b'original-browser-client'
        app.options=SimpleNamespace(desktop=True)
        app.records=mock.Mock();app.records.directory=Path('unused-native-route-fixture')
        app.records.list.return_value=[dict(id='battle',platform='native',replay=dict(id='attempt'))]
        app.client_journals=mock.Mock();app.client_journals.statuses.return_value={}
        unavailable=dict(id='attempt',available=False,format='native',engine='browser',reason='incompatible')
        with mock.patch('tools.portable_replay.availability',return_value={'attempt':unavailable}),mock.patch('tools.native_playback.NativeControlClient',side_effect=OSError('not running')):
            result=app.battle_records()[0]['replay']
        self.assertEqual(result['engine'],'native')
        self.assertFalse(result['available'])

    def test_native_editor_and_history_need_no_browser_engine_download(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(paths,'USERDATA',Path(directory)/'native'):
            paths.ensure_user_data()
            options=SimpleNamespace(desktop=True,client=Path(directory)/'never-downloaded.js',data_dir=Path(directory)/'web',
                records_dir=Path(directory)/'battles',ws_port=8766)
            with mock.patch('tools.web_app.ensure_cached_client',side_effect=OSError('Network unavailable')) as download:
                app=Prototype(options)
                try:
                    self.assertTrue(app.workspace.editor()['desktop'])
                    self.assertEqual(app.battle_records(),[])
                    self.assertGreater(len(app.info()['maps']),10)
                    self.assertIsNone(app._client)
                    download.assert_not_called()
                    with self.assertRaisesRegex(OSError,'Network unavailable'):_=app.client
                finally:app.stop()

    def test_telemetry_recovers_windows_udp_startup_errors_on_receive_and_send(self):
        app=Prototype.__new__(Prototype)
        app.options=SimpleNamespace(bridge_port=21551)
        app.feedback=app.damage=app.received_damage=0
        app.latest_inventory=None;app.inventory_error=None
        forwarded=[];received=threading.Event();finished=threading.Event()
        target=mock.Mock()
        def recv(_size):
            if not received.is_set():
                received.set()
                raise ConnectionResetError(10054,'Port not bound yet')
            if not finished.is_set():
                finished.wait(1)
                return b'{"map":"egy_run","room_token":1}'
            return b''
        attempts=0
        def send(data):
            nonlocal attempts
            attempts+=1
            if attempts==1: raise ConnectionRefusedError(10061,'Bridge not bound yet')
            forwarded.append(json.loads(data))
            finished.set()
        target.recv.side_effect=recv;target.send.side_effect=send
        ws=mock.MagicMock();ws.request.path='/telemetry'
        def frames():
            yield json.dumps(dict(player=dict(objid=1,worldid=2,hp=100)))
            yield json.dumps(dict(player=dict(objid=1,worldid=2,hp=99)))
            self.assertTrue(received.wait(1))
        ws.__iter__.side_effect=frames
        with mock.patch('tools.web_app.socket.socket',return_value=target):
            app.websocket(ws)
        self.assertEqual(attempts,2)
        self.assertEqual(forwarded[0]['player']['hp'],99)
        self.assertEqual(app.feedback,2)

    def test_local_compatible_playback_retains_strict_takeover_checks(self):
        from tools.web_client import CLIENT_SHA256
        from tools.client_replay import ClientJournals
        import uuid
        with tempfile.TemporaryDirectory() as directory:
            app=Prototype.__new__(Prototype);app.client=b'current patched client';app.client_journals=ClientJournals(directory)
            identifier=str(uuid.uuid4())
            app.client_journals.begin(identifier,CLIENT_SHA256,'c'*64,dict(content=None),[1366,768])
            app.client_journals.append(identifier,dict(sequence=0,ops=[['loop',0],['loop',1]],end=True))
            self.assertEqual(app.replay_document(identifier)['id'],identifier)
            for invoke in (app.warm_takeover,app.prepare_takeover):
                with self.assertRaisesRegex(ValueError,'matching replay adapter'):invoke(dict(id=identifier))
            with self.assertRaisesRegex(ValueError,'matching replay adapter'):app.replay_document(identifier,require_exact=True)

    def test_incomplete_client_cache_is_replaced_only_after_verified_download(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'client.js';path.write_bytes(b'incomplete')
            response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'complete'
            def verify(data):
                if data!=b'complete':raise ValueError('Unsupported or incomplete client')
            with mock.patch('tools.web_client.patch_client',side_effect=verify), \
                    mock.patch('tools.web_client.urlopen',side_effect=[TimeoutError('temporary'),response]) as network:
                self.assertEqual(ensure_cached_client(path),path)
                self.assertEqual(network.call_count,2)
            self.assertEqual(path.read_bytes(),b'complete')
            self.assertEqual(list(path.parent.iterdir()),[path])
            with mock.patch('tools.web_client.patch_client',side_effect=verify), \
                    mock.patch('tools.web_client.urlopen') as network:
                self.assertEqual(ensure_cached_client(path),path)
                network.assert_not_called()

    def test_failed_client_download_preserves_existing_cache_and_leaves_no_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'client.js';path.write_bytes(b'previous incomplete cache')
            with mock.patch('tools.web_client.patch_client',side_effect=ValueError('unsupported')), \
                    mock.patch('tools.web_client.urlopen',side_effect=TimeoutError('network')) as network:
                with self.assertRaises(TimeoutError): ensure_cached_client(path)
                self.assertEqual(network.call_count,3)
            self.assertEqual(path.read_bytes(),b'previous incomplete cache')
            self.assertEqual(list(path.parent.iterdir()),[path])

    def test_live_editor_feedback_is_shared_by_new_battles_and_takeover(self):
        app=Prototype.__new__(Prototype)
        app.feedback=app.damage=app.received_damage=0
        app.latest_inventory=None;app.inventory_error=None
        app.observe_feedback(dict(player=dict(worldid=2,objid=1,inventory_preview={'count':14}),
            events=[dict(kind='damage',ownerid=2,victim=1,value=7),dict(kind='damage',ownerid=1,victim=90,value=11)]))
        self.assertEqual((app.feedback,app.damage,app.received_damage),(1,11,7))
        self.assertEqual(app.latest_inventory,{'count':14})
        app.observe_feedback(dict(player=dict(worldid=2,objid=1,inventory_error='Inventory refresh scope mismatch')))
        self.assertEqual(app.inventory_error,'Inventory refresh scope mismatch')

    def test_long_takeover_verification_is_pollable_and_bounded(self):
        jobs = TakeoverJobs()
        entered, release = threading.Event(), threading.Event()
        def prepare(document, progress):
            self.assertEqual(document, {'cursor': 4000})
            progress(123, 1000); entered.set(); release.wait(3)
            progress(1000, 1000)
            return {'token': 'local-host', 'id': 'separate-branch'}
        job = jobs.begin(prepare, {'cursor': 4000})['job']
        try:
            self.assertTrue(entered.wait(1))
            self.assertEqual(jobs.status(job), dict(state='pending', current=123, total=1000))
            with self.assertRaisesRegex(ValueError, 'already'):
                jobs.begin(prepare, {})
        finally:release.set()
        self.assertTrue(jobs.jobs[job]['done'].wait(2))
        status=jobs.status(job)
        self.assertEqual(status['state'], 'ready')
        self.assertEqual(status['result']['id'], 'separate-branch')

    def test_cancelled_takeover_never_becomes_ready_and_errors_are_sanitized(self):
        jobs = TakeoverJobs()
        entered, release = threading.Event(), threading.Event()
        def prepare(document, progress):
            entered.set(); release.wait(3); progress(1, 2)
            self.fail('A cancelled worker must not publish a live host')
        job = jobs.begin(prepare, {})['job']
        try:
            self.assertTrue(entered.wait(1)); jobs.cancel()
        finally:release.set()
        self.assertTrue(jobs.jobs[job]['done'].wait(2))
        status=jobs.status(job)
        self.assertEqual(status['state'], 'failed')
        self.assertNotIn('result', status)
        self.assertEqual(status['error'], 'Takeover verification failed (InterruptedError)')

    def test_personal_preferences_reload_in_the_original_engine(self):
        source = ROOT/'userdata/web/VR1574197275.mvmmoclient.js'
        if not source.is_file() or not shutil.which('node'):
            self.skipTest('Pinned original web client and Node required for engine acceptance')
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)/'client.js'
            target.write_bytes(patch_client(source.read_bytes()))
            result = subprocess.run(['node', str(ROOT/'tests/test_web_preferences_engine.js'), str(target)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, (result.stdout+result.stderr).decode('utf8','replace')[-4000:])

    def test_browser_records_by_default_and_storage_failure_does_not_block_play(self):
        app=Prototype.__new__(Prototype)
        app.client=b'compatible original client';app.client_journals=mock.Mock();app.game_preferences=mock.Mock()
        app.workspace=mock.Mock();app.workspace.preferences.return_value=dict(record_replays=True)
        with mock.patch.dict(os.environ):
            os.environ.pop('SB_RECORD_REPLAY',None)
            app.begin_recording('city_mid')
            self.assertIsInstance(app.recording_id,str)
            self.assertIsNone(app.recording_error)
            app.begin_recording('etl_run')
            self.assertIsInstance(app.recording_id,str,'A full run needs one client tape across all rooms')
            app.client_journals.begin.side_effect=OSError('disk full')
            app.begin_recording('city_mid')
            self.assertIsNone(app.recording_id)
            self.assertEqual(app.recording_error,'OSError')
            app.client_journals.begin.reset_mock()
            app.begin_recording('home')
            app.client_journals.begin.assert_not_called()
            os.environ['SB_RECORD_REPLAY']='0'
            app.begin_recording('city_mid')
            app.client_journals.begin.assert_not_called()

    def test_shutdown_waits_for_the_owned_game_connection_to_seal_history(self):
        client,server=socket.socketpair();finished=threading.Event()
        process=mock.Mock();process.poll.return_value=None;process.pid=123
        app=Prototype.__new__(Prototype)
        app.connection_lock=threading.Lock();app.game_connections={client:finished}
        app.process=process;app.log=None;app.records=mock.Mock()
        def finalize():
            self.assertEqual(server.recv(1),b'')
            finished.set();server.close()
        worker=threading.Thread(target=finalize);worker.start()
        process.terminate.side_effect=lambda:self.assertTrue(finished.is_set(),'The listener must outlive recorder finalization')
        try:
            app.stop();worker.join(2);self.assertFalse(worker.is_alive())
            process.terminate.assert_called_once()
        finally:client.close();server.close()

    def test_unknown_client_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported web client'):
            patch_client(b'unknown client')

    def test_browser_hello_uses_required_fixed32_without_changing_native(self):
        args = ['--map', 'hulk_mid', '--create-world-hex', str(ROOT / 'maps/hulk_mid/createworld.hex')]
        native = LocalServer(parse_args(args))
        browser = LocalServer(parse_args(args + ['--web-client']))
        hello = build_hello(dict(id='local', timestamp=1470335003))
        self.assertEqual(native.layouts.encode('Hello', hello)[0], 8)
        wire = browser.layouts.encode('Hello', hello)
        self.assertEqual(wire[:5], bytes.fromhex('0d78563412'))
        self.assertEqual(browser.layouts.decode('Hello', wire)['hid_'], 0x12345678)
        self.assertEqual(native.layouts.encode('Hello', hello)[0], 8)

    def test_assets_reject_traversal_and_arbitrary_files(self):
        app = Prototype.__new__(Prototype)
        for path in ('/texture/../secret.png', '/texture/./secret.png',
                     '/texture/a/../../secret.bpb', '/userdata/config.json',
                     '/texture/secret.py', '//example.com/a.png'):
            with self.subTest(path=path), self.assertRaises(FileNotFoundError):
                app.asset(path)

    def test_game_asset_cache_works_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Prototype.__new__(Prototype)
            app.cache = Path(directory)
            (app.cache / 'texture').mkdir()
            (app.cache / 'texture/base.0.test.png').write_bytes(b'cached')
            with mock.patch('tools.web_app.urlopen') as network:
                self.assertEqual(app.asset('/texture/base.0.test.png'), b'cached')
                network.assert_not_called()

    def test_temporary_asset_failure_retries_before_caching(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Prototype.__new__(Prototype)
            app.cache = Path(directory)
            app.options = SimpleNamespace(client=Path(directory) / 'client.js')
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b'complete asset'
            with mock.patch('tools.web_app.urlopen', side_effect=[TimeoutError('slow network'), response]) as network:
                self.assertEqual(app.asset('/texture/base.test.png'), b'complete asset')
                self.assertEqual(network.call_count, 2)
                self.assertEqual((app.cache / 'texture/base.test.png').read_bytes(), b'complete asset')

    def test_preparation_reports_progress_without_blocking_the_browser(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Prototype.__new__(Prototype)
            app.cache = Path(directory)
            app.options = SimpleNamespace(client=app.cache / 'client.js', ws_port=8766)
            app.asset_paths = ['/texture/one.png']
            app.required_assets = mock.Mock(return_value=app.asset_paths)
            app.prepared_map = 'hulk_mid'
            app.map, app.shell, app.process = 'hulk_mid', 1, None
            app.feedback = app.damage = app.received_damage = 0
            app.latest_player, app.items = {}, {}
            app.lock, app.preparing, app.asset_error = threading.Lock(), False, ''
            release, complete = threading.Event(), threading.Event()
            def download(path):
                release.wait(3)
                target = app.cache / path.lstrip('/')
                target.parent.mkdir()
                target.write_bytes(b'finished')
                complete.set()
            with mock.patch.object(app, 'asset', side_effect=download):
                state = app.prepare()
                self.assertFalse(state['assets_ready'])
                self.assertEqual(state['assets_cached'], 0)
                release.set()
                self.assertTrue(complete.wait(2))
                self.assertTrue(app.info()['assets_ready'])

    def test_invalid_selection_cannot_stop_or_start_a_server(self):
        app = Prototype.__new__(Prototype)
        app.lock = threading.Lock()
        app.items = {100: dict(slot_=256, usableby_=[1], name_='Test weapon')}
        with mock.patch.object(app, 'stop') as stop:
            for selection in (dict(shell=99), dict(map='other'), dict(shell=2, weapon=100),
                              dict(weapon=999), dict(weapon='invalid')):
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    app.start(selection)
            stop.assert_not_called()

    def test_weapon_changes_only_the_isolated_session_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory)
            original = b'{"1":{"items_":[{"slotpos_":256,"type_":10},{"slotpos_":512,"type_":20}]}}'
            (user / 'class_loadouts.json').write_bytes(original)
            app = Prototype.__new__(Prototype)
            app.directory = user / 'web'
            app.directory.mkdir()
            app.version_file = app.directory / 'dataVersion.bpb'
            app.options = SimpleNamespace(game_port=20480, bridge_port=21551, ws_port=8766)
            app.lock, app.process, app.log = threading.Lock(), None, None
            app.items = {100: dict(slot_=256, usableby_=[1], name_='Test weapon')}
            app.workspace = mock.Mock()
            app.workspace.preferences.return_value=dict(record_replays=True)
            app.records = mock.Mock()
            app.client=b'compatible original client'
            app.client_journals=mock.Mock();app.game_preferences=mock.Mock()
            app.workspace.materialize.side_effect = lambda map_id: (app.directory / 'class_loadouts.json').write_bytes(original)
            process = mock.Mock()
            process.pid = 123
            process.poll.return_value = None
            def launch(*args, **kwargs):
                self.assertEqual(kwargs['env']['SB_RECORD_REPLAY'],'1')
                self.assertEqual(kwargs['env']['SB_REPLAY_ATTEMPT'],app.recording_id)
                self.assertIsNone(app.recording_error)
                kwargs['stdout'].write('listening\n')
                kwargs['stdout'].flush()
                return process
            with mock.patch.object(paths, 'USERDATA', user), mock.patch.dict(os.environ,{'SB_RECORD_REPLAY':'1'}), mock.patch('tools.web_app.subprocess.Popen', side_effect=launch):
                app.start(dict(shell=1, weapon=100))
                self.assertEqual((user / 'class_loadouts.json').read_bytes(), original)
                edited = json.loads((app.directory / 'class_loadouts.json').read_text())['1']['items_']
                self.assertEqual(edited[0]['type_'], 100)
                self.assertEqual(edited[1]['type_'], 20)
                app.stop()
                process.terminate.assert_called_once()
                process.wait.assert_called_once()
                app.records.finish_owner.assert_called_once_with(123)

    def test_http_boundary_and_dynamic_websocket_port(self):
        app = mock.Mock()
        app.options = SimpleNamespace(ws_port=19999)
        app.workspace.preferences.return_value={'keys':{'interact':'Auto'}}
        app.game_preferences.read.return_value=dict(key='LocalStorage.pb',revision=0,content=None)
        app.start.return_value = dict(running=True)
        app.prepare.return_value = dict(assets_ready=True)
        app.info.return_value = dict(running=False)
        app.update_checker.status.return_value=dict(state='updated',current_version='v20261016-r6',latest_version='v20261016-r6')
        app.update_checker.start.return_value=dict(state='checking',current_version='v20261016-r6')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.prototype = app
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read()
            finally:
                connection.close()
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            connection.request('GET','/editor.js');response=connection.getresponse();response.read()
            tag=response.getheader('ETag');self.assertTrue(tag)
            self.assertIn('must-revalidate',response.getheader('Cache-Control'))
            connection.close()
            code,data=request('GET','/editor.js',headers={'If-None-Match':tag})
            self.assertEqual(code,304);self.assertEqual(data,b'')
            code, data = request('GET', '/runtime.js')
            self.assertEqual(code, 200)
            self.assertIn(b'19999', data)
            self.assertIn(b'gamePreferences', data)
            code, data = request('GET', '/api/replay/check?id=00000000-0000-4000-8000-000000000000')
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(data)['ready'])
            app.replay_document.assert_called_once_with('00000000-0000-4000-8000-000000000000')
            code, data = request('GET', '/api/game-preferences')
            self.assertEqual(code, 200)
            self.assertIsNone(json.loads(data)['content'])
            code, _ = request('POST', '/api/game-preferences', '{}', {'Origin': 'https://attacker.example'})
            self.assertEqual(code, 403)
            app.game_preferences.save.assert_not_called()
            code, _ = request('POST', '/api/replay/client', '{}', {'Origin': 'https://attacker.example'})
            self.assertEqual(code, 403)
            app.client_journals.append.assert_not_called()
            app.client_journals.append.return_value = dict(sequence=1, complete=True)
            chunk = dict(id='00000000-0000-4000-8000-000000000000', sequence=0, ops=[], end=True)
            code, data = request('POST', '/api/replay/client', json.dumps(chunk))
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(data)['complete'])
            app.client_journals.append.assert_called_once_with(chunk['id'], chunk)
            # The advertised 128-record export must fit the HTTP request limit.
            identifiers = [f'00000000-0000-4000-8000-{index:012d}' for index in range(128)]
            app.records.export.return_value = ('battles.zip', b'test archive')
            code, data = request('POST', '/api/records/export', json.dumps({'ids': identifiers}))
            self.assertEqual(code, 200)
            app.records.export.assert_called_once_with(identifiers)
            code, _ = request('GET', '/api/info', headers={'Host': 'attacker.example'})
            self.assertEqual(code, 403)
            code, _ = request('POST', '/api/start', '{}', {'Origin': 'https://attacker.example'})
            self.assertEqual(code, 403)
            code, _ = request('POST', '/api/start', '[]')
            # Bad documents must not reach the process launcher.
            self.assertEqual(code, 400)
            app.start.assert_not_called()
            code, data = request('POST', '/api/start', '{"shell":1}')
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(data)['running'])
            app.start.assert_called_once_with({'shell': 1})
            code, data = request('POST', '/api/prepare', '{}')
            self.assertEqual(code, 200)
            self.assertTrue(json.loads(data)['assets_ready'])
            app.prepare.assert_called_once_with({})
            code,data=request('GET','/api/update')
            self.assertEqual(code,200)
            self.assertEqual(json.loads(data)['state'],'updated')
            self.assertIn('Successfully updated',json.loads(data)['messages']['en'])
            code,_=request('POST','/api/update','{}',{'Origin':'https://attacker.example'})
            self.assertEqual(code,403)
            app.update_checker.start.assert_not_called()
            code,data=request('POST','/api/update','{}')
            self.assertEqual(code,200)
            self.assertEqual(json.loads(data)['state'],'checking')
            app.update_checker.start.assert_called_once_with()
            # Version checks must not start or restart a game.
            self.assertEqual(app.start.call_count,1)

        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
