"""SC Chest equipment rolls and native CreateLoot emission.

Retained for original-server research; singleplayer/activity SC does not call
this equipment reward helper.

USER affix-count weights are relative 10:8:6:5:1 for counts 0..4. Item
selection and per-affix selection are uniform local FIT policies. The native
sample's three ordinary tier5/build0 items plus one 5 EC pickup provide the
default quantity FIT, configurable per room with chest_equipment_count.
"""
import base64
import json
import random
from pathlib import Path

from ...equipment import AFFIX_GUIDE, CATALOGUE
from ...pb_layout import default_layouts


AFFIX_COUNT_WEIGHTS = (10, 8, 6, 5, 1)
CHEST_RNG_SALT = 0x53434348455354
EQUIPMENT_SLOTS = {256, 512, 768, 1024, 1280}


def load_descriptions(path=CATALOGUE):
    layouts, descriptions = default_layouts(), {}
    for line in Path(path).read_text(encoding='utf8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        item = layouts.decode('ItemDescription', base64.b64decode(row['payloadBase64']))
        descriptions[int(item['type_'])] = item
    return descriptions


def equipment_pool(catalogue, guide):
    """Validate the exact item pool; missing affix sources fail explicitly."""
    result = []
    sources = guide.get('items', {})
    for item_type, description in sorted(catalogue.items(), key=lambda row: int(row[0])):
        if description.get('tier_') != 5 or description.get('build_') != 0 or description.get('slot_') not in EQUIPMENT_SLOTS:
            continue
        item_type = int(item_type)
        source = sources.get(str(item_type), sources.get(item_type, {}))
        boosts = source.get('pool')
        if not boosts:
            raise ValueError('SC equipment %s has no affix pool' % item_type)
        normalized = []
        for boost in boosts:
            stat, value = boost.get('stat'), boost.get('defaultValue')
            if type(stat) is not int or stat <= 0 or type(value) is not int:
                raise ValueError('SC equipment %s has an incomplete affix default' % item_type)
            # Same label-only rule as _label_only_item_affixes: absent val is
            # native numeric zero; explicitly encoding +0 changes its tooltip.
            normalized.append(dict(stat_=stat, **({'val_': value} if value else {})))
        result.append((item_type, normalized))
    if not result:
        raise ValueError('SC catalogue has no ordinary tier5/build0 equipment')
    return result


def roll_affix_count(rng):
    ticket = rng.randrange(sum(AFFIX_COUNT_WEIGHTS))
    for count, weight in enumerate(AFFIX_COUNT_WEIGHTS):
        if ticket < weight:
            return count
        ticket -= weight
    raise AssertionError('SC affix ticket outside weighted range')


def _roll_equipment(rng, pool):
    item_type, allowed = rng.choice(pool)
    count = roll_affix_count(rng)
    # Sampling with replacement is intentional: duplicate captured affixes are
    # valid, and a requested count of four stays four even for two-stat pools.
    boosts = [dict(rng.choice(allowed)) for _ in range(count)]
    return dict(type_=item_type, statboosts_=boosts)


def roll_equipment(rng, catalogue, guide):
    """Pure roll from decoded ItemDescription rows and the read-only guide."""
    return _roll_equipment(rng, equipment_pool(catalogue, guide))


class ScChestLoot:
    def __init__(self, wrapper, catalogue=None, guide=None):
        self.wrapper, self.run = wrapper, wrapper.run
        if catalogue is None:
            catalogue = load_descriptions()
        if guide is None:
            guide = json.loads(Path(AFFIX_GUIDE).read_text(encoding='utf8'))
        self.pool = equipment_pool(catalogue, guide)
        if 96 not in catalogue:
            raise ValueError('SC Chest needs the native 5 EC ItemDescription (96)')
        if not hasattr(self.run, 'chests_rewarded'):
            self.run.chests_rewarded = set()
        if not hasattr(self.run, '_sc_chest_rng'):
            self.run._sc_chest_rng = random.Random(int(self.run.seed) ^ CHEST_RNG_SALT)
        self.rng = self.run._sc_chest_rng

    def drop(self, role, position_mp):
        if role in self.run.chests_rewarded:
            return False
        room = self.run.room(role)
        count = room.recipe.get('metadata', {}).get('chest_equipment_count', 3)
        if type(count) is not int or count < 0:
            raise ValueError('SC chest_equipment_count must be a nonnegative integer')
        x, y = position_mp
        items = [_roll_equipment(self.rng, self.pool) for _ in range(count)]
        items.append(dict(type_=96))
        message = dict(createloot_=dict(props_=dict(xmp_=round(x), ymp_=round(y)), itemprops_=items))
        wrapper = self.wrapper
        payload = wrapper.layouts.encode('Delta', message)
        # Claim after successful encoding, before transport. RoomServer applies
        # its snapshot before TCP; an uncertain send must not mint a second set.
        self.run.chests_rewarded.add(role)
        result = wrapper.proxy._send_raw(wrapper.channel, 12, payload, 'sc-chest-equipment', record=False)
        self.run.events.append(dict(kind='chest_reward', room=role, equipment_count=count,
                                    item_types=[item['type_'] for item in items], quantity_policy='FIT observed default'))
        return result is not False
