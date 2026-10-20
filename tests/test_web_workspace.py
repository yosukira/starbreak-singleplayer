import copy
import json
from pathlib import Path
import tempfile
import unittest

from localserver import paths
from tools.web_workspace import Workspace


class WorkspaceTests(unittest.TestCase):
    def test_large_modifier_list_preserves_order_and_duplicates_in_saved_and_materialized_profile(self):
        document=self.workspace.read()
        expected=[dict(stat_=9,val_=i-75) for i in range(150)]
        document['loadouts']['1']['items_'][0]['statboosts_']=expected
        self.workspace.save(document)
        again=Workspace(self.workspace.directory,self.source)
        self.assertEqual(again.read()['loadouts']['1']['items_'][0]['statboosts_'],expected)
        again.materialize('hulk_mid')
        self.assertEqual(json.loads((again.directory/'class_loadouts.json').read_text())['1']['items_'][0]['statboosts_'],expected)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / 'desktop'
        self.source.mkdir()
        for name in ('class_loadouts.json', 'class_loadout_overrides.json', 'game_settings.json'):
            (self.source / name).write_bytes((paths.ROOT / 'data/defaults' / name).read_bytes())
        self.original = {p.name: p.read_bytes() for p in self.source.iterdir()}
        self.workspace = Workspace(Path(self.temporary.name) / 'web', self.source)

    def test_all_slots_modifiers_speeds_round_trip_without_touching_desktop(self):
        profile = self.workspace.read()
        character = profile['loadouts']['1']
        character['mk_'], character['impslots_'] = 4, 3
        character['items_'][0]['statboosts_'] = [dict(stat_=9, val_=123), dict(stat_=9, val_=-2)]
        projectile_item = next(item for item in self.workspace.catalogue.values() if item['projectiles'])
        projectile = projectile_item['projectiles'][0]
        profile['projectile_speeds'] = {str(projectile_item['type']): {str(projectile['index']): 901}}
        saved = self.workspace.save(profile)
        self.assertEqual(saved['revision'], 1)
        self.assertEqual(saved['loadouts']['1']['items_'][0]['statboosts_'], character['items_'][0]['statboosts_'])
        self.workspace.materialize('hulk_mid')
        effective = json.loads((self.workspace.directory / 'class_loadouts.json').read_text())
        self.assertEqual(effective['1']['mk_'], 4)
        speeds = json.loads((self.workspace.directory / 'class_loadout_overrides.json').read_text())
        self.assertEqual(speeds['_projectile_speeds'], profile['projectile_speeds'])
        self.assertEqual(json.loads((self.workspace.directory / 'game_settings.json').read_text())['map'], 'hulk_mid')
        self.assertEqual(self.original, {p.name: p.read_bytes() for p in self.source.iterdir()})

    def test_preferences_drafts_and_keys_survive_restart_without_applying_draft(self):
        preferences=self.workspace.preferences()
        preferences.update(language='en',shell=3,slot=1280,item_sort='name',query='drag',all_classes=True)
        preferences['keys'].update(restart='T',hold_seconds=.7)
        self.workspace.save_preferences(preferences)
        draft=self.workspace.read();draft['loadouts']['1']['mk_']=2
        self.workspace.save_draft(draft)
        again=Workspace(self.workspace.directory,self.source)
        self.assertEqual(again.preferences(),preferences)
        self.assertEqual(again.draft()['loadouts']['1']['mk_'],2)
        self.assertNotEqual(again.read()['loadouts']['1']['mk_'],2)
        again.save(draft)
        self.assertIsNone(again.draft())
        preferences['keys']['wf']='T'
        before=(again.directory/'preferences.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'Duplicate'):again.save_preferences(preferences)
        self.assertEqual((again.directory/'preferences.json').read_bytes(),before)

    def test_stale_tab_cannot_overwrite_newer_loadout(self):
        first = self.workspace.read()
        second = copy.deepcopy(first)
        self.workspace.save(first)
        second['loadouts']['1']['mk_'] = 2
        with self.assertRaisesRegex(ValueError, 'another tab'):
            self.workspace.save(second)
        self.assertEqual(self.workspace.read()['loadouts']['1']['mk_'], first['loadouts']['1']['mk_'])

    def test_invalid_fields_leave_saved_profile_unchanged(self):
        before = self.workspace.path.read_bytes()
        for change in (
            lambda doc: doc['loadouts']['1'].update(mk_=0),
            lambda doc: doc['loadouts']['1'].update(impslots_=9),
            lambda doc: doc['loadouts']['1']['items_'].append(copy.deepcopy(doc['loadouts']['1']['items_'][0])),
            lambda doc: doc.update(projectile_speeds={'108': {'99999': 3}}),
            lambda doc: doc['loadouts']['1']['items_'][0].update(type_=99999),
            lambda doc: doc['loadouts']['1']['items_'][0].update(statboosts_=[dict(stat_=9, val_=2**31)]),
        ):
            with self.subTest(change=change):
                draft = self.workspace.read()
                change(draft)
                with self.assertRaises(ValueError):
                    self.workspace.save(draft)
                self.assertEqual(self.workspace.path.read_bytes(), before)

    def test_restart_preserves_saved_profile_and_corruption_is_not_overwritten(self):
        profile = self.workspace.read()
        profile['loadouts']['2']['mk_'] = 3
        self.workspace.save(profile)
        again = Workspace(self.workspace.directory, self.source)
        self.assertEqual(again.read()['loadouts']['2']['mk_'], 3)
        again.path.write_text('broken')
        with self.assertRaises(ValueError):
            Workspace(self.workspace.directory, self.source)
        self.assertEqual(again.path.read_text(), 'broken')

    def test_old_web_defaults_upgrade_once_without_overwriting_custom_changes(self):
        old = self.workspace.read()
        old.pop('defaults_revision')
        old['loadouts']['1']['mk_'] = 3
        item = next(item for item in old['loadouts']['1']['items_'] if item['slotpos_'] == 1280)
        item['statboosts_'][-1]['val_'] = 500
        self.workspace.path.write_text(json.dumps(old))
        updated = Workspace(self.workspace.directory, self.source)
        profile = updated.read()
        self.assertEqual(profile['loadouts']['1']['mk_'], 3)
        item = next(item for item in profile['loadouts']['1']['items_'] if item['slotpos_'] == 1280)
        self.assertEqual(item['statboosts_'][-1]['val_'], 50)
        item['statboosts_'][-1]['val_'] = 500  # A later explicit user edit is retained.
        updated.save(profile)
        self.assertEqual(next(item for item in updated.read()['loadouts']['1']['items_'] if item['slotpos_'] == 1280)['statboosts_'][-1]['val_'], 500)


if __name__ == '__main__':
    unittest.main()
