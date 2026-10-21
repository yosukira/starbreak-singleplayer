"""Always-on-top gear editor for the local Hulk Hard simulator.

Press the key left of ``1`` (``~`` / VK_OEM_3) to show or hide the panel.  The
panel edits the same override file as the in-client loadout builder:

    userdata/class_loadout_overrides.json

After Apply, the editor writes class_loadouts.json directly; the local
server notices the file mtime change and reloads the current class without
requiring a long-press.  Hold R=2s to restart the current class and hold
7/8/9/0 for WF/DW/FAB/IC to switch class.

This is intentionally a separate desktop overlay: it can be developed and
tested without injecting a new UI into the retail client, while still sitting
on top of the game window.
"""

import argparse
import base64
import ctypes
import hashlib
import io
import json
import math
import os
import queue
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if LAB not in sys.path:
    sys.path.insert(0, LAB)

from localserver import game_settings, jsonio, projectile_speeds, presets
from localserver.equipment import (SLOTS, STAT_NAMES, CANDIDATE_CACHE, AFFIX_DEFAULTS,
    num, load_catalogue, candidate_items, build_affix_priority, affix_default_value)
from tools import i18n, update
from tools.editor_widgets import ScrollFrame, fit_window, key_dialog, work_area
from plugins import _practice_keybindings as keybindings
from localserver.live.turrets import (normalize_missile_counts,
                                      normalize_turret_counts)

CATALOGUE = os.path.join(LAB, 'data', 'catalogue',
                         'item_descriptions.jsonl')
OVERRIDES = os.path.join(LAB, 'userdata',
                         'class_loadout_overrides.json')
AFFIX_GUIDE = os.path.join(LAB, 'data', 'mechanics',
                           'item-affix-guide.json')
CLASS_LOADOUTS = os.path.join(LAB, 'userdata',
                              'class_loadouts.json')
PRACTICE_LOADOUT = os.path.join(LAB, 'userdata',
                                'practice_loadout.json')
VK_OEM_3 = 0xC0
KEYBINDINGS = os.path.join(LAB, 'userdata', 'keybindings.json')
WINDOW_TITLE = 'StarBreak Gear Editor - ' + hashlib.sha1(LAB.encode('utf8')).hexdigest()[:8]

MUTEX_NAME = 'StarBreakGearEditor-' + hashlib.sha1(
    os.path.normcase(LAB).encode('utf-8')).hexdigest()[:16]

def process_alive(pid):
    """True while the Windows process *pid* is still running."""
    if not pid:
        return True
    kernel32 = ctypes.windll.kernel32
    synchronize = 0x00100000
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int,
                                     ctypes.c_uint32)
    kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p,
                                             ctypes.c_uint32)
    kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
    handle = kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        return False
    try:

        return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
    finally:
        kernel32.CloseHandle(handle)

CLASS_NAMES = {1: 'WF', 2: 'DW', 3: 'IC', 5: 'FAB'}
EDITOR_MAP_ORDER = game_settings.MAP_DISPLAY_ORDER
MK_VALUES = tuple('MK{0}'.format(index) for index in range(1, 7))
IMPLANT_VALUES = tuple(str(value) for value in range(9))
TURRET_ROWS = (
    ('front', '前面', 10),
    ('upper', '上面', 20),
    ('lower', '下面', 20),
)
MISSILE_ROWS = (('upper', '上追踪'), ('lower', '下追踪'))

TURRET_MAPS = (game_settings.ORIGINAL_MAP, 'hulk_normal')

BALANCE_MAPS = (game_settings.ORIGINAL_MAP, 'hulk_normal')
BALANCE_MULT_KEYS = (
    ('enemy_hp', '敌人血量'),
    ('enemy_damage', '敌人伤害'),
    ('enemy_speed', '敌人速度'),
    ('spawn_rate', '刷怪速率'),
)
BALANCE_MULT_MIN_LOG = -1.0
BALANCE_MULT_MAX_LOG = 2.0
SPAWN_CAP_MIN = 1
SPAWN_CAP_MAX = 1000



def editor_map_choices(language='en'):
    """Ordered (stable map id, display title) rows for the map picker."""
    order = dict((map_id, index)
                 for index, map_id in enumerate(EDITOR_MAP_ORDER))
    map_ids = sorted(game_settings.available_maps(),
                     key=lambda map_id: (order.get(map_id, len(order)), map_id))
    rows = [(map_id, i18n.map_title(map_id, game_settings.map_info(map_id)['title'], language))
            for map_id in map_ids]

    titles = [title for _map_id, title in rows]
    return [(map_id, '{0} ({1})'.format(title, map_id)
             if titles.count(title) > 1 else title)
            for map_id, title in rows]







def _dpi_scale(root):
    """Best-effort Windows DPI awareness and Tk point scaling."""
    dpi = 96
    try:
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        try:
            hwnd = int(root.winfo_id())
            get_dpi = getattr(user32, 'GetDpiForWindow', None)
            if get_dpi is not None:
                dpi = int(get_dpi(hwnd))
        except Exception:
            pass
        if dpi <= 0:
            hdc = user32.GetDC(0)
            try:
                dpi = int(ctypes.windll.gdi32.GetDeviceCaps(hdc, 88))
            finally:
                user32.ReleaseDC(0, hdc)
    except Exception:
        dpi = 96
    scale = max(1.0, min(2.0, dpi / 96.0))
    try:
        root.tk.call('tk', 'scaling', max(1.0, dpi / 72.0))
    except Exception:
        pass
    return scale

def _ui_font(root, scale):
    families = set()
    try:
        families = set(tkfont.families(root))
    except Exception:
        pass
    for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI',
                 'Tahoma', 'Arial'):
        if not families or name in families:
            return name, 11
    return 'TkDefaultFont', 11

def _parse_stat_id(text):
    match = re.match(r'^\s*(\d+)', str(text or ''))
    return int(match.group(1)) if match else None

def _format_number(value):
    """Percentages lose a trailing .0 so 5.0 shows as 5."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return '0'
    if number == int(number):
        return str(int(number))
    return '{0:.2f}'.format(number)

def apply_import_document(document, overrides, settings):
    """Merge an exported document into overrides + game settings.

    Older exports only carry class rows (and possibly the turret keys), so
    the map/drop fields are optional: a missing value keeps the current
    one, and an older editor importing a newer file simply ignores the
    extra underscore keys.  ``settings`` is normalized in place.
    """
    document = document or {}
    target_map = document.get('_map') or settings.get('map') or game_settings.ORIGINAL_MAP
    drop_rows = dict(settings.get('drops') or {})
    imported_drops = document.get('_drops')
    if isinstance(imported_drops, dict):
        for map_id, row in imported_drops.items():
            if isinstance(map_id, str) and isinstance(row, dict):
                drop_rows[map_id] = dict(row)
    legacy_drops = {}
    for key, rows in document.items():
        name = str(key)
        if name == '_turret_counts':
            try:
                overrides[name] = normalize_turret_counts(rows)
            except (TypeError, ValueError):
                pass
        elif name == '_turret_missiles':
            try:
                overrides[name] = normalize_missile_counts(rows)
            except (TypeError, ValueError):
                pass
        elif name == projectile_speeds.KEY:
            overrides[name] = projectile_speeds.normalize(rows)
        elif name == '_map':
            if isinstance(rows, str) and rows.strip():
                settings['map'] = rows.strip()
        elif name == '_drop_health_percent':
            legacy_drops['health_percent'] = rows
        elif name == '_drop_ammo_percent':
            legacy_drops['ammo_percent'] = rows
        elif name == '_drop_ec_percent':
            legacy_drops['ec_percent'] = rows
        elif name == '_drops':
            continue
        elif name == '_balance':
            if isinstance(rows, dict):
                merged = dict(settings.get('balance') or {})
                for map_id, entry in rows.items():
                    if isinstance(map_id, str) and isinstance(entry, dict):
                        merged[map_id] = dict(entry)
                settings['balance'] = merged
        elif name == '_missile_delay_ms':
            if isinstance(rows, dict):
                settings['missile_delay_ms'] = game_settings.normalize_missile_delays(rows)
        elif name == '_skip_intro':
            if isinstance(rows, dict):
                settings['skip_intro'] = game_settings.normalize({'skip_intro': rows})['skip_intro']
        elif name.startswith('_'):
            continue
        elif isinstance(rows, dict):
            overrides[name] = rows
    if legacy_drops and not (isinstance(imported_drops, dict) and target_map in imported_drops):
        row = game_settings.drop_settings(target_map, settings)
        row.update(legacy_drops)
        drop_rows[target_map] = row
    if legacy_drops or isinstance(imported_drops, dict):
        settings['drops'] = drop_rows
    normalized = game_settings.normalize(settings)
    settings.clear()
    settings.update(normalized)
    return overrides, settings


def load_overrides(path=OVERRIDES):
    if not os.path.exists(path):
        return {}
    return jsonio.load_json(path)[0]

def save_overrides(document, path=OVERRIDES):
    backup = path + '.bak'
    if os.path.exists(path):
        shutil.copy2(path, backup)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    os.replace(tmp, path)

def _atomic_write_json(path, document):
    backup = path + '.bak'
    if os.path.exists(path):
        shutil.copy2(path, backup)
    tmp = path + '.tmp'
    with io.open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(document, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    os.replace(tmp, path)

def _effective_item_row(slotpos, item_type, boosts):
    row = {'type_': int(item_type), 'slotpos_': int(slotpos)}
    values = [{'stat_': int(boost['stat_']),
               'val_': int(boost.get('val_', 0))}
              for boost in boosts if boost.get('stat_') is not None]
    if values:
        row['statboosts_'] = values
    return row

def _replace_slot(items, slotpos, item_type, boosts):
    result = []
    replaced = False
    for item in items or []:
        if not isinstance(item, dict):
            continue
        value = item.get('slotpos_', item.get('slotpos'))
        try:
            current = int(value)
        except (TypeError, ValueError):
            result.append(dict(item))
            continue
        if current == int(slotpos):
            if not replaced:
                result.append(_effective_item_row(slotpos, item_type, boosts))
                replaced = True
            continue
        result.append(dict(item))
    if not replaced:
        result.append(_effective_item_row(slotpos, item_type, boosts))
    result.sort(key=lambda item: int(item.get('slotpos_', 3840)))
    return result

def update_class_loadouts_file(path, char_type, slotpos, item_type, boosts):
    """Write the exact file the local server watches (not only overrides)."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    key = str(int(char_type))
    node = document.get(key)
    if not isinstance(node, dict):
        node = {'type_': int(char_type), 'items_': []}
    node['type_'] = int(char_type) if 'type_' not in node else node.get('type_')
    node['items_'] = _replace_slot(node.get('items_') or [], slotpos,
                                   item_type, boosts)
    document[key] = node
    _atomic_write_json(path, document)
    return path

