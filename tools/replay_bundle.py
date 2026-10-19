"""Portable battle files: bounded JSON journals, never executable snapshots."""
import gzip
import base64
import io
import json
import re
import struct
import uuid

from localserver import replay
from tools.client_replay import ClientJournals, SCHEMA as CLIENT_SCHEMA, operation, integer, encode
from tools.game_preferences import GamePreferences

MAGIC = b'SBREPLAY1\n'
MAGIC_COMPACT = b'SBREPLAY2\n'
MAX_EXPANDED = 272 * 1024 * 1024


def transform_wire(client):
    """Reversible storage transform; runtime packets stay byte-for-byte exact.

    The offline client's fixed transport cipher hides repetitions from gzip.
    Applying the same stream on write/read exposes them for storage only. Each
    recorded network chunk keeps its size and boundary, including split Hellos
    and independent connections. This never changes clocks, inputs or frames.
    """
    from localserver.protocol import RC4, random_string
    if client.get('schema')!=CLIENT_SCHEMA:return client
    cipher=RC4(random_string(0,64));header=bytearray();remaining=None;rows=[]
    for row in client['ops']:
        if row[0]=='open':cipher=RC4(random_string(0,64));header=bytearray();remaining=None
        if row[0]!='net':rows.append(row);continue
        data=base64.b64decode(row[1],validate=True);output=bytearray();offset=0
        if len(header)<5:
            size=min(5-len(header),len(data));header.extend(data[:size]);output.extend(data[:size]);offset=size
            if len(header)==5:
                remaining=struct.unpack('>i',header[1:])[0]
                if header[0]!=1 or not 0<=remaining<=65536:raise ValueError('Invalid replay Hello boundary')
        if remaining:
            size=min(remaining,len(data)-offset);output.extend(data[offset:offset+size]);remaining-=size;offset+=size
        if offset<len(data):output.extend(cipher.crypt(data[offset:]))
        rows.append(['net',base64.b64encode(output).decode('ascii')])
    return dict(client,ops=rows)


def pack_battle(value):
    """Keep legacy files readable; select the lossless codec only when useful."""
    from localserver.battle_records import encoded
    raw=encoded(value)
    if len(raw)>MAX_EXPANDED:raise ValueError('Replay is too large; select fewer battles')
    standard=MAGIC+gzip.compress(raw,compresslevel=6,mtime=0)
    if value.get('client',{}).get('schema')!=CLIENT_SCHEMA:return standard,len(raw)
    try:
        packed=encoded(dict(value,encoding='wire-clear-v1',client=transform_wire(value['client'])))
    except (ValueError,TypeError,KeyError,struct.error):return standard,len(raw)
    if len(packed)>MAX_EXPANDED:return standard,len(raw)
    compact=MAGIC_COMPACT+gzip.compress(packed,compresslevel=6,mtime=0)
    if len(standard)-len(compact)<max(1024,len(standard)*.05):return standard,len(raw)
    return compact,max(len(raw),len(packed))


def digest(value):
    if not isinstance(value,str) or not re.fullmatch('[0-9a-f]{64}',value):
        raise ValueError('Invalid replay version digest')
    return value


def client_document(value):
    from tools import native_replay
    if isinstance(value,dict)and value.get('schema')==native_replay.native.SCHEMA:
        return native_replay.document(value)
    if not isinstance(value,dict) or value.get('schema') != CLIENT_SCHEMA:
        raise ValueError('Unsupported client recording')
    viewport=value['viewport']
    if not isinstance(viewport,list) or len(viewport)!=2:
        raise ValueError('Invalid replay viewport')
    prefs=value.get('preferences')
    if prefs is not None:
        prefs=GamePreferences.validate(dict(key='LocalStorage.pb',revision=0,content=prefs))['content']
    rows=value['ops']
    if not isinstance(rows,list) or len(rows)>3000000:
        raise ValueError('Client recording is too long')
    return dict(schema=CLIENT_SCHEMA,id=str(uuid.UUID(value['id'])),
        client=digest(value['client']),adapter=digest(value['adapter']),preferences=prefs,
        viewport=[integer(v,1,16384) for v in viewport],ops=[operation(row) for row in rows])


