"""Local, versioned battle records. Only explicit gameplay fields cross exports.

Documents are JSON, never executable snapshots. SQLite owns the index and atomic
writes, so the UI can read while the simulator records a battle in another process.
"""
from datetime import datetime
import hashlib
import errno
import shutil
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from contextlib import contextmanager
import threading
import tempfile
import time
import uuid
import zipfile

from . import equipment, game_settings, paths, projectile_speeds

SCHEMA = 'starbreak-battle-record-v1'
CLASSES = {1: 'Wildfire', 2: 'Duskwing', 5: 'Fabricator', 3: 'Ironclad'}
RESULTS = ('active', 'victory', 'defeat', 'interrupted')
METRICS = ('damage_dealt', 'kills', 'health_packs', 'ammo_packs', 'healing', 'damage_taken', 'generators_destroyed')
MAX_RECORD = 8 * 1024 * 1024
MAX_IMPORT = 256 * 1024 * 1024
MAX_EXPANDED_IMPORT = 512 * 1024 * 1024
NON_BATTLE_MAPS = frozenset(('home', 'lobby'))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf8')


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value, low=0, high=1e15, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid numeric field')
    if integer and type(value) is not int:
        raise ValueError('Expected an integer')
    return value


def text(value, limit=160):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid text field')
    return value


def seed_string(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,20}', value):
        value = int(value)
    return str(number(value, 0, 2**64-1, True))


