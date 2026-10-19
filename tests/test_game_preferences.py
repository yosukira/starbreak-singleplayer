import json
import base64
from localserver.pb_layout import _varint
from pathlib import Path
import tempfile
import unittest
from tools.game_preferences import GamePreferences, KEY


def blob(rows):
    def field(tag,data):return _varint(tag*8+2)+_varint(len(data))+data
    message=b''.join(field(1,field(1,k.encode())+field(2,v)) for k,v in rows.items())
    return base64.b64encode(base64.b64encode(message)).decode()



class GamePreferenceTests(unittest.TestCase):
    def test_takeover_edits_only_changed_options_without_overwriting_own_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GamePreferences(directory)
            own=store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'70','musicVol':b'40','impbstr':b'own bindings'})))
            recorded=blob({'sfxVol':b'50','musicVol':b'10','impbstr':b'recorded bindings'})
            incoming=blob({'sfxVol':b'20','musicVol':b'10','impbstr':b'recorded bindings','username':b'do not retain'})
            saved=store.save(dict(key=KEY,revision=own['revision'],baseline=recorded,content=incoming))
            self.assertEqual(saved['content'],blob({'sfxVol':b'20','musicVol':b'40','impbstr':b'own bindings'}))
            # An identical retry or a settings dialog opened without edits
            # cannot replace personal bindings with the replay's bindings.
            self.assertEqual(store.save(dict(key=KEY,revision=own['revision'],baseline=recorded,content=incoming)),saved)
            self.assertEqual(store.save(dict(key=KEY,revision=saved['revision'],baseline=recorded,content=recorded)),saved)
            # Returning to the replay's initial volume is still an edit,
            # compared with the last acknowledged in-game snapshot.
            restored=store.save(dict(key=KEY,revision=saved['revision'],baseline=incoming,content=recorded))
            self.assertEqual(restored['content'],blob({'sfxVol':b'50','musicVol':b'40','impbstr':b'own bindings'}))

    def test_restart_and_stale_tabs_preserve_the_latest_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GamePreferences(directory)
            self.assertIsNone(store.read()['content'])
            saved=store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'22','impbstr':b'\x00\xff'})))
            self.assertEqual(GamePreferences(directory).read(),saved)
            with self.assertRaises(ValueError):store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'55'})))
            self.assertEqual(store.read(),saved)
            self.assertEqual(store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'22','impbstr':b'\x00\xff'}))),saved)

    def test_invalid_data_does_not_replace_saved_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GamePreferences(directory)
            saved=store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'55'})))
            for row in (dict(key='../other',revision=1,content=blob({'sfxVol':b'66'})),dict(key=KEY,revision=1,content='not base64'),dict(key=KEY,revision=True,content=blob({'sfxVol':b'66'})),dict(key=KEY,revision=1,content='A'*100000)):
                with self.assertRaises(ValueError):store.save(row)
            self.assertEqual(store.read(),saved)
            self.assertEqual([p.name for p in Path(directory).iterdir()],['game-preferences.json'])

    def test_only_options_are_persisted_and_login_fields_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GamePreferences(directory)
            value=store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'20','username':b'private','secret':b'private','guid':b'private'})))
            self.assertEqual(value['content'],blob({'sfxVol':b'20'}))

    def test_corrupt_file_is_reported_and_never_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            store=GamePreferences(directory);store.path.write_bytes(b'{broken')
            with self.assertRaises(ValueError):store.read()
            with self.assertRaises(ValueError):store.save(dict(key=KEY,revision=0,content=blob({'sfxVol':b'55'})))
            self.assertEqual(store.path.read_bytes(),b'{broken')

if __name__=='__main__':unittest.main()
