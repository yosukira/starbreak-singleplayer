'Fully local StarBreak server.'

import argparse
import base64
import copy
import importlib
import json
import os
import random
import socket
import sys
import threading
import time
import traceback

if __package__ is None or __package__ == '':
    sys.path.insert(0, os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    from localserver import (character_messages, decorations,
                             game_settings, jsonio, pb_layout,
                             player_messages, protocol, world_objects)
else:
    from . import (character_messages, decorations, game_settings, jsonio,
                   pb_layout, player_messages, protocol, world_objects)

_OPTION_POSITION = object()

DEFAULT_DATA_VERSION = (
    os.path.join(os.environ.get('HULK_GAME','StarBreak'),'data','texture','dataVersion.bpb'))
DEFAULT_CHARACTERS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'catalogue', 'characters.json')
DEFAULT_CLASS_LOADOUTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'userdata', 'class_loadouts.json')
DEFAULT_PLAYER_PAYLOADS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'catalogue', 'class_players.json')
DEFAULT_CHARACTER_DESCRIPTIONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'data', 'catalogue', 'character_descriptions.jsonl')

MT_HELLO = 1
MT_CONNECT = 3
MT_JOINED = 8
MT_CREATE_WORLD = 10
MT_WORLD_CREATED = 11
MT_SERVER_DELTA = 12
MT_DELTA_ACK = 13
MT_PLAYER_INPUT = 14
MT_TIME_PASSES = 15
MT_DESCRIBE_ITEM = 16
MT_DESCRIBE_CHAR = 17
MT_PVID_LOADED = 22
MT_LOAD_PVID = 21

LOG = None

