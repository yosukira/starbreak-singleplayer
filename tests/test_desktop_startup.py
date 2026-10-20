import json
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
import zipfile
from unittest import mock

from tools.desktop_view import open_local, service_failure, WindowActions
from tools import desktop_view
from tools import web_dependencies

ROOT=Path(__file__).resolve().parents[1]


class DesktopStartupTests(unittest.TestCase):
    def viewer_fixture(self,root):
        files={'webview/__init__.py':b'# viewer\n','pythonnet/runtime/Python.Runtime.dll':b'pinned assembly'}
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for name,data in files.items():archive.writestr(name,data)
        raw=stream.getvalue();sha=hashlib.sha256(raw).hexdigest()
        manifest=[dict(name='fixture',size=len(raw),sha256=sha,url='https://invalid.example/viewer.whl')]
        (root/'data').mkdir()
        (root/'data/desktop-view-runtime.json').write_text(json.dumps({'windows-x64':manifest}))
        (root/'data/desktop-view-integrity.json').write_text(json.dumps({'windows-x64':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()}}))
        digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]
        packaged=root/'python/desktop-view'/('runtime-windows-x64-'+digest)
        packaged.mkdir(parents=True)
        (packaged/'.ready').write_text(json.dumps(dict(digest=digest)))
        for name,data in files.items():
            path=packaged/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        (packaged.parent/(sha+'.download')).write_bytes(raw)
        return packaged,files,digest

    def test_incomplete_packaged_viewer_repairs_from_verified_archives_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);packaged,files,digest=self.viewer_fixture(root)
            dll=packaged/'pythonnet/runtime/Python.Runtime.dll';dll.write_bytes(b'incomplete')
            with mock.patch.object(desktop_view,'ROOT',root),mock.patch.object(desktop_view,'urlopen',side_effect=AssertionError('Must remain offline')):
                repaired=desktop_view.install(root/'cache',target='windows-x64',check_python=False)
                self.assertNotEqual(repaired,packaged)
                self.assertTrue(desktop_view.verify_viewer(repaired,'windows-x64',digest))
                self.assertEqual((repaired/'pythonnet/runtime/Python.Runtime.dll').read_bytes(),files['pythonnet/runtime/Python.Runtime.dll'])
                self.assertEqual(dll.read_bytes(),b'incomplete','Keep the damaged package for diagnosis')

    @unittest.skipUnless(os.name=='nt','NTFS download origin streams')
    def test_unblocking_is_limited_to_verified_runtime_binaries(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);packaged,files,digest=self.viewer_fixture(root)
            dll=packaged/'pythonnet/runtime/Python.Runtime.dll';zone=Path(str(dll)+':Zone.Identifier')
            extra=packaged/'unrelated.dll';extra.write_bytes(b'unknown')
            unrelated=Path(str(extra)+':Zone.Identifier')
            zone.write_text('[ZoneTransfer]\nZoneId=3\n');unrelated.write_text('[ZoneTransfer]\nZoneId=3\n')
            with mock.patch.object(desktop_view,'ROOT',root):
                diagnostics={}
                self.assertEqual(desktop_view.prepare_windows_viewer(packaged,root/'cache',diagnostics),packaged)
                self.assertFalse(zone.exists());self.assertTrue(unrelated.exists())
                self.assertEqual(diagnostics['blocked_binaries'],['pythonnet/runtime/Python.Runtime.dll'])
                dll.write_bytes(b'changed');zone.write_text('[ZoneTransfer]\nZoneId=3\n')
                with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                    desktop_view.prepare_windows_viewer(packaged,root/'cache',{})
                self.assertTrue(zone.exists(),'Never unblock a file that failed its pinned checksum')

    def test_windows_clr_selection_ignores_then_restores_external_pythonnet_settings(self):
        from types import SimpleNamespace as NS
        observed=[];runtime=object()
        def load(value):
            self.assertIs(value,runtime)
            observed.append([key for key in os.environ if key.startswith('PYTHONNET_')])
        modules={'clr_loader':NS(get_netfx=lambda:runtime),'pythonnet':NS(load=load,get_runtime_info=lambda:'NetFX'),'clr':NS(AddReference=lambda _:None)}
        with mock.patch.dict(sys.modules,modules),mock.patch.dict(os.environ,dict(PYTHONNET_RUNTIME='coreclr',PYTHONNET_PYDLL='unrelated.dll')):
            self.assertEqual(desktop_view.initialize_windows_viewer(),'NetFX')
            self.assertEqual(observed,[[]])
            self.assertEqual(os.environ['PYTHONNET_RUNTIME'],'coreclr')
            self.assertEqual(os.environ['PYTHONNET_PYDLL'],'unrelated.dll')

    def test_runtime_check_records_full_failure_without_opening_a_dialog(self):
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            with mock.patch.object(desktop_view,'install',side_effect=RuntimeError('verified runtime initialization failed')):
                with self.assertRaisesRegex(RuntimeError,'initialization failed'):
                    desktop_view.main(['--standalone','--check-runtime','--directory',str(folder)])
            value=json.loads((folder/'window-status.json').read_text(encoding='utf8'))
            self.assertEqual(value['state'],'failed')
            self.assertEqual(value['details'],str(folder/'startup-error.log'))
            self.assertIn('Traceback',(folder/'startup-error.log').read_text(encoding='utf8'))
            self.assertIn('initialization failed',json.loads((folder/'runtime-diagnostics.json').read_text(encoding='utf8'))['error'])

    def test_native_window_changes_are_posted_without_synchronous_gui_calls(self):
        from types import SimpleNamespace as NS
        native=mock.Mock(IsDisposed=False);queued=[]
        native.BeginInvoke.side_effect=queued.append
        window=mock.Mock(native=native)
        with mock.patch('tools.desktop_view.os.name','nt'),mock.patch.dict(sys.modules,{'System':NS(Action=lambda callback:callback)}):
            actions=WindowActions(window)
            actions.post('pin');actions.post('hide');actions.post('close')
            native.Hide.assert_not_called();native.Close.assert_not_called()
            self.assertEqual(len(queued),3)
            for callback in queued:callback()
        self.assertTrue(native.TopMost)
        native.Hide.assert_called_once();native.Close.assert_called_once()
        window.hide.assert_not_called();window.destroy.assert_not_called()

    def test_websocket_library_loads_offline_without_site_packages(self):
        code=('import sys;sys.path.insert(0,'+repr(str(ROOT))+');'
              'from tools.web_dependencies import websocket_server;'
              'assert callable(websocket_server());import websockets;'
              'assert ".whl" in websockets.__file__;print(websockets.__version__)')
        result=subprocess.run([sys.executable,'-I','-S','-c',code],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(result.stdout.strip(),'17.0.1')

    def test_corrupt_bundled_library_reports_integrity_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'data/vendor'/web_dependencies.WHEEL
            path.parent.mkdir(parents=True);path.write_bytes(b'incomplete')
            with mock.patch.object(web_dependencies,'ROOT',root):
                with self.assertRaisesRegex(RuntimeError,'checksum mismatch'):
                    web_dependencies.websocket_server()

    def test_local_service_probe_ignores_unreachable_system_proxy(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200);self.end_headers();self.wfile.write(b'{"ready":true}')
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        try:
            with mock.patch.dict(os.environ,dict(http_proxy='http://127.0.0.1:1',HTTP_PROXY='http://127.0.0.1:1',no_proxy='',NO_PROXY='')):
                with open_local('http://127.0.0.1:'+str(server.server_port)) as response:
                    self.assertEqual(json.load(response),dict(ready=True))
        finally:server.shutdown();server.server_close();thread.join()

    def test_failure_details_include_current_traceback_without_stale_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'web.log';old=b'previous unrelated failure\n'
            path.write_bytes(old+b'Traceback (most recent call last):\nValueError: exact configuration error\n')
            detail=service_failure(path,len(old),1)
            self.assertIn('ValueError: exact configuration error',detail)
            self.assertNotIn('previous unrelated failure',detail)


if __name__=='__main__':unittest.main()
