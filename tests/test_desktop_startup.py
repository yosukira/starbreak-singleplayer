import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest import mock

from tools.desktop_view import open_local, service_failure, WindowActions
from tools import web_dependencies

ROOT=Path(__file__).resolve().parents[1]


class DesktopStartupTests(unittest.TestCase):
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
