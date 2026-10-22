import copy
import json
import random
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from localserver.equipment import AFFIX_GUIDE
from localserver.pb_layout import default_layouts
from localserver.maps.sc_run.loot import (AFFIX_COUNT_WEIGHTS, ScChestLoot,
    equipment_pool, load_descriptions, roll_affix_count, roll_equipment)


class FixedTicket:
    def __init__(self, ticket):
        self.ticket = ticket

    def randrange(self, total):
        assert total == 30
        return self.ticket

    def choice(self, values):
        return values[0]


class ScChestLootTests(unittest.TestCase):
    def test_all_thirty_weight_tickets_have_exact_user_count_weights(self):
        counts = Counter(roll_affix_count(FixedTicket(ticket)) for ticket in range(30))
        self.assertEqual(tuple(counts[count] for count in range(5)), AFFIX_COUNT_WEIGHTS)
        self.assertEqual([roll_affix_count(FixedTicket(t)) for t in (9, 10, 17, 18, 23, 24, 28, 29)],
                         [0, 1, 1, 2, 2, 3, 3, 4])

    def test_four_affixes_keep_duplicates_and_label_only_missing_value(self):
        catalogue = {207: dict(type_=207, tier_=5, build_=0, slot_=256)}
        guide = dict(items={'207': dict(pool=[dict(stat=32, defaultValue=0)])})
        original = copy.deepcopy(guide)
        item = roll_equipment(FixedTicket(29), catalogue, guide)
        self.assertEqual(item, dict(type_=207, statboosts_=[dict(stat_=32)]*4))
        self.assertEqual(guide, original)
        with self.assertRaisesRegex(ValueError, 'no affix pool'):
            equipment_pool(catalogue, dict(items={}))
        with self.assertRaisesRegex(ValueError, 'incomplete affix default'):
            equipment_pool(catalogue, dict(items={'207': dict(pool=[dict(stat=32)])}))

    def test_real_pool_native_encoding_and_run_scoped_revisit_dedup(self):
        layouts = default_layouts()
        catalogue = load_descriptions()
        guide = json.loads(Path(AFFIX_GUIDE).read_text(encoding='utf8'))
        pool = equipment_pool(catalogue, guide)
        self.assertEqual(len(pool), 20)
        self.assertTrue({232, 255, 273}.issubset({item for item, _ in pool}))
        self.assertTrue(all(catalogue[item]['tier_'] == 5 and catalogue[item]['build_'] == 0 for item, _ in pool))
        sent = []
        ai_rng = random.Random(11)
        before = ai_rng.getstate()
        run = SimpleNamespace(seed=1234, chests_rewarded=set(), events=[], rng=ai_rng,
                              room=lambda role: SimpleNamespace(recipe=dict(metadata={})))
        wrapper = SimpleNamespace(run=run, layouts=layouts, channel=None,
            proxy=SimpleNamespace(_send_raw=lambda *args, **kwargs: sent.append(args[2])))
        loot = ScChestLoot(wrapper, catalogue, guide)
        self.assertTrue(loot.drop('chest_1', (12345, -67890)))
        self.assertFalse(loot.drop('chest_1', (12345, -67890)))
        # A new room wrapper shares the exact run-level claim and random stream.
        revisit = ScChestLoot(wrapper, catalogue, guide)
        self.assertIs(revisit.rng, loot.rng)
        self.assertFalse(revisit.drop('chest_1', (12345, -67890)))
        self.assertEqual(len(sent), 1)
        decoded = layouts.decode('Delta', sent[0])['createloot_']
        self.assertEqual(decoded['props_'], dict(xmp_=12345, ymp_=-67890))
        items = decoded['itemprops_']
        self.assertEqual(len(items), 4)
        self.assertEqual(items[-1]['type_'], 96)
        defaults = {item: {b['stat']: b['defaultValue'] for b in guide['items'][str(item)]['pool']} for item, _ in pool}
        for item in items[:-1]:
            allowed = defaults[item['type_']]
            self.assertLessEqual(len(item.get('statboosts_', [])), 4)
            for boost in item.get('statboosts_', []):
                self.assertIn(boost['stat_'], allowed)
                self.assertEqual(boost.get('val_', 0), allowed[boost['stat_']])
        self.assertEqual(ai_rng.getstate(), before)


if __name__ == '__main__':
    unittest.main()
