import hashlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import uuid

from localserver.battle_records import RecordStore
from localserver.replay import JournalWriter,read_journal
from plugins._native_death import NativeDeath
from plugins._native_replay import wire_size
from tools.native_recording import NativeRecording
from tools.native_replay import NativeJournals
from tools import update
from test_battle_records import sample
from test_native_playback import Engine
from test_native_recording import manifest


class LifecycleTests(unittest.TestCase):
    def test_verified_native_death_survives_player_removal_but_loading_does_not_fail(self):
        ffi=mock.Mock(NULL=None);ffi.sizeof.return_value=4;ffi.cast.side_effect=lambda kind,value:100 if kind=='uintptr_t' else value
        code=b'test checked death function';ffi.buffer.return_value=code
        world=NS(asWorld=NS(props=NS(seed=99)));client=NS(clientWorld=world);refs=NS(WorldClient=client)
        hook=mock.Mock()
        with mock.patch('plugins._native_death.BODY',hashlib.sha256(code).hexdigest()):observer=NativeDeath(ffi,None,refs,0x400000,hook=hook)
        live=dict(objid=1,worldid=2,hp=10,alive=True,room_seed=99)
        observer.document(world,live)
        self.assertNotIn('alive',observer.document(world,dict(room_seed=99)),'Loading alone is not death')
        original=mock.Mock(return_value=7);self.assertEqual(observer.observe(original,client),7);original.assert_called_once_with(client)
        dead=observer.document(world,dict(room_seed=99));self.assertEqual((dead['objid'],dead['hp'],dead['alive']),(1,0,False))
        world.asWorld.props.seed+=1;self.assertNotIn('alive',observer.document(world,dict(room_seed=100)))

    def test_native_battle_is_replayable_before_the_recording_game_closes(self):
        with tempfile.TemporaryDirectory() as directory,mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
            owner=NativeRecording(directory,'owned.exe')
            try:
                engine=Engine();engine.record(10)
                for chunk in engine.chunks:owner.journals.append(owner.identifier,dict(chunk,end=False))
                store=RecordStore(directory);row=sample();row.update(map='hulk_mid',replay=dict(id=owner.identifier),platform='native');store.save(row)
                path=Path(directory)/(row['id']+'.server.jsonl.gz');writer=JournalWriter(path,dict(attempt=owner.identifier,world_sequence=1));writer.frame(dict(event='same exact frame'));writer.close()
                owner.finalize_battles(immediate=True)
                saved=store.get(row['id']);self.assertNotEqual(saved['replay']['id'],owner.identifier)
                sealed=owner.journals.read(saved['replay']['id']);self.assertEqual(sealed['ops'],[op for chunk in engine.chunks for op in chunk['ops']])
                journal=read_journal(path);self.assertEqual(journal['attempt'],sealed['id']);self.assertEqual(journal['frames'],[dict(event='same exact frame')])
                with owner.journals.connect() as db:self.assertEqual(db.execute('SELECT complete FROM attempts WHERE id=?',(owner.identifier,)).fetchone()[0],0)
                self.assertFalse(owner.closed)
            finally:owner.close()

    def test_local_delete_keeps_shared_tapes_until_last_reference_and_rejects_active(self):
        with tempfile.TemporaryDirectory() as directory:
            store=RecordStore(directory);clients=NativeJournals(directory);identifier=str(uuid.uuid4());clients.begin(identifier,'a'*64,'b'*64,dict(content=None),[800,600])
            with clients.connect() as db:db.execute('UPDATE attempts SET complete=1 WHERE id=?',(identifier,))
            first=sample();first['replay']=dict(id=identifier);second=dict(first,id=str(uuid.uuid4()));store.save(first);store.save(second)
            store.delete([first['id']])
            with clients.connect() as db:self.assertIsNotNone(db.execute('SELECT id FROM attempts WHERE id=?',(identifier,)).fetchone())
            active=sample();active.update(id=str(uuid.uuid4()),result='active');store.save(active)
            with self.assertRaisesRegex(ValueError,'Finish'):store.delete([second['id'],active['id']])
            self.assertEqual(len(store.list()),2)
            store.delete([second['id']])
            with clients.connect() as db:self.assertIsNone(db.execute('SELECT id FROM attempts WHERE id=?',(identifier,)).fetchone())

    def test_streamed_update_reports_actual_download_bytes_and_enforces_limit(self):
        class Response(io.BytesIO):
            headers={'Content-Length':'200000'}
            def geturl(self):return update.UPDATE_CHANNEL+'/starbreak-singleplayer-update-v20261020-r4.zip'
        seen=[];opener=NS(open=lambda *args,**kwargs:Response(b'a'*200000))
        with mock.patch.object(update,'build_opener',return_value=opener):
            self.assertEqual(len(update.fetch('starbreak-singleplayer-update-v20261020-r4.zip',300000,lambda n,total:seen.append((n,total)))),200000)
            with self.assertRaisesRegex(ValueError,'too large'):update.fetch('starbreak-singleplayer-update-v20261020-r4.zip',100000)
        self.assertEqual(seen[0],(0,200000));self.assertEqual(seen[-1],(200000,200000));self.assertGreater(len(seen),3)

    def test_player_death_has_priority_over_boss_victory(self):
        from localserver.battle_records import BattleRecorder,parameters
        from localserver.live.bridge import PlayerState
        from test_battle_records import participating
        player=PlayerState(objid=1,worldid=2,hp=100,alive=True)
        session=NS(options=NS(map='egy_run',player_type=1),run=NS(seed=1,completed=False),instance=NS(role='boss',seed=1,visits=1),current=NS(phase='fight'),bridge=NS(snapshot=lambda:(player,{'gameplay':True}),native_context={}),_ready=True)
        with tempfile.TemporaryDirectory() as directory:
            recorder=BattleRecorder(participating(session),RecordStore(directory))
            with mock.patch('localserver.battle_records.snapshot',return_value=parameters(sample()['parameters'])):
                recorder.tick(.1);player.hp=0;player.alive=False;session.current.phase='death';recorder.tick(.1)
            self.assertEqual(recorder.store.list()[0]['result'],'defeat')

    def test_identity_roundtrip_and_victory_text(self):
        from localserver import player_identity,pb_layout
        identity=dict(character_name='Daft',guild_name='Planetpune',callouts=True)
        self.assertEqual(player_identity.callout('Elite Temple of the Lost Boss',identity),'The Elite Temple of the Lost has been defeated! Daft of Planetpune gave an impressive performance!')
        self.assertNotIn(' of ',player_identity.callout('Elite Graveyard Boss',dict(identity,guild_name='')))
        with mock.patch.object(player_identity,'load',return_value=identity):
            player=player_identity.apply(dict(username_='Localplayer',factionname_='old'))
            decoded=pb_layout.default_layouts().decode('CreatePlayer',pb_layout.default_layouts().encode('CreatePlayer',player))
            self.assertEqual(decoded['username_'],'Daft');self.assertEqual(decoded['factionname_'],'Planetpune')
            server=NS(layouts=pb_layout.default_layouts(),_send_raw=mock.Mock());channel=object()
            player_identity.announce(NS(server=server,channel=channel),'Elite Graveyard Boss')
            args=server._send_raw.call_args.args
            chat=server.layouts.decode('Chat',args[2]);self.assertEqual(chat['chattype_'],3)
            self.assertIn('Daft of Planetpune gave',chat['message_'])
        with mock.patch.object(player_identity,'load',return_value=dict(identity,callouts=False)):
            server._send_raw.reset_mock();player_identity.announce(NS(server=server,channel=channel),'Elite Graveyard Boss');server._send_raw.assert_not_called()

    def test_fast_sizes_are_exact_for_every_native_operation_shape(self):
        source=Engine().record(4)
        for row in source['ops']:self.assertEqual(wire_size(row),len(json.dumps(row,separators=(',',':')).encode()))


if __name__=='__main__':unittest.main()
