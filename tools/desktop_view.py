"""One native equipment/history window using the shared browser components.

Pinned viewer dependencies are bundled or cached in userdata on first use. No
package manager or executable installer is run. Equipment and shortcuts write
the native player's existing configuration files.
"""
import argparse
import hashlib
import io
import html
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import sys
import tarfile
import threading
import time
from urllib.request import urlopen, build_opener, ProxyHandler
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def open_local(request, timeout=1):
    return build_opener(ProxyHandler({})).open(request,timeout=timeout)


def alive(pid):
    if not pid:return False
    if os.name=='nt':
        import ctypes
        ctypes.windll.kernel32.OpenProcess.restype=ctypes.c_void_p
        ctypes.windll.kernel32.GetExitCodeProcess.argtypes=(ctypes.c_void_p,ctypes.c_void_p)
        ctypes.windll.kernel32.CloseHandle.argtypes=(ctypes.c_void_p,)
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,int(pid))
        if not handle:return False
        try:
            result=ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(result))) and result.value==259
        finally:ctypes.windll.kernel32.CloseHandle(handle)
    try:os.kill(int(pid),0);return True
    except OSError:return False


def install(directory, progress=lambda *_:None, target=None, check_python=True, bundled=True):
    if check_python and sys.version_info[:2]!=(3,12):raise ValueError('The native records window requires the bundled Python 3.12 runtime')
    if target is None:
        target=('windows-x64' if os.name=='nt' else 'macos-arm64' if platform.machine()=='arm64' else 'macos-x64')
        if os.name!='nt' and sys.platform!='darwin':raise ValueError('Native records window supports Windows and macOS')
    manifest=json.loads((ROOT/'data/desktop-view-runtime.json').read_text())[target]
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]
    packaged=ROOT/'python/desktop-view'/('runtime-'+target+'-'+digest)
    if bundled and (packaged/'.ready').is_file() and (packaged/'webview/__init__.py').is_file():return packaged
    destination=Path(directory)/('runtime-'+target+'-'+digest)
    if (destination/'.ready').is_file():return destination
    from tools.web_workspace import atomic_json
    destination.mkdir(parents=True,exist_ok=True)
    completed=0;total=sum(row['size'] for row in manifest)
    for row in manifest:
        cache=Path(directory)/(row['sha256']+'.download')
        data=cache.read_bytes() if cache.is_file() else b''
        if hashlib.sha256(data).hexdigest()!=row['sha256']:
            chunks=[];received=0
            with urlopen(row['url'],timeout=60) as response:
                while True:
                    chunk=response.read(128*1024)
                    if not chunk:break
                    received+=len(chunk)
                    if received>row['size']:raise ValueError('Viewer download size mismatch')
                    chunks.append(chunk);progress(completed+received,total)
            data=b''.join(chunks)
            if len(data)!=row['size'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Viewer download checksum mismatch')
            temporary=cache.with_suffix('.part');temporary.write_bytes(data);temporary.replace(cache)
        if row['url'].endswith('.whl'):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(v.file_size for v in archive.infolist())>100*1024*1024:raise ValueError('Viewer archive is too large')
                for entry in archive.infolist():
                    path=Path(entry.filename)
                    if path.is_absolute() or '..' in path.parts:raise ValueError('Unsafe viewer archive path')
                archive.extractall(destination)
        else:
            # proxy_tools is a tiny pure-Python source distribution. Extract
            # its source/metadata only; never execute setup.py or other builds.
            with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
                for entry in archive.getmembers():
                    parts=Path(entry.name).parts[1:]
                    if not entry.isfile() or not parts or any(p=='..' for p in parts):continue
                    if parts[0] not in ('proxy_tools','proxy_tools.egg-info','PKG-INFO','README.rst'):continue
                    target_path=destination.joinpath(*parts);target_path.parent.mkdir(parents=True,exist_ok=True)
                    target_path.write_bytes(archive.extractfile(entry).read())
        completed+=row['size'];progress(completed,total)
    atomic_json(destination/'.ready',{'digest':digest})
    return destination


def ports():
    sockets=[]
    try:
        for _ in range(4):
            sock=socket.socket();sock.bind(('127.0.0.1',0));sockets.append(sock)
        return [s.getsockname()[1] for s in sockets]
    finally:
        for sock in sockets:sock.close()


def service_failure(path, offset, code):
    try:
        with Path(path).open('rb') as log:
            log.seek(0, 2)
            log.seek(max(offset, log.tell()-6000))
            detail=log.read().decode('utf8',errors='replace').strip()
    except OSError:
        detail=''
    return 'Local equipment service exited ('+str(code)+').\n'+detail


class WindowActions:
    """Post native changes to their owning UI thread without waiting on it."""
    def __init__(self,window):self.window=window

    def post(self,action):
        window=self.window
        if os.name=='nt':
            from System import Action
            native=window.native
            if native is None or native.IsDisposed:return
            def apply():
                if native.IsDisposed:return
                if action=='hide':native.Hide()
                elif action=='close':native.Close()
                else:
                    native.TopMost=True
                    if action=='show':
                        from System.Windows.Forms import FormWindowState
                        native.Show();native.WindowState=FormWindowState.Normal;native.Activate()
            native.BeginInvoke(Action(apply))
        elif action=='hide':window.hide()
        elif action=='close':window.destroy()
        elif action=='show':window.show();window.restore();window.on_top=True
        else:window.on_top=True


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-pid',type=int)
    parser.add_argument('--standalone',action='store_true',help='Open equipment and records without starting the game')
    parser.add_argument('--directory',type=Path,default=ROOT/'userdata/desktop-records')
    parser.add_argument('--records-dir',type=Path,default=ROOT/'userdata/battles')
    parser.add_argument('--check-runtime',action='store_true')
    parser.add_argument('--show',action='store_true')
    options=parser.parse_args(argv)
    if options.owner_pid is None:
        if not options.standalone:parser.error('--owner-pid or --standalone is required')
        options.owner_pid=os.getpid()
    folder=options.directory;folder.mkdir(parents=True,exist_ok=True)
    from tools.web_workspace import atomic_json
    status=folder/'window-status.json'
    if not options.check_runtime:
        try:
            previous=json.loads(status.read_text())
            if previous.get('state') not in ('closed','failed') and alive(previous.get('pid')):
                if options.show:atomic_json(folder/'show.json',{'at':time.time()})
                return
        except (OSError,ValueError):pass
    def report(**value):atomic_json(status,dict(pid=os.getpid(),**value))
    process=None;url=None
    try:
        report(state='preparing',current=0,total=1)
        library=install(folder,lambda current,total:report(state='preparing',current=current,total=total))
        sys.path.insert(0,str(library))
        import webview
        if options.check_runtime:
            report(state='checked');print('Native record window runtime OK');return
        report(state='starting')
        def startup_page(message, failed=False):
            title='Could not open equipment & records / 装备与战绩窗口启动失败' if failed else 'Equipment & records / 装备与战绩'
            return ('<!doctype html><html><meta charset="utf-8"><title>StarBreak</title>'
                '<style>body{background:#0b141d;color:#e1edf5;font:16px system-ui;padding:40px;line-height:1.7}p{max-width:65em;white-space:pre-wrap}</style>'
                '<h2>'+title+'</h2><p id="startup-message">'+html.escape(message)+'</p></html>')
        class Bridge:
            def show_native_game(self):
                nonlocal visible
                visible=False;actions.post('hide')
            def hide_window(self):
                nonlocal visible
                visible=False;actions.post('hide')
        webview.settings['ALLOW_DOWNLOADS']=True
        width,height=1280,800
        if sys.platform=='darwin':
            import AppKit
            bounds=AppKit.NSScreen.mainScreen().visibleFrame().size
            width=min(width,max(640,int(bounds.width)-40));height=min(height,max(480,int(bounds.height)-50))
        elif os.name=='nt':
            import ctypes
            from ctypes import wintypes
            bounds=wintypes.RECT()
            if ctypes.windll.user32.SystemParametersInfoW(48,0,ctypes.byref(bounds),0):
                width=min(width,max(640,bounds.right-bounds.left-40));height=min(height,max(480,bounds.bottom-bounds.top-50))
        window=webview.create_window('StarBreak',html=startup_page('Preparing equipment and records…\n正在准备装备与战绩…'),js_api=Bridge(),width=width,height=height,min_size=(640,420),hidden=not options.show,background_color='#0b141d',on_top=True)
        actions=WindowActions(window)
        def pin_window():actions.post('pin')
        stopped=threading.Event()
        pin_pending=threading.Event()
        window.events.loaded+=pin_pending.set
        window.events.shown+=pin_pending.set
        visible=options.show
        def prepare():
            nonlocal process,url
            try:
                http,ws,game,bridge=ports()
                from tools.web_client import CLIENT_FILENAME
                shared_client=ROOT/'userdata/web'/CLIENT_FILENAME
                client=shared_client if shared_client.is_file() else folder/'web'/CLIENT_FILENAME
                if stopped.is_set() or not alive(options.owner_pid):return
                command=[sys.executable,'-X','utf8','-u','-m','tools.web_app','--desktop','--owner-pid',str(os.getpid()),'--client',str(client),'--no-browser','--data-dir',str(folder/'web'),'--records-dir',str(options.records_dir),'--port',str(http),'--ws-port',str(ws),'--game-port',str(game),'--bridge-port',str(bridge)]
                with (folder/'web.log').open('ab') as log:
                    offset=log.tell()
                    process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                url='http://127.0.0.1:'+str(http)
                deadline=time.monotonic()+120
                while not stopped.is_set():
                    if process.poll() is not None:raise RuntimeError(service_failure(folder/'web.log',offset,process.returncode))
                    if not alive(options.owner_pid):return
                    try:
                        with open_local(url+'/api/status') as response:response.read(1024)
                        break
                    except OSError:
                        if time.monotonic()>deadline:raise RuntimeError('Local records service timed out')
                        time.sleep(.2)
                if stopped.is_set():return
                window.load_url(url+'/?desktop=1');report(state='ready')
            except Exception as error:
                report(state='failed',error=str(error)[-2000:])
                if not stopped.is_set():window.load_html(startup_page(
                    'The local equipment service could not start. Error details:\n本地装备服务启动失败，错误详情：\n'+str(error)[-4000:]+
                    '\n\nLog / 日志: '+str(folder/'web.log'),failed=True))
        def toggle():
            nonlocal visible
            visible=not visible
            actions.post('show' if visible else 'hide')
        def closing():
            nonlocal visible
            if options.standalone:stopped.set();return
            if not stopped.is_set():
                visible=False;actions.post('hide');return False
        window.events.closing+=closing
        def monitor():
            nonlocal visible
            pin_window()
            threading.Thread(target=prepare,daemon=True).start()
            stamp=0;held={};next_keys=0;keys=()
            while not stopped.wait(.04):
                if pin_pending.is_set():pin_pending.clear();pin_window()
                if not alive(options.owner_pid):stopped.set();actions.post('close');return
                control=folder/'show.json'
                if control.is_file() and control.stat().st_mtime_ns!=stamp:
                    stamp=control.stat().st_mtime_ns;visible=True;actions.post('show')
                if os.name=='nt':
                    from plugins import _practice_keybindings as bindings
                    if time.monotonic()>=next_keys:
                        keys=bindings.editor_keys(bindings.load(ROOT/'userdata/keybindings.json'));next_keys=time.monotonic()+.5
                    for key in keys:
                        down=bool(ctypes.windll.user32.GetAsyncKeyState(key)&0x8000)
                        if down and not held.get(key):toggle()
                        held[key]=down
        window.events.closed+=lambda:stopped.set()
        webview.start(monitor,private_mode=False,storage_path=str(folder/'browser'))
        report(state='closed')
    except (Exception,SystemExit) as error:
        report(state='failed',error=str(error)[:250])
        if os.name=='nt' and not options.check_runtime and alive(options.owner_pid):
            import ctypes
            message=('Equipment & records could not start.\n装备与战绩窗口启动失败。\n\n'+str(error)[:300]+
                '\n\nDetails / 日志: '+str(folder/'window-status.json')+
                '\nRetry / 重试: tools\\open-editor.cmd')
            ctypes.windll.user32.MessageBoxW(None,message,'StarBreak',0x10)
        raise
    finally:
        if process and process.poll() is None:
            if url:
                try:
                    from urllib.request import Request
                    with open_local(Request(url+'/api/quit',data=b'{}',headers={'Content-Type':'application/json'}),timeout=2) as response:response.read()
                except OSError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)


if __name__=='__main__':main()
