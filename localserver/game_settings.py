"""Shared settings for the gear editor, launcher and live session.

``userdata/game_settings.json`` holds the map the next launch
uses, the generic health/ammo supply-drop rates and the per-map balance
knobs (enemy HP/damage/speed multipliers, Hulk spawn rate/cap).  The gear
editor writes it (``~``/F8 panel -> 保存设置), ``play_hulk.py`` reads the
map before starting a server, and the live session re-reads it with an
mtime check so a change applies without restarting the game.  The file is
optional: each map owns a ``drops.<map_id>`` row for health, ammo and EC.
The three Hulk maps default to 5%% for each category, and TL Mid defaults
to 1%% for each. There is no automatic difficulty multiplier. Every balance
multiplier ships at 1.0x (the unmodified encounter).

``maps/<id>/map.json`` is the optional map manifest.  It names pvid/zone
and the live-session module/class so a future map pack works without
editing the launcher or the server; the editor lists every ``maps/``
directory that carries a ``createworld.hex``.
"""
import json
import os
import copy
from contextlib import contextmanager
from contextvars import ContextVar

from . import jsonio
from .paths import ensure_user_data

ensure_user_data()

_EVENT_SNAPSHOT = ContextVar('starbreak_event_settings',default=None)

@contextmanager
def event_snapshot(document):
    """Freeze settings within one event tick, without changing any SP file."""
    token=_EVENT_SNAPSHOT.set(document)
    try:yield
    finally:_EVENT_SNAPSHOT.reset(token)

LAB = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = (os.environ.get('SB_GAME_SETTINGS_PATH')
        or os.path.join(LAB, 'userdata', 'game_settings.json'))
MAPS_DIR = os.path.join(LAB, 'maps')
ORIGINAL_MAP = 'egy_hulk'
MAP_DISPLAY_ORDER = (
    'lobby', 'home', 'ice_mid', 'ice_boss', 'forest_mid', 'forest_boss',
    'city_mid', 'city_boss', 'cave_boss', 'dumb_normal_wave', 'dumb_mid',
    'dumb_wave', 'dumb_boss', 'jungle_mid', 'jungle_boss', 'hulk_normal',
    'hulk_mid', 'egy_hulk', 'egy_run', 'lab_mid', 'lab_hard', 'sc_patriarch',
    'sc_hive', 'sc_bonesaw', 'sc_boss', 'sc_boss_elite', 'eub_run', 'etl_run',
    'est_run', 'efb_run', 'eff_run',
)

def ordered_maps():
    """The same stable order for the local editor and activity catalogue."""
    order = {map_id: index for index, map_id in enumerate(MAP_DISPLAY_ORDER)}
    return tuple(sorted(available_maps(), key=lambda map_id: (order.get(map_id, len(order)), map_id)))

DEFAULT_COMMENT = ('装备面板（~ / F8）→“保存设置”写入：地图 + 通用掉落率'
                   '（drops.<地图id> 的血包/EC/弹药各自保存，不自动按难度翻倍）+ 每图数值 balance.<地图id>（敌人血量/伤害/'
                   '速度、刷怪速率/上限；目前由 egy_hulk 与 hulk_normal 会话驱动）。'
                   'map 取 maps/ 下的目录名，或 egy_hulk（原图）。')

DEFAULT_DROPS = {'health': 5.0, 'ammo': 5.0, 'ec': 5.0}
HULK_DROP_MAPS = frozenset((ORIGINAL_MAP, 'hulk_normal', 'hulk_mid', 'egy_run'))

ELITE_SUPPLY_MAPS = frozenset((
    ORIGINAL_MAP, 'hulk_mid', 'cave_boss', 'dumb_boss', 'jungle_mid',
    'jungle_boss', 'lab_mid', 'lab_hard',
))
DEFAULTS = {
    'map': ORIGINAL_MAP,
    'drop_health_percent': DEFAULT_DROPS['health'],
    'drop_ammo_percent': DEFAULT_DROPS['ammo'],
    'drop_ec_percent': DEFAULT_DROPS['ec'],
}
SUPPLY_ITEMS = {'health': 18, 'ammo': 21}
DROP_FIELDS = ('health_percent', 'ammo_percent', 'ec_percent')
LEGACY_DROP_FIELDS = ('drop_health_percent', 'drop_ammo_percent', 'drop_ec_percent')

