"""Complete original audio inventory for offline browser publication."""
import hashlib
import json
from pathlib import Path
import re
from concurrent.futures import ThreadPoolExecutor


def inventory(path=None):
    path = path or Path(__file__).resolve().parents[1] / 'data/mechanics/audio-assets.json'
    value = json.loads(Path(path).read_text(encoding='utf8'))
    files = value.get('files')
    if value.get('schema') != 1 or not isinstance(files, dict) or not files:
        raise ValueError('Invalid audio inventory')
    for name, row in files.items():
        if (not re.fullmatch(r'/client/data/(?:music|sfx)/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+\.ogg', name)
                or not isinstance(row, dict) or type(row.get('size')) is not int
                or row['size'] <= 0 or not re.fullmatch('[0-9a-f]{64}', row.get('sha256', ''))):
            raise ValueError('Invalid audio asset: ' + name)
    return files


def prepare(fetch, files=None):
    """Fetch every declared sound and stop publication on missing/corrupt bytes.

    Availability on the server is independent of download timing in the client;
    the game still loads only the sounds and track used by the current map.
    """
    files = inventory() if files is None else files

    def verify(item):
        name, expected = item
        data = fetch(name)
        if (len(data) != expected['size'] or not data.startswith(b'OggS')
                or hashlib.sha256(data).hexdigest() != expected['sha256']):
            raise ValueError('Audio asset checksum mismatch: ' + name)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(verify, files.items()))
    return len(files)
