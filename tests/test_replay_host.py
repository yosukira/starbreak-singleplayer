import base64
import copy
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import uuid

from localserver import game_settings, pb_layout
from localserver.battle_records import RecordStore
from localserver.protocol import RC4, random_string
from localserver.replay import ServerCapture, ServerReplay, JournalWriter, read_journal
from tools.client_replay import ClientJournals
from tools.replay_host import Takeover
from tools.replay_stream import framed
from test_replay import encounter, input_at
from test_battle_records import sample
from tools.web_app import Prototype


class ReplayHostTests(unittest.TestCase):
    def test_playback_offers_takeover_only_with_matching_server_recording(self):
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,_=self.documents(directory)
            app=Prototype.__new__(Prototype);app.records=records
            self.assertTrue(app.replay_takeover_ready(request['id'],request['record']))
            self.assertTrue(app.replay_takeover_ready(request['id']))
            self.assertFalse(app.replay_takeover_ready(str(uuid.uuid4()),request['record']))
            path=Path(directory)/(request['record']+'.server.jsonl.gz')
            path.write_bytes(b'\x1f\x8b\x08')
            self.assertFalse(app.replay_takeover_ready(request['id'],request['record']))
            path.unlink()
            self.assertFalse(app.replay_takeover_ready(request['id'],request['record']))

    def documents(self, directory):
        records=RecordStore(directory);clients=ClientJournals(directory)
        attempt=str(uuid.uuid4());clients.begin(attempt,'a'*64,'b'*64,dict(key='LocalStorage.pb',revision=0,content=None),[1366,768])
        packets=[];ops=[];checkpoints=[]
        cipher=RC4(random_string(0,64))
        prefix=framed(8,b'joined')+framed(12,pb_layout.default_layouts().encode('Delta',{'createplayer_':{}}))
        ops.extend([['open'],['net',base64.b64encode(framed(1,b'anonymous Hello')).decode()],['send',53,0],['net',base64.b64encode(cipher.crypt(prefix)).decode()]])
        with game_settings.event_snapshot(game_settings.normalize({'map':'egy_hulk'})):
            session,bridge,server=encounter('egy_hulk')
            server._send_raw=lambda c,k,p,*a,**kw:packets.append(framed(k,p))
            capture=ServerCapture(session);capture.run('prepare')
            for tick in range(40):
                raw=input_at(tick);bridge._absorb(raw);capture.run('step',1/60)
                if packets:ops.append(['net',base64.b64encode(cipher.crypt(b''.join(packets))).decode()]);packets.clear()
                p=raw['player'];state=[p['room_seed'],0,1,2,p['xmp'],p['ymp'],p['wmp'],p['hmp'],500,500,1,12,0,1]
                ops.extend([['loop',0],['frame',17],['state',state],['loop',1]])
                checkpoints.append((len(ops),state))
        clients.append(attempt,dict(sequence=0,ops=ops,end=True))
        parent=sample();parent.update(seed=str(capture.session.seed),replay={'id':attempt},platform='web')
        records.save(parent)
        header=dict(capture.header,attempt=attempt)
        writer=JournalWriter(Path(directory)/(parent['id']+'.server.jsonl.gz'),header)
        for frame in capture.frames:writer.frame(frame)
        writer.close()
        cursor,state=checkpoints[19]
        request=dict(id=attempt,record=parent['id'],cursor=cursor,player=state)
        return records,clients,request,ops

    def test_warming_never_creates_a_battle_and_takeover_reuses_verified_state(self):
        from tools.replay_host import WarmTakeovers
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,_=self.documents(directory)
            warm=WarmTakeovers();warm.warm(records,clients,request)
            rebuilt=warm.prepared.rebuilt
            self.assertEqual(len(records.list()),1)
            with mock.patch('tools.replay_host.PreparedReplay',side_effect=AssertionError('Must reuse verified controller')):
                host=warm.take(records,clients,request,manual=True)
            self.assertIs(host.rebuilt,rebuilt)
            host.stop()

    def test_continues_verified_controller_and_keeps_independent_replay_and_history(self):
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,ops=self.documents(directory)
            host=Takeover(records,clients,request)
            self.assertEqual(clients.read(host.branch['id'],require_complete=False)['ops'],ops[:request['cursor']])
            transport=mock.Mock()
            host.bind(transport)
            with mock.patch('tools.replay_host.threading.Thread'):
                host.commit()
            with self.assertRaisesRegex(ValueError,'not found'):records.get(host.recorder.identifier)
            first=input_at(19);first['keys']['gameplay']=True
            host.telemetry(first);host.step(1/60)
            self.assertEqual(records.get(host.recorder.identifier)['parent']['id'],request['record'])
            self.assertEqual(records.get(host.recorder.identifier)['replay']['id'],host.branch['id'])
            for tick in range(20,40):
                host.telemetry(input_at(tick));host.step(1/60)
            death=input_at(40);death['player'].update(hp=0,alive=False)
            host.telemetry(death);host.step(1/60)
            self.assertEqual(records.get(host.recorder.identifier)['result'],'defeat')
            self.assertEqual(records.get(request['record'])['result'],'victory','Taking over never overwrites the original battle')
            host.stop()
            rebuilt=ServerReplay(read_journal(Path(directory)/(host.recorder.identifier+'.server.jsonl.gz')))
            while rebuilt.advance():pass
            self.assertGreater(rebuilt.index,20)
            self.assertGreater(transport.send.call_count,20)

    def test_invalid_checkpoint_or_incomplete_iteration_cannot_create_a_branch(self):
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,ops=self.documents(directory)
            wrong=copy.deepcopy(request);wrong['player'][4]+=1
            with self.assertRaisesRegex(ValueError,'not been verified'):Takeover(records,clients,wrong)
            wrong=dict(request,cursor=request['cursor']-1)
            with self.assertRaisesRegex(ValueError,'complete game iterations'):Takeover(records,clients,wrong)
            self.assertEqual(len(records.list()),1)
            with clients.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)

    def test_storage_failure_cannot_stop_the_taken_over_battle(self):
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,_=self.documents(directory)
            host=Takeover(records,clients,request);transport=mock.Mock();host.bind(transport)
            with mock.patch('tools.replay_host.threading.Thread'):host.commit()
            host.recorder.tick=mock.Mock(side_effect=OSError('disk full'))
            close=host.writer.close
            with mock.patch.object(host.writer,'close',side_effect=OSError('disk full')):
                host.telemetry(input_at(20));host.step(1/60)
            self.assertTrue(host.capture_closed)
            self.assertFalse(host.stopped.is_set())
            self.assertEqual(host.record_error,'OSError')
            count=transport.send.call_count
            host.telemetry(input_at(21));host.step(1/60)
            self.assertGreater(transport.send.call_count,count,'Gameplay continues after recorder failure')
            host.recorder.tick.assert_called_once()
            close(complete=False);host.stop()

    def test_first_history_write_failure_still_starts_live_gameplay(self):
        with tempfile.TemporaryDirectory() as directory:
            records,clients,request,_=self.documents(directory)
            host=Takeover(records,clients,request);transport=mock.Mock();host.bind(transport)
            host.recorder.tick=mock.Mock(side_effect=OSError('disk full'))
            with mock.patch('tools.replay_host.threading.Thread') as worker:
                host.commit();worker.return_value.start.assert_called_once()
            self.assertTrue(host.committed);self.assertTrue(host.capture_closed)
            count=transport.send.call_count
            host.telemetry(input_at(20));host.step(1/60)
            self.assertGreater(transport.send.call_count,count);host.stop()


if __name__=='__main__':unittest.main()
