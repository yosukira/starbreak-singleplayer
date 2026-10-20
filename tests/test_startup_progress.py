import contextlib
import io
import unittest
from unittest import mock

from tools import startup_progress,update
import test_updater
from pathlib import Path
import tempfile


class StartupProgressTests(unittest.TestCase):
    def test_checking_is_visible_before_the_network_operation_starts(self):
        output=io.StringIO()
        def operation(progress):
            self.assertIn('正在检查更新',output.getvalue())
            progress('downloading',0,1024,'v20261020-r9')
            progress('downloading',1024,1024,'v20261020-r9')
            return 'done'
        with contextlib.redirect_stdout(output):self.assertEqual(startup_progress.run(operation,popup=False),'done')
        self.assertIn('100%',output.getvalue())
        self.assertIn('KB/s',output.getvalue())

    def test_console_selection_is_disabled_and_original_flags_restored_on_failure(self):
        kernel=mock.Mock();kernel.GetStdHandle.return_value=123;kernel.SetConsoleMode.return_value=1
        def get_mode(handle,mode):mode._obj.value=0x40|0x20|0x10;return 1
        kernel.GetConsoleMode.side_effect=get_mode
        with self.assertRaisesRegex(RuntimeError,'failure'):
            with startup_progress.console_selection_guard(kernel):raise RuntimeError('failure')
        self.assertEqual(kernel.SetConsoleMode.call_args_list,[mock.call(123,0x80|0x20|0x10),mock.call(123,0x40|0x20|0x10)])

    def test_real_update_reports_validation_backup_and_each_installed_file(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'tools').mkdir();(root/'tools/launch.py').write_text('old')
            data,info=test_updater.UpdateTests().bundle();seen=[]
            update.apply_update(data,info,root,progress=lambda *row:seen.append(row))
            for stage in ('verifying','backing_up','installing'):
                rows=[row for row in seen if row[0]==stage]
                self.assertTrue(rows,stage)
                self.assertEqual(rows[-1][1],rows[-1][2])
            self.assertTrue((root/'data/version.json').is_file())

    def test_progress_uses_file_units_for_installation_and_unknown_time_for_checking(self):
        self.assertIn('5s',startup_progress.describe('checking',0,0,'',5))
        self.assertIn('50%',startup_progress.describe('installing',450,900,'v20261020-r9',8))
        self.assertIn('450 / 900 files',startup_progress.describe('installing',450,900,'v20261020-r9',8))