HULK_EC_WEIGHTS = ((49, 3.), (96, 11.), (50, 4.), (97, 4.))
TL_EC_WEIGHTS = ((49, 10.), (96, 8.), (50, 3.))
OTHER_EC_WEIGHTS = ((49, 1.), (96, 1.), (50, 1.))

BALANCE_DEFAULTS = {
    'enemy_hp': 1.0,
    'enemy_damage': 1.0,
    'enemy_speed': 1.0,
    'spawn_rate': 1.0,
    'spawn_cap': 180,
}
BALANCE_LIMITS = {
    'enemy_hp': (0.1, 100.0),
    'enemy_damage': (0.1, 100.0),
    'enemy_speed': (0.1, 100.0),
    'spawn_rate': (0.1, 100.0),
    'spawn_cap': (1, 1000),
}

MAP_BALANCE_LIMITS = {
    'sc_patriarch': {'enemy_hp': (1., 100.), 'boss_attack_rate': (1., 2.),
                     'turret_attack_rate': (1., 2.)},
    'dumb_wave': {'enemy_hp': (1., 10.), 'spawn_rate': (1., 2.)},
    'sc_bonesaw': {'laser_rate': (1., 10.), 'bullet_rate': (1., 10.),
                   'saw_speed': (1., 3.)},
}
BALANCE_MAPS = (ORIGINAL_MAP, 'hulk_normal', *MAP_BALANCE_LIMITS)

def balance_limits(map_id):
    return MAP_BALANCE_LIMITS.get(map_id, BALANCE_LIMITS)

def balance_defaults(map_id):
    return {key: BALANCE_DEFAULTS.get(key, 1.) for key in balance_limits(map_id)}
LEGACY_SESSIONS = {
    'lab_hard': ('localserver.maps.lab_hard.session', 'LabHardLiveSession'),
    'lab_mid': ('localserver.maps.lab_mid.session', 'LabMidLiveSession'),
}

def map_dir(map_id, root=None):
    return os.path.join(root or LAB, 'maps', map_id)

def map_available(map_id, root=None):
    """True when *map_id* is the original map or a maps/ directory."""
    if map_id == ORIGINAL_MAP:
        return True
    if not isinstance(map_id, str) or not map_id.strip():
        return False
    map_id = map_id.strip()
    if os.path.basename(map_id) != map_id:
        return False
    return os.path.isfile(os.path.join(map_dir(map_id, root),
                                      'createworld.hex'))

def available_maps(root=None):
    """Map ids the editor can switch to: original first, then maps/."""
    result = [ORIGINAL_MAP]
    base = os.path.join(root or LAB, 'maps')
    if os.path.isdir(base):
        for name in sorted(os.listdir(base)):
            if map_available(name, root) and name not in result:
                result.append(name)
    return result

def map_manifest(map_id, root=None):
    """Optional ``maps/<id>/map.json`` descriptor; {} when absent."""
    if map_id == ORIGINAL_MAP or not map_available(map_id, root):
        return {}
    try:
        document, _ = jsonio.load_json(os.path.join(
            map_dir(map_id, root), 'map.json'))
    except (OSError, ValueError, UnicodeError):
        return {}
    return document if isinstance(document, dict) else {}

def map_info(map_id, root=None):
    """Launch/session descriptor for *map_id*.

    Built-in lab maps keep their historical pvid/zone as the fallback and
    a manifest can override everything, including the session class.
    """
    info = {
        'id': map_id,
        'pvid': 'hulk',
        'zone': 'hulk_boss',
        'session_module': None,
        'session_class': None,
        'title': map_id,
        'gdv': 1,
        'preload_pvids': [],
    }
    if map_id in LEGACY_SESSIONS:
        module_name, class_name = LEGACY_SESSIONS[map_id]
        info['session_module'] = module_name
        info['session_class'] = class_name
    if map_id == ORIGINAL_MAP:
        info['title'] = 'Elite Graveyard Boss'
    elif map_id == 'lab_hard':
        info.update({'pvid': 'lab', 'zone': 'lab_boss',
                     'title': 'Elite Temple of the Lost Boss'})
    elif map_id == 'lab_mid':
        info.update({'pvid': 'lab', 'zone': 'labHard5',
                     'title': 'Elite Temple of the Lost Mid'})
    manifest = map_manifest(map_id, root)
    for key in ('pvid', 'zone', 'session_module', 'session_class', 'title'):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            info[key] = value.strip()
    if manifest.get('gdv') in (0, 1):
        info['gdv'] = int(manifest['gdv'])
    dependencies=manifest.get('preload_pvids')
    if isinstance(dependencies,list):
        info['preload_pvids']=list(dict.fromkeys(p.strip() for p in dependencies
            if isinstance(p,str) and p.strip() and p.strip()!=info['pvid']))
    return info

