"""Progress for user-launched downloads; the UI and network use separate threads."""
import queue
import threading


def run(operation):
    events=queue.Queue();result=[];error=[]
    def work():
        try:result.append(operation(lambda *value:events.put(value)))
        except BaseException as exc:error.append(exc)
    worker=threading.Thread(target=work,daemon=True);worker.start()
    window=None;unavailable=False;last=-1
    try:
        while worker.is_alive() or not events.empty():
            try:state,current,total,version=events.get(timeout=.05)
            except queue.Empty:
                if window is not None:window.update()
                continue
            while not events.empty():
                try:state,current,total,version=events.get_nowait()
                except queue.Empty:break
            percent=min(100,int(current*100/total)) if total else 0
            if percent!=last or state!='downloading':
                print(('Downloading new version / 正在下载新版本 ' if state=='downloading' else 'Verifying and installing / 正在校验并安装 ')+version+' · '+str(percent)+'% · '+format(current/1024,'.0f')+' / '+format(total/1024,'.0f')+' KB',flush=True);last=percent
            if not window and not unavailable:
                try:
                    import tkinter as tk
                    from tkinter import ttk
                    window=tk.Tk();window.title('StarBreak Update / 更新');window.geometry('510x150');window.resizable(False,False)
                    title=ttk.Label(window,padding=(16,16));title.pack(fill='x')
                    bar=ttk.Progressbar(window,maximum=100);bar.pack(fill='x',padx=16)
                    detail=ttk.Label(window,padding=(16,8));detail.pack(fill='x')
                    window.protocol('WM_DELETE_WINDOW',window.withdraw)
                except Exception:unavailable=True;window=None
            if window is not None:
                title.configure(text=('Downloading new version / 正在下载新版本 ' if state=='downloading' else 'Verifying and installing / 正在校验并安装 ')+version)
                bar.configure(value=percent);detail.configure(text=str(percent)+'% · '+format(current/1024,'.0f')+' / '+format(total/1024,'.0f')+' KB');window.update()
        worker.join()
    finally:
        if window is not None:window.destroy()
    if error:raise error[0]
    return result[0]
