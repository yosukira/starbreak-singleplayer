import http.client
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from tools.web_app import Handler, Prototype, ThreadingHTTPServer
from tools.web_client import patch_client
from localserver import paths
from localserver.server import LocalServer, build_hello, parse_args

ROOT = Path(__file__).resolve().parents[1]


class WebRuntimeTests(unittest.TestCase):
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
            app.workspace.materialize.side_effect = lambda map_id: (app.directory / 'class_loadouts.json').write_bytes(original)
            process = mock.Mock()
            process.poll.return_value = None
            def launch(*args, **kwargs):
                kwargs['stdout'].write('listening\n')
                kwargs['stdout'].flush()
                return process
            with mock.patch.object(paths, 'USERDATA', user), mock.patch('tools.web_app.subprocess.Popen', side_effect=launch):
                app.start(dict(shell=1, weapon=100))
                self.assertEqual((user / 'class_loadouts.json').read_bytes(), original)
                edited = json.loads((app.directory / 'class_loadouts.json').read_text())['1']['items_']
                self.assertEqual(edited[0]['type_'], 100)
                self.assertEqual(edited[1]['type_'], 20)
                app.stop()
                process.terminate.assert_called_once()

    def test_http_boundary_and_dynamic_websocket_port(self):
        app = mock.Mock()
        app.options = SimpleNamespace(ws_port=19999)
        app.workspace.preferences.return_value={'keys':{'interact':'Auto'}}
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
