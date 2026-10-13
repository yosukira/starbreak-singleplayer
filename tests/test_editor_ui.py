"""Optional Tk checks: run on a host with a desktop and tkinter."""
import os
import unittest
from unittest import mock

from tools import i18n


@unittest.skipUnless(os.environ.get('STARBREAK_UI_TESTS') == '1', 'Set STARBREAK_UI_TESTS=1 on a desktop host')
class EditorWindowTests(unittest.TestCase):
    def test_switch_preserves_unsaved_values(self):
        import tkinter as tk
        from tools.gear_editor import GearEditor
        root = tk.Tk()
        try:
            with mock.patch.object(i18n, 'load_preference', return_value='en'), mock.patch.object(i18n, 'save_preference'):
                editor = GearEditor(root, show=False)
                editor.item_var.set('31  Kantikoy Repeater')
                editor._on_item_select()
                editor.projectile_speed_rows[0][1].set('601')
                editor.mk_var.set('MK3')
                editor.implant_var.set('4')
                editor.drop_health_var.set('12.5')
                editor.balance_entry_vars['enemy_hp'].set('2.5')
                editor._set_affixes([{'stat_': 23, 'val_': 777}])
                item = editor.item_type
                editor.language_var.set(i18n.LANGUAGES['zh'])
                editor._change_language()
                root.update_idletasks()
                self.assertEqual(editor.language, 'zh')
                self.assertEqual(editor.mk_var.get(), 'MK3')
                self.assertEqual(editor.implant_var.get(), '4')
                self.assertEqual(editor.drop_health_var.get(), '12.5')
                self.assertEqual(editor.balance_entry_vars['enemy_hp'].get(), '2.5')
                self.assertEqual(editor.item_type, item)
                self.assertEqual(editor.projectile_speed_rows[0][1].get(), '601')
                self.assertEqual(editor._current_boosts(), [{'stat_': 23, 'val_': 777}])
                editor.language_var.set(i18n.LANGUAGES['en'])
                editor._change_language()
                root.update_idletasks()
                self.assertEqual(editor.language, 'en')
                self.assertEqual(editor.projectile_speed_rows[0][1].get(), '601')
                self.assertEqual(editor._current_boosts(), [{'stat_': 23, 'val_': 777}])
                editor.close()
                if editor._hotkey_thread is not None:
                    self.assertFalse(editor._hotkey_thread.is_alive())
        finally:
            try:root.destroy()
            except tk.TclError:pass


if __name__ == '__main__':
    unittest.main()
