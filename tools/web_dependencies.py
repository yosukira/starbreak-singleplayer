"""Load the pinned, licensed pure-Python WebSocket library offline."""
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
WHEEL = 'websockets-17.0.1-py3-none-any.whl'
SHA256 = 'c6be9cba65c65cc76dfa3d4619e359ff02a4476c74e179b215236c11a0b32345'


def websocket_server():
    # Small updates retain the old Python runtime; ship this dependency in
    # managed data as well so upgraded installations need no pip or download.
    wheel = ROOT / 'data/vendor' / WHEEL
    if wheel.is_file():
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != SHA256:
            raise RuntimeError('WebSocket library checksum mismatch; reinstall the complete player package')
        if str(wheel) not in sys.path:
            sys.path.insert(0, str(wheel))
    from websockets.sync.server import serve
    return serve