def _label_only_item_affixes(value):
    """Omit zero modifier values on the wire, retaining the saved recipe.

    ItemToolTip::addPropLabel @0x4b8cce checks StatBoost.has_val(). With
    no value field it builds a label only; with an explicit zero it prints
    '+0'. The stat getter still returns zero when the field is absent.
    This is applied to CreatePlayer items, never DescribeItem base stats.
    """
    if isinstance(value, list):
        return [_label_only_item_affixes(row) for row in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, row in value.items():
        if key in ('statboosts', 'statboosts_') and isinstance(row, list):
            boosts = []
            for boost in row:
                current = dict(boost)
                for field in ('val', 'val_'):
                    if field in current and current[field] == 0:
                        del current[field]
                boosts.append(current)
            result[key] = boosts
        else:
            result[key] = _label_only_item_affixes(row)
    return result

def log(message):
    line = '[{0}] {1}'.format(time.strftime('%H:%M:%S'), message)
    print(line)
    if LOG is not None:
        LOG.write(line + '\n')
        LOG.flush()

def _original_session_class():
    """Import the original Boss session lazily.

    ``localserver.live.session`` imports this module's siblings, so the
    original code imported it inside ``_start_live``; the map dispatcher
    needs the same lazy import because the name is not a module global.
    """
    if __package__ in (None, ''):
        from localserver.live.session import LiveSession
    else:
        from .live.session import LiveSession
    return LiveSession

def _map_session_class(map_id):
    """Resolve the live-session class for a map pack.

    ``maps/<id>/map.json`` names the session module and class, so a new map
    pack works without editing this file; the two built-in lab maps keep
    their historical mapping as a fallback and anything unknown falls back
    to the original Boss session.
    """
    from .maps.run_common.session import session_class as full_run_session_class
    full_run = full_run_session_class(map_id)
    if full_run is not None:
        return full_run
    if map_id in (None, '', game_settings.ORIGINAL_MAP):
        return _original_session_class()
    info = game_settings.map_info(map_id)
    module_name = info.get('session_module')
    class_name = info.get('session_class')
    if not module_name or not class_name:
        legacy = game_settings.LEGACY_SESSIONS.get(map_id)
        if legacy:
            module_name, class_name = legacy
    if not module_name or not class_name:
        return _original_session_class()
    try:
        module = importlib.import_module(module_name)
        return getattr(module, class_name)
    except (ImportError, AttributeError) as error:
        log('map {0} session unavailable ({1}); using the original session'
            .format(map_id, error))
        return _original_session_class()

def load_data_version(path):
    layouts = pb_layout.default_layouts()
    try:
        value = layouts.decode_file('DataVersion', path)
    except Exception:
        value = {}
    return {
        'id': value.get('id_', value.get('id', 'EPREccMdnFQ8-gdEwi1SwQ')),
        'timestamp': int(value.get(
            'timestamp_', value.get('timestamp', 0)) or 0),
    }

def build_hello(data_version):
    return {
        'hid': 0x12345678,
        'gameversion': '1.14.6',
        'dataversionid': data_version['id'],
        'dataversiontimestamp': data_version['timestamp'],
    }

def build_joined():

    return {
        'guid': 'local-guid-0001',
        'accountid': 'local-account-0001',
        'username': 'Localplayer',
        'isadmin': False,
    }

def _describe_item_type(payload):
    """Return field 1 (``type_``) of a DescribeItem message."""
    offset = 0
    while offset < len(payload):
        tag = payload[offset]
        offset += 1
        field = tag >> 3
        wire = tag & 7
        if wire == 0:
            value = 0
            shift = 0
            while offset < len(payload):
                byte = payload[offset]
                offset += 1
                value |= (byte & 0x7f) << shift
                if not byte & 0x80:
                    break
                shift += 7
            if field == 1:
                return value
        elif wire == 2:
            if offset >= len(payload):
                return None
            offset += 1 + payload[offset]
        elif wire == 1:
            offset += 8
        elif wire == 5:
            offset += 4
        else:
            return None
    return None

def _item_field(message, field, default=None):
    'Accept both runtime JSON fields and decoded protobuf fields.'
    value = message.get(field + '_', message.get(field, default))
    return value.get('value', default) if isinstance(value, dict) else value

class LocalServer(object):

    INVENTORY_REFRESH_REQUIRED = True

    GROUND_EGRESS_REQUIRED = False
    GROUND_RESTORE_REQUIRED = False

    def __init__(self, options):
        self.options = options
        self.layouts = pb_layout.default_layouts()
        self.data_version = load_data_version(options.data_version)
        self.create_world_blob = self._load_create_world(options)
        self.bootstrap_delta_blob = self._load_bootstrap_delta(options)
        self.character_library = self._load_character_library(options)
        self.character_description_payloads = (
            self._load_character_description_payloads(options))
        self.class_loadouts = self._load_class_loadouts(options)
        self.class_loadouts_path = getattr(options, 'class_loadouts', None)
        self._class_loadouts_mtime = None
        self._remember_class_loadouts_mtime()
        self.item_library = self._load_item_library(options)
        self.player_delta_blob = self._load_player_delta(options)
        self.pending_player_delta = False
        self.tick_thread = None
        self.tick_stop = threading.Event()
        self.live_session = None
        self.map_watch_thread = None
        self.map_watch_stop = threading.Event()
        self._map_settings_mtime = None
        self._restart_bridge = None
        self._restart_in_progress = False
        self._restart_player_type = None

        self.supports_class_switch = bool(
            getattr(options, 'experimental_class_switch', False))
        self.send_lock = threading.Lock()
        self.session = 0

    def _load_character_library(self, options):
        path = options.characters
        if not path:
            return {}
        if not os.path.exists(path):
            log('character catalog not found: {0}'.format(path))
            return {}
        library = character_messages.load_library(path)
        log('loaded {0} character descriptions from {1}'.format(
            len(library), path))
        return library

    def _load_character_description_payloads(self, options):
        'Load raw CharacterDescription payloads runtime from retail.'
        path = getattr(options, 'character_descriptions', None)
        if not path or not os.path.exists(path):
            return []
        payloads = []
        text, _ = jsonio.read_text(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                blob = base64.b64decode(row.get('payloadBase64') or '')
            except Exception:
                continue
            if blob:
                payloads.append(blob)
        log('loaded {0} raw CharacterDescription payloads from {1}'.format(
            len(payloads), path))
        return payloads

    def _load_class_loadouts(self, options):
        path = getattr(options, 'class_loadouts', None)
        if not path or not os.path.exists(path):
            return {}
        try:
            document, _ = jsonio.load_json(path)
        except Exception as error:
            log('class loadouts load failed: {0}: {1}'.format(
                type(error).__name__, error))
            return {}
        loadouts = {}
        for key, value in (document or {}).items():
            if key in ('_turret_counts', '_turret_missiles'):
                loadouts[key] = value
                continue
            try:
                loadouts[int(key)] = value
            except (TypeError, ValueError):
                continue
        class_count = sum(isinstance(key, int) for key in loadouts)
        log('loaded {0} class loadouts from {1}'.format(class_count, path))
        return loadouts

    def _remember_class_loadouts_mtime(self):
        path = getattr(self, 'class_loadouts_path', None)
        try:
            self._class_loadouts_mtime = os.path.getmtime(path) if path else None
        except OSError:
            self._class_loadouts_mtime = None

    def class_loadout_apply_pending(self):
        """True when the gear editor replaced the watched preset file."""
        path = getattr(self, 'class_loadouts_path', None)
        if not path or not os.path.exists(path):
            return False
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False
        return (self._class_loadouts_mtime is None
                or mtime > self._class_loadouts_mtime)

    def reload_class_loadouts_if_changed(self):
        """Re-read the preset file after the gear editor writes it.

        The long-press character reload rebuilds ``CreatePlayer`` from
        ``self.class_loadouts``; refreshing here makes editor saves visible
        without restarting the local server.  The mtime check keeps the
        normal no-edit path free of file I/O beyond one stat call.
        """
        path = getattr(self, 'class_loadouts_path', None)
        if not path or not os.path.exists(path):
            return False
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False
        if self._class_loadouts_mtime is not None and mtime <= self._class_loadouts_mtime:
            return False
        try:
            document, _ = jsonio.load_json(path)
        except Exception as error:
            log('class loadout hot reload failed: {0}: {1}'.format(
                type(error).__name__, error))
            return False
        loadouts = {}
        for key, value in (document or {}).items():
            if key in ('_turret_counts', '_turret_missiles'):
                loadouts[key] = value
                continue
            try:
                loadouts[int(key)] = value
            except (TypeError, ValueError):
                continue
        self.class_loadouts = loadouts
        self._class_loadouts_mtime = mtime
        class_count = sum(isinstance(key, int) for key in loadouts)
        log('hot-reloaded {0} class loadouts from {1}'.format(
            class_count, path))
        return True

    def _load_player_payloads(self, options):
        """Load anonymous class templates with the native character shape."""
        if getattr(options, 'no_player_payloads', False):
            return {}
        path = getattr(options, 'player_payloads', DEFAULT_PLAYER_PAYLOADS)
        if not path or not os.path.isfile(path):
            return {}
        document, _ = jsonio.load_json(path)
        payloads = {}
        for key, value in document.items():
            if key not in ('1', '2', '3', '5') or not isinstance(value, dict):
                continue
            char_type = int(key)
            character = value.get('charprops') or {}
            if int(_item_field(character, 'type', 0)) != char_type:
                raise ValueError('Class template does not match character ' + key)
            payloads[char_type] = value
        log('loaded {0} anonymous character templates'.format(len(payloads)))
        return payloads

    def _build_recorded_player_blob(self, options, template, player_type,
                                    position, loadout=None):
        """Keep native class fields and apply the editable starting loadout."""
        message = copy.deepcopy(template)
        message['accountid'] = 'local-account-0001'
        message['username'] = 'Localplayer'
        message.pop('factionname', None)
        message['charprops']['charid'] = player_type
        if loadout:
            self._apply_recorded_loadout(message, loadout, player_type)
        self._scrub_recorded_progress(message)
        message.pop('pos', None)
        message.pop('pos_', None)
        if position is not None:
            message['pos'] = dict(zip(('xmp', 'ymp', 'wmp', 'hmp'),
                                      map(int, position)))
        for slot in message.get('slottedcharprops') or []:
            slot_type = int(_item_field(slot, 'type', 0))
            preset = (self.class_loadouts.get(slot_type) or {}).get('items_')
            if preset:
                slot['items'] = preset
        dropped = self._drop_unknown_items(message)
        self._fill_loadout_vitals(message, loadout, player_type)
        blob = self.layouts.encode('Delta', {'createplayer_': _label_only_item_affixes(message)})
        log('built class template type={0} items={1} dropped={2} ({3} bytes)'.format(
            player_type, len(message['charprops'].get('items') or []), dropped, len(blob)))
        return blob

    LOADOUT_STAT_FIELDS = (
        'hitpoints_', 'maxhitpoints_', 'ammo_', 'maxammo_', 'damagebonus_',
        'armor_', 'walkspeed_', 'jumpspeed_', 'critchance_', 'critmult_',
        'mainslots_', 'secslots_', 'impslots_', 'invslots_',
    )

    def _apply_recorded_loadout(self, message, loadout, player_type):
        'Overlay the editable practice numbers on a runtime character.'
        if int(loadout.get('type_', player_type) or player_type) != player_type:
            log('loadout type {0} does not match character {1}; not applied'
                .format(loadout.get('type_'), player_type))
            return
        charprops = message.setdefault('charprops', {})
        applied = []
        if loadout.get('mk_') is not None:
            try:
                mk = int(loadout.get('mk_'))
            except (TypeError, ValueError):
                mk = None
            description = (getattr(self, 'character_library', None) or {}).get(
                int(player_type))
            if mk is not None and description:
                level = max(0, min(5, mk - 1))
                base = character_messages.character_properties(
                    description, level=level,
                    charid=charprops.get('charid_', player_type))
                for key in self.LOADOUT_STAT_FIELDS:
                    if key in base:
                        charprops[key[:-1] if key.endswith('_') else key] = base[key]
                applied.append('mk_{0}'.format(mk))
            elif mk is not None:
                log('cannot apply mk_{0} to character {1}: missing '
                    'CharacterDescription'.format(mk, player_type))

        for key in self.LOADOUT_STAT_FIELDS:
            if key not in loadout:
                continue
            charprops[key[:-1] if key.endswith('_') else key] = loadout[key]
            applied.append(key)
        if loadout.get('items_'):
            charprops['items'] = loadout['items_']
            applied.append('items_')
        log('applied practice loadout to recorded character {0}: {1}'.format(
            player_type, ','.join(applied) or 'nothing'))

    def _fill_loadout_vitals(self, message, loadout, player_type):
        """Spawn an MK preset full after equipped item bonuses are applied.

        Max HP/ammo fields remain the base stats: the client adds equipment
        itself.  Only current HP/ammo include those bonuses here.  Explicit
        current values in a practice/editor loadout remain authoritative.
        """
        if (not loadout or loadout.get('mk_') is None
                or int(loadout.get('type_', player_type) or player_type)
                != player_type
                or not (getattr(self, 'character_library', None) or {}).get(
                    player_type)):
            return
        charprops = message.get('charprops_', message.get('charprops')) or {}
        bonuses = {2: 0, 4: 0}
        ammo_multiplier = 1
        catalogue = getattr(self, 'item_descriptions', None) or {}
        for item in _item_field(charprops, 'items', []) or []:
            slot = _item_field(item, 'slotpos')
            if slot is None or not 0 <= int(slot) < 3840:
                continue
            description = catalogue.get(int(_item_field(item, 'type', 0))) or {}
            boosts = list(_item_field(description, 'statboosts', []) or [])
            boosts.extend(_item_field(item, 'statboosts', []) or [])
            for boost in boosts:
                stat = _item_field(boost, 'stat')
                if stat is not None and int(stat) in bonuses:
                    bonuses[int(stat)] += int(_item_field(boost, 'val', 0) or 0)
                elif stat is not None and int(stat) == 27:
                    ammo_multiplier += int(_item_field(boost, 'val', 0) or 0)
        for current, maximum, stat, minimum in (
                ('hitpoints', 'maxhitpoints', 2, 1),
                ('ammo', 'maxammo', 4, 0)):
            if current + '_' in loadout or current in loadout:
                continue
            base = _item_field(charprops, maximum)
            if base is not None:
                key = current + '_' if current + '_' in charprops else current
                value = max(minimum, int(base) + bonuses[stat])
                charprops[key] = value * ammo_multiplier if stat == 4 else value

    def _scrub_recorded_progress(self, message):
        'Test/sandbox accounts must not inherit any runtime progression.'
        for alias in ('accountprops','accountprops_'):
            props=message.get(alias)
            if not isinstance(props,dict):continue
            for key in ('progress','progress_','totalstats','totalstats_','ec','ec_'):props.pop(key,None)
            props['progress' if alias=='accountprops' else 'progress_']={}
        holders=[message.get('charprops'),message.get('charprops_'),
            *(message.get('slottedcharprops') or []),*(message.get('slottedcharprops_') or [])]
        for holder in holders:
            if isinstance(holder,dict):
                for key in ('stats','stats_','score','score_'):holder.pop(key,None)

    def _drop_unknown_items(self, message):
        'Remove equipped items the client has no DescribeItem for.'
        known = getattr(self, 'item_types', None)
        if not known:
            return 0
        dropped = 0

        def scrub(holder):
            nonlocal dropped
            items = holder.get('items') or []
            kept = []
            for item in items:
                item_type = _item_field(item, 'type')
                if item_type is None or int(item_type) in known:
                    kept.append(item)
                else:
                    dropped += 1
            if len(kept) != len(items):
                holder['items'] = kept

        charprops = message.get('charprops')
        if isinstance(charprops, dict):
            scrub(charprops)
        for slot in message.get('slottedcharprops') or []:
            if isinstance(slot, dict):
                scrub(slot)
        if dropped:
            log('dropped {0} equipped items with no DescribeItem'.format(
                dropped))
        return dropped

    def _load_player_delta(self, options):
        self.player_payloads = self._load_player_payloads(options)
        return self._build_player_delta_blob(options)

    def _build_player_delta_blob(self, options, player_type=None,
                                 position=_OPTION_POSITION):
        """Build a CreatePlayer delta for one character type.

        ``position=None`` asks the client for its normal spawn-object search;
        this is what a zone reload/character switch uses.  The practice
        loadout is only used when its own ``type_`` matches the requested
        class, so switching from WF to DW cannot leak WF-only equipment.
        """
        if options.mode != 'hulk' or options.no_player:
            return None
        player_type = int(player_type if player_type is not None
                          else options.player_type)
        if position is _OPTION_POSITION:
            position = None
            if options.player_x is not None or options.player_y is not None:
                position = (
                    options.player_x if options.player_x is not None else 0,
                    options.player_y if options.player_y is not None else 0,
                    options.player_wmp,
                    options.player_hmp,
                )
        loadout = self.class_loadouts.get(player_type)
        explicit_loadout = False
        if getattr(options, 'loadout', None):
            explicit, loadout_encoding = jsonio.load_json(options.loadout)
            if loadout_encoding not in ('utf-8', 'utf-8-sig'):
                log('loadout {0} is {1}-encoded; re-save it as UTF-8 when '
                    'editing it in other tools'.format(options.loadout,
                                                       loadout_encoding))
            if int(explicit.get('type_', player_type)) == player_type:
                loadout = explicit
                explicit_loadout = True
            else:
                log('explicit loadout type {0} does not match character '
                    '{1}; keeping class loadout'.format(
                        explicit.get('type_'), player_type))

        template = self.player_payloads.get(player_type)
        if template is not None:
            return self._build_recorded_player_blob(
                options, template, player_type, position, loadout=loadout)
        slotted = []
        for char_type in sorted(self.character_library):
            class_loadout = self.class_loadouts.get(char_type) or {}
            slot = character_messages.character_properties(
                self.character_library[char_type],
                level=options.player_level,
                charid=char_type)
            slot['charslotid_'] = max(0, int(char_type) - 1)
            if class_loadout.get('items_'):
                slot['items_'] = class_loadout['items_']
            slotted.append(slot)
        message = player_messages.build_player_delta(
            playertype=player_type,
            description=self.character_library.get(player_type),
            level=options.player_level,
            position=position, loadout=loadout,
            charid=player_type, charslotid=max(0, int(player_type) - 1),
            slottedcharprops=slotted)
        self._fill_loadout_vitals(message['createplayer_'], loadout, player_type)
        blob = self.layouts.encode('Delta', _label_only_item_affixes(message))
        log('built create-player delta type={0} ({1} bytes)'.format(
            player_type, len(blob)))
        return blob

    def _load_item_library(self, options):
        """Load raw MT_DESCRIBE_ITEM payloads for the local client.

        The retail client looks up item descriptions when a loot object is
        created; without the login-time catalogue it aborts inside
        ``Item.cc`` ("Unable to find description of: N").  The one-shot
        capture stores every DescribeItem frame, so replay the same bytes.
        """
        from localserver import projectile_speeds
        self.item_descriptions = {}
        path = getattr(options, 'items', None)
        if not path:
            return []
        if not os.path.exists(path):
            log('item catalogue not found: {0}'.format(path))
            return []
        override_path = (getattr(options, 'projectile_overrides', None) or
                         os.path.join(game_settings.LAB, 'userdata', 'class_loadout_overrides.json'))
        overrides = (jsonio.load_json(override_path)[0]
                     if os.path.exists(override_path) else {})
        speeds = projectile_speeds.normalize(overrides.get(projectile_speeds.KEY) or {})
        payloads = []
        text, _ = jsonio.read_text(path)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except ValueError:
                continue
            payload = base64.b64decode((item.get('payloadBase64') or '').encode('ascii'))
            description = self.layouts.decode('ItemDescription', payload)
            item_type = _item_field(description, 'type')
            changes = speeds.get(str(item_type))
            if changes:
                payload = projectile_speeds.patch_payload(self.layouts, payload, description, changes)
                log('applied projectile speeds for item {0}: {1}'.format(item_type, changes))
            payloads.append(payload)
        self.item_types = set(
            value for value in (_describe_item_type(payload)
                                for payload in payloads)
            if value is not None)
        for payload in payloads:
            description = self.layouts.decode('ItemDescription', payload)
            item_type = _item_field(description, 'type')
            if item_type is not None:
                self.item_descriptions[int(item_type)] = description
        log('loaded {0} item descriptions ({1} ids) from {2}'.format(
            len(payloads), len(self.item_types), path))
        return payloads


    def _load_bootstrap_delta(self, options):
        if options.mode != 'hulk' or options.no_bootstrap:
            return None
        make_create_world = self._import_world_tool()
        if options.bootstrap_creates:

            objects = self._world_objects(make_create_world)
            message = make_create_world.build_bootstrap_delta(
                objects, self._worldid())
        else:
            message = make_create_world.build_subworld_delta(
                self._worldid())
        blob = self.layouts.encode('Delta', message)
        log('built bootstrap delta ({0} bytes)'.format(len(blob)))
        return blob

    def _worldid(self):
        return int(getattr(self.options, 'worldid', 1) or 1)

    @staticmethod
    def _import_world_tool():
        try:
            from tools import make_create_world
        except Exception:
            sys.path.insert(0, os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))))
            from tools import make_create_world
        return make_create_world

    def _world_objects(self, make_create_world):
        path = getattr(self.options, 'objects', None)
        if path:
            objects, _ = jsonio.load_json(path)
            log('loaded {0} world objects from {1}'.format(
                len(objects), path))
            return self._decorate_objects(objects)
        return [{'objid': 1, 'props': make_create_world.stub_props()}]

    def _decorate_objects(self, objects):
        """Add the render-only flags the retail client reads back."""
        if not self.options.terrain_flags:
            return objects
        zone = self.options.zone or self.options.pvid
        normalized = world_objects.normalize(objects, zone=zone)
        terrain = sum(
            1 for item in normalized
            if item['props'].get('isinternal_'))
        log('normalized {0} objects ({1} baked into the tile map)'.format(
            len(normalized), terrain))
        return normalized

    def _roll_map_room(self, blob, options):
        """Let a map module randomize its room *inside* the CreateWorld.

        The jungle boss rolls hives/red worms/eye worms/pincers while the room
        is built, so the client never plays placeholder destroy animations and
        the pincers keep their original creation order (drawn under the boss).
        """
        try:
            map_id = getattr(options, 'map', None)
            if not map_id:
                return None
            info = game_settings.map_info(map_id) or {}
            module_name = info.get('session_module')
            if not module_name:
                return None
            module = importlib.import_module(module_name)
            hook = getattr(module, 'roll_create_world', None)
            if hook is None:
                return None
            seed = random.SystemRandom().randrange(1 << 30)
            choose_seed = getattr(module, 'choose_room_seed', None)
            if choose_seed is not None:
                previous_seed = (getattr(options, 'room_roll_seed', None)
                                 if getattr(options, 'room_roll_map', None) == map_id else None)
                seed = choose_seed(previous_seed, root=os.path.dirname(options.create_world_hex))
            rolled = hook(blob, seed)
            if not rolled:
                return None
            options.room_roll_seed = seed
            options.room_roll_map = map_id
            log('rolled room for {0} (seed {1})'.format(map_id, seed))
            return rolled
        except Exception as error:
            log('room roll failed: {0}: {1}'.format(type(error).__name__, error))
            return None

    def _load_create_world(self, options):
        from .maps.run_common.session import session_class as full_run_session_class
        full_run = full_run_session_class(getattr(options, 'map', None))
        if full_run is not None:
            return self._distinct_player_world_blob(full_run.initial_world(self, options))
        if getattr(options, 'map', None) == 'egy_run':
            from .maps.egy_run.session import initial_world
            return self._distinct_player_world_blob(initial_world(self, options))
        if options.create_world_hex:
            with open(options.create_world_hex, 'r') as handle:
                blob = bytes.fromhex(handle.read().strip())
            log('loaded create-world blob ({0} bytes)'.format(len(blob)))
            if options.terrain_flags:
                try:
                    blob = self._normalize_create_world_blob(blob)
                except Exception as error:
                    log('terrain normalization failed: {0}: {1}'.format(
                        type(error).__name__, error))
            try:
                blob = self._strip_turret_objects(blob)
            except Exception as error:
                log('turret strip failed: {0}: {1}'.format(
                    type(error).__name__, error))
            rolled = self._roll_map_room(blob, options)
            if rolled:
                blob = rolled
            return self._distinct_player_world_blob(blob)
        if options.mode != 'hulk':
            return None
        make_create_world = self._import_world_tool()
        objects = self._world_objects(make_create_world)
        zone = options.zone or options.pvid
        decoration = self._decoration(zone)
        message = make_create_world.build_create_world(
            objects, self._worldid(), zone, decoration=decoration,
            gdv=options.gdv if options.gdv else None,
            seed=options.seed if options.seed else None)
        raw = self.layouts.encode('CreateWorld', message)
        blob = make_create_world.wrap_compress(raw)
        log('built create-world blob ({0} bytes, raw {1})'.format(
            len(blob), len(raw)))
        try:
            blob = self._strip_turret_objects(blob)
        except Exception as error:
            log('turret strip failed: {0}: {1}'.format(
                type(error).__name__, error))
        return self._distinct_player_world_blob(blob)

    def _strip_turret_objects(self, blob):
        """Remove every ``hulk.bossgunturret`` from the client CreateWorld.

        The retail server creates only the turrets it rolled (the map-export
        snapshots show the rolled set, not a fixed slot table).  The live
        session creates the rolled set in ``_apply_turret_layout`` and never
        creates slots that were not rolled, so unused turrets do not exist
        on the client instead of being hidden with ``DRAW=False``.
        """
        make_create_world = self._import_world_tool()
        raw = make_create_world.unwrap_decompress(blob)
        message = self.layouts.decode('CreateWorld', raw)
        world = (message.get('worldstate_') or {}).get('serverworld_') or {}
        objects = world.get('obj_') or []
        kept = [item for item in objects
                if (item.get('props_') or {}).get('vid_')
                != 'hulk.bossgunturret']
        if len(kept) == len(objects):
            return blob
        world['obj_'] = kept
        raw2 = self.layouts.encode('CreateWorld', message)
        wrapped = make_create_world.wrap_compress(raw2)
        log('removed {0} pre-baked turret objects from CreateWorld'.format(
            len(objects) - len(kept)))
        return wrapped

    def _normalize_create_world_blob(self, blob):
        """Bake room walls/floor into the client tile map.

        The retail CreateWorld carries the wall rectangles but leaves the
        render-only ``isinternal_``/``draw_`` flags unset.  ``TileMap`` only
        rasterises objects with ``isinternal_`` set, which is why a local
        room renders sprites yet has no floor (and the player falls through).
        """
        make_create_world = self._import_world_tool()
        raw = make_create_world.unwrap_decompress(blob)
        message = self.layouts.decode('CreateWorld', raw)
        if (getattr(self.options, 'map', '') or '') == 'lab_hard':
            from .maps.lab_hard.terrain import thicken_boundary_message
            thicken_boundary_message(message)
        world = (message.get('worldstate_') or {}).get('serverworld_') or {}
        objects = world.get('obj_') or []
        zone = self.options.pvid or self.options.zone or 'hulk'
        tile_vids = decorations.tile_set_vids(zone)

        patched = 0
        for item in objects:
            props = item.get('props_') or {}
            terrain = int(props.get('terraintype_') or 0)
            vid = props.get('vid_') or ''
            if terrain in (1, 2):
                if vid not in tile_vids:

                    props['isinternal_'] = False
                    continue

                if terrain == 1:
                    map_id=getattr(self.options,'map','')

                    eub_boundary=(map_id=='sc_hive' or
                                  (map_id in ('dumb_boss','dumb_wave','dumb_normal_wave','dumb_mid','ice_boss','ice_mid','sc_bonesaw','sc_patriarch','forest_mid','forest_boss')
                                   and props.get('isinternal_') is False))

                    props['isinternal_'] = not eub_boundary

                    if map_id!='sc_patriarch' or props.get('boundsworld_') is not False:
                        props['boundsworld_'] = True
                    props.setdefault('z_', 60)
                if 'draw_' not in props:
                    props['draw_'] = True
                patched += 1

        raw2 = self.layouts.encode('CreateWorld', message)
        log('normalized terrain flags on {0} objects'.format(patched))
        return make_create_world.wrap_compress(raw2)

    def _distinct_player_world_blob(self, blob):
        """Keep native player IDs separate from server/peer object IDs.

        CreateWorld.worldid constructs the client's own NativeSubWorld.
        WorldState.serverworld.ownerid identifies the foreign room objects.
        A generated pack using 1 for both makes local bullet IDs collide
        with scenery IDs in otherwise valid Damage/Destroy feedback.
        """
        tool = self._import_world_tool()
        message = self.layouts.decode('CreateWorld', tool.unwrap_decompress(blob))
        state = message.get('worldstate_') or {}
        worlds = [state.get('serverworld_') or {}]+list(state.get('clientworld_') or [])
        used = {int(w.get('ownerid_', 0)) for w in worlds}
        if int(message.get('worldid_', 0)) not in used:
            return blob
        message['worldid_'] = max(used)+1
        return tool.wrap_compress(self.layouts.encode('CreateWorld', message))

    def _decoration(self, zone):
        if not self.options.decoration:
            return None
        decoration = decorations.build(
            zone, gdv=self.options.gdv or 0, z=self.options.decoration_z)
        log('decoration: external={0} transition={1} internal={2} z={3}'.format(
            decoration['externaltiles_'][0]['vid_'],
            decoration['transitiontiles_'][0]['vid_'],
            decoration['internaltiles_'][0]['vid_'],
            self.options.decoration_z))
        return decoration

    def serve_forever(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.options.host, self.options.port))
        sock.listen(4)
        log('local server listening on {0}:{1}'.format(
            self.options.host, self.options.port))
        while True:
            try:
                client, address = sock.accept()
            except KeyboardInterrupt:
                break
            self.session += 1
            log('client connected: {0}:{1}'.format(*address))
            try:
                self.handle_client(client)
            except Exception:
                log('client session failed:\n' + traceback.format_exc())
            finally:
                self.tick_stop.set()
                self.map_watch_stop.set()
                ground=getattr(self,'_ground_egress_host',None)
                if ground is not None:ground.stop.set()
                self._ground_egress_host=None
                self._ground_egress_ack_pending=None
                restore=getattr(self,'_ground_restore_host',None)
                if restore is not None:restore.stop.set()
                self._ground_restore_host=None
                if self.live_session is not None:
                    self.live_session.stop()
                    self.live_session = None
                self.tick_thread = None
                try:
                    client.close()
                except Exception:
                    pass
                log('client disconnected')

    def handle_client(self, client):
        self.tick_stop = threading.Event()
        self.tick_thread = None
        channel = protocol.FrameChannel(client)
        hello = build_hello(self.data_version)
        hello_blob = self.layouts.encode('Hello', hello)
        channel.send_frame(MT_HELLO, hello_blob)
        log('-> HELLO {0}'.format(json.dumps(hello, sort_keys=True)))

        message_type, payload = channel.read_frame()
        log('<- type={0} length={1}'.format(message_type, len(payload)))
        self._record('client', message_type, payload)
        if message_type != MT_CONNECT:
            raise protocol.ProtocolError(
                'expected MT_CONNECT, got {0}'.format(message_type))
        connect = self.layouts.decode('Connect', payload)
        raw_seed = connect.get('enckey_', connect.get('enckey', 0)) or 0
        if isinstance(raw_seed, (bytes, bytearray)):
            raw_seed = int.from_bytes(raw_seed, 'little')
        seed = int(raw_seed) & 0xffffffff
        key = protocol.random_string(seed, 64)
        channel.enable_rc4(key)
        log('MT_CONNECT seed=0x{0:08x} rc4=on'.format(seed & 0xffffffff))
        log('Local client connected')

        if self.options.mode == 'join':
            self._send(channel, MT_JOINED, 'Joined', build_joined())
        elif self.options.mode == 'hulk':
            self._send(channel, MT_JOINED, 'Joined', build_joined())
            self._send_character_descriptions(channel)
            self._send_item_descriptions(channel)
            self._request_pvid_loads(channel)
        if self.options.interactive:
            thread = threading.Thread(target=self._console, args=(channel,))
            thread.daemon = True
            thread.start()
        self._read_loop(channel)

    def _send_character_descriptions(self, channel):
        'Local battle simulator runtime.'
        wanted = self.options.describe_types
        if self.character_description_payloads:
            sent = set()
            for payload in self.character_description_payloads:
                try:
                    description = self.layouts.decode(
                        'CharacterDescription', payload)
                    char_type = int(description.get('type_') or 0)
                except Exception:
                    char_type = 0
                if wanted and char_type not in wanted:
                    continue
                if char_type in sent:
                    continue
                sent.add(char_type)
                self._send_raw(channel, MT_DESCRIBE_CHAR, payload,
                               'CharacterDescription')
            return
        for char_type in sorted(self.character_library):
            if wanted and char_type not in wanted:
                continue
            description = self.character_library[char_type]
            self._send(channel, MT_DESCRIBE_CHAR, 'CharacterDescription',
                       description)

    def _send_item_descriptions(self, channel):
        'Local battle simulator runtime.'
        for payload in self.item_library:
            self._send_raw(channel, MT_DESCRIBE_ITEM, payload, None,
                           record=False)

    def _send_raw(self, channel, message_type, blob, label=None, record=True):
        """Send a pre-encoded payload under the channel lock."""
        restore=getattr(self,'_ground_restore_host',None)
        if restore is not None and restore.world_sent and not restore.ready.is_set() and message_type==12:
            from .world_snapshot import decode_delta
            if 'createloot_' in decode_delta(self.layouts,blob):restore.allow_createloot(blob)
        with self.send_lock:
            channel.send_frame(message_type, blob)
        if record:
            log('-> type={0} length={1}{2}'.format(
                message_type, len(blob),
                ' ({0})'.format(label) if label else ''))
            self._record('server', message_type, blob)

    def _start_world_tick(self, channel):
        """Feed the client a WorldUpdate every frame.

        ``ClientWorld`` only advances its simulation while it has server
        time credit: ``ClientWorld::addTimePasses`` compares the local frame
        delta against credit accumulated from incoming ``WorldUpdate.dt_``
        messages and logs "Time reserve exhausted: skipping update" when it
        runs dry (the world freezes and the camera never follows the
        player).  A real server therefore sends one delta per tick even when
        nothing changed; the local server mimics that.
        """
        if self.tick_thread is not None:
            return
        hz = max(1, int(self.options.tick_hz or 60))
        dt = max(1, int(round(1000.0 / hz)))
        payload = self.layouts.encode('Delta', {
            'worldupdate_': {'worldid_': self._worldid(), 'dt_': dt},
        })

        def run():
            interval = 1.0 / hz
            deadline = time.time()
            while not self.tick_stop.is_set():
                deadline += interval
                try:
                    self._send_raw(channel, MT_SERVER_DELTA, payload, 'tick')
                except Exception:
                    return
                wait = deadline - time.time()
                if wait > 0:
                    time.sleep(wait)
                else:
                    deadline = time.time()

        self.tick_thread = threading.Thread(target=run)
        self.tick_thread.daemon = True
        self.tick_thread.start()
        log('world tick started at {0} Hz (dt={1})'.format(hz, dt))

    def request_live_restart(self, channel, session, player_type):
        """Reload the current zone so the client rebuilds the room.

        This is the long-press R (restart) / 7-8-9-0 (class switch) path.
        LoadPVid is the normal retail
        zone transition: the client answers PVID_LOADED, the read loop sends
        CreateWorld again, and a fresh LiveSession starts from the intro.
        The old session stops sending immediately; its bridge is handed to
        the replacement session so the UDP port never has to rebind.
        """
        if self._restart_in_progress:
            return
        if not self._ground_ack_ready(session.bridge):return
        self.reload_class_loadouts_if_changed()
        requested_type = int(player_type)
        current_type = int(getattr(session, 'player_type', self.options.player_type) or 1)
        if requested_type != current_type:
            has_payload = requested_type in (self.player_payloads or {})
            if not self.supports_class_switch and not has_payload:
                log('class switch to {0} is not enabled: no recorded '
                    'CreatePlayer payload for it and '
                    '--experimental-class-switch is off; restarting current '
                    'class {1} instead'.format(requested_type, current_type))
                requested_type = current_type
            elif has_payload:
                log('class switch to {0} uses the recorded CreatePlayer '
                    'payload'.format(requested_type))
        self._restart_in_progress = True
        self._restart_player_type = requested_type
        if getattr(self.options,'map',None)=='lab_hard':
            from .maps.lab_hard.terrain import choose_variant
            self.options.create_world_hex=choose_variant(game_settings.map_dir('lab_hard'),getattr(self.options,'terrain_variant',None))

        self.create_world_blob = self._load_create_world(self.options)
        try:
            self.player_delta_blob = self._build_player_delta_blob(
                self.options, self._restart_player_type, position=None)
        except Exception as error:
            log('restart player delta failed: {0}: {1}'.format(
                type(error).__name__, error))

        try:
            session.bridge.consume_events(('restart', 'switch_character'))
        except Exception:
            pass
        session.suppress_send = True
        session.stop(stop_bridge=False)
        self._restart_bridge = session.bridge
        if self.live_session is session:
            self.live_session = None
        log('live restart requested: character type {0}'.format(
            self._restart_player_type))
        self._request_pvid_loads(channel)

    def _ensure_map_watch(self, channel):
        """Watch game_settings.json so an editor map change applies live."""
        if (self.map_watch_thread is not None
                and self.map_watch_thread.is_alive()):
            return
        self.map_watch_stop.clear()
        self._map_settings_mtime = game_settings.settings_mtime()
        thread = threading.Thread(target=self._map_watch_loop,
                                  args=(channel,))
        thread.daemon = True
        self.map_watch_thread = thread
        thread.start()

    def _apply_bridge_restart(self, channel, session):
        """Honour long-press R / 7-8-9-0 for every map session.

        Map sessions drain gameplay events with ``poll_events``; the bridge
        keeps the restart holds queued so this server-side handler serves
        any current or future map pack without touching its session code.
        """
        bridge = getattr(session, 'bridge', None)
        if bridge is None:
            return False
        try:
            events = bridge.peek_events()
        except Exception:
            return False
        for item in events:
            if not isinstance(item, dict):
                continue
            kind = item.get('kind')
            if kind not in ('restart', 'switch_character'):
                continue
            current = getattr(session, 'player_type', 1) or 1
            try:
                char_type = int(current if kind == 'restart'
                                else item.get('type') or current)
            except (TypeError, ValueError):
                char_type = current
            log('bridge {0} hold: character {1}'.format(kind, char_type))
            self.request_live_restart(channel, session, char_type)
            return True
        return False

    def _map_watch_loop(self, channel):
        """Apply editor changes (map + long-press holds) to the session.

        The editor writes ``game_settings.json``; this poll (~0.5 s) turns
        a changed map selection into a zone reload, and the same tick
        serves the map-independent R restart / class-switch holds.
        """
        while not self.map_watch_stop.wait(0.5):
            session = self.live_session
            if session is None:
                continue
            if self._apply_bridge_restart(channel, session):
                continue
            current = (getattr(self.options, 'map', None)
                       or game_settings.ORIGINAL_MAP)
            changed, mtime = game_settings.changed_map(
                current, self._map_settings_mtime)
            if changed is None:
                self._map_settings_mtime = mtime
                continue
            if self.request_live_map_switch(channel, session, changed):
                self._map_settings_mtime = mtime

    def request_live_room_transition(self, channel, session, link, player):
        """Accept a door intent; commit only after its native inventory refresh."""
        if not self.INVENTORY_REFRESH_REQUIRED:
            return self._commit_live_room_transition(channel,session,link,player)
        return self._request_live_inventory_transition(channel,session,link,player,'room')

    def request_live_world_exit(self, channel, session, link, player):
        """A world portal preserves the current character; editor/R still reset."""
        link=copy.deepcopy(link)
        if link.get('destination')=='lobby':link['destination']='home'
        if link.get('destination')!='home':return False
        if not self.INVENTORY_REFRESH_REQUIRED:
            return self.request_live_map_switch(channel,session,'home')
        if self._world_exit_options('home') is None:return False
        return self._request_live_inventory_transition(channel,session,link,player,'world-exit')

    def _request_live_inventory_transition(self,channel,session,link,player,purpose):
        if not self._ground_ack_ready(session.bridge):return False
        map_id=getattr(self.options,'map',None)
        run=getattr(session,'run',None)
        if (self._restart_in_progress or run is None or
                run is not getattr(self,'egy_run' if map_id=='egy_run' else 'level_run',None) or
                player is None or not player.alive or player.hp<=0 or player.age()>.5 or not run.allows(link)):
            return False
        pending=getattr(session,'_inventory_handoff',None)
        if pending is not None:
            return pending.link==link and getattr(session,'_inventory_handoff_purpose','room')==purpose

        if purpose=='room':run.destination_spawn(link['destination'],link.get('entry','default'))
        context=session.bridge.native_context or {}
        if not context.get('room_ready') or context.get('map')!=map_id:
            return False
        if player.raw.get('inventory_protocol')!='full-run-inventory-v1':
            reason='native inventory bridge unavailable; restart the simulator to load its updated plugin'
            if getattr(session,'_inventory_status',{}).get('reason')!=reason:log(map_id+' door rejected: '+reason)
            session._inventory_status=dict(status='rejected',reason=reason)
            return False
        issued=self.layouts.decode('Delta',self.player_delta_blob)['createplayer_']['charprops_']
        scope=dict(map=map_id,room_token=session._input_serial,
            room_seed=session.instance.recipe['world']['worldprops_'].get('seed_',0),
            worldid=player.worldid,objid=player.objid,charid=issued['charid_'],player_type=issued['type_'])
        from .maps.run_common.inventory_handoff import InventoryHandoff
        self._inventory_request_serial=getattr(self,'_inventory_request_serial',0)+1
        session._inventory_handoff=InventoryHandoff(self._inventory_request_serial,link,scope,time.time())
        session._inventory_handoff_purpose=purpose
        session._inventory_status=dict(status='pending',id=self._inventory_request_serial)
        session._inventory_effect_epoch_seen=None

        return True

    @staticmethod
    def _publish_inventory_control(session,control):
        context=dict(session.bridge.native_context or {})
        if control is None:context.pop('inventory_request',None)
        else:
            previous=context.get('inventory_request') or {}
            control=dict(control)
            same=all(control.get(k)==previous.get(k) for k in ('id','attempt','issued_at','scope'))
            control['native_t_min']=(previous['native_t_min'] if same and 'native_t_min' in previous
                else round((session.instance.snapshot.time-session._snapshot_native_origin)*1000)+1)
            context['inventory_request']=control
        session.bridge.native_context=context

    def poll_live_room_inventory(self,channel,session):
        """Keep the departing room alive while the bounded door request waits."""
        self._ground_ack_ready(session.bridge)
        pending=getattr(session,'_inventory_handoff',None)
        if pending is None:return False
        player,_=session.bridge.snapshot()
        context=session.bridge.native_context or {}
        if (self._restart_in_progress or self.live_session is not session or
                getattr(self.options,'map',None)!=session.map_id or
                any(context.get(k)!=pending.scope[k] for k in ('map','room_token','room_seed'))):
            pending.cancel('room/restart identity changed')
        epoch=getattr(session,'_inventory_effect_epoch',0)
        seen=getattr(session,'_inventory_effect_epoch_seen',None)
        if seen is not None and epoch!=seen:
            pending.restart_after_effect(time.time(),'new loot was sent before handoff')
        session._inventory_effect_epoch_seen=epoch
        snapshot=player.raw.get('inventory',{}) if player is not None else {}
        if not isinstance(snapshot,dict):snapshot={}
        control=context.get('inventory_request') or {}
        if (snapshot.get('request_id')==pending.request_id and snapshot.get('request_attempt')==pending.attempt and
                (type(snapshot.get('native_server_t')) is not int or
                 snapshot['native_server_t']<control.get('native_t_min',0))):
            pending.restart_after_effect(time.time(),'native TCP update barrier was not reached')
        issued=self.layouts.decode('Delta',self.player_delta_blob)['createplayer_']
        outcome=pending.poll(player,issued,time.time())
        session._inventory_status={k:v for k,v in outcome.items() if k not in ('create_player','link')}
        if outcome['status']=='pending':
            self._publish_inventory_control(session,pending.control());return False
        self._publish_inventory_control(session,None)
        session._inventory_handoff=None
        if outcome['status']!='ready':
            log(session.map_id+' inventory handoff rejected: '+str(outcome.get('reason')))
            return False
        log('{0} inventory ready: request={1} attempt={2} items={3} stash={4} native_t={5} barrier={6}'.format(
            session.map_id,pending.request_id,pending.attempt,outcome['report']['item_count'],
            outcome['report']['stash_item_count'],snapshot.get('native_server_t'),control.get('native_t_min')))
        if self.GROUND_EGRESS_REQUIRED:
            from .maps.run_common.ground_host import GroundEgressHost
            try:
                host=GroundEgressHost(self,channel,session,outcome['link'],
                    getattr(session,'_inventory_handoff_purpose','room'),pending.request_id,pending.attempt,pending.scope,issued)
                self._ground_egress_host=host
                session._inventory_status=dict(status='egress-arm',id=pending.request_id)
                host.start()
                return False
            except Exception as error:
                session._inventory_status=dict(status='rejected',reason='ground transition preparation failed: '+str(error))
                log(session.map_id+' '+session._inventory_status['reason']);return False
        if getattr(session,'_inventory_handoff_purpose','room')=='world-exit':
            return self._commit_live_world_exit(channel,session,outcome['link'],player,outcome['create_player'])
        return self._commit_live_room_transition(channel,session,outcome['link'],player,
            inventory_create=outcome['create_player'])

    def _ground_ack_ready(self,bridge):
        """Publishing an ACK cannot authorize a new same-PVID native load.

        Wait for the native coordinator to confirm consuming that exact ACK
        and removing its gate, including for R/editor/class restarts.
        """
        pending=getattr(self,'_ground_egress_ack_pending',None)
        if pending is None:return True
        if pending['bridge'] is not bridge:return False
        from .maps.run_common.ground_transition import fingerprint
        small=bridge.ground_egress_status()
        try:
            if (not isinstance(small,dict) or small.get('schema')!='full-run-egress-ready-v1' or
                    small.get('status')!='acknowledged' or
                    fingerprint(small.get('acknowledged_receipt'))!=fingerprint(pending['ack'])):return False
            fields=('request_id','request_attempt','issued_at','scope','expected_pvid','expected_cw_sha256')
            if fingerprint({k:small.get(k) for k in fields})!=fingerprint({k:pending['control'][k] for k in fields}):return False
        except (ValueError,TypeError,KeyError,UnicodeError):return False
        self._ground_egress_ack_pending=None
        bridge.finish_ground_egress_ack()
        return True

    def _world_exit_options(self,map_id):
        if map_id!='home':return None
        path=os.path.join(game_settings.map_dir(map_id),'createworld.hex')
        if not os.path.isfile(path):return None
        options=copy.copy(self.options);info=game_settings.map_info(map_id)
        options.map=map_id;options.create_world_hex=path
        options.pvid=info.get('pvid') or options.pvid
        options.zone=info.get('zone') or options.zone
        options.gdv=info.get('gdv',1)
        return options

    def _commit_live_world_exit(self,channel,session,link,player,inventory_create):
        """Prepare every Home payload before stopping the departing room."""
        map_id=getattr(self.options,'map',None)
        run=getattr(session,'run',None)
        if (self._restart_in_progress or self.live_session is not session or run is None or
                run is not getattr(self,'egy_run' if map_id=='egy_run' else 'level_run',None) or
                not run.allows(link) or player is None or not player.alive or player.hp<=0):return False
        options=self._world_exit_options(link.get('destination'))
        if options is None:return False
        try:
            world=self._load_create_world(options)
            bootstrap=self._load_bootstrap_delta(options)
            message=self.layouts.decode('Delta',self.player_delta_blob)
            created=copy.deepcopy(inventory_create);created.pop('pos_',None)
            created['charprops_']['hitpoints_']=int(player.hp)
            if player.raw.get('ammo') is not None:created['charprops_']['ammo_']=max(0,int(player.raw['ammo']))
            message['createplayer_']=created
            player_blob=self.layouts.encode('Delta',message)
            document=self.layouts.decode('CreateWorld',self._import_world_tool().unwrap_decompress(world))
            seed=document['worldprops_'].get('seed_',0)
        except Exception as error:
            session._inventory_status=dict(status='rejected',reason='world exit preparation failed: '+str(error))
            log(map_id+' '+session._inventory_status['reason']);return False
        self._restart_in_progress=True
        session.suppress_send=True;session.stop(stop_bridge=False)
        self.options=options;self.create_world_blob=world;self.bootstrap_delta_blob=bootstrap;self.player_delta_blob=player_blob
        self._restart_bridge=session.bridge;self._restart_player_type=int(getattr(session,'player_type',1) or 1)
        self._world_exit_arrival=dict(map='home',room_token=session._input_serial+1,room_seed=seed,
            room_ready=False,room_handoff=True)
        with session.bridge._lock:
            session.bridge.state=None;session.bridge.events=[];session.bridge.keys={}
            session.bridge.native_context=dict(self._world_exit_arrival)
        self.live_session=None
        log(map_id+' world exit -> home with current inventory')
        self._request_pvid_loads(channel)
        return True

    def _commit_live_room_transition(self, channel, session, link, player, inventory_create=None):
        """Enter a persistent room without changing the editor's selected map.

        This uses the normal LoadPVid/WorldCreated handshake, preserving the
        issued character/equipment and current HP/ammo. R still starts a new
        run through request_live_restart; doors never take that reset path.
        """
        map_id = getattr(self.options, 'map', None)
        if self._restart_in_progress or map_id not in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run'):
            return False
        run = getattr(session, 'run', None)
        expected = getattr(self, 'egy_run' if map_id == 'egy_run' else 'level_run', None)
        if run is None or run is not expected or not run.allows(link):
            return False
        if player is None or not player.alive or player.hp <= 0:
            return False
        if map_id == 'egy_run':
            from .maps.egy_run.session import world_blob
        else:
            world_blob = session.world_blob
        previous = (run.current, run.entry, run.transitions, run.player, len(run.events))
        self._restart_in_progress = True
        try:
            if not run.move(link, player):
                self._restart_in_progress = False
                return False
            blob = self._distinct_player_world_blob(world_blob(self, run))
            spawn = run.destination_spawn(run.current, run.entry)
            recipe = run.room().recipe
            width, height = recipe['metadata'].get('spawn_footprint_mp', (10240, 20480))
            position = (round(spawn[0] + (width-player.wmp)/2),
                        round(spawn[1] + height-player.hmp), player.wmp, player.hmp)

            message = self.layouts.decode('Delta', self.player_delta_blob)
            if inventory_create is not None:
                message['createplayer_']=inventory_create
            created = message['createplayer_']
            created['pos_'] = dict(zip(('xmp_', 'ymp_', 'wmp_', 'hmp_'), position))
            character = created['charprops_']
            character['hitpoints_'] = int(player.hp)
            if player.raw.get('ammo') is not None:
                character['ammo_'] = max(0, int(player.raw['ammo']))
            if inventory_create is None and player.raw.get('ec') is not None:
                created.setdefault('accountprops_', {})['ec_'] = max(0, int(player.raw['ec']))
            player_blob = self.layouts.encode('Delta', message)
        except Exception:
            run.current, run.entry, run.transitions, run.player, event_count = previous
            del run.events[event_count:]
            self._restart_in_progress = False
            raise
        session.suppress_send = True
        session.stop(stop_bridge=False)
        self.create_world_blob, self.player_delta_blob = blob, player_blob
        self.bootstrap_delta_blob = self._load_bootstrap_delta(self.options)
        self._restart_bridge = session.bridge
        self._restart_player_type = int(getattr(session, 'player_type', self.options.player_type))
        with session.bridge._lock:
            session.bridge.state = None
            session.bridge.events = []
            session.bridge.keys = {}
        if self.live_session is session:
            self.live_session = None
        log('{0} room transition: {1} -> {2}, entry {3}'.format(map_id, previous[0], run.current, run.entry))
        self._request_pvid_loads(channel)
        return True

    def request_live_map_switch(self, channel, session, map_id):
        """Reload the zone into another map pack (editor map switch).

        The CreateWorld and bootstrap blobs are built once in ``__init__``,
        so a map change rebuilds them from the new ``createworld.hex`` and
        then reuses the normal zone-reload path (LoadPVid -> PVID_LOADED ->
        CreateWorld).  Returns True when the switch was started.
        """
        if self._restart_in_progress:
            return False
        if not self._ground_ack_ready(session.bridge):return False
        map_id = str(map_id or '').strip()
        current = (getattr(self.options, 'map', None)
                   or game_settings.ORIGINAL_MAP)
        if not map_id or map_id == current:
            return False
        if map_id == game_settings.ORIGINAL_MAP:
            create_world = os.path.join(
                game_settings.LAB, 'data', 'catalogue',
                'real_hulkboss_createworld_flags.hex')
        else:
            map_dir = game_settings.map_dir(map_id)
            create_world = os.path.join(map_dir, 'createworld.hex')
            if map_id == 'lab_hard':
                from .maps.lab_hard.terrain import choose_variant
                create_world = choose_variant(map_dir)
        if not os.path.isfile(create_world):
            log('map switch to {0} ignored: missing {1}'.format(
                map_id, create_world))
            return False
        info = game_settings.map_info(map_id)
        self.options.map = map_id
        self.options.create_world_hex = create_world
        self.options.pvid = info.get('pvid') or self.options.pvid
        self.options.zone = info.get('zone') or self.options.zone
        self.options.gdv = info.get('gdv', 1)
        try:
            self.create_world_blob = self._load_create_world(self.options)
            self.bootstrap_delta_blob = self._load_bootstrap_delta(
                self.options)
        except Exception as error:
            log('map switch to {0} failed: {1}: {2}'.format(
                map_id, type(error).__name__, error))
            return False
        log('map switch: {0} -> {1} (pvid {2}, zone {3})'.format(
            current, map_id, self.options.pvid, self.options.zone))
        self.request_live_restart(channel, session,
                                  getattr(session, 'player_type', 1) or 1)
        return True

    def _new_ground_restore_host(self,egress,destination,checkpoint):
        from pathlib import Path
        from .maps.run_common.ground_restore_host import GroundRestoreHost,RestoreProofStore
        player,_=egress.source.bridge.snapshot()
        if player is None or player.raw.get('ground_restore_protocol')!='full-run-ground-restore-transport-v1':
            raise ValueError('native ground restore component unavailable; destination remains untouched')
        world=self.layouts.decode('CreateWorld',self._import_world_tool().unwrap_decompress(egress.world))
        room=dict(map=egress.source.map_id,room_token=egress.source_token+1,room_seed=world['worldprops_']['seed_'])
        directory=Path(getattr(self,'ground_restore_directory',
            Path(__file__).resolve().parents[1]/'data/catalogue/ground-restore-evidence'))
        host=GroundRestoreHost(self,egress.channel,egress.source.bridge,checkpoint,
            getattr(destination,'ground_checkpoint_boundary',None),room,egress.world,
            RestoreProofStore(directory/str(getattr(self,'session',0))))
        self._ground_restore_host=host
        return host

    def _start_live(self, channel):
        'Local battle simulator runtime.'
        if self.live_session is not None:
            return
        from .live.room import BossRoom
        from .live.session import LiveSession
        map_id = getattr(self.options, 'map', 'egy_hulk') or 'egy_hulk'
        room = None
        if map_id not in ('eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run'):
            create_world_hex = self.options.create_world_hex
            if not create_world_hex:
                raise protocol.ProtocolError(
                    'live mode needs --create-world-hex')
            templates = getattr(self.options, 'boss_templates', None)
            room = BossRoom.from_capture(
                create_world_hex, templates_path=templates,
                source=create_world_hex)
        bridge = self._restart_bridge
        self._restart_bridge = None
        if bridge is not None:
            bridge.native_context = None
        session_class = _map_session_class(map_id)
        session = session_class(
            self, channel, self.options, room, log=log, bridge=bridge)
        if bridge is not None:

            session.owns_bridge = True
        requested_type = self._restart_player_type
        if requested_type is not None:
            session.player_type = int(requested_type)
            self.options.player_type = int(requested_type)
        self.live_session = session
        self._restart_in_progress = False
        self._restart_player_type = None
        session.start()
        self._ensure_map_watch(channel)
        log('live Boss session online (bridge port {0}{1})'.format(
            getattr(self.options, 'bridge_port', 21550),
            '' if requested_type is None else
            ', character type {0}'.format(requested_type)))

    def _console(self, channel):
        """Read commands from stdin so a session can be driven by hand.

        Commands:
            send <type> <MessageName> <json>
            rawsend <type> <hex>
            decode <MessageName> <hex>
        """
        log('interactive console ready')
        while True:
            line = sys.stdin.readline()
            if not line:
                return
            line = line.strip()
            if not line:
                continue
            parts = line.split(None, 3)
            verb = parts[0]
            try:
                if verb == 'send' and len(parts) >= 3:
                    message_type = int(parts[1], 0)
                    name = parts[2]
                    payload = (
                        json.loads(parts[3]) if len(parts) > 3 else {})
                    self._send(channel, message_type, name, payload)
                elif verb == 'rawsend' and len(parts) >= 3:
                    message_type = int(parts[1], 0)
                    blob = base64.b16decode(parts[2].encode('ascii'), True)
                    channel.send_frame(message_type, blob)
                    log('-> type={0} length={1} (raw)'.format(
                        message_type, len(blob)))
                    self._record('server', message_type, blob)
                elif verb == 'decode' and len(parts) >= 3:
                    blob = base64.b16decode(parts[2].encode('ascii'), True)
                    log('   ' + json.dumps(
                        self.layouts.decode(parts[1], blob), sort_keys=True))
                elif verb == 'quit':
                    log('console quit requested')
                    os._exit(0)
                else:
                    log('console: unknown command: ' + line)
            except Exception as error:
                log('console error: {0}: {1}'.format(
                    type(error).__name__, error))

    def _request_pvid_loads(self, channel):
        """Load dependencies before the primary pack/room, awaiting each ACK.

        SC Boss sprites live in scboss, while its room tiles live in sc.
        Existing maps have no dependencies and keep their one-pack flow.
        """
        info=game_settings.map_info(getattr(self.options,'map',game_settings.ORIGINAL_MAP))
        self._pending_pvids=list(info.get('preload_pvids') or [])+[self.options.pvid]
        self._send(channel, MT_LOAD_PVID, 'LoadPVid', {'pvid_':self._pending_pvids.pop(0)})

    def _read_loop(self, channel):
        deadline = None
        if self.options.duration > 0:
            deadline = time.time() + self.options.duration
        while True:
            if deadline is not None and time.time() >= deadline:
                log('log duration reached')
                return
            try:
                message_type, payload = channel.read_frame()
            except socket.timeout:
                continue
            log('<- type={0} length={1}'.format(message_type, len(payload)))
            self._record('client', message_type, payload)
            ground=getattr(self,'_ground_egress_host',None)
            if ground is not None:
                if message_type==MT_PVID_LOADED:
                    ground.send_world();continue
                if message_type==MT_WORLD_CREATED:
                    ground.world_created()
                    if ground.wait_ready():
                        self.pending_player_delta=False
                        restore=getattr(ground,'restore',None)
                        if restore is None:
                            self._send_raw(channel,MT_SERVER_DELTA,self.player_delta_blob,'final ground create player')
                            ground.player_sent()
                            if self.options.live:self._start_live(channel)
                        else:
                            restore.source_commit(self.player_delta_blob)
                            left=max(0.,restore.prepare['expires_at']-time.time())
                            if not restore.prepared.wait(left):
                                restore.fail('native pre-CP pause prepared receipt timeout');continue
                            restore.send_player();ground.player_sent()
                            left=max(0.,restore.prepare['expires_at']-time.time())
                            if not restore.ready.wait(left):
                                restore.fail('native restore/durable receipt timeout');continue
                            self._ground_restore_host=None
                            if self.options.live:self._start_live(channel)
                    continue
                if self.pending_player_delta and message_type in (MT_DELTA_ACK,MT_TIME_PASSES):
                    continue
            if message_type == MT_PVID_LOADED and getattr(self, '_pending_pvids', None):
                self._send(channel, MT_LOAD_PVID, 'LoadPVid',
                           {'pvid_': self._pending_pvids.pop(0)})
                continue
            if (message_type == MT_PVID_LOADED
                    and self.options.mode == 'hulk'
                    and self.create_world_blob):
                channel.send_frame(MT_CREATE_WORLD, self.create_world_blob)
                log('-> type={0} length={1} (create world)'.format(
                    MT_CREATE_WORLD, len(self.create_world_blob)))
                self._record('server', MT_CREATE_WORLD,
                             self.create_world_blob)
                if self.bootstrap_delta_blob:
                    channel.send_frame(MT_SERVER_DELTA,
                                       self.bootstrap_delta_blob)
                    log('-> type={0} length={1} (bootstrap delta)'.format(
                        MT_SERVER_DELTA, len(self.bootstrap_delta_blob)))
                    self._record('server', MT_SERVER_DELTA,
                                 self.bootstrap_delta_blob)
                if self.player_delta_blob:

                    self.pending_player_delta = True
            elif (message_type in (MT_WORLD_CREATED, MT_DELTA_ACK,
                                   MT_TIME_PASSES)
                    and self.pending_player_delta):
                self.pending_player_delta = False
                self._send_raw(channel, MT_SERVER_DELTA,
                               self.player_delta_blob, 'create player')
                if self.options.live:
                    self._start_live(channel)
                else:
                    self._start_world_tick(channel)
                    self._start_live(channel)
            elif message_type == MT_TIME_PASSES and self.options.time_echo:

                dt = 16
                try:
                    dt = int(self.layouts.decode(
                        'TimePasses', payload).get('dt_', dt))
                except Exception:
                    pass
                dt = max(10, min(int(dt or 16), 40))
                self._send(channel, MT_TIME_PASSES, 'TimePasses',
                           {'dt_': dt})
            self._log_decoded(message_type, payload)

    def _log_decoded(self, message_type, payload):
        names = {
            MT_CONNECT: 'Connect',
            MT_JOINED: 'Joined',
            14: 'PlayerInput',
            15: 'TimePasses',
            18: 'ModifyInv',
            20: 'Chat',
        }
        name = names.get(message_type)
        if not name:
            return
        try:
            decoded = self.layouts.decode(name, payload)
            log('   {0}: {1}'.format(
                name, json.dumps(decoded, sort_keys=True)[:2000]))
        except Exception as error:
            log('   {0} decode failed: {1}'.format(name, error))

    def _send(self, channel, message_type, type_name, value):
        payload = (
            self.layouts.encode(type_name, value) if type_name else value)
        channel.send_frame(message_type, payload)
        log('-> type={0} length={1}'.format(message_type, len(payload)))
        self._record('server', message_type, payload)

    def _record(self, direction, message_type, payload):
        if not self.options.log_file:
            return
        record = {
            'direction': direction,
            'messageType': int(message_type),
            'length': len(payload),
            'payloadBase64': base64.b64encode(payload).decode('ascii'),
            'receivedAt': time.time(),
        }
        with open(self.options.log_file, 'a') as handle:
            handle.write(json.dumps(record, sort_keys=True) + '\n')

