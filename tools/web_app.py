"""Run the local browser battle prototype without a native game or SBPE.

Usage: python3 tools/web_app.py --client /path/to/VR1574197275.mvmmoclient.js
Requires websockets; serves only the prototype UI and whitelisted game assets.
"""
import argparse
import contextlib
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.client import HTTPException
import json
import hashlib
import mimetypes
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from urllib.parse import unquote, urlsplit, parse_qs
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localserver import game_settings, paths, pb_layout
from tools.web_client import CLIENT_FILENAME, CLIENT_SHA256, ensure_cached_client, patch_client
from tools.client_replay import ClientJournals, MAX_CHUNK, SCHEMA as CLIENT_JOURNAL_SCHEMA
from tools.web_workspace import Workspace, atomic_json
from tools.web_text import map_names
from tools import update
from tools.game_preferences import GamePreferences
from localserver.battle_records import RecordStore, MAX_IMPORT

# These encounters need inventory/ground handoff adapters before being enabled.
PENDING_MAPS = {'eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run'}


class TakeoverJobs:
    """Keep long verification off HTTP requests, with bounded local jobs."""
    def __init__(self):
        self.lock, self.jobs = threading.Lock(), {}

    def begin(self, prepare, document):
        with self.lock:
            now = time.monotonic()
            for token, job in list(self.jobs.items()):
                if now - job['created'] > 600:
                    job['cancel'].set(); del self.jobs[token]
            if any(not job['done'].is_set() for job in self.jobs.values()):
                raise ValueError('A takeover is already being verified')
            if len(self.jobs) >= 8:
                del self.jobs[min(self.jobs, key=lambda token: self.jobs[token]['created'])]
            token = str(uuid.uuid4())
            job = dict(state='pending', current=0, total=0, created=now, cancel=threading.Event(), done=threading.Event())
            self.jobs[token] = job

        def progress(current, total):
            with self.lock:
                if job['cancel'].is_set():raise InterruptedError('Takeover cancelled')
                job.update(current=current, total=total)

        def worker():
            try:
                result = prepare(document, progress=progress)
                with self.lock:
                    if job['cancel'].is_set():
                        job.update(state='failed', error='Takeover cancelled');return
                    job.update(state='ready', result=result)
            except Exception as error:
                with self.lock:
                    job.update(state='failed', error=str(error)[:200] if isinstance(error,ValueError) else 'Takeover verification failed ('+type(error).__name__+')')
            finally:job['done'].set()

        threading.Thread(target=worker, daemon=True).start()
        return dict(job=token)

    def status(self, token):
        token = str(uuid.UUID(token))
        with self.lock:
            job = self.jobs.get(token)
            if job is None:raise ValueError('Unknown takeover verification')
            return {key: job[key] for key in ('state', 'current', 'total', 'result', 'error') if key in job}

    def cancel(self):
        with self.lock:
            for job in self.jobs.values():job['cancel'].set()


def maps():
    return [dict(id=map_id, title=game_settings.map_info(map_id)['title'], names=map_names(map_id, game_settings.map_info(map_id)['title']), ready=map_id not in PENDING_MAPS)
            for map_id in game_settings.ordered_maps()]


def checked_map(map_id):
    if map_id not in game_settings.available_maps() or map_id in PENDING_MAPS:
        raise ValueError('This map is not available in the browser yet')
    return map_id


