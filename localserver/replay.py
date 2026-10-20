"""Deterministic encounter input journal and verified server continuation.

The journal contains whitelisted gameplay observations, not Python objects,
memory dumps, credentials, paths or executable snapshots. Rebuilding a controller
replays its inputs and checks every outgoing packet before it can be continued.
Client input/physics playback is a separate layer; this alone is not a video.
"""
import copy
import gzip
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace

from . import game_settings, paths, pb_layout
from .live.bridge import PlayerState

SCHEMA = 'starbreak-server-journal-v1'
SUPPORTED_MAPS = ('egy_hulk', 'hulk_normal', 'lab_hard', 'lab_mid',
    'cave_boss', 'city_boss', 'city_mid', 'dumb_boss', 'dumb_mid',
    'dumb_normal_wave', 'dumb_wave', 'forest_boss', 'forest_mid', 'hulk_mid',
    'ice_boss', 'ice_mid', 'jungle_boss', 'jungle_mid', 'sc_bonesaw',
    'sc_boss', 'sc_boss_elite', 'sc_hive', 'sc_patriarch')
FULL_RUN_MAPS = ('egy_run', 'eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run')
CLIENT_MAPS = SUPPORTED_MAPS + FULL_RUN_MAPS
MAX_CALLS = 64
MAX_FRAMES = 60 * 60 * 2 * 60
MAX_JOURNAL_BYTES = 128 * 1024 * 1024
PLAYER_INTS = ('objid', 'worldid', 'xmp', 'ymp', 'wmp', 'hmp', 'hp', 'maxhp',
               'facing', 'ammo', 'ec', 'input', 'room_seed', 'player_type', 'life_id')
PLAYER_BOOLS = ('alive', 'spectator', 'loaded', 'interaction_blocked')
KEYS = ('interact', 'interact_edge', 'attack', 'interaction_blocked')
EVENT_INTS = ('victim', 'objid', 'ownerid', 'value', 'damage', 'native_seed', 'type', 'command_id', 'source_oid')
EVENT_KINDS = ('damage', 'destroy', 'restart', 'switch_character', 'player_teleport_result')
METHODS = ('snapshot', 'peek_events', 'poll_events', 'consume_interactions', 'connected')
OPTIONS = ('live_auto_interact', 'live_demo_damage', 'live_force_demo',
           'live_demo_wait', 'live_player_gap')


class ReplayMismatch(ValueError):
    pass


