import ast
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from localserver import game_settings, paths, pb_layout
from localserver.maps.hulk_mid.controller import HulkMidController
from localserver.native_death_policy import _POLICY
from localserver.runtime_content import require_runtime_content
from tools import i18n
from tools.make_create_world import unwrap_decompress

ROOT = Path(__file__).resolve().parents[1]


class PublicRuntimeTests(unittest.TestCase):
    def test_runtime_content_complete(self):
        require_runtime_content(ROOT)

    def test_all_map_session_classes_import(self):
        maps = game_settings.available_maps()
        self.assertEqual(len(maps), 31)
        for map_id in maps:
            info = game_settings.map_info(map_id)
            module = info.get('session_module')
            if module:
                with self.subTest(map=map_id):
                    self.assertTrue(callable(getattr(importlib.import_module(module), info['session_class'])))

    def test_no_research_or_account_dumps(self):
        for name in ('captures', 'docs', 'anim', 'pypy'):
            self.assertFalse((ROOT / name).exists(), name)
        for p in (ROOT / 'maps').rglob('*'):
            self.assertNotIn(p.name, ('evidence', 'player-track.json', 'damage-ledger.json'))
        self.assertFalse((ROOT / 'data/catalogue/class_players.json').exists())

    def test_no_personal_fields_in_public_catalogues(self):
        forbidden = {'accountid', 'account_id', 'username', 'factionname', 'email',
                     'password', 'access_token', 'refresh_token', 'slottedcharprops'}
        def walk(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    self.assertNotIn(key.rstrip('_'), forbidden)
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)
        for folder in ('data', 'maps', 'localserver/data'):
            for p in (ROOT / folder).rglob('*.json'):
                with self.subTest(file=str(p.relative_to(ROOT))):
                    walk(json.loads(p.read_text(encoding='utf8')))

    def test_compressed_worlds_do_not_include_player_layers(self):
        for folder in ('maps', 'data'):
            for p in (ROOT / folder).rglob('*.hex'):
                with self.subTest(file=str(p.relative_to(ROOT))):
                    blob = bytes.fromhex(p.read_text(encoding='utf8').strip())
                    doc = pb_layout.default_layouts().decode('CreateWorld', unwrap_decompress(blob))
                    self.assertNotIn('clientworld_', doc.get('worldstate_', {}))

    def test_first_launch_does_not_replace_user_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            user = Path(directory) / 'userdata'
            with mock.patch.object(paths, 'USERDATA', user):
                paths.ensure_user_data()
                settings = user / 'game_settings.json'
                settings.write_text('{"map":"hulk_mid"}')
                paths.ensure_user_data()
                self.assertEqual(json.loads(settings.read_text(encoding='utf8'))['map'], 'hulk_mid')
                self.assertTrue((user / 'class_loadouts.json').exists())

    def test_starter_loadouts_are_clean_and_usable(self):
        rows = json.loads((ROOT / 'data/defaults/class_loadouts.json').read_text(encoding='utf8'))
        for key in ('1', '2', '3', '5'):
            self.assertEqual(rows[key]['type_'], int(key))
            self.assertEqual(len(rows[key]['items_']), 6)
            self.assertTrue(all(item['statboosts_'] == [] for item in rows[key]['items_']))

    def test_synthetic_players_for_every_class(self):
        from localserver.server import LocalServer, parse_args
        options = parse_args(['--mode', 'hulk', '--map', 'hulk_mid',
                              '--create-world-hex', str(ROOT / 'maps/hulk_mid/createworld.hex'),
                              '--class-loadouts', str(ROOT / 'data/defaults/class_loadouts.json')])
        server = LocalServer(options)
        for char_type in (1, 2, 3, 5):
            player = server.layouts.decode('Delta', server._build_player_delta_blob(options, char_type))['createplayer_']
            self.assertEqual(player['accountid_'], 'local-account-0001')
            self.assertEqual(player['username_'], 'Localplayer')
            self.assertEqual(player['charprops_']['charid_'], char_type)
            self.assertEqual(player['charprops_']['type_'], char_type)
            self.assertEqual(len(player['charprops_']['items_']), 6)

    def test_native_death_policy_remains_valid(self):
        self.assertGreater(len(_POLICY), 50)

    def test_hulk_mid_independent_time_and_damage_gates(self):
        c = HulkMidController(seed=4)
        boss_id = next(iter(c.bosses))
        boss = c.bosses[boss_id]
        c.set_players({1: {'center': [v + 16384 for v in boss['pos']], 'alive': True}})
        c.step(1 / 60)
        c.add_damage(30000, objid=boss_id, player_id=1)
        for _ in range(49 * 60):
            c.step(1 / 60)
        self.assertEqual(boss['phase'], 1)
        for _ in range(3 * 60):
            c.step(1 / 60)
        self.assertEqual(boss['phase'], 2)
        other = next(b for key, b in c.bosses.items() if key != boss_id)
        self.assertEqual(other['phase'], 1)
        self.assertIsNone(other['first_hit'])

    def test_full_run_rooms_serialize(self):
        names = {'egy_run': 'EgyRunPack', 'efb_run': 'EfbRunPack',
                 'eff_run': 'EffRunPack', 'est_run': 'EstRunPack',
                 'etl_run': 'EtlRunPack', 'eub_run': 'EubRunPack'}
        for map_id, name in names.items():
            scene = importlib.import_module('localserver.maps.' + map_id + '.scene')
            pack = getattr(scene, name)()
            for role in pack.room_names:
                with self.subTest(map=map_id, role=role):
                    blob = pack.world_blob(role, seed=17)
                    doc = pb_layout.default_layouts().decode('CreateWorld', unwrap_decompress(blob))
                    self.assertTrue(doc['worldstate_']['serverworld_']['obj_'])
            if hasattr(pack, 'for_run'):
                planned = pack.for_run(17)
                for role in planned.room_names:
                    with self.subTest(map=map_id, planned_role=role):
                        self.assertTrue(planned.world_blob(role))

    def test_all_source_parses(self):
        for directory in ('localserver', 'plugins', 'tools'):
            for p in (ROOT / directory).rglob('*.py'):
                ast.parse(p.read_bytes(), filename=str(p))