def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=2048)
    parser.add_argument('--mode',
                        choices=('hulk',),
                        default='hulk')
    parser.add_argument('--map', default='egy_hulk',
                        choices=game_settings.available_maps(),
                        help='live map pack (default: egy_hulk)')
    parser.add_argument('--pvid', default='hulk',
                        help='texture/sound prefix sent in MT_LOAD_PVID')
    parser.add_argument('--zone', default=None,
                        help='zone string written into CreateWorld '
                             '(defaults to --pvid)')
    parser.add_argument('--create-world-hex',
                        help='file with a hex-encoded compressed CreateWorld')
    parser.add_argument('--terrain-variant',default=None,
                        help='pin a lab_hard captured layout on room restart; default rolls a new layout')
    parser.add_argument('--objects',
                        help='JSON list of {objid, props} world objects')
    parser.add_argument('--no-player', action='store_true',
                        help='do not synthesize a local CreatePlayer delta')
    parser.add_argument('--no-bootstrap', action='store_true',
                        help='do not queue a delta right after CreateWorld')
    parser.add_argument('--bootstrap-creates', action='store_true',
                        help='replay every object as a GeneralUpdate create '
                             'instead of only creating the sub-world')
    parser.add_argument('--decoration', dest='decoration',
                        action='store_true', default=True,
                        help='build the zone DecorationDescription the client '
                             'needs before it will render any terrain')
    parser.add_argument('--no-decoration', dest='decoration',
                        action='store_false')
    parser.add_argument('--decoration-z', type=int, default=60,
                        help='tile layer for the stand-in decoration '
                             '(60 = behind sprites, 70 = in front)')
    parser.add_argument('--terrain-flags', dest='terrain_flags',
                        action='store_true', default=True,
                        help='add draw_/isinternal_ to exported terrain so the '
                             'client rasterises walls into its tile map')
    parser.add_argument('--no-terrain-flags', dest='terrain_flags',
                        action='store_false')
    parser.add_argument('--gdv', type=int, default=0,
                        help='GDV_NORMAL(0)/GDV_HARD(1) tile variation')
    parser.add_argument('--worldid', type=int, default=1,
                        help='world id used by CreateWorld, the bootstrap '
                             'delta and the per-frame world update '
                             '(the retail forest payload uses 2)')
    parser.add_argument('--seed', type=int, default=0,
                        help='world seed forwarded in WorldProperties')
    parser.add_argument('--player-type', type=int,
                        default=player_messages.CHARACTER_WILDFIRE,
                        help='CharacterType value for the local test player')
    parser.add_argument('--player-level', type=int, default=0,
                        help='character level index used to pick description '
                             'stats (0 = base level)')
    parser.add_argument('--loadout', help='local character combat properties JSON')
    parser.add_argument('--experimental-class-switch', action='store_true',
                        help='allow switching to a class that has no '
                             'recorded CreatePlayer payload (falls back to '
                             'the description/loadout builder)')
    parser.add_argument('--character-descriptions',
                        default=DEFAULT_CHARACTER_DESCRIPTIONS,
                        help='raw retail CharacterDescription payloads '
                             '(JSONL payloadBase64)')
    parser.add_argument('--class-loadouts', default=DEFAULT_CLASS_LOADOUTS,
                        help='per-character-type loadout JSON '
                             '(R restart / 7-8-9-0 class switch)')
    parser.add_argument('--player-payloads', default=DEFAULT_PLAYER_PAYLOADS,
                        help='anonymous native character templates by class')
    parser.add_argument('--no-player-payloads', action='store_true',
                        help='build characters from descriptions and loadouts only')
    parser.add_argument('--characters', default=DEFAULT_CHARACTERS,
                        help='JSON catalog of recorded CharacterDescription '
                             'messages (tools/'
                             'collect_character_descriptions.py output)')
    parser.add_argument('--projectile-overrides', help='local projectile speed settings file')
    parser.add_argument('--items',
                        help='JSONL of raw DescribeItem payloads captured at '
                             'login (tools/build_boss_scene.py --items-out)')
    parser.add_argument('--describe-types', type=int, nargs='*', default=None,
                        help='only send DescribeChar for these types '
                             '(default: every type in the catalog)')
    parser.add_argument('--player-x', type=int, default=None)
    parser.add_argument('--player-y', type=int, default=None)
    parser.add_argument('--player-wmp', type=int, default=10240)
    parser.add_argument('--player-hmp', type=int, default=17920)
    parser.add_argument('--live', action='store_true',
                        help='run the live Boss controller instead of '
                             'replaying --scene (real logic, real physics)')
    parser.add_argument('--bridge-port', type=int, default=21550,
                        help='UDP port for the in-client telemetry bridge')
    parser.add_argument('--boss-templates',
                        default=os.path.join(
                            os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__))),
                            'data', 'catalogue',
                            'boss_templates.json'),
                        help='recorded spawn templates for the live Boss '
                             'controller')
    parser.add_argument('--death-sequence',
                        default=os.path.join(
                            os.path.dirname(os.path.dirname(
                                os.path.abspath(__file__))),
                            'data', 'mechanics',
                            'death-presentation.json'),
                        help='captured deterministic death cutscene deltas')
    parser.add_argument('--live-seed', type=int, default=None,
                        help='fixed combat/loot seed for repeatable debugging; '
                             'normal practice chooses a new seed each session')
    parser.add_argument('--live-auto-interact', action='store_true',
                        help='activate lit panels by standing on them '
                             '(debug mode without the bridge plugin)')
    parser.add_argument('--live-intro-count', type=int, default=0,
                        help='force the opening big-robot count (1-4); '
                             '0 rolls it like the live fight')
    parser.add_argument('--live-intro-vids', default='',
                        help='comma-separated opening big-robot types; '
                             'empty rolls them like the live fight')
    parser.add_argument('--live-force-demo', action='store_true',
                        help='keep the autoplay damage timer running while a '
                             'client is attached (watch-only verification)')
    parser.add_argument('--live-demo-damage', action='store_true',
                        help='automatically damage armed gens so a headless '
                             'demo run can reach the death sequence')
    parser.add_argument('--speed', type=float, default=1.0)
    parser.add_argument('--duration', type=float, default=0.0)
    parser.add_argument('--data-version', default=DEFAULT_DATA_VERSION)
    parser.add_argument('--log-dir', default=None)
    parser.add_argument('--interactive', action='store_true',
                        help='read send/decode commands from stdin')
    parser.add_argument('--time-echo', dest='time_echo', action='store_true',
                        default=True,
                        help='answer MT_TIME_PASSES with a matching '
                             'TimePasses so the client clock advances')
    parser.add_argument('--no-time-echo', dest='time_echo',
                        action='store_false')
    parser.add_argument('--tick-hz', type=int, default=60,
                        help='rate of the synthetic WorldUpdate delta '
                             'stream (0 disables it)')
    return parser.parse_args(argv)

def main(argv=None):
    global LOG
    options = parse_args(argv)
    if options.log_dir:
        os.makedirs(options.log_dir, exist_ok=True)
        options.log_file = os.path.join(
            options.log_dir, 'local-session.jsonl')
        LOG = open(os.path.join(options.log_dir, 'local-server.log'), 'a')
    else:
        options.log_file = None
    try:
        LocalServer(options).serve_forever()
    except KeyboardInterrupt:
        log('stopped')

if __name__ == '__main__':
    main()
