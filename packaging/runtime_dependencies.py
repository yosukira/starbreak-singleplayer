"""Pinned pure-Python browser dependency for portable player packages."""
import hashlib
import io
from pathlib import Path
from urllib.request import urlopen
import zipfile

URL = 'https://files.pythonhosted.org/packages/09/ce/3929538b2b9918f5eee623fbf3346893973191f6df93f19bbda097bd7bb7/websockets-17.0.1-py3-none-any.whl'
SHA256 = 'c6be9cba65c65cc76dfa3d4619e359ff02a4476c74e179b215236c11a0b32345'


def install(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with urlopen(URL, timeout=60) as response:
        data = response.read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise RuntimeError('WebSocket dependency checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        for entry in archive.infolist():
            name = Path(entry.filename)
            if name.is_absolute() or '..' in name.parts:
                raise RuntimeError('Unexpected dependency archive path')
        archive.extractall(destination)
    (destination / 'WEBSOCKETS-SOURCE.txt').write_text(
        'websockets 17.0.1 (pure Python wheel, no system installation)\n' + URL
        + '\nSHA256: ' + SHA256 + '\nLicense: websockets-17.0.1.dist-info/licenses/LICENSE\n', encoding='utf8')
