"""Public player-side protocol tests; no deployment or storage-server code."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from tools.replay_sharing import ShareClient,code,display_code

class ReplaySharingClientTests(unittest.TestCase):
    def test_listing_filters_are_bounded_and_forwarded_to_the_service(self):
        from tools.replay_sharing import listing_filters
        self.assertEqual(listing_filters(dict(map='egy_hulk',shell='2',front='0',upper='7')),dict(map='egy_hulk',shell=2,front=0,upper=7))
        for invalid in ({'map':'../../etc'},{'shell':'4'},{'front':'7'},{'map':'egy_hulk','lower':'101'},{'shell':True}):
            with self.assertRaises(ValueError):listing_filters(invalid)
        with tempfile.TemporaryDirectory() as directory:
            response=mock.MagicMock();response.__enter__.return_value.read.return_value=b'{"records":[]}'
            with mock.patch('tools.replay_sharing.urlopen',return_value=response) as network:
                ShareClient(directory).listings('popular',dict(map='hulk_normal',lower='0'))
                self.assertIn('sort=popular&map=hulk_normal&lower=0',network.call_args.args[0])
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

    def test_multilingual_comments_and_political_names_are_filtered(self):
        from localserver.replay_identity import nickname, comment_text
        for name in ('XiJinPing','xi_jin_ping','Xi.Jin-Ping_42','x1j1np1ng','MaoZeDong','Hu_Jin_Tao'):
            with self.subTest(name=name),self.assertRaises(ValueError):nickname(name)
        for name in ('Player_34','ChinaPlayer','Xi'):
            self.assertEqual(nickname(name),name)
        for value in ('这个走位很好，👍','Nice replay!','この動きはいいですね','좋은 리플레이입니다','Хорошая игра','لعبة جميلة','移动到左边\nthen jump'):
            self.assertEqual(comment_text(value),value)
        for value in ('习近平','習近平','x i-jin_ping','ＸｉＪｉｎＰｉｎｇ','x1j1np1ng','中国政治','中\u200b共','台湾独立','political debate','Political debate','시진핑','владимир путин'):
            with self.subTest(text=value),self.assertRaisesRegex(ValueError,'political-content'):comment_text(value)
        for value in ('',None,'a'*1001,'bad\x00text'):
            with self.assertRaises(ValueError):comment_text(value)

    def test_web_zoom_and_affix_language_preferences_preserve_existing_bindings(self):
        from tools.web_workspace import Workspace
        prefs=Workspace.__new__(Workspace).normalize_preferences({'language':'zh-affixes','keys':{'restart':'T','zoom_percent':25}})
        self.assertEqual(prefs['language'],'zh-affixes')
        self.assertEqual(prefs['keys']['restart'],'T')
        self.assertEqual(prefs['keys']['zoom_in'],'Equal')
        self.assertEqual(prefs['keys']['zoom_percent'],25)
        with self.assertRaises(ValueError):Workspace.__new__(Workspace).normalize_preferences({'keys':{'zoom_in':'R'}})
