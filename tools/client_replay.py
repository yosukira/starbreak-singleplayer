"""Bounded, transactional journals for the pinned browser client's execution.

Only typed gameplay inputs, clocks, random bytes, received game packets and
semantic player checks are accepted. No outgoing login payloads, arbitrary
metadata, filesystem paths, executable state or browser storage are collected.
"""
import base64
import hashlib
import json
import math
import re
from pathlib import Path
import sqlite3
from contextlib import contextmanager
import uuid
import zlib

from tools.game_preferences import GamePreferences

SCHEMA = 'starbreak-client-journal-v1'
MAX_BYTES = 128 * 1024 * 1024
MAX_CHUNK = 512 * 1024
MAX_OPS = 4096


def encode(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False).encode('utf8')


def integer(value, low=-(2**31), high=2**32-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid client replay integer')
    return value


def finite(value, low=0, high=10**15):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError('Invalid client replay number')
    return value


def operation(row):
    if not isinstance(row, list) or not row:
        raise ValueError('Invalid client replay operation')
    kind, *values = row
    if kind == 'exit' and len(values) == 1:
        return [kind, integer(values[0], 0, 255)]
    if kind == 'preferences' and len(values) == 1:
        clean = GamePreferences.validate(dict(key='LocalStorage.pb', revision=0, content=values[0]))['content']
        if clean != values[0]:
            raise ValueError('Replay options contain non-game fields')
        return [kind, clean]
    if kind == 'loop' and len(values) == 1:
        return [kind, integer(values[0], 0, 1)]
    if kind in ('clock', 'time', 'tick') and len(values) == 1:
        return [kind, finite(values[0])]
    if kind == 'random' and len(values) == 1:
        return [kind, integer(values[0], 0, 255)]
    if kind == 'input' and len(values) == 4:
        return [kind, *(integer(v) for v in values)]
    if kind == 'control' and len(values) == 2:
        control, args = values
        counts = dict(key=2, mouse=2, button=4, window=3, reset=0, resize=0)
        if control not in counts or not isinstance(args, list) or len(args) != counts[control]:
            raise ValueError('Invalid replay control')
        return [kind, control, [integer(int(v) if type(v) is bool else v) for v in args]]
    if kind == 'asset' and len(values) == 1 and isinstance(values[0], str) and re.fullmatch(r'(?:texture|data):[A-Za-z0-9_.-]{1,240}', values[0]):
        return [kind, values[0]]
    if kind == 'frame' and len(values) == 1:
        return [kind, integer(values[0], 0, 10000)]
    if kind == 'viewport' and len(values) == 2:
        return [kind, *(integer(v, 1, 16384) for v in values)]
    if kind == 'state' and len(values) == 1 and isinstance(values[0], list) and len(values[0]) in (2, 14):
        return [kind, [integer(v) for v in values[0]]]
    if kind == 'net' and len(values) == 1 and isinstance(values[0], str):
        if len(values[0]) > 350000:
            raise ValueError('Client replay packet too large')
        data = base64.b64decode(values[0], validate=True)
        if not data or len(data) > 262144:
            raise ValueError('Invalid client replay packet')
        return [kind, base64.b64encode(data).decode('ascii')]
    if kind in ('open', 'close', 'timer', 'quit') and not values:
        return [kind]
    if kind == 'send' and len(values) == 2:
        # Only a digest of client -> server data; never record its contents.
        return [kind, integer(values[0], 0, 1024*1024), integer(values[1], 0, 2**32-1)]
    raise ValueError('Unsupported client replay operation')


class ClientJournals:
    schema = SCHEMA
    operation = staticmethod(operation)
    def __init__(self, directory):
        self.path = Path(directory) / 'client-journals.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, header TEXT, sequence INTEGER, size INTEGER, complete INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS chunks (id TEXT, sequence INTEGER, digest TEXT, data BLOB, PRIMARY KEY(id,sequence))')

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=5, isolation_level='IMMEDIATE')
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def begin(self, identifier, client, adapter, preferences, viewport):
        identifier = str(uuid.UUID(identifier))
        if not isinstance(viewport, (list, tuple)) or len(viewport) != 2:
            raise ValueError('Invalid client replay viewport')
        clean = None if preferences['content'] is None else GamePreferences.validate(preferences)['content']
        header = dict(schema=self.schema, id=identifier, client=client, adapter=adapter,
                      preferences=clean, viewport=[integer(v, 1, 16384) for v in viewport])
        with self.connect() as db:
            db.execute('INSERT INTO attempts VALUES(?,?,0,0,0)', (identifier, encode(header).decode()))
        return header

    def append(self, identifier, document):
        identifier = str(uuid.UUID(identifier))
        sequence = integer(document.get('sequence'), 0, 10**7)
        rows = document.get('ops')
        end = document.get('end', False)
        if not isinstance(rows, list) or len(rows) > MAX_OPS or type(end) is not bool:
            raise ValueError('Invalid client replay chunk')
        clean = dict(sequence=sequence, ops=[self.operation(row) for row in rows], end=end)
        data = encode(clean)
        if len(data) > MAX_CHUNK:
            raise ValueError('Client replay chunk too large')
        digest = hashlib.sha256(data).hexdigest()
        with self.connect() as db:
            # Acquire the writer lock before the sequence check. Two windows or
            # an HTTP retry cannot append the same sequence twice.
            db.execute('UPDATE attempts SET sequence=sequence WHERE id=?', (identifier,))
            row = db.execute('SELECT sequence,size,complete,header FROM attempts WHERE id=?', (identifier,)).fetchone()
            if row is None:
                raise ValueError('Unknown client replay attempt')
            if json.loads(row[3]).get('schema')!=self.schema:
                raise ValueError('Client recording belongs to another edition')
            previous = db.execute('SELECT digest FROM chunks WHERE id=? AND sequence=?', (identifier, sequence)).fetchone()
            if previous is not None:
                if previous[0] != digest:
                    raise ValueError('Client replay retry changed its data')
                return dict(sequence=sequence+1, complete=bool(row[2]))
            if row[2] or row[0] != sequence:
                raise ValueError('Client replay sequence changed')
            if row[1]+len(data) > MAX_BYTES:
                raise ValueError('Client replay exceeds 128 MB')
            db.execute('INSERT INTO chunks VALUES(?,?,?,?)', (identifier, sequence, digest, zlib.compress(data, 3)))
            db.execute('UPDATE attempts SET sequence=?,size=?,complete=? WHERE id=?',
                       (sequence+1, row[1]+len(data), int(end), identifier))
        return dict(sequence=sequence+1, complete=end)

    def read(self, identifier, require_complete=True):
        identifier = str(uuid.UUID(identifier))
        with self.connect() as db:
            row = db.execute('SELECT header,sequence,complete FROM attempts WHERE id=?', (identifier,)).fetchone()
            if row is None or (require_complete and not row[2]):
                raise ValueError('Client replay is incomplete')
            chunks = db.execute('SELECT sequence,digest,data FROM chunks WHERE id=? ORDER BY sequence', (identifier,)).fetchall()
        if len(chunks) != row[1]:
            raise ValueError('Client replay is damaged')
        header=json.loads(row[0])
        if header.get('schema')!=self.schema:
            raise ValueError('Client recording belongs to another edition')
        operations = []
        for expected, (sequence, digest, compressed) in enumerate(chunks):
            data = zlib.decompress(compressed)
            if sequence != expected or hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('Client replay is damaged')
            document = json.loads(data)
            operations.extend(document['ops'])
        return dict(header, ops=operations)

    def statuses(self, identifiers, adapter, client=None, allow_compatible=False):
        result = {}
        with self.connect() as db:
            for start in range(0, len(identifiers), 500):
                selected = identifiers[start:start+500]
                if not selected:
                    continue
                rows = db.execute('SELECT id,header,complete FROM attempts WHERE id IN ('+','.join('?' for _ in selected)+')', selected)
                for identifier, header, complete in rows:
                    header=json.loads(header)
                    same_client=client is None or header.get('client')==client
                    matches = header.get('schema')==self.schema and header['adapter'] == adapter and same_client
                    trial=(allow_compatible and client is not None and same_client and self.schema==SCHEMA and header.get('schema')==SCHEMA)
                    result[identifier] = dict(available=bool(complete and (matches or trial)),
                        reason='ready' if complete and matches else 'compatible' if complete and trial else 'recording' if not complete else 'incompatible', id=identifier)
        return result

    def fork(self, identifier, cursor, source=None):
        """Copy a verified execution prefix into an independent local journal."""
        source = self.read(identifier) if source is None else source
        if source.get('id')!=identifier:raise ValueError('Replay does not belong to this branch')
        if type(cursor) is not int or not 0 < cursor <= len(source['ops']) or source['ops'][cursor-1] != ['loop', 1]:
            raise ValueError('Take over between complete game iterations')
        target = str(uuid.uuid4())
        self.begin(target, source['client'], source['adapter'],
            dict(key='LocalStorage.pb', revision=0, content=source['preferences']), source['viewport'])
        branch=dict(id=target,sequence=0)
        self.extend_fork(branch,source['ops'][:cursor])
        return branch

    def extend_fork(self, branch, operations):
        target=branch['id'];sequence=branch['sequence'];rows=[];size=0
        for row in operations:
            length = len(encode(row))+1
            if rows and (size+length > 450000 or len(rows) >= 1000):
                self.append(target, dict(sequence=sequence, ops=rows, end=False))
                sequence += 1; rows, size = [], 0
            rows.append(row); size += length
        if rows:
            self.append(target, dict(sequence=sequence, ops=rows, end=False))
            sequence += 1
        branch['sequence']=sequence
        return branch
