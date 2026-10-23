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
from plugins._native_replay_capture import NativeCapture,ChunkDelivery,ABI,PlatformDropRetry
from plugins._native_replay_player import NativePlayer
from tools.native_replay import NativeJournals,adapter_digest
from tools.native_playback import NativePlayback
from tools.replay_host import Takeover
from test_native_recording import manifest
import test_replay_host


class Engine:
    def __init__(self):
        self.now=0;self.mono=0.;self.diverge=False;self.chunks=[];self.hooks={}
        self.props=NS(seed=123,xmp=100,ymp=200,wmp=10240,hmp=20480,hitpoints=150,maxhitpoints=150,facing=1,invulnerable=False)
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
                self.props.xmp=100;self.props.hitpoints=150;self.props.invulnerable=False;self.world.total=0
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
    def test_protection_records_a_scoped_flag_and_replays_at_the_same_boundary(self):
        source=Engine();source.props.seed=0xfedcba98
        source.capture.protection_command=lambda:dict(room_seed=0xfedcba98,worldid=2,objid=1,enabled=True) if source.now>=34 else None
        document=source.record(8)
        self.assertEqual([row for row in document['ops'] if row[0]=='protection'],[['protection',0xfedcba98,2,1,1]])
        self.assertTrue(source.props.invulnerable)
        target=Engine();target.props.seed=0xfedcba98;player=target.play(document)
        player.control(dict(action='seek',seconds=player.view_duration,paused=True));player.update()
        self.assertIsNone(target.capture.tape.error)
        self.assertTrue(target.props.invulnerable)
        self.assertEqual(target.capture.tape.cursor,len(document['ops']))
        with self.assertRaisesRegex(ValueError,'target changed'):target.capture.apply_protection(['protection',0xfedcba99,2,1,0])

    def test_platform_drop_retries_only_the_same_catching_ledge_and_completed_steps(self):
        retry=PlatformDropRetry();world=(100,42);ledge=(1,6)
        retry.arm(world,ledge,10,0)
        self.assertFalse(retry.due(world,ledge,24,10,.001),'No retry before the original physics step')
        self.assertFalse(retry.due(world,None,24,11,.02),'An airborne drop is left alone')
        self.assertTrue(retry.due(world,ledge,24,12,.03),'Retry if the original ledge catches the player again')
        for _ in range(100):self.assertFalse(retry.due(world,ledge,24,12,.031),'Rendering more frames must not add inputs')
        retry.arm(world,ledge,13,.2)
        self.assertFalse(retry.due(world,ledge,24,14,.26),'Retries cannot extend their own deadline')
        for support,total,new_world in [((1,7),24,world),(ledge,8,world),(ledge,24,(200,42)),(('solid',1,1),24,world)]:
            retry.arm(world,ledge,1,1)
            self.assertFalse(retry.due(new_world,support,total,2,1.01),'Other ledges, released keys and changed worlds are not retried')
        retry.arm(world,('solid',1,1),1,2)
        self.assertIsNone(retry.intent,'Solid ground cannot arm a drop retry')

    def test_world_lifetime_callback_runs_even_when_recording_is_disabled(self):
        for recording in (False,True):
            engine=Engine();engine.capture.recording=recording
            reset=engine.capture.world_started=mock.Mock()
            engine.capture.before()
            engine.call('message',engine.game.serverConnection.messageHandlers,10,b'world',5)
            engine.capture.after()
            reset.assert_called_once_with()
            self.assertIs(engine.refs.WorldClient,engine.game.worldClient)

    def test_restarted_tape_contains_exact_login_descriptions_for_a_fresh_engine(self):
        engine=Engine();capture=engine.capture
        descriptions={(16,b'exact item'),(17,b'exact character')}
        capture.descriptions=descriptions;capture.awaiting_world=True;capture.bootstrap_descriptions=True
        capture.before()
        engine.call('message',engine.game.serverConnection.messageHandlers,10,b'world',5)
        capture.after();capture.tape.finish()
        ops=[row for chunk in engine.chunks for row in chunk['ops']]
        messages=[row for row in ops if row[0]=='message']
        self.assertEqual([(row[1],base64.b64decode(row[2])) for row in messages],sorted(descriptions)+[(10,b'world')])
        document=dict(id=str(uuid.uuid4()),ops=ops)
        player=engine.play(document);engine.mono=1;player.update()
        self.assertEqual(player.phase,'ended');self.assertIsNone(capture.tape.error)

    def test_original_socket_is_drained_without_applying_stale_world_packets(self):
        engine=Engine();player=engine.play(Engine().record(30))
        player.control(dict(action='seek',seconds=.17,paused=True));player.update()
        original=mock.Mock(side_effect=lambda *_:engine.call('message',engine.game.serverConnection.messageHandlers,12,b'99',2))
        for phase in ('playing','live'):
            player.phase=phase;hp=engine.props.hitpoints;cursor=engine.capture.tape.cursor
            self.assertTrue(engine.capture.replacement('read')(original,engine.game.serverConnection))
            self.assertEqual(engine.props.hitpoints,hp)
            self.assertEqual(engine.capture.tape.cursor,cursor)
            self.assertFalse(engine.capture.draining)
        self.assertEqual(original.call_count,2)

    def test_live_recording_failure_keeps_simulation_clock_and_physics_running(self):
        target=Engine();player=target.play(Engine().record(30))
        player.control(dict(action='seek',seconds=.17,paused=True));player.update()
        target.now=10
        with mock.patch('plugins._native_replay.wire_size',side_effect=AssertionError('input-edge prefix scan')):
            player.commit(lambda _:None,0,size=1000)
        self.assertEqual(target.capture.tape.ops,[])
        target.capture.tape.fail(OSError('writer unavailable'))
        actual=[];original=target.hooks['frame'].original
        def frame(*args):
            actual.append(target.call('clock'));return original(*args)
        target.hooks['frame'].original=frame
        before=target.props.xmp;elapsed=target.capture.engine_elapsed
        target.capture.before();target.call('frame',target.client);target.capture.after()
        self.assertEqual(actual,[target.now+target.capture.clock_offset])
        self.assertGreater(target.props.xmp,before)
        self.assertGreater(target.capture.engine_elapsed,elapsed)
        self.assertEqual(target.call('clock'),target.now,'Window clocks stay real even when recording fails')

    def test_culled_legacy_input_sound_retires_only_its_verified_clock_tail(self):
        def sound(engine,audible):
            engine.hooks['clock'].caller=0
            hook=engine.hooks['input'];original=hook.original
            def press(*args):
                result=original(*args)
                engine.call('sound')
                return result
            hook.original=press
            def play():
                if audible:
                    engine.hooks['clock'].caller=123
                    try:engine.call('clock')
                    finally:engine.hooks['clock'].caller=0
            engine.hooks['sound'].original=play
        source=Engine();sound(source,True);document=source.record(8)
        for audible in (False,True):
            target=Engine();sound(target,audible);target.capture.sound_clock_caller=123
            player=target.play(document);target.mono=2;player.update()
            self.assertEqual(player.phase,'ended');self.assertIsNone(target.capture.tape.error)
            self.assertEqual(target.capture.state(),source.capture.state())
        target=Engine();player=target.play(document);target.mono=2;player.update()
        self.assertEqual(player.phase,'failed','A missing unclassified clock must still fail')

    def test_playback_does_not_catch_up_a_stalled_window(self):
        document=Engine().record(200);target=Engine();player=target.play(document)
        target.mono=.1;player.update();before=player.seconds
        target.mono=8;player.update()
        self.assertLess(player.seconds-before,.1)
        target.now=999999
        self.assertEqual(target.call('clock'),999999,'Outer window scheduling keeps its real clock')
        player.control(dict(action='pause',paused=True));before=player.seconds
        target.mono=10;player.update();self.assertEqual(player.seconds,before)
        player.control(dict(action='pause',paused=False));target.mono+=.1;player.update()
        self.assertGreater(player.seconds,before)

    def test_clock_excursions_cannot_strand_playback_in_the_future(self):
        target=Engine();player=target.play(Engine().record(300))
        target.mono=.1;player.update();before=player.seconds
        # Observed with the embedded 32-bit interpreter at high render rates:
        # one monotonic clock sample jumps by a uint64 conversion interval.
        target.mono+=17179869.184;player.update()
        self.assertLess(player.seconds-before,.1)
        target.mono=.12;player.update();before=player.seconds
        for _ in range(100):target.mono+=1/240;player.update()
        self.assertGreater(player.seconds,before+.35)
        self.assertIsNone(target.capture.tape.error)

    def test_playback_speed_is_independent_of_render_rate_and_irregular_frames(self):
        document=Engine().record(300)
        for hz in (24,30,60,75,120,144,165,240,360,720):
            for speed in (.25,.5,1,2,4):
                target=Engine();player=target.play(document)
                player.control(dict(action='speed',speed=speed))
                for index in range(hz):
                    target.mono=(index+1)/hz;player.update()
                self.assertAlmostEqual(player.seconds,speed,delta=.018,msg=(hz,speed))
                self.assertIsNone(target.capture.tape.error)
        target=Engine();player=target.play(document)
        for gap in (.002,.017,.1,.004,.24,.03,.3,.001,.306):
            target.mono+=gap;player.update()
        self.assertAlmostEqual(player.seconds,1,delta=.018)

    def test_verified_audio_clock_count_can_vary_without_relaxing_game_clocks(self):
        def audio(engine,count,verified=True):
            engine.capture.sound_clock_caller=123 if verified else None
            engine.hooks['clock'].caller=0
            hook=engine.hooks['engine-frame'];original=hook.original
            def update(*args):
                result=original(*args)
                engine.hooks['clock'].caller=123
                for _ in range(count):engine.call('clock')
                engine.hooks['clock'].caller=0
                return result
            hook.original=update
        for legacy in (False,True):
            for count in (0,1,4):
                with self.subTest(legacy=legacy,playback_audio_calls=count):
                    source=Engine();audio(source,2,verified=not legacy)
                    document=source.record(8)
                    if not legacy:document['clock_scope']='game-v1'
                    target=Engine();audio(target,count)
                    player=target.play(document);target.mono=2
                    player.update()
                    self.assertEqual(player.phase,'ended')
                    self.assertIsNone(target.capture.tape.error)
        source=Engine();document=source.record(8);document['clock_scope']='game-v1'
        target=Engine();audio(target,1,verified=False)
        player=target.play(document);target.mono=2;player.update()
        self.assertEqual(player.phase,'failed','Unclassified extra clocks must still fail closed')

    def test_prepared_replay_attaches_without_validating_or_indexing_on_game_thread(self):
        from plugins._native_replay_player import PreparedReplay
        source=Engine();document=source.record();prepared=PreparedReplay(document)
        target=Engine()
        with mock.patch('plugins._native_replay_player.validate_operations',side_effect=AssertionError('game thread validation')):
            player=target.play(prepared)
        self.assertIs(player.document['ops'],prepared.document['ops'])
        self.assertIs(player.times,prepared.times)
        self.assertIs(player.capture.tape.ops,prepared.document['ops'])

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
        with mock.patch.object(capture,'sync_audio_clock',wraps=capture.sync_audio_clock) as rebase:
            capture.poll();rebase.assert_called_once_with(engine.now)
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

    def test_mixed_engine_time_credit_follows_each_recorded_handoff(self):
        player=object.__new__(NativePlayer)
        player.document=dict(runners=[dict(cursor=10,engine='browser'),dict(cursor=20,engine='native')])
        player.capture=NS(tape=NS(cursor=0))
        for cursor,expected in [(0,True),(9,True),(10,False),(19,False),(20,True),(30,True)]:
            player.capture.tape.cursor=cursor
            self.assertEqual(player.uses_native_time_credit(),expected)

    def test_repeated_takeover_requests_keep_the_offered_host_and_live_feedback(self):
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.serial=3;service.source=dict(id='source');service.command=dict(document=dict(runners=[]))
            service.clients.mark_runner=mock.Mock()
            host=NS(branch=dict(id='branch'),committed=False,bind=mock.Mock(),description=lambda:dict(id='branch'),
                    telemetry=mock.Mock(),binary=mock.Mock(),stop=mock.Mock())
            host.commit=lambda:setattr(host,'committed',True)
            service.warm=NS(take=mock.Mock(return_value=host),close=mock.Mock())
            request=dict(serial=3,status=dict(state='taking-over'),checkpoint=dict(cursor=5),takeover=True)
            service.feedback(request)
            deadline=time.monotonic()+2
            while service.preparing and time.monotonic()<deadline:time.sleep(.01)
            self.assertIs(service.host,host)
            self.assertEqual(service.clients.mark_runner.call_args.kwargs,dict(engine='native'))
            self.assertEqual(service.clients.mark_runner.call_args.args[2],5)
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

    def test_tap_released_during_handoff_survives_an_empty_first_iteration(self):
        target=Engine();player=target.play(Engine().record());target.mono=.1;player.update()
        player.keyboard_input(4,True);player.keyboard_input(4,False)
        chunks=[];player.commit(chunks.append,0);target.capture.reset_inputs=True
        before=target.props.xmp
        target.capture.before();target.capture.after()
        self.assertEqual(target.props.xmp,before)
        self.assertEqual(target.world.total,4,'Empty display iterations must not consume the tap')
        target.capture.before();target.call('frame',target.client);target.capture.after()
        self.assertEqual(target.props.xmp,before+17)
        self.assertEqual(target.world.total,0)
        target.capture.before();target.call('frame',target.client);target.capture.after()
        self.assertEqual(target.props.xmp,before+17,'A tap must not become a held key')
        self.assertIsNone(target.capture.tape.error)

    def test_pending_keyboard_takeover_reaches_a_checkpoint_before_freezing(self):
        target=Engine();player=target.play(Engine().record())
        # A living world can be visible before a usable last checkpoint exists.
        player.update();self.assertTrue(player.world_created)
        target.capture.tape.last_state=None
        player.paused=True;player.keyboard_input(4,True)
        self.assertTrue(player.requested);self.assertFalse(player.can_takeover())
        target.capture.before()
        self.assertTrue(player.can_takeover())
        self.assertTrue(player.requested)
        cursor=target.capture.tape.cursor
        target.mono+=1;target.capture.before()
        self.assertEqual(target.capture.tape.cursor,cursor,'Only a verified boundary may freeze for handoff')

    def test_cancel_pending_takeover_clears_tapped_inputs_and_allows_seek(self):
        target=Engine();player=target.play(Engine().record(100));target.mono=.1;player.update()
        player.keyboard_input(4,True);player.keyboard_input(4,False)
        player.control(dict(action='seek',seconds=.4,paused=False))
        self.assertFalse(player.requested);self.assertEqual(player.pending_keys,[])
        target.capture.before()
        self.assertFalse(player.seeking);self.assertGreater(player.seconds,.35)
        old=player.seconds;target.mono+=.1;target.capture.before()
        self.assertGreater(player.seconds,old)

    def test_failed_takeover_notification_restores_playback_once_and_ignores_stale_offer(self):
        import queue
        target=Engine();player=target.play(Engine().record(100));target.mono=.1;player.update()
        capture=target.capture
        capture.delivery=NS(incoming=queue.Queue(),feedback=mock.Mock(),put=mock.Mock())
        player.keyboard_input(4,True)
        failed=dict(serial=0,error='Could not reconstruct server',error_revision=1)
        capture.delivery.incoming.put(failed);capture.poll()
        self.assertFalse(player.requested);self.assertFalse(player.paused)
        self.assertEqual(player.pending_keys,[])
        old=player.seconds;target.mono+=.1;capture.before();self.assertGreater(player.seconds,old)
        # A delayed copy of the same failure must not cancel the user's retry.
        player.keyboard_input(4,True)
        capture.delivery.incoming.put(failed);capture.poll();self.assertTrue(player.requested)
        player.control(dict(action='seek',seconds=.4,paused=True));capture.before()
        capture.delivery.incoming.put(dict(serial=0,branch=dict(id='stale',sequence=0,cursor=1)))
        capture.poll();self.assertIsNone(capture.branch);self.assertEqual(player.phase,'playing')

    def test_cancelled_server_preparation_cannot_offer_a_branch_or_restart_from_stale_feedback(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.serial=3;service.source=dict(id='source');service.command=dict(document={})
            service.clients.mark_runner=mock.Mock()
            entered=threading.Event();release=threading.Event();finished=threading.Event()
            host=NS(branch=dict(id='branch'),bind=mock.Mock(),description=lambda:dict(id='branch'),
                    stop=lambda:finished.set())
            def take(*_):entered.set();release.wait(3);return host
            service.warm=NS(take=mock.Mock(side_effect=take),close=mock.Mock())
            request=dict(serial=3,status=dict(state='taking-over'),checkpoint=dict(cursor=5),takeover=True,takeover_generation=0)
            try:
                service.feedback(request);self.assertTrue(entered.wait(1))
                service.action(dict(action='seek',seconds=.3))
                service.feedback(request)
                self.assertEqual(service.warm.take.call_count,1)
                release.set();self.assertTrue(finished.wait(2))
                host.bind.assert_not_called();self.assertIsNone(service.branch);self.assertIsNone(service.host)
                self.assertFalse(service.preparing)
            finally:release.set();service.close()

    def test_server_takeover_failure_waits_for_a_new_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.serial=3;service.source=dict(id='source');service.command=dict(document={})
            service.warm=NS(take=mock.Mock(side_effect=ValueError('bad checkpoint')),close=mock.Mock())
            request=dict(serial=3,status=dict(state='taking-over'),checkpoint=dict(cursor=5),takeover=True,takeover_attempt=1)
            for attempt in (1,1,2):
                request['takeover_attempt']=attempt;service.feedback(request)
                deadline=time.monotonic()+2
                while service.preparing and time.monotonic()<deadline:time.sleep(.01)
                self.assertEqual(service.warm.take.call_count,attempt)
            self.assertEqual(service.error_revision,2)
            service.close()

    def test_pause_cancels_takeover_without_dropping_an_already_queued_seek(self):
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            service.source=dict(id='source');service.command=dict(document={})
            service.action(dict(action='takeover'))
            service.action(dict(action='seek',seconds=.7))
            service.action(dict(action='pause',paused=True))
            self.assertEqual(service.poll(0)['controls'],[dict(action='seek',seconds=.7),dict(action='pause',paused=True)])
            service.close()

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
    def test_loading_is_responsive_and_cancelled_selection_cannot_replace_new_state(self):
        import threading
        with tempfile.TemporaryDirectory() as directory:
            records,native,request,document=native_fixture(directory);service=NativePlayback(directory,'a'*64)
            entered=threading.Event();release=threading.Event()
            def read(_):entered.set();release.wait(3);return document
            try:
                with mock.patch.object(service.clients,'read',side_effect=read):
                    result=service.action(dict(action='watch',record=request['record']))
                    self.assertTrue(entered.wait(1))
                    self.assertEqual(service.poll(0)['serial'],result['serial'])
                    self.assertEqual(service.status['stage'],'reading')
                    service.action(dict(action='stop'))
                    release.set();service.load_thread.join(3)
                    self.assertEqual(service.status['state'],'stopped')
                    stopped=service.poll(0)
                    self.assertNotIn('play',stopped)
                    self.assertEqual(stopped['controls'],[dict(action='stop')])
            finally:release.set();service.close()

    def test_transfer_pages_are_bounded_and_reject_stale_or_out_of_range_requests(self):
        from plugins._native_replay import OperationValidator
        with tempfile.TemporaryDirectory() as directory:
            service=NativePlayback(directory,'a'*64)
            payload=base64.b64encode(b'x'*200000).decode()
            ops=[['loop',0],['message',10,payload],['return','message'],['state',[1,0]],['loop',1]]*6
            service.serial=3;service.command=dict(document=dict(ops=ops))
            validator=OperationValidator();offset=0;pages=0
            while offset<len(ops):
                page=service.page(3,offset);pages+=1
                self.assertLess(len(json.dumps(page,separators=(',',':')).encode()),512*1024)
                validator.append(page['ops']);offset+=len(page['ops'])
            self.assertGreater(pages,1);self.assertEqual(validator.finish(),ops)
            for serial,offset in ((2,0),(3,-1),(3,len(ops))):
                with self.assertRaises(ValueError):service.page(serial,offset)
            service.close()

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
                endpoint.playback.load_thread.join(3)
                initial=control.call('/control?serial=0')
                self.assertTrue(endpoint.ready.is_set());self.assertEqual(initial['play']['record'],request['record'])
                # This models the plugin's verified frame-boundary request,
                # rather than accepting coordinates supplied by the record UI.
                control.call('/feedback',dict(serial=initial['serial'],status=dict(state='taking-over'),checkpoint=request,takeover=True))
                deadline=time.monotonic()+15;branch=None
                while time.monotonic()<deadline:
                    response=control.call('/control?serial='+str(initial['serial']))
                    self.assertIsNone(response.get('error'),response)
                    branch=response.get('branch')
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
                service.load_thread.join(3)
                initial=service.poll(0)
                selected=dict(initial['play']['document'],ops=service.page(service.serial,0)['ops'])
                self.assertEqual({k:v for k,v in selected.items() if k!='view'},document)
                self.assertEqual(selected['view']['end'],len(document['ops']))
                self.assertNotIn('play',service.poll(initial['serial']))
                service.action(dict(action='speed',speed=.25));self.assertEqual(service.poll(service.serial)['controls'][0]['speed'],.25)
                service.feedback(dict(serial=service.serial-1,status=dict(state='live')))
                self.assertEqual(service.status['state'],'starting','Stale player feedback cannot control a new replay')
                self.assertTrue(service.availability([request['id']])[request['id']]['available'])
            finally:service.close()


if __name__=='__main__':unittest.main()
