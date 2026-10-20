"""Player-device runtime. Uses memory messages, never sockets or subprocesses.

CPython and the same encounter code run in a browser Web Worker via Pyodide.
Profile files are mounted by the worker in IndexedDB, not stored on the host.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import struct
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from localserver import game_settings, paths, pb_layout, protocol
from localserver.server import LocalServer, parse_args
from localserver.battle_records import RecordStore
from tools.client_replay import ClientJournals, SCHEMA
from tools.game_preferences import GamePreferences
from tools.web_workspace import Workspace

PENDING_MAPS = {'eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run'}


class MemoryChannel:
    """Incremental wire framing with the original client's two cipher streams."""
    def __init__(self, emit):
        self.emit, self.incoming = emit, bytearray()
        self.rx = self.tx = self.header = None

    def enable_rc4(self, key):
        self.rx, self.tx = protocol.RC4(key), protocol.RC4(key)

    def send_frame(self, kind, payload):
        self.send_frames(((kind, payload),))

    def send_frames(self, frames):
        raw = b''.join(bytes([kind])+struct.pack('>i', len(data))+data for kind, data in frames)
        if raw:self.emit(self.tx.crypt(raw) if self.tx else raw)

    def feed(self, raw, receiver):
        if len(raw) > protocol.MAX_FRAME_SIZE:
            raise ValueError('Game message exceeds its limit')
        self.incoming.extend(raw)
        while True:
            if self.header is None:
                if len(self.incoming) < 5:return
                header = bytes(self.incoming[:5]);del self.incoming[:5]
                if self.rx:header = self.rx.crypt(header)
                length = struct.unpack('>i', header[1:])[0]
                if not 0 <= length <= protocol.MAX_FRAME_SIZE:
                    raise ValueError('Invalid game message size')
                self.header = header[0], length
            kind, length = self.header
            if len(self.incoming) < length:return
            data = bytes(self.incoming[:length]);del self.incoming[:length]
            if self.rx and data:data = self.rx.crypt(data)
            self.header = None
            receiver.send((kind, data))


