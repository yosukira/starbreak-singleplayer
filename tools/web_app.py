"""Run the local browser battle prototype without a native game or SBPE.

Usage: python3 tools/web_app.py --client /path/to/VR1574197275.mvmmoclient.js
Requires websockets; serves only the prototype UI and whitelisted game assets.
"""
import argparse
import contextlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import re
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localserver import game_settings, paths, pb_layout
from tools.web_client import patch_client


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
        self.feedback = 0
        self.damage = 0
        self.received_damage = 0
        self.directory = ROOT / 'userdata' / 'web'
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
        self.items = {}
        import base64
        for line in (ROOT / 'data/catalogue/item_descriptions.jsonl').read_text().splitlines():
            row = json.loads(line)
            item = pb_layout.default_layouts().decode('ItemDescription', base64.b64decode(row['payloadBase64']))
            self.items[int(item['type_'])] = item

    def info(self):
        return dict(map=self.map, shell=self.shell, running=bool(self.process and self.process.poll() is None),
                    ws_port=self.options.ws_port, feedback=self.feedback, damage=self.damage,
                    received_damage=self.received_damage,
                    player=self.latest_player,
                    maps=[dict(id='hulk_mid', title=game_settings.map_info('hulk_mid')['title'])],
                    items=[dict(id=k, name=v['name_'], slot=v.get('slot_'), usableby=v.get('usableby_', []))
                           for k, v in self.items.items() if v.get('slot_') == 256])

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
            if shell not in (1, 2, 3, 5) or document.get('map', 'hulk_mid') != 'hulk_mid':
                raise ValueError('This prototype currently supports Hulk Mid and the four shells.')
            weapon = document.get('weapon')
            if weapon is not None:
                weapon = int(weapon)
                item = self.items.get(weapon)
                if item is None or item.get('slot_') != 256 or shell not in item.get('usableby_', []):
                    raise ValueError('Weapon is not compatible with this shell.')
            self.stop()
            self.map, self.shell = 'hulk_mid', shell
            loadouts = json.loads((paths.USERDATA / 'class_loadouts.json').read_text(encoding='utf8'))
            if weapon is not None:
                row = loadouts[str(shell)]
                row['items_'] = [v for v in row['items_'] if v.get('slotpos_') != 256]
                row['items_'].insert(0, dict(type_=weapon, slotpos_=256, statboosts_=[]))
            loadout_path = self.directory / 'class_loadouts.json'
            loadout_path.write_text(json.dumps(loadouts), encoding='utf8')
            self.log = (self.directory / 'server.log').open('w', encoding='utf8')
            info = game_settings.map_info(self.map)
            command = [sys.executable, '-u', '-m', 'localserver.server', '--web-client', '--mode', 'hulk', '--live',
                       '--map', self.map, '--pvid', info['pvid'], '--zone', info['zone'],
                       '--gdv', str(info.get('gdv', 1)), '--host', '127.0.0.1',
                       '--port', str(self.options.game_port), '--bridge-port', str(self.options.bridge_port),
                       '--player-type', str(shell), '--create-world-hex', str(ROOT / 'maps' / self.map / 'createworld.hex'),
                       '--characters', str(ROOT / 'data/catalogue/characters.json'),
                       '--items', str(ROOT / 'data/catalogue/item_descriptions.jsonl'),
                       '--data-version', str(self.version_file),
                       '--class-loadouts', str(loadout_path),
                       '--player-payloads', str(ROOT / 'data/catalogue/class_players.json'), '--no-time-echo']
            self.process = subprocess.Popen(command, cwd=ROOT, stdout=self.log, stderr=subprocess.STDOUT)
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError((self.directory / 'server.log').read_text(encoding='utf8')[-2000:])
                if 'listening' in (self.directory / 'server.log').read_text(encoding='utf8'):
                    self.latest_player, self.feedback, self.damage, self.received_damage = {}, 0, 0, 0
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
        with urlopen('https://www.starbreak.com' + path, timeout=30) as response:
            data = response.read()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
        return data

    def websocket(self, ws):
        path = urlsplit(ws.request.path).path
        stop = threading.Event()
        if path == '/game':
            target = socket.create_connection(('127.0.0.1', self.options.game_port), timeout=10)
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
                    except (socket.timeout, ConnectionRefusedError):
                        continue
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
                    self.feedback += 1
                    for event in document.get('events', []):
                        if event.get('kind') == 'damage':
                            if event.get('ownerid') == self.latest_player.get('worldid') and event.get('victim') == self.latest_player.get('objid'):
                                self.received_damage += int(event.get('value', 0))
                            else:
                                self.damage += int(event.get('value', 0))
                    target.send(message.encode('utf8'))
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

    def send(self, data, content_type='application/json', status=200):
        if not isinstance(data, bytes):
            data = json.dumps(data, ensure_ascii=False).encode('utf8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self.local_host():
            return self.send(dict(error='Host rejected'), status=403)
        path = unquote(urlsplit(self.path).path)
        try:
            if path in ('/api/info', '/api/status'):
                return self.send(self.server.prototype.info())
            if path == '/client.js':
                return self.send(self.server.prototype.client, 'text/javascript')
            if path == '/runtime.js':
                data = 'var STARBREAK_WEB_BOOT=' + json.dumps(dict(wsPort=self.server.prototype.options.ws_port)) + ';'
                return self.send(data.encode('utf8'), 'text/javascript')
            if path in ('/', '/index.html', '/game.html', '/app.js', '/bridge.js', '/style.css'):
                filename = 'index.html' if path == '/' else path[1:]
                return self.send((ROOT / 'web' / filename).read_bytes(), mimetypes.guess_type(filename)[0] or 'text/plain')
            data = self.server.prototype.asset(path)
            return self.send(data, mimetypes.guess_type(path)[0] or 'application/octet-stream')
        except Exception as error:
            return self.send(dict(error=str(error)), status=404)

    def do_POST(self):
        if not self.local_host():
            return self.send(dict(error='Host rejected'), status=403)
        if self.path != '/api/start':
            return self.send(dict(error='Unknown action'), status=404)
        # Local UI only. Do not allow cross-origin web pages to start processes.
        origin = self.headers.get('Origin')
        if origin and origin != 'http://' + self.headers.get('Host', ''):
            return self.send(dict(error='Origin rejected'), status=403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 4096:
                raise ValueError('Invalid request size')
            document = json.loads(self.rfile.read(length))
            if not isinstance(document, dict):
                raise ValueError('Expected a settings object')
            self.send(self.server.prototype.start(document))
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
    options = parser.parse_args()
    if options.client is None:
        options.client = ROOT / 'userdata/web/VR1574197275.mvmmoclient.js'
        if not options.client.is_file():
            print('Downloading the original 1.14.6 web client from starbreak.com...', flush=True)
            with urlopen('https://www.starbreak.com/VR1574197275.mvmmoclient.js', timeout=60) as response:
                data = response.read()
            patch_client(data)  # Check exact supported version before caching.
            options.client.parent.mkdir(parents=True, exist_ok=True)
            options.client.write_bytes(data)
    from websockets.sync.server import serve
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
            http.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            prototype.stop()
            http.server_close()


if __name__ == '__main__':
    main()
