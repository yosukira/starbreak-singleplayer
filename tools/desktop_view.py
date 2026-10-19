"""Native battle-record window sharing the browser UI and the existing store.

Optional, pinned viewer dependencies are cached in userdata on first use. No
package manager or executable installer is run, and the original Tk editor
keeps its own workflow and configuration files.
"""
import argparse
import hashlib
import io
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
from urllib.request import urlopen
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def alive(pid):
    if not pid:return False
    if os.name=='nt':
        import ctypes
        handle=ctypes.windll.kernel32.OpenProcess(0x1000,False,int(pid))
        if not handle:return False
        try:
            result=ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle,ctypes.byref(result))) and result.value==259
        finally:ctypes.windll.kernel32.CloseHandle(handle)
    try:os.kill(int(pid),0);return True
    except OSError:return False


def install(directory, progress=lambda *_:None):
    if sys.version_info[:2]!=(3,12):raise ValueError('The native records window requires the bundled Python 3.12 runtime')
    target=('windows-x64' if os.name=='nt' else 'macos-arm64' if platform.machine()=='arm64' else 'macos-x64')
    if os.name!='nt' and sys.platform!='darwin':raise ValueError('Native records window supports Windows and macOS')
    manifest=json.loads((ROOT/'data/desktop-view-runtime.json').read_text())[target]
    digest=hashlib.sha256(json.dumps(manifest,sort_keys=True).encode()).hexdigest()[:16]
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


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner-pid',type=int,required=True)
    parser.add_argument('--directory',type=Path,default=ROOT/'userdata/desktop-records')
    parser.add_argument('--records-dir',type=Path,default=ROOT/'userdata/battles')
    parser.add_argument('--check-runtime',action='store_true')
    options=parser.parse_args()
    folder=options.directory;folder.mkdir(parents=True,exist_ok=True)
    from tools.web_workspace import atomic_json
    status=folder/'window-status.json'
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
        http,ws,game,bridge=ports()
        log=(folder/'web.log').open('ab')
        from tools.web_client import CLIENT_FILENAME,ensure_cached_client
        shared_client=ROOT/'userdata/web'/CLIENT_FILENAME
        client=ensure_cached_client(shared_client if shared_client.is_file() else folder/'web'/CLIENT_FILENAME)
        command=[sys.executable,'-u','-m','tools.web_app','--client',str(client),'--no-browser','--data-dir',str(folder/'web'),'--records-dir',str(options.records_dir),'--port',str(http),'--ws-port',str(ws),'--game-port',str(game),'--bridge-port',str(bridge)]
        process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        url='http://127.0.0.1:'+str(http)
        deadline=time.monotonic()+120
        while True:
            if process.poll() is not None:raise RuntimeError('Local records service could not start')
            if not alive(options.owner_pid):return
            try:
                with urlopen(url+'/api/status',timeout=1) as response:response.read(1024)
                break
            except OSError:
                if time.monotonic()>deadline:raise RuntimeError('Local records service timed out')
                time.sleep(.2)
        class Bridge:
            def show_editor(self):
                report(state='editor');window.hide()
            def show_native_game(self):window.minimize()
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
        window=webview.create_window('StarBreak · Battle records',url+'/?view=records&desktop=1',js_api=Bridge(),width=width,height=height,min_size=(640,480),background_color='#0b141d')
        stopped=threading.Event()
        def monitor():
            stamp=0
            while not stopped.wait(.4):
                if not alive(options.owner_pid):window.destroy();return
                control=folder/'show.json'
                if control.is_file() and control.stat().st_mtime_ns!=stamp:
                    stamp=control.stat().st_mtime_ns;window.show();window.restore();report(state='ready')
        window.events.closed+=lambda:stopped.set()
        report(state='ready')
        webview.start(monitor,private_mode=False,storage_path=str(folder/'browser'))
        report(state='closed')
    except Exception as error:
        report(state='failed',error=str(error)[:250]);raise
    finally:
        if process and process.poll() is None:
            if url:
                try:
                    from urllib.request import Request
                    with urlopen(Request(url+'/api/quit',data=b'{}',headers={'Content-Type':'application/json'}),timeout=2) as response:response.read()
                except OSError:pass
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:process.kill()


if __name__=='__main__':main()
