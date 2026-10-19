"""Public player-side protocol tests; no deployment or storage-server code."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.replay_sharing import ShareClient,code,display_code

class ReplaySharingClientTests(unittest.TestCase):
    def test_endpoint_validation_and_code_format(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch('tools.replay_sharing.urlopen') as network:
                self.assertEqual(ShareClient(directory).url,'https://sbre.online/singleweb')
                network.assert_not_called()
            for value in ('http://example.invalid','https://user:password@example.invalid','https://example.invalid/?token=hidden'):
                (Path(directory)/'replay-sharing.json').write_text(json.dumps({'url':value}))
                with self.assertRaisesRegex(ValueError,'HTTPS'):ShareClient(directory)
        self.assertEqual(display_code('abcd efgh jkmn'),'ABCD-EFGH-JKMN')
        for value in ('../etc/passwd','0'*12,'A'*13):
            with self.assertRaises(ValueError):code(value)

    def test_repeated_explicit_share_reuses_a_saved_code(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory)/'replay-sharing.json').write_text('{"url":"https://example.invalid/singleweb"}')
            response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"code":"ABCD-EFGH-JKMN","size":123}'
            with mock.patch('tools.replay_sharing.urlopen',return_value=response) as upload:
                result=ShareClient(directory).upload(b'fixture')
                self.assertEqual(ShareClient(directory).upload(b'fixture'),result)
                self.assertEqual(upload.call_count,1)
                self.assertEqual(upload.call_args.args[0].full_url,'https://example.invalid/singleweb/replays')

class ReplayIdentityTests(unittest.TestCase):
    def test_optional_nickname_is_explicit_bounded_and_never_a_placeholder(self):
        from localserver.replay_identity import nickname
        self.assertEqual(nickname(None),'')
        self.assertEqual(nickname(' Player_34 '),'Player_34')
        for value in ('null','UnKnOw','UNKNOWN','n_u_l_l','中文','<script>','a'*33,34):
            with self.assertRaises(ValueError):nickname(value)

    def test_web_zoom_and_affix_language_preferences_preserve_existing_bindings(self):
        from tools.web_workspace import Workspace
        prefs=Workspace.__new__(Workspace).normalize_preferences({'language':'zh-affixes','keys':{'restart':'T','zoom_percent':25}})
        self.assertEqual(prefs['language'],'zh-affixes')
        self.assertEqual(prefs['keys']['restart'],'T')
        self.assertEqual(prefs['keys']['zoom_in'],'Equal')
        self.assertEqual(prefs['keys']['zoom_percent'],25)
        with self.assertRaises(ValueError):Workspace.__new__(Workspace).normalize_preferences({'keys':{'zoom_in':'R'}})
