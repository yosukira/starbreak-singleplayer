"""Optional Tk checks: run on a host with a desktop and tkinter."""
import os
import unittest
from unittest import mock

from tools import i18n


@unittest.skipUnless(os.environ.get('STARBREAK_UI_TESTS') == '1', 'Set STARBREAK_UI_TESTS=1 on a desktop host')
class EditorWindowTests(unittest.TestCase):
    def test_records_tab_preserves_original_editor_and_unsaved_values(self):
        import tkinter as tk
        import tempfile
        from tools.gear_editor import GearEditor
        root = tk.Tk()
        try:
            with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {'SB_BATTLE_RECORDS_PATH':directory}), mock.patch.object(i18n, 'load_preference', return_value='en'):
                editor = GearEditor(root, show=False)
                notebook = editor.battle_panel.master
                original_page = notebook.select()
                editor.mk_var.set('MK3')
                editor._set_affixes([{'stat_':9, 'val_':321}])
                original_item = editor.item_type
                notebook.select(editor.battle_panel)
                root.update()
                self.assertEqual(editor.battle_panel.summary['text'], 'Battles  0     Win rate  —     Damage  —')
                notebook.select(original_page)
                root.update()
                self.assertEqual(editor.mk_var.get(), 'MK3')
                self.assertEqual(editor.item_type, original_item)
                self.assertEqual(editor._current_boosts(), [{'stat_':9, 'val_':321}])
                editor.close()
        finally:
            try:root.destroy()
            except tk.TclError:pass

    def test_search_sort_preserve_selected_item_and_unsaved_modifiers(self):
        import tkinter as tk
        from tools.gear_editor import GearEditor
        root=tk.Tk()
        try:
            with mock.patch.object(i18n,'load_preference',return_value='en'),mock.patch.object(i18n,'save_preference'):
                editor=GearEditor(root,show=False)
                editor.item_search_var.set('bolt');editor._refresh_item_options()
                item=editor.item_type;label=editor.item_var.get()
                values=list(editor.item_box['values'])
                self.assertTrue(any('Bolt' in row for row in values))
                self.assertTrue(all('bolt' in row.casefold() or row==label for row in values))
                editor._set_affixes([{'stat_':9,'val_':321}])
                editor.toggle_item_sort()
                self.assertEqual(editor.item_type,item)
                self.assertEqual(editor.item_var.get(),label)
                self.assertEqual(editor._current_boosts(),[{'stat_':9,'val_':321}])
                names=[row.split('  ',1)[1].casefold() for row in editor.item_box['values']]
                self.assertEqual(names,sorted(names))
                editor.language_var.set(i18n.LANGUAGES['zh']);editor._change_language()
                self.assertEqual(editor.item_sort,'name')
                self.assertEqual(editor.item_search_var.get(),'bolt')
                self.assertEqual(editor._current_boosts(),[{'stat_':9,'val_':321}])
                editor.close()
        finally:
            try:root.destroy()
            except tk.TclError:pass

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
                editor.map_var.set('egy_hulk')
                editor._on_map_select()
                editor.missile_delay_var.set('123')
                editor.skip_intro_var.set(True)
                editor._store_intro_choice()
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
                self.assertTrue(editor.skip_intro_var.get())
                self.assertEqual(editor.missile_delay_var.get(), '123')
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
                self.assertTrue(editor.skip_intro_var.get())
                self.assertEqual(editor.missile_delay_var.get(), '123')
                self.assertEqual(editor.projectile_speed_rows[0][1].get(), '601')
                self.assertEqual(editor._current_boosts(), [{'stat_': 23, 'val_': 777}])
                editor.close()
                if editor._hotkey_thread is not None:
                    self.assertFalse(editor._hotkey_thread.is_alive())
        finally:
            try:root.destroy()
            except tk.TclError:pass

    def test_update_check_is_nonblocking_and_uses_inline_bilingual_results(self):
        import tkinter as tk
        import tempfile
        import threading
        from pathlib import Path
        from tools import update
        from tools.gear_editor import GearEditor
        entered,release=threading.Event(),threading.Event()
        def delayed_manifest():
            entered.set();release.wait(3)
            return dict(sequence=1,version='v20261016-r1')
        with tempfile.TemporaryDirectory() as directory:
            checker=update.UpdateChecker(Path(directory));root=tk.Tk()
            try:
                with mock.patch('tools.gear_editor.update.UpdateChecker',return_value=checker),mock.patch.object(update,'manifest',side_effect=delayed_manifest) as request,mock.patch.object(i18n,'load_preference',return_value='zh'),mock.patch.object(i18n,'save_preference'),mock.patch('tools.gear_editor.messagebox.showerror') as popup:
                    editor=GearEditor(root,show=False)
                    editor._check_updates()
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(str(editor.update_button['state']),'disabled')
                    editor.mk_var.set('MK3');root.update_idletasks()
                    release.set();checker.thread.join(3);editor._render_update_status()
                    self.assertIn('发现新版',editor.update_label['text'])
                    editor.language_var.set(i18n.LANGUAGES['en']);editor._change_language()
                    self.assertIn('available',editor.update_label['text'])
                    self.assertEqual(editor.mk_var.get(),'MK3')
                    request.side_effect=OSError('offline')
                    editor._check_updates();checker.thread.join(3);editor._render_update_status()
                    self.assertIn('failed',editor.update_label['text'])
                    self.assertEqual(str(editor.update_button['state']),'normal')
                    popup.assert_not_called()
                    editor.close()
            finally:
                release.set()
                if checker.thread:checker.thread.join(3)
                try:root.destroy()
                except tk.TclError:pass


if __name__ == '__main__':
    unittest.main()
