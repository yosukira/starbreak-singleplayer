"""Regression checks against shipped item data; never edit real presets."""
import base64
import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localserver import projectile_speeds as ps
from localserver.pb_layout import default_layouts, _varint
from localserver.server import LocalServer
from tools import gear_editor as ge, i18n

import os
UI = os.environ.get('STARBREAK_UI_TESTS') == '1' or '--ui' in sys.argv
if '--ui' in sys.argv:
    sys.argv.remove('--ui')


class SpeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layouts = default_layouts()
        cls.payloads = {}
        for line in Path(ge.CATALOGUE).read_text(encoding='utf8').splitlines():
            row = json.loads(line)
            blob = base64.b64decode(row['payloadBase64'])
            desc = cls.layouts.decode('ItemDescription', blob)
            cls.payloads[desc['type_']] = (blob, desc)

    def test_every_exposed_speed_preserves_other_values(self):
        count = 0
        for item, info in ge.load_catalogue().items():
            blob, desc = self.payloads[item]
            for obj in info['projectiles']:
                expected = copy.deepcopy(desc)
                expected['obj_'][obj['index']]['speed_'] = 600
                modified = ps.patch_payload(self.layouts, blob, desc, {str(obj['index']): 600})
                self.assertEqual(self.layouts.decode('ItemDescription', modified), expected)
                count += 1
        self.assertGreater(count, 30)

    def test_unknown_wire_fields_survive(self):
        blob, desc = self.payloads[31]
        obj_spec = self.layouts.resolve_name('ItemDescription', 'obj_')
        unknown = _varint((999 << 3) | 2) + _varint(4) + b'test'
        parts = []
        for field, start, end, value in ps.fields(blob):
            if field == obj_spec['number']:
                value += unknown
                parts.append(_varint((field << 3) | 2) + _varint(len(value)) + value)
            else:
                parts.append(blob[start:end])
        blob = b''.join(parts) + unknown
        modified = ps.patch_payload(self.layouts, blob, desc, {'0': 600})
        self.assertEqual(modified.count(unknown), 2)
        self.assertEqual(ps.patch_payload(self.layouts, blob, desc, {}), blob)

    def test_invalid_input_and_nonprojectile(self):
        for value in (-1, 1000001, True, 1.5, '600'):
            with self.assertRaises(ValueError):
                ps.normalize({'31': {'0': value}})
        blob, desc = self.payloads[36]
        with self.assertRaises(ValueError):
            ps.patch_payload(self.layouts, blob, desc, {'0': 600})
        blob, desc = self.payloads[31]
        with self.assertRaises(ValueError):
            ps.patch_payload(self.layouts, blob, desc, {'5': 600})

    def test_server_login_payload_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            catalogue = Path(directory)/'item_descriptions.jsonl'
            shutil.copy2(ge.CATALOGUE, catalogue)
            overrides = catalogue.parent/'class_loadout_overrides.json'
            overrides.write_text(json.dumps({ps.KEY: {'31': {'0': 600}}}))
            host = SimpleNamespace(layouts=self.layouts)
            options = SimpleNamespace(items=str(catalogue), projectile_overrides=str(overrides))
            with patch('localserver.server.log'):
                payloads = LocalServer._load_item_library(host, options)
            self.assertEqual(host.item_descriptions[31]['obj_'][0]['speed_'], 600)
            sent = {self.layouts.decode('ItemDescription', blob)['type_']: blob for blob in payloads}
            for item, (blob, _) in self.payloads.items():
                if item != 31:
                    self.assertEqual(sent[item], blob)
            # Exercise the actual MT_DESCRIBE_ITEM sender, not only its cache.
            calls = []
            host.item_library = payloads
            host._send_raw = lambda channel, kind, blob, *a, **kw: calls.append((kind, blob))
            LocalServer._send_item_descriptions(host, None)
            self.assertTrue(all(kind == 16 for kind, _ in calls))
            self.assertEqual([blob for _, blob in calls], payloads)
            overrides.write_text(json.dumps({ps.KEY: {}}))
            with patch('localserver.server.log'):
                LocalServer._load_item_library(host, options)
            self.assertEqual(host.item_descriptions[31]['obj_'][0]['speed_'], 300)
            self.assertEqual(catalogue.read_bytes(), Path(ge.CATALOGUE).read_bytes())

    def test_import_new_and_legacy_exports(self):
        overrides = {ps.KEY: {'31': {'0': 600}}}
        settings = {}
        ge.apply_import_document({'1': {'mk_': 4}}, overrides, settings)
        self.assertEqual(overrides[ps.KEY]['31']['0'], 600)
        ge.apply_import_document({ps.KEY: {'31': {'0': 900}}}, overrides, settings)
        self.assertEqual(overrides[ps.KEY]['31']['0'], 900)

    @unittest.skipUnless(UI, 'pass --ui with a Tk desktop')
    def test_editor_save_switch_and_restore(self):
        import tkinter as tk
        with tempfile.TemporaryDirectory() as directory:
            copies = {}
            for name in ('OVERRIDES', 'CLASS_LOADOUTS', 'PRACTICE_LOADOUT'):
                target = Path(directory)/Path(getattr(ge, name)).name
                shutil.copy2(getattr(ge, name), target)
                copies[name] = str(target)
            root = tk.Tk()
            editor = None
            try:
                with patch.object(i18n, 'load_preference', return_value='zh'), patch.object(ge, 'load_overrides', return_value={}), patch.multiple(ge, **copies), patch.object(ge.game_settings, 'PATH', str(Path(directory)/'settings.json')), patch.object(ge.GearEditor, '_start_hotkey'), patch.object(ge.GearEditor, '_log'), patch.object(ge, 'save_overrides', side_effect=lambda document: ge._atomic_write_json(copies['OVERRIDES'], document)):
                    editor = ge.GearEditor(root, show=True)
                    editor.item_var.set('31  Kantikoy Repeater')
                    editor._on_item_select()
                    root.update_idletasks()
                    editor.projectile_speed_rows[0][1].set('600')
                    editor.apply()
                    saved = json.loads(Path(copies['OVERRIDES']).read_text(encoding='utf8'))
                    self.assertEqual(saved[ps.KEY]['31']['0'], 600)
                    self.assertEqual(editor._export_document()[ps.KEY]['31']['0'], 600)
                    self.assertIn('重新启动', editor.status.cget('text'))
                    editor.item_var.set('36  Cruel Sledge')
                    editor._on_item_select()
                    self.assertEqual(editor.projectile_speed_rows, [])
                    editor.item_var.set('31  Kantikoy Repeater')
                    editor._on_item_select()
                    self.assertEqual(editor.projectile_speed_rows[0][1].get(), '600')
                    # Invoke the visible restore button rather than setting data.
                    row = editor.projectile_frame.winfo_children()[0]
                    row.winfo_children()[-1].invoke()
                    editor.apply()
                    saved = json.loads(Path(copies['OVERRIDES']).read_text(encoding='utf8'))
                    self.assertNotIn('31', saved[ps.KEY])
                    self.assertIn('重新启动', editor.status.cget('text'))
                    root.update_idletasks()
                    self.assertGreater(editor.projectile_frame.winfo_height(), 40)
            finally:
                if editor:
                    editor._hotkey_stop = True
                root.destroy()


if __name__ == '__main__':
    unittest.main()
