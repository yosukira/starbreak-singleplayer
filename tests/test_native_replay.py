import ast
import base64
import copy
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid
import zipfile

from plugins._native_replay import NativeTape,operation,validate_operations,SCHEMA
from tools.native_replay import NativeJournals,document
from tools.client_replay import ClientJournals
from tools.replay_bundle import MAGIC,read_battle
from localserver.battle_records import RecordStore
from test_battle_records import sample

STATE=[123,1,1,2,100,200,10240,20480,150,150,1,12,4,1]


def frame(tape,unrecorded=10,state=None):
    tape.loop(0)
    input_clock=tape.call(['input',4,1],lambda:tape.value('clock',lambda:unrecorded))
    tape.call(['delta',12,base64.b64encode(b'\x08\x01').decode()],lambda:None)
    def update():
        return(tape.value('clock',lambda:unrecorded+1),
               tape.value('random32',lambda:123),tape.value('random64',lambda:2**64-1),
               tape.value('step',lambda:17))
    result=tape.call(['frame'],update)
    tape.loop(1,STATE if state is None else state)
    return input_clock,result


def tape_document(directory,frames=2):
    store=NativeJournals(directory);identifier=str(uuid.uuid4())
    store.begin(identifier,'a'*64,'b'*64,dict(content=None),[1366,768])
    tape=NativeTape(save=lambda chunk:store.append(identifier,chunk),keep_ops=True)
    for _ in range(frames):frame(tape)
    tape.finish()
    return store,identifier,tape


class NativeExecutionTests(unittest.TestCase):
    def test_original_call_order_scalar_replay_and_player_checks(self):
        recorded=NativeTape(keep_ops=True);expected=frame(recorded);recorded.finish()
        played=NativeTape(mode='play',ops=recorded.ops)
        self.assertFalse(played.can_takeover())
        actual=frame(played,99999)
        self.assertEqual(actual,expected)
        self.assertEqual(actual[1][2],2**64-1,'64-bit seeds survive the JSON-safe limb format')
        self.assertIsNone(played.error);self.assertEqual(played.cursor,len(recorded.ops))
        self.assertEqual(played.elapsed,17)
        self.assertTrue(played.can_takeover())
        self.assertEqual(played.value('clock',lambda:71),71,'Unrelated UI clocks are not virtualized')

    def test_mismatch_ends_playback_without_claiming_takeover(self):
        original=NativeTape(keep_ops=True);frame(original);original.finish()
        played=NativeTape(mode='play',ops=original.ops)
        altered=list(STATE);altered[4]+=1
        frame(played,state=altered)
        self.assertIsNotNone(played.error);self.assertFalse(played.can_takeover())
        changed=copy.deepcopy(original.ops);changed[2][1]+=1
        played=NativeTape(mode='play',ops=changed)
        played.loop(0)
        with self.assertRaises(ValueError):played.call(['input',1,1],lambda:None)
        self.assertIsNotNone(played.error)

    def test_recording_storage_errors_leave_live_original_calls_running(self):
        sink=mock.Mock(side_effect=OSError('/private/account/path'))
        tape=NativeTape(save=sink)
        for _ in range(80):frame(tape)
        self.assertEqual(tape.error,'OSError')
        original=mock.Mock(return_value=19)
        self.assertEqual(tape.call(['frame'],original),19);original.assert_called_once()
        self.assertNotIn('private',tape.error)
        tape.finish();sink.assert_called_once()
        broken=NativeTape();original.reset_mock()
        self.assertEqual(broken.call(['native-code','not-permitted'],original),19)
        original.assert_called_once()

    def test_lifecycle_validation_rejects_half_calls_unknown_data_and_invalid_order(self):
        native=NativeTape(keep_ops=True);frame(native);native.finish()
        validate_operations(native.ops)
        for bad in([['loop',0],['loop',1]], [['frame']], [['clock',1]],
                    [['input',4,1],['return','frame']],native.ops[:-1]):
            with self.subTest(rows=bad[:2]),self.assertRaises(ValueError):validate_operations(bad)
        for bad in(['address',4194304],['heap','memory'],['account','private'],['random64',2**64,0],
                    ['delta',3,'YQ=='],['delta',12,'invalid'],['input',16384,1]):
            with self.subTest(kind=bad[0]),self.assertRaises(ValueError):operation(bad)
        unfinished=NativeTape(keep_ops=True);unfinished.loop(0);unfinished.finish()
        self.assertIsNotNone(unfinished.error)
        with self.assertRaises(ValueError):unfinished.continue_recording(lambda _:None,0)

    def test_plugin_uses_the_embedded_python35_syntax(self):
        root=Path(__file__).resolve().parents[1]
        ast.parse((root/'plugins/_native_replay.py').read_text(),feature_version=(3,5))