def server_document(value):
    from localserver import run_replay
    if isinstance(value,dict) and value.get('schema')==run_replay.SCHEMA:
        result=run_replay.validate(value)
        result['attempt']=str(uuid.UUID(value['attempt']))
        return result
    if not isinstance(value,dict) or value.get('schema')!=replay.SCHEMA or value.get('map') not in replay.SUPPORTED_MAPS:
        raise ValueError('Unsupported server recording')
    result=dict(schema=replay.SCHEMA,engine=digest(value['engine']),map=value['map'],
        attempt=str(uuid.UUID(value['attempt'])),seed=str(integer(int(value['seed']),0,2**64-1)),
        shell=integer(value['shell'],1,5),worldid=integer(value['worldid'],1,2**31-1),
        world_sequence=integer(value.get('world_sequence',1),1,10000),
        settings=replay.gameplay_settings(value['settings']),options={},frames=[])
    if 'room_seed' in value:
        result['room_seed']=str(integer(int(value['room_seed']),0,2**64-1))
    for group,keys in (('turrets',('front','upper','lower')),('missiles',('upper','lower'))):
        source=value[group]
        if source is None:result[group]=None
        elif isinstance(source,dict):result[group]={key:None if source.get(key) is None else integer(source[key],0,100) for key in keys}
        else:raise ValueError('Invalid replay turret layout')
    items=value['item_types']
    if not isinstance(items,list) or len(items)>10000:raise ValueError('Too many replay item types')
    result['item_types']=[integer(v,1,2**31-1) for v in items]
    for key,v in value.get('options',{}).items():
        if key not in replay.OPTIONS:continue
        if key in ('live_demo_wait','live_player_gap'):v=replay.finite(v,0,3600)
        elif type(v) is not bool:raise ValueError('Invalid replay option')
        result['options'][key]=v
    frames=value['frames']
    if not isinstance(frames,list) or not 1<=len(frames)<=replay.MAX_FRAMES:raise ValueError('Invalid replay frame count')
    previous=0
    for index,row in enumerate(frames):
        action='prepare' if index==0 else 'step'
        if row.get('action')!=action:raise ValueError('Invalid replay lifecycle')
        wall=replay.finite(row['wall'],previous,7*86400);previous=wall
        clean=dict(action=action,dt=replay.finite(row['dt'],0,1),wall=wall,
            packets=integer(row['packets'],0,10000),sha256=digest(row['sha256']),calls=[])
        calls=row['calls']
        if not isinstance(calls,list) or len(calls)>replay.MAX_CALLS:raise ValueError('Too many replay observations')
        for call in calls:
            method=call['method']
            if method not in (*replay.METHODS,'datagrams') or not isinstance(call.get('args'),list):
                raise ValueError('Invalid replay observation')
            arguments=replay.call_arguments(method,call['args'])
            raw=call['value']
            restored=replay.call_value(method,raw,reading=True)
            observed=replay.call_value(method,restored)
            # Validation constructs a PlayerState with a local receive clock.
            # Retain the recorded age exactly instead of adding validation time.
            if method=='snapshot' and observed[0] is not None:
                observed[0]['age']=replay.finite(raw[0].get('age',0),0,86400)
            if method=='consume_interactions':
                for old,new in zip(raw,observed):
                    if new is not None:new['age']=replay.finite(old.get('age',0),0,86400)
            clean['calls'].append(dict(method=method,args=arguments,value=observed))
        if 'settings' in row:clean['settings']=replay.gameplay_settings(row['settings'])
        result['frames'].append(clean)
    return result


def export_battle(store, document):
    from localserver.battle_records import encoded, MAX_RECORD
    metadata=encoded(document)
    if len(metadata)>MAX_RECORD:raise ValueError('Record is too large; select fewer battles')
    attempt=document.get('replay',{}).get('id')
    if not attempt:
        return metadata,len(metadata)
    from tools.native_replay import NativeJournals
    native_format=document.get('replay',{}).get('format',document.get('platform'))=='native'
    clients=(NativeJournals if native_format else ClientJournals)(store.directory)
    try:client=clients.read(attempt)
    except ValueError:
        return metadata,len(metadata)
    value=dict(record=document,client=client_document(client))
    path=store.directory/(document['id']+'.server.jsonl.gz')
    if path.is_file():
        try:value['server']=server_document(replay.read_journal(path))
        except ValueError:pass  # A usable client replay does not require takeover data.
    return pack_battle(value)


def read_battle(blob):
    from localserver.battle_records import validate, MAX_RECORD
    if blob.startswith((MAGIC,MAGIC_COMPACT)):
        with gzip.GzipFile(fileobj=io.BytesIO(blob[len(MAGIC):])) as handle:
            raw=handle.read(MAX_EXPANDED+1)
        if len(raw)>MAX_EXPANDED:raise ValueError('Expanded replay is too large')
        value=json.loads(raw)
        record=validate(value['record']);client=client_document(value['client'])
        if blob.startswith(MAGIC_COMPACT):
            if value.get('encoding')!='wire-clear-v1':raise ValueError('Unsupported replay compression')
            client=transform_wire(client)
        from plugins._native_replay import SCHEMA as NATIVE_SCHEMA
        if (client['schema']==NATIVE_SCHEMA)!=(record['replay'].get('format',record['platform'])=='native'):
            raise ValueError('Battle and recording editions do not match')
        server=server_document(value['server']) if 'server' in value else None
        if record.get('replay',{}).get('id')!=client['id'] or (server and server['attempt']!=client['id']):
            raise ValueError('Recording does not belong to this battle')
        return record,client,server,len(raw)
    if len(blob)>MAX_RECORD:raise ValueError('Single record exceeds 8 MB')
    return validate(json.loads(blob)),None,None,len(blob)


def client_chunks(document):
    """Canonical chunks, shared by the atomic cross-database importer."""
    import hashlib
    import zlib
    rows=[];size=0;sequence=0
    for row in document['ops']:
        length=len(encode(row))+1
        if rows and (size+length>450000 or len(rows)>=1000):
            raw=encode(dict(sequence=sequence,ops=rows,end=False))
            yield sequence,hashlib.sha256(raw).hexdigest(),zlib.compress(raw,3),len(raw)
            sequence+=1;rows=[];size=0
        rows.append(row);size+=length
    raw=encode(dict(sequence=sequence,ops=rows,end=True))
    yield sequence,hashlib.sha256(raw).hexdigest(),zlib.compress(raw,3),len(raw)
