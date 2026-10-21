"""Prepare engine-neutral data for replaying native journals in the web engine."""
import base64
import hashlib
from pathlib import Path

from localserver import protocol, pb_layout
from localserver.server import build_hello, build_joined
from tools.native_replay import NativeJournals
from tools.replay_stream import framed


def adapter_digest():
    from tools.web_client import CLIENT_SHA256
    root=Path(__file__).resolve().parents[1]
    value=hashlib.sha256(CLIENT_SHA256.encode())
    for name in ('tools/native_web_client.py','web/native-replay.js'):
        value.update((root/name).read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n'))
    return value.hexdigest()


def check_runner(runner,adapter=None):
    from tools.web_client import CLIENT_SHA256
    expected=adapter or adapter_digest()
    compatible=runner=={'client':CLIENT_SHA256,'adapter':expected}
    if not compatible and runner.get('client')==CLIENT_SHA256:
        import json
        try:
            aliases=json.loads((Path(__file__).resolve().parents[1]/'data/mechanics/portable_replay_compatibility.json').read_text(encoding='utf8'))
            compatible=runner.get('adapter') in aliases.get(expected,[])
        except (OSError,ValueError,TypeError):pass
    if not compatible:
        raise ValueError('Portable takeover requires the matching browser replay adapter')


def supported_runners(value,adapter=None):
    for row in value.get('runners',[]):
        if row['engine']=='native':
            from plugins._native_replay_capture import CLIENTS
            from tools.native_replay import compatible_adapter
            if row['client'] not in CLIENTS or not compatible_adapter(row['adapter']):
                raise ValueError('Portable takeover requires the matching native continuation adapter')
        else:check_runner({k:row[k] for k in ('client','adapter')},adapter)


def append_runner(host,request,clients,source,adapter=None):
    if source.get('schema')=='starbreak-native-journal-v1':
        runner=request.get('runner')
        check_runner(runner,adapter)
        clients.mark_runner(host.branch['id'],source,request['cursor'],runner)
        host.portable_runner=True
        if hasattr(host.server,'options'):host.server.options.web_client=True
        host.session.options.web_client=True


def browser_document(directory, identifier, version, adapter=None, require_exact=False, source=None):
    value = NativeJournals(directory).read(identifier) if source is None else source
    from plugins._native_replay_capture import CLIENTS
    if value['client'] not in CLIENTS:
        raise ValueError('Unsupported original native client version')
    if require_exact:
        from tools.native_replay import compatible_adapter
        if not compatible_adapter(value['adapter']):raise ValueError('Portable takeover requires the matching native replay adapter')
        supported_runners(value,adapter)
    hello = build_hello(dict(id=version.get('id_',version.get('id')), timestamp=version.get('timestamp_',version.get('timestamp'))))
    # The browser executable's protobuf uses the same version data, with a
    # distinct optional field for the anonymous offline connection.
    hello['hid'] = 305419896
    wire = dict(pb_layout.default_layouts().wire_overrides)
    wire[('Hello', 'hid_')] = 5
    layouts = pb_layout.Layouts(wire_overrides=wire)
    # This is the same Hello serializer used by the local browser server.
    from localserver.server import MT_HELLO, MT_JOINED
    from tools.web_client import CLIENT_SHA256
    return dict(value,runner=dict(client=CLIENT_SHA256,adapter=adapter or adapter_digest()),bootstrap=dict(
        assets=['ui','base'],
        hello=base64.b64encode(framed(MT_HELLO, layouts.encode('Hello', hello))).decode(),
        joined=base64.b64encode(framed(MT_JOINED, layouts.encode('Joined', build_joined()))).decode(),
        key=base64.b64encode(protocol.random_string(0,64)).decode()))


def availability(directory, identifiers):
    import json
    from plugins._native_replay import SCHEMA
    from plugins._native_replay_capture import CLIENTS
    store=NativeJournals(directory)
    result={}
    with store.connect() as db:
        for start in range(0,len(identifiers),500):
            selected=identifiers[start:start+500]
            if not selected:continue
            for identifier,header,complete in db.execute('SELECT id,header,complete FROM attempts WHERE id IN ('+','.join('?' for _ in selected)+')',selected):
                header=json.loads(header)
                if header.get('schema')==SCHEMA:
                    supported=header.get('client') in CLIENTS
                    result[identifier]=dict(id=identifier,available=bool(complete and supported),engine='browser',
                        reason='ready' if complete and supported else 'recording' if not complete else 'incompatible')
    return result
