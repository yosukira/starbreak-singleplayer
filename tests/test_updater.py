import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import update


class UpdateTests(unittest.TestCase):
    def bundle(self,extra=None):
        files={'data/version.json':json.dumps({'version':'v20261016-r2','sequence':2}),
               'tools/launch.py':'# launcher','tools/update.py':'# updater',
               'tools/web_app.py':'# application'}
        files.update(extra or {})
        buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:
            for name,contents in files.items():z.writestr('starbreak-singleplayer/'+name,contents)
        data=buffer.getvalue()
        return data,dict(size=len(data),sha256=hashlib.sha256(data).hexdigest(),version='v20261016-r2',sequence=2,file='starbreak-singleplayer-update-v20261016-r2.zip')

    def test_update_preserves_data_runtime_and_keeps_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for name in ('userdata/web/profile.json','userdata/keybindings.json','python/python.exe','tools/web_app.py'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('original')
            data,info=self.bundle();backup=update.apply_update(data,info,root)
            self.assertEqual((root/'tools/web_app.py').read_text(),'# application')
            self.assertEqual((backup/'tools/web_app.py').read_text(),'original')
            for name in ('userdata/web/profile.json','userdata/keybindings.json','python/python.exe'):
                self.assertEqual((root/name).read_text(),'original')
            self.assertEqual(update.version(root)['sequence'],2)
            self.assertFalse((root/'userdata/updates/pending.json').exists())

    def test_tampering_and_protected_paths_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            data,info=self.bundle()
            with self.assertRaisesRegex(ValueError,'checksum'):update.apply_update(data+b'bad',info,root)
            for path in ('../outside','userdata/web/profile.json','python/python.exe','tools/../../outside','tools/C:bad'):
                data,info=self.bundle({path:'bad'})
                with self.subTest(path=path),self.assertRaises(ValueError):update.apply_update(data,info,root)
            self.assertFalse((root/'tools').exists())

    def test_failed_replacement_restores_all_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'tools').mkdir();(root/'tools/web_app.py').write_text('old app')
            data,info=self.bundle();replace=update.os.replace
            def fail_once(source,target):
                if Path(target)==root/'tools/web_app.py':raise OSError('locked file')
                return replace(source,target)
            with mock.patch.object(update.os,'replace',side_effect=fail_once),self.assertRaises(OSError):
                update.apply_update(data,info,root)
            self.assertEqual((root/'tools/web_app.py').read_text(),'old app')
            self.assertFalse((root/'tools/launch.py').exists())
            self.assertFalse((root/'data/version.json').exists())

    def test_interrupted_update_recovers_on_next_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);state=root/'userdata/updates';backup=state/'backup-test'
            (backup/'tools').mkdir(parents=True);(backup/'tools/web_app.py').write_text('old')
            (root/'tools').mkdir();(root/'tools/web_app.py').write_text('half updated')
            (root/'tools/new.py').write_text('new')
            update.atomic_json(state/'pending.json',dict(backup='backup-test',files=['tools/web_app.py','tools/new.py'],originals=['tools/web_app.py']))
            update.recover(root)
            self.assertEqual((root/'tools/web_app.py').read_text(),'old')
            self.assertFalse((root/'tools/new.py').exists())

    def test_cumulative_patch_requires_its_bootstrap_version(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with mock.patch.object(update,'manifest',return_value=dict(sequence=3,minimum_sequence=2)):
                with self.assertRaisesRegex(ValueError,'complete player package'):update.auto_update(root)

    def test_source_checkout_does_not_download_or_replace_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'.git').write_text('gitdir: worktree')
            with mock.patch.object(update,'manifest') as request:
                self.assertIn('Git checkout',update.auto_update(root))
                request.assert_not_called()

    def test_downloads_only_follow_trusted_https_hosts(self):
        for url in ('http://github.com/file','https://example.com/file',
                    'https://github.com.evil.example/file','https://github.com:8443/file',
                    'https://user@github.com/file'):
            with self.subTest(url=url),self.assertRaises(ValueError):
                update.download_url(url)
        url='https://release-assets.githubusercontent.com/path?signature=value'
        self.assertEqual(update.download_url(url),url)
        redirect=update.DownloadRedirects()
        with self.assertRaises(ValueError):
            redirect.redirect_request(None,None,302,'redirect',{},'http://github.com/file')

    def test_download_uses_ca_verification_and_limits_body_size(self):
        response=mock.MagicMock()
        response.__enter__.return_value=response
        response.geturl.return_value='https://release-assets.githubusercontent.com/download'
        response.read.return_value=b'x'*17
        opener=mock.Mock();opener.open.return_value=response
        with mock.patch.object(update,'build_opener',return_value=opener),mock.patch.object(update.ssl,'create_default_context',wraps=update.ssl.create_default_context) as context:
            with self.assertRaisesRegex(ValueError,'too large'):update.fetch('latest.json',16)
            context.assert_called_once_with()
            response.read.assert_called_once_with(17)
        with self.assertRaises(ValueError):update.fetch('../private',16)

    def test_manual_check_is_read_only_and_works_in_source_or_disabled_installs(self):
        for source in (False,True):
            with self.subTest(source=source),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                update.atomic_json(root/'data/version.json',dict(version='v20261016-r3',sequence=3))
                update.atomic_json(root/'userdata/update-settings.json',dict(enabled=False))
                if source:(root/'.git').write_text('source checkout')
                with mock.patch.object(update,'manifest',return_value=dict(sequence=4,version='v20261016-r4')),mock.patch.object(update,'apply_update') as install:
                    result=update.check_for_updates(root)
                    self.assertEqual(result['state'],'source_update' if source else 'available')
                    install.assert_not_called()
                self.assertEqual(update.version(root)['sequence'],3)
                self.assertEqual(update.read_status(root)['state'],result['state'])
                self.assertIn('v20261016-r4',update.status_message(result,'zh'))
                self.assertNotIn('detail',update.public_status(result))

    def test_automatic_success_and_failure_are_saved_for_the_next_ui(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data,info=self.bundle()
            with mock.patch.object(update,'manifest',return_value=info),mock.patch.object(update,'fetch',return_value=data):
                update.auto_update(root)
            self.assertEqual(update.read_status(root)['state'],'updated')
            self.assertIn('Successfully updated',update.status_message(update.read_status(root)))
            with mock.patch.object(update,'manifest',side_effect=OSError('offline')):
                with self.assertRaises(OSError):update.auto_update(root)
            result=update.read_status(root)
            self.assertEqual(result['state'],'failed')
            self.assertEqual(result['current_version'],'v20261016-r2')
            self.assertIn('继续使用当前版本',update.status_message(result,'zh'))

    def test_background_checks_do_not_block_or_duplicate_the_request(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);checker=update.UpdateChecker(root)
            entered,release=threading.Event(),threading.Event()
            def delayed_manifest():
                entered.set();release.wait(3)
                return dict(sequence=1,version='v20261016-r1')
            with mock.patch.object(update,'manifest',side_effect=delayed_manifest) as request:
                try:
                    self.assertEqual(checker.start()['state'],'checking')
                    self.assertTrue(entered.wait(1))
                    self.assertTrue(checker.busy)
                    self.assertEqual(checker.start()['state'],'checking')
                    self.assertEqual(request.call_count,1)
                finally:release.set();checker.thread.join(3)
            self.assertFalse(checker.busy)
            self.assertEqual(checker.status()['state'],'available')

    def test_new_ui_recognizes_first_update_from_an_older_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            update.atomic_json(root/'data/version.json',dict(version='v20261016-r1',sequence=1))
            data,info=self.bundle();update.apply_update(data,info,root)
            self.assertEqual(update.read_status(root)['state'],'updated')
            self.assertTrue((root/'userdata/updates/status.json').is_file())

    def test_failures_and_interrupted_checks_can_be_retried_without_exposing_details(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            with mock.patch.object(update,'manifest',side_effect=OSError('private diagnostic')):
                self.assertEqual(update.check_for_updates(root)['state'],'failed')
            self.assertNotIn('private diagnostic',str(update.public_status(update.read_status(root))))
            with mock.patch.object(update,'manifest',return_value=dict(sequence=1,version='v20261016-r1')):
                self.assertEqual(update.check_for_updates(root)['state'],'available')
            state=update.record_status('checking',root)
            state['updated_at']-=121
            update.atomic_json(root/'userdata/updates/status.json',state)
            self.assertEqual(update.read_status(root)['state'],'interrupted')

    def test_unreleased_local_version_is_not_reported_as_a_completed_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            update.atomic_json(root/'data/version.json',dict(version='v20261016-r6',sequence=6))
            with mock.patch.object(update,'manifest',return_value=dict(sequence=5,version='v20261016-r5')):
                result=update.check_for_updates(root)
            self.assertEqual(result['state'],'ahead')
            self.assertIn('v20261016-r5',update.status_message(result))
