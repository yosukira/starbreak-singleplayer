import contextlib
import base64
import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest import mock

from localserver import game_settings,paths
from localserver.live.bridge import PlayerBridge
from localserver.replay import FULL_RUN_MAPS,restore_player,player_document
from localserver.run_replay import RunCapture,RunReplay
from localserver.server import LocalServer,parse_args,_map_session_class


class WholeRun:
    def __init__(self,map_id,integrated=False):
        self.integrated=integrated
        info=game_settings.map_info(map_id)
        self.options=parse_args(['--map',map_id,'--live-seed','123','--pvid',info['pvid'],
            '--zone',info['zone'],'--gdv',str(info.get('gdv',1)),
            '--class-loadouts',str(paths.ROOT/'data/defaults/class_loadouts.json')])
        self.options.log_file=None
        self.server=LocalServer(self.options);self.server._live_world_sequence=1
        self.channel=SimpleNamespace(send_frame=lambda *a:None);self.bridge=PlayerBridge(0);self.seq=0
        self.session=_map_session_class(map_id)(self.server,self.channel,self.options,bridge=self.bridge)
        self.server.live_session=self.session
        if integrated:self.attach()
        else:
            self.capture=RunCapture(self.session);self.capture.enter(self.session)

    def attach(self):
        from localserver.battle_records import attach
        attach(self.session)
        self.capture=self.session.run.battle_recording.capture

    def feedback(self,pos=None,interact=False,changed_items=False,events=None,native_server_t=None):
        session=self.session;context=self.bridge.native_context
        x,y=pos or session.run.destination_spawn(session.run.current,session.run.entry)
        player=dict(objid=1,worldid=2,xmp=x,ymp=y,wmp=10240,hmp=20480,hp=500,maxhp=500,alive=True,
            room_seed=context['room_seed'],ammo=8,ec=777,inventory_protocol='full-run-inventory-v1',
            account='private-player',password='private-password')
        control=context.get('inventory_request')
        if control and (native_server_t is None or native_server_t>=control.get('native_t_min',0)):
            issued=self.server.layouts.decode('Delta',self.server.player_delta_blob)['createplayer_']
            items=copy.deepcopy(issued['charprops_']['items_'])+copy.deepcopy(issued.get('accountprops_',{}).get('progress_',{}).get('stash_',[]))
            if changed_items and not any(item['slotpos_']==4097 for item in items):
                stash=copy.deepcopy(items[0]);stash['slotpos_']=4097;items.append(stash)
                items[0].setdefault('statboosts_',[]).append(dict(stat_=9,val_=17))
            player['inventory']=dict(schema='full-run-inventory-v1',sampled_at=time.time(),
                request_id=control['id'],request_attempt=control['attempt'],native_server_t=2147483647 if native_server_t is None else native_server_t,
                scope=copy.deepcopy(control['scope']),ok=True,items_ok=True,ec_ok=True,ec=777,count=len(items),items=items,
                slots=[dict(map_key=i['slotpos_'],native_slotpos=i['slotpos_'],serialized_slotpos=i['slotpos_'],type=i['type_'],owner_ok=True)for i in items],
                quality=dict(stable=True,slot_source='Item.slotPos',declared_count=len(items),traversed_count=len(items),abi=dict(name='web-1.14.6-items-v1'),errors=[]),
                account='private-inventory',unknown_path='/private/not-exported')
        self.seq+=1
        self.bridge._absorb(dict(seq=self.seq,room_context=context,player=player,keys=dict(interact=interact,gameplay=True),
            events=[dict(event,native_seed=context['room_seed']) for event in events or []]))
        (self.session.step if self.integrated else self.capture.step)(.02)

    def travel(self,destination,changed_items=False):
        old=self.session
        oid,link=next((oid,link)for oid,link in old.instance.recipe['links'].items()
            if link['destination']==destination and old.run.allows(link))
        props=old.instance.snapshot.properties_at((1,int(oid)))
        if link.get('trigger') in ('boundary', 'native-door-boundary'):
            props=old.boundary_properties(oid,link)
        point=(props['xmp_'],props['ymp_'])
        self.feedback((point[0]-100000,point[1]),False)
        self.feedback(point,True)
        for _ in range(12):
            if self.server.live_session is None:break
            self.feedback(point,False,changed_items)
        if self.server.live_session is not None:
            raise AssertionError('The real inventory handshake did not commit this door')
        bridge=self.server._restart_bridge
        self.server._live_world_sequence+=1
        self.session=_map_session_class(self.options.map)(self.server,self.channel,self.options,bridge=bridge)
        self.server.live_session=self.session;self.server._restart_in_progress=False
        self.server._restart_bridge=self.server._restart_player_type=None
        self.server._pending_pvids=[];self.server.pending_player_delta=False
        if self.integrated:self.attach()
        else:self.capture.enter(self.session)
        self.feedback()