def json_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def integer(value, low=-(2**31), high=2**32-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid replay integer')
    return value


def finite(value, low=0., high=1e12):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid replay number')
    return value


def gameplay_settings(document):
    # game_settings.normalize preserves unknown keys, so allowlist BEFORE it.
    keys = ('map', 'drops', 'balance', 'missile_delay_ms', 'skip_intro')
    source = {k: document[k] for k in keys if k in document}
    known = set(game_settings.available_maps())
    for group in ('drops', 'balance', 'missile_delay_ms', 'skip_intro'):
        if isinstance(source.get(group), dict):
            source[group] = {k:v for k,v in source[group].items() if k in known}
    value = game_settings.normalize(source)
    return {k: value[k] for k in keys}


def player_document(player):
    if player is None:
        return None
    raw = player.raw
    result = {k: integer(getattr(player, k, raw.get(k))) for k in PLAYER_INTS
              if hasattr(player, k) or k in raw}
    result.update({k: raw[k] is True for k in PLAYER_BOOLS if k in raw})
    result['alive'] = bool(player.alive)
    result['age'] = max(0., min(86400., player.age()))
    entry_ready = getattr(player, '_replay_entry_ready', None)
    if entry_ready is not None:
        if type(entry_ready) is not bool:raise ValueError('Invalid replay room readiness')
        result['entry_ready'] = entry_ready
    if raw.get('inventory_protocol') == 'full-run-inventory-v1':
        result['inventory_protocol'] = 'full-run-inventory-v1'
        if isinstance(raw.get('inventory'), dict):
            result['inventory'] = inventory_document(raw['inventory'])
    for key in ('vid', 'action_type'):
        if key in raw:
            value = raw[key]
            if not isinstance(value, str) or len(value) > 100:
                raise ValueError('Invalid player action')
            result[key] = value
    animation = raw.get('animprops') or {}
    if animation:
        result['animprops'] = {k: integer(animation[k]) for k in ('animid', 'animid_') if k in animation}
    terrain = raw.get('terrainObjects', [])
    if not isinstance(terrain, list) or len(terrain) > 2048:
        raise ValueError('Invalid terrain observations')
    if terrain:
        result['terrainObjects'] = [dict(objid=integer(row['objid']),
            hp=integer(row.get('hp', 1)), present=row.get('present') is not False) for row in terrain]
    return result


def inventory_document(value):
    """Only the existing door handoff's gameplay fields, never account data."""
    from .maps.run_common.player_inventory import _message
    if value.get('schema') != 'full-run-inventory-v1':raise ValueError('Invalid replay inventory schema')
    scope=value.get('scope') or {}
    if scope.get('map') not in FULL_RUN_MAPS:raise ValueError('Invalid replay inventory map')
    clean=dict(schema='full-run-inventory-v1',sampled_at=finite(value['sampled_at'],0,1e12),
        scope=dict(map=scope['map']),quality={},items=[],slots=[])
    for key in ('room_token','room_seed','worldid','objid','charid','player_type'):
        clean['scope'][key]=integer(scope[key],0,2**32-1)
    for key in ('request_id','request_attempt','native_server_t','count','ec'):
        if key in value:clean[key]=integer(value[key],0,2**31-1)
    for key in ('ok','items_ok','ec_ok'):clean[key]=value.get(key) is True
    quality=value.get('quality') or {}
    clean['quality']=dict(stable=quality.get('stable') is True,
        slot_source='Item.slotPos' if quality.get('slot_source')=='Item.slotPos' else '',
        declared_count=integer(quality.get('declared_count',0),0,256),
        traversed_count=integer(quality.get('traversed_count',0),0,256),
        abi=dict(name=quality.get('abi',{}).get('name') if quality.get('abi',{}).get('name') in
            ('win32-gcc48-items-v1','web-1.14.6-items-v1') else ''),errors=[])
    rows=value.get('items');slots=value.get('slots')
    if not isinstance(rows,list) or not isinstance(slots,list) or len(rows)>256 or len(slots)>256:
        raise ValueError('Invalid replay inventory size')
    clean['items']=[_message('ItemProperties',row,pb_layout.default_layouts()) for row in rows]
    for row in slots:
        clean['slots'].append(dict(**{key:integer(row[key]) for key in
            ('map_key','native_slotpos','type')},
            serialized_slotpos=None if row.get('serialized_slotpos') is None else integer(row['serialized_slotpos']),
            owner_ok=row.get('owner_ok') is True))
    if len(json_bytes(clean))>60000:raise ValueError('Replay inventory is too large')
    return clean


def restore_player(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError('Invalid replay player')
    age = finite(value.get('age', 0), 0, 86400)
    player = PlayerState(**dict(value, received_at=time.time()-age))
    if 'entry_ready' in value:player._replay_entry_ready=value['entry_ready']
    # Validate every consumed value and remove unknown fields on import.
    clean = player_document(player)
    clean['age'] = age
    result=PlayerState(**dict(clean, received_at=time.time()-age))
    if 'entry_ready' in clean:result._replay_entry_ready=clean['entry_ready']
    return result


def events_document(events):
    if not isinstance(events, (tuple, list)) or len(events) > 4096:
        raise ValueError('Invalid replay event list')
    result = []
    for event in events:
        if event.get('kind') not in EVENT_KINDS:
            continue
        row = {k: integer(event[k]) for k in EVENT_INTS if k in event}
        row['kind'] = event['kind']
        for key in ('crit', 'owner_explicit', 'applied'):
            if key in event: row[key] = event[key] is True
        reason = event.get('reason')
        if reason is not None:
            if type(reason) is int: row['reason'] = integer(reason)
            elif reason in ('HITPOINTS', 'CANCEL', 'LIFETIME', 'DESTROYREASON_HITPOINTS'): row['reason'] = reason
            else: raise ValueError('Invalid destroy reason')
        result.append(row)
    return result


def call_value(method, value, reading=False):
    if method == 'datagrams': return integer(value, 0, 2**53-1)
    if method == 'connected':
        if type(value) is not bool: raise ValueError('Invalid connection state')
        return value
    if method == 'snapshot':
        player, keys = value
        if not isinstance(keys, dict): raise ValueError('Invalid replay keys')
        return [restore_player(player) if reading else player_document(player),
                {k: keys[k] is True for k in KEYS if k in keys}]
    if method == 'consume_interactions':
        if len(value) > 64: raise ValueError('Too many interaction edges')
        return [(restore_player if reading else player_document)(p) for p in value]
    if method in ('peek_events', 'poll_events'): return events_document(value)
    raise ValueError('Unknown replay observation')


def call_arguments(name, args, kwargs=None):
    """Canonicalize the bridge's optional bounded polling/age argument."""
    kwargs = kwargs or {}
    args = list(args)
    parameter = 'limit' if name in ('poll_events', 'peek_events') else 'max_age' if name == 'connected' else None
    if kwargs:
        if args or set(kwargs) != {parameter}:raise ValueError('Invalid replay observation arguments')
        args = [kwargs[parameter]]
    if args:
        if len(args) != 1 or parameter is None:raise ValueError('Invalid replay observation arguments')
        args = [integer(args[0], 0, 4096) if parameter == 'limit' else finite(args[0], 0, 86400)]
    return args


def engine_fingerprint():
    """AI/data identity, including uncommitted changes during previews.

    Recording/index changes do not change an encounter. Its schema and packet
    verification handle recorder compatibility separately from AI compatibility.
    """
    digest = hashlib.sha256()
    for base, pattern in (('localserver', '*.py'), ('maps', '*'), ('data/catalogue', '*')):
        for path in sorted((paths.ROOT/base).rglob(pattern)):
            if not path.is_file() or '__pycache__' in path.parts: continue
            if path.suffix not in ('.py','.json','.jsonl','.hex'):continue
            if path in (paths.ROOT/'localserver/replay_identity.py', paths.ROOT/'localserver/battle_records.py', paths.ROOT/'localserver/battle_metrics.py', paths.ROOT/'localserver/replay.py', paths.ROOT/'localserver/run_replay.py'):
                continue
            relative = path.relative_to(paths.ROOT).as_posix().encode()
            # These are text runtime inputs. Git packaging and Windows writes
            # may use different line endings without changing their meaning.
            # Runtime logs/backups are not encounter inputs.
            content=path.read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n')
            digest.update(relative+b'\0'+hashlib.sha256(content).digest())
    return digest.hexdigest()


def compatible_engine(recorded, map_id=None):
    current=engine_fingerprint()
    if recorded==current:return True
    # Exact, audited recorder-only migrations; a future mechanics edit changes
    # the current digest and automatically stops accepting these aliases.
    try:
        aliases=json.loads((paths.ROOT/'data/mechanics/replay_compatibility.json').read_text())
        if recorded in aliases.get(current,[]):return True
        # A correction to one encounter need not invalidate unrelated maps.
        # These identities are still exact and continuation verifies packets.
        if map_id is not None:
            scoped=json.loads((paths.ROOT/'data/mechanics/replay_map_compatibility.json').read_text())
            return recorded in scoped.get(current,{}).get(map_id,[])
        return False
    except (OSError,ValueError,TypeError):return False


class ObservedBridge:
    def __init__(self, source, capture):
        self.source, self.capture = source, capture

    def __setattr__(self, name, value):
        if name in ('source', 'capture'):object.__setattr__(self,name,value)
        else:setattr(self.source,name,value)

    def __getattr__(self, name):
        value = getattr(self.source, name)
        if name == 'datagrams':
            self.capture.observe(name, [], value)
        if name not in METHODS: return value
        def call(*args, **kwargs):
            result = value(*args, **kwargs)
            try:self.capture.observe(name, call_arguments(name, args, kwargs), result)
            except ValueError:self.capture.failed = 'Unsupported gameplay observation arguments'
            return result
        return call


class RecordedBridge:
    shared_room = False

    def __init__(self):
        self.calls, self.index = [], 0

    def begin(self, calls):
        if not isinstance(calls, list) or len(calls) > MAX_CALLS:
            raise ValueError('Invalid replay observations')
        self.calls, self.index = calls, 0

    def end(self):
        if self.index != len(self.calls):
            raise ReplayMismatch('Replay consumed fewer observations than recorded')

    def __getattr__(self, name):
        if name == 'datagrams': return self.read(name, [])
        if name not in METHODS: raise AttributeError(name)
        return lambda *args, **kwargs: self.read(name, call_arguments(name, args, kwargs))

    def read(self, name, args):
        if self.index >= len(self.calls):
            raise ReplayMismatch('Replay requested an extra observation: '+name)
        row = self.calls[self.index]
        if row.get('method') != name or row.get('args') != args:
            raise ReplayMismatch('Replay observation order changed: '+name)
        self.index += 1
        return call_value(name, row['value'], reading=True)


class ObservedServer:
    def __init__(self, source, capture):
        self.source, self.capture = source, capture

    def __getattr__(self, name): return getattr(self.source, name)

    def _send_raw(self, channel, kind, payload, *args, **kwargs):
        result = self.source._send_raw(channel, kind, payload, *args, **kwargs)
        self.capture.packet(kind, payload)
        return result


def packet_digest(digest, kind, payload):
    digest.update(struct.pack('>II', kind, len(payload)))
    digest.update(payload)


def refresh_settings(session, previous, current):
    if previous != current:
        session._balance_mtime = object()
        session._supply_settings_mtime = object()
        if getattr(session, 'supplies', None) is not None:
            session.supplies._cache_key = None


class ServerCapture:
    """Capture at the actual observation points, including queued input edges."""
    def __init__(self, session, sink=None, keep_frames=True):
        map_id = getattr(session, 'map_id', session.options.map)
        if map_id not in SUPPORTED_MAPS or getattr(session.bridge, 'shared_room', False):
            raise ValueError('Encounter is not supported by this journal')
        self.session, self.sink = session, sink
        self.keep_frames, self.frame_count = keep_frames, 0
        self.started = time.monotonic()
        self.active_thread = None
        self.prepare, self.step = session.prepare, session.step
        self.frames, self.active, self.failed = [], None, None
        self.current_settings = gameplay_settings(game_settings.load())
        self.header = dict(schema=SCHEMA, engine=engine_fingerprint(), map=map_id,
            seed=str(session.seed), worldid=session.worldid, shell=session.options.player_type,
            world_sequence=int(getattr(session.server, '_live_world_sequence', 1)),
            settings=self.current_settings,
            options={k: getattr(session.options, k) for k in OPTIONS if hasattr(session.options, k)},
            turrets=copy.deepcopy(getattr(session, 'turret_counts', None)), missiles=copy.deepcopy(getattr(session, 'turret_missiles', None)),
            item_types=sorted(getattr(session.server, 'item_types', []) or []))
        room_seed = getattr(session.options, 'room_roll_seed', None)
        if room_seed is not None:
            self.header['room_seed'] = str(integer(int(room_seed), 0, 2**64-1))
        attempt = os.environ.get('SB_REPLAY_ATTEMPT')
        if attempt:
            import uuid
            self.header['attempt'] = str(uuid.UUID(attempt))
        self.source_bridge, self.source_server = session.bridge, session.server
        session.bridge = ObservedBridge(session.bridge, self)
        session.server = ObservedServer(session.server, self)

    def observe(self, name, args, value):
        if self.active is None or self.failed or threading.get_ident() != self.active_thread: return
        try:
            if len(self.active['calls']) >= MAX_CALLS: raise ValueError('Replay observation budget exceeded')
            self.active['calls'].append(dict(method=name, args=args, value=call_value(name, value)))
        except (ValueError, TypeError, KeyError):
            # Observing must never interrupt the live simulation.
            self.failed = 'Unsupported gameplay observation'

    def packet(self, kind, payload):
        if self.active is not None and threading.get_ident() == self.active_thread:
            packet_digest(self.digest, kind, payload)
            self.active['packets'] += 1

    def run(self, action, dt=0):
        if action not in ('prepare', 'step'): raise ValueError('Unknown replay action')
        if self.active is not None: raise ValueError('Nested replay frame')
        finite(dt, 0, 1)
        if self.frame_count >= MAX_FRAMES: self.failed = 'Replay duration limit exceeded'
        if self.failed:
            self._restore_clock(self.session)
            return self.prepare() if action == 'prepare' else self.step(dt)
        settings = gameplay_settings(game_settings.load())
        self.active = dict(action=action, dt=dt, wall=time.monotonic()-self.started, calls=[], packets=0)
        self.active_thread = threading.get_ident()
        self.session._replay_wall_time = self.active['wall']
        if settings != self.current_settings: self.active['settings'] = settings
        refresh_settings(self.session, self.current_settings, settings)
        self.current_settings = settings
        self.digest = hashlib.sha256()
        try:
            with game_settings.event_snapshot(settings):
                result = self.prepare() if action == 'prepare' else self.step(dt)
            self.active['sha256'] = self.digest.hexdigest()
            self.frame_count += 1
            if self.keep_frames: self.frames.append(self.active)
            if self.sink and not self.failed:
                try: self.sink(self.active)
                except (OSError, ValueError): self.failed = 'Replay journal could not be saved'
            return result
        except Exception:
            self.failed = 'Simulation did not complete this replay frame'
            raise
        finally:
            self.active = None
            self.active_thread = None

    def document(self):
        return dict(self.header, frames=copy.deepcopy(self.frames))

    def detach(self, restore_methods=True):
        self.session.bridge, self.session.server = self.source_bridge, self.source_server
        if restore_methods:
            self.session.prepare, self.session.step = self.prepare, self.step
        self._restore_clock(self.session)

    @staticmethod
    def _restore_clock(session):
        wall = getattr(session, '_replay_wall_time', None)
        if wall is None: return
        since = getattr(session, '_player_missing_since', None)
        if since is not None: session._player_missing_since = time.time()-(wall-since)
        del session._replay_wall_time


class ServerReplay:
    """Rebuild and verify, leaving the real controller alive for continuation."""
    def __init__(self, document, wire=None):
        self.wire=wire
        if document.get('schema') != SCHEMA or not compatible_engine(document.get('engine'),document.get('map')):
            raise ValueError('Replay requires the same simulator engine and map data')
        map_id = document.get('map')
        if map_id not in SUPPORTED_MAPS: raise ValueError('Unsupported replay map')
        frames = document.get('frames')
        if not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
            raise ValueError('Invalid replay frame count')
        if frames[0].get('action') != 'prepare' or any(f.get('action') != 'step' for f in frames[1:]):
            raise ValueError('Invalid replay lifecycle')
        from .server import parse_args, _map_session_class
        from .live.room import BossRoom
        source = paths.ROOT/('data/catalogue/real_hulkboss_createworld_flags.hex' if map_id == 'egy_hulk' else 'maps/'+map_id+'/createworld.hex')
        options = parse_args(['--map', map_id, '--create-world-hex', str(source)])
        options.live_seed = integer(int(document['seed']), 0, 2**64-1)
        if 'room_seed' in document:
            options.room_roll_seed = integer(int(document['room_seed']), 0, 2**64-1)
        options.player_type = integer(document['shell'], 1, 5)
        for key, value in document.get('options', {}).items():
            if key not in OPTIONS: raise ValueError('Unknown replay option')
            if key in ('live_demo_wait', 'live_player_gap'): finite(value, 0, 3600)
            elif type(value) is not bool: raise ValueError('Invalid replay option')
            setattr(options, key, value)
        worldid = integer(document['worldid'], 1, 2**31-1)
        options.worldid = worldid
        self.bridge = RecordedBridge()
        self.server = SimpleNamespace(layouts=pb_layout.default_layouts(), _worldid=lambda: worldid,
            _send_raw=self.packet, class_loadouts={'_turret_counts': document['turrets'], '_turret_missiles': document['missiles']},
            item_types=set(integer(v, 1) for v in document['item_types']))
        self.settings = gameplay_settings(document['settings'])
        self.digest, self.packets = hashlib.sha256(), 0
        with game_settings.event_snapshot(self.settings):
            session_class = _map_session_class(map_id)
            kwargs = dict(bridge=self.bridge)
            if 'seed' in inspect.signature(session_class).parameters:
                kwargs['seed'] = options.live_seed
            self.session = session_class(self.server, None, options,
                BossRoom.from_capture(str(source), options.boss_templates), **kwargs)
        self.frames, self.index, self.verified, self.failed = frames, 0, False, False

    def packet(self, channel, kind, payload, *args, **kwargs):
        if self.wire is not None:payload=self.wire.reconcile(self.index,self.packets,kind,payload,self.session)
        packet_digest(self.digest, kind, payload)
        self.packets += 1

    def advance(self):
        if self.failed: raise ReplayMismatch('Replay has already diverged')
        if self.index >= len(self.frames): return False
        row = self.frames[self.index]
        finite(row.get('dt'), 0, 1)
        self.session._replay_wall_time = finite(row.get('wall'), 0, 7*86400)
        self.bridge.begin(row.get('calls'))
        settings = gameplay_settings(row.get('settings', self.settings))
        refresh_settings(self.session, self.settings, settings)
        self.settings = settings
        self.digest, self.packets = hashlib.sha256(), 0
        try:
            with game_settings.event_snapshot(settings):
                if self.index == 0: self.session.prepare()
                else: self.session.step(row['dt'])
            self.bridge.end()
            if self.packets != row['packets'] or self.digest.hexdigest() != row['sha256']:
                raise ReplayMismatch('Replay output diverged at frame '+str(self.index))
        except Exception:
            self.failed = True
            raise
        self.index += 1
        self.verified = True
        return True

    def continue_live(self, bridge, server, channel):
        if not self.verified or self.failed or self.index < 2:
            raise ReplayMismatch('A verified battle frame is required before continuation')
        # Keep this exact controller, RNG states, projectiles and attack timers.
        # Caller must separately restore/verify the client's player state first.
        self.session.bridge, self.session.server, self.session.channel = bridge, server, channel
        ServerCapture._restore_clock(self.session)
        return self.session


class JournalWriter:
    """Bounded, compressed append-only journal. An incomplete file cannot play."""
    def __init__(self, path, header):
        self.path = Path(path)
        self.handle = gzip.open(self.path, 'xb', compresslevel=3)
        self.size, self.count = 0, 0
        self.digest = hashlib.sha256()
        self.write(dict(header=header))

    def write(self, value):
        data = json_bytes(value)+b'\n'
        if len(data) > 512*1024 or self.size+len(data) > MAX_JOURNAL_BYTES:
            raise ValueError('Replay size limit exceeded')
        self.handle.write(data)
        self.digest.update(data)
        self.size += len(data)

    def frame(self, value):
        self.write(dict(frame=value))
        self.count += 1
        if self.count % 60 == 0: self.handle.flush()

    def close(self, complete=True):
        if self.handle.closed: return
        try:
            if complete:
                self.handle.write(json_bytes(dict(end=dict(frames=self.count, sha256=self.digest.hexdigest())))+b'\n')
        finally: self.handle.close()


def read_journal(path):
    digest, size, header, frames, end = hashlib.sha256(), 0, None, [], None
    with gzip.open(path, 'rb') as handle:
        while True:
            line = handle.readline(512*1024+1)
            if not line: break
            size += len(line)
            if end is not None or size > MAX_JOURNAL_BYTES or len(line) > 512*1024:
                raise ValueError('Invalid or oversized replay journal')
            row = json.loads(line)
            if 'end' in row:
                end = row['end']
                continue
            digest.update(line)
            if header is None and set(row) == {'header'}: header = row['header']
            elif header is not None and set(row) == {'frame'} and len(frames) < MAX_FRAMES: frames.append(row['frame'])
            else: raise ValueError('Invalid replay journal sequence')
    if end != dict(frames=len(frames), sha256=digest.hexdigest()) or header is None:
        raise ValueError('Replay journal is incomplete or damaged')
    return dict(header, frames=frames)
