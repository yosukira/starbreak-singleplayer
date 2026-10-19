import copy
import json
import math
import inspect
from types import SimpleNamespace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

from localserver import game_settings, paths, pb_layout
from localserver.live.bridge import PlayerBridge
from localserver.live.room import BossRoom
from localserver.replay import ServerCapture, ServerReplay, ReplayMismatch, JournalWriter, read_journal, engine_fingerprint
from localserver.server import parse_args, _map_session_class


def encounter(map_id, room_seed=None, worldid=1):
    source = paths.ROOT/('data/catalogue/real_hulkboss_createworld_flags.hex' if map_id == 'egy_hulk' else 'maps/'+map_id+'/createworld.hex')
    options = parse_args(['--map', map_id, '--create-world-hex', str(source)])
    options.live_seed = 1234567
    options.worldid = worldid
    if room_seed is not None:options.room_roll_seed=room_seed
    bridge = PlayerBridge(0)
    server = SimpleNamespace(layouts=pb_layout.default_layouts(), _worldid=lambda: worldid,
        _send_raw=lambda *a, **kw: None, class_loadouts={}, item_types={3, 4})
    session_class = _map_session_class(map_id)
    kwargs = dict(bridge=bridge)
    if 'seed' in inspect.signature(session_class).parameters:kwargs['seed']=options.live_seed
    session = session_class(server, None, options,
        BossRoom.from_capture(str(source), options.boss_templates), **kwargs)
    return session, bridge, server


def input_at(tick):
    return dict(seq=tick, player=dict(objid=1, worldid=2, xmp=round((1100+500*math.sin(tick/100))*256),
        ymp=-100*256, wmp=40*256, hmp=80*256, hp=500, maxhp=500, alive=True, room_seed=123,
        account='not-for-recording', password='not-for-recording', vid='browser-player'),
        keys=dict(interact=False), events=[])


