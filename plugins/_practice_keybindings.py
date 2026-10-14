"""Local practice shortcuts, shared by the editor and the PyPy bridge."""
import json
import math
import os
import time

KEYS = {'None': (0, 0), 'Auto': (0, 0), 'Backtick': (53, 0xC0),
        'Space': (44, 0x20), 'Enter': (40, 0x0D), 'Tab': (43, 0x09)}
for index, name in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ'):
    KEYS[name] = (4 + index, ord(name))
for name in '0123456789':
    KEYS[name] = (39 if name == '0' else 29 + int(name), ord(name))
for index in range(12):
    KEYS['F' + str(index + 1)] = (58 + index, 0x70 + index)

DEFAULTS = {'interact': 'Auto', 'editor_toggle': 'Backtick',
            'editor_toggle_alt': 'F8', 'restart': 'R',
            'wf': '7', 'dw': '8', 'fab': '9', 'ic': '0', 'hold_seconds': 2.0}
ACTIONS = ('interact', 'editor_toggle', 'editor_toggle_alt', 'restart', 'wf', 'dw', 'fab', 'ic')
CHOICES = tuple('ABCDEFGHIJKLMNOPQRSTUVWXYZ') + tuple('0123456789') + tuple(
    'F' + str(index + 1) for index in range(12) if index != 5) + ('Backtick', 'Space', 'Enter', 'Tab')


def normalize(document):
    if not isinstance(document, dict):
        raise ValueError('Invalid keyboard settings')
    result = dict(DEFAULTS)
    used = set()
    for action in ACTIONS:
        name = document.get(action, DEFAULTS[action])
        if name not in KEYS or name == 'F6' or (name == 'Auto' and action != 'interact'):
            raise ValueError('Invalid key: ' + str(name))
        if action == 'interact' and name == 'None':
            raise ValueError('Interaction cannot be disabled')
        if name not in ('None', 'Auto'):
            if name in used:
                raise ValueError('Duplicate key: ' + name)
            used.add(name)
        result[action] = name
    if result['editor_toggle'] == result['editor_toggle_alt'] == 'None':
        raise ValueError('Keep an editor shortcut')
    seconds = float(document.get('hold_seconds', DEFAULTS['hold_seconds']))
    if not math.isfinite(seconds) or not .5 <= seconds <= 5:
        raise ValueError('Hold duration must be between 0.5 and 5 seconds')
    result['hold_seconds'] = seconds
    return result


def load(path):
    try:
        with open(str(path), 'r', encoding='utf-8-sig') as handle:
            return normalize(json.load(handle))
    except (OSError, ValueError, TypeError):
        return dict(DEFAULTS)


def scancode(document, action):
    return KEYS[document[action]][0]


def keycode(name):
    if len(name) == 1:
        return ord(name.lower())
    if name in ('None', 'Auto'):
        return 0
    if name in ('Backtick', 'Space', 'Enter', 'Tab'):
        return {'Backtick': 96, 'Space': 32, 'Enter': 13, 'Tab': 9}[name]
    return 0x40000000 | KEYS[name][0]


def editor_keys(document):
    return tuple(KEYS[document[action]][1] for action in ('editor_toggle', 'editor_toggle_alt')
                 if document[action] != 'None')


def shortcuts(document):
    rows = []
    for action, kind, char_type in (('restart', 'restart', None), ('wf', 'switch_character', 1),
                                   ('dw', 'switch_character', 2), ('fab', 'switch_character', 5),
                                   ('ic', 'switch_character', 3)):
        name = document[action]
        if name == 'None':
            continue
        rows.append((KEYS[name][0], name, char_type, kind))
        if name in ('7', '8', '9', '0'):
            rows.append(({'7': 95, '8': 96, '9': 97, '0': 98}[name], 'Num' + name, char_type, kind))
    return tuple(rows)


class Reader(object):
    def __init__(self, path=None):
        self.path = path or os.environ.get('HARDLAB_KEYBINDINGS', '')
        self.document = dict(DEFAULTS)
        self.next_read = 0.0
        self.stamp = None

    def refresh(self):
        now = time.monotonic()
        if now < self.next_read or not self.path:
            return False
        self.next_read = now + .5
        try:
            stat = os.stat(self.path)
            stamp = (stat.st_mtime, stat.st_size)
            if stamp == self.stamp:
                return False
            with open(self.path, 'r', encoding='utf-8-sig') as handle:
                document = normalize(json.load(handle))
            changed = document != self.document
            self.document, self.stamp = document, stamp
            return changed
        except (OSError, ValueError, TypeError):
            return False
