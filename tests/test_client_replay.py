import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from tools.client_replay import ClientJournals, operation


class ClientJournalTests(unittest.TestCase):
    def begin(self, directory):
        store=ClientJournals(directory)
        identifier=str(uuid.uuid4())
        store.begin(identifier,'a'*64,'b'*64,dict(key='LocalStorage.pb',revision=0,content=None),[1366,768])
        return store,identifier

    def test_durable_ordered_recording_retries_and_complete_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            chunk=dict(sequence=0,ops=[['clock',123456789],['loop',0],['frame',17],['state',[12,0]],['loop',1]],end=False)
            with ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(list(pool.map(lambda _:store.append(identifier,chunk),range(4))),[dict(sequence=1,complete=False)]*4)
            with self.assertRaisesRegex(ValueError,'incomplete'):store.read(identifier)
            with self.assertRaisesRegex(ValueError,'changed'):
                store.append(identifier,dict(chunk,ops=[['clock',1]]))
            store.append(identifier,dict(sequence=1,ops=[['close']],end=True))
            reopened=ClientJournals(directory)
            self.assertEqual(reopened.read(identifier)['ops'],chunk['ops']+[['close']])
            self.assertTrue(reopened.statuses([identifier], 'b'*64)[identifier]['available'])
            self.assertEqual(reopened.statuses([identifier], 'c'*64)[identifier]['reason'],'incompatible')
            with self.assertRaisesRegex(ValueError,'sequence'):
                store.append(identifier,dict(sequence=2,ops=[],end=True))

    def test_rejects_out_of_order_unbounded_and_arbitrary_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            for row in (['eval','code'],['file','private-path'],['storage','account'],['random',256],['frame',-1],['net','invalid'],['state',[0]*100]):
                with self.subTest(operation=row[0]),self.assertRaises(ValueError):operation(row)
            with self.assertRaisesRegex(ValueError,'sequence'):
                store.append(identifier,dict(sequence=2,ops=[],end=False))
            store.append(identifier,dict(sequence=0,ops=[['send',123,12345],['net',base64.b64encode(b'game packet').decode()]],end=True,account='private-account'))
            document=store.read(identifier)
            self.assertNotIn('private-account',json.dumps(document))
            self.assertEqual(document['ops'][0],['send',123,12345])

    def test_takeover_preferences_are_bounded_game_options_only(self):
        from test_game_preferences import blob
        clean=blob({'musicVol':b'17','sfxVol':b'33','impbstr':b'original protobuf bindings'})
        self.assertEqual(operation(['preferences',clean]),['preferences',clean])
        self.assertEqual(operation(['preferences','']),['preferences',''])
        for value in (blob({'account':b'private'}),blob({'musicVol':b'17','password':b'private'}),None,'x'*100000):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                operation(['preferences',value])
        self.assertEqual(operation(['quit']),['quit'])
        self.assertEqual(operation(['exit',0]),['exit',0])
        for row in (['quit','arbitrary'],['exit',-1],['exit',256],['exit',True]):
            with self.assertRaises(ValueError):operation(row)


if __name__=='__main__':unittest.main()
