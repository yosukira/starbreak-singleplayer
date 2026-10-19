"""Durable local storage for the original browser client's options blob."""
import base64
import binascii
import json
from pathlib import Path
import threading

from tools.web_workspace import atomic_json
from localserver.pb_layout import _read_varint

MAX_BYTES = 64*1024
KEY = 'LocalStorage.pb'
SETTINGS = frozenset(('impbstr', 'gq', 'windowWidth', 'windowHeight', 'fullScreen',
                      'musicVol', 'defMusicVol', 'sfxVol', 'defSFXVol', 'tipIndex'))


def fields(data):
    offset = 0
    while offset < len(data):
        start = offset
        tag, offset = _read_varint(data, offset)
        if tag & 7 != 2:
            raise ValueError('Invalid game settings message')
        size, offset = _read_varint(data, offset)
        end = offset+size
        if end > len(data):
            raise ValueError('Truncated game settings')
        yield tag >> 3, data[offset:end], data[start:end]
        offset = end


def options_only(raw):
    """LocalStorage.pb is base64-encoded protobuf, with string key/value rows.

    Persist options and bindings, excluding the client's account/session fields.
    The selected rows retain their original bytes and are not reinterpreted.
    """
    try:
        message = base64.b64decode(raw, validate=True)
        kept = []
        for tag, entry, wire in fields(message):
            if tag != 1:
                raise ValueError('Invalid game settings entry')
            pair = {field: value for field, value, _ in fields(entry)}
            if set(pair) != {1, 2}:
                raise ValueError('Invalid game settings pair')
            if pair[1].decode('utf8') in SETTINGS:
                kept.append(wire)
        return base64.b64encode(b''.join(kept))
    except (ValueError, UnicodeError, IndexError, binascii.Error):
        raise ValueError('Invalid original-client settings data') from None


def option_rows(content):
    """Keep each original protobuf row intact while comparing named options."""
    if content is None:return {}
    result={}
    message=base64.b64decode(base64.b64decode(content,validate=True),validate=True)
    for _tag,entry,wire in fields(message):
        pair={tag:value for tag,value,_wire in fields(entry)}
        name=pair[1].decode('utf8')
        if name in result:raise ValueError('Duplicate game option')
        if name in SETTINGS:result[name]=(pair[2],wire)
    return result


def changed_options(current, baseline, incoming):
    """Apply explicit in-game edits after takeover, retaining unrelated options."""
    rows=option_rows(current);old=option_rows(baseline);new=option_rows(incoming)
    for key in old.keys() | new.keys():
        if (old.get(key) or (None,))[0] == (new.get(key) or (None,))[0]:continue
        if key in new:rows[key]=new[key]
        else:rows.pop(key,None)
    raw=b''.join(wire for _value,wire in rows.values())
    return base64.b64encode(base64.b64encode(raw)).decode('ascii')


class GamePreferences:
    def __init__(self, directory):
        self.path = Path(directory) / 'game-preferences.json'
        self.lock = threading.Lock()

    @staticmethod
    def validate(document):
        if not isinstance(document, dict) or document.get('key') != KEY:
            raise ValueError('Unknown game settings key')
        revision = document.get('revision')
        content = document.get('content')
        if type(revision) is not int or not 0 <= revision < 2**53:
            raise ValueError('Invalid settings revision')
        if not isinstance(content, str) or len(content) > (MAX_BYTES+2)//3*4:
            raise ValueError('Game settings exceed 64 KB')
        try:
            raw = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid game settings encoding') from None
        if len(raw) > MAX_BYTES:
            raise ValueError('Game settings exceed 64 KB')
        return dict(key=KEY, revision=revision, content=base64.b64encode(options_only(raw)).decode('ascii'))

    def read(self):
        if not self.path.exists():
            return dict(key=KEY, revision=0, content=None)
        # Report corrupt saved settings rather than silently replacing them.
        return self.validate(json.loads(self.path.read_text(encoding='utf8')))

    def save(self, document):
        value = self.validate(document)
        baseline=document.get('baseline')
        if baseline is not None:
            baseline=self.validate(dict(key=KEY,revision=0,content=baseline))['content']
        with self.lock:
            current = self.read()
            if 'baseline' in document:
                value['content']=changed_options(current['content'],baseline,value['content'])
                if value['content']==current['content']:return current
            if value['revision'] != current['revision']:
                if value['content'] == current['content']:
                    return current
                raise ValueError('Game settings changed in another window; reload before saving')
            value['revision'] += 1
            atomic_json(self.path, value)
            return value
