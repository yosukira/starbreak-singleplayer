"""Small-screen editor containers and local shortcut preferences."""
import ctypes
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from plugins import _practice_keybindings as keybindings


def work_area(root):
    fallback = (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
    if os.name != 'nt':
        return fallback
    try:
        from ctypes import wintypes
        class MonitorInfo(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('monitor', wintypes.RECT),
                        ('work', wintypes.RECT), ('flags', wintypes.DWORD)]
        user32 = ctypes.windll.user32
        user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.MonitorFromWindow.restype = wintypes.HANDLE
        user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        info = MonitorInfo()
        info.size = ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(root.winfo_id(), 2)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return (info.work.left, info.work.top, info.work.right, info.work.bottom)
    except (AttributeError, OSError, ValueError):
        pass
    return fallback


def fit_window(root, width=None, height=None):
    left, top, right, bottom = work_area(root)
    available_w = max(240, right - left - 24)
    available_h = max(240, bottom - top - 64)
    width = min(width or max(root.winfo_width(), 240), available_w)
    height = min(height or max(root.winfo_height(), 240), available_h)
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height - 40) // 3)
    root.geometry('{0}x{1}{2:+d}{3:+d}'.format(int(width), int(height), int(x), int(y)))
    root.minsize(min(680, width, available_w), min(360, height, available_h))


class ScrollFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, highlightthickness=0, borderwidth=0, background='#101923')
        vertical = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        horizontal = ttk.Scrollbar(self, orient='horizontal', command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.content = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', self._fit)
        self.canvas.bind('<Configure>', self._fit)
        self.top = self.winfo_toplevel()
        self.wheel_id = self.top.bind('<MouseWheel>', self._wheel, add='+')
        self.bind('<Destroy>', self._destroyed)

    def _fit(self, _event=None):
        width = max(self.canvas.winfo_width(), self.content.winfo_reqwidth())
        self.canvas.itemconfigure(self.window, width=width)
        self.canvas.configure(scrollregion=self.canvas.bbox('all'))

    def _wheel(self, event):
        widget = event.widget
        while widget is not None and widget is not self.content:
            widget = getattr(widget, 'master', None)
        if widget is None:
            return
        if isinstance(event.widget, (tk.Listbox, ttk.Combobox, ttk.Spinbox, ttk.Scale)):
            return
        units = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        (self.canvas.xview_scroll if event.state & 1 else self.canvas.yview_scroll)(units, 'units')
        return 'break'

    def _destroyed(self, event):
        if event.widget is self:
            try:
                self.top.unbind('<MouseWheel>', self.wheel_id)
            except tk.TclError:
                pass


def key_dialog(editor, path):
    tr = editor.tr
    dialog = tk.Toplevel(editor.root)
    dialog.withdraw()
    dialog.configure(background='#101923')
    dialog.title(tr('按键设置'))
    dialog.transient(editor.root)
    dialog.attributes('-topmost', True)
    controls = ttk.Frame(dialog, padding=8)
    controls.pack(side='bottom', fill='x')
    area = ScrollFrame(dialog)
    area.pack(fill='both', expand=True)
    body = ttk.Frame(area.content, padding=12)
    body.pack(fill='x', expand=True)
    ttk.Label(body, text=tr('互动键默认跟随游戏绑定；识别不正确时，选择与游戏设置相同的键。'),
              wraplength=630, justify='left').grid(row=0, column=0, columnspan=4, sticky='w', pady=(0, 10))
    document = keybindings.load(path)
    variables = {}
    labels = ('互动键', '显示/隐藏编辑器', '备用编辑器快捷键', '重开当前局',
              '切换 WF', '切换 DW', '切换 FAB', '切换 IC')
    for index, (action, label) in enumerate(zip(keybindings.ACTIONS, labels)):
        row,column=index//2+1,(index%2)*2
        ttk.Label(body, text=tr(label)).grid(row=row, column=column, sticky='w', padx=(0, 10), pady=6)
        values = (('Auto',) if action == 'interact' else ('None',)) + keybindings.CHOICES
        variables[action] = tk.StringVar(value=document[action])
        ttk.Combobox(body, textvariable=variables[action], state='readonly', values=values,
                     width=14, height=12).grid(row=row, column=column+1, sticky='ew', padx=(0,18), pady=6)
    duration = tk.StringVar(value=str(document['hold_seconds']))
    ttk.Label(body, text=tr('长按时间（秒）')).grid(row=5, column=0, sticky='w', pady=3)
    ttk.Spinbox(body, from_=.5, to=5, increment=.5, textvariable=duration, width=16).grid(
        row=5, column=1, sticky='ew', pady=3)
    ttk.Label(body, text=tr('Auto = 跟随游戏；None = 关闭快捷键。移动和攻击等操作使用游戏内的按键设置。'),
              wraplength=630, justify='left').grid(row=6, column=0, columnspan=4, sticky='w', pady=(10, 0))
    ttk.Label(body, text=tr('编辑器快捷键为全局按键，建议使用 F 键。'),
              wraplength=630, justify='left').grid(row=7, column=0, columnspan=4, sticky='w', pady=(4, 0))

    def defaults():
        for action, variable in variables.items():
            variable.set(keybindings.DEFAULTS[action])
        duration.set(str(keybindings.DEFAULTS['hold_seconds']))

    def save():
        try:
            document = {action: variable.get() for action, variable in variables.items()}
            document['hold_seconds'] = duration.get()
            document = keybindings.normalize(document)
        except (ValueError, TypeError):
            messagebox.showerror(tr('按键设置'), tr('按键不能重复；保留至少一个编辑器快捷键，长按时间为 0.5–5 秒。'), parent=dialog)
            return
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix('.tmp')
            temporary.write_text(json.dumps(document, indent=2) + '\n', encoding='utf8')
            temporary.replace(target)
        except OSError as error:
            messagebox.showerror(tr('按键设置'), str(error), parent=dialog)
            return
        editor.keybindings = document
        editor._update_key_labels()
        editor.status.configure(text=tr('按键已保存，立即生效。'))
        dialog.destroy()

    ttk.Button(controls, text=tr('保存设置'), command=save).pack(side='left')
    ttk.Button(controls, text=tr('恢复默认按键'), command=defaults).pack(side='left', padx=6)
    ttk.Button(controls, text=tr('关闭'), command=dialog.destroy).pack(side='right')
    fit_window(dialog, 700, 390)
    dialog.deiconify()
    dialog.grab_set()
