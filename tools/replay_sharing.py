"""Player-side replay sharing: validated files and explicit upload requests.

The storage service and deployment configuration live in a separate private
operations repository. No infrastructure credentials are included here.
"""
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from localserver.battle_records import encoded
from tools.replay_bundle import MAGIC, read_battle

MAX_FILE = 32 * 1024 * 1024
MAX_EXPANDED = 96 * 1024 * 1024
ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


def code(value):
    if not isinstance(value, str) or len(value) > 80:
        raise ValueError('Invalid replay sharing code')
    result = re.sub(r'[-\s]', '', value).upper()
    if len(result) != 12 or any(c not in ALPHABET for c in result):
        raise ValueError('Invalid replay sharing code')
    return result


def display_code(value):
    value = code(value)
    return '-'.join(value[i:i+4] for i in range(0, 12, 4))


def clean_replay(blob, author=None):
    if not 0 < len(blob) <= MAX_FILE or not blob.startswith(MAGIC):
        raise ValueError('Share a complete replay, up to 32 MB')
    with gzip.GzipFile(fileobj=io.BytesIO(blob[len(MAGIC):])) as stream:
        raw = stream.read(MAX_EXPANDED + 1)
    if len(raw) > MAX_EXPANDED:
        raise ValueError('Expanded sharing file exceeds 96 MB')
    record, client, server, _ = read_battle(blob)
    if client is None or record['result'] == 'active':
        raise ValueError('Finish the battle before sharing its recording')
    # Serialize only validated game fields, including when the uploader supplied
    # extra personal/account fields in an otherwise parseable recording.
    value = dict(record=record, client=client)
    if server is not None:
        value['server'] = server
    if author is not None:
        from localserver.replay_identity import nickname
        value['record']['shared_by'] = nickname(author)
    clean = MAGIC + gzip.compress(encoded(value), compresslevel=6, mtime=0)
    if len(clean) > MAX_FILE:
        raise ValueError('Replay sharing file exceeds 32 MB')
    return clean


class ShareClient:
    def __init__(self, directory):
        settings = Path(directory) / 'replay-sharing.json'
        self.directory = Path(directory)
        value = json.loads(settings.read_text(encoding='utf8')) if settings.is_file() else {'url': 'https://sbre.online/singleweb'}
        self.url = value['url'].rstrip('/')
        parsed = urlsplit(self.url)
        local = parsed.hostname in ('localhost', '127.0.0.1', '::1')
        if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http') or
                not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError('Replay sharing requires HTTPS')

    def upload(self, blob, author=''):
        from localserver.replay_identity import nickname
        author = nickname(author)
        if len(blob) > MAX_FILE:
            raise ValueError('Replay sharing file exceeds 32 MB')
        # This method is reached only after an explicit share request. Cache by
        # endpoint and exact recording digest; never upload on history refresh.
        path = self.directory / 'replay-sharing-cache.json'
        try:cache = json.loads(path.read_text())
        except (OSError, ValueError):cache = {}
        key = hashlib.sha256(self.url.encode()+b'\0'+author.encode()+b'\0'+blob).hexdigest()
        if key in cache:
            entry = cache[key]
            return dict(code=display_code(entry['code']),size=entry['size'],nickname=author)
        request = Request(self.url + '/replays', data=blob, headers={'Content-Type': 'application/octet-stream', 'X-Replay-Nickname': author}, method='POST')
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read(4097))
        result['code'] = display_code(result['code'])
        if type(result.get('size')) is not int or not 0 < result['size'] <= MAX_FILE:
            raise ValueError('Invalid sharing service response')
        entry = {key: result[key] for key in ('code', 'size')}
        entry['nickname'] = author
        cache[key] = entry
        from tools.web_workspace import atomic_json
        atomic_json(path, cache)
        return entry

    def download(self, identifier, progress=None):
        with urlopen(self.url + '/replays/' + code(identifier), timeout=60) as response:
            self.import_token = response.headers.get('X-Replay-Import-Token', '')
            total=int(response.headers.get('Content-Length','0'));chunks=[];received=0
            if total>MAX_FILE:raise ValueError('Replay exceeds sharing limit')
            while True:
                chunk=response.read(128*1024)
                if not chunk:break
                received+=len(chunk)
                if received>MAX_FILE:raise ValueError('Replay exceeds sharing limit')
                chunks.append(chunk)
                if progress:progress('download',received,total)
            blob=b''.join(chunks)
        if progress:progress('validate',received,total)
        return clean_replay(blob)



    def acknowledge(self, identifier):
        if not getattr(self, 'import_token', ''):return
        import uuid
        path=self.directory/'sharing-viewer.json'
        try:viewer=str(uuid.UUID(json.loads(path.read_text())['viewer']))
        except (OSError, ValueError, KeyError):
            viewer=str(uuid.uuid4())
            from tools.web_workspace import atomic_json
            atomic_json(path,{'viewer':viewer})
        data=json.dumps(dict(viewer=viewer,token=self.import_token)).encode()
        try:
            with urlopen(Request(self.url+'/replays/'+code(identifier)+'/imported',data=data,headers={'Content-Type':'application/json'}),timeout=10) as response:response.read(4096)
        except (OSError,ValueError):pass  # Import stays successful if counting is offline.

    def details(self, identifier):
        with urlopen(self.url+'/replays/'+code(identifier)+'/details',timeout=30) as response:
            data=response.read(2*1024*1024+1)
        if len(data)>2*1024*1024:raise ValueError('Replay details are too large')
        return json.loads(data)

    def listings(self, order='latest'):
        order='popular' if order=='popular' else 'latest'
        with urlopen(self.url+'/replays/feed?sort='+order,timeout=20) as response:
            data=response.read(256*1024+1)
        if len(data)>256*1024:raise ValueError('Replay listing is too large')
        return json.loads(data)
