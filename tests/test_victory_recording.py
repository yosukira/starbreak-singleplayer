import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from localserver import battle_records,game_settings
from localserver.replay import read_journal
from localserver.run_replay import RunRecording
from test_replay import encounter,input_at

class VictoryRecordingTests(unittest.TestCase):
    def test_standalone_victory_journal_keeps_the_complete_animation_and_combat_time(self):
        with tempfile.TemporaryDirectory() as directory,mock.patch.dict(os.environ,SB_BATTLE_RECORDS_PATH=directory,SB_RECORD_REPLAY='1'),game_settings.event_snapshot(game_settings.normalize(dict(map='egy_hulk',skip_intro={'egy_hulk':True}))):
            session,bridge,_=encounter('egy_hulk');recorder=battle_records.attach(session)
            try:
                session.prepare()
                def feedback(tick):
                    value=input_at(tick);value['keys']['gameplay']=True;bridge._absorb(value)
                for tick in range(20):feedback(tick);session.step(.05)
                session._begin_death();feedback(21);session.step(.05)
                self.assertTrue(recorder.closed,'The combat result is immediately final')
                record=recorder.store.get(recorder.identifier);self.assertEqual(record['result'],'victory')
                path=Path(directory)/(recorder.identifier+'.server.jsonl.gz')
                with self.assertRaises((EOFError,ValueError,OSError)):read_journal(path)
                for tick in range(22,170):
                    feedback(tick);session.step(.05)
                    if session.phase=='done':break
                self.assertEqual(session.phase,'done')
                journal=read_journal(path)
                self.assertGreater(sum(frame['dt'] for frame in journal['frames']),record['duration']+4)
                self.assertEqual(recorder.store.get(recorder.identifier)['duration'],record['duration'],'Presentation tail does not inflate combat time')
                self.assertEqual(journal['victory_rules'],1)
            finally:session.stop()

    def test_full_run_tail_waits_for_the_current_boss_but_stop_and_failure_seal(self):
        current=NS(phase='death',death_records=[1]);session=NS(current=current)
        recording=RunRecording.__new__(RunRecording)
        recording.closed=False;recording.recorder=NS(closed=True)
        recording.capture=NS(active=None,failed=None,session=session,detach=mock.Mock())
        recording.writer=mock.Mock()
        recording.seal();recording.writer.close.assert_not_called()
        current.phase='done';recording.seal();recording.writer.close.assert_called_once_with(complete=True)
        for force,failed in [(True,None),(False,'write failure')]:
            recording.closed=False;current.phase='death';recording.capture.failed=failed;recording.writer.reset_mock()
            recording.seal(force=force);recording.writer.close.assert_called_once_with(complete=not failed)

    def test_historical_journals_and_other_encounters_keep_their_original_boundary(self):
        self.assertFalse(battle_records.recording_tail_pending(NS(phase='death')))
        self.assertFalse(battle_records.recording_tail_pending(NS(phase='death',death_records=[1],_legacy_victory_presentation=True)))

if __name__=='__main__':unittest.main()
