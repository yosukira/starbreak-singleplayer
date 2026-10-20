"""Progress for user-launched downloads; the UI and network use separate threads."""
import queue
import threading
import os
import time
from contextlib import contextmanager


@contextmanager
def console_selection_guard(kernel=None):
    """Prevent a mouse selection from suspending this launcher's console IO."""
    changed=False
    if kernel is not None or os.name=='nt':
        try:
            import ctypes
            from ctypes import wintypes
            kernel=kernel or ctypes.windll.kernel32
            kernel.GetStdHandle.argtypes=[wintypes.DWORD];kernel.GetStdHandle.restype=wintypes.HANDLE
            kernel.GetConsoleMode.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
            kernel.SetConsoleMode.argtypes=[wintypes.HANDLE,wintypes.DWORD]
            handle=kernel.GetStdHandle(-10);mode=wintypes.DWORD()
            if kernel.GetConsoleMode(handle,ctypes.byref(mode)) and mode.value&0x40:
                previous=mode.value
                changed=bool(kernel.SetConsoleMode(handle,(previous|0x80)&~0x40))
        except (OSError,AttributeError,TypeError):pass
    try:yield
    finally:
        if changed:kernel.SetConsoleMode(handle,previous)


def describe(state,current,total,version,elapsed,speed=0):
    labels={'checking':'Checking for updates / 正在检查更新',
            'downloading':'Downloading new version / 正在下载新版本',
            'verifying':'Verifying update / 正在校验更新文件',
            'backing_up':'Backing up installed files / 正在备份原有文件',
            'installing':'Installing new version / 正在安装新版本'}
    title=labels.get(state,state)+(' '+version if version else '')
    if not total:return title+' · '+str(int(elapsed))+'s / 秒'
    percent=min(100,int(current*100/total));bar='#'*(percent//5)+'-'*(20-percent//5)
    detail=(format(current/1024,'.0f')+' / '+format(total/1024,'.0f')+' KB · '+format(speed/1024,'.0f')+' KB/s'
            if state=='downloading' else str(current)+' / '+str(total)+' files / 文件')
    return title+' ['+bar+'] '+str(percent)+'% · '+detail+' · '+str(int(elapsed))+'s / 秒'


def run(operation,popup=True):
    events=queue.Queue();result=[];error=[]
    download_started=[None]
    def notify(*value):
        if value[0]=='downloading' and download_started[0] is None:download_started[0]=time.monotonic()
        events.put(value)
    def work():
        try:result.append(operation(notify))
        except BaseException as exc:error.append(exc)
    started=time.monotonic();last_report=started;state='checking';current=total=0;version=''
    print(describe(state,current,total,version,0),flush=True)
    worker=threading.Thread(target=work,daemon=True);worker.start()
    window=None;unavailable=not popup;previous_state=state
    try:
        while worker.is_alive() or not events.empty():
            try:value=events.get(timeout=.05)
            except queue.Empty:
                if window is not None:window.update()
                value=None
            if value is not None:
                state,current,total,version=value
                while not events.empty():
                    try:state,current,total,version=events.get_nowait()
                    except queue.Empty:break
            now=time.monotonic()
            speed=current/max(.01,now-download_started[0]) if state=='downloading' and download_started[0] is not None else 0
            percent=min(100,int(current*100/total)) if total else 0
            if state!=previous_state or now-last_report>=1 or (value is not None and total and current==total):
                print(describe(state,current,total,version,now-started,speed),flush=True)
                previous_state=state;last_report=now
            if not window and not unavailable and state!='checking':
                try:
                    import tkinter as tk
                    from tkinter import ttk
                    window=tk.Tk();window.title('StarBreak Update / 更新');window.geometry('650x170');window.resizable(False,False)
                    title=ttk.Label(window,padding=(16,16),wraplength=618);title.pack(fill='x')
                    bar=ttk.Progressbar(window,maximum=100);bar.pack(fill='x',padx=16)
                    detail=ttk.Label(window,padding=(16,8));detail.pack(fill='x')
                    window.protocol('WM_DELETE_WINDOW',window.withdraw)
                except Exception:unavailable=True;window=None
            if window is not None:
                title.configure(text=describe(state,0,0,version,now-started))
                bar.configure(value=percent)
                detail.configure(text=(str(percent)+'% · '+format(current/1024,'.0f')+' / '+format(total/1024,'.0f')+' KB · '+format(speed/1024,'.0f')+' KB/s') if state=='downloading' else str(percent)+'% · '+str(current)+' / '+str(total)+' files / 文件')
                window.update()
        worker.join()
    finally:
        if window is not None:window.destroy()
    if error:raise error[0]
    return result[0]
