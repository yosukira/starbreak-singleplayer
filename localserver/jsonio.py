"""Encoding-tolerant readers for the JSON files players edit by hand.

Notepad on a Chinese Windows saves "ANSI" text as CP936/GBK, newer editors
may add a UTF-8 BOM, and "Unicode" saves are UTF-16.  A practice loadout
saved that way used to abort the server with a bare UnicodeDecodeError while
it was reading the file (server.py ``_build_player_delta_blob``).  These
helpers accept the encodings users actually write and report which one
matched so callers can warn instead of crashing.  Writers keep saving UTF-8
with ``ensure_ascii=False``; the gear editor rewrites files it has read so a
GBK file is normalised on the next Apply.
"""
import codecs
import json

__all__ = ('read_text', 'load_json')

def _decode(data):
    """Return (text, encoding) for the first encoding that decodes."""
    if data.startswith(codecs.BOM_UTF8):
        return data.decode('utf-8-sig'), 'utf-8-sig'
    if (data.startswith(codecs.BOM_UTF16_LE)
            or data.startswith(codecs.BOM_UTF16_BE)):
        return data.decode('utf-16'), 'utf-16'
    try:
        return data.decode('utf-8'), 'utf-8'
    except UnicodeDecodeError as utf8_error:
        try:
            return data.decode('gbk'), 'gbk'
        except UnicodeDecodeError:
            raise utf8_error

def read_text(path):
    """Read *path* bytes; return (text, encoding)."""
    with open(path, 'rb') as handle:
        data = handle.read()
    return _decode(data)

def load_json(path):
    """``json.load`` for *path*; return (document, encoding)."""
    text, encoding = read_text(path)
    return json.loads(text), encoding
