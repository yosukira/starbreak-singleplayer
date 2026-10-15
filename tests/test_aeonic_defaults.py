import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from localserver import paths


def boots(document):
    wf = document.get('1', document)
    return wf.get('1280') or next(item for item in wf['items_'] if item['slotpos_'] == 1280)


class AeonicDefaultsTests(unittest.TestCase):
    def test_both_default_loadout_formats_use_half_crit_damage(self):
        for name in ('class_loadouts.json', 'practice_loadout.json', 'class_loadout_overrides.json'):
            with self.subTest(name=name):
                item = boots(json.loads((paths.ROOT / 'data/defaults' / name).read_text()))
                self.assertEqual(item['type_'], 266)
                self.assertEqual(next(boost['val_'] for boost in item['statboosts_'] if boost['stat_'] == 20), 50)

    def test_existing_defaults_are_backed_up_and_only_boots_change(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(paths, 'USERDATA', Path(directory)):
            paths.ensure_user_data()
            path = paths.USERDATA / 'class_loadouts.json'
            old = json.loads(path.read_text())
            old['1']['mk_'] = 4
            boots(old)['statboosts_'][-1]['val_'] = 500
            path.write_text(json.dumps(old))
            original = path.read_bytes()
            paths.ensure_user_data()
            fixed = json.loads(path.read_text())
            expected = json.loads(original)
            boots(expected)['statboosts_'][-1]['val_'] = 50
            self.assertEqual(fixed, expected)
            backup = path.with_name('class_loadouts.before-aeonic-crit-fix.json')
            self.assertEqual(backup.read_bytes(), original)
            paths.ensure_user_data()
            self.assertEqual(backup.read_bytes(), original)

    def test_custom_aeonic_modifiers_are_preserved(self):
        item = boots(json.loads((paths.ROOT / 'data/defaults/class_loadouts.json').read_text()))
        item['statboosts_'][-1]['val_'] = 500
        item['statboosts_'][0]['val_'] = 17
        original = json.dumps(item)
        self.assertFalse(paths.repair_default_aeonic(item))
        self.assertEqual(json.dumps(item), original)


if __name__ == '__main__':
    unittest.main()