def loadout(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid loadout')
    items = value.get('items_', [])
    if not isinstance(items, list) or len(items) > 32:
        raise ValueError('Invalid equipment list')
    result = dict(type_=number(value.get('type_', 1), 1, 5, True),
                  mk_=number(value.get('mk_', 6), 1, 6, True),
                  impslots_=number(value.get('impslots_', 0), 0, 8, True), items_=[])
    for item in items:
        boosts = item.get('statboosts_', [])
        if not isinstance(boosts, list):
            raise ValueError('Invalid modifier list')
        result['items_'].append(dict(type_=number(item['type_'], 1, 2**31-1, True),
            slotpos_=number(item['slotpos_'], 1, 65535, True),
            statboosts_=[dict(stat_=number(b['stat_'], 1, 1000, True),
                            val_=number(b.get('val_', 0), -2**31, 2**31-1, True)) for b in boosts]))
    return result


def parameters(value):
    """An explicit allowlist prevents exports from collecting arbitrary files."""
    if not isinstance(value, dict):
        raise ValueError('Invalid battle parameters')
    result = {}
    for group in ('balance', 'drops', 'turrets', 'missiles'):
        row = value.get(group) or {}
        if not isinstance(row, dict):
            raise ValueError('Invalid battle parameter group')
        allowed = {'balance': ('enemy_hp', 'enemy_damage', 'enemy_speed', 'spawn_rate', 'spawn_cap', 'boss_attack_rate'),
                   'drops': ('health_percent', 'ammo_percent', 'ec_percent'),
                   'turrets': ('front', 'upper', 'lower'), 'missiles': ('upper', 'lower')}[group]
        result[group] = {k: None if row[k] is None else number(row[k]) for k in allowed if k in row}
    result['skip_intro'] = value.get('skip_intro') is True
    result['missile_delay_ms'] = number(value.get('missile_delay_ms', 275), 0, 500)
    result['projectile_speeds'] = projectile_speeds.normalize(value.get('projectile_speeds', {}))
    result['loadout'] = loadout(value['loadout'])
    result['items'] = []
    items = value.get('items', [])
    if not isinstance(items, list) or len(items) > 32:
        raise ValueError('Invalid item snapshots')
    for item in items:
        base = item.get('base', [])
        projectiles = item.get('projectiles', [])
        if len(base) > 64 or len(projectiles) > 128:
            raise ValueError('Item snapshot too large')
        result['items'].append(dict(type=number(item['type'], 1, 2**31-1, True),
            name=text(item['name']),
            base=[dict(stat_=number(b['stat_'], 1, 1000, True), val_=number(b.get('val_', 0), -2**31, 2**31-1, True)) for b in base],
            projectiles=[dict(index=number(p['index'], 0, 127, True), speed=number(p['speed'], -1000000, 1000000, True)) for p in projectiles]))
    return result


def validate(value):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('Unsupported battle record format')
    identifier = str(uuid.UUID(value['id']))
    shell = number(value['shell'], 1, 5, True)
    if shell not in CLASSES or value['result'] not in RESULTS:
        raise ValueError('Invalid class or battle result')
    result = dict(schema=SCHEMA, id=identifier, started=number(value['started']),
        duration=number(value['duration'], 0, 7*86400), shell=shell,
        map=text(value['map'], 80), map_title=text(value['map_title']),
        version=text(value.get('version', 'unknown'), 64), result=value['result'],
        source='import' if value.get('source') == 'import' else 'local',
        platform=value.get('platform') if value.get('platform') in ('web', 'native') else 'native',
        parameters=parameters(value['parameters']),
        metrics={key: None if value.get('metrics', {}).get(key) is None else
                 number(value['metrics'][key]) for key in METRICS},
        replay=dict(available=False, reason='not-recorded'), changes=[])
    from .replay_identity import nickname, sharing_code
    result['combat_duration']=None if value.get('combat_duration') is None else number(value['combat_duration'],0,result['duration'])
    result['shared_by'] = nickname(value.get('shared_by', ''))
    result['shared_code'] = sharing_code(value.get('shared_code',''))
    if type(value.get('recording')) is bool:
        result['recording']=value['recording']
        if not value['recording']:result['replay']['reason']='disabled'
    replay = value.get('replay', {})
    if not isinstance(replay, dict):
        raise ValueError('Invalid replay reference')
    replay_id = replay.get('id')
    if replay_id is not None:
        result['replay']['id'] = str(uuid.UUID(replay_id))
    if 'format' in replay:
        if replay['format'] not in ('native','web'):raise ValueError('Invalid replay format')
        result['replay']['format']=replay['format']
    if value.get('parent') is not None:
        parent = value['parent']
        result['parent'] = dict(id=str(uuid.UUID(parent['id'])), at=number(parent['at'], 0, 7*86400))
    # Random seeds describe this battle only; no host, paths or account metadata.
    result['seed'] = seed_string(value.get('seed', '0'))
    result['room_seed'] = number(value.get('room_seed', 0), 0, 2**32-1, True)
    rooms = value.get('rooms', [])
    if not isinstance(rooms, list) or len(rooms) > 1000:
        raise ValueError('Too many visited rooms')
    result['rooms'] = [dict(role=text(room['role'], 80),
        seed=seed_string(room['seed']),
        entered=number(room['entered'], 0, 7*86400)) for room in rooms]
    layout = value.get('turret_layout', [])
    if not isinstance(layout, list) or len(layout) > 50:
        raise ValueError('Invalid turret layout')
    result['turret_layout'] = []
    for slot in layout:
        if slot.get('row') not in ('front', 'upper', 'lower') or slot.get('ammo') not in ('bossmissile', 'csbullet', 'scattershot'):
            raise ValueError('Invalid turret slot')
        result['turret_layout'].append(dict(id=number(slot['id'], 1, 2**31-1, True), row=slot['row'], ammo=slot['ammo']))
    changes = value.get('changes', [])
    if not isinstance(changes, list) or len(changes) > 1000:
        raise ValueError('Too many configuration changes')
    for change in changes:
        result['changes'].append(dict(at=number(change['at'], 0, 7*86400), parameters=parameters(change['parameters'])))
    result['rules_hash'] = fingerprint(result['parameters'])
    return result


class RecordStore:
    def __init__(self, directory=None):
        self.directory = Path(directory or os.environ.get('SB_BATTLE_RECORDS_PATH') or paths.USERDATA / 'battles')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'records.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, started REAL, updated REAL, owner INTEGER, document TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS records_started ON records(started DESC)')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(str(self.path), timeout=5)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def save(self, document, owner=None):
        document = validate(document)
        with self.connect() as db:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,owner=excluded.owner,document=excluded.document',
                       (document['id'], document['started'], time.time(), owner or os.getpid(), encoded(document).decode('utf8')))
        return document

    @staticmethod
    def recovered(document, updated, owner):
        if document['result'] == 'active' and time.time() - updated > 30:
            # Every active session writes a heartbeat. A dead/stalled writer
            # leaves its last durable snapshot as an interrupted battle.
            document['result'] = 'interrupted'
        return document

    def get(self, identifier):
        identifier = str(uuid.UUID(identifier))
        with self.connect() as db:
            row = db.execute('SELECT document,updated,owner FROM records WHERE id=?', (identifier,)).fetchone()
        if row is None:
            raise ValueError('Battle record not found')
        return self.recovered(json.loads(row[0]), row[1], row[2])

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT document,updated,owner FROM records ORDER BY started DESC LIMIT 10000').fetchall()
        return [self.recovered(json.loads(row[0]), row[1], row[2]) for row in rows]

    def finish_owner(self, owner):
        """Called only after the launcher has waited for its child to exit."""
        with self.connect() as db:
            for identifier, blob in db.execute('SELECT id,document FROM records WHERE owner=?', (owner,)).fetchall():
                document = json.loads(blob)
                if document['result'] == 'active':
                    document['result'] = 'interrupted'
                    db.execute('UPDATE records SET document=?,updated=? WHERE id=?',
                               (encoded(document).decode('utf8'), time.time(), identifier))

    def delete(self, identifiers):
        """Delete completed local records and unreferenced replay payloads only."""
        if not isinstance(identifiers,list) or not 1<=len(identifiers)<=1000:raise ValueError('Select between 1 and 1000 battles')
        identifiers=[str(uuid.UUID(value)) for value in identifiers]
        if len(set(identifiers))!=len(identifiers):raise ValueError('Duplicate battle selection')
        removed=[];chunk_files=[]
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            documents={row[0]:(json.loads(row[1]),row[2],row[3]) for row in db.execute('SELECT id,document,updated,owner FROM records')}
            for identifier in identifiers:
                if identifier not in documents:raise ValueError('Battle record not found')
                doc,updated,owner=documents[identifier]
                if self.recovered(dict(doc),updated,owner)['result']=='active':raise ValueError('Finish the battle before deleting it')
            candidates={documents[value][0].get('replay',{}).get('id') for value in identifiers}
            remaining={doc.get('replay',{}).get('id') for key,(doc,*_) in documents.items() if key not in identifiers}
            journals=self.directory/'client-journals.sqlite3'
            if journals.is_file():
                db.execute('ATTACH DATABASE ? AS client_store',(str(journals),))
                for candidate in candidates-remaining-{None}:
                    state=db.execute('SELECT complete FROM client_store.attempts WHERE id=?',(candidate,)).fetchone()
                    if state and state[0]:
                        chunk_files.extend(row[0] for row in db.execute('SELECT data FROM client_store.chunks WHERE id=?',(candidate,)))
                        db.execute('DELETE FROM client_store.chunks WHERE id=?',(candidate,))
                        db.execute('DELETE FROM client_store.attempts WHERE id=?',(candidate,))
                # Content-addressed browser chunks can be shared by multiple recordings.
                retained={row[0] for row in db.execute('SELECT data FROM client_store.chunks WHERE substr(data,1,6)=?',(b'SBJC1:',))}
                chunk_files=[value for value in chunk_files if value not in retained]
            db.executemany('DELETE FROM records WHERE id=?',[(value,) for value in identifiers])
            removed=identifiers
        for identifier in removed:
            (self.directory/(identifier+'.server.jsonl.gz')).unlink(missing_ok=True)
        from tools.client_replay import CHUNK_FILE
        for value in chunk_files:
            if value.startswith(CHUNK_FILE):
                digest=value[len(CHUNK_FILE):].decode('ascii')
                if re.fullmatch('[0-9a-f]{64}',digest):(self.directory/'client-chunks'/(digest+'.z')).unlink(missing_ok=True)
        return dict(deleted=removed)

    def export(self, identifiers):
        if not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 128 or len(set(identifiers)) != len(identifiers):
            raise ValueError('Select between 1 and 128 battles')
        documents = [self.get(identifier) for identifier in identifiers]
        if any(d['result'] == 'active' for d in documents):
            raise ValueError('Finish the battle before exporting it')
        from tools.replay_bundle import export_battle
        packed = [export_battle(self,d) for d in documents]
        blobs = [item[0] for item in packed]
        # Every export must fit the corresponding importer, including the
        # expanded size of compressed ZIPs.
        if sum(map(len, blobs)) > MAX_IMPORT or sum(item[1] for item in packed)>MAX_EXPANDED_IMPORT:
            raise ValueError('Export is too large; select fewer battles')
        if len(documents) == 1:
            return filename(documents[0]), blobs[0]
        output = io.BytesIO()
        names = set()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            for document, blob in zip(documents, blobs):
                name = filename(document)
                if name in names:
                    name = name[:-9] + '-' + document['id'][:8] + '.sbreplay'
                names.add(name)
                archive.writestr(name, blob)
        if output.tell() > MAX_IMPORT:
            raise ValueError('Export is too large; select fewer battles')
        return 'StarBreak-Battles-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.zip', output.getvalue()

    def remember_share_code(self, identifier, code, author=None):
        from .replay_identity import sharing_code, nickname
        identifier=str(uuid.UUID(identifier));code=sharing_code(code)
        with self.connect() as db:
            row=db.execute('SELECT document FROM records WHERE id=?',(identifier,)).fetchone()
            if row is None:raise ValueError('Battle not found')
            value=json.loads(row[0])
            if not value.get('shared_code'):
                value['shared_code']=code
                if author is not None:value['shared_by']=nickname(author)
                db.execute('UPDATE records SET document=? WHERE id=?',(encoded(value).decode(),identifier))

    def import_bytes(self, data, shared_code=None, max_expanded=None):
        if not data or len(data) > MAX_IMPORT:
            raise ValueError('Battle import exceeds 256 MB')
        if data[:2] == b'PK':
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                entries = archive.infolist()
                if not 1 <= len(entries) <= 128 or sum(e.file_size for e in entries) > MAX_IMPORT:
                    raise ValueError('Too many records or archive exceeds 256 MB')
                if len({e.filename for e in entries})!=len(entries) or any(not re.fullmatch(r'[^/\\\x00]+\.sbreplay', e.filename) or e.flag_bits & 1 for e in entries):
                    raise ValueError('Invalid battle archive')
                blobs = [archive.read(e) for e in entries]
        else:
            blobs = [data]
        from .replay_identity import sharing_code
        original_code=sharing_code(shared_code)
        from tools.replay_bundle import read_battle, client_chunks
        from tools.client_replay import ClientJournals, MAX_BYTES
        from .replay import JournalWriter
        documents = []
        expanded = 0
        for blob in blobs:
            document, client, server, size = read_battle(blob,max_expanded=max_expanded)
            expanded += size
            if expanded>MAX_EXPANDED_IMPORT:raise ValueError('Expanded battle data exceeds 512 MB')
            if document['result'] == 'active':
                document['result'] = 'interrupted'
            document['source'] = 'import'
            if original_code:document['shared_code']=original_code
            documents.append((document,client,server))
        # Validate the complete batch before writing. Re-import never overwrites originals.
        added = 0
        clients = ClientJournals(self.directory)
        created = []
        try:
            with tempfile.TemporaryDirectory(prefix='.import-',dir=self.directory) as temporary, self.connect() as db:
                db.execute('ATTACH DATABASE ? AS replay', (str(clients.path),))
                db.execute('BEGIN IMMEDIATE')
                for document,client,server in documents:
                    existing=db.execute('SELECT document FROM records WHERE id=?',(document['id'],)).fetchone()
                    if existing:
                        previous=json.loads(existing[0])
                        if original_code and previous.get('source')=='import' and not previous.get('shared_code'):
                            previous['shared_code']=original_code
                            db.execute('UPDATE records SET document=? WHERE id=?',(encoded(previous).decode(),document['id']))
                        continue
                    if client:
                        # Independent namespace; never replace an existing local
                        # recording because a shared file reuses its attempt ID.
                        identifier=str(uuid.uuid4());client=dict(client,id=identifier)
                        document['replay']=dict(id=identifier,**({'format':'native'} if client['schema']=='starbreak-native-journal-v1' and document['platform']=='web' else {}))
                        chunks=list(client_chunks(client));total=sum(c[3] for c in chunks)
                        if total>MAX_BYTES:raise ValueError('Client recording exceeds 128 MB')
                        header={k:v for k,v in client.items() if k!='ops'}
                        db.execute('INSERT INTO replay.attempts VALUES(?,?,?,?,1)',(identifier,encoded(header).decode(),len(chunks),total))
                        db.executemany('INSERT INTO replay.chunks VALUES(?,?,?,?)',[(identifier,s,d,c) for s,d,c,_ in chunks])
                        if server:
                            staged=Path(temporary)/(document['id']+'.gz')
                            writer=JournalWriter(staged,dict({k:v for k,v in server.items() if k!='frames'},attempt=identifier))
                            try:
                                for frame in server['frames']:writer.frame(frame)
                            finally:writer.close()
                            destination=self.directory/(document['id']+'.server.jsonl.gz')
                            publish_replay(staged,destination);created.append(destination)
                    else:
                        document['replay']=dict(available=False,reason='not-recorded')
                    db.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                        (document['id'],document['started'],time.time(),os.getpid(),encoded(document).decode()))
                    added+=1
        except Exception:
            for path in created:path.unlink(missing_ok=True)
            raise
        return dict(imported=added, duplicates=len(documents)-added)