def update_practice_loadout_file(path, char_type, slotpos, item_type, boosts):
    """Keep the WF compatibility loadout in sync when it applies to this class."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    try:
        document_type = int(document.get('type_', -1))
    except (TypeError, ValueError):
        document_type = -1
    if document_type != int(char_type):
        return None
    document['items_'] = _replace_slot(document.get('items_') or [], slotpos,
                                        item_type, boosts)
    _atomic_write_json(path, document)
    return path

def set_class_mk(document, char_type, mk):
    """Store the selected MK (1-6) inside a class override node."""
    value = int(mk)
    if value < 1 or value > 6:
        raise ValueError('MK must be between 1 and 6')
    class_rows = document.setdefault(str(int(char_type)), {})
    class_rows['mk_'] = value
    return value

def set_class_implants(document, char_type, count):
    """Store the selected implant-slot count (0-8)."""
    value = int(count)
    if value < 0 or value > 8:
        raise ValueError('implant slots must be between 0 and 8')
    class_rows = document.setdefault(str(int(char_type)), {})
    class_rows['impslots_'] = value
    return value

def update_class_implants_file(path, char_type, count):
    """Write the selected implant-slot count into class_loadouts.json."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    key = str(int(char_type))
    node = document.get(key)
    if not isinstance(node, dict):
        node = {'type_': int(char_type), 'items_': []}
    node['type_'] = int(char_type) if 'type_' not in node else node.get('type_')
    node['impslots_'] = set_class_implants({key: node}, key, count)
    document[key] = node
    _atomic_write_json(path, document)
    return path

def update_practice_implants_file(path, char_type, count):
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    try:
        document_type = int(document.get('type_', -1))
    except (TypeError, ValueError):
        document_type = -1
    if document_type != int(char_type):
        return None
    document['impslots_'] = int(count)
    _atomic_write_json(path, document)
    return path

def update_class_mk_file(path, char_type, mk):
    """Write the selected MK into the effective class_loadouts file."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    key = str(int(char_type))
    node = document.get(key)
    if not isinstance(node, dict):
        node = {'type_': int(char_type), 'items_': []}
    node['type_'] = int(char_type) if 'type_' not in node else node.get('type_')
    node['mk_'] = set_class_mk({key: node}, key, mk)
    document[key] = node
    _atomic_write_json(path, document)
    return path

def update_practice_mk_file(path, char_type, mk):
    """Mirror the MK into the WF practice loadout when it matches."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    try:
        document_type = int(document.get('type_', -1))
    except (TypeError, ValueError):
        document_type = -1
    if document_type != int(char_type):
        return None
    document['mk_'] = int(mk)
    _atomic_write_json(path, document)
    return path

def set_turret_counts(document, counts):
    """Store validated sandbox turret counts as top-level metadata."""
    normalized = normalize_turret_counts(counts)
    document['_turret_counts'] = normalized
    return normalized

def update_class_missile_file(path, counts):
    """Write tracking-turret counts into class_loadouts.json."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    set_turret_missiles(document, counts)
    _atomic_write_json(path, document)
    return path

def update_class_turret_file(path, counts):
    """Write turret counts into class_loadouts.json without touching gear."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    set_turret_counts(document, counts)
    _atomic_write_json(path, document)
    return path

def set_turret_missiles(document, counts):
    """Store validated upper/lower tracking-turret counts."""
    normalized = normalize_missile_counts(counts)
    document['_turret_missiles'] = normalized
    return normalized