class ReplayTests(unittest.TestCase):
    def test_etl_mid_and_boss_restore_random_state_and_bounded_bridge_polling(self):
        for map_id in ('lab_hard', 'lab_mid'):
            with self.subTest(map=map_id), game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                session,bridge,server=encounter(map_id)
                capture=ServerCapture(session);capture.run('prepare')
                for tick in range(1200):
                    raw=input_at(tick)
                    if tick%60==30:
                        raw['events']=[dict(kind='damage',victim=session.pack.boss_objid,value=5000,ownerid=session.worldid)]
                    bridge._absorb(raw);capture.run('step',.05)
                rebuilt=ServerReplay(json.loads(json.dumps(capture.document())))
                while rebuilt.advance():pass
                self.assertIsNone(capture.failed)
                self.assertEqual(rebuilt.session.rng.getstate(),session.rng.getstate())
                self.assertEqual(rebuilt.session.loot_rng.getstate(),session.loot_rng.getstate())
                self.assertEqual(rebuilt.session.room_time,session.room_time)
                self.assertIsNotNone(session.controller)
                self.assertGreater(sum(f['packets'] for f in capture.frames),1000)
                capture.detach()
                original_packets=[];restored_packets=[]
                session.server=SimpleNamespace(**dict(vars(server),_send_raw=lambda c,k,p,*a,**kw:original_packets.append((k,p))))
                restored=rebuilt.continue_live(bridge,server,None)
                restored.server=SimpleNamespace(**dict(vars(server),_send_raw=lambda c,k,p,*a,**kw:restored_packets.append((k,p))))
                for tick in range(1200,1260):
                    bridge._absorb(input_at(tick));session.step(.05)
                    bridge._absorb(input_at(tick));restored.step(.05)
                self.assertEqual(original_packets,restored_packets)

    def capture(self, map_id='egy_hulk', frames=300):
        settings = game_settings.normalize(dict(map=map_id, skip_intro={map_id: True},
            private_address='not-for-recording', password='not-for-recording'))
        with game_settings.event_snapshot(settings):
            session, bridge, server = encounter(map_id)
            capture = ServerCapture(session)
            capture.run('prepare')
            for tick in range(frames):
                bridge._absorb(input_at(tick))
                capture.run('step', 1/60 + (tick%3)*.0001)
            return capture, bridge, server

    def test_rebuilds_both_encounters_packet_for_packet_and_continues_same_state(self):
        for map_id in ('egy_hulk', 'hulk_normal'):
            with self.subTest(map=map_id):
                capture, bridge, server = self.capture(map_id)
                document = json.loads(json.dumps(capture.document()))
                self.assertNotIn('not-for-recording', json.dumps(document))
                replay = ServerReplay(document)
                while replay.advance(): pass
                source, restored = capture.session, replay.continue_live(bridge, server, None)
                self.assertEqual(restored.phase, source.phase)
                self.assertEqual(restored.room_time, source.room_time)
                self.assertEqual(restored.turret_random.getstate(), source.turret_random.getstate())
                self.assertEqual(restored.loot_random.getstate(), source.loot_random.getstate())
                # Continue two real controllers with identical fresh telemetry.
                # Outgoing packets include all movement, attacks and loot rolls.
                capture.detach()
                a, b = [], []
                source.server = SimpleNamespace(**dict(vars(server), _send_raw=lambda c,k,p,*args,**kw: a.append((k,p))))
                restored.server = SimpleNamespace(**dict(vars(server), _send_raw=lambda c,k,p,*args,**kw: b.append((k,p))))
                with game_settings.event_snapshot(replay.settings):
                    for tick in range(300, 420):
                        bridge._absorb(input_at(tick))
                        source.step(1/60)
                        bridge._absorb(input_at(tick))
                        restored.step(1/60)
                self.assertEqual(a, b)

    def test_rejects_corrupted_or_incompatible_stream_before_continuation(self):
        capture, bridge, server = self.capture(frames=3)
        changed = capture.document()
        changed['frames'][1]['sha256'] = '0'*64
        replay = ServerReplay(changed)
        replay.advance()
        with self.assertRaises(ReplayMismatch): replay.advance()
        with self.assertRaises(ReplayMismatch): replay.continue_live(bridge, server, None)
        changed = capture.document(); changed['engine'] = '0'*64
        with self.assertRaises(ValueError): ServerReplay(changed)

    def test_live_parameter_changes_and_queued_damage_are_reproduced(self):
        settings = game_settings.normalize(dict(map='egy_hulk', skip_intro={'egy_hulk': True}))
        with game_settings.event_snapshot(settings):
            session, bridge, _ = encounter('egy_hulk')
            capture = ServerCapture(session); capture.run('prepare')
            for tick in range(180):
                document = input_at(tick)
                if tick == 60:
                    document['events'] = [dict(kind='damage', victim=99999, value=60, ownerid=2, native_seed=123)]
                    settings['missile_delay_ms']['egy_hulk'] = 0
                    settings['balance']['egy_hulk'] = dict(enemy_speed=.5)
                bridge._absorb(document)
                with game_settings.event_snapshot(settings): capture.run('step', 1/60)
        replay = ServerReplay(capture.document())
        while replay.advance(): pass
        self.assertEqual(replay.session.projectiles.missile_delay_seconds, 0)
        self.assertEqual(replay.session.balance.values['enemy_speed'], .5)

    def test_compressed_journal_requires_complete_verified_footer(self):
        capture, _, _ = self.capture(frames=3)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'record.gz'
            writer = JournalWriter(path, capture.header)
            for frame in capture.frames: writer.frame(frame)
            writer.close()
            document = read_journal(path)
            self.assertEqual(document, capture.document())
            replay = ServerReplay(document)
            while replay.advance(): pass
            broken = Path(directory)/'broken.gz'
            writer = JournalWriter(broken, capture.header)
            writer.frame(capture.frames[0]); writer.close(complete=False)
            with self.assertRaises(ValueError): read_journal(broken)

    def test_background_restart_watcher_is_not_a_simulation_observation(self):
        with game_settings.event_snapshot(game_settings.normalize({'map':'egy_hulk'})):
            session, bridge, _ = encounter('egy_hulk')
            original = session.step
            def concurrent(dt):
                watcher = threading.Thread(target=lambda: session.bridge.peek_events())
                watcher.start(); watcher.join()
                return original(dt)
            session.step = concurrent
            capture = ServerCapture(session)
            capture.run('prepare')
            bridge._absorb(input_at(1))
            capture.run('step', 1/60)
        replay = ServerReplay(capture.document())
        while replay.advance(): pass

    def test_recording_stop_inside_a_frame_seals_it_before_restoring_live_clock(self):
        from localserver.battle_records import attach
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict('os.environ', SB_RECORD_REPLAY='1', SB_BATTLE_RECORDS_PATH=directory):
            with game_settings.event_snapshot(game_settings.normalize({'map':'egy_hulk'})):
                session, bridge, _ = encounter('egy_hulk')
                original = session.step
                def stop_inside(dt):
                    original(dt)
                    session.stop()
                session.step = stop_inside
                recorder = attach(session)
                session.prepare()
                bridge._absorb(input_at(1))
                session.step(1/60)
                self.assertTrue(recorder.closed)
                self.assertFalse(hasattr(session,'_replay_wall_time'))
                self.assertIs(session.bridge,bridge)
            document = read_journal(next(Path(directory).glob('*.gz')))
            self.assertEqual(len(document['frames']),2)
            replay = ServerReplay(document)
            while replay.advance(): pass

    def test_failed_frame_cannot_be_published_as_a_complete_replay(self):
        with game_settings.event_snapshot(game_settings.normalize({'map':'egy_hulk'})):
            session, bridge, _ = encounter('egy_hulk')
            capture = ServerCapture(session)
            capture.run('prepare')
            capture.step = mock.Mock(side_effect=RuntimeError('incomplete frame'))
            with self.assertRaises(RuntimeError):capture.run('step',1/60)
            self.assertIsNotNone(capture.failed)
            capture.detach()
            self.assertFalse(hasattr(session,'_replay_wall_time'))

    def test_history_only_changes_do_not_invalidate_the_simulation_identity(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(paths,'ROOT',Path(directory)):
            ai=Path(directory)/'localserver/live/enemy.py';ai.parent.mkdir(parents=True)
            ai.write_text('speed = 20')
            recorder=Path(directory)/'localserver/battle_records.py';recorder.write_text('columns = 4')
            original=engine_fingerprint()
            recorder.write_text('columns = 5')
            self.assertEqual(engine_fingerprint(),original)
            ai.write_text('speed = 30')
            self.assertNotEqual(engine_fingerprint(),original)


if __name__ == '__main__': unittest.main()