class LanguageTests(unittest.TestCase):
    def test_locales_and_fallback(self):
        with mock.patch.object(i18n.os, 'name', 'posix'), mock.patch.dict(
                i18n.os.environ, {'LC_ALL': 'zh_CN.UTF-8'}):
            self.assertEqual(i18n.system_language(), 'zh')
        with mock.patch.object(i18n.os, 'name', 'posix'), mock.patch.dict(
                i18n.os.environ, {'LC_ALL': 'de_DE.UTF-8'}):
            self.assertEqual(i18n.system_language(), 'en')
        self.assertEqual(i18n.resolve_language('invalid'), 'en')

    def test_windows_ui_language(self):
        kernel = mock.Mock()
        kernel.GetUserDefaultUILanguage.return_value = 0x0804
        with mock.patch.object(i18n.os, 'name', 'nt'), mock.patch.object(
                i18n.ctypes, 'windll', mock.Mock(kernel32=kernel), create=True):
            self.assertEqual(i18n.system_language(), 'zh')
            kernel.GetUserDefaultUILanguage.return_value = 0x0409
            self.assertEqual(i18n.system_language(), 'en')

    def test_preference_round_trip_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'ui.json'
            self.assertEqual(i18n.load_preference(p), 'auto')
            i18n.save_preference('en', p)
            self.assertEqual(i18n.load_preference(p), 'en')
            p.write_text('bad JSON')
            self.assertEqual(i18n.load_preference(p), 'auto')

    def test_english_ui_translation_coverage(self):
        import re
        tree = ast.parse((ROOT / 'tools/gear_editor.py').read_text(encoding='utf8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'GearEditor')
        for node in ast.walk(cls):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search('[\u4e00-\u9fff]', node.value):
                self.assertIn(node.value, i18n.EN)

    def test_map_and_stat_labels(self):
        from tools.gear_editor import STAT_NAMES, editor_map_choices
        self.assertEqual(set(STAT_NAMES), set(i18n.STATS_ZH))
        english = dict(editor_map_choices('en'))
        chinese = dict(editor_map_choices('zh'))
        self.assertEqual(set(english), set(chinese))
        self.assertNotEqual(english['hulk_mid'], chinese['hulk_mid'])
        self.assertEqual(english['efb_run'], 'Elite Frozen Blight · Full Run')


if __name__ == '__main__':
    unittest.main()
