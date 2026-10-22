import ast
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request,urlopen

from plugins._native_replay import NativeTape,validate_operations
from plugins._native_replay_capture import NativeCapture,ChunkDelivery,CLIENTS,ABI,create
from tools.native_recording import NativeRecording


def manifest():
    return dict(client=CLIENTS[1],pointer_bytes=4,hooks={
        row[0]:dict(rva=0x1000+i*32,signature='5589e583ec78908b4508909090c9c39090')for i,row in enumerate(ABI)})


class NativeCaptureTests(unittest.TestCase):
    def test_sized_delivery_does_not_walk_the_batch_on_the_game_thread(self):
        from plugins import _native_replay_capture as module
        class Untouchable(list):
            def __iter__(self):raise AssertionError('game thread rewalked the batch')
        with mock.patch.object(module.threading.Thread,'start'):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64,attempt='owned'))
            delivery.put_sized(dict(ops=Untouchable(),sequence=0,end=False),512)
            value,size=delivery.queue.get_nowait()
            self.assertEqual((size,delivery.queued_bytes,value['attempt']),(512,512,'owned'))
        chunks=[];tape=NativeTape(save_sized=lambda chunk,size:chunks.append((chunk,size)))
        tape.loop(0);tape.loop(1,[0,0]);tape.flush()
        from plugins._native_replay import wire_size
        self.assertEqual(chunks[0][1],sum(wire_size(row)+1 for row in chunks[0][0]['ops'])+256)

    def test_health_writes_are_deferred_and_coalesce_to_latest_status(self):
        from plugins import _native_replay_capture as module
        with tempfile.TemporaryDirectory() as folder,mock.patch.object(module.threading.Thread,'start'):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64),control=False)
            target=Path(folder)/'health.json'
            delivery.health(str(target),dict(sent=1));delivery.health(str(target),dict(sent=2))
            self.assertFalse(target.exists())
            delivery.poll_control()
            self.assertEqual(json.loads(target.read_text()),dict(sent=2))
            self.assertIsNone(delivery.health_value)

    def test_incomplete_older_battle_does_not_block_finalizing_newer_records(self):
        recorder=NativeRecording.__new__(NativeRecording)
        db=mock.MagicMock();db.__enter__.return_value.execute.return_value.fetchall.return_value=[('older','{}'),('newer','{}')]
        records=NS(connect=lambda:db);recorder.playback=NS(records=records,branches=set())
        recorder.started_wall=0;recorder.identifiers=set()
        recorder._finalize_battle=mock.Mock(side_effect=[ValueError('unfinished older tape'),None])
        recorder.finalize_battles()
        self.assertEqual(recorder._finalize_battle.call_count,2)
        self.assertEqual(recorder._finalize_battle.call_args.args[:2],('newer','{}'))

    def test_delivery_absorbs_small_bursts_with_a_bounded_byte_budget(self):
        import io
        from plugins import _native_replay_capture as module
        with mock.patch.object(module.threading.Thread,'start'),mock.patch.object(module,'urlopen',return_value=io.BytesIO()):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64),control=False)
            for index in range(32):delivery.put(dict(sequence=index,ops=[['loop',0],['loop',1]],end=False))
            self.assertEqual(delivery.queue.qsize(),32)
            before=delivery.queued_bytes
            with self.assertRaisesRegex(OSError,'buffer is full'):
                delivery.put(dict(ops=[['message',12,'A'*(8*1024*1024)]]))
            self.assertEqual(delivery.queued_bytes,before)
    def test_failed_transfer_reports_reason_and_acknowledges_selection_once(self):
        import io
        from plugins import _native_replay_capture as module
        feedback=[];downloads=[]
        def request(value,timeout=3):
            if '/feedback' in value.full_url:
                feedback.append(json.loads(value.data));return io.BytesIO(b'{}')
            if '/replay?' in value.full_url:downloads.append(value.full_url);raise MemoryError('bounded page allocation')
            if 'serial=0' in value.full_url:
                return io.BytesIO(json.dumps(dict(serial=1,controls=[],play=dict(serial=1,document={},operations=10,transport='pages-v1'))).encode())
            return io.BytesIO(b'{"serial":1,"controls":[]}')
        with mock.patch.object(module.threading.Thread,'start'),mock.patch.object(module,'urlopen',side_effect=request):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64))
            delivery.poll_control();delivery.last_poll=0;delivery.poll_control()
        self.assertEqual(len(downloads),1)
        self.assertEqual(feedback[0]['status']['state'],'failed')
        self.assertEqual(feedback[0]['status']['error'],'MemoryError')
        self.assertIn('allocation',feedback[0]['status']['message'])
        self.assertEqual(delivery.serial,1)

    def test_slow_playback_download_does_not_block_recording_delivery(self):
        import io
        import threading
        from plugins import _native_replay_capture as module
        entered=threading.Event();release=threading.Event();written=threading.Event()
        def request(value,timeout=3):
            if '/control?' in value.full_url:
                entered.set();release.wait(3)
                return io.BytesIO(b'{"serial":0,"controls":[]}')
            written.set();return io.BytesIO(b'{}')
        with mock.patch.object(module,'urlopen',side_effect=request):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64,attempt='first'))
            try:
                self.assertTrue(entered.wait(1))
                delivery.put(dict(sequence=0,ops=[['loop',0],['loop',1]],end=False))
                self.assertTrue(written.wait(1),'the recorder must not wait for the playback download')
                self.assertIsNone(delivery.error)
            finally:release.set();delivery.close()

    def test_recording_disabled_keeps_gameplay_and_playback_descriptions_without_a_tape(self):
        chunks=[];capture,_world,hooks=self.environment(chunks.append)
        capture.recording=False
        for _ in range(90):
            capture.before()
            clock=mock.Mock(return_value=123)
            self.assertEqual(hooks['TimeUtil::NowMS'].replace(clock),123)
            clock.assert_called_once()
            message=mock.Mock()
            hooks['MessageHandlers::run'].replace(message,1,16,b'description',11)
            message.assert_called_once()
            capture.after()
        self.assertEqual(capture.descriptions,{(16,b'description')})
        self.assertFalse(capture.tape.in_loop)
        self.assertEqual(chunks,[])
        self.assertEqual(capture.frames,0)

    def test_control_poll_streams_small_reads_and_decodes_json_for_python35(self):
        import io
        import queue
        from plugins import _native_replay_capture as capture
        class Response(io.BytesIO):
            def read(self, size=-1):
                if not 0 < size <= 65536:raise MemoryError('oversized 32-bit HTTP reservation')
                return super().read(size)
        class Work:
            calls=0
            def get(self, **_):
                self.calls+=1
                if self.calls==1:raise queue.Empty
                return None
            def task_done(self):pass
        with mock.patch.object(capture.threading.Thread,'start'),mock.patch.object(capture,'urlopen',return_value=Response(b'{"serial":1,"controls":[]}')):
            delivery=ChunkDelivery(dict(url='http://127.0.0.1:12345/chunks',token='a'*64))
            delivery.queue=Work()
            loads=capture.json.loads
            def legacy_loads(value):
                self.assertIsInstance(value,str,'Python 3.5 json.loads rejects raw bytes')
                return loads(value)
            with mock.patch.object(capture.json,'loads',side_effect=legacy_loads):delivery.poll_control();delivery.run()
            self.assertIsNone(delivery.control_error)
            self.assertEqual(delivery.incoming.get_nowait()['serial'],1)
        with self.assertRaisesRegex(ValueError,'too large'):
            capture.read_control(Response(b'x'*11),limit=10)

    def environment(self,save=None):
        ffi=mock.Mock(NULL=None);ffi.buffer.side_effect=lambda pointer,size:pointer[:size]
        props=NS(seed=123,xmp=100,ymp=200,wmp=10240,hmp=20480,hitpoints=150,maxhitpoints=150,facing=1)
        obj=NS(objId=1,props=props)
        player=NS(asClientObject=NS(asNativeObject=NS(asWorldObject=obj)))
        char=NS(ammo=12,total=4);ffi.cast.return_value=char
        sub=NS(asNativeSubWorld=NS(asSubWorldImpl=NS(asSubWorld=NS(worldId=2))))
        world=NS(total=0,asWorld=NS(props=props),player=player,mySubWorld=sub)
        refs=NS(WorldClient=NS(clientWorld=world))
        def cast(kind,pointer):
            if kind=='struct WorldClient *':return refs.WorldClient
            if kind=='struct ClientWorld *':return world
            if kind=='struct PlayerInput *':return pointer
            return char
        ffi.cast.side_effect=cast
        hooks={}
        def factory(_ffi,_lib,target,prefix,**kwargs):
            name=ABI[(target-0x401000)//32][0]
            value=mock.Mock(target=target,**kwargs);hooks[name]=value;return value
        capture=NativeCapture(ffi,mock.Mock(),refs,manifest(),save,0x400000,hook=factory)
        return capture,world,hooks

    def test_original_native_calls_are_observed_once_with_correct_scalars_and_player_state(self):
        chunks=[];capture,world,hooks=self.environment(chunks.append)
        def call(name,original,*args):return hooks[name].replace(original,*args)
        capture.before()
        def read(_):
            self.assertEqual(call('TimeUtil::NowMS',lambda:-1),-1)
            self.assertEqual(call('randUtil::randomSeed64',lambda:2**64-1),2**64-1)
            return call('WorldClient::handleServerDelta',lambda *args:True,1,12,b'\x08\x01',2)
        original=mock.Mock(side_effect=read)
        self.assertTrue(call('ServerConnection::doRead',original,1));original.assert_called_once_with(1)
        input_original=mock.Mock(return_value=77)
        self.assertEqual(call('WorldClient::updateInput',input_original,1,4,True),77)
        input_original.assert_called_once_with(1,4,True)
        def frame(_):
            world.total=512  # Holding a mobility key is not 512 ms of time.
            call('ClientWorld::internalUpdate',lambda *args:None,1,17)
        call('WorldClient::doTimePasses',frame,1);capture.after();capture.tape.finish()
        ops=[row for chunk in chunks for row in chunk['ops']]
        validate_operations(ops)
        self.assertIn(['read'],ops);self.assertIn(['random64',0xffffffff,0xffffffff],ops)
        self.assertIn(['clock',-1],ops);self.assertIn(['step',17],ops)
        self.assertEqual(ops[-2],['state',[123,512,1,2,100,200,10240,20480,150,150,1,12,4,1]])
        self.assertEqual(hooks['TimeUtil::NowMS'].result,'int')
        self.assertEqual(hooks['ServerConnection::doRead'].result,'_Bool')

    def test_ui_calls_and_failed_recorders_preserve_the_original_live_calls(self):
        capture,world,hooks=self.environment(mock.Mock(side_effect=OSError('offline')))
        call=hooks['WorldClient::updateInput'].replace;original=mock.Mock(return_value=17)
        self.assertEqual(call(original,1,4,False),17);original.assert_called_once()
        self.assertIn(['input',4,0],capture.tape.pending)
        capture.before();capture.tape.fail(OSError('private source path'))
        original.reset_mock();self.assertEqual(call(original,1,4,False),17);original.assert_called_once()
        self.assertNotIn('private',capture.tape.error)

    def test_io_thread_values_do_not_interleave_with_the_simulation_call_stack(self):
        import threading
        chunks=[];capture,_world,hooks=self.environment(chunks.append);capture.before()
        def worker():hooks['TimeUtil::NowMS'].replace(lambda:12345)
        thread=threading.Thread(target=worker);thread.start();thread.join()
        capture.after();capture.tape.finish()
        self.assertNotIn(['clock',12345],[row for chunk in chunks for row in chunk['ops']])
        self.assertIsNone(capture.tape.error)

    def test_core_packets_and_each_physics_checkpoint_are_kept_independently_of_ui_keys(self):
        chunks=[];capture,world,hooks=self.environment(chunks.append);capture.before()
        packet=NS(total=17,hit=4,unhit=2,_has_bits=[7])
        callback=mock.Mock(return_value=True)
        self.assertTrue(hooks['ClientWorld::addPlayerInput'].replace(callback,1,packet))
        callback.assert_called_once_with(1,packet)
        def update(_client,dt):world.total+=dt
        hooks['ClientWorld::internalUpdate'].replace(update,1,17)
        capture.after();capture.tape.finish()
        rows=[row for chunk in chunks for row in chunk['ops']];validate_operations(rows)
        self.assertIn(['player-input',17,4,2,7],rows)
        index=rows.index(['engine-frame',17])
        self.assertEqual(rows[index+1],['return','engine-frame'])
        self.assertEqual(rows[index+2][0],'engine-state')
        self.assertEqual(rows[index+2][1][1],17)
        changed=list(rows);changed.insert(1,changed.pop(index+2))
        with self.assertRaisesRegex(ValueError,'checkpoint'):validate_operations(changed)

    def test_capture_is_not_attached_to_online_sessions_or_unrecognized_clients(self):
        self.assertIsNone(create(None,None,None,{}))
        value=manifest();value['client']='a'*64
        with self.assertRaisesRegex(ValueError,'does not support'):
            NativeCapture(None,None,None,value,None,0x400000)
        root=Path(__file__).resolve().parents[1]
        ast.parse((root/'plugins/_native_replay_capture.py').read_text(),feature_version=(3,5))


class NativeRecordingEndpointTests(unittest.TestCase):
    def test_connection_health_reports_missing_disabled_degraded_and_connected(self):
        import time
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bridge-status.json'
            with mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
                recording=NativeRecording(directory,'owned-client.exe',recording=False,health_path=path)
            try:
                self.assertEqual(recording.health()['state'],'starting')
                recording.started-=30;self.assertEqual(recording.health()['state'],'missing')
                for state,changes in [('disabled',dict(active=False)),('degraded',dict(hook_error=True)),('waiting',dict(connected=False)),('connected',{})]:
                    path.write_text(json.dumps(dict(dict(at=time.time(),active=True,connected=True,replay_ready=True),**changes)),encoding='utf8')
                    request=Request(recording.config['url'].replace('/chunks','/health'),headers={'Authorization':'Bearer '+recording.token})
                    with urlopen(request) as response:self.assertEqual(json.load(response)['state'],state)
                path.write_text('[]',encoding='utf8');self.assertEqual(recording.health()['state'],'missing')
            finally:recording.close()

    def test_disabled_recording_retains_the_owned_restart_endpoint_without_an_empty_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
                recording=NativeRecording(directory,'owned-client.exe',recording=False)
            try:
                with recording.journals.connect() as db:self.assertEqual(db.execute('SELECT id FROM attempts').fetchall(),[])
                self.assertFalse(recording.config['recording'])
                request=Request(recording.config['url'].replace('/chunks','/command'),data=json.dumps(dict(action='practice',map='hulk_mid',shell=2)).encode(),headers={'Authorization':'Bearer '+recording.token})
                with urlopen(request) as response:self.assertTrue(json.load(response)['starting'])
                self.assertEqual(recording.restart_request,dict(map='hulk_mid',shell=2))
            finally:recording.close()

    def test_practice_restarts_only_the_owned_launcher_with_a_valid_map_and_class(self):
        with tempfile.TemporaryDirectory() as directory:
            recording=self.recording(directory)
            try:
                def command(value):
                    request=Request(recording.config['url'].replace('/chunks','/command'),data=json.dumps(value).encode(),headers={'Authorization':'Bearer '+recording.token})
                    with urlopen(request) as response:return json.load(response)
                command(dict(action='practice',map='hulk_mid',shell=2))
                self.assertEqual(recording.restart_request,dict(map='hulk_mid',shell=2))
                with self.assertRaises(HTTPError):command(dict(action='practice',map='invalid',shell=2))
                self.assertEqual(recording.restart_request,dict(map='hulk_mid',shell=2))
            finally:recording.close()

    def recording(self,directory):
        with mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
            return NativeRecording(directory,'owned-client.exe')

    def test_authenticated_delivery_retry_and_abrupt_close_keep_only_verified_frames(self):
        with tempfile.TemporaryDirectory() as directory:
            recording=self.recording(directory)
            try:
                bad=Request(recording.config['url'],data=b'{}',headers={'Authorization':'Bearer wrong'})
                with self.assertRaises(HTTPError) as error:urlopen(bad)
                self.assertEqual(error.exception.code,403)
                chunks=[];tape=NativeTape(save=chunks.append)
                tape.loop(0);tape.call(['frame'],lambda:tape.value('step',lambda:17));tape.loop(1,[123,17])
                tape.flush();good=chunks[0]
                delivery=ChunkDelivery(recording.config)
                delivery.put(good);delivery.put(good)
                # Simulate process shutdown while a subsequent callback is in
                # progress. Its half call must never enter a playable trace.
                delivery.put(dict(sequence=1,ops=[['loop',0],['input',1,0]],end=False))
                delivery.close();self.assertIsNone(delivery.error)
                recording.close();trace=recording.journals.read(recording.identifier)
                self.assertEqual(trace['ops'],good['ops']);validate_operations(trace['ops'])
                self.assertNotIn(recording.token,json.dumps(trace))
                self.assertNotIn('owned-client',json.dumps(trace))
            finally:recording.close()

    def test_empty_owned_attempts_are_removed_without_touching_other_records(self):
        with tempfile.TemporaryDirectory() as directory:
            first=self.recording(directory);second=self.recording(directory)
            try:
                first.close()
                with first.journals.connect() as db:
                    self.assertEqual(db.execute('SELECT id FROM attempts').fetchall(),[(second.identifier,)])
            finally:second.close()

    def test_delivery_refuses_external_servers_and_credentials(self):
        for url in ('http://example.com/chunks','http://user:secret@127.0.0.1/chunks','https://127.0.0.1/chunks','http://127.0.0.1/other'):
            with self.subTest(url=url),self.assertRaises(ValueError):ChunkDelivery(dict(url=url,token='a'*64))


if __name__=='__main__':unittest.main()
