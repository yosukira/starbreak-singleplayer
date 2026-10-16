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
        return data,dict(size=len(data),sha256=hashlib.sha256(data).hexdigest(),version='v20261016-r2',sequence=2)

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
