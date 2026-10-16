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
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localserver import game_settings, paths, pb_layout
from tools.web_client import CLIENT_FILENAME, ensure_cached_client, patch_client
from tools.web_workspace import Workspace, atomic_json
from tools.web_text import map_names
from tools import update

# These encounters need inventory/ground handoff adapters before being enabled.
PENDING_MAPS = {'eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run'}


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
        return dict(map=self.map, shell=self.shell, running=bool(self.process and self.process.poll() is None),
                    assets_ready=cached == len(assets), assets_cached=cached, assets_total=len(assets),
                    asset_error=getattr(self, 'asset_error', ''),
                    ws_port=self.options.ws_port, feedback=self.feedback, damage=self.damage,
                    received_damage=self.received_damage,
                    player=self.latest_player,
                    maps=maps(),
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

    def stop(self):
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
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
            environment = dict(os.environ, SB_GAME_SETTINGS_PATH=str(self.directory / 'game_settings.json'))
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

    def asset(self, path):
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
        stop = threading.Event()
        if path == '/game':
            try:
                target = socket.create_connection(('127.0.0.1', self.options.game_port), timeout=10)
            except OSError:
                ws.close(1013, 'Start or restart the encounter')
                return
            target.settimeout(.5)
        elif path == '/telemetry':
            target = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            target.connect(('127.0.0.1', self.options.bridge_port))
            target.settimeout(.5)
        else:
            ws.close(1008, 'Unknown channel')
            return

        def receive():
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
                        break
                    ws.send(data.decode('utf8') if path == '/telemetry' else data)
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
                    try:
                        target.send(message.encode('utf8'))
                    except (ConnectionRefusedError, ConnectionResetError):
                        # An early UDP error may surface on send as well as recv.
                        # Telemetry is a snapshot; the next frame replaces it.
                        continue
                elif isinstance(message, bytes):
                    target.sendall(message)
        except Exception:
            pass
        finally:
            stop.set()
            target.close()
            with contextlib.suppress(Exception):
                ws.close()
            thread.join(timeout=2)


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
            if path == '/api/editor':
                return self.send(dict(self.server.prototype.workspace.editor(),
                                      update=update.public_status(self.server.prototype.update_checker.status())))
            if path == '/api/update':
                return self.send(update.public_status(self.server.prototype.update_checker.status()))
            if path == '/api/inventory':
                return self.send(dict(inventory=self.server.prototype.latest_inventory, error=self.server.prototype.inventory_error))
            if path == '/client.js':
                return self.send(self.server.prototype.client, 'text/javascript',cache=True)
            if path == '/runtime.js':
                data = 'var STARBREAK_WEB_BOOT=' + json.dumps(dict(wsPort=self.server.prototype.options.ws_port,keybindings=self.server.prototype.workspace.preferences()['keys'])) + ';'
                return self.send(data.encode('utf8'), 'text/javascript')
            if path in ('/', '/index.html', '/game.html', '/app.js', '/editor.js', '/inventory.js', '/bridge.js', '/shortcuts.js', '/style.css'):
                filename = 'index.html' if path == '/' else path[1:]
                return self.send((ROOT / 'web' / filename).read_bytes(), mimetypes.guess_type(filename)[0] or 'text/plain',cache=True)
            data = self.server.prototype.asset(path)
            return self.send(data, mimetypes.guess_type(path)[0] or 'application/octet-stream',cache=True)
        except Exception as error:
            return self.send(dict(error=str(error)), status=404)

    def do_POST(self):
        if not self.local_host():
            return self.send(dict(error='Host rejected'), status=403)
        if self.path not in ('/api/start', '/api/prepare', '/api/editor', '/api/validate', '/api/reset', '/api/preferences', '/api/draft', '/api/update'):
            return self.send(dict(error='Unknown action'), status=404)
        # Local UI only. Do not allow cross-origin web pages to start processes.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(dict(error='Origin rejected'), status=403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= (512*1024 if self.path in ('/api/editor', '/api/validate', '/api/draft') else 4096):
                raise ValueError('Invalid request size')
            document = json.loads(self.rfile.read(length))
            if not isinstance(document, dict):
                raise ValueError('Expected a settings object')
            if self.path == '/api/update':
                return self.send(update.public_status(self.server.prototype.update_checker.start()))
            if self.path == '/api/preferences':
                return self.send(self.server.prototype.workspace.save_preferences(document))
            if self.path == '/api/draft':
                return self.send(self.server.prototype.workspace.save_draft(document))
            if self.path == '/api/editor':
                return self.send(self.server.prototype.workspace.save(document))
            if self.path == '/api/validate':
                return self.send(self.server.prototype.workspace.validate(document))
            if self.path == '/api/reset':
                return self.send(self.server.prototype.workspace.restore_defaults(document.get('revision')))
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