class NativeJournalTests(unittest.TestCase):
    def test_portable_branch_preserves_engine_history_and_cross_end_bundle(self):
        with tempfile.TemporaryDirectory() as source,tempfile.TemporaryDirectory() as target:
            native,identifier,tape=tape_document(source)
            original=native.read(identifier);cursor=original['ops'].index(['loop',1])+1
            fork=native.fork(identifier,cursor)
            runner=dict(client='c'*64,adapter='d'*64)
            native.mark_runner(fork['id'],original,cursor,runner)
            native.append(fork['id'],dict(sequence=fork['sequence'],ops=[],end=True))
            document=native.read(fork['id'])
            self.assertEqual(document['runners'],[dict(cursor=cursor,engine='browser',**runner)])
            self.assertNotIn('runners',native.read(identifier))
            records=RecordStore(source);entry=sample();entry.update(platform='web',replay=dict(id=fork['id'],format='native'))
            records.save(entry);_,blob=records.export([entry['id']])
            dest=RecordStore(target);dest.import_bytes(blob)
            _,again=dest.export([entry['id']])
            self.assertEqual(read_battle(again)[1]['runners'],document['runners'])
            altered=copy.deepcopy(document);altered['runners'][0]['cursor']-=1
            with self.assertRaises(ValueError):globals()['document'](altered)

    def test_atomic_chunks_native_web_isolation_and_forked_continuation(self):
        with tempfile.TemporaryDirectory()as directory:
            native,identifier,recorded=tape_document(directory)
            web=ClientJournals(directory)
            self.assertEqual(native.read(identifier)['ops'],recorded.ops)
            with self.assertRaisesRegex(ValueError,'another edition'):web.read(identifier)
            with self.assertRaisesRegex(ValueError,'another edition'):web.append(identifier,dict(sequence=0,ops=[],end=True))
            self.assertFalse(web.statuses([identifier],'b'*64)[identifier]['available'])
            self.assertFalse(native.statuses([identifier],'b'*64)[identifier]['available'])
            self.assertTrue(native.statuses([identifier],'b'*64,'a'*64)[identifier]['available'])
            played=NativeTape(mode='play',ops=recorded.ops,keep_ops=True);frame(played)
            fork=native.fork(identifier,played.cursor)
            played.continue_recording(lambda chunk:native.append(fork['id'],chunk),fork['sequence'])
            frame(played,unrecorded=777);played.finish()
            self.assertEqual(native.read(identifier)['ops'],recorded.ops)
            self.assertEqual(native.read(fork['id'])['ops'],played.ops)
            self.assertNotEqual(played.ops,recorded.ops)

    def test_native_exports_roundtrip_in_a_mixed_batch_without_code_or_private_metadata(self):
        with tempfile.TemporaryDirectory()as source,tempfile.TemporaryDirectory()as target:
            native,identifier,recorded=tape_document(source)
            records=RecordStore(source);entry=sample()
            entry.update(platform='native',replay=dict(id=identifier))
            records.save(entry)
            legacy=sample();records.save(legacy)
            _,blob=records.export([entry['id']])
            self.assertTrue(blob.startswith(MAGIC))
            decoded=read_battle(blob)
            self.assertEqual(decoded[1]['schema'],SCHEMA)
            self.assertEqual(decoded[1]['ops'],recorded.ops)
            raw=json.loads(gzip.decompress(blob[len(MAGIC):]))
            raw['client'].update(account='private-account',code_addresses={'bad':1234})
            self.assertNotIn('code_addresses',document(raw['client']))
            _,batch=records.export([entry['id'],legacy['id']])
            dest=RecordStore(target)
            self.assertEqual(dest.import_bytes(batch),dict(imported=2,duplicates=0))
            imported=dest.get(entry['id']);newid=imported['replay']['id']
            self.assertNotEqual(newid,identifier)
            self.assertEqual(NativeJournals(target).read(newid)['ops'],recorded.ops)
            _,again=dest.export([entry['id']])
            self.assertEqual(read_battle(again)[1]['ops'],recorded.ops)
            self.assertEqual(dest.import_bytes(batch),dict(imported=0,duplicates=2))
            raw['record']['platform']='web'
            with self.assertRaisesRegex(ValueError,'editions'):read_battle(MAGIC+gzip.compress(json.dumps(raw).encode()))

    def test_incomplete_native_tapes_and_executable_imports_do_not_replace_records(self):
        with tempfile.TemporaryDirectory()as source,tempfile.TemporaryDirectory()as target:
            native,identifier,_=tape_document(source)
            records=RecordStore(source);entry=sample();entry.update(platform='native',replay=dict(id=identifier));records.save(entry)
            _,blob=records.export([entry['id']]);raw=json.loads(gzip.decompress(blob[len(MAGIC):]))
            raw['client']['ops'].insert(0,['load-code',1234])
            broken=MAGIC+gzip.compress(json.dumps(raw).encode())
            batch=io.BytesIO()
            with zipfile.ZipFile(batch,'w')as archive:
                archive.writestr('valid.sbreplay',blob);archive.writestr('invalid.sbreplay',broken)
            dest=RecordStore(target)
            with self.assertRaises(ValueError):dest.import_bytes(batch.getvalue())
            self.assertEqual(dest.list(),[])
            with NativeJournals(target).connect()as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)
