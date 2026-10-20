import ast
import base64
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
import uuid
import time

from plugins._native_replay import NativeTape
from plugins._native_replay_capture import NativeCapture,ChunkDelivery,ABI
from plugins._native_replay_player import NativePlayer
from tools.native_replay import NativeJournals,adapter_digest
from tools.native_playback import NativePlayback
from tools.replay_host import Takeover
from test_native_recording import manifest
import test_replay_host


class Engine:
    def __init__(self):
        self.now=0;self.mono=0.;self.diverge=False;self.chunks=[];self.hooks={}
        self.props=NS(seed=123,xmp=100,ymp=200,wmp=10240,hmp=20480,hitpoints=150,maxhitpoints=150,facing=1)
        obj=NS(objId=1,props=self.props)
        self.player=NS(asClientObject=NS(asNativeObject=NS(asWorldObject=obj)),ammo=12,total=0)
        sub=NS(asNativeSubWorld=NS(asSubWorldImpl=NS(asSubWorld=NS(worldId=2))))
        self.world=NS(total=0,asWorld=NS(props=self.props),player=self.player,mySubWorld=sub)
        self.client=NS(clientWorld=None)
        self.game=NS(worldClient=None,serverConnection=NS(messageHandlers=NS()))
        self.refs=NS(GameClient=self.game,WorldClient=None)
        ffi=mock.Mock(NULL=None)
        ffi.cast.side_effect=lambda kind,value:value
        ffi.buffer.side_effect=lambda value,size:value[:size]
        ffi.addressof.side_effect=lambda value,name:getattr(value,name)
        ffi.new.side_effect=lambda kind,value=None:bytearray(value) if kind=='char[]' else NS(total=0,hit=0,unhit=0,_has_bits=[0])
        def factory(_ffi,_lib,target,prefix,**kwargs):
            kind=ABI[(target-0x401000)//32][-1]
            hook=NS(target=target,replace=kwargs['replace'],original=self.original(kind),check=lambda:None,install=lambda:None,remove=lambda:None)
            self.hooks[kind]=hook;return hook
        self.capture=NativeCapture(ffi,None,self.refs,manifest(),self.chunks.append,0x400000,hook=factory)

    def original(self,kind):
        def invoke(*args):
            if kind=='clock':return self.now
            if kind=='random32':return 123
            if kind=='random64':return 2**64-1
            if kind=='message' and args[1]==10:
                self.props.xmp=100;self.props.hitpoints=150;self.world.total=0
                self.client.clientWorld=self.world;self.game.worldClient=self.client;self.refs.WorldClient=self.client
            if kind=='message' and args[1]==12:self.call('delta',self.client,12,args[2],args[3])
            if kind=='message' and args[1]==21:self.call('ack',self.game.serverConnection,22)
            if kind=='delta':self.props.hitpoints-=int(bytes(args[2]).decode())
            if kind=='input':
                if args[2]:self.world.total|=args[1]
                else:self.world.total&=~args[1]
            if kind=='inventory':
                self.props.maxhitpoints=100 if args[1].slotpos==3840 else 150
            if kind=='frame':
                self.call('clock');self.call('engine-frame',self.world,17)
            if kind=='engine-frame':
                self.call('random32');self.call('random64')
                self.props.xmp+=args[1] if self.world.total&4 else 0
                if self.diverge:self.props.xmp+=1
            return True if kind in('read','player-input') else None
        return invoke

    def call(self,kind,*args):return self.hooks[kind].replace(self.hooks[kind].original,*args)

    def record(self,count=30):
        self.capture.before();self.call('message',self.game.serverConnection.messageHandlers,10,b'world',5);self.capture.after()
        for index in range(count):
            self.now+=17;self.capture.before()
            if index==0:self.call('input',self.client,4,1)
            if index==5:self.call('message',self.game.serverConnection.messageHandlers,12,b'17',2)
            self.call('frame',self.client);self.capture.after()
        self.capture.tape.finish()
        return dict(id=str(uuid.uuid4()),ops=[row for chunk in self.chunks for row in chunk['ops']])

    def play(self,document):
        self.capture.player=NativePlayer(self.capture,document,clock=lambda:self.mono)
        return self.capture.player


class NativePlayerTests(unittest.TestCase):
    def test_rewind_rebases_only_future_sound_channel_times(self):
        capture=NativeCapture.__new__(NativeCapture)
        channels=[NS(lastPlayMS=500,soundName='shot'),NS(lastPlayMS=20,soundName='hit')]
        memory={1000:[NS(start=2000,finish=2064)],2000:channels}
        capture.ffi=NS(cast=lambda kind,value:value if kind=='uintptr_t' else memory[value])
        capture.sound_mixer=NS(channels=NS(start=1000,finish=1012))
        capture.sync_audio_clock(100)
        self.assertEqual([row.lastPlayMS for row in channels],[99,20])
        self.assertEqual([row.soundName for row in channels],['shot','hit'])
        capture.sound_mixer.channels.finish=1001
        with self.assertRaisesRegex(ValueError,'sound channel vector'):capture.sync_audio_clock(100)

    def test_inventory_and_macro_inputs_outside_the_render_callback_replay_in_order(self):
        engine=Engine();capture=engine.capture
        capture.before();engine.call('message',engine.game.serverConnection.messageHandlers,10,b'world',5);capture.after()
        engine.call('inventory',engine.client,NS(command=2,slotpos=3840,targetslotpos=1536,_has_bits=[7]))
        engine.call('input',engine.client,4,1)
        capture.before();engine.call('frame',engine.client);capture.after();capture.tape.finish()
        ops=[row for chunk in engine.chunks for row in chunk['ops']]
        self.assertIn(['inventory',2,3840,1536,7],ops)
        fresh=Engine();player=fresh.play(dict(id='fresh',ops=ops))
        for _ in range(20):fresh.mono+=.1;player.update()
        self.assertEqual(player.phase,'ended')
        self.assertIsNone(fresh.capture.tape.error)
        self.assertEqual(fresh.props.maxhitpoints,100)
        self.assertEqual(fresh.world.total,4)

    def test_resume_starts_a_fresh_tape_at_the_next_zone_without_old_world_frames(self):
        import queue
        engine=Engine();capture=engine.capture
        source=engine.record(5)
        delivery=NS(incoming=queue.Queue(),feedback=mock.Mock(),put=engine.chunks.append,attempt='old',error='OSError')
        capture.delivery=delivery
        delivery.incoming.put(dict(serial=1,resume=dict(serial=1,id='fresh',recording=True)))
        capture.poll()
        self.assertTrue(capture.awaiting_world)
        self.assertIsNone(capture.player)
        self.assertIsNone(delivery.error)
        self.assertEqual(delivery.attempt,'fresh')
        capture.before();engine.call('frame',engine.client);capture.after()
        self.assertEqual(capture.tape.pending,[])
        engine.call('message',engine.game.serverConnection.messageHandlers,21,b'zone',4);capture.after()
        self.assertEqual(capture.tape.last_state,[0,0])
        capture.before();engine.call('message',engine.game.serverConnection.messageHandlers,10,b'world',5);capture.after()
        self.assertEqual(len(capture.tape.last_state),14)
        self.assertFalse(capture.awaiting_created)
        delivery.feedback.assert_called_with(dict(serial=1,status=dict(state='idle'),resume='fresh'))
        capture.before();engine.call('frame',engine.client);capture.after()
        capture.tape.finish()
        ops=[row for chunk in engine.chunks[1:] for row in chunk['ops']]
        fresh=Engine();player=fresh.play(dict(id='fresh',ops=ops))
        for _ in range(100):fresh.mono+=.1;player.update()
        self.assertIsNone(fresh.capture.tape.error)
        self.assertEqual(player.phase,'ended')

    def test_repeated_takeover_requests_keep_the_offered_host_and_live_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.serial=3;service.source=dict(id='source')
            host=NS(committed=False,bind=mock.Mock(),description=lambda:dict(id='branch'),
                    telemetry=mock.Mock(),binary=mock.Mock(),stop=mock.Mock())
            host.commit=lambda:setattr(host,'committed',True)
            service.warm=NS(take=mock.Mock(return_value=host),close=mock.Mock())
            request=dict(serial=3,status=dict(state='taking-over'),checkpoint=dict(cursor=5),takeover=True)
            service.feedback(request)
            deadline=time.monotonic()+2
            while service.preparing and time.monotonic()<deadline:time.sleep(.01)
            self.assertIs(service.host,host)
            for _ in range(12):service.feedback(request)
            self.assertEqual(service.warm.take.call_count,1)
            player=dict(player=dict(objid=1,hp=100))
            service.feedback(dict(serial=3,status=dict(state='live'),committed='branch',telemetry=player))
            self.assertTrue(host.committed)
            host.telemetry.assert_called_once_with(player)
            self.assertIs(service.host,host)
            service.close()

    def test_specialized_room_documents_are_not_coalesced_into_player_telemetry(self):
        import threading
        delivery=object.__new__(ChunkDelivery)
        delivery.feedback_lock=threading.Lock();delivery.feedback_value=None
        restore=dict(seq=2,t=1,ground_restore=dict(serial=7))
        egress=dict(seq=3,t=1,egress_ready=dict(request_id='owned'))
        delivery.feedback(dict(serial=1,telemetry=dict(player=dict(hp=100),events=[dict(damage=1)]),documents=[restore]))
        delivery.feedback(dict(serial=1,telemetry=None,documents=[egress]))
        delivery.feedback(dict(serial=1,telemetry=dict(player=dict(hp=90),events=[dict(damage=2)])))
        value=delivery.feedback_value
        self.assertEqual(value['documents'],[restore,egress])
        self.assertEqual(value['telemetry']['player']['hp'],90)
        self.assertEqual(value['telemetry']['events'],[dict(damage=1),dict(damage=2)])

    def test_native_room_mailboxes_use_existing_scope_validation_and_return_restore(self):
        from localserver.live.bridge import PlayerBridge
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.source=dict(id='source');service.serial=1;service.branch=dict(id='branch')
            bridge=PlayerBridge(0)
            bridge._absorb(dict(player=dict(objid=1,hp=100)))
            service.host=NS(committed=True,bridge=bridge,telemetry=mock.Mock(),binary=mock.Mock())
            document=dict(seq=3,t=1,ground_restore=dict(serial=7))
            service.feedback(dict(serial=1,status=dict(state='live'),documents=[document]))
            self.assertEqual(bridge._restore.received,1)
            self.assertEqual(bridge.state.hp,100,'Specialized documents cannot clear player state')
            bridge._restore.cached_packet=json.dumps(document).encode('ascii')
            self.assertEqual(service.poll(1)['documents'],[document])
            with self.assertRaises(ValueError):
                service.feedback(dict(serial=1,status=dict(state='live'),documents=[dict(player=dict(hp=999))]))

    def test_takeover_isolates_old_udp_and_routes_ground_restore_to_owned_host(self):
        from plugins._hardlab_livebridge import LiveBridge
        bridge=LiveBridge.__new__(LiveBridge)
        document=dict(seq=1,t=1,ground_restore=dict(serial=7))
        bridge.socket=mock.Mock();bridge.feedback_isolated=True
        bridge._ground_restore=NS(frame=lambda context,now:json.dumps(document).encode('ascii'))
        bridge._ground_restore_sent_frame=None;bridge._network_frame_id=1
        bridge._network_frame=dict(elapsed_s=0,restore_calls=0,overrun_s=0)
        bridge._network_totals=dict(restore_sent=0)
        bridge._network_clock=lambda:1.;bridge.seq=0;bridge.sent=0
        bridge.feedback_sink=mock.Mock(return_value=True)
        bridge.send_ground_restore(1.)
        bridge.feedback_sink.assert_called_once_with(document)
        bridge.socket.sendto.assert_not_called();bridge.socket.recvfrom.assert_not_called()

    def test_resource_preload_finishes_before_any_recorded_physics_and_is_not_taped(self):
        source=Engine();source.capture.before()
        source.call('message',source.game.serverConnection.messageHandlers,21,b'pack',4)
        source.capture.after();document=source.record()
        self.assertFalse(any(row[0]=='ack' for row in document['ops']),'Asynchronous asset acknowledgements are not simulation events')
        target=Engine();player=target.play(document)
        self.assertEqual(player.phase,'preparing');self.assertFalse(player.can_takeover())
        player.update();self.assertEqual(player.phase,'playing')
        self.assertEqual(target.capture.tape.cursor,0,'Preloading cannot consume or mutate the original journal')
        target.mono=1;player.update()
        self.assertEqual(player.phase,'ended');self.assertIsNone(target.capture.tape.error)
    def test_actual_adapter_reconstructs_messages_original_physics_clocks_and_damage(self):
        source=Engine();document=source.record();before=copy.deepcopy(document)
        target=Engine();player=target.play(document);target.now=99999;target.mono=1
        player.update()
        self.assertEqual(player.phase,'ended');self.assertIsNone(target.capture.tape.error)
        self.assertEqual(target.capture.state(),source.capture.state())
        self.assertEqual(target.props.hitpoints,133);self.assertEqual(target.props.xmp,610)
        self.assertEqual(player.seconds,.51);self.assertEqual(document,before)

    def test_selected_world_primes_verified_prefix_then_uses_relative_replay_time(self):
        from tools.native_playback import native_battle_view
        document=Engine().record(10);document['ops']+=Engine().record(10)['ops']
        document['view']=native_battle_view(document['ops'],2)
        target=Engine();player=target.play(document)
        self.assertFalse(player.can_takeover())
        player.control(dict(action='pause',paused=True));player.update()
        self.assertFalse(player.priming);self.assertTrue(player.can_takeover())
        self.assertAlmostEqual(player.status()['seconds'],0)
        player.control(dict(action='seek',seconds=.085));player.update()
        self.assertFalse(player.seeking);self.assertAlmostEqual(player.status()['seconds'],.085)
        player.control(dict(action='seek',seconds=.017));player.update();player.update()
        self.assertFalse(player.priming);self.assertFalse(player.seeking)
        self.assertAlmostEqual(player.status()['seconds'],.017)
        self.assertIsNone(target.capture.tape.error)

    def test_pause_quarter_speed_seek_and_original_keyboard_only_takeover(self):
        document=Engine().record();target=Engine();player=target.play(document)
        player.control(dict(action='speed',speed=.25));target.mono=.4;player.update()
        self.assertLess(player.seconds,.11);self.assertGreater(player.seconds,.08)
        old=player.seconds;player.control(dict(action='pause',paused=True));target.mono=2;player.update()
        self.assertEqual(player.seconds,old)
        player.control(dict(action='seek',seconds=.35));player.update()
        self.assertFalse(player.seeking);self.assertLessEqual(player.seconds,.35)
        self.assertTrue(player.can_takeover())
        target.call('input',target.client,1,1)
        self.assertFalse(player.requested,'Mouse/world-input calls cannot take over')
        target.hooks['keyboard'].replace(lambda *_:target.call('input',target.client,4,1),target.game,1,65)
        self.assertTrue(player.requested);checkpoint=player.checkpoint();self.assertEqual(checkpoint['player'],target.capture.state())
        target.now=70000;chunks=[];player.commit(chunks.append,0)
        self.assertEqual(player.phase,'live');self.assertEqual(target.capture.clock_offset,340-70000)
        self.assertFalse(player.requested)
        self.assertFalse(player.paused)
        self.assertEqual(player.speed,1.)
        with self.assertRaises(ValueError):player.control(dict(action='seek',seconds=.1))
        player=target.play(document)
        # Backward seek rebuilds through the original CreateWorld dispatcher.
        player.control(dict(action='seek',seconds=.1));player.update()
        self.assertLessEqual(player.seconds,.1);self.assertIsNone(target.capture.tape.error)

    def test_divergence_and_missing_bootstrap_fail_without_a_takeover(self):
        document=Engine().record();target=Engine();player=target.play(document);target.diverge=True;target.mono=1;player.update()
        self.assertEqual(player.phase,'failed');self.assertFalse(player.can_takeover())
        with self.assertRaises(ValueError):player.commit(lambda _:None,0)
        with self.assertRaisesRegex(ValueError,'bootstrap'):NativePlayer(target.capture,dict(id=document['id'],ops=[]))
        ast.parse(Path('plugins/_native_replay_player.py').read_text(),feature_version=(3,5))


def native_fixture(directory):
    from localserver.protocol import RC4,random_string
    from tools.replay_stream import framed
    records,web,request,ops=test_replay_host.ReplayHostTests().documents(directory)
    native=NativeJournals(directory);attempt=str(uuid.uuid4())
    native.begin(attempt,'a'*64,adapter_digest(),dict(content=None),[1366,768])
    tape=NativeTape(save=lambda chunk:native.append(attempt,chunk));cipher=RC4(random_string(0,64));pending=[];first=True;checkpoint=None
    tape.loop(0);tape.call(['message',10,base64.b64encode(b'world').decode()],lambda:None);tape.loop(1,[0,0])
    for index,row in enumerate(ops):
        if row[0]=='net':
            if first:first=False;continue
            raw=cipher.crypt(base64.b64decode(row[1]));offset=0
            while offset<len(raw):
                import struct
                kind=raw[offset];size=struct.unpack('>i',raw[offset+1:offset+5])[0]
                if kind in(12,37,38):pending.append((kind,raw[offset+5:offset+5+size]))
                offset+=size+5
        if row==['loop',0]:
            tape.loop(0)
            for kind,data in pending:tape.call(['message',kind,base64.b64encode(data).decode()],lambda:None)
            pending=[]
        elif row[0]=='frame':tape.call(['frame'],lambda:tape.value('step',lambda:row[1]))
        elif row==['loop',1]:
            state=ops[index-1][1];tape.loop(1,state)
            if index+1==request['cursor']:checkpoint=dict(cursor=tape.size,player=state)
    tape.finish();document=native.read(attempt)
    ends=[i+1 for i,row in enumerate(document['ops']) if row==['loop',1]]
    request=dict(request,id=attempt,cursor=ends[20])
    record=records.get(request['record']);record.update(platform='native',replay=dict(id=attempt));records.save(record)
    from localserver.replay import read_journal,JournalWriter
    path=Path(directory)/(record['id']+'.server.jsonl.gz');server=read_journal(path);header=dict(server,attempt=attempt);header.pop('frames')
    path.unlink();writer=JournalWriter(path,header)
    for frame in server['frames']:writer.frame(frame)
    writer.close()
    return records,native,request,document


class NativeHostTests(unittest.TestCase):
    def test_live_takeover_shortcuts_route_once_to_normal_practice(self):
        for kind,shell,expected in [('restart',None,2),('switch_character',1,1),('switch_character',2,2),('switch_character',5,5),('switch_character',3,3)]:
            with self.subTest(kind=kind,shell=shell),tempfile.TemporaryDirectory() as directory:
                service=NativePlayback(directory,'a'*64)
                service.source=dict(id='source',map='egy_hulk');service.serial=1;service.branch=dict(id='branch')
                service.host=NS(committed=True,session=NS(player_type=2),telemetry=mock.Mock(),binary=mock.Mock())
                event=dict(kind=kind)
                if shell is not None:event['type']=shell
                value=dict(serial=1,status=dict(state='live'),telemetry=dict(player=dict(hp=100),events=[event]))
                self.assertEqual(service.feedback(value),dict(map_id='egy_hulk',shell=expected))
                self.assertIsNone(service.feedback(value))

    def test_owned_http_control_prepares_commits_and_seals_a_native_branch(self):
        from tools.native_recording import NativeRecording
        from tools.native_playback import NativeControlClient
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        with tempfile.TemporaryDirectory() as directory:
            records,native,request,document=native_fixture(directory)
            registry=Path(directory)/'control.json'
            with mock.patch('tools.native_recording.native_symbols.resolve',return_value=manifest()):
                endpoint=NativeRecording(directory,'owned.exe',registry=registry)
            endpoint.playback.client='a'*64
            control=NativeControlClient(registry)
            try:
                with self.assertRaises(HTTPError):urlopen(endpoint.config['url'].replace('/chunks','/control'))
                control.call('/command',dict(action='watch',record=request['record']))
                initial=control.call('/control?serial=0')
                self.assertTrue(endpoint.ready.is_set());self.assertEqual(initial['play']['record'],request['record'])
                # This models the plugin's verified frame-boundary request,
                # rather than accepting coordinates supplied by the record UI.
                control.call('/feedback',dict(serial=initial['serial'],status=dict(state='taking-over'),checkpoint=request,takeover=True))
                deadline=time.monotonic()+3;branch=None
                while time.monotonic()<deadline:
                    branch=control.call('/control?serial='+str(initial['serial'])).get('branch')
                    if branch:break
                    time.sleep(.01)
                self.assertIsNotNone(branch)
                from test_replay import input_at
                telemetry=input_at(20);telemetry['keys']['gameplay']=True
                control.call('/feedback',dict(serial=initial['serial'],status=dict(state='live'),committed=branch['id'],telemetry=telemetry))
                deadline=time.monotonic()+2
                while time.monotonic()<deadline and len(records.list())<2:time.sleep(.01)
                self.assertEqual(len(records.list()),2)
                # Abrupt native process closure seals only complete branch
                # frames and preserves the original source recording.
                endpoint.close()
                self.assertEqual(native.read(request['id']),document)
                self.assertEqual(native.read(branch['id'])['ops'],document['ops'][:request['cursor']])
                self.assertFalse(registry.exists())
            finally:endpoint.close()

    def test_real_controller_rebuild_native_branch_and_portable_record_stay_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            records,native,request,document=native_fixture(directory)
            source=copy.deepcopy(native.read(request['id']))
            host=Takeover(records,native,request,manual=True);transport=mock.Mock();host.bind(transport);host.commit()
            self.assertFalse(host.session.options.web_client)
            from test_replay import input_at
            first=input_at(20);first['keys']['gameplay']=True;host.telemetry(first);host.step(1/60)
            branch=records.get(host.recorder.identifier)
            self.assertEqual(branch['platform'],'native');self.assertEqual(branch['parent']['id'],request['record'])
            self.assertEqual(native.read(request['id']),source)
            self.assertEqual(native.read(host.branch['id'],require_complete=False)['ops'],source['ops'][:request['cursor']])
            host.stop()

    def test_service_routes_only_its_matching_native_recordings(self):
        with tempfile.TemporaryDirectory() as directory:
            records,native,request,document=native_fixture(directory);service=NativePlayback(directory,'a'*64)
            try:
                service.action(dict(action='watch',record=request['record']))
                initial=service.poll(0)
                selected=initial['play']['document']
                self.assertEqual({k:v for k,v in selected.items() if k!='view'},document)
                self.assertEqual(selected['view']['end'],len(document['ops']))
                self.assertNotIn('play',service.poll(initial['serial']))
                service.action(dict(action='speed',speed=.25));self.assertEqual(service.poll(service.serial)['controls'][0]['speed'],.25)
                service.feedback(dict(serial=service.serial-1,status=dict(state='live')))
                self.assertEqual(service.status['state'],'starting','Stale player feedback cannot control a new replay')
                self.assertTrue(service.availability([request['id']])[request['id']]['available'])
            finally:service.close()


if __name__=='__main__':unittest.main()
