import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from localserver import paths, presets
from tools.web_workspace import Workspace


def main_item(document):
    ic = document['3']
    return ic.get('256') or next(item for item in ic['items_'] if item['slotpos_'] == 256)


def old_ic(item):
    item['statboosts_'] = [dict(stat_=stat, val_=val) for stat, val in
        ((37,50),(22,50),(43,100),(51,50),(9,10000),(25,200),(24,5000))]


class PresetTests(unittest.TestCase):
    def test_ic_defaults_and_exact_legacy_migration(self):
        for name in ('class_loadouts.json', 'class_loadout_overrides.json'):
            with self.subTest(name=name):
                document = presets.defaults(name)
                item = main_item(document)
                self.assertEqual(item['statboosts_'], [dict(stat_=stat, val_=val) for stat, val in
                    ((37,20),(22,50),(43,100),(51,50))])
                old_ic(item)
                expected = copy.deepcopy(document)
                paths.repair_default_ic(main_item(expected))
                with tempfile.TemporaryDirectory() as directory, mock.patch.object(paths, 'USERDATA', Path(directory)):
                    target = Path(directory) / name
                    target.write_text(json.dumps(document))
                    original = target.read_bytes()
                    paths._repair_ic_presets()
                    self.assertEqual(json.loads(target.read_text()), expected)
                    self.assertEqual(target.with_name(target.stem + '.before-ic-default-fix.json').read_bytes(), original)
                old_ic(item)
                item['statboosts_'][1]['val_'] = 27
                before = copy.deepcopy(item)
                self.assertFalse(paths.repair_default_ic(item))
                self.assertEqual(item, before)

    def test_desktop_restore_copies_clean_defaults_and_retains_backup_and_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            targets = {name: root / name for name in presets.FILES}
            for name, target in targets.items():
                target.write_text(json.dumps({'custom': name}))
            for name in ('keybindings.json', 'language.json'):
                (root / name).write_text('keep')
            before = {name: target.read_bytes() for name, target in targets.items()}
            backup = presets.restore(targets)
            for name, target in targets.items():
                self.assertEqual(json.loads(target.read_text()), presets.defaults(name))
                self.assertEqual((backup / name).read_bytes(), before[name])
            self.assertEqual((root / 'keybindings.json').read_text(), 'keep')
            self.assertEqual((root / 'language.json').read_text(), 'keep')

    def test_browser_reset_has_clean_shipped_defaults_stale_guard_and_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)/'web', Path(directory)/'empty')
            profile = workspace.read()
            profile['loadouts']['3']['mk_'] = 2
            profile['loadouts']['3']['items_'][0]['statboosts_'].append(dict(stat_=9,val_=123))
            profile['settings']['map'] = 'forest_boss'
            saved = workspace.save(profile)
            before = workspace.path.read_bytes()
            with self.assertRaisesRegex(ValueError, 'another tab'):
                workspace.restore_defaults(0)
            self.assertEqual(workspace.path.read_bytes(), before)
            reset = workspace.restore_defaults(saved['revision'])
            self.assertEqual(reset['revision'], saved['revision']+1)
            self.assertEqual(main_item(reset['loadouts'])['statboosts_'], main_item(presets.defaults('class_loadouts.json'))['statboosts_'])
            self.assertEqual(reset['loadouts']['3']['mk_'], 6)
            self.assertEqual(reset['projectile_speeds'], {})
            self.assertEqual(reset['settings']['map'], presets.defaults('game_settings.json')['map'])
            self.assertEqual(list(workspace.directory.glob('profile.before-restore-defaults-*'))[0].read_bytes(), before)
            self.assertEqual(workspace.read(), reset)

    def test_previous_web_ic_default_migrates_once(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)/'web', Path(directory)/'empty')
            document = workspace.read()
            old_ic(main_item(document['loadouts']))
            document['defaults_revision'] = 1
            document['loadouts']['2']['mk_'] = 4
            workspace.path.write_text(json.dumps(document))
            workspace = Workspace(workspace.directory, workspace.source)
            self.assertEqual(main_item(workspace.read()['loadouts'])['statboosts_'][0]['val_'], 20)
            self.assertEqual(workspace.read()['loadouts']['2']['mk_'], 4)
            custom = workspace.read()
            old_ic(main_item(custom['loadouts']))
            workspace.save(custom)
            self.assertEqual(len(main_item(workspace.read()['loadouts'])['statboosts_']), 7)
