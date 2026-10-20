"""Validated, isolated browser loadouts; no desktop UI or native dependencies."""
import copy
import json
from pathlib import Path
import tempfile
import threading

from localserver import equipment, game_settings, paths, projectile_speeds, presets
from localserver.live.turrets import normalize_missile_counts, normalize_turret_counts
from tools import i18n
from plugins import _practice_keybindings as keybindings

SCHEMA = 'starbreak-web-loadouts-v1'
CLASSES = (1, 2, 5, 3)
WEB_KEYS = dict(keybindings.DEFAULTS, zoom_in='Equal', zoom_out='Minus',zoom_percent=10)
ZOOM_KEYS = ('Equal', 'Minus', 'NumpadAdd', 'NumpadSubtract')

def normalize_web_keys(value):
    result = keybindings.normalize(value)
    used = {key for key in result.values() if isinstance(key, str) and key not in ('None', 'Auto')}
    for action in ('zoom_in', 'zoom_out'):
        name = value.get(action, WEB_KEYS[action])
        if name not in ('None', *keybindings.CHOICES, *ZOOM_KEYS):
            raise ValueError('Invalid zoom key: ' + str(name))
        if name != 'None' and name in used:
            raise ValueError('Duplicate key: ' + name)
        result[action] = name
        if name != 'None': used.add(name)
    percent=value.get('zoom_percent',10)
    if isinstance(percent,bool) or not isinstance(percent,(int,float)) or not 5<=percent<=50:raise ValueError('Zoom step must be between 5% and 50%')
    result['zoom_percent']=percent
    return result


def atomic_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        json.dump(document, output, ensure_ascii=False, indent=2)
        output.write('\n')
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label}: expected an integer from {low} to {high}')
    return value