class RunReplayTests(unittest.TestCase):
    def test_inventory_retry_does_not_outrun_a_client_300ms_behind(self):
        with contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map='egy_run'))):
            source=WholeRun('egy_run')
            wall=time.time()
            with mock.patch('localserver.server.time.time',side_effect=lambda:wall):
                source.feedback()
                oid=next(oid for oid,link in source.session.instance.recipe['links'].items() if link['destination']=='hall')
                p=source.session.instance.snapshot.properties_at((1,oid));point=(p['xmp_'],p['ymp_'])
                source.feedback(point,True,native_server_t=0)
                first=None;attempts=set()
                for _ in range(60):
                    wall+=.02
                    control=source.bridge.native_context.get('inventory_request')
                    if control:
                        attempts.add(control['attempt'])
                        if first is None:first=control['native_t_min']
                        self.assertEqual(control['native_t_min'],first,'A retry must not move the TCP barrier')
                    native=round((source.session.instance.snapshot.time-source.session._snapshot_native_origin)*1000)-300
                    source.feedback(point,False,native_server_t=native)
                    if source.server.live_session is None:break
                self.assertIn(2,attempts,'Exercise a real retry while waiting for the clock')
                self.assertIsNone(source.server.live_session,'Fresh inventory must commit after catching up')

    def test_new_inventory_effect_raises_the_barrier_after_retry(self):
        session=SimpleNamespace(bridge=PlayerBridge(0),instance=SimpleNamespace(snapshot=SimpleNamespace(time=10.0)),_snapshot_native_origin=5.0)
        control=dict(id=3,attempt=1,issued_at=100,scope={'room_token':2})
        LocalServer._publish_inventory_control(session,control)
        self.assertEqual(session.bridge.native_context['inventory_request']['native_t_min'],5001)
        session.instance.snapshot.time=10.4
        LocalServer._publish_inventory_control(session,dict(control,attempt=2,issued_at=100.25))
        self.assertEqual(session.bridge.native_context['inventory_request']['native_t_min'],5001)
        LocalServer._publish_inventory_control(session,None)
        LocalServer._publish_inventory_control(session,dict(control,attempt=3,issued_at=100.4))
        self.assertEqual(session.bridge.native_context['inventory_request']['native_t_min'],5401)

    def test_recorded_teleport_receipts_rearm_commands_without_reusing_serials(self):
        from localserver.run_replay import RunBridge
        from localserver.replay import events_document
        bridge=RunBridge()
        bridge.native_context=dict(map='eub_run',room_ready=True,room_seed=123,room_token=1)
        player=restore_player(dict(objid=1,worldid=2,hp=500,alive=True,age=0))
        self.assertTrue(bridge.request_player_teleport(40,player,[100,200]))
        bridge.begin([dict(method='poll_events',args=[],value=events_document([
            dict(kind='player_teleport_result',command_id=1,source_oid=40,applied=True,password='never-exported')]))])
        receipt=bridge.poll_events()[0];bridge.end()
        self.assertEqual(receipt['source_oid'],40);self.assertTrue(receipt['applied'])
        self.assertNotIn('password',receipt)
        self.assertIsNone(bridge._teleport_command)
        self.assertTrue(bridge.request_player_teleport(41,player,[300,400]))
        self.assertEqual(bridge._teleport_command['command_id'],2)

    def test_browser_host_takes_over_second_room_and_uses_real_loading_handshake(self):
        from localserver.protocol import RC4,random_string
        from localserver.replay import JournalWriter,read_journal
        from localserver.battle_records import RecordStore
        from tools.client_replay import ClientJournals
        from tools.replay_host import Takeover
        from tools.replay_stream import framed
        from test_battle_records import sample
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()),\
                game_settings.event_snapshot(game_settings.normalize(dict(map='eub_run'))):
            source=WholeRun('eub_run');plain=[];ops=[];checkpoints=[]
            cipher=RC4(random_string(0,64))
            ops.extend([['open'],['net',base64.b64encode(framed(1,b'anonymous Hello')).decode()],['send',53,0]])
            plain.append(framed(12,source.server.player_delta_blob))
            source.channel.send_frame=lambda k,p:plain.append(framed(k,p))
            source.capture.sink=lambda row:plain.append(framed(12,source.server.player_delta_blob)) if row['action']=='enter' else None
            original=source.feedback
            def feedback(*args,**kwargs):
                original(*args,**kwargs)
                if plain:
                    ops.append(['net',base64.b64encode(cipher.crypt(b''.join(plain))).decode()]);plain.clear()
                p=source.bridge.state
                if p is not None:
                    state=[p.raw['room_seed'],0,p.objid,p.worldid,p.xmp,p.ymp,p.wmp,p.hmp,p.hp,p.maxhp,p.facing,8,0,1]
                    ops.extend([['loop',0],['frame',20],['state',state],['loop',1]])
                    checkpoints.append((len(ops),state,source.session.instance.role))
            source.feedback=feedback
            for _ in range(10):feedback()
            destination=next(link['destination']for link in source.session.instance.recipe['links'].values()
                if link['destination']in source.session.run.pack.room_names and source.session.run.allows(link))
            source.travel(destination)
            for _ in range(15):feedback()
            source.travel('entry')
            for _ in range(10):feedback()
            records=RecordStore(directory);clients=ClientJournals(directory);attempt=str(uuid.uuid4())
            clients.begin(attempt,'a'*64,'b'*64,dict(key='LocalStorage.pb',revision=0,content=None),[1366,768])
            clients.append(attempt,dict(sequence=0,ops=ops,end=True))
            doc=source.capture.document();doc['attempt']=attempt
            parent=sample();parent.update(map='eub_run',map_title='Elite Underground Base',seed=doc['seed'],
                parameters=doc['parameters'],replay=dict(id=attempt),platform='web',result='interrupted')
            records.save(parent)
            writer=JournalWriter(Path(directory)/(parent['id']+'.server.jsonl.gz'),{k:v for k,v in doc.items()if k!='frames'})
            for row in doc['frames']:writer.frame(row)
            writer.close()
            cursor,state,_=next(row for row in checkpoints if row[2]==destination)
            host=Takeover(records,clients,dict(record=parent['id'],id=attempt,cursor=cursor,player=state))
            try:
                self.assertEqual(host.session.instance.role,destination)
                host.bind(mock.Mock())
                with mock.patch('tools.replay_host.threading.Thread'):host.commit()
                continued=WholeRun.__new__(WholeRun)
                continued.options=host.server.options;continued.server=host.server;continued.bridge=host.bridge
                continued.integrated=False;continued.seq=0;continued.session=host.session
                continued.capture=SimpleNamespace(step=host.step)
                for _ in range(5):continued.feedback()
                old=host.session
                oid,link=next((oid,link)for oid,link in old.instance.recipe['links'].items() if link['destination']=='entry')
                point=old.instance.snapshot.properties_at((1,oid));x,y=point['xmp_'],point['ymp_']
                continued.feedback((x-100000,y));continued.feedback((x,y),True)
                for _ in range(12):
                    if host.server.live_session is None:break
                    continued.feedback((x,y),False,True)
                self.assertIsNone(host.server.live_session)
                # Feed encrypted ACKs through the host's normal frame queue;
                # LocalServer, rather than the fixture, creates the next room.
                tx=RC4(random_string(0,64))
                def deliver(kind):
                    host.binary(tx.crypt(framed(kind,b'')))
                    read=host.channel.read_frame
                    def next_message():
                        if host.channel.messages.empty():raise ConnectionError('test queue drained')
                        return read()
                    with mock.patch.object(host.channel,'read_frame',side_effect=next_message):
                        with self.assertRaisesRegex(ConnectionError,'test queue drained'):host.server._read_loop(host.channel)
                for _ in range(10):
                    deliver(22)
                    if host.server.pending_player_delta:break
                self.assertTrue(host.server.pending_player_delta)
                deliver(11)
                self.assertEqual(host.session.instance.role,'entry')
                self.assertFalse(host.stopped.is_set())
                continued.session=host.session
                for _ in range(10):continued.feedback()
                branch_id=host.recorder.identifier
            finally:host.stop()
            branch=RunReplay(read_journal(Path(directory)/(branch_id+'.server.jsonl.gz')))
            try:
                while branch.advance():pass
                self.assertEqual(branch.session.instance.role,'entry')
                self.assertEqual(branch.session.run.status(),host.session.run.status())
            finally:branch.close()
            self.assertEqual(records.get(branch_id)['parent']['id'],parent['id'])

    def test_reconstructed_run_continues_through_a_real_door_and_can_replay_again(self):
        for map_id in ('egy_run','eub_run'):
            with self.subTest(map=map_id),contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                source=WholeRun(map_id)
                for _ in range(10):source.feedback()
                document=source.capture.document();replay=RunReplay(document)
                try:
                    while replay.advance():pass
                    continued=WholeRun.__new__(WholeRun)
                    continued.integrated=False;continued.server=replay.server;continued.options=replay.server.options
                    continued.seq=0;continued.bridge=PlayerBridge(0);continued.channel=SimpleNamespace(send_frame=lambda *a:None)
                    continued.session=replay.continue_live(continued.bridge,continued.channel)
                    self.assertNotIn('_recorded_wall_time',vars(continued.session))
                    continued.capture=RunCapture(continued.session)
                    continued.capture.header={k:v for k,v in document.items()if k!='frames'}
                    continued.capture.frames=copy.deepcopy(document['frames'])
                    continued.capture.frame_count=len(continued.capture.frames)
                    continued.capture.enter(continued.session,record=False)
                    for _ in range(20):continued.feedback()
                    destination=next(link['destination']for link in continued.session.instance.recipe['links'].values()
                        if link['destination']in continued.session.run.pack.room_names and continued.session.run.allows(link))
                    continued.travel(destination,True)
                    for _ in range(10):continued.feedback()
                    branch=RunReplay(continued.capture.document())
                    try:
                        while branch.advance():pass
                        self.assertEqual(branch.session.run.status(),continued.session.run.status())
                        self.assertEqual(branch.server.player_delta_blob,continued.server.player_delta_blob)
                    finally:branch.close()
                finally:replay.close()

    def test_wire_alignment_tracks_room_handshakes_and_refuses_loading_gaps(self):
        from localserver.protocol import RC4,random_string
        from tools.replay_stream import ReplayStream,framed
        with contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map='eub_run'))):
            source=WholeRun('eub_run');plain=[]
            cp=lambda:framed(12,source.server.player_delta_blob)
            plain.append(cp());source.channel.send_frame=lambda k,p:plain.append(framed(k,p))
            source.capture.sink=lambda row:plain.extend([framed(10,b'room world'),cp()]) if row['action']=='enter' else None
            for _ in range(10):source.feedback()
            destination=next(link['destination']for link in source.session.instance.recipe['links'].values()
                if link['destination']in source.session.run.pack.room_names and source.session.run.allows(link))
            source.travel(destination)
            for _ in range(10):source.feedback()
            source.travel('entry')
            for _ in range(10):source.feedback()
            doc=source.capture.document()
            raw=framed(1,b'anonymous Hello')+RC4(random_string(0,64)).crypt(b''.join(plain))
            ops=[['open'],['send',53,0]]
            for offset in range(0,len(raw),97):ops.append(['net',base64.b64encode(raw[offset:offset+97]).decode()])
            stream=ReplayStream(dict(ops=ops),doc)
            self.assertEqual(len(stream.boundaries),sum(bool(row['packets'])for row in doc['frames']))
            for boundary in stream.boundaries:
                selected,suffix=stream.catchup(boundary.start+1)
                self.assertEqual(selected,boundary)
                self.assertEqual(stream.transmitter(boundary.start+1).crypt(suffix),raw[boundary.start+1:boundary.end])
            gaps=[(a.end,b.start)for a,b in zip(stream.boundaries,stream.boundaries[1:])if a.end<b.start]
            self.assertEqual(len(gaps),2)
            for start,end in gaps:
                with self.assertRaisesRegex(ValueError,'room loading'):stream.catchup(start+1)

    def test_public_attach_keeps_one_journal_and_record_across_room_loads(self):
        from localserver.replay import read_journal
        from tools.replay_bundle import server_document
        with tempfile.TemporaryDirectory() as directory,mock.patch.dict('os.environ',{
            'SB_BATTLE_RECORDS_PATH':directory,'SB_RECORD_REPLAY':'1',
            'SB_REPLAY_ATTEMPT':'a839a439-1ed5-492b-9553-cd8b2a11e6ac'}),\
                contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map='eub_run'))):
            source=WholeRun('eub_run',integrated=True)
            for _ in range(10):source.feedback()
            recorder=source.session.battle_recorder
            destination=next(link['destination']for link in source.session.instance.recipe['links'].values()
                if link['destination']in source.session.run.pack.room_names and source.session.run.allows(link))
            source.travel(destination,changed_items=True)
            self.assertIs(source.session.battle_recorder,recorder)
            self.assertFalse(recorder.closed)
            for _ in range(10):source.feedback()
            source.travel('entry')
            source.session.stop()
            self.assertTrue(recorder.closed)
            self.assertEqual(len(recorder.document['rooms']),3)
            files=list(Path(directory).glob('*.server.jsonl.gz'))
            self.assertEqual(len(files),1)
            doc=server_document(read_journal(files[0]))
            replay=RunReplay(doc)
            try:
                while replay.advance():pass
                self.assertEqual(replay.session.run.status(),source.session.run.status())
            finally:replay.close()
            from tools.client_replay import ClientJournals
            from localserver.battle_records import RecordStore
            clients=ClientJournals(directory);attempt=doc['attempt']
            clients.begin(attempt,'a'*64,'b'*64,dict(key='LocalStorage.pb',revision=0,content=None),[1366,768])
            clients.append(attempt,dict(sequence=0,ops=[['loop',0],['loop',1]],end=True))
            # The client tape in this transport-independent fixture is web;
            # use matching history metadata for the portable browser bundle.
            recorder.document.update(platform='web',replay=dict(id=attempt))
            recorder.store.save(recorder.document)
            _,blob=recorder.store.export([recorder.identifier])
            with tempfile.TemporaryDirectory() as imported:
                destination=RecordStore(imported)
                self.assertEqual(destination.import_bytes(blob)['imported'],1)
                restored=server_document(read_journal(Path(imported)/(recorder.identifier+'.server.jsonl.gz')))
                self.assertEqual(restored['frames'],doc['frames'])
                self.assertNotEqual(restored['attempt'],attempt)
                copied=RunReplay(restored)
                try:
                    while copied.advance():pass
                    self.assertEqual(copied.session.run.status(),source.session.run.status())
                finally:copied.close()

    def test_real_inventory_door_and_return_preserve_room_state_for_all_six_runs(self):
        for map_id in FULL_RUN_MAPS:
            with self.subTest(map=map_id),contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                source=WholeRun(map_id)
                for _ in range(30):source.feedback()
                first=source.session.instance
                before=first.runtime.room_time
                if map_id=='est_run':
                    source.feedback(events=[dict(kind='damage',victim=104,value=100000,ownerid=1)])
                next_room=next(link['destination']for link in first.recipe['links'].values()
                    if link['destination']in source.session.run.pack.room_names and link['destination']!=first.role and source.session.run.allows(link))
                source.travel(next_room,changed_items=True)
                frozen=first.runtime.room_time
                for _ in range(20):source.feedback()
                self.assertGreaterEqual(frozen,before)
                self.assertEqual(first.runtime.room_time,frozen,'Unoccupied rooms stay paused')
                player=source.server.layouts.decode('Delta',source.server.player_delta_blob)['createplayer_']
                self.assertEqual(player['accountprops_']['ec_'],777)
                self.assertEqual(player['accountprops_']['progress_']['stash_'][0]['slotpos_'],4097)
                back=any(link['destination']==first.role and source.session.run.allows(link)for link in source.session.instance.recipe['links'].values())
                if back:
                    source.travel(first.role)
                    for _ in range(10):source.feedback()
                    self.assertIs(source.session.instance,first)
                    self.assertGreater(first.runtime.room_time,frozen)
                document=json.loads(json.dumps(source.capture.document()))
                self.assertNotIn('private-player',json.dumps(document));self.assertNotIn('private-password',json.dumps(document))
                self.assertNotIn('private-inventory',json.dumps(document));self.assertNotIn('not-exported',json.dumps(document))
                replay=RunReplay(document)
                try:
                    while replay.advance():pass
                    self.assertEqual(replay.session.run.status(),source.session.run.status())
                    self.assertEqual(replay.server.player_delta_blob,source.server.player_delta_blob)
                    for role,room in source.session.run.rooms.items():
                        restored=replay.session.run.rooms[role]
                        if room.snapshot is None:
                            # SC preloads recipes before entering a room; this
                            # must remain uninstantiated after reconstruction.
                            self.assertIsNone(restored.snapshot)
                            self.assertIsNone(restored.runtime)
                            self.assertEqual(restored.recipe,room.recipe)
                            continue
                        self.assertEqual(restored.snapshot.document(2),room.snapshot.document(2))
                        if room.runtime is not None:self.assertEqual(restored.runtime.room_time,room.runtime.room_time)
                finally:replay.close()

    def test_replay_freshness_is_a_typed_observation_not_an_imported_timestamp(self):
        original=restore_player(dict(objid=1,worldid=2,hp=50,alive=True,age=.1,entry_ready=False,
            account='never-exported'))
        self.assertFalse(original.received_since(-1e12))
        self.assertNotIn('account',player_document(original))
        original._replay_entry_ready=True
        self.assertTrue(original.received_since(1e12))
        with self.assertRaises(ValueError):restore_player(dict(objid=1,hp=50,entry_ready='execute'))

    def test_journal_write_failure_releases_clocks_without_stopping_the_level(self):
        with contextlib.redirect_stdout(io.StringIO()),game_settings.event_snapshot(game_settings.normalize(dict(map='egy_run'))):
            source=WholeRun('egy_run')
            source.capture.sink=mock.Mock(side_effect=OSError('disk full'))
            source.feedback()
            self.assertIsNotNone(source.capture.failed)
            self.assertNotIn('_recorded_wall_time',vars(source.session))
            source.capture.sink.reset_mock();before=source.session.current.room_time
            source.feedback()
            self.assertGreater(source.session.current.room_time,before)
            source.capture.sink.assert_not_called()