def publish_replay(staged, destination):
    """Publish without replacing an existing replay, including on browser FS."""
    link=getattr(os,'link',None)
    if link is not None:
        try:
            link(staged,destination)
            return
        except NotImplementedError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOSYS,errno.EPERM,errno.EXDEV,errno.EOPNOTSUPP):raise
    # The index transaction exposes this file only after the copy succeeds.
    # Exclusive creation and cleanup preserve collision/rollback guarantees.
    created=False
    try:
        with Path(staged).open('rb') as source,Path(destination).open('xb') as target:
            created=True
            shutil.copyfileobj(source,target)
    except BaseException:
        if created:Path(destination).unlink(missing_ok=True)
        raise


def filename(document):
    stamp = datetime.fromtimestamp(document['started']).strftime('%Y%m%d-%H%M%S')
    safe_map = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', document['map_title']).strip('. ')
    return '{}-{}-{}.sbreplay'.format(CLASSES[document['shell']], safe_map, stamp)


def snapshot(session):
    settings = game_settings.load()
    map_id = str(getattr(session.options, 'map', None) or getattr(session, 'map_id', 'egy_hulk'))
    row = session.server.class_loadouts.get(int(session.options.player_type), {})
    # Descriptions are the bytes already sent at login. Reading a newly edited
    # override file here would incorrectly claim next-launch speeds were live.
    items, speeds = [], {}
    descriptions = getattr(session.server, 'item_descriptions', {})
    for equipped in row.get('items_', []):
        kind = equipped['type_']
        description = descriptions.get(kind)
        if description:
            projectiles = [dict(index=i, speed=int(p['speed_'])) for i, p in enumerate(description.get('obj_', [])) if 'speed_' in p]
            items.append(dict(type=kind, name=description.get('name_', str(kind)), base=description.get('statboosts_', []), projectiles=projectiles))
            if projectiles:
                speeds[str(kind)] = {str(p['index']): p['speed'] for p in projectiles}
    live_balance = getattr(getattr(session, 'balance', None), 'values', None)
    return parameters(dict(loadout=row, items=items, balance=live_balance or game_settings.balance(map_id, settings),
        drops=game_settings.drop_settings(map_id, settings),
        skip_intro=getattr(session, 'skip_intro', game_settings.skip_intro(map_id, settings)),
        missile_delay_ms=round(getattr(getattr(session, 'projectiles', None), 'missile_delay_seconds', game_settings.missile_delay_ms(map_id, settings)/1000.)*1000),
        turrets=getattr(session, 'turret_counts', {}), missiles=getattr(session, 'turret_missiles', {}),
        projectile_speeds=speeds))