class Workspace:
    def __init__(self, directory, source=None):
        self.directory = Path(directory)
        self.source = Path(source or paths.USERDATA)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'profile.json'
        self.lock = threading.RLock()
        self.catalogue = equipment.load_catalogue()
        self.priority = equipment.build_affix_priority(self.catalogue)
        if not self.path.exists():
            # First launch copies existing equipment; future edits stay here.
            def load(name):
                path = self.source / name
                if not path.is_file():
                    path = paths.ROOT / 'data/defaults' / name
                return json.loads(path.read_text(encoding='utf-8-sig'))
            loadouts = load('class_loadouts.json')
            overrides = load('class_loadout_overrides.json')
            document = dict(schema=SCHEMA, revision=0,
                loadouts={str(k): loadouts[str(k)] for k in CLASSES},
                projectile_speeds=overrides.get(projectile_speeds.KEY, {}),
                turrets=overrides.get('_turret_counts', {}),
                missiles=overrides.get('_turret_missiles', {}),
                settings=load('game_settings.json'))
            atomic_json(self.path, self.validate(document))
        # Upgrade only the exact previous default. Preserve a recoverable copy.
        original = self.path.read_bytes()
        current = json.loads(original.decode('utf8'))
        if current.get('defaults_revision', 0) < 2:
            normalized = self.validate(current)
            suffix = 'aeonic-crit' if current.get('defaults_revision', 0) < 1 else 'ic-default'
            backup = self.path.with_name(f'profile.before-{suffix}-fix.json')
            if not backup.exists():
                with backup.open('xb') as output:
                    output.write(original)
            atomic_json(self.path, normalized)
        self.read()  # Corrupt saved data is reported, never silently replaced.

    def preferences(self):
        try:value=json.loads((self.directory/'preferences.json').read_text(encoding='utf8'))
        except (OSError,ValueError):value={}
        return self.normalize_preferences(value)

    def normalize_preferences(self, value):
        if not isinstance(value,dict):raise ValueError('Invalid preferences')
        shell=value.get('shell',1);slot=value.get('slot',256)
        from localserver.player_identity import normalize
        return dict(**normalize(value),language=value.get('language') if value.get('language') in ('en','zh','zh-affixes') else None,
                    shell=shell if type(shell) is int and shell in CLASSES else 1,
                    slot=slot if type(slot) is int and slot in {s for s,_ in equipment.SLOTS} else 256,
                    item_sort='name' if value.get('item_sort')=='name' else 'id',
                    query=str(value.get('query',''))[:100],all_classes=value.get('all_classes') is True,
                    record_replays=value.get('record_replays', True) is not False,
                    keys=normalize_web_keys(value.get('keys',WEB_KEYS)))

    def save_preferences(self, value):
        result=self.normalize_preferences(value)
        with self.lock:
            atomic_json(self.directory/'preferences.json',result)
            from localserver.player_identity import normalize
            atomic_json(self.directory/'identity.json',normalize(result))
        return result

    def draft(self):
        try:
            value=self.validate(json.loads((self.directory/'draft.json').read_text(encoding='utf8')))
            return value if value['revision']==self.read()['revision'] else None
        except (OSError,ValueError):return None

    def save_draft(self, value):
        with self.lock:
            if value.get('discard') is True:
                if value.get('revision')!=self.read()['revision']:raise ValueError('Stale draft')
                (self.directory/'draft.json').unlink(missing_ok=True)
                return dict(discarded=True)
            draft=self.validate(value)
            if draft['revision']!=self.read()['revision']:raise ValueError('Stale draft')
            atomic_json(self.directory/'draft.json',draft)
            return dict(saved=True)

    def read(self):
        with self.lock:
            return self.validate(json.loads(self.path.read_text(encoding='utf8')))

    def validate(self, document):
        if not isinstance(document, dict) or document.get('schema') != SCHEMA:
            raise ValueError('Unsupported browser loadout file')
        result = dict(schema=SCHEMA, revision=integer(document.get('revision', 0), 'Revision', 0, 2**31-1))
        old_defaults = document.get('defaults_revision', 0) < 1
        old_ic_defaults = document.get('defaults_revision', 0) < 2
        result['defaults_revision'] = 2
        classes = document.get('loadouts')
        if not isinstance(classes, dict) or set(classes) != {str(k) for k in CLASSES}:
            raise ValueError('Loadouts must contain WF, DW, FAB and IC')
        result['loadouts'] = {}
        slots = {row[0] for row in equipment.SLOTS}
        for char in CLASSES:
            row = copy.deepcopy(classes[str(char)])
            if not isinstance(row, dict):
                raise ValueError('Invalid class loadout')
            items = row.get('items_')
            if not isinstance(items, list) or len(items) > len(slots):
                raise ValueError('Invalid equipment slot list')
            normalized, seen = [], set()
            for item in items:
                if old_defaults and char == 1 and isinstance(item, dict) and item.get('slotpos_') == 1280:
                    paths.repair_default_aeonic(item)
                if old_ic_defaults and char == 3 and isinstance(item, dict) and item.get('slotpos_') == 256:
                    paths.repair_default_ic(item)
                if not isinstance(item, dict):
                    raise ValueError('Invalid equipment item')
                slot = integer(item.get('slotpos_'), 'Slot', 1, 65535)
                kind = integer(item.get('type_'), 'Item ID', 1, 2**31-1)
                if slot not in slots or slot in seen or kind not in self.catalogue:
                    raise ValueError('Unknown or duplicate equipment slot/item')
                if slot < 3840 and self.catalogue[kind]['slot'] != slot:
                    raise ValueError('Item does not match the selected equipment slot')
                boosts = item.get('statboosts_', [])
                if not isinstance(boosts, list) or len(boosts) > 64:
                    raise ValueError('At most 64 modifiers per item')
                values = []
                for boost in boosts:
                    if not isinstance(boost, dict) or boost.get('stat_') not in equipment.STAT_NAMES:
                        raise ValueError('Unknown modifier')
                    stat = integer(boost['stat_'], 'Modifier', 1, 1000)
                    values.append(dict(stat_=stat, val_=integer(boost.get('val_', 0), 'Modifier value', -2**31, 2**31-1)))
                normalized.append(dict(type_=kind, slotpos_=slot, statboosts_=values))
                seen.add(slot)
            if 256 not in seen:
                raise ValueError('A main weapon is required')
            result['loadouts'][str(char)] = dict(type_=char,
                mk_=integer(row.get('mk_', 6), 'MK', 1, 6),
                impslots_=integer(row.get('impslots_', 1), 'Implant slots', 0, 8),
                items_=sorted(normalized, key=lambda item: item['slotpos_']))
        speeds = projectile_speeds.normalize(document.get('projectile_speeds', {}))
        for kind, values in speeds.items():
            available = {str(p['index']) for p in self.catalogue.get(int(kind), {}).get('projectiles', [])}
            if not set(values) <= available:
                raise ValueError('Projectile index is not editable for this item')
        result['projectile_speeds'] = speeds
        result['turrets'] = normalize_turret_counts(document.get('turrets'))
        result['missiles'] = normalize_missile_counts(document.get('missiles'))
        for side in ('upper', 'lower'):
            if result['turrets'][side] is not None and result['missiles'][side] is not None and result['missiles'][side] > result['turrets'][side]:
                raise ValueError('Seeking turrets cannot exceed the row total')
        settings = document.get('settings')
        if not isinstance(settings, dict):
            raise ValueError('Invalid game settings')
        result['settings'] = game_settings.normalize(settings)
        # Export only settings used by the simulator, never arbitrary imported fields.
        result['settings'] = {k: result['settings'][k] for k in ('map', 'drops', 'balance', 'skip_intro', 'missile_delay_ms') if k in result['settings']}
        return result

    def save(self, document):
        value = self.validate(document)
        with self.lock:
            current = self.read()
            if value['revision'] != current['revision']:
                raise ValueError('Loadout changed in another tab. Export your draft, then reload.')
            value['revision'] += 1
            atomic_json(self.path, value)
            (self.directory/'draft.json').unlink(missing_ok=True)
        return copy.deepcopy(value)

    def materialize(self, map_id):
        """Write only browser session inputs, never the desktop presets."""
        document = self.read()
        loadouts = copy.deepcopy(document['loadouts'])
        loadouts.update(_turret_counts=document['turrets'], _turret_missiles=document['missiles'])
        atomic_json(self.directory / 'class_loadouts.json', loadouts)
        atomic_json(self.directory / 'class_loadout_overrides.json', {
            projectile_speeds.KEY: document['projectile_speeds']})
        atomic_json(self.directory / 'game_settings.json', dict(document['settings'], map=map_id))
        return document

    def restore_defaults(self, revision):
        with self.lock:
            current = self.read()
            if integer(revision, 'Revision', 0, 2**31-1) != current['revision']:
                raise ValueError('Loadout changed in another tab. Export your draft, then reload.')
            loadouts = presets.defaults('class_loadouts.json')
            overrides = presets.defaults('class_loadout_overrides.json')
            document = dict(schema=SCHEMA, revision=revision, defaults_revision=2,
                loadouts={str(k): loadouts[str(k)] for k in CLASSES},
                projectile_speeds=overrides.get(projectile_speeds.KEY, {}),
                turrets=overrides.get('_turret_counts', {}),
                missiles=overrides.get('_turret_missiles', {}),
                settings=presets.defaults('game_settings.json'))
            document = self.validate(document)
            # Retain a recovery copy for each explicit reset.
            with tempfile.NamedTemporaryFile(mode='wb', prefix='profile.before-restore-defaults-',
                                             suffix='.json', dir=self.directory, delete=False) as backup:
                backup.write(self.path.read_bytes())
            return self.save(document)

    def editor(self):
        profile = self.read()
        return dict(profile=profile, preferences=self.preferences(), draft=self.draft(),
            key_choices=list(keybindings.CHOICES), zoom_key_choices=list(ZOOM_KEYS), key_defaults=dict(WEB_KEYS),
            maps=[dict(id=map_id, drops=game_settings.drop_settings(map_id, profile['settings']),
                       balance_limits=game_settings.balance_limits(map_id) if map_id in game_settings.BALANCE_MAPS else {},
                       balance_defaults=game_settings.balance_defaults(map_id))
                  for map_id in game_settings.ordered_maps()],
            slots=[dict(id=slot, zh=name, en=i18n.translate(name, 'en')) for slot, name in equipment.SLOTS],
            stats=[dict(id=stat, en=name, zh=i18n.STAT_ZH.get(stat,name)) for stat, name in equipment.STAT_NAMES.items()],
            items=[dict(item, suggested=self.priority['exact'].get(item['type'], []),
                        defaults=self.priority['defaults'].get(item['type'], {})) for item in self.catalogue.values()])
