import base64
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest import mock

from tools.client_replay import ClientJournals, operation


class ClientJournalTests(unittest.TestCase):
    def test_failed_fork_does_not_leave_an_orphan_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            store.append(identifier,dict(sequence=0,ops=[['loop',0],['loop',1]],end=True))
            with mock.patch.object(store,'pack_chunk',side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):store.fork(identifier,2)
            with store.connect() as db:
                self.assertEqual(db.execute('SELECT id FROM attempts').fetchall(),[(identifier,)])
            self.assertEqual(store.read(identifier)['ops'],[['loop',0],['loop',1]])

    def test_corrupt_compression_and_stale_terminal_chunks_report_damage(self):
        import hashlib,zlib
        from tools.client_replay import encode
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            store.append(identifier,dict(sequence=0,ops=[['loop',0]],end=False))
            store.append(identifier,dict(sequence=1,ops=[['loop',1]],end=True))
            with store.connect() as db:
                db.execute('UPDATE chunks SET data=? WHERE id=? AND sequence=0',(b'broken zlib',identifier))
            with self.assertRaisesRegex(ValueError,'damaged'):store.read(identifier)
            stale=encode(dict(sequence=0,ops=[['loop',0]],end=True))
            with store.connect() as db:
                db.execute('UPDATE chunks SET data=?,digest=? WHERE id=? AND sequence=0',
                           (zlib.compress(stale),hashlib.sha256(stale).hexdigest(),identifier))
            with self.assertRaisesRegex(ValueError,'boundary'):store.read(identifier)

    def test_prefix_extension_rolls_back_all_chunks_and_retries_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            branch=dict(id=identifier,sequence=0)
            rows=[['loop',0],['frame',17],['state',[1,0]],['loop',1]]*600
            pack=store.pack_chunk;count=0
            def fail_second(data):
                nonlocal count
                count+=1
                if count==2:raise OSError('temporary disk failure')
                return pack(data)
            with mock.patch.object(store,'pack_chunk',side_effect=fail_second):
                with self.assertRaisesRegex(OSError,'disk failure'):store.extend_fork(branch,rows)
            self.assertEqual(branch,dict(id=identifier,sequence=0))
            self.assertEqual(store.read(identifier,require_complete=False)['ops'],[])
            with mock.patch.object(store,'connect',wraps=store.connect) as connect:
                store.extend_fork(branch,rows)
            self.assertEqual(connect.call_count,1)
            store.append(identifier,dict(sequence=branch['sequence'],ops=[],end=True))
            self.assertEqual(store.read(identifier)['ops'],rows)

    def test_immutable_browser_chunks_migrate_reopen_retry_and_fork(self):
        with tempfile.TemporaryDirectory() as directory:
            legacy, identifier = self.begin(directory)
            operations = [['loop',0],['clock',123],['loop',1]]
            legacy.append(identifier,dict(sequence=0,ops=operations,end=False))
            legacy.append(identifier,dict(sequence=1,ops=[['close']],end=True))
            before = legacy.read(identifier)
            browser = ClientJournals(directory,chunk_files=True)
            self.assertEqual(browser.migrate_chunks(),2)
            self.assertEqual(browser.migrate_chunks(),0)
            self.assertEqual(browser.read(identifier),before)
            files = {path.name:path.read_bytes() for path in browser.chunk_directory.iterdir()}
            browser.append(identifier,dict(sequence=1,ops=[['close']],end=True))
            self.assertEqual(files,{path.name:path.read_bytes() for path in browser.chunk_directory.iterdir()})
            branch = browser.fork(identifier,3)
            browser.append(branch['id'],dict(sequence=branch['sequence'],ops=[['close']],end=True))
            self.assertEqual(ClientJournals(directory).read(branch['id'])['ops'],before['ops'])
            # A missing payload must be reported; never return a partial replay.
            next(browser.chunk_directory.iterdir()).unlink()
            with self.assertRaisesRegex(ValueError,'missing'):browser.read(identifier)

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

    def test_adapter_changes_can_be_tried_only_with_the_same_original_client(self):
        with tempfile.TemporaryDirectory() as directory:
            store,identifier=self.begin(directory)
            store.append(identifier,dict(sequence=0,ops=[['loop',0],['loop',1]],end=True))
            value=store.statuses([identifier],'c'*64,client='a'*64,allow_compatible=True)[identifier]
            self.assertTrue(value['available']);self.assertEqual(value['reason'],'compatible')
            self.assertFalse(store.statuses([identifier],'c'*64,client='d'*64,allow_compatible=True)[identifier]['available'])
            self.assertFalse(store.statuses([identifier],'b'*64,client='d'*64,allow_compatible=True)[identifier]['available'])

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
