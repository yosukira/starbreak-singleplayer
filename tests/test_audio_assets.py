import hashlib
import unittest

from tools.audio_assets import inventory, prepare


class AudioAssetsTests(unittest.TestCase):
    def test_all_maps_have_music_and_enemy_sound_resources(self):
        files = inventory()
        for pack in ('cave', 'city', 'dumb', 'forest', 'hulk', 'ice', 'jungle', 'lab', 'sc'):
            with self.subTest(pack=pack):
                self.assertIn('/client/data/music/' + pack + '.ogg', files)
                self.assertIn('/client/data/music/' + pack + '_boss.ogg', files)
                self.assertTrue(any(p.startswith('/client/data/sfx/' + pack + '/') for p in files))

    def test_missing_and_corrupt_audio_fail_publication(self):
        sound = b'OggSoriginal audio'
        name = '/client/data/sfx/jungle/bossx.ogg'
        files = {name: dict(size=len(sound), sha256=hashlib.sha256(sound).hexdigest())}
        self.assertEqual(prepare(lambda _: sound, files), 1)
        for bad in (b'', b'<html>not found</html>', sound[:-1] + b'X'):
            with self.subTest(data=bad), self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                prepare(lambda _: bad, files)
        def missing(_):
            raise FileNotFoundError(name)
        with self.assertRaises(FileNotFoundError):
            prepare(missing, files)


if __name__ == '__main__':
    unittest.main()