def normalize(document):
    """Fill defaults, clamp rates/multipliers, validate the map."""
    result = dict(document) if isinstance(document, dict) else {}
    map_id = str(result.get('map') or ORIGINAL_MAP).strip()
    result['map'] = map_id if map_available(map_id) else ORIGINAL_MAP
    result['drops'] = normalize_drops(result.get('drops'))

    source = document if isinstance(document, dict) else {}
    if not isinstance(source.get('drops'), dict) and any(
            key in result for key in LEGACY_DROP_FIELDS):
        row = drop_defaults(map_id)
        for key, old_key in zip(DROP_FIELDS, LEGACY_DROP_FIELDS):
            if old_key in result:
                row[key] = _drop_percent(result[old_key], row[key])
        result['drops'][map_id] = row
    selected = dict(drop_defaults(map_id), **result['drops'].get(map_id, {}))
    for key, old_key in zip(DROP_FIELDS, LEGACY_DROP_FIELDS):
        result[old_key] = selected[key]
    result['balance'] = normalize_balance(result.get('balance'))
    result.setdefault('_comment', DEFAULT_COMMENT)
    return result

def _drop_percent(value, default):
    import math
    try:
        value = float(value)
        if not math.isfinite(value):
            return float(default)
    except (TypeError, ValueError):
        return float(default)
    return max(0., min(100., value))

def drop_defaults(map_id):
    if map_id in HULK_DROP_MAPS:
        return {'health_percent': 5., 'ammo_percent': 5., 'ec_percent': 5.}
    if map_id == 'lab_mid':
        return {'health_percent': 1., 'ammo_percent': 1., 'ec_percent': 1.}
    value = 2. if map_id in ELITE_SUPPLY_MAPS else 1.
    return {'health_percent': value, 'ammo_percent': value, 'ec_percent': 0.}

def normalize_drops(document):
    result = {}
    if isinstance(document, dict):
        for map_id, row in document.items():
            if not isinstance(map_id, str) or not map_id.strip() or not isinstance(row, dict):
                continue
            defaults = drop_defaults(map_id.strip())
            result[map_id.strip()] = {key: _drop_percent(row.get(key, default), default)
                                      for key, default in defaults.items()}
    return result

def drop_settings(map_id, document=None):
    data = normalize(document if document is not None else load())
    return dict(drop_defaults(map_id), **data['drops'].get(map_id, {}))

def migrate_global_drops(document):
    """Keep former effective rates on other maps; apply new Hulk/TL defaults."""
    result = dict(document)
    if 'drops' in result or not any(key in result for key in LEGACY_DROP_FIELDS):
        return result
    rows = {}
    for map_id in available_maps():
        row = drop_defaults(map_id)
        if map_id not in HULK_DROP_MAPS and map_id != 'lab_mid':
            multiplier = 2. if map_id in ELITE_SUPPLY_MAPS else 1.
            row['health_percent'] = _drop_percent(result.get('drop_health_percent', 1.), 1.) * multiplier
            row['ammo_percent'] = _drop_percent(result.get('drop_ammo_percent', 1.), 1.) * multiplier
        rows[map_id] = row
    result['drops'] = rows
    return result

def normalize_balance(document):
    """Clamp ``{map_id: {knob: value}}``; unknown keys are dropped.

    Missing files/rows always fall back to ``BALANCE_DEFAULTS`` so the
    original encounter is the neutral 1.0x configuration.
    """
    result = {}
    if not isinstance(document, dict):
        return result
    for map_id, row in document.items():
        if not isinstance(map_id, str) or not map_id.strip():
            continue
        if not isinstance(row, dict):
            continue
        entry = {}
        limits = balance_limits(map_id.strip())
        for key, default in balance_defaults(map_id.strip()).items():
            try:
                value = float(row.get(key, default))
            except (TypeError, ValueError):
                value = float(default)
            low, high = limits[key]
            value = max(low, min(high, value))
            entry[key] = int(round(value)) if key == 'spawn_cap' else value
        result[map_id.strip()] = entry
    return result

