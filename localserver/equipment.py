"""UI-independent equipment catalogue shared by desktop and browser editors."""
import base64
import json
import os
from . import jsonio
from .paths import ROOT

CATALOGUE = str(ROOT / 'data/catalogue/item_descriptions.jsonl')
AFFIX_GUIDE = str(ROOT / 'data/mechanics/item-affix-guide.json')

SLOTS = (
    (256, '主武器'), (512, '副武器'), (768, '特殊武器'),
    (1024, '衣服'), (1280, '机动/鞋'), (1536, '植入体'),
) + tuple((3840 + index, '背包 {0}'.format(index + 1)) for index in range(8))

STAT_NAMES = {
    1: 'HP', 2: 'Max HP', 3: 'Ammo', 4: 'Max Ammo', 8: 'Air Jumps',
    9: 'Damage(flat)', 10: 'Armor', 11: 'Movement Speed', 12: 'Jump Height',
    13: 'Range', 14: 'Precise', 17: 'Implant Slot', 18: 'Damage%',
    19: 'Base Crit Chance', 20: 'Crit Damage', 21: 'Air Dashes',
    22: 'Knockback Resist', 23: 'Crit Chance', 24: 'Item Crit Chance',
    25: 'Item Crit Damage', 27: 'Ammo Multiplier', 32: 'Penetrating',
    33: 'Bounce', 34: 'Explosive Ammo', 35: 'Incendiary', 36: 'Subobjects/Shots',
    37: 'Speed%', 38: 'Lifetime(ms)', 39: 'Seeking', 41: 'Radius',
    42: 'Acceleration', 43: 'Armor Pierce', 45: 'Simultaneous',
    46: 'Secondary Subobjects', 47: 'Barbed', 48: 'Trisected', 49: 'Gravity%',
    50: 'Leech', 51: 'Extra Hit', 52: 'Reflect', 53: 'Move Time(ms)',
    54: 'Item Armor', 55: 'Quick Shot/AnimSpeed', 56: 'Shielded', 57: 'Cooldown',
}

CANDIDATE_CACHE = {}

AFFIX_DEFAULTS = {14: 100, 23: 500, 24: 500, 43: 100, 55: 20}

def num(value):
    return value.get('value') if isinstance(value, dict) else value

def load_catalogue(path=CATALOGUE):
    from localserver.pb_layout import default_layouts
    layouts = default_layouts()
    items = {}
    for line in jsonio.read_text(path)[0].splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            message = layouts.decode(
                'ItemDescription', base64.b64decode(row['payloadBase64']))
        except Exception:
            continue
        item_type = num(message.get('type_'))
        if item_type is None:
            continue
        items[int(item_type)] = {
            'type': int(item_type),
            'name': message.get('name_') or str(item_type),
            'slot': num(message.get('slot_')),
            'itemclass': num(message.get('itemclass_')),
            'usableby': [num(value) for value in
                         (message.get('usableby_') or [])],
            'base': [
                {'stat_': num(boost.get('stat_')),
                 'val_': int(boost.get('val_') or 0)}
                for boost in (message.get('statboosts_') or [])
                if num(boost.get('stat_')) is not None],
            'projectiles': [
                {'index': index, 'vid': obj['vid_'], 'speed': int(obj['speed_'])}
                for index, obj in enumerate(message.get('obj_') or [])
                if 'speed_' in obj and obj.get('vid_')],
        }
    return items

def candidate_items(catalogue, char_type, slotpos):
    key = (id(catalogue), int(char_type), int(slotpos))
    cached = CANDIDATE_CACHE.get(key)
    if cached is not None:
        return cached
    result = []
    for item in catalogue.values():
        if int(slotpos) < 3840 and item.get('slot') != int(slotpos):
            continue
        result.append(item)
    def priority(item):
        usable = item.get('usableby') or []
        compatible = (int(char_type) in usable or
                      (not usable and item.get('slot') in (256, 512, 768, 1024, 1280, 1536)))
        return (0 if compatible else 1, int(item['type']))
    result.sort(key=priority)
    CANDIDATE_CACHE[key] = result
    return result

def build_affix_priority(catalogue, path=AFFIX_GUIDE):
    'Load CHM/wiki item pools, independently of the available runtime rolls.'
    document = {}
    if os.path.exists(path):
        try:
            document = jsonio.load_json(path)[0]
        except (IOError, OSError, ValueError, TypeError, UnicodeError):
            document = {}
    items = {int(key): value for key, value in (document.get('items') or {}).items()
             if int(key) in catalogue}
    return dict(exact={key: [int(row['stat']) for row in item['pool']]
                       for key, item in items.items()},
                defaults={key: {int(row['stat']): int(row['defaultValue'])
                                 for row in item['pool']} for key, item in items.items()},
                sources=items)

def affix_default_value(priority, item_type, stat):
    """Value for a newly selected modifier; never rewrite loaded instances."""
    stat = int(stat)
    defaults = priority.get('defaults', {}).get(item_type, {})
    if stat in defaults:
        return defaults[stat]
    return AFFIX_DEFAULTS.get(stat, 0)
