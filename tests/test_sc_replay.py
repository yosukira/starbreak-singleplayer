import copy
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from localserver import game_settings
from localserver.run_replay import RunReplay, validate
from test_run_replay import WholeRun


class ScReplayTests(unittest.TestCase):
    def test_all_room_music_uses_available_original_audio(self):
        from localserver.maps.sc_run.scene import ScRunPack
        from tools.audio_assets import inventory
        pack=ScRunPack().configure(42)
        sounds=inventory()
        for role in pack.room_names:
            with self.subTest(room=role):
                music=pack.room(role)['world']['worldprops_']['music_']
                self.assertIn('/client/data/music/'+music+'.ogg',sounds)

    def test_global_clock_loading_gap_room_return_and_takeover(self):
        with game_settings.event_snapshot(game_settings.normalize(dict(map='sc_run'))):
            source = WholeRun('sc_run')
            elapsed = [source.capture.run_elapsed]
            source.capture.run_clock = lambda: source.session.run.started_at + elapsed[0]
            for _ in range(30):
                elapsed[0] += .02
                source.feedback()
            first = source.session.instance
            target = next(link['destination'] for link in first.recipe['links'].values()
                          if source.session.run.allows(link))
            source.travel(target, changed_items=True)
            # Real wall time can pass during assets/inventory loading, without
            # the tick dt growing to that duration.
            elapsed[0] += 91.5
            for _ in range(5):
                source.feedback()
            source.travel(first.role)
            for _ in range(5):
                elapsed[0] += .02
                source.feedback()
            document = json.loads(json.dumps(source.capture.document()))
            self.assertEqual(document['run_clock'], 'elapsed-v1')
            self.assertGreater(document['frames'][-1]['run_elapsed'], 90)
            replay = RunReplay(document)
            try:
                while replay.advance():
                    pass
                self.assertEqual(source.session.run.status(), replay.session.run.status())
                for role, room in source.session.run.rooms.items():
                    restored = replay.session.run.rooms[role]
                    self.assertEqual(room.snapshot is None, restored.snapshot is None)
                    self.assertEqual(room.document(2), restored.document(2))
                remaining = replay.session.run.remaining()
                with mock.patch('localserver.run_replay.time.monotonic', return_value=5000.) as clock:
                    live = replay.continue_live(source.bridge, SimpleNamespace(send_frame=lambda *_: None))
                    self.assertEqual(live.run.remaining(), remaining)
                    clock.return_value = 5001.
                    self.assertAlmostEqual(live.run.remaining(), remaining - 1., places=6)
                missing = copy.deepcopy(document)
                missing.pop('run_clock')
                with self.assertRaisesRegex(ValueError, 'clock is missing'):
                    validate(missing)
                backwards = copy.deepcopy(document)
                backwards['frames'][-1]['run_elapsed'] = -1
                with self.assertRaises(ValueError):
                    validate(backwards)
            finally:
                replay.close()