def balance(map_id, document=None):
    """Return one map's clamped balance row (defaults when unset)."""
    data = normalize(document if document is not None else load())
    rows = data.get('balance') or {}
    row = rows.get(map_id) if isinstance(map_id, str) else None
    entry = balance_defaults(map_id)
    if isinstance(row, dict):
        for key in entry:
            if key in row:
                entry[key] = row[key]
    return entry

class Balance(object):
    """Live multiplier holder shared by a session, its manager and its units.

    ``update`` rewrites the values in place so holders keep the same
    object and pick up a gear-editor save on the next use.
    """

    def __init__(self, values=None):
        self.values = dict(BALANCE_DEFAULTS)
        self.update(values)

    def update(self, values):
        fresh = dict(BALANCE_DEFAULTS)
        if isinstance(values, dict):
            for key in BALANCE_DEFAULTS:
                if key in values:
                    fresh[key] = values[key]
        self.values = fresh

    def factor(self, key):
        try:
            value = float(self.values.get(key, BALANCE_DEFAULTS[key]))
        except (TypeError, ValueError):
            value = float(BALANCE_DEFAULTS[key])
        low, high = BALANCE_LIMITS[key]
        return max(low, min(high, value))

    def hp(self, value):
        if value <= 0:
            return 0
        return max(1, int(round(float(value) * self.factor('enemy_hp'))))

    def damage(self, value):
        if value <= 0:
            return 0
        return max(1, int(round(float(value) * self.factor('enemy_damage'))))

    def speed(self, value):
        return float(value) * self.factor('enemy_speed')

    def spawn_rate(self):
        return self.factor('spawn_rate')

    def spawn_cap(self):
        return max(1, int(round(self.factor('spawn_cap'))))

def load(path=None):
    """Read the settings file; missing/broken files mean defaults."""
    snapshot=_EVENT_SNAPSHOT.get()
    if snapshot is not None and (path is None or path == PATH):return copy.deepcopy(snapshot)
    try:
        document, _ = jsonio.load_json(path or PATH)
    except (OSError, ValueError, UnicodeError):
        document = {}
    return normalize(migrate_global_drops(document))

def save(document, path=None):
    """Atomically write the settings as UTF-8; returns the path."""
    target = path or PATH
    directory = os.path.dirname(target)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory)
    data = normalize(document)
    tmp = target + '.tmp'
    with open(tmp, 'w', encoding='utf-8', newline='\n') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)
        handle.write('\n')
    os.replace(tmp, target)
    return target

def settings_mtime(path=None):
    """mtime of the settings file, or None when it does not exist yet."""
    if path is None and _EVENT_SNAPSHOT.get() is not None:return -1
    try:
        return os.path.getmtime(path or PATH)
    except OSError:
        return None

def changed_map(current_map, last_mtime=None, path=None):
    """Return (new_map, mtime) when the file selects a different map.

    ``(None, mtime)`` means "no map change"; the caller stores the mtime so
    the next poll only reacts to a real file change.  An unreadable or
    invalid selection never switches.
    """
    target = path or PATH
    mtime = settings_mtime(target)
    if mtime == last_mtime:
        return None, mtime
    configured = load(target).get('map')
    if configured and configured != (current_map or ORIGINAL_MAP):
        return configured, mtime
    return None, mtime

def supply_rates(document=None, map_id=None):
    """Return ``(drop_chance, ((item_type, weight), ...))``.

    Every map has three independent percentages. EC is a total category
    probability, split over that map's denomination table. Above 100%,
    preserve the split and cap the gross chance. An all-zero
    configuration disables supply drops.
    """
    raw = document if document is not None else load()
    if map_id is not None and 'drops' not in raw:
        raw = dict(raw, map=map_id)
    data = normalize(raw)
    selected_map = map_id or data['map']
    row = drop_settings(selected_map, data)
    health, ammo, ec = (row[key] for key in DROP_FIELDS)
    total = health + ammo + ec
    if total <= 0:
        return 0.0, ()
    table = [(SUPPLY_ITEMS['health'], health), (SUPPLY_ITEMS['ammo'], ammo)]
    weights = HULK_EC_WEIGHTS if selected_map in HULK_DROP_MAPS else (
        TL_EC_WEIGHTS if selected_map == 'lab_mid' else OTHER_EC_WEIGHTS)
    weight_total = sum(weight for _item, weight in weights)
    table += [(item, ec * weight / weight_total) for item, weight in weights if ec > 0]
    return min(1., total / 100.), tuple(table)
