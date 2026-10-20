"""Pinned pure-Python browser dependency for portable player packages."""
import hashlib
import io
from pathlib import Path
from urllib.request import urlopen
import zipfile

URL = 'https://files.pythonhosted.org/packages/09/ce/3929538b2b9918f5eee623fbf3346893973191f6df93f19bbda097bd7bb7/websockets-17.0.1-py3-none-any.whl'
SHA256 = 'c6be9cba65c65cc76dfa3d4619e359ff02a4476c74e179b215236c11a0b32345'


def verify_sqlite(runtime, windows=False):
    """Battle records and recordings need both SQLite's module and library."""
    runtime = Path(runtime)
    if windows:
        missing = [name for name in ('_sqlite3.pyd', 'sqlite3.dll', 'python312.zip')
                   if not (runtime / name).is_file()]
        if missing:
            raise RuntimeError('Portable runtime missing: ' + ', '.join(missing))
        with zipfile.ZipFile(runtime / 'python312.zip') as archive:
            names = set(archive.namelist())
        modules = all(any('sqlite3/' + name + suffix in names for suffix in ('.py', '.pyc'))
                      for name in ('__init__', 'dbapi2'))
    else:
        library = runtime / 'lib/python3.12'
        native = any((library / 'lib-dynload').glob('_sqlite3*.so'))
        if not native:
            # The pinned macOS standalone builds register SQLite inside
            # libpython instead of shipping a separate extension. Check the
            # actual Mach-O implementation, not a text/metadata declaration.
            binaries = list((runtime / 'lib').glob('libpython3.12*.dylib'))
            binaries.append(runtime / 'bin/python3.12')
            magic = (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
                     b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca',
                     b'\xca\xfe\xba\xbf', b'\xbf\xba\xfe\xca')
            for binary in binaries:
                if binary.is_file():
                    data = binary.read_bytes()
                    if data[:4] in magic and b'PyInit__sqlite3' in data:
                        native = True
                        break
        modules = ((library / 'sqlite3/__init__.py').is_file() and
                   (library / 'sqlite3/dbapi2.py').is_file() and
                   native)
    if not modules:
        raise RuntimeError('Portable runtime is missing the SQLite standard library')


def install(destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    bundled = Path(__file__).resolve().parents[1] / 'data/vendor/websockets-17.0.1-py3-none-any.whl'
    if bundled.is_file():
        data = bundled.read_bytes()
    else:
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
