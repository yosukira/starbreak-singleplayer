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
        self.assertEqual(capture.tape.pending,[])
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
