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

from localserver.battle_records import RecordStore
from localserver import game_settings
from localserver.replay import ServerReplay, ServerCapture, read_journal
from tools.client_replay import ClientJournals
from tools.replay_bundle import MAGIC, read_battle, server_document
from tools.replay_host import Takeover
import test_replay_host
from test_battle_records import sample
from test_replay import encounter, input_at


class PortableReplayTests(unittest.TestCase):
    def test_rolled_encounters_keep_geometry_seed_through_portable_validation(self):
        for map_id in ('ice_boss','forest_mid','jungle_boss'):
            with self.subTest(map=map_id),game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                session,bridge,_=encounter(map_id,room_seed=98765,worldid=7)
                capture=ServerCapture(session);capture.run('prepare')
                for tick in range(5):
                    bridge._absorb(input_at(tick));capture.run('step',.05)
                value=dict(capture.document(),attempt=str(uuid.uuid4()),private='not-exported')
                portable=server_document(json.loads(json.dumps(value)))
                self.assertEqual(portable['room_seed'],'98765')
                self.assertEqual(portable['worldid'],7)
                self.assertNotIn('private',portable)
                rebuilt=ServerReplay(portable)
                while rebuilt.advance():pass
                self.assertEqual(rebuilt.session.phase,session.phase)
                with self.assertRaises(ValueError):server_document(dict(value,room_seed='-1'))

    def fixture(self,directory):
        return test_replay_host.ReplayHostTests().documents(directory)

    def test_browser_filesystem_without_hard_links_imports_and_preserves_collisions(self):
        from localserver.battle_records import publish_replay
        with tempfile.TemporaryDirectory() as source,tempfile.TemporaryDirectory() as target:
            store,_,request,ops=self.fixture(source)
            _,blob=store.export([request['record']]);imported=RecordStore(target)
            with mock.patch('localserver.battle_records.os.link',None):
                self.assertEqual(imported.import_bytes(blob)['imported'],1)
                record=imported.get(request['record'])
                self.assertEqual(ClientJournals(target).read(record['replay']['id'])['ops'],ops)
                self.assertTrue((Path(target)/(record['id']+'.server.jsonl.gz')).is_file())
                original=Path(target)/'existing';original.write_bytes(b'keep')
                with self.assertRaises(FileExistsError):publish_replay(Path(target)/(record['id']+'.server.jsonl.gz'),original)
                self.assertEqual(original.read_bytes(),b'keep')
                destination=Path(target)/'partial'
                with mock.patch('localserver.battle_records.shutil.copyfileobj',side_effect=OSError('Disk full')):
                    with self.assertRaises(OSError):publish_replay(original,destination)
                self.assertFalse(destination.exists())

    def test_complete_roundtrip_plays_and_can_take_over_without_original_files(self):
        with tempfile.TemporaryDirectory() as source,tempfile.TemporaryDirectory() as target:
            store,clients,request,ops=self.fixture(source)
            name,blob=store.export([request['record']])
            self.assertTrue(name.startswith('Wildfire-'))
            self.assertTrue(name.endswith('.sbreplay'));self.assertTrue(blob.startswith(MAGIC))
            imported=RecordStore(target)
            self.assertEqual(imported.import_bytes(blob),dict(imported=1,duplicates=0))
            record=imported.get(request['record']);identifier=record['replay']['id']
            self.assertEqual(record['source'],'import');self.assertNotEqual(identifier,request['id'])
            destination=ClientJournals(target)
            self.assertEqual(destination.read(identifier)['ops'],ops)
            self.assertEqual(clients.read(request['id'])['ops'],ops)
            self.assertTrue(destination.statuses([identifier],'b'*64)[identifier]['available'])
            server=read_journal(Path(target)/(record['id']+'.server.jsonl.gz'))
            rebuilt=ServerReplay(server)
            while rebuilt.advance():pass
            self.assertEqual(rebuilt.index,41)
            host=Takeover(imported,destination,dict(request,id=identifier));host.stop()
            self.assertEqual(imported.import_bytes(blob),dict(imported=0,duplicates=1))
            with destination.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],2)
            # Re-exported imported records carry their independently stored tape.
            _,second=imported.export([record['id']])
            self.assertEqual(read_battle(second)[1]['ops'],ops)

    def test_mixed_batch_preserves_legacy_records_and_unique_names(self):
        with tempfile.TemporaryDirectory() as source,tempfile.TemporaryDirectory() as target:
            store,_,request,_=self.fixture(source)
            legacy=sample();store.save(legacy)
            _,blob=store.export([request['record'],legacy['id']])
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                self.assertEqual(len(archive.namelist()),2)
            imported=RecordStore(target);self.assertEqual(imported.import_bytes(blob)['imported'],2)
            self.assertEqual(imported.get(legacy['id'])['replay'],dict(available=False,reason='not-recorded'))

    def test_rejects_executable_operations_and_oversize_before_writing_any_batch(self):
        with tempfile.TemporaryDirectory() as source,tempfile.TemporaryDirectory() as target:
            store,_,request,_=self.fixture(source);_,blob=store.export([request['record']])
            value=json.loads(gzip.decompress(blob[len(MAGIC):]));value['client']['ops'].append(['eval','execute'])
            broken=MAGIC+gzip.compress(json.dumps(value).encode())
            output=io.BytesIO()
            with zipfile.ZipFile(output,'w') as archive:
                archive.writestr('valid.sbreplay',blob);archive.writestr('broken.sbreplay',broken)
            imported=RecordStore(target)
            with self.assertRaisesRegex(ValueError,'Unsupported client replay operation'):imported.import_bytes(output.getvalue())
            self.assertEqual(imported.list(),[])
            self.assertEqual(list(Path(target).glob('*.gz')),[])
            with mock.patch('tools.replay_bundle.MAX_EXPANDED',10):
                with self.assertRaisesRegex(ValueError,'too large'):imported.import_bytes(blob)

    def test_unknown_metadata_is_not_reexported_and_original_age_is_unchanged(self):
        with tempfile.TemporaryDirectory() as source:
            store,_,request,_=self.fixture(source);_,blob=store.export([request['record']])
            value=json.loads(gzip.decompress(blob[len(MAGIC):]));value['client']['private']='private-client'
            value['server']['private']='private-server'
            frame=next(f for f in value['server']['frames'] if any(c['method']=='snapshot' for c in f['calls']))
            before=copy.deepcopy(frame)
            value['server']['frames'][0]['private']='private-frame'
            parsed=read_battle(MAGIC+gzip.compress(json.dumps(value).encode()))
            self.assertNotIn('private-client',json.dumps(parsed));self.assertNotIn('private-server',json.dumps(parsed));self.assertNotIn('private-frame',json.dumps(parsed))
            self.assertEqual(parsed[2]['frames'][value['server']['frames'].index(frame)],before)


if __name__=='__main__':unittest.main()