class BattleRecorder:
    def __init__(self, session, store):
        self.session, self.store = session, store
        self.identifier = str(uuid.uuid4())
        self.document = None
        self.elapsed = 0.
        self.combat_elapsed=0.;self.combat_started=False
        self.last_saved = -10.
        self.settings_stamp = None
        self.pending_damage = 0
        self.pending_kills = 0
        self.pending_generators = 0
        self.closed = False
        self.player_acted = False
        self.saved = False
        self._metric_previous = {}
        self._metric_room = None
        self._visited_room = None
        self.boundary = threading.RLock()
        self.bind(session)

    def bind(self, session):
        from .battle_metrics import bind
        self.session = session
        self.combat_observer = bind(session, self)

    def tick(self, dt):
        if self.closed:
            return
        session = self.session
        run = getattr(session, 'run', None)
        player = (session.bridge.snapshot()[0] if getattr(session, '_ready', False) else None) if run is not None else getattr(session, 'player', None)
        if run is None and self.document is not None and getattr(session,'bridge',None) is not None:
            observed=session.bridge.snapshot()[0]
            # Some controllers clear their selected target when it dies.
            # The local player's feedback still owns the battle result/metrics.
            if observed is not None and (player is None or not observed.alive):player=observed
        if self.document is None:
            if player is None or not player.alive:
                return
            map_id = str(getattr(session.options, 'map', 'egy_hulk'))
            try:
                version = json.loads((paths.ROOT / 'data/version.json').read_text(encoding='utf-8-sig'))['version']
            except (OSError, ValueError, KeyError):
                version = 'development'
            self.document = dict(schema=SCHEMA, id=self.identifier, started=time.time(), duration=0.,
                shell=int(session.options.player_type), map=map_id,
                map_title=game_settings.map_info(map_id)['title'], version=version, result='active',
                platform='web' if getattr(session.options, 'web_client', False) else 'native',
                parameters=snapshot(session), seed=int(getattr(session, 'seed', getattr(run, 'seed', 0))),
                room_seed=int(getattr(player, 'raw', {}).get('room_seed', getattr(session.options, 'room_roll_seed', 0)) or 0),
                turret_layout=[dict(id=s.objid, row=s.row, ammo=s.ammo) for s in getattr(session, 'turret_layout', {}).values()],
                metrics={key: None for key in METRICS}, changes=[], rooms=[])
            attempt = os.environ.get('SB_REPLAY_ATTEMPT')
            self.document['recording']=os.environ.get('SB_RECORD_REPLAY')=='1'
            if attempt and (self.document['platform'] == 'web' or os.environ.get('SB_RECORD_REPLAY')=='1'):
                self.document['replay'] = dict(id=str(uuid.UUID(attempt)))
            if map_id in ('egy_hulk', 'hulk_normal') or self.combat_observer is not None:
                self.document['metrics'].update(damage_dealt=0, kills=0)
            if map_id in ('egy_hulk', 'hulk_normal', 'egy_run'):
                self.document['metrics']['generators_destroyed'] = 0
        # Menus, gear changes, gravity, incoming damage and automatic attacks
        # are not player participation. Both clients latch accepted gameplay
        # input before telemetry throttling, so even a short tap is retained.
        bridge = getattr(session, 'bridge', None)
        if bridge is not None:
            observed, keys = bridge.snapshot()
            if observed is not None and keys.get('gameplay') is True:
                self.player_acted = True
        self.elapsed += max(0., min(float(dt), 1.))
        self.document['duration'] = round(self.elapsed, 3)
        if self.document['map'] in ('egy_hulk','hulk_normal'):
            step=max(0.,min(float(dt),1.))
            if self.document['parameters']['skip_intro']:
                self.combat_elapsed=self.elapsed
                self.document['combat_duration']=round(self.combat_elapsed,3)
            else:
                ship=getattr(session,'ship_controller',None)
                arrived=getattr(ship,'arrival_time',None)
                if arrived is not None:
                    boss_time=float(getattr(session,'boss_time',0))
                    if self.combat_started:self.combat_elapsed+=step
                    elif getattr(session,'phase',None) in ('deploying','fighting','death','done') and boss_time>=arrived:
                        self.combat_started=True;self.combat_elapsed+=min(step,max(0.,boss_time-arrived))
                    self.document['combat_duration']=round(self.combat_elapsed,3)
        room_context = getattr(getattr(session, 'bridge', None), 'native_context', None) or {}
        token = ((getattr(player, 'raw', {}) or {}).get('room_seed'),
                 room_context.get('room_token'), (getattr(player, 'raw', {}) or {}).get('life_id'))
        if player is not None and token != self._metric_room:
            self._metric_previous = {}
            self._metric_room = token
        if run is not None and player is not None:
            instance = session.instance
            visit = (instance.role, room_context.get('room_token'), getattr(instance, 'visits', 0))
            if visit != self._visited_room and len(self.document['rooms']) < 1000:
                self.document['rooms'].append(dict(role=instance.role, seed=str(instance.seed), entered=round(self.elapsed, 3)))
                self._visited_room = visit
        metrics = getattr(player, 'raw', {}).get('battle_metrics', {}) if player is not None else {}
        if isinstance(metrics, dict) and metrics.get('protocol') == 'player-metrics-v1':
            for key in ('health_packs', 'ammo_packs', 'healing', 'damage_taken'):
                value = metrics.get(key)
                if type(value) is int and 0 <= value <= 10**12:
                    # Counters are cumulative within one client world. A new
                    # world may reset them; never subtract previously counted data.
                    previous = self._metric_previous.get(key, 0)
                    self.document['metrics'][key] = (self.document['metrics'][key] or 0) + (value-previous if value >= previous else value)
                    self._metric_previous[key] = value
        if self.combat_observer is not None:
            self.combat_observer.tick()
            if self.combat_observer.failed:
                self.document['metrics'].update(damage_dealt=None, kills=None)
                self.document['metrics']['generators_destroyed'] = None
        if self.document['metrics']['damage_dealt'] is not None:
            self.document['metrics']['damage_dealt'] += self.pending_damage
            self.document['metrics']['kills'] += self.pending_kills
        self.pending_damage = self.pending_kills = 0
        if self.document['metrics']['generators_destroyed'] is not None:
            self.document['metrics']['generators_destroyed'] += self.pending_generators
        self.pending_generators = 0
        # A midboss's death/done phase is not the result of a whole-level run.
        if run is not None:
            boss_death = (session.options.map == 'egy_run' and session.instance.role == 'boss'
                          and getattr(getattr(session, 'current', None), 'phase', None) in ('death', 'done'))
            phase = 'done' if run.completed or boss_death else None
        else:
            phase = getattr(session, 'phase', None)
        if phase == 'failed' or (player is not None and not player.alive):
            self.finish('defeat')
        elif (phase == 'done' or
                phase == 'death' and self.document['map'] in ('egy_hulk', 'hulk_normal') or
                phase == 'defeated' and self.document['map'] in ('jungle_mid', 'jungle_boss')):
            self.finish('victory')
        elif self.elapsed - self.last_saved >= 1 or self.player_acted and not self.saved:
            current = snapshot(session)
            previous = self.document['changes'][-1]['parameters'] if self.document['changes'] else self.document['parameters']
            if current != previous and len(self.document['changes']) < 1000:
                self.document['changes'].append(dict(at=round(self.elapsed, 3), parameters=current))
            if self.player_acted:
                self.store.save(self.document)
                self.saved = True
            self.last_saved = self.elapsed

    def damage(self, value, killed=False):
        if not self.closed:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('Invalid accepted damage')
            self.pending_damage += max(0, value)
            self.pending_kills += int(bool(killed))

    def generator_destroyed(self, count=1):
        if not self.closed:
            self.pending_generators += max(0, int(count))

    def finish(self, result='interrupted'):
        with self.boundary:
            if self.closed:
                return
            if self.document is not None:
                self.document['result'] = result
                if result=='victory':
                    try:
                        from .player_identity import announce
                        announce(self.session,self.document['map_title'])
                    except (OSError,ValueError,AttributeError):pass
                if self.player_acted:
                    self.store.save(self.document)
            self.closed = True