class BrowserRuntime:
    def __init__(self, directory, manifest):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        os.environ.update(SB_GAME_SETTINGS_PATH=str(self.directory/'game_settings.json'),
                          SB_BATTLE_RECORDS_PATH=str(self.directory/'battles'), SB_RECORD_REPLAY='1',
                          SB_PLAYER_IDENTITY=str(self.directory/'identity.json'))
        game_settings.PATH = str(self.directory/'game_settings.json')
        self.workspace = Workspace(self.directory, source=paths.ROOT/'data/defaults')
        self.records = RecordStore(self.directory/'battles')
        for row in self.records.list():
            if row['result']=='active':
                row['result']='interrupted'
                self.records.save(row)
        self.clients = ClientJournals(self.directory/'battles')
        self.clients.migrate_chunks()
        self.preferences = GamePreferences(self.directory)
        self.manifest = manifest
        self.server = self.receiver = self.channel = self.attempt = None
        self.map, self.shell, self.player = 'hulk_mid', 1, {}
        self.feedback = self.damage = self.received_damage = 0
        self.events, self.takeovers, self.jobs = [], {}, {}

    def emit(self, socket, data):
        self.events.append(dict(socket=socket, data=base64.b64encode(data).decode() if isinstance(data, bytes) else data,
                                binary=isinstance(data, bytes)))

    def drain(self):
        rows, self.events = self.events, []
        return rows

    def info(self):
        return dict(map=self.map, shell=self.shell, running=self.server is not None,
            assets_ready=True, assets_cached=1, assets_total=1, asset_error='', ws_port=0,
            feedback=self.feedback, damage=self.damage, received_damage=self.received_damage, player=self.player,
            maps=[dict(id=key, title=game_settings.map_info(key)['title'],
                       names=dict(en=game_settings.map_info(key)['title'], zh=game_settings.map_info(key)['title']),
                       ready=key not in PENDING_MAPS) for key in game_settings.ordered_maps()])

    def start(self, document):
        map_id, shell = document.get('map'), document.get('shell')
        if map_id not in game_settings.available_maps() or map_id in PENDING_MAPS or shell not in (1,2,3,5):
            raise ValueError('Unknown map or character')
        self.stop()
        self.map, self.shell, self.player = map_id, shell, {}
        self.workspace.materialize(map_id)
        recording = self.workspace.preferences()['record_replays']
        os.environ['SB_RECORD_REPLAY'] = '1' if recording else '0'
        os.environ.pop('SB_REPLAY_ATTEMPT', None)
        self.attempt = None
        if recording:
            self.attempt = str(uuid.uuid4())
            self.clients.begin(self.attempt, self.manifest['client'], self.manifest['adapter'], self.preferences.read(), [1366,768])
            os.environ['SB_REPLAY_ATTEMPT'] = self.attempt
        info = game_settings.map_info(map_id)
        world = paths.ROOT/('data/catalogue/real_hulkboss_createworld_flags.hex' if map_id == game_settings.ORIGINAL_MAP else f'maps/{map_id}/createworld.hex')
        version = self.directory/'dataVersion.bpb'
        version.write_bytes(base64.b64decode(self.manifest['data_version']))
        options = parse_args(['--web-client','--mode','hulk','--live','--map',map_id,'--pvid',info['pvid'],
            '--zone',info['zone'],'--gdv',str(info.get('gdv',1)),'--player-type',str(shell),
            '--create-world-hex',str(world),'--data-version',str(version),
            '--characters',str(paths.ROOT/'data/catalogue/characters.json'),
            '--items',str(paths.ROOT/'data/catalogue/item_descriptions.jsonl'),
            '--player-payloads',str(paths.ROOT/'data/catalogue/class_players.json'),
            '--class-loadouts',str(self.directory/'class_loadouts.json'),
            '--projectile-overrides',str(self.directory/'class_loadout_overrides.json'),'--no-time-echo'])
        options.log_file = None
        self.server = LocalServer(options)
        self.server.manual_ticks = True
        return self.info()

    def stop(self):
        self.close_replay_pages()
        for host in self.takeovers.values():host.stop()
        self.takeovers.clear()
        self.active_takeover = None
        if self.server is not None:
            session = self.server.live_session
            if session is not None:session.stop()
            for name in ('egy_run','level_run'):
                recorder = getattr(getattr(self.server,name,None),'battle_recorder',None)
                if recorder is not None:recorder.finish()
        if self.receiver:self.receiver.close()
        self.server = self.channel = self.receiver = None

    def close_replay_pages(self):
        pages=getattr(self,'replay_pages',None)
        self.replay_pages=None
        if pages:pages['reader'].close()

    def open(self, identifier, url):
        path = urlsplit(url).path
        if path == '/game':
            if self.server is None:raise ValueError('Start a battle first')
            self.game_socket = identifier
            self.channel = MemoryChannel(lambda raw:self.emit(identifier, raw))
            self.receiver = self.server.connection(self.channel)
            next(self.receiver)
        elif path == '/takeover':
            token = parse_qs(urlsplit(url).query)['token'][0]
            host = self.takeovers[token]
            class Transport:
                def send(_self, data):self.emit(identifier, data)
            host.bind(Transport())
            self.active_takeover = host
            self.takeover_socket = identifier
        elif path != '/telemetry':raise ValueError('Unknown game channel')

    def close_socket(self, identifier, url):
        path = urlsplit(url).path
        if path == '/game' and identifier == getattr(self,'game_socket',None):
            self.stop()
        elif path == '/takeover' and identifier == getattr(self,'takeover_socket',None):
            host = getattr(self,'active_takeover',None)
            if host:host.stop()
            self.active_takeover = None

    def send(self, identifier, url, data, binary=False):
        path = urlsplit(url).path
        if path == '/game':
            self.channel.feed(base64.b64decode(data), self.receiver)
        else:
            host = getattr(self,'active_takeover',None) if path == '/takeover' else None
            if binary:
                host.binary(base64.b64decode(data));return
            document = json.loads(data)
            if host and document.get('type') == 'takeover-commit':
                host.commit();return
            self.player = document.get('player') or {}
            self.feedback += 1
            if host:ack = host.telemetry(document)
            elif self.server and self.server.live_session:
                bridge = self.server.live_session.bridge
                bridge._absorb(document)
                ack = bridge.acknowledgement(document.get('seq'))
            else:ack = dict(ack=document.get('seq',0))
            self.emit(identifier, json.dumps(ack))

    def tick(self, dt):
        host = getattr(self,'active_takeover',None)
        if host and host.committed and not host.stopped.is_set():host.step(dt)
        elif self.server and self.server.live_session:self.server.live_session.step(dt)
        return bool(self.server or host and host.committed and not host.stopped.is_set())

    def replay_document(self, identifier, require_exact=False, header_only=False):
        if header_only:
            from tools.native_replay import NativeJournals
            try:header,_=NativeJournals(self.records.directory).page_header(identifier)
            except ValueError:pass
            else:
                from tools.portable_replay import browser_document
                version=pb_layout.default_layouts().decode('DataVersion',base64.b64decode(self.manifest['data_version']))
                return browser_document(self.records.directory,identifier,version,self.manifest.get('native_adapter'),require_exact,source=dict(header,ops=[]))
        try:value=self.clients.read(identifier)
        except ValueError:
            from tools.portable_replay import browser_document
            version=pb_layout.default_layouts().decode('DataVersion',base64.b64decode(self.manifest['data_version']))
            return browser_document(self.records.directory,identifier,version,self.manifest.get('native_adapter'),require_exact)
        if value['client']!=self.manifest['client']:raise ValueError('This replay requires a supported original game client')
        from tools.client_replay import compatible_adapter
        if require_exact and not compatible_adapter(value['adapter'],self.manifest['adapter']):
            raise ValueError('Compatible playback can be tried, but takeover requires the matching replay adapter')
        return value

    def boot(self, query):
        self.close_replay_pages()
        values = parse_qs(query.lstrip('?'))
        replay = dict(id=self.attempt,mode='record') if self.attempt else None
        prefs = self.preferences.read()
        if 'replay' in values:
            attempt = str(uuid.UUID(values['replay'][0]))
            paged=None
            if values.get('paged')==['1']:
                from tools.native_replay import NativeJournals
                native=NativeJournals(self.records.directory)
                try:paged=native.page_header(attempt)
                except ValueError:pass
            if paged:
                from tools.portable_replay import browser_document
                version=pb_layout.default_layouts().decode('DataVersion',base64.b64decode(self.manifest['data_version']))
                document=browser_document(self.records.directory,attempt,version,self.manifest.get('native_adapter'),source=dict(paged[0],ops=[]))
                token=str(uuid.uuid4());self.replay_pages=dict(token=token,reader=native.read_pages(attempt),sequence=0)
                document['pages']=dict(token=token,total=paged[1])
            else:document = self.replay_document(attempt)
            replay = dict(id=attempt,mode='play',document=document, record=values.get('record',[None])[0],takeover=False)
            record = next((r for r in self.records.list() if r['id']==replay['record']),None)
            if record:
                from localserver.replay import read_journal, compatible_engine
                try:
                    header = read_journal(self.records.directory/(record['id']+'.server.jsonl.gz'))
                    if paged:
                        document['view_world']=header.get('world_sequence',1)
                        browser_document(self.records.directory,attempt,version,self.manifest.get('native_adapter'),True,source=dict(paged[0],ops=[]))
                    else:self.replay_document(attempt,require_exact=True)
                    replay['takeover'] = compatible_engine(header['engine'],header.get('map'))
                except (ValueError,OSError):pass
            prefs = dict(key='LocalStorage.pb',revision=0,content=document['preferences'])
        return dict(wsPort=0,keybindings=self.workspace.preferences()['keys'],gamePreferences=prefs,replay=replay)

    def api(self, method, url, document=None):
        path = urlsplit(url).path
        document = document or {}
        if path in ('/api/info','/api/status'):return self.info()
        if path == '/api/editor':return self.workspace.editor() if method=='GET' else self.workspace.save(document)
        if path == '/api/validate':return self.workspace.validate(document)
        if path == '/api/preferences':return self.workspace.save_preferences(document)
        if path == '/api/draft':return self.workspace.save_draft(document)
        if path == '/api/reset':return self.workspace.restore_defaults(document.get('revision'))
        if path == '/api/game-preferences':return self.preferences.read() if method=='GET' else self.preferences.save(document)
        if path == '/api/prepare':return self.info()
        if path == '/api/start':return self.start(document)
        if path == '/api/update':return dict(state='current',version=self.manifest['version'])
        if path == '/api/inventory':return dict(inventory=self.player.get('inventory_preview'),error=self.player.get('inventory_error'))
        if path == '/api/records':
            rows = self.records.list()
            states = self.clients.statuses([r['replay']['id'] for r in rows if r.get('replay',{}).get('id')],self.manifest['adapter'],client=self.manifest['client'],allow_compatible=True)
            from tools.portable_replay import availability
            states.update(availability(self.records.directory,[r['replay']['id'] for r in rows if r.get('replay',{}).get('id')]))
            for row in rows:
                if row.get('replay',{}).get('id') in states:row['replay']=states[row['replay']['id']]
            return dict(records=rows)
        if path == '/api/replay/client':
            clients=self.clients
            if document.get('native') is True:
                from tools.native_replay import NativeJournals
                clients=NativeJournals(self.records.directory)
            return clients.append(document['id'],document)
        if path == '/api/replay/check':
            attempt = parse_qs(urlsplit(url).query)['id'][0]
            if parse_qs(urlsplit(url).query).get('paged')==['1']:
                from tools.native_replay import NativeJournals
                try:NativeJournals(self.records.directory).page_header(attempt);return dict(ready=True)
                except ValueError:pass
            self.replay_document(attempt)
            return dict(ready=True)
        if path == '/api/replay/page':
            query=parse_qs(urlsplit(url).query);pages=getattr(self,'replay_pages',None)
            if not pages or query.get('token')!=[pages['token']]:raise ValueError('Replay loading was cancelled')
            if query.get('sequence')!=[str(pages['sequence'])]:raise ValueError('Replay page sequence changed')
            try:value=next(pages['reader'])
            except Exception:
                self.close_replay_pages();raise
            pages['sequence']+=1
            if value['end']:self.close_replay_pages()
            return value
        if path == '/api/replay/warm':
            from tools.replay_host import WarmTakeovers
            source=self.replay_document(document['id'],require_exact=True,header_only=True)
            clients=self.clients
            if source['schema']=='starbreak-native-journal-v1':
                from tools.native_replay import NativeJournals
                from tools.portable_replay import check_runner
                check_runner(document.get('runner'),self.manifest.get('native_adapter'));clients=NativeJournals(self.records.directory)
            if not getattr(self,'warm_takeovers',None):self.warm_takeovers=WarmTakeovers()
            return self.warm_takeovers.warm(self.records,clients,document)
        if path == '/api/replay/takeover':
            if method=='GET':return self.jobs[parse_qs(urlsplit(url).query)['job'][0]]
            from tools.replay_host import WarmTakeovers
            source=self.replay_document(document['id'],require_exact=True,header_only=True)
            clients=self.clients
            if source['schema']=='starbreak-native-journal-v1':
                from tools.native_replay import NativeJournals
                from tools.portable_replay import check_runner
                check_runner(document.get('runner'),self.manifest.get('native_adapter'));clients=NativeJournals(self.records.directory)
            if not getattr(self,'warm_takeovers',None):self.warm_takeovers=WarmTakeovers()
            self.stop()
            host = self.warm_takeovers.take(self.records,clients,document,manual=True)
            from tools.portable_replay import append_runner
            try:append_runner(host,document,clients,source,self.manifest.get('native_adapter'))
            except Exception:host.stop();raise
            self.takeovers[host.token] = host
            self.jobs[host.token] = dict(state='ready',result=dict(host.description(),preferences=self.preferences.read()))
            return dict(job=host.token)
        if path == '/api/records/export':
            name, data = self.records.export(document['ids'])
            return dict(filename=name,content=base64.b64encode(data).decode())
        if path == '/api/records/delete':return self.records.delete(document.get('ids'))
        if path == '/api/records/reference':
            row=self.records.get(parse_qs(urlsplit(url).query).get('id',[''])[0])
            return {key:row.get(key,'') for key in ('id','source','shared_by','shared_code')}
        if path == '/api/records/origin':
            self.records.remember_share_code(document['id'],document['code'],document.get('nickname'))
            return dict(saved=True)
        if path == '/api/records/import':return self.records.import_bytes(base64.b64decode(document['content']),shared_code=document.get('shared_code'))
        raise ValueError('Unknown browser action')
