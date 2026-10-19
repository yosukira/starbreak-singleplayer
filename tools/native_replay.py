"""Native execution journals use the same atomic local store as browser tapes."""
import hashlib
from pathlib import Path
import re
import uuid

from plugins import _native_replay as native
from tools.client_replay import ClientJournals,integer


def adapter_digest():
    root=Path(__file__).resolve().parents[1]
    value=hashlib.sha256()
    for name in ('_native_replay.py','_native_replay_hooks.py','_native_replay_capture.py','_native_replay_player.py'):
        value.update((root/'plugins'/name).read_bytes())
    return value.hexdigest()


def document(value):
    if not isinstance(value,dict)or value.get('schema')!=native.SCHEMA:
        raise ValueError('Unsupported native client recording')
    viewport=value['viewport']
    if not isinstance(viewport,list)or len(viewport)!=2:raise ValueError('Invalid native replay viewport')
    for key in('client','adapter'):
        if not isinstance(value.get(key),str)or not re.fullmatch('[0-9a-f]{64}',value[key]):
            raise ValueError('Invalid native replay version digest')
    if value.get('preferences')is not None:raise ValueError('Native recordings do not include browser preferences')
    return dict(schema=native.SCHEMA,id=str(uuid.UUID(value['id'])),client=value['client'],adapter=value['adapter'],
        preferences=None,viewport=[integer(v,1,16384)for v in viewport],ops=native.validate_operations(value['ops']))


class NativeJournals(ClientJournals):
    schema=native.SCHEMA
    operation=staticmethod(native.operation)

    def begin(self,identifier,client,adapter,preferences,viewport):
        if preferences.get('content')is not None:raise ValueError('Native preferences must remain on the local client')
        for digest in(client,adapter):
            if not isinstance(digest,str)or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid native engine digest')
        return super().begin(identifier,client,adapter,preferences,viewport)

    def read(self,identifier,require_complete=True):
        value=super().read(identifier,require_complete=require_complete)
        return document(value)if require_complete else value

    def statuses(self,identifiers,adapter,client=None):
        result=super().statuses(identifiers,adapter)
        if client is None:
            for row in result.values():row.update(available=False,reason='client-unverified')
            return result
        with self.connect()as db:
            import json
            for identifier,row in result.items():
                header=json.loads(db.execute('SELECT header FROM attempts WHERE id=?',(identifier,)).fetchone()[0])
                if header.get('client')!=client:row.update(available=False,reason='incompatible')
        return result