def recording_tail_pending(session):
    """Keep the new ship death presentation after the combat result is final."""
    current=getattr(session,'current',None) or session
    return (getattr(current,'phase',None)=='death' and
            bool(getattr(current,'death_records',())) and
            not getattr(current,'_legacy_victory_presentation',False))


def attach(session):
    """Observe public session boundaries without changing any AI or timestep."""
    if getattr(session.options, 'map', None) in NON_BATTLE_MAPS:
        return None
    directory = os.environ.get('SB_BATTLE_RECORDS_PATH')
    store = RecordStore(directory)
    run = getattr(session, 'run', None)
    recorder = getattr(run, 'battle_recorder', None) if run is not None else None
    if recorder is None:
        recorder = BattleRecorder(session, store)
        if run is not None:
            run.battle_recorder = recorder
    with recorder.boundary:
        recorder.bind(session)
    session.battle_recorder = recorder
    if run is not None and os.environ.get('SB_RECORD_REPLAY')=='1':
        from .run_replay import RunRecording
        try:
            recording=getattr(run,'battle_recording',None)
            if recording is None:
                recording=run.battle_recording=RunRecording(session,recorder)
            recording.attach(session)
            return recorder
        except Exception as error:
            session.log('Battle replay unavailable: '+type(error).__name__)
    original_step, original_stop = session.step, session.stop
    capture = journal = None
    boundary = recorder.boundary
    detached = False
    # Enable the server journal during replay integration. The UI only enables
    # playback after a compatible client recording is present and verified.
    from .replay import SUPPORTED_MAPS
    if os.environ.get('SB_RECORD_REPLAY') == '1' and getattr(session, 'map_id', session.options.map) in SUPPORTED_MAPS:
        from .replay import ServerCapture, JournalWriter
        try:
            capture = ServerCapture(session, keep_frames=False)
            journal = JournalWriter(store.directory / (recorder.identifier+'.server.jsonl.gz'), capture.header)
            capture.sink = journal.frame
            session.prepare = lambda: capture.run('prepare')
        except Exception as error:
            if capture is not None:capture.detach()
            capture = journal = None
            session.log('Battle replay unavailable: ' + type(error).__name__)

    def safely(action, *args):
        try:
            action(*args)
        except Exception as error:
            recorder.closed = True
            session.log('Battle recording unavailable: ' + type(error).__name__ + ': ' + str(error)[:200])

    def close_capture(force=False):
        nonlocal detached
        # stop() may be called from inside a tick. Seal only after that frame
        # has reached the journal, and never race the simulation writer.
        if journal is not None and not detached and capture.active is None and (recorder.closed or capture.failed):
            if not force and not capture.failed and recording_tail_pending(session):return
            safely(journal.close, not capture.failed)
            capture.detach(restore_methods=False)
            detached = True

    def step(dt, *args, **kwargs):
        with boundary:
            stopping = getattr(session, '_stop', None)
            if stopping is not None and stopping.is_set():
                return
            try:
                result = capture.run('step', dt) if capture is not None and not detached else original_step(dt, *args, **kwargs)
                if recorder.session is session:
                    safely(recorder.tick, dt)
                return result
            finally:
                close_capture()

    def stop(*args, **kwargs):
        # Set the stop flag/join before taking the recorder lock: otherwise a
        # full-run worker waiting to enter its tick cannot finish that join.
        result = original_stop(*args, **kwargs)
        with boundary:
            room_transition = getattr(session, '_battle_room_transition', False)
            session._battle_room_transition = False
            if not (run is not None and room_transition):
                safely(recorder.finish)
            close_capture(force=True)
            return result

    session.step, session.stop = step, stop
    return recorder
