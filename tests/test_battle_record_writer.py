import copy
import os
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

from localserver import battle_records as records
from localserver.live.bridge import PlayerState
from test_battle_records import sample, participating


class RecordWriterTests(unittest.TestCase):
    def test_blocked_disk_does_not_block_ticks_and_latest_final_result_is_durable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = records.RecordStore(directory)
            entered, release = threading.Event(), threading.Event()
            original = store.save
            written = []

            def blocked(document):
                written.append(copy.deepcopy(document))
                entered.set()
                if not release.wait(5):
                    raise TimeoutError('test disk was never released')
                return original(document)

            store.save = blocked

            session = participating(SimpleNamespace(
                options=SimpleNamespace(map='egy_hulk', player_type=1), seed=123,
                player=PlayerState(objid=1, worldid=2, hp=100, alive=True), phase='fighting'))
            recorder = records.BattleRecorder(session, store, background_writes=True)
            completed = threading.Event()
            failures = []

            def advance():
                try:
                    with mock.patch.object(records, 'snapshot', return_value=records.parameters(sample()['parameters'])):
                        recorder.tick(1)
                        if not entered.wait(2):
                            raise AssertionError('disk worker did not start')
                        # This includes sixty periodic checkpoints while the
                        # first actual SQLite write remains blocked.
                        for _ in range(60):
                            recorder.tick(1)
                        recorder.damage(123, killed=True)
                        recorder.tick(.02)
                        recorder.finish('victory')
                    completed.set()
                except Exception as error:
                    failures.append(error)

            simulation = threading.Thread(target=advance)
            simulation.start()
            try:
                progressed = completed.wait(3)
                self.assertEqual(failures, [])
                self.assertTrue(progressed, 'simulation waited for disk I/O')
                self.assertEqual(len(written), 1, 'blocked writes must coalesce')
                self.assertEqual(written[0]['duration'], 1)
                recorder.document['metrics']['damage_dealt'] = 999  # not shared with worker
            finally:
                release.set()
                simulation.join(5)
                recorder.flush()
            self.assertEqual(len(written), 2)
            saved = store.get(recorder.identifier)
            self.assertEqual(saved['result'], 'victory')
            self.assertEqual(saved['metrics']['damage_dealt'], 123)
            self.assertEqual(saved['metrics']['kills'], 1)
            self.assertGreater(saved['duration'], 60)
            self.assertFalse(recorder.writer.thread.is_alive())

    def test_write_failure_is_reported_and_worker_can_be_closed(self):
        error = OSError('disk full')
        attempted = threading.Event()

        def fail(document):
            attempted.set()
            raise error

        writer = records.RecordWriter(SimpleNamespace(save=fail))
        writer.submit(sample())
        self.assertTrue(attempted.wait(2))
        with self.assertRaisesRegex(OSError, 'disk full'):
            writer.close()
        with self.assertRaisesRegex(OSError, 'disk full'):
            writer.submit(sample())
        self.assertFalse(writer.thread.is_alive())

    def test_native_room_handoff_keeps_writer_and_shutdown_flushes_once(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
                os.environ, SB_BATTLE_RECORDS_PATH=directory, SB_RECORD_REPLAY='0'):
            run = SimpleNamespace(seed=123, completed=False)
            player = PlayerState(objid=1, worldid=2, hp=100, alive=True)

            def session(role):
                return SimpleNamespace(options=SimpleNamespace(map='egy_run', player_type=1),
                    run=run, instance=SimpleNamespace(role=role, seed=1, visits=1), _ready=True,
                    bridge=SimpleNamespace(snapshot=lambda: (player, {'gameplay': True}), native_context={}),
                    step=lambda dt: None, stop=lambda **kw: None, log=lambda message: None)

            with mock.patch.object(records, 'snapshot', return_value=records.parameters(sample()['parameters'])):
                first = session('entry')
                recorder = records.attach(first, background_writes=True)
                first.step(.1)
                first._battle_room_transition = True
                first.stop()
                self.assertFalse(recorder.writer.closed)
                second = session('boss')
                self.assertIs(records.attach(second, background_writes=True), recorder)
                second.step(.1)
                second.stop()
            saved = recorder.store.get(recorder.identifier)
            self.assertEqual(saved['result'], 'interrupted')
            self.assertEqual([room['role'] for room in saved['rooms']], ['entry', 'boss'])
            self.assertFalse(recorder.writer.thread.is_alive())
            self.assertEqual(len(recorder.store.list()), 1)

    def test_unused_writer_does_not_create_threads_or_records(self):
        store = mock.Mock()
        writer = records.RecordWriter(store)
        writer.close()
        self.assertIsNone(writer.thread)
        store.save.assert_not_called()

    def test_launcher_opt_in_excludes_browser_and_manual_replay_runtimes(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(
                os.environ, SB_BATTLE_RECORDS_PATH=directory, SB_BATTLE_BACKGROUND_WRITES='1', SB_RECORD_REPLAY='0'):
            for web, manual, asynchronous in ((False, False, True), (True, False, False), (False, True, False)):
                with self.subTest(web=web, manual=manual):
                    session = SimpleNamespace(options=SimpleNamespace(map='egy_hulk', web_client=web),
                        server=SimpleNamespace(manual_ticks=manual), step=lambda dt: None,
                        stop=lambda: None, log=lambda message: None)
                    recorder = records.attach(session)
                    self.assertEqual(recorder.writer is not None, asynchronous)
                    session.stop()


if __name__ == '__main__':
    unittest.main()