def update_class_settings_file(path, char_type, mk, implants, counts,
                               missile_counts=None, slotpos=None,
                               item_type=None, boosts=None):
    """Apply all editor settings to class_loadouts.json in one atomic write."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    key = str(int(char_type))
    node = document.get(key)
    if not isinstance(node, dict):
        node = {'type_': int(char_type), 'items_': []}
    node['type_'] = int(char_type) if 'type_' not in node else node.get('type_')
    node['mk_'] = set_class_mk({key: node}, key, mk)
    node['impslots_'] = set_class_implants({key: node}, key, implants)
    if slotpos is not None and item_type is not None:
        node['items_'] = _replace_slot(node.get('items_') or [], slotpos,
                                       item_type, boosts or [])
    document[key] = node
    set_turret_counts(document, counts)
    set_turret_missiles(document, missile_counts)
    _atomic_write_json(path, document)
    return path

def update_practice_settings_file(path, char_type, mk, implants,
                                  slotpos=None, item_type=None, boosts=None):
    """Apply the matching subset to the WF practice loadout in one write."""
    if not os.path.exists(path):
        return None
    document = jsonio.load_json(path)[0]
    try:
        document_type = int(document.get('type_', -1))
    except (TypeError, ValueError):
        document_type = -1
    if document_type != int(char_type):
        return None
    document['mk_'] = int(mk)
    document['impslots_'] = int(implants)
    if slotpos is not None and item_type is not None:
        document['items_'] = _replace_slot(document.get('items_') or [],
                                           slotpos, item_type, boosts or [])
    _atomic_write_json(path, document)
    return path

def set_instance(document, char_type, slotpos, item_type, boosts):
    class_key = str(int(char_type))
    slot_key = str(int(slotpos))
    class_rows = document.setdefault(class_key, {})
    row = class_rows.setdefault(slot_key, {})
    row['type_'] = int(item_type)
    row['statboosts_'] = [
        {'stat_': int(boost['stat_']), 'val_': int(boost.get('val_', 0))}
        for boost in boosts if boost.get('stat_') is not None]
    row['_kind'] = 'edited-recipe'
    row['_label'] = 'gear editor'
    row['_evidence'] = 'tools/gear_editor.py'
    return row

class GearEditor(object):
    def __init__(self, root, show=True, log_path=None, owner_pid=None):
        self.root = root
        self.language_preference = i18n.load_preference()
        self.language = i18n.resolve_language(self.language_preference)
        self.keybindings = keybindings.load(KEYBINDINGS)
        self.stat_names = STAT_NAMES
        self.update_checker = update.UpdateChecker(Path(LAB))
        self.owner_pid = int(owner_pid) if owner_pid else None
        self.catalogue = load_catalogue()
        self.overrides = load_overrides()
        self.affix_priority = build_affix_priority(self.catalogue)
        self._affix_choice_cache = []
        self._affix_choice_by_stat = {}
        self.ui_scale = 1.0
        self.ui_font = ('TkDefaultFont', 11)
        self.char_type = 1
        self.slotpos = 256
        self.item_type = None
        self.affix_rows = []
        self.projectile_speed_rows = []
        self.affix_sort = 'id'
        self.item_sort = 'id'
        self.item_search_var = tk.StringVar()
        self.all_classes_var = tk.BooleanVar(value=False)
        self.mk_var = tk.StringVar(value='MK6')
        self.implant_var = tk.StringVar(value='1')
        self.turret_vars = {}
        self.turret_missile_vars = {}
        self.turret_counts = normalize_turret_counts(
            (self.overrides or {}).get('_turret_counts'))
        self.game_settings = game_settings.load()
        self.skip_intro_var = tk.BooleanVar(value=game_settings.skip_intro(
            self.game_settings.get('map'), self.game_settings))
        self.missile_delay_var = tk.StringVar(value=str(game_settings.missile_delay_ms(
            self.game_settings.get('map'), self.game_settings)))
        self.missile_delay_var.trace_add('write', self._store_missile_delay)
        self.map_var = tk.StringVar(
            value=self.game_settings.get('map') or game_settings.ORIGINAL_MAP)
        self.map_display_var = tk.StringVar()
        self.drop_health_var = tk.StringVar(value=_format_number(
            self.game_settings.get(
                'drop_health_percent',
                game_settings.DEFAULTS['drop_health_percent'])))
        self.drop_ammo_var = tk.StringVar(value=_format_number(
            self.game_settings.get(
                'drop_ammo_percent',
                game_settings.DEFAULTS['drop_ammo_percent'])))
        self.drop_ec_var = tk.StringVar(value=_format_number(
            self.game_settings.get('drop_ec_percent', game_settings.DEFAULTS['drop_ec_percent'])))
        self.balance_vars = dict(
            (key, tk.DoubleVar(value=0.0)) for key, _label in BALANCE_MULT_KEYS)

        self.balance_entry_vars = dict(
            (key, tk.StringVar(value='1')) for key, _label in BALANCE_MULT_KEYS)
        self.balance_entries = {}
        self.spawn_cap_var = tk.StringVar(value=str(
            game_settings.BALANCE_DEFAULTS['spawn_cap']))
        self._hotkey_stop = False
        self._hotkey_thread = None
        self._hotkey_down = {}
        self._hotkey_events = queue.Queue()

        self.log_path = (log_path or
                         os.path.join(LAB, 'userdata', 'logs', 'gear-editor.log'))
        self.visible = bool(show)
        self._build()
        self._load_slot()
        if not show:
            root.withdraw()
        if os.name == 'nt':
            self._start_hotkey()
        self.root.after(50, self._poll_hotkey)
        self.root.after(500, self._poll_updates)
        if self.owner_pid:
            self.root.after(2000, self._check_owner)

    def _log(self, message):
        try:
            with io.open(self.log_path, 'a', encoding='utf-8') as handle:
                handle.write('{0} {1}\n'.format(
                    time.strftime('%Y-%m-%d %H:%M:%S'), message))
        except Exception:
            pass

    def _build(self):
        self.ui_scale = _dpi_scale(self.root)
        self.ui_font = _ui_font(self.root, self.ui_scale)
        left, top, right, bottom = work_area(self.root)
        self.compact = (bottom - top) / self.ui_scale < 820
        if self.compact:
            self.ui_font = (self.ui_font[0], 9)
        ui_size = self.ui_font[1]

        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')
        except tk.TclError:
            pass
        self.root.configure(background='#101923')
        style.configure('.', font=self.ui_font, background='#101923', foreground='#d6e4ed', bordercolor='#304657', lightcolor='#304657', darkcolor='#0b131b', troughcolor='#0b131b', selectbackground='#35513a', selectforeground='#d7f5b7')
        style.configure('TFrame', background='#101923')
        style.configure('TLabel', background='#101923', foreground='#b9ccd9')
        style.configure('TButton', background='#1b3040', foreground='#d6e4ed', borderwidth=1)
        style.map('TButton', background=[('active','#2d4543'),('pressed','#35513a')], foreground=[('disabled','#617582')])
        style.configure('TEntry', fieldbackground='#0d1b26', foreground='#e0ebf1', insertcolor='#c0e975')
        style.configure('TCombobox', fieldbackground='#0d1b26', background='#1b3040', foreground='#e0ebf1', arrowcolor='#c0e975')
        style.map('TCombobox', fieldbackground=[('readonly','#0d1b26')], foreground=[('readonly','#e0ebf1')], selectbackground=[('readonly','#0d1b26')], selectforeground=[('readonly','#e0ebf1')])
        style.configure('TNotebook', background='#0b131b', borderwidth=0)
        style.configure('TNotebook.Tab', background='#152633', foreground='#91a9b9', padding=(18,8))
        style.map('TNotebook.Tab', background=[('selected','#c0e975'),('!selected','#152633')], foreground=[('selected','#122014'),('!selected','#91a9b9')])
        style.configure('TLabelframe', background='#101923', bordercolor='#304657')
        style.configure('TLabelframe.Label', background='#101923', foreground='#c0e975')
        style.configure('Treeview', background='#122330', fieldbackground='#122330', foreground='#d6e4ed')
        style.configure('Treeview.Heading', background='#1b3040', foreground='#91a9b9')
        self.root.option_add('*TCombobox*Listbox.background','#0d1b26')
        self.root.option_add('*TCombobox*Listbox.foreground','#d6e4ed')
        style.configure('TLabel', font=self.ui_font)
        style.configure('TButton', font=self.ui_font, padding=(6, 3) if self.compact else (10, 6))
        style.configure('TCombobox', font=self.ui_font, padding=(4, 4))
        style.configure('TEntry', font=self.ui_font, padding=(4, 4))
        style.configure('Title.TLabel', font=(self.ui_font[0], ui_size + 4, 'bold'))
        style.configure('Hint.TLabel', foreground='#91a9b9')
        try:
            self.root.option_add('*TCombobox*Listbox.font', self.ui_font)
        except tk.TclError:
            pass

        self.root.title(WINDOW_TITLE)
        self.root.attributes('-topmost', True)
        self.root.attributes('-alpha', 0.97)
        fit_window(self.root, int(1180 * self.ui_scale), int(790 * self.ui_scale))
        self.root.resizable(True, True)
        self.root.protocol('WM_DELETE_WINDOW', self.hide)

        header = ttk.Frame(self.root, padding=(12, 10, 12, 4))
        header.pack(fill='x')
        ttk.Label(header, text=self.tr('StarBreak 装备编辑'),
                  style='Title.TLabel').pack(side='left')
        ttk.Label(header,text=update.version(Path(LAB)).get('version','development'),style='Hint.TLabel').pack(side='left',padx=12)
        hotkeys = ttk.Frame(self.root, padding=(12, 0, 12, 4))
        hotkeys.pack(fill='x')
        self.key_hint = ttk.Label(hotkeys, style='Hint.TLabel',
                                 wraplength=max(300, right - left - 60))
        self.key_hint.pack(side='left')

        self.language_var = tk.StringVar(value=i18n.LANGUAGES[self.language_preference])
        ttk.Label(header, text=self.tr('语言')).pack(side='right', padx=(8, 4))
        language_box = ttk.Combobox(header, state='readonly', width=22,
                                   textvariable=self.language_var,
                                   values=tuple(i18n.LANGUAGES.values()))
        language_box.pack(side='right')
        language_box.bind('<<ComboboxSelected>>', self._change_language)
        ttk.Button(header, text=self.tr('按键设置'),
                   command=lambda: key_dialog(self, KEYBINDINGS)).pack(side='right', padx=6)

        self.status = ttk.Label(self.root, padding=(12, 4),
                                wraplength=max(300, right - left - 60))
        self.status.pack(fill='x', side='bottom')
        update_row = ttk.Frame(self.root, padding=(12, 4))
        update_row.pack(fill='x', side='bottom')
        self.update_button = ttk.Button(update_row, text=self.tr('检查更新'), command=self._check_updates)
        self.update_button.pack(side='left')
        self.update_label = ttk.Label(update_row, wraplength=max(240, min(650, right-left-180)), style='Hint.TLabel')
        self.update_label.pack(side='left', fill='x', expand=True, padx=(12, 0))
        self._render_update_status()
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill='both', expand=True)
        gear_page = ttk.Frame(notebook)
        notebook.add(gear_page, text=self.tr('装备与设置'))
        from tools.battle_panel import BattlePanel
        self.battle_panel = BattlePanel(notebook, self.language)
        notebook.add(self.battle_panel, text=self.tr('战斗记录'))
        self.notebook,self.gear_page=notebook,gear_page
        ttk.Button(self.battle_panel,text=self.tr('打开完整战斗记录'),command=self._open_records_window).pack(side='bottom',fill='x',pady=8)
        notebook.bind('<<NotebookTabChanged>>',lambda event:self._open_records_window() if notebook.select()==str(self.battle_panel) and self.root.winfo_viewable() else None)

        actions = ttk.Frame(gear_page, padding=(8, 4))
        actions.pack(side='bottom', fill='x')
        self.viewport = ScrollFrame(gear_page)
        self.viewport.pack(fill='both', expand=True)
        content = self.viewport.content

        top = ttk.Frame(content, padding=(12, 4, 12, 8))
        top.pack(fill='x')
        ttk.Label(top, text=self.tr('职业')).pack(side='left', padx=(0, 8))
        self.class_buttons = {}
        for char_type, name in ((1, 'WF'), (2, 'DW'), (3, 'IC'), (5, 'FAB')):
            button = ttk.Button(top, text=name, width=8,
                                command=lambda value=char_type: self.select_class(value))
            button.pack(side='left', padx=3)
            self.class_buttons[char_type] = button
        ttk.Label(top, text=self.tr('角色 MK')).pack(side='left', padx=(18, 6))
        self.mk_box = ttk.Combobox(top, textvariable=self.mk_var,
                                   values=MK_VALUES, state='readonly',
                                   width=6, font=self.ui_font)
        self.mk_box.pack(side='left')
        ttk.Label(top, text=self.tr('植入体槽')).pack(side='left', padx=(14, 6))
        self.implant_box = ttk.Combobox(top, textvariable=self.implant_var,
                                        values=IMPLANT_VALUES,
                                        state='readonly', width=5,
                                        font=self.ui_font)
        self.implant_box.pack(side='left')

        settings_row = ttk.Frame(content, padding=(12, 0, 12, 8))
        settings_row.pack(fill='x')
        ttk.Label(settings_row, text=self.tr('地图')).pack(side='left', padx=(0, 6))
        self.map_box = ttk.Combobox(
            settings_row, textvariable=self.map_display_var,
            state='readonly', width=36, height=30, font=self.ui_font)
        self.map_box.pack(side='left')
        self.map_box.bind('<<ComboboxSelected>>', self._on_map_choice)
        self._refresh_map_choices()
        ttk.Label(settings_row, text=self.tr('血包 %')).pack(side='left', padx=(16, 4))
        self.drop_health_spin = ttk.Spinbox(
            settings_row, from_=0, to=100, increment=1, width=6,
            textvariable=self.drop_health_var, font=self.ui_font)
        self.drop_health_spin.pack(side='left')
        ttk.Label(settings_row, text='EC %').pack(side='left', padx=(12, 4))
        self.drop_ec_spin = ttk.Spinbox(
            settings_row, from_=0, to=100, increment=1, width=6,
            textvariable=self.drop_ec_var, font=self.ui_font)
        self.drop_ec_spin.pack(side='left')
        ttk.Label(settings_row, text=self.tr('弹药 %')).pack(side='left', padx=(12, 4))
        self.drop_ammo_spin = ttk.Spinbox(
            settings_row, from_=0, to=100, increment=1, width=6,
            textvariable=self.drop_ammo_var, font=self.ui_font)
        self.drop_ammo_spin.pack(side='left')
        ttk.Button(settings_row, text=self.tr('保存设置'),
                   command=self.save_game_settings).pack(side='left',
                                                         padx=(14, 0))
        ttk.Label(settings_row,
                  text=self.tr('（当前地图，各自保存）'),
                  style='Hint.TLabel').pack(side='left', padx=(10, 0))

        self.balance_row = ttk.Frame(content, padding=(12, 0, 12, 8))
        self.balance_row.pack(fill='x')
        balance = ttk.Frame(self.balance_row)
        balance.pack(fill='x')
        for index, (key, label) in enumerate(BALANCE_MULT_KEYS):
            ttk.Label(balance, text=self.tr(label)).pack(
                side='left', padx=((0 if index == 0 else 16), 4))
            self._add_balance_scale(balance, key)
        cap_row = ttk.Frame(self.balance_row)
        cap_row.pack(fill='x', pady=(4, 0))
        ttk.Label(cap_row, text=self.tr('刷怪上限')).pack(side='left', padx=(0, 4))
        self.spawn_cap_spin = ttk.Spinbox(
            cap_row, from_=SPAWN_CAP_MIN, to=SPAWN_CAP_MAX, increment=10,
            width=6, textvariable=self.spawn_cap_var, font=self.ui_font)
        self.spawn_cap_spin.pack(side='left')
        ttk.Label(cap_row,
                  text=self.tr('（0.1×–100×，可直接输入；1×=原版；数值对新刷出的怪/弹生效，上限=房间同时存活地面怪，飞机不计）'),
                  style='Hint.TLabel').pack(side='left', padx=(14, 0))

        self.turret_row = ttk.Frame(content, padding=(12, 0, 12, 8))
        self.turret_row.pack(fill='x')
        turrets = self.turret_row
        ttk.Label(turrets, text=self.tr('炮台数量')).pack(side='left', padx=(0, 8))
        self.turret_widgets = []
        for key, label, maximum in TURRET_ROWS:
            ttk.Label(turrets, text=self.tr(label)).pack(side='left', padx=(10, 3))
            variable = tk.StringVar(value=self.tr('随机'))
            combo = ttk.Combobox(
                turrets, textvariable=variable, state='readonly', width=6,
                values=(self.tr('随机'),) + tuple(str(value) for value in range(maximum + 1)),
                font=self.ui_font)
            combo.pack(side='left')
            self.turret_vars[key] = variable
            self.turret_widgets.append(combo)
        for key, label in MISSILE_ROWS:
            ttk.Label(turrets, text=self.tr(label)).pack(side='left', padx=(10, 3))
            variable = tk.StringVar(value=self.tr('随机'))
            combo = ttk.Combobox(
                turrets, textvariable=variable, state='readonly', width=6,
                values=(self.tr('随机'),) + tuple(str(value) for value in range(21)),
                font=self.ui_font)
            combo.pack(side='left')
            self.turret_missile_vars[key] = variable
            self.turret_widgets.append(combo)
        self.turret_scope = ttk.Label(turrets, text='', style='Hint.TLabel')
        self.turret_scope.pack(side='left', padx=(12, 0))

        self.intro_row = ttk.Frame(content, padding=(12, 0, 12, 8))
        self.intro_row.pack(fill='x')
        ttk.Checkbutton(self.intro_row, text=self.tr('跳过开局'), variable=self.skip_intro_var,
                        command=self._store_intro_choice).pack(side='left')
        ttk.Label(self.intro_row, text=self.tr('新一局生效；直接从首领飞船停稳后开始'),
                  style='Hint.TLabel').pack(side='left', padx=(12, 0))

        self.missile_delay_row = ttk.Frame(content, padding=(12, 0, 12, 8))
        self.missile_delay_row.pack(fill='x')
        ttk.Label(self.missile_delay_row, text=self.tr('追踪弹延迟 (ms)')).pack(side='left')
        ttk.Spinbox(self.missile_delay_row, from_=0, to=500, increment=1,
                    textvariable=self.missile_delay_var, width=7).pack(side='left', padx=(8, 12))
        ttk.Label(self.missile_delay_row,
                  text=self.tr('首领与小怪共用；瞄准玩家多少毫秒前的位置；0 = 当前位置，默认 275ms'),
                  style='Hint.TLabel').pack(side='left')

        body = ttk.Frame(content, padding=(12, 4, 12, 8))
        body.pack(fill='both', expand=True)
        self.body_frame = body
        left = ttk.Frame(body)
        left.pack(side='left', fill='y', padx=(0, 12))
        ttk.Label(left, text=self.tr('槽位')).pack(anchor='w', pady=(0, 6))
        self.slot_list = tk.Listbox(
            left, width=24, exportselection=False, font=self.ui_font,
            activestyle='dotbox', selectborderwidth=0, highlightthickness=1, background='#122330', foreground='#b9ccd9', selectbackground='#35513a', selectforeground='#d7f5b7', highlightbackground='#304657')
        for slotpos, label in SLOTS:
            self.slot_list.insert('end', '{0}  {1}'.format(slotpos, self.tr(label)))
        self.slot_list.pack(fill='y', expand=True)
        self.slot_list.bind('<<ListboxSelect>>', self._on_slot_select)

        right = ttk.Frame(body)
        right.pack(side='left', fill='both', expand=True)
        ttk.Label(right, text=self.tr('物品 type / 名称')).pack(anchor='w')
        ttk.Label(right, text=self.tr('搜索物品名称或 ID')).pack(anchor='w', pady=(6, 0))
        self.item_search = ttk.Entry(right, textvariable=self.item_search_var)
        self.item_search.pack(fill='x', pady=(4, 0))
        self.item_search.bind('<KeyRelease>', self._refresh_item_options)
        item_controls = ttk.Frame(right)
        item_controls.pack(fill='x', pady=(4, 0))
        ttk.Checkbutton(item_controls, text=self.tr('显示所有职业装备'), variable=self.all_classes_var,
                        command=self._refresh_item_options).pack(side='left')
        self.item_sort_button = ttk.Button(item_controls, text=self.tr('装备排序：ID') if self.item_sort == 'id' else self.tr('装备排序：首字母'), command=self.toggle_item_sort)
        self.item_sort_button.pack(side='left', padx=(10, 0))
        self.item_var = tk.StringVar()
        self.item_box = ttk.Combobox(right, textvariable=self.item_var,
                                     state='readonly', font=self.ui_font)
        self.item_box.pack(fill='x', pady=(4, 0))
        self.item_box.bind('<<ComboboxSelected>>', self._on_item_select)

        self.base_label = ttk.Label(
            right, text=self.tr('基础属性：—'), justify='left',
            wraplength=500 if self.compact else 750)
        self.base_label.pack(anchor='w', pady=(10, 8))

        self.projectile_frame = ttk.LabelFrame(right, text=self.tr('子弹基础速度'), padding=6)
        self.projectile_frame.pack(fill='x', pady=(0, 8))

        ttk.Label(right, text=self.tr('实例词缀（最终值 = 描述基础值 + 这里）')).pack(anchor='w')
        self.affix_hint = ttk.Label(
            right, text=self.tr('词缀顺序：本武器实测优先；其余按 ID 或首字母排序'),
            style='Hint.TLabel', justify='left',
            wraplength=500 if self.compact else 750)
        self.affix_hint.pack(anchor='w', pady=(3, 6))

        self.affix_frame = ttk.Frame(right)
        self.affix_frame.pack(fill='both', expand=True, pady=(0, 6))

        primary_actions = ttk.Frame(actions) if self.compact else actions
        secondary_actions = ttk.Frame(actions) if self.compact else actions
        if self.compact:
            primary_actions.pack(fill='x', pady=(0, 3))
            secondary_actions.pack(fill='x')
        ttk.Button(primary_actions, text=self.tr('应用/保存'), command=self.apply).pack(side='left')
        ttk.Button(primary_actions, text=self.tr('导出'), command=self.export).pack(side='left', padx=6)
        ttk.Button(primary_actions, text=self.tr('导入'), command=self.import_file).pack(side='left', padx=6)
        ttk.Button(primary_actions, text=self.tr('重新载入'), command=self.reload).pack(side='left', padx=6)
        ttk.Button(primary_actions, text=self.tr('恢复默认配置'), command=self.restore_defaults).pack(side='left', padx=6)
        self.affix_sort_button = ttk.Button(
            secondary_actions, text=self.tr('词缀排序：ID'), command=self.toggle_affix_sort)
        self.affix_sort_button.pack(side='left', padx=6)
        ttk.Button(secondary_actions, text=self.tr('退出'), command=self.close).pack(side='right')
        ttk.Button(secondary_actions, text=self.tr('隐藏'), command=self.hide).pack(
            side='right', padx=6)
        ttk.Button(secondary_actions, text=self.tr('+ 添加词缀'), command=self.add_affix).pack(side='left', padx=4)
        self._update_key_labels()
        self.status.configure(text=self.tr('保存后自动重开应用。内容可滚动，Shift + 滚轮可左右滚动。'))
        self._on_map_select()

    def _update_key_labels(self):
        document = self.keybindings
        self.key_hint.configure(text=self.tr('显示/隐藏：{0} / {1}；长按 {2} 秒：重开 {3}，WF {4}，DW {5}，FAB {6}，IC {7}').format(
            document['editor_toggle'], document['editor_toggle_alt'], document['hold_seconds'],
            document['restart'], document['wf'], document['dw'], document['fab'], document['ic']))

    def tr(self, text):
        return i18n.translate(text, self.language)

    def _change_language(self, _event=None):
        preference = next((key for key, label in i18n.LANGUAGES.items()
                           if label == self.language_var.get()), 'auto')
        language = i18n.resolve_language(preference)
        try:
            i18n.save_preference(preference)
        except OSError as error:
            messagebox.showerror(self.tr('Gear Editor'), str(error))
            self.language_var.set(i18n.LANGUAGES[self.language_preference])
            return
        self.language_preference = preference
        if language == self.language:
            return
        values = {name: getattr(self, name).get() for name in (
            'mk_var', 'implant_var', 'map_var', 'drop_health_var',
            'drop_ammo_var', 'drop_ec_var', 'spawn_cap_var')}
        values['skip_intro_var'] = self.skip_intro_var.get()
        values['missile_delay_var'] = self.missile_delay_var.get()
        multipliers = {key: var.get() for key, var in self.balance_entry_vars.items()}
        turret_values = {key: var.get() for key, var in self.turret_vars.items()}
        missile_values = {key: var.get() for key, var in self.turret_missile_vars.items()}
        old_random = self.tr('随机')
        item_type = self.item_type
        boosts = [{'stat_': _parse_stat_id(stat.get()), 'val_': value.get()}
                  for _frame, stat, value, _combo in self.affix_rows]
        projectile_values = {obj['index']: variable.get() for obj, variable in self.projectile_speed_rows}
        geometry = self.root.geometry()
        self.language = language
        self.stat_names = STAT_NAMES
        for child in self.root.winfo_children():
            child.destroy()
        self.affix_rows = []
        self.projectile_speed_rows = []
        self._affix_choice_cache = []
        self._affix_choice_by_stat = {}
        self.balance_entries = {}
        self._build()
        self._load_slot()
        for name, value in values.items():
            getattr(self, name).set(value)
        for key, value in multipliers.items():
            self.balance_entry_vars[key].set(value)
            try:self.balance_vars[key].set(math.log10(float(value)))
            except (ValueError, TypeError):pass
        for controls, saved in ((self.turret_vars, turret_values),
                                (self.turret_missile_vars, missile_values)):
            for key, value in saved.items():
                controls[key].set(self.tr('随机') if value == old_random else value)
        self.map_display_var.set(self._map_choice_titles.get(self.map_var.get(), self.map_var.get()))
        if item_type is not None:
            item = self.catalogue.get(item_type, {})
            self.item_var.set('{0}  {1}'.format(item_type, item.get('name', item_type)))
            self._on_item_select()
        self._set_affixes(boosts)
        for obj, variable in self.projectile_speed_rows:
            if obj['index'] in projectile_values:
                variable.set(projectile_values[obj['index']])
        self.affix_sort_button.configure(text=self.tr('词缀排序：ID') if self.affix_sort == 'id' else self.tr('词缀排序：首字母'))
        self.root.geometry(geometry)

    def select_class(self, char_type):
        self.char_type = int(char_type)
        self._load_slot()

    def _on_slot_select(self, _event=None):
        selection = self.slot_list.curselection()
        if selection:
            self.slotpos = SLOTS[int(selection[0])][0]
            self._load_slot()

    def _refresh_item_options(self, _event=None):
        query = self.item_search_var.get().strip().casefold()
        def visible(item):
            return (
                (not query or query in (str(item['type'])+' '+item.get('name','')).casefold())
                and (self.all_classes_var.get()
                     or not item.get('usableby') or self.char_type in item['usableby']))
        values = [item for item in candidate_items(self.catalogue, self.char_type, self.slotpos) if visible(item)]
        def priority(item):
            compatible = not item.get('usableby') or self.char_type in item['usableby']
            return (not compatible, item.get('name','').casefold() if self.item_sort == 'name' else '', int(item['type']))
        values.sort(key=priority)
        self.item_box['values'] = ['{0}  {1}'.format(item['type'],item['name']) for item in values]

    def toggle_item_sort(self):
        self.item_sort = 'name' if self.item_sort == 'id' else 'id'
        self.item_sort_button.configure(text=self.tr('装备排序：ID') if self.item_sort == 'id' else self.tr('装备排序：首字母'))
        self._refresh_item_options()

    def _on_item_select(self, _event=None):
        text = self.item_var.get()
        if not text:
            return
        try:
            selected_type = int(text.split(' ', 1)[0])
        except (ValueError, IndexError):
            return
        previous_type = self.item_type
        boosts = None
        if previous_type is not None and selected_type != previous_type:
            current = self._current_boosts()
            drafts = getattr(self, '_item_affix_drafts', {})
            self._item_affix_drafts = drafts
            drafts[(self.char_type, self.slotpos, previous_type)] = current
            selected_key = (self.char_type, self.slotpos, selected_type)
            saved = (self.overrides.get(str(self.char_type)) or {}).get(str(self.slotpos)) or {}
            if selected_key in drafts:
                boosts = drafts[selected_key]
            elif saved.get('type_') == selected_type:
                boosts = saved.get('statboosts_') or []
            else:
                boosts = []
                known = self.affix_priority.get('defaults', {}).get(selected_type, {})
                for row in current:
                    boost = dict(row)
                    stat = boost['stat_']
                    old_default = affix_default_value(self.affix_priority, previous_type, stat)
                    if (boost.get('val_', 0) == old_default
                            and (stat in known or stat in AFFIX_DEFAULTS)):
                        boost['val_'] = affix_default_value(self.affix_priority, selected_type, stat)
                    boosts.append(boost)
        self.item_type = selected_type
        item = self.catalogue.get(self.item_type)
        self.base_label.configure(text=self.tr('基础属性：{0}').format(
            self._format_boosts(item.get('base') if item else [])))
        self._refresh_affix_options()
        if boosts is not None:
            self._set_affixes(boosts)
        self._load_projectile_speeds()


    def _slot_index(self):
        for index, (slotpos, _label) in enumerate(SLOTS):
            if slotpos == self.slotpos:
                return index
        return 0

    def _load_settings_controls(self):
        rows = self.overrides.get(str(self.char_type)) or {}
        try:
            mk = int(rows.get('mk_', 6))
        except (TypeError, ValueError):
            mk = 6
        self.mk_var.set('MK{0}'.format(max(1, min(6, mk))))
        try:
            implants = int(rows.get('impslots_', 1))
        except (TypeError, ValueError):
            implants = 1
        self.implant_var.set(str(max(0, min(8, implants))))
        try:
            counts = normalize_turret_counts(
                self.overrides.get('_turret_counts') or self.turret_counts)
        except (TypeError, ValueError):
            counts = normalize_turret_counts({})
        self.turret_counts = counts
        for key, _label, _maximum in TURRET_ROWS:
            value = counts.get(key)
            self.turret_vars[key].set(self.tr('随机') if value is None else str(value))
        try:
            missiles = normalize_missile_counts(
                self.overrides.get('_turret_missiles'))
        except (TypeError, ValueError):
            missiles = normalize_missile_counts({})
        for key, _label in MISSILE_ROWS:
            value = missiles.get(key)
            self.turret_missile_vars[key].set(
                self.tr('随机') if value is None else str(value))

    def _selected_mk(self):
        try:
            return max(1, min(6, int(self.mk_var.get().replace('MK', ''))))
        except (TypeError, ValueError):
            return 6

    def _selected_implants(self):
        try:
            return max(0, min(8, int(self.implant_var.get())))
        except (TypeError, ValueError):
            return 1

    def _selected_turret_counts(self):
        values = {}
        for key, _label, _maximum in TURRET_ROWS:
            text = self.turret_vars[key].get().strip()
            values[key] = None if text in ('', self.tr('随机')) else int(text)
        return normalize_turret_counts(values)

    def _selected_turret_missiles(self, turret_counts=None):
        values = {}
        for key, _label in MISSILE_ROWS:
            text = self.turret_missile_vars[key].get().strip()
            values[key] = None if text in ('', self.tr('随机')) else int(text)
        values = normalize_missile_counts(values)
        if turret_counts:
            for key in ('upper', 'lower'):
                limit = turret_counts.get(key)
                if limit is not None and values.get(key) is not None:
                    values[key] = min(values[key], limit)
        return values

    def _refresh_map_choices(self):
        rows = editor_map_choices(self.language)
        self._map_choice_ids = [map_id for map_id, _title in rows]
        self._map_choice_titles = dict(rows)
        self.map_box.configure(values=[title for _map_id, title in rows])
        self.map_display_var.set(self._map_choice_titles.get(
            self.map_var.get(), self.map_var.get()))

    def _on_map_choice(self, _event=None):
        if not self._valid_missile_delay() or not self._stash_drop_controls():
            self._refresh_map_choices()
            return
        index = self.map_box.current()
        if 0 <= index < len(self._map_choice_ids):
            self.map_var.set(self._map_choice_ids[index])
        self._on_map_select()

    def _current_drop_entry(self, map_id=None):
        map_id = map_id or self.map_var.get()
        row = game_settings.drop_settings(map_id, getattr(self, 'game_settings', None))
        for key, attribute in (('health_percent', 'drop_health_var'),
                               ('ammo_percent', 'drop_ammo_var'), ('ec_percent', 'drop_ec_var')):
            variable = getattr(self, attribute, None)
            if variable is not None:
                value = float(variable.get())
                if not math.isfinite(value):
                    raise ValueError('invalid drop percentage')
                row[key] = max(0., min(100., value))
        return row

    def _stash_drop_controls(self):
        map_id = getattr(self, '_drop_controls_map', None)
        if map_id is None:
            return True
        try:
            row = self._current_drop_entry(map_id)
        except (TypeError, ValueError):
            messagebox.showwarning(self.tr('Gear Editor'), self.tr('掉落率请填 0-100 的数字'))
            return False
        rows = dict(self.game_settings.get('drops') or {})
        rows[map_id] = row
        self.game_settings['drops'] = rows
        return True

    def _load_drop_controls(self):
        map_id = self.map_var.get()
        row = game_settings.drop_settings(map_id, getattr(self, 'game_settings', None))
        for key, attribute in (('health_percent', 'drop_health_var'),
                               ('ammo_percent', 'drop_ammo_var'), ('ec_percent', 'drop_ec_var')):
            variable = getattr(self, attribute, None)
            if variable is not None:
                variable.set(_format_number(row[key]))
        self._drop_controls_map = map_id

    def _on_map_select(self, _event=None):
        """Hide the turret row for maps that do not use it.

        The original egy_hulk session and hulk_normal both roll these
        rows from the editor sandbox, so the controls stay visible for
        them and are hidden entirely on maps that run their own scripts
        (packed back in the same position when a supported map returns).
        """
        if getattr(self, '_drop_controls_map', None) != self.map_var.get():
            self._load_drop_controls()
        display = getattr(self, 'map_display_var', None)
        if display is not None:
            display.set(self._map_choice_titles.get(
                self.map_var.get(), self.map_var.get()))
        self.skip_intro_var.set(game_settings.skip_intro(self.map_var.get(), self.game_settings))
        intro = self.intro_row
        if self.map_var.get() in game_settings.SKIP_INTRO_MAPS:
            if not intro.winfo_manager():
                intro.pack(fill='x', before=self.body_frame)
        elif intro.winfo_manager():
            intro.pack_forget()
        delay_row = self.missile_delay_row
        if self.map_var.get() in game_settings.MISSILE_DELAY_MAPS:
            self.missile_delay_var.set(str(game_settings.missile_delay_ms(self.map_var.get(), self.game_settings)))
            if not delay_row.winfo_manager():
                delay_row.pack(fill='x', before=self.body_frame)
        elif delay_row.winfo_manager():
            delay_row.pack_forget()
        supported = self.map_var.get() in TURRET_MAPS
        row = getattr(self, 'turret_row', None)
        if row is not None:
            if supported:
                if not row.winfo_manager():
                    body = getattr(self, 'body_frame', None)
                    if body is not None:
                        row.pack(fill='x', before=body)
                    else:
                        row.pack(fill='x')
            elif row.winfo_manager():
                row.pack_forget()
        scope = getattr(self, 'turret_scope', None)
        if scope is not None:
            scope.configure(text=self.tr('（egy / hulk_normal 可用）'))
        self._sync_balance_row()

    def _add_balance_scale(self, parent, key):
        variable = self.balance_vars[key]
        scale = ttk.Scale(
            parent, from_=BALANCE_MULT_MIN_LOG, to=BALANCE_MULT_MAX_LOG,
            length=92, variable=variable,
            command=lambda _value, name=key: self._refresh_balance_entry(name))
        scale.pack(side='left')
        ttk.Label(parent, text='×').pack(side='left', padx=(3, 0))
        entry = ttk.Entry(parent, width=6,
                          textvariable=self.balance_entry_vars[key])
        entry.pack(side='left', padx=(1, 0))
        entry.bind('<Return>',
                   lambda _event, name=key: self._commit_balance_entry(name))
        entry.bind('<KP_Enter>',
                   lambda _event, name=key: self._commit_balance_entry(name))
        entry.bind('<FocusOut>',
                   lambda _event, name=key: self._commit_balance_entry(name))
        entry.bind('<Escape>',
                   lambda _event, name=key: self._refresh_balance_entry(name))
        self.balance_entries[key] = entry

    @staticmethod
    def _format_balance_value(log_value):
        """Slider position -> the text shown in the editable shortcut."""
        try:
            value = 10.0 ** float(log_value)
        except (TypeError, ValueError):
            value = 1.0
        value = max(0.1, min(100.0, value))
        if value >= 10:
            if abs(value - round(value)) < 1e-9:
                return str(int(round(value)))
            return '{0:.1f}'.format(value)
        return '{0:g}'.format(round(value, 2))

    def _refresh_balance_entry(self, key):
        entry = getattr(self, 'balance_entry_vars', {}).get(key)
        variable = getattr(self, 'balance_vars', {}).get(key)
        if entry is None or variable is None:
            return
        entry.set(self._format_balance_value(variable.get()))

    def _commit_balance_entry(self, key):
        """Typed value -> slider position; invalid text reverts."""
        entry = getattr(self, 'balance_entry_vars', {}).get(key)
        variable = getattr(self, 'balance_vars', {}).get(key)
        if entry is None or variable is None:
            return
        try:
            value = float(str(entry.get()).strip())
        except (TypeError, ValueError):
            self._refresh_balance_entry(key)
            return
        value = max(0.1, min(100.0, value))
        variable.set(math.log10(value))
        entry.set(self._format_balance_value(math.log10(value)))

    def _balance_multiplier(self, key):
        """The written value: the entry when set, otherwise the slider."""
        entry = getattr(self, 'balance_entry_vars', {}).get(key)
        if entry is not None:
            text = str(entry.get()).strip()
            if text:
                value = float(text)
                return round(max(0.1, min(100.0, value)), 4)
        value = float(self.balance_vars[key].get())
        value = max(BALANCE_MULT_MIN_LOG, min(BALANCE_MULT_MAX_LOG, value))
        return round(10.0 ** value, 4)

    def _current_balance_entry(self):
        """Slider/entry values as a settings row; None without widgets."""
        variables = getattr(self, 'balance_vars', None)
        if not variables:
            return None
        entry = {}
        for key, _label in BALANCE_MULT_KEYS:
            entry[key] = self._balance_multiplier(key)
        cap = int(round(float(self.spawn_cap_var.get())))
        entry['spawn_cap'] = max(SPAWN_CAP_MIN, min(SPAWN_CAP_MAX, cap))
        return entry

    def _set_balance_controls(self, map_id):
        variables = getattr(self, 'balance_vars', None)
        if not variables:
            return
        values = game_settings.balance(
            map_id, getattr(self, 'game_settings', None))
        for key, _label in BALANCE_MULT_KEYS:
            value = float(values.get(key) or 1.0)
            value = max(0.1, min(100.0, value))
            variables[key].set(math.log10(value))
        cap = int(values.get('spawn_cap')
                  or game_settings.BALANCE_DEFAULTS['spawn_cap'])
        self.spawn_cap_var.set(str(cap))
        for key, _label in BALANCE_MULT_KEYS:
            self._refresh_balance_entry(key)

    def _sync_balance_row(self):
        """Show the row on the Hulk pair and load the selected map's row."""
        row = getattr(self, 'balance_row', None)
        if row is None:
            return
        if self.map_var.get() in BALANCE_MAPS:
            if not row.winfo_manager():
                anchor = getattr(self, 'turret_row', None)
                body = getattr(self, 'body_frame', None)
                if (anchor is not None
                        and getattr(anchor, 'winfo_manager', None)
                        and anchor.winfo_manager()):
                    row.pack(fill='x', before=anchor)
                elif body is not None:
                    row.pack(fill='x', before=body)
                else:
                    row.pack(fill='x')
            self._set_balance_controls(self.map_var.get())
        elif row.winfo_manager():
            row.pack_forget()

    def _store_missile_delay(self, *_args):
        map_id = self.map_var.get()
        if map_id not in game_settings.MISSILE_DELAY_MAPS:
            return True
        try:
            value = int(self.missile_delay_var.get())
            if not 0 <= value <= 500:
                return False
        except (ValueError, TypeError):
            return False
        rows = dict(self.game_settings.get('missile_delay_ms') or {})
        rows[map_id] = value
        self.game_settings['missile_delay_ms'] = rows
        return True

    def _valid_missile_delay(self):
        if self._store_missile_delay():
            return True
        messagebox.showwarning(self.tr('Gear Editor'), self.tr('追踪弹延迟请填 0–500 的整数'))
        return False

    def _store_intro_choice(self):
        map_id = self.map_var.get()
        if map_id in game_settings.SKIP_INTRO_MAPS:
            rows = dict(self.game_settings.get('skip_intro') or {})
            rows[map_id] = self.skip_intro_var.get()
            self.game_settings['skip_intro'] = rows

    def _persist_game_settings(self):
        """Write map + drop rates + the current map's balance row."""
        if not self._valid_missile_delay():
            return False
        self._store_intro_choice()
        document = dict(self.game_settings)
        document['map'] = self.map_var.get()
        try:
            rows = dict(document.get('drops') or {})
            rows[self.map_var.get()] = self._current_drop_entry()
            document['drops'] = rows
        except (TypeError, ValueError):
            messagebox.showwarning(self.tr('Gear Editor'), self.tr('掉落率请填 0-100 的数字'))
            return False
        if self.map_var.get() in BALANCE_MAPS:
            try:
                entry = self._current_balance_entry()
            except (TypeError, ValueError):
                messagebox.showwarning(self.tr('Gear Editor'), self.tr('数值倍率/刷怪上限请填数字'))
                return False
            if entry is not None:
                rows = dict(document.get('balance') or {})
                rows[self.map_var.get()] = entry
                document['balance'] = rows
        written = game_settings.save(document)
        self.game_settings = game_settings.load()
        self.map_var.set(self.game_settings.get('map'))
        self._load_drop_controls()
        self._on_map_select()
        self._log('game settings saved to {0}: map={1} health={2} ammo={3} ec={4}'
                  .format(written, self.game_settings.get('map'),
                          self.game_settings.get('drop_health_percent'),
                          self.game_settings.get('drop_ammo_percent'),
                          self.game_settings.get('drop_ec_percent')))
        return True

    def save_game_settings(self):
        if self._persist_game_settings():
            self.status.configure(
                text=self.tr('已保存设置：地图立即重载，掉落率立即生效，数值对新刷出的怪生效'))

    def _percent_var_value(self, variable, default):
        try:
            value = float(variable.get())
            return value if math.isfinite(value) else float(default)
        except (TypeError, ValueError):
            return float(default)

    def _export_document(self):
        """Overrides plus the editor settings, in the historical format.

        The extra keys are plain top-level entries, so an older editor
        still imports the gear part and simply skips them.
        """
        document = dict(self.overrides or {})
        self._store_missile_delay()
        document['_missile_delay_ms'] = dict(self.game_settings.get('missile_delay_ms') or {})
        document['_map'] = self.map_var.get()
        rows = dict(self.game_settings.get('skip_intro') or {})
        if self.map_var.get() in game_settings.SKIP_INTRO_MAPS:
            rows[self.map_var.get()] = self.skip_intro_var.get()
        document['_skip_intro'] = rows
        document['_drop_health_percent'] = self._percent_var_value(
            self.drop_health_var, game_settings.DEFAULTS['drop_health_percent'])
        document['_drop_ammo_percent'] = self._percent_var_value(
            self.drop_ammo_var, game_settings.DEFAULTS['drop_ammo_percent'])
        drop_rows = dict((getattr(self, 'game_settings', None) or {}).get('drops') or {})
        try:
            drop_rows[self.map_var.get()] = self._current_drop_entry()
        except (TypeError, ValueError):
            pass
        document['_drops'] = drop_rows
        ec_variable = getattr(self, 'drop_ec_var', None)
        if ec_variable is not None:
            document['_drop_ec_percent'] = self._percent_var_value(ec_variable, game_settings.DEFAULTS['drop_ec_percent'])
        rows = dict((getattr(self, 'game_settings', None) or {}).get('balance') or {})
        if (getattr(self, 'balance_vars', None)
                and self.map_var.get() in BALANCE_MAPS):
            try:
                entry = self._current_balance_entry()
            except (TypeError, ValueError):
                entry = None
            if entry is not None:
                rows[self.map_var.get()] = entry
        if rows:
            document['_balance'] = rows
        return document

    def _load_slot(self):
        self._load_settings_controls()
        self.slot_list.selection_clear(0, 'end')
        self.slot_list.selection_set(self._slot_index())
        self.slot_list.activate(self._slot_index())
        context = (self.char_type, self.slotpos)
        if getattr(self, '_item_search_context', None) != context:
            self.item_search_var.set('')
        self._item_search_context = context
        candidates = candidate_items(self.catalogue, self.char_type, self.slotpos)
        rows = (self.overrides.get(str(self.char_type)) or {})
        current = rows.get(str(self.slotpos)) or {}
        if current.get('type_') is not None:
            self.item_type = int(current['type_'])
        elif candidates:
            self.item_type = int(candidates[0]['type'])
        else:
            self.item_type = None
        if self.item_type is not None:
            item = self.catalogue.get(self.item_type)
            value = '{0}  {1}'.format(
                self.item_type, item['name'] if item else self.item_type)
            self.item_var.set(value)
            self.base_label.configure(text=self.tr('基础属性：{0}').format(
                self._format_boosts(item.get('base') if item else [])))
            self._refresh_affix_options()
            self._set_affixes(current.get('statboosts_') or [])
        else:
            self.item_var.set('')
            self.base_label.configure(text=self.tr('基础属性：—'))
            self._refresh_affix_options()
            self._set_affixes([])

        self._refresh_item_options()
        self._load_projectile_speeds()

    def _load_projectile_speeds(self):
        for widget in self.projectile_frame.winfo_children():
            widget.destroy()
        self.projectile_speed_rows = []
        item = self.catalogue.get(self.item_type) or {}
        objects = item.get('projectiles') or []
        if not objects:
            ttk.Label(self.projectile_frame,
                      text=self.tr('此物品没有可编辑的弹体基础速度。')).pack(anchor='w')
            return
        saved = (self.overrides.get(projectile_speeds.KEY) or {}).get(str(self.item_type), {})
        for obj in objects:
            row = ttk.Frame(self.projectile_frame)
            row.pack(fill='x', pady=2)
            ttk.Label(row, text=self.tr('{0}  （原值 {1}）').format(obj['vid'], obj['speed']),
                      width=42).pack(side='left')
            variable = tk.StringVar(value=str(saved.get(str(obj['index']), obj['speed'])))
            ttk.Entry(row, textvariable=variable, width=12).pack(side='left', padx=6)
            ttk.Button(row, text=self.tr('恢复原值'),
                       command=lambda v=variable, speed=obj['speed']: v.set(str(speed))).pack(side='left')
            self.projectile_speed_rows.append((obj, variable))
        ttk.Label(self.projectile_frame,
                  text=self.tr('整数 -1000000–1000000；同名武器共用。保存后退出并重新启动模拟器生效。'),
                  style='Hint.TLabel').pack(anchor='w', pady=(4, 0))


    def _current_projectile_speeds(self):
        speeds = {}
        for obj, variable in self.projectile_speed_rows:
            value = int(variable.get().strip())
            if not -projectile_speeds.MAX_SPEED <= value <= projectile_speeds.MAX_SPEED:
                raise ValueError(self.tr('弹速必须是 -1000000–1000000 的整数'))
            if value != obj['speed']:
                speeds[str(obj['index'])] = value
        return speeds


    def _store_projectile_speeds(self, speeds):
        rows = projectile_speeds.normalize(self.overrides.get(projectile_speeds.KEY) or {})
        if speeds:
            rows[str(self.item_type)] = speeds
        else:
            rows.pop(str(self.item_type), None)
        self.overrides[projectile_speeds.KEY] = rows


    def _format_boosts(self, boosts):
        if not boosts:
            return '—'
        return ', '.join(
            '{0}({1})={2}'.format(
                self.stat_names.get(int(boost.get('stat_')), '?'),
                boost.get('stat_'), boost.get('val_', 0))
            for boost in boosts)

    def _affix_choices(self):
        if self._affix_choice_cache:
            return self._affix_choice_cache
        rankings = []
        seen = set()
        exact = (self.affix_priority.get('exact', {}).get(self.item_type)
                 or {})

        for stat in exact:
            if stat in seen or stat not in self.stat_names:
                continue
            seen.add(stat)
            rankings.append((stat, ''))
        remaining = [stat for stat in self.stat_names if stat not in seen]
        if getattr(self, 'affix_sort', 'id') == 'name':
            remaining.sort(key=lambda stat: (self.stat_names[stat].lower(), stat))
        else:
            remaining.sort()
        for stat in remaining:
            rankings.append((stat, ''))
        self._affix_choice_cache = [
            '{0:>3}  {1:<24}'.format(stat, self.stat_names.get(stat, '?'))
            for stat, _label in rankings]
        self._affix_choice_by_stat = dict(
            (_parse_stat_id(row), row)
            for row in self._affix_choice_cache)
        return self._affix_choice_cache

    def _choice_for_stat(self, stat):
        self._affix_choices()
        row = self._affix_choice_by_stat.get(int(stat))
        if row is not None:
            return row
        return '{0:>3}  {1:<24}'.format(
            int(stat), self.stat_names.get(int(stat), '?'))

    def _refresh_affix_options(self):
        self._affix_choice_cache = []
        self._affix_choice_by_stat = {}
        values = self._affix_choices()
        item = self.catalogue.get(self.item_type) if self.item_type else None
        if item and self.item_type in self.affix_priority.get('exact', {}):
            pool = self.affix_priority['exact'][self.item_type]
            names = ', '.join(
                '{0}({1})={2}'.format(self.stat_names.get(stat, '?'), stat,
                                     affix_default_value(self.affix_priority, self.item_type, stat))
                for stat in pool)
            self.affix_hint.configure(
                text=self.tr('本物品词缀默认值：{0}').format(names))
        else:
            self.affix_hint.configure(
                text=self.tr('本物品暂无 Wiki / CHM 词缀池；词缀按 ID 或首字母排序。'))
        for frame, _stat_var, _value_var, combo in self.affix_rows:
            combo.configure(values=values)
        return values

    def toggle_affix_sort(self):
        self.affix_sort = 'name' if self.affix_sort == 'id' else 'id'
        self._affix_choice_cache = []
        self._affix_choice_by_stat = {}
        self.affix_sort_button.configure(
            text=self.tr('词缀排序：ID') if self.affix_sort == 'id' else self.tr('词缀排序：首字母'))
        self._refresh_affix_options()
        for _frame, stat_var, _value_var, _combo in self.affix_rows:
            stat = _parse_stat_id(stat_var.get())
            if stat is not None:
                stat_var.set(self._choice_for_stat(stat))

    def _set_affixes(self, boosts):
        for frame, _stat_var, _value_var, _combo in self.affix_rows:
            frame.destroy()
        self.affix_rows = []
        for boost in boosts:
            self.add_affix(boost)

    def add_affix(self, initial=None):
        frame = ttk.Frame(self.affix_frame)
        frame.pack(fill='x', pady=3)
        stat_var = tk.StringVar()
        value_var = tk.StringVar()
        values = self._affix_choices()
        combo = ttk.Combobox(frame, textvariable=stat_var, values=values,
                             state='readonly', width=52, font=self.ui_font)
        combo.pack(side='left', fill='x', expand=True)
        entry = ttk.Entry(frame, textvariable=value_var, width=10,
                          font=self.ui_font)
        entry.pack(side='left', padx=6)
        if initial:
            stat = int(initial.get('stat_'))
            stat_var.set(self._choice_for_stat(stat))
            value_var.set(str(initial.get('val', initial.get('val_', 0))))
        else:
            stat_var.set(values[0])
            value_var.set(str(affix_default_value(
                self.affix_priority, self.item_type, _parse_stat_id(values[0]))))
        def selected(_event=None):
            value_var.set(str(affix_default_value(
                self.affix_priority, self.item_type, _parse_stat_id(stat_var.get()))))
        combo.bind('<<ComboboxSelected>>', selected)
        def remove():
            frame.destroy()
            self.affix_rows = [row for row in self.affix_rows if row[0] is not frame]
        ttk.Button(frame, text=self.tr('删除'), command=remove).pack(side='left')
        self.affix_rows.append((frame, stat_var, value_var, combo))

    def _current_boosts(self):
        result = []
        for _frame, stat_var, value_var, _combo in self.affix_rows:
            stat = _parse_stat_id(stat_var.get())
            if stat is None:
                continue
            try:
                value = int(value_var.get().strip() or '0')
            except ValueError:
                continue
            result.append({'stat_': stat, 'val_': value})
        return result

    def apply(self):
        if not self._valid_missile_delay():
            return
        try:
            speeds = self._current_projectile_speeds()
        except (TypeError, ValueError):
            messagebox.showwarning(self.tr('Gear Editor'), self.tr('子弹速度请填 -1000000–1000000 的整数'))
            return
        previous_speeds = dict((self.overrides.get(projectile_speeds.KEY) or {}).get(str(self.item_type), {}))
        self._store_projectile_speeds(speeds)
        mk = self._selected_mk()
        implants = self._selected_implants()
        turret_counts = self._selected_turret_counts()
        turret_missiles = self._selected_turret_missiles(turret_counts)
        set_class_mk(self.overrides, self.char_type, mk)
        set_class_implants(self.overrides, self.char_type, implants)
        set_turret_counts(self.overrides, turret_counts)
        set_turret_missiles(self.overrides, turret_missiles)
        save_overrides(self.overrides)
        self._persist_game_settings()
        # Write the effective preset files directly.  The server watches
        # class_loadouts.json, so a save is applied without waiting for the
        # player to long-press a class key.  Write overrides first and the
        # watched file last, making the final replace the atomic signal.
        slotpos = item_type = boosts = None
        if self.item_type is not None:
            slotpos = self.slotpos
            item_type = self.item_type
            boosts = self._current_boosts()
            set_instance(self.overrides, self.char_type, self.slotpos,
                         self.item_type, boosts)
        save_overrides(self.overrides)
        # One atomic write per effective file avoids multiple mtime/restart
        # signals when MK, implant slots, gear and turret counts change.
        update_practice_settings_file(
            PRACTICE_LOADOUT, self.char_type, mk, implants,
            slotpos, item_type, boosts)
        written = update_class_settings_file(
            CLASS_LOADOUTS, self.char_type, mk, implants, turret_counts,
            turret_missiles, slotpos, item_type, boosts)
        if written is None:
            messagebox.showwarning(
                self.tr('Gear Editor'), self.tr('找不到 class_loadouts.json，只保存了 overrides'))
            self.status.configure(text=self.tr('已保存 overrides；class_loadouts.json 不存在'))
            return
        self.status.configure(text=(
            self.tr('弹速已保存；请退出游戏并重新启动模拟器，长按 R 不会更新基础弹速。')
            if speeds or previous_speeds else
            self.tr('已保存 MK / 炮台数量 / 装备；服务器检测到文件变化后会自动重开本局')))


    def export(self):
        if not self._valid_missile_delay():
            return
        path = filedialog.asksaveasfilename(
            title=self.tr('导出装备配置'), defaultextension='.json',
            filetypes=[('JSON', '*.json')])
        if not path:
            return
        with io.open(path, 'w', encoding='utf-8', newline='\n') as handle:
            json.dump(self._export_document(), handle,
                      ensure_ascii=False, indent=1)
            handle.write('\n')
        self.status.configure(text=self.tr('已导出: ') + path)

    def import_file(self):
        path = filedialog.askopenfilename(
            title=self.tr('导入装备配置'), filetypes=[('JSON', '*.json')])
        if not path:
            return
        try:
            document = jsonio.load_json(path)[0]
        except Exception as error:
            messagebox.showerror(self.tr('Gear Editor'), self.tr(str(error)))
            return
        try:
            if projectile_speeds.KEY in document:
                projectile_speeds.normalize(document[projectile_speeds.KEY])
            apply_import_document(document, self.overrides, self.game_settings)
        except (TypeError, ValueError) as error:
            messagebox.showerror(self.tr('Gear Editor'), self.tr(str(error)))
            return
        self.map_var.set(self.game_settings.get('map')
                         or game_settings.ORIGINAL_MAP)
        self._load_drop_controls()
        self._on_map_select()
        self._load_slot()
        self.status.configure(text=self.tr('已导入；点“应用/保存”写装备与设置'))


    def restore_defaults(self):
        try:
            backup = presets.restore({
                'class_loadouts.json': CLASS_LOADOUTS,
                'practice_loadout.json': PRACTICE_LOADOUT,
                'class_loadout_overrides.json': OVERRIDES,
                'game_settings.json': game_settings.PATH,
            })
            self.reload()
            self.status.configure(text=self.tr('全部配装与战斗设置已恢复默认；原配置已备份：') + str(backup))
        except (OSError, ValueError) as error:
            messagebox.showerror(self.tr('Gear Editor'), str(error))

    def reload(self):
        self.keybindings = keybindings.load(KEYBINDINGS)
        self._update_key_labels()
        self.catalogue = load_catalogue()
        self.affix_priority = build_affix_priority(self.catalogue)
        self._item_affix_drafts = {}
        self.overrides = load_overrides()
        self.game_settings = game_settings.load()
        self._refresh_map_choices()
        self.map_var.set(self.game_settings.get('map')
                         or game_settings.ORIGINAL_MAP)
        self._load_drop_controls()
        self._on_map_select()
        try:
            self.turret_counts = normalize_turret_counts(
                self.overrides.get('_turret_counts'))
        except (TypeError, ValueError):
            self.turret_counts = normalize_turret_counts({})
        CANDIDATE_CACHE.clear()
        self._load_slot()

    def hide(self):
        self.visible = False
        self.root.withdraw()
        self._log('hidden')

    def show(self):
        self._refresh_map_choices()
        self.keybindings = keybindings.load(KEYBINDINGS)
        self._update_key_labels()
        fit_window(self.root)
        self.visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost', True)
        self._log('shown')

    def toggle(self):
        if self.visible:
            self.hide()
        else:
            self.show()

    def _check_owner(self):
        """Close once the launcher that spawned this panel has exited.

        play_hulk normally terminates the editor when the game closes;
        this covers a killed launcher/console window, which used to leave
        a stale panel behind that grabbed ~/F8 and edited the wrong copy.
        """
        if self.owner_pid and not process_alive(self.owner_pid):
            self._log('owner process {0} exited; closing'.format(
                self.owner_pid))
            self.close()
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        if not self._hotkey_stop:
            self.root.after(2000, self._check_owner)

    def _poll_hotkey(self):
        changed = False
        while True:
            try:
                self._hotkey_events.get_nowait()
                changed = True
            except queue.Empty:
                break
        if changed and self.root.grab_current() is None:
            self.toggle()
        if not self._hotkey_stop:
            self.root.after(50, self._poll_hotkey)

    def _render_update_status(self):
        result = self.update_checker.status()
        self.update_label.configure(text=update.status_message(result, self.language))
        self.update_button.configure(state='disabled' if self.update_checker.busy else 'normal')

    def _check_updates(self):
        self.update_checker.start()
        self._render_update_status()

    def _poll_updates(self):
        if not self._hotkey_stop:
            self._render_update_status()
            self.root.after(500, self._poll_updates)

    def _start_hotkey(self):

        def worker():
            user32 = ctypes.windll.user32
            while not self._hotkey_stop:
                for code in keybindings.editor_keys(self.keybindings):
                    down = bool(user32.GetAsyncKeyState(code) & 0x8000)
                    was = self._hotkey_down.get(code, False)
                    if down and not was:
                        self._hotkey_down[code] = True
                        self._hotkey_events.put(code)
                    elif not down:
                        self._hotkey_down[code] = False
                time.sleep(0.04)
        thread = threading.Thread(target=worker)
        thread.daemon = True
        self._hotkey_thread = thread
        thread.start()
        self._log('editor hotkeys: {0} / {1}'.format(
            self.keybindings['editor_toggle'], self.keybindings['editor_toggle_alt']))

    def _stop_hotkey(self):
        self._hotkey_stop = True
        if self._hotkey_thread and self._hotkey_thread is not threading.current_thread():
            self._hotkey_thread.join(timeout=0.5)

    def _open_records_window(self):
        from tools.desktop_view import alive
        from tools.web_workspace import atomic_json
        folder=Path(LAB)/'userdata/desktop-records';folder.mkdir(parents=True,exist_ok=True)
        status=folder/'window-status.json'
        process=getattr(self,'_records_window',None)
        if process is not None and process.poll() is None:
            atomic_json(folder/'show.json',{'at':time.time()});return
        try:
            # A language/layout rebuild may replace Tk widgets, never the
            # already-open record window or its user data.
            state=json.loads(status.read_text()) if status.is_file() else {}
            if alive(state.get('pid')):
                atomic_json(folder/'show.json',{'at':time.time()});return
        except (OSError,ValueError):pass
        try:
            self._records_window=subprocess.Popen([sys.executable,'-u','-m','tools.desktop_view','--owner-pid',str(os.getpid()),'--directory',str(folder)],cwd=LAB,stdout=subprocess.DEVNULL,stderr=(folder/'window.log').open('ab'),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self._records_status_stamp=None
            self.root.after(200,self._poll_records_window)
        except OSError as error:self.battle_panel.notice.configure(text=str(error))

    def _poll_records_window(self):
        try:
            path=Path(LAB)/'userdata/desktop-records/window-status.json'
            state=json.loads(path.read_text()) if path.is_file() else {}
            value=state.get('state')
            if value=='preparing':
                percent=int(100*state.get('current',0)/max(1,state.get('total',1)))
                message=self.tr('准备战斗记录窗口')+' · '+str(percent)+'%'
            elif value=='failed':message=self.tr('暂时无法打开战斗记录')+' · '+state.get('error','')
            else:message=self.tr('战斗记录在客户端窗口中打开；可直接回放、导入和分享。')
            self.battle_panel.notice.configure(text=message)
            if value=='editor' and self._records_status_stamp!=path.stat().st_mtime_ns:
                self.notebook.select(self.gear_page);self.root.deiconify();self.root.lift()
            if path.is_file():self._records_status_stamp=path.stat().st_mtime_ns
            process=getattr(self,'_records_window',None)
            if process is not None and process.poll() is None:self.root.after(400,self._poll_records_window)
        except (tk.TclError,OSError,ValueError):pass

    def close(self):
        self._stop_hotkey()
        self._log('closing')
        self.root.quit()
        self.root.destroy()

def _ensure_bundled_tcl():
    """Let the package's bundled python/ find its Tcl/Tk script libraries.

    The embeddable interpreter has no registry entries, so Tcl must be told
    where tcl8.6/tk8.6 live; the packager ships them under python/tcl/.
    """
    if os.environ.get('TCL_LIBRARY') and os.environ.get('TK_LIBRARY'):
        return False
    for base in (os.path.join(LAB, 'python'),
                 os.path.join(os.path.dirname(LAB), 'python')):
        tcl = os.path.join(base, 'tcl', 'tcl8.6')
        tk = os.path.join(base, 'tcl', 'tk8.6')
        if os.path.isdir(tcl) and os.path.isdir(tk):
            os.environ.setdefault('TCL_LIBRARY', tcl)
            os.environ.setdefault('TK_LIBRARY', tk)
            return True
    return False

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--show', action='store_true', help='show on startup')
    parser.add_argument('--self-test', action='store_true',
                        help='build the panel and exit without mainloop')
    parser.add_argument('--log', default=os.path.join(
        LAB, 'userdata', 'logs', 'gear-editor.log'), help='hotkey/debug log path')
    parser.add_argument('--owner-pid', type=int,
                        help='launcher process; the panel closes when it exits')
    options = parser.parse_args(argv)
    if not options.self_test:
        from tools.desktop_view import main as shared_editor
        return shared_editor(['--owner-pid', str(options.owner_pid or os.getpid()), *(['--show'] if options.show else [])])
    _ensure_bundled_tcl()

    kernel32 = ctypes.windll.kernel32 if os.name == 'nt' else None
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME) if kernel32 else None
    if kernel32 and kernel32.GetLastError() == 183:
        hwnd = ctypes.windll.user32.FindWindowW(None, WINDOW_TITLE)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 5)
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            print('gear editor already running; asked the existing panel '
                  'to show itself')
        else:
            print('gear editor already running')
        return 0
    root = tk.Tk()
    editor = GearEditor(root, show=options.show and not options.self_test,
                        log_path=options.log, owner_pid=options.owner_pid)
    if options.self_test:
        root.update_idletasks()
        root.update()
        editor.close()
        if kernel32:kernel32.CloseHandle(mutex)
        print('gear editor self-test OK')
        return 0
    try:
        root.mainloop()
    finally:
        editor._stop_hotkey()
        if kernel32:kernel32.CloseHandle(mutex)
    return 0

if __name__ == '__main__':
    main()
