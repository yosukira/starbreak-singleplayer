import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from tools.desktop_workspace import DesktopWorkspace
from tools.web_workspace import atomic_json
from localserver import paths


class DesktopWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'native'
        self.source.mkdir()
        for name in (*DesktopWorkspace.files, 'practice_loadout.json', 'keybindings.json'):
            shutil.copy2(paths.ROOT / 'data/defaults' / name, self.source / name)
        self.workspace = DesktopWorkspace(self.root / 'ui', self.source)

    def test_shared_editor_saves_all_classes_into_effective_native_files(self):
        document = self.workspace.read()
        document['loadouts']['2']['mk_'] = 3
        document['loadouts']['3']['impslots_'] = 4
        document['loadouts']['2']['items_'][0]['statboosts_'] = [{'stat_':13, 'val_':777}]
        result = self.workspace.save(document)
        loadouts = json.loads((self.source / 'class_loadouts.json').read_text())
        overrides = json.loads((self.source / 'class_loadout_overrides.json').read_text())
        for key in ('1', '2', '3', '5'):
            self.assertEqual(loadouts[key], result['loadouts'][key])
        self.assertEqual(overrides['2']['mk_'], 3)
        self.assertEqual(overrides['3']['impslots_'], 4)
        self.assertEqual(overrides['2']['256']['statboosts_'], [{'stat_':13, 'val_':777}])
        self.assertTrue((self.source / 'class_loadouts.json.bak').is_file())
        self.assertEqual(self.workspace.editor()['profile'], result)

    def test_external_native_edit_rejects_stale_draft_without_overwriting(self):
        draft = self.workspace.read()
        path = self.source / 'class_loadouts.json'
        loadouts = json.loads(path.read_text())
        loadouts['2']['mk_'] = 2
        atomic_json(path, loadouts)
        with self.assertRaisesRegex(ValueError, 'another tab'):
            self.workspace.save(draft)
        self.assertEqual(self.workspace.editor()['profile']['loadouts']['2']['mk_'], 2)
        self.assertEqual(json.loads(path.read_text())['2']['mk_'], 2)

    def test_preferences_and_reset_use_native_settings(self):
        preferences = self.workspace.preferences()
        preferences['keys']['editor_toggle_alt'] = 'F9'
        self.workspace.save_preferences(preferences)
        self.assertEqual(json.loads((self.source / 'keybindings.json').read_text())['editor_toggle_alt'], 'F9')
        original = self.workspace.read()
        original['loadouts']['2']['mk_'] = 2
        saved = self.workspace.save(original)
        restored = self.workspace.restore_defaults(saved['revision'])
        self.assertEqual(json.loads((self.source / 'class_loadouts.json').read_text())['2'], restored['loadouts']['2'])

    def test_start_contains_only_valid_local_encounter_and_class(self):
        value = self.workspace.start(dict(map='hulk_mid', shell=2))
        self.assertEqual((value['map'], value['shell']), ('hulk_mid', 2))
        with self.assertRaises(ValueError):
            self.workspace.start(dict(map='missing', shell=1))


if __name__ == '__main__':unittest.main()