class Prototype:
    def __init__(self, options):
        self.options = options
        self.client = patch_client(options.client.read_bytes())
        self.process = None
        self.log = None
        self.lock = threading.Lock()
        self.map = 'hulk_mid'
        self.shell = 1
        self.started = 0
        self.latest_player = {}
        self.latest_inventory = None
        self.inventory_error = None
        self.feedback = 0
        self.damage = 0
        self.received_damage = 0
        self.directory = getattr(options, 'data_dir', None) or ROOT / 'userdata' / 'web'
        self.directory.mkdir(parents=True, exist_ok=True)
        # Use this client's own embedded version manifest for required HELLO
        # fields and texture identifiers, rather than a missing native install.
        manifest = re.search(rb'var fileData0=\[\];fileData0.push.apply\(fileData0,(\[[0-9,]+\])\)',
                             options.client.read_bytes())
        if not manifest:
            raise ValueError('Client data-version manifest signature missing.')
        self.version_file = self.directory / 'dataVersion.bpb'
        self.version_file.write_bytes(bytes(json.loads(manifest.group(1))))
        self.cache = self.directory / 'assets'
        self.cache.mkdir(exist_ok=True)
        self.version = pb_layout.default_layouts().decode('DataVersion', self.version_file.read_bytes())
        self.workspace = Workspace(self.directory)
        if getattr(options, 'desktop', False):
            from tools.desktop_workspace import DesktopWorkspace
            self.workspace = DesktopWorkspace(self.directory, paths.USERDATA)
        self.records = RecordStore(getattr(options,'records_dir',None) or self.directory / 'battles')
        self.client_journals = ClientJournals(self.records.directory)
        self.takeovers, self.takeover_lock = {}, threading.Lock()
        self.takeover_jobs = TakeoverJobs()
        self.game_connections, self.connection_lock = {}, threading.Lock()
        self.recording_id = None
        self.recording_error = None
        self.game_preferences = GamePreferences(self.directory)
        self.update_checker = update.UpdateChecker(ROOT)
        self.prepared_map = 'hulk_mid'
        self.asset_paths = self.required_assets(self.prepared_map)
        self.preparing, self.asset_error = False, ''
        self.items = {}
        import base64
        for line in (ROOT / 'data/catalogue/item_descriptions.jsonl').read_text().splitlines():
            row = json.loads(line)
            item = pb_layout.default_layouts().decode('ItemDescription', base64.b64decode(row['payloadBase64']))
            self.items[int(item['type_'])] = item

    def required_assets(self, map_id):
        info = game_settings.map_info(map_id)
        needed = {'ui', 'title', 'base', game_settings.map_info('home')['pvid'], info['pvid'], *info.get('preload_pvids', [])}
        assets = []
        available = {pack['name_'] for pack in self.version.get('pvid_', [])}
        if not needed <= available:
            raise ValueError('Missing texture manifest for ' + ', '.join(sorted(needed-available)))
        for pack in self.version.get('pvid_', []):
            name = pack['name_']
            if name in needed:
                assets.append('/texture/' + name + '.' + pack['descid_'] + '.bpb')
                assets.extend('/texture/' + name + '.' + image + '.png' for image in pack['imageids_'])
        return assets

    def info(self):
        assets = getattr(self, 'asset_paths', [])
        cached = sum((self.cache / path.lstrip('/')).is_file() or
                     (self.options.client.parent / Path(path).name).is_file() for path in assets)
        continuing = any(host.committed and not host.stopped.is_set() for host in getattr(self, 'takeovers', {}).values())
        return dict(map=self.map, shell=self.shell, running=continuing or bool(self.process and self.process.poll() is None),
                    assets_ready=cached == len(assets), assets_cached=cached, assets_total=len(assets),
                    asset_error=getattr(self, 'asset_error', ''),
                    ws_port=self.options.ws_port, feedback=self.feedback, damage=self.damage,
                    received_damage=self.received_damage,
                    player=self.latest_player,
                    desktop=getattr(self.options, 'desktop', False),
                    maps=[dict(row, ready=True) for row in maps()] if getattr(self.options, 'desktop', False) else maps(),
                    items=[dict(id=k, name=v['name_'], slot=v.get('slot_'), usableby=v.get('usableby_', []))
                           for k, v in self.items.items() if v.get('slot_') == 256])

    def prepare(self, document=None):
        # Fetch before creating the game iframe: the original image loader has
        # a shorter timeout than some first-time official asset downloads.
        with self.lock:
            map_id = checked_map((document or {}).get('map', 'hulk_mid'))
            if self.preparing and map_id != self.prepared_map:
                raise ValueError('Wait for the current resource preparation to finish')
            self.prepared_map, self.asset_paths = map_id, self.required_assets(map_id)
            if not self.preparing:
                self.preparing, self.asset_error = True, ''
                def work():
                    try:
                        with ThreadPoolExecutor(max_workers=4) as pool:
                            list(pool.map(self.asset, self.asset_paths))
                    except Exception as error:
                        self.asset_error = 'Game asset download failed: ' + str(error)
                    finally:
                        self.preparing = False
                threading.Thread(target=work, daemon=True).start()
        return self.info()

    def battle_records(self):
        rows = self.records.list()
        identifiers = [row['replay']['id'] for row in rows if row.get('replay', {}).get('id')]
        adapter = hashlib.sha256(self.client+CLIENT_JOURNAL_SCHEMA.encode()).hexdigest()
        states = self.client_journals.statuses(identifiers, adapter, client=CLIENT_SHA256, allow_compatible=True)
        from tools.portable_replay import availability
        states.update(availability(self.records.directory,identifiers))
        for row in rows:
            identifier = row.get('replay', {}).get('id')
            if identifier in states:
                row['replay'] = states[identifier]
        native_ids=[row['replay']['id'] for row in rows if row.get('replay',{}).get('format',row.get('platform'))=='native' and row.get('replay',{}).get('id')]
        if native_ids and getattr(self.options,'desktop',False):
            try:
                from tools.native_playback import NativeControlClient
                control=NativeControlClient();native_states={}
                for offset in range(0,len(native_ids),500):native_states.update(control.call('/availability',dict(ids=native_ids[offset:offset+500])))
                for row in rows:
                    if native_states.get(row.get('replay',{}).get('id'),{}).get('available'):
                        row['replay']=dict(native_states[row['replay']['id']],engine='native')
            except (OSError,ValueError,KeyError):pass
        return rows

    def replay_document(self, identifier, require_exact=False):
        try:document = self.client_journals.read(identifier)
        except ValueError:
            from tools.portable_replay import browser_document
            return browser_document(self.records.directory,identifier,self.version,require_exact=require_exact)
        adapter = hashlib.sha256(self.client+CLIENT_JOURNAL_SCHEMA.encode()).hexdigest()
        if document['client'] != CLIENT_SHA256:
            raise ValueError('Replay requires a supported original game client')
        if require_exact and document['adapter'] != adapter:
            raise ValueError('Compatible playback can be tried, but takeover requires the matching replay adapter')
        return document

    def warm_takeover(self, document):
        from tools.replay_host import WarmTakeovers
        source=self.replay_document(document['id'],require_exact=True)
        clients=self.client_journals
        if source.get('schema')=='starbreak-native-journal-v1':
            from tools.native_replay import NativeJournals
            from tools.portable_replay import check_runner
            check_runner(document.get('runner'));clients=NativeJournals(self.records.directory)
        # Instantiate under the existing lock so concurrent HTTP requests cannot
        # allocate unbounded caches. Warm requests create no branch or record.
        with self.takeover_lock:
            if not getattr(self,'warm_takeovers',None):self.warm_takeovers=WarmTakeovers()
        return self.warm_takeovers.warm(self.records,clients,document)

    def prepare_takeover(self, document, progress=None):
        from tools.replay_host import WarmTakeovers
        source=self.replay_document(document['id'],require_exact=True)
        clients=self.client_journals
        if source.get('schema')=='starbreak-native-journal-v1':
            from tools.native_replay import NativeJournals
            from tools.portable_replay import check_runner
            check_runner(document.get('runner'));clients=NativeJournals(self.records.directory)
        # Verify compatibility before creating any branch files or records.
        if not getattr(self,'warm_takeovers',None):self.warm_takeovers=WarmTakeovers()
        preferences=self.game_preferences.read()
        with self.takeover_lock:
            for token, old in list(self.takeovers.items()):
                if old.stopped.is_set() or (not old.committed and time.monotonic()-old.created>60):
                    old.stop();self.takeovers.pop(token,None)
            if not document.get('record'):
                matches=[r['id'] for r in self.records.list() if r.get('replay',{}).get('id')==document['id']]
                if len(matches)!=1:raise ValueError('Select the battle to take over')
                document=dict(document,record=matches[0])
            host=self.warm_takeovers.take(self.records,clients,document,progress=progress)
            from tools.portable_replay import append_runner
            try:append_runner(host,document,clients,source)
            except Exception:host.stop();raise
            if progress is not None:
                try:progress(1, 1)
                except Exception:
                    host.stop();raise
            self.takeovers[host.token]=host
            return dict(host.description(),preferences=preferences)

    def stop(self):
        if getattr(self, 'takeover_jobs', None) is not None:self.takeover_jobs.cancel()
        # Cancel reconstruction first, then synchronize with its final host
        # publication so restarting a battle cannot leave an orphan live host.
        with getattr(self, 'takeover_lock', threading.Lock()):
            for host in list(getattr(self,'takeovers',{}).values()):host.stop()
            if getattr(self,'warm_takeovers',None):self.warm_takeovers.close()
        # Half-close owned game sockets first. The child seals its journal and
        # battle record before closing its side; only then terminate the idle
        # listener. This also works on Windows, where terminate skips finally.
        with getattr(self,'connection_lock',threading.Lock()):
            connections=list(getattr(self,'game_connections',{}).items())
        for target,_closed in connections:
            with contextlib.suppress(OSError):target.shutdown(socket.SHUT_WR)
        for _target,closed in connections:closed.wait(timeout=2)
        owner = self.process.pid if self.process else None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if owner is not None:
            try:
                self.records.finish_owner(owner)
            except Exception as error:
                # A recording failure must not prevent closing/restarting play.
                print('Battle record finalization failed: ' + type(error).__name__, flush=True)
        self.process = None
        if self.log:
            self.log.close()
            self.log = None

    def start(self, document):
        with self.lock:
            shell = int(document.get('shell', 1))
            map_id = checked_map(document.get('map', 'hulk_mid'))
            if shell not in (1, 2, 3, 5):
                raise ValueError('Unknown shell')
            weapon = document.get('weapon')
            if weapon is not None:
                weapon = int(weapon)
                item = self.items.get(weapon)
                if item is None or item.get('slot_') != 256 or shell not in item.get('usableby_', []):
                    raise ValueError('Weapon is not compatible with this shell.')
            self.stop()
            self.map, self.shell = map_id, shell
            self.begin_recording(map_id)
            self.workspace.materialize(map_id)
            loadout_path = self.directory / 'class_loadouts.json'
            loadouts = json.loads(loadout_path.read_text(encoding='utf8'))
            if weapon is not None:
                row = loadouts[str(shell)]
                current = next((v for v in row['items_'] if v.get('slotpos_') == 256), {})
                if current.get('type_') != weapon:
                    row['items_'] = [v for v in row['items_'] if v.get('slotpos_') != 256]
                    row['items_'].insert(0, dict(type_=weapon, slotpos_=256, statboosts_=[]))
            atomic_json(loadout_path, loadouts)
            self.log = (self.directory / 'server.log').open('w', encoding='utf8')
            info = game_settings.map_info(self.map)
            command = [sys.executable, '-u', '-m', 'localserver.server', '--web-client', '--mode', 'hulk', '--live',
                       '--map', self.map, '--pvid', info['pvid'], '--zone', info['zone'],
                       '--gdv', str(info.get('gdv', 1)), '--host', '127.0.0.1',
                       '--port', str(self.options.game_port), '--bridge-port', str(self.options.bridge_port),
                       '--player-type', str(shell), '--create-world-hex', str(
                           ROOT / 'data/catalogue/real_hulkboss_createworld_flags.hex' if self.map == game_settings.ORIGINAL_MAP
                           else ROOT / 'maps' / self.map / 'createworld.hex'),
                       '--characters', str(ROOT / 'data/catalogue/characters.json'),
                       '--items', str(ROOT / 'data/catalogue/item_descriptions.jsonl'),
                       '--data-version', str(self.version_file),
                       '--class-loadouts', str(loadout_path),
                       '--projectile-overrides', str(self.directory / 'class_loadout_overrides.json'),
                       '--player-payloads', str(ROOT / 'data/catalogue/class_players.json'), '--no-time-echo']
            environment = dict(os.environ, SB_GAME_SETTINGS_PATH=str(self.directory / 'game_settings.json'),
                               SB_BATTLE_RECORDS_PATH=str(self.records.directory),
                               SB_RECORD_REPLAY='1' if self.recording_id else '0',
                               SB_REPLAY_ATTEMPT=self.recording_id or '')
            self.process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=self.log, stderr=subprocess.STDOUT)
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError((self.directory / 'server.log').read_text(encoding='utf8')[-2000:])
                if 'listening' in (self.directory / 'server.log').read_text(encoding='utf8'):
                    self.latest_player, self.feedback, self.damage, self.received_damage = {}, 0, 0, 0
                    self.latest_inventory = self.inventory_error = None
                    self.started = time.time()
                    return self.info()
                time.sleep(.05)
            self.stop()
            raise RuntimeError('Local battle server startup timed out.')

    def begin_recording(self, map_id):
        from localserver.replay import CLIENT_MAPS
        self.recording_id = self.recording_error = None
        if os.environ.get('SB_RECORD_REPLAY', '1') != '1' or map_id not in CLIENT_MAPS:
            return
        identifier = str(uuid.uuid4())
        try:
            self.client_journals.begin(identifier, CLIENT_SHA256,
                hashlib.sha256(self.client+CLIENT_JOURNAL_SCHEMA.encode()).hexdigest(),
                self.game_preferences.read(), [1366, 768])
        except Exception as error:
            self.recording_error = type(error).__name__
            print('Battle replay unavailable: ' + self.recording_error, flush=True)
        else:
            self.recording_id = identifier

    def replay_takeover_ready(self, identifier, record=None, client_document=None):
        """Only offer continuation when matching server state is present."""
        import gzip
        from localserver.replay import engine_fingerprint, compatible_engine, SUPPORTED_MAPS, SCHEMA
        from localserver.run_replay import SCHEMA as RUN_SCHEMA
        from localserver.replay import FULL_RUN_MAPS
        if client_document is not None:
            if client_document['schema']=='starbreak-native-journal-v1':
                try:self.replay_document(identifier,require_exact=True)
                except (OSError,ValueError):return False
            elif client_document['adapter']!=hashlib.sha256(self.client+CLIENT_JOURNAL_SCHEMA.encode()).hexdigest():return False
        if record is None:
            matches=[r for r in self.records.list() if r.get('replay',{}).get('id')==identifier]
            if len(matches)!=1:return False
            record=matches[0]['id']
        try:
            document=self.records.get(str(uuid.UUID(record)))
            if document.get('replay',{}).get('id')!=identifier:return False
            path=self.records.directory/(document['id']+'.server.jsonl.gz')
            with gzip.open(path,'rb') as stream:
                row=json.loads(stream.readline(512*1024+1))
            header=row['header']
            supported=((header.get('schema')==SCHEMA and header.get('map') in SUPPORTED_MAPS) or
                       (header.get('schema')==RUN_SCHEMA and header.get('map') in FULL_RUN_MAPS))
            return (supported and
                    header.get('attempt')==identifier and compatible_engine(header.get('engine')))
        except (OSError,EOFError,ValueError,KeyError,TypeError):
            return False

    def asset(self, path):
        if path=='/client/data/sfx/soldierhit.ogg':path='/client/data/sfx/pc/wildfire/hit.ogg'
        # No arbitrary paths or URLs can be read through this handler.
        if any(part in ('.', '..') for part in path.split('/')) or not re.fullmatch(r'/(?:client/data|data|texture|sfx|music)/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.(?:bpb|png|ogg|mp3|ttf|woff)', path):
            raise FileNotFoundError(path)
        relative = path.lstrip('/')
        cached = self.cache / relative
        if cached.is_file():
            return cached.read_bytes()
        candidate = self.options.client.parent / Path(path).name
        # Flat, version-hashed textures are the only files read beside a client.
        # Do not expose arbitrary downloads which happen to have an audio suffix.
        if re.fullmatch(r'[A-Za-z0-9_-]+\.(?:d|[0-9]+)\.[A-Za-z0-9_-]{22}\.(?:png|bpb)', candidate.name) and candidate.is_file():
            return candidate.read_bytes()
        for attempt in range(3):
            try:
                with urlopen('https://www.starbreak.com' + path, timeout=60) as response:
                    data = response.read()
                break
            except HTTPError as error:
                if error.code < 500 or attempt == 2:
                    raise
            except (OSError, HTTPException):
                if attempt == 2:
                    raise
        cached.parent.mkdir(parents=True, exist_ok=True)
        # Readers must never see a partially written texture during a restart.
        with tempfile.NamedTemporaryFile(dir=cached.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        try:
            temporary.replace(cached)
        finally:
            temporary.unlink(missing_ok=True)
        return data

    def websocket(self, ws):
        path = urlsplit(ws.request.path).path
        if path == '/takeover':
            token=parse_qs(urlsplit(ws.request.path).query).get('token',[''])[0]
            host=self.takeovers.get(token)
            if host is None:
                ws.close(1008,'Unknown takeover');return
            try:
                host.bind(ws)
                for message in ws:
                    if isinstance(message,bytes):host.binary(message)
                    elif isinstance(message,str) and len(message)<60000:
                        document=json.loads(message)
                        if document.get('type')=='takeover-commit':host.commit()
                        else:
                            acknowledgement=host.telemetry(document)
                            self.observe_feedback(document)
                            ws.send(json.dumps(acknowledgement))
                    else:raise ValueError('Invalid takeover message')
            except Exception as error:
                print('Takeover connection ended: '+type(error).__name__,flush=True)
            finally:
                host.stop()
                self.takeovers.pop(token,None)
            return
        stop = threading.Event()
        closed = threading.Event()
        if path == '/game':
            try:
                target = socket.create_connection(('127.0.0.1', self.options.game_port), timeout=10)
            except OSError:
                ws.close(1013, 'Start or restart the encounter')
                return
            target.settimeout(.5)
            with self.connection_lock:self.game_connections[target]=closed
        elif path == '/telemetry':
            target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            target.connect(('127.0.0.1', self.options.bridge_port))
            target.settimeout(.5)
        else:
            ws.close(1008, 'Unknown channel')
            return

        def receive():
            deliver=True
            try:
                while not stop.is_set():
                    try:
                        data = target.recv(262144)
                    except socket.timeout:
                        continue
                    except (ConnectionRefusedError, ConnectionResetError):
                        # The browser can send before the live UDP bridge binds.
                        # Windows reports its ICMP port-unreachable as 10054.
                        # Keep the channel alive for the next player frame.
                        if path == '/telemetry':
                            continue
                        raise
                    if not data:
                        closed.set()
                        break
                    if deliver:
                        try:ws.send(data.decode('utf8') if path == '/telemetry' else data)
                        except Exception:
                            if path != '/game':raise
                            deliver=False
            except Exception:
                pass
            finally:
                with contextlib.suppress(Exception):
                    ws.close()

        thread = threading.Thread(target=receive, daemon=True)
        thread.start()
        try:
            for message in ws:
                if path == '/telemetry':
                    if not isinstance(message, str) or len(message) > 60000:
                        break
                    document = json.loads(message)
                    self.observe_feedback(document)
                    try:
                        target.send(message.encode('utf8'))
                    except (ConnectionRefusedError, ConnectionResetError):
                        # Windows can report an early UDP port error on send.
                        # The next player snapshot retries after bridge startup.
                        continue
                elif isinstance(message, bytes):
                    target.sendall(message)
        except Exception:
            pass
        finally:
            if path == '/game':
                with contextlib.suppress(OSError):target.shutdown(socket.SHUT_WR)
                closed.wait(timeout=2)
                with self.connection_lock:self.game_connections.pop(target,None)
            stop.set()
            target.close()
            with contextlib.suppress(Exception):
                ws.close()
            thread.join(timeout=2)

    def observe_feedback(self, document):
        """The editor follows the active character after either kind of start."""
        self.latest_player = document.get('player') or {}
        if self.latest_player.get('inventory_preview'):
            self.latest_inventory = self.latest_player['inventory_preview']
            self.inventory_error = None
        if self.latest_player.get('inventory_error'):
            self.inventory_error = self.latest_player['inventory_error']
        self.feedback += 1
        for event in document.get('events', []):
            if event.get('kind') == 'damage':
                if event.get('ownerid') == self.latest_player.get('worldid') and event.get('victim') == self.latest_player.get('objid'):
                    self.received_damage += int(event.get('value', 0))
                else:
                    self.damage += int(event.get('value', 0))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        if '/api/status' not in args[0]:
            super().log_message(fmt, *args)

    def send(self, data, content_type='application/json', status=200, cache=False):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf8')
        etag='"'+hashlib.sha256(data).hexdigest()+'"' if cache else None
        if etag and self.headers.get('If-None-Match')==etag:
            self.send_response(304)
            self.send_header('ETag',etag)
            self.send_header('Cache-Control','public, max-age=0, must-revalidate')
            self.end_headers()
            return
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'public, max-age=0, must-revalidate' if cache else 'no-store')
        if etag:self.send_header('ETag',etag)
        with contextlib.suppress(BrokenPipeError, ConnectionResetError):
            self.end_headers()
            self.wfile.write(data)

    def do_GET(self):
        if not self.local_host():
            return self.send(dict(error='Host rejected'), status=403)
        path = unquote(urlsplit(self.path).path)
        try:
            if path in ('/api/info', '/api/status'):
                return self.send(self.server.prototype.info())
            if path=='/api/native/status':
                from tools.native_playback import NativeControlClient
                return self.send(NativeControlClient().call('/status'))
            if path == '/api/editor':
                return self.send(dict(self.server.prototype.workspace.editor(),
                                      update=update.public_status(self.server.prototype.update_checker.status())))
            if path == '/api/item-icons':
                prototype = self.server.prototype
                if not hasattr(prototype, 'item_icons'):
                    from tools.item_icons import all_icons
                    prototype.item_icons = all_icons(prototype.version, prototype.items, prototype.asset)
                return self.send(prototype.item_icons, cache=True)
            if path == '/api/update':
                return self.send(update.public_status(self.server.prototype.update_checker.status()))
            if path == '/api/records/transfer':
                token=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                return self.send(getattr(self.server.prototype,'transfer_progress',{}).get(token,dict(stage='connecting')))
            if path == '/api/records/origin':
                from tools.replay_sharing import ShareClient
                identifier=parse_qs(urlsplit(self.path).query).get('id',[''])[0]
                row=self.server.prototype.records.get(identifier)
                result=ShareClient(self.server.prototype.directory).origin(row)
                self.server.prototype.records.remember_share_code(identifier,result['code'])
                return self.send(result)
            if path == '/api/records/comments':
                from tools.replay_sharing import ShareClient
                query=parse_qs(urlsplit(self.path).query)
                return self.send(ShareClient(self.server.prototype.directory).comments(query.get('code',[''])[0],query.get('before',[None])[0]))
            if path == '/api/records/shared-detail':
                from tools.replay_sharing import ShareClient
                identifier=parse_qs(urlsplit(self.path).query).get('code',[''])[0]
                return self.send(ShareClient(self.server.prototype.directory).details(identifier))
            if path == '/api/records/shared':
                from tools.replay_sharing import ShareClient
                query={key:values[0] for key,values in parse_qs(urlsplit(self.path).query).items()}
                return self.send(ShareClient(self.server.prototype.directory).listings(query.get('sort','latest'),query))
            if path == '/api/records':
                return self.send(dict(records=self.server.prototype.battle_records()))
            if path == '/api/replay/check':
                identifier = parse_qs(urlsplit(self.path).query).get('id', [''])[0]
                self.server.prototype.replay_document(identifier)
                return self.send(dict(ready=True))
            if path == '/api/replay/takeover':
                token = parse_qs(urlsplit(self.path).query).get('job', [''])[0]
                return self.send(self.server.prototype.takeover_jobs.status(token))
            if path == '/api/game-preferences':
                return self.send(self.server.prototype.game_preferences.read())
            if path == '/api/inventory':
                return self.send(dict(inventory=self.server.prototype.latest_inventory, error=self.server.prototype.inventory_error))
            if path == '/client.js':
                return self.send(self.server.prototype.client, 'text/javascript',cache=True)
            if path == '/runtime.js':
                prototype = self.server.prototype
                recording = getattr(prototype, 'recording_id', None)
                preferences = prototype.game_preferences.read()
                replay = dict(id=recording,mode='record') if isinstance(recording,str) else None
                identifier = parse_qs(urlsplit(self.path).query).get('replay', [None])[0]
                if identifier:
                    document = prototype.replay_document(identifier)
                    record=parse_qs(urlsplit(self.path).query).get('record',[None])[0]
                    if record is not None:record=str(uuid.UUID(record))
                    replay = dict(id=document['id'], mode='play', document=document, record=record,
                                  takeover=prototype.replay_takeover_ready(identifier,record,document))
                    preferences = dict(key='LocalStorage.pb', revision=0, content=document['preferences'])
                data = 'var STARBREAK_WEB_BOOT=' + json.dumps(dict(wsPort=prototype.options.ws_port,keybindings=prototype.workspace.preferences()['keys'],gamePreferences=preferences,replay=replay)) + ';'
                return self.send(data.encode('utf8'), 'text/javascript')
            if path in ('/', '/index.html', '/game.html', '/app.js', '/editor.js', '/records.js', '/records.css', '/guide.js', '/guide.css', '/canvas.js', '/zoom.js', '/game-preferences.js', '/inventory.js', '/bridge.js', '/shortcuts.js', '/native-replay.js', '/replay.js', '/replay-storage.js', '/style.css'):
                filename = 'index.html' if path == '/' else path[1:]
                data = (ROOT / 'web' / filename).read_bytes()
                if path == '/game.html':
                    identifier = parse_qs(urlsplit(self.path).query).get('replay', [None])[0]
                    if identifier:
                        identifier = str(uuid.UUID(identifier))
                        record=parse_qs(urlsplit(self.path).query).get('record',[None])[0]
                        suffix='&amp;record='+str(uuid.UUID(record)) if record else ''
                        data = data.replace(b'src="runtime.js"', ('src="runtime.js?replay='+identifier+suffix+'"').encode())
                return self.send(data, mimetypes.guess_type(filename)[0] or 'text/plain',cache=True)
            data = self.server.prototype.asset(path)
            return self.send(data, mimetypes.guess_type(path)[0] or 'application/octet-stream',cache=True)
        except Exception as error:
            return self.send(dict(error=str(error)), status=404)

    def do_POST(self):
        if not self.local_host():
            return self.send(dict(error='Host rejected'), status=403)
        if self.path not in ('/api/start', '/api/prepare', '/api/editor', '/api/validate', '/api/reset', '/api/preferences', '/api/draft', '/api/update', '/api/records/import', '/api/records/export', '/api/records/share', '/api/records/redeem', '/api/records/comments', '/api/game-preferences', '/api/replay/client', '/api/replay/takeover', '/api/replay/warm', '/api/native/command', '/api/quit'):
            return self.send(dict(error='Unknown action'), status=404)
        # Local UI only. Do not allow cross-origin web pages to start processes.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(dict(error='Origin rejected'), status=403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if self.path == '/api/records/import':
                if not 0 < length <= MAX_IMPORT:
                    raise ValueError('Battle import exceeds 256 MB')
                return self.send(self.server.prototype.records.import_bytes(self.rfile.read(length)))
            limit = 8192 if self.path=='/api/records/comments' else 512*1024 if self.path in ('/api/editor', '/api/validate', '/api/draft', '/api/game-preferences') else 4096
            if self.path == '/api/replay/client':
                limit = MAX_CHUNK
            if self.path == '/api/records/export':
                limit = 16*1024  # Up to 128 UUIDs plus JSON delimiters.
            if not 0 < length <= limit:
                raise ValueError('Invalid request size')
            document = json.loads(self.rfile.read(length))
            if not isinstance(document, dict):
                raise ValueError('Expected a settings object')
            if self.path=='/api/native/command':
                from tools.native_playback import NativeControlClient
                return self.send(NativeControlClient().call('/command',document))
            if self.path == '/api/quit':
                if not getattr(self.server.prototype.options,'records_dir',None):raise ValueError('This server is managed by its launcher')
                self.send(dict(closing=True));threading.Thread(target=self.server.shutdown,daemon=True).start();return
            if self.path == '/api/replay/client':
                store=self.server.prototype.client_journals
                if document.get('native') is True:
                    from tools.native_replay import NativeJournals
                    store=NativeJournals(store.path.parent)
                return self.send(store.append(document.get('id'), document))
            if self.path == '/api/replay/warm':
                return self.send(self.server.prototype.warm_takeover(document))
            if self.path == '/api/replay/takeover':
                prototype = self.server.prototype
                return self.send(prototype.takeover_jobs.begin(prototype.prepare_takeover, document))
            if self.path == '/api/records/export':
                import base64
                name, data = self.server.prototype.records.export(document.get('ids'))
                return self.send(dict(filename=name, content=base64.b64encode(data).decode('ascii')))
            if self.path == '/api/records/comments':
                from tools.replay_sharing import ShareClient
                return self.send(ShareClient(self.server.prototype.directory).comment(document.get('code'),document.get('nickname'),document.get('text')))
            if self.path in ('/api/records/share', '/api/records/redeem'):
                from tools.replay_sharing import ShareClient
                service = ShareClient(self.server.prototype.directory)
                if self.path.endswith('/share'):
                    identifier = str(uuid.UUID(document['id']))
                    row=self.server.prototype.records.get(identifier)
                    if row['source']=='import':
                        result=service.origin(row);self.server.prototype.records.remember_share_code(identifier,result['code'])
                        return self.send(result)
                    token=str(uuid.UUID(document.get('transfer_id',str(uuid.uuid4()))))
                    prototype=self.server.prototype
                    if not hasattr(prototype,'transfer_progress'):prototype.transfer_progress={}
                    def progress(stage,loaded=0,total=0):prototype.transfer_progress[token]=dict(stage=stage,loaded=loaded,total=total)
                    progress('preparing')
                    _name, data = prototype.records.export([identifier])
                    result=service.upload(data,document.get('nickname',''),progress)
                    prototype.records.remember_share_code(identifier,result['code'],result.get('nickname',document.get('nickname','')))
                    return self.send(result)
                token=str(uuid.UUID(document.get('transfer_id',str(uuid.uuid4()))))
                prototype=self.server.prototype
                if not hasattr(prototype,'transfer_progress'):prototype.transfer_progress={}
                if len(prototype.transfer_progress)>32:prototype.transfer_progress.clear()
                def progress(stage,loaded=0,total=0):prototype.transfer_progress[token]=dict(stage=stage,loaded=loaded,total=total)
                progress('connecting')
                data = service.download(document.get('code'),progress)
                progress('validate',len(data),len(data))
                result=self.server.prototype.records.import_bytes(data,shared_code=document.get('code'))
                progress('saved',len(data),len(data))
                if result['imported']:service.acknowledge(document.get('code'))
                return self.send(result)
            if self.path == '/api/update':
                return self.send(update.public_status(self.server.prototype.update_checker.start()))
            if self.path == '/api/preferences':
                return self.send(self.server.prototype.workspace.save_preferences(document))
            if self.path == '/api/game-preferences':
                return self.send(self.server.prototype.game_preferences.save(document))
            if self.path == '/api/draft':
                return self.send(self.server.prototype.workspace.save_draft(document))
            if self.path == '/api/editor':
                return self.send(self.server.prototype.workspace.save(document))
            if self.path == '/api/validate':
                return self.send(self.server.prototype.workspace.validate(document))
            if self.path == '/api/reset':
                return self.send(self.server.prototype.workspace.restore_defaults(document.get('revision')))
            if getattr(self.server.prototype.options, 'desktop', False):
                if self.path == '/api/prepare':return self.send(dict(assets_ready=True))
                from tools.native_playback import NativeControlClient
                request=self.server.prototype.workspace.start(document)
                return self.send(NativeControlClient().call('/command',dict(action='practice',map=request['map'],shell=request['shell'])))
            self.send(self.server.prototype.prepare(document) if self.path == '/api/prepare' else self.server.prototype.start(document))
        except Exception as error:
            self.send(dict(error=str(error)), status=400)

    def local_host(self):
        return self.headers.get('Host') in (
            '127.0.0.1:' + str(self.server.server_port),
            'localhost:' + str(self.server.server_port))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client', type=Path,
                        help='Original 1.14.6 browser client; otherwise download and cache the pinned official client.')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--ws-port', type=int, default=8766)
    parser.add_argument('--game-port', type=int, default=20480)
    parser.add_argument('--bridge-port', type=int, default=21551)
    parser.add_argument('--data-dir', type=Path, help='Separate browser profile and asset cache directory')
    parser.add_argument('--records-dir', type=Path, help='Use an existing battle history without moving or copying records')
    parser.add_argument('--desktop', action='store_true', help='Shared UI backed by native equipment, settings and launch controls')
    parser.add_argument('--no-browser', action='store_true', help='Print the address without opening a browser')
    parser.add_argument('--check-runtime', action='store_true', help='Verify the bundled browser runtime and exit')
    options = parser.parse_args()
    from websockets.sync.server import serve
    if options.check_runtime:
        print('Browser runtime OK: Python ' + sys.version.split()[0] + ', WebSockets available')
        return
    if options.client is None:
        directory = options.data_dir or ROOT / 'userdata/web'
        options.client = ensure_cached_client(directory / CLIENT_FILENAME)
    paths.ensure_user_data()
    prototype = Prototype(options)
    http = ThreadingHTTPServer(('127.0.0.1', options.port), Handler)
    http.prototype = prototype
    origins = ['http://127.0.0.1:' + str(options.port), 'http://localhost:' + str(options.port)]
    with serve(prototype.websocket, '127.0.0.1', options.ws_port, subprotocols=['binary'],
               origins=origins, max_size=1024*1024) as websocket:
        threading.Thread(target=websocket.serve_forever, daemon=True).start()
        print('Browser prototype: http://127.0.0.1:' + str(options.port), flush=True)
        try:
            if not options.no_browser:
                import webbrowser
                threading.Thread(target=webbrowser.open, args=('http://127.0.0.1:' + str(options.port),), daemon=True).start()
            http.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            prototype.stop()
            http.server_close()


if __name__ == '__main__':
    main()
