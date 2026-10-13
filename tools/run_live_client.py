"""Launch the local StarBreak client with SBPE plugins injected.

Unlike the SBPE ``run.bat`` loader this accepts a server address, so the real
client + the live bridge plugin can talk to the local live simulator:

    python tools/run_live_client.py --server 127.0.0.1:2048

It performs the same steps as SBPE's ``loader.launch``: start the client,
load ``build/remote.bin`` into the process and start its kickstart thread.
"""

import argparse
import os
import subprocess
import sys
import time
import threading

SBPE_DIR = os.environ.get('HULK_SBPE', 'sbpe')
CLIENT = os.path.join(os.environ.get('HULK_GAME', 'StarBreak'), 'mvmmoclient_local2.exe')

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--sbpe', default=SBPE_DIR)
    parser.add_argument('--client', default=CLIENT)
    parser.add_argument('--server', default='127.0.0.1:2048')
    parser.add_argument('--runtime-dir', help='isolated SBPE config/plugins directory')
    parser.add_argument('--wait', type=float, default=0.0,
                        help='seconds to wait before injecting (default 0)')
    parser.add_argument('--keep', action='store_true',
                        help='keep the launcher attached until the client '
                             'exits')
    parser.add_argument('--stop-file', help='integration test stop signal for this spawned client only')
    parser.add_argument('--log-file', help='owned client log (isolated probes)')
    parser.add_argument('--background',action='store_true',help='keep only this owned observer client minimized without activating it')
    parser.add_argument('--offscreen',action='store_true',help='render an owned fixture offscreen without minimizing or activating it')
    options = parser.parse_args(argv)

    sbpe = os.path.abspath(options.sbpe)
    client = os.path.abspath(options.client)
    if not os.path.exists(client):
        raise SystemExit('client not found: ' + client)

    pypy_dir = os.path.join(sbpe, 'pypy')
    env_path = os.environ.get('PATH', '')
    os.environ['PATH'] = os.pathsep.join(
        [pypy_dir, os.path.dirname(client), env_path])

    sys.path.insert(0, sbpe)
    os.environ['SBPE_SYMFILE'] = os.path.join(
        os.path.abspath(options.runtime_dir or sbpe), 'symbols.json')

    import loader
    from mayhem.proc.native import NativeProcess

    log_path = options.log_file or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'catalogue', 'live-client.log')

    os.environ['HARDLAB_LIVE_SESSION'] = '1'
    log = open(log_path, 'wb')
    startup=None
    if options.background or options.offscreen:
        startup=subprocess.STARTUPINFO();startup.dwFlags|=subprocess.STARTF_USESHOWWINDOW;startup.wShowWindow=0
    process = subprocess.Popen(
        [client, '-s', options.server, '-k'],
        cwd=os.path.dirname(client), stdout=log, stderr=subprocess.STDOUT,startupinfo=startup)
    print('client pid={0} server={1}'.format(process.pid, options.server), flush=True)
    render_ready = threading.Event()
    if options.background or options.offscreen:

        def keep_owned_window_background():
            import ctypes
            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)
            placed = set()
            configured = set()
            def place_owned(hwnd, _):
                found = ctypes.c_ulong()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(found))
                if found.value == process.pid:
                    if hwnd not in configured:
                        style = user32.GetWindowLongW(hwnd, -20)
                        user32.SetWindowLongW(hwnd, -20, style | 0x08000000)
                        if options.offscreen:
                            user32.SetWindowPos(hwnd, None, -10000, -10000, 0, 0, 0x35)
                        configured.add(hwnd)
                    if render_ready.is_set() and hwnd not in placed:
                        user32.ShowWindow(hwnd, 4 if options.offscreen else 7)
                        placed.add(hwnd)
                return 1
            callback = callback_type(place_owned)
            while process.poll() is None:
                user32.EnumWindows(callback, 0)
                time.sleep(.01)
        threading.Thread(target=keep_owned_window_background, daemon=True).start()
    if options.wait > 0:
        time.sleep(options.wait)
    native = NativeProcess(pid=process.pid)
    library = native.load_library(loader.REMOTEPATH)
    print('remote.bin loaded at 0x{0:x}'.format(library))
    thread = native.start_thread(library + loader.STARTUP_OFFSET)
    render_ready.set()
    print('kickstart thread {0}'.format(thread))
    if options.keep:
        if options.stop_file:
            window_hidden=False
            while process.poll() is None:
                if (options.background or options.offscreen) and not window_hidden:
                    import ctypes
                    user32=ctypes.windll.user32
                    callback_type=ctypes.WINFUNCTYPE(ctypes.c_int,ctypes.c_void_p,ctypes.c_void_p)
                    def minimize_owned(hwnd,_):
                        nonlocal window_hidden
                        found=ctypes.c_ulong();user32.GetWindowThreadProcessId(hwnd,ctypes.byref(found))
                        if found.value==process.pid and user32.IsWindowVisible(hwnd):
                            if options.offscreen:
                                user32.SetWindowPos(hwnd,None,-10000,-10000,0,0,0x15)
                                user32.ShowWindow(hwnd,4)
                            else:user32.ShowWindow(hwnd,7)
                            window_hidden=True
                        return 1
                    user32.EnumWindows(callback_type(minimize_owned),0)
                if os.path.exists(options.stop_file):
                    process.terminate()
                    process.wait()
                    break
                time.sleep(0.1)
        else:
            process.wait()
    return 0

if __name__ == '__main__':
    sys.exit(main())
