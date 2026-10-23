"""Global SC countdown notifications through room handoffs and Gen bonuses."""
import unittest

import test_sc_run as fixtures


class ScTimerNoticeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ScRunTests()
        self.run, self.clock = self.fixture.make_run()

    def notices(self, server):
        return [server.layouts.decode('Chat', payload)
                for kind, payload, label in server.sent
                if kind == 20 and label == 'sc-announcement']

    def test_first_boundary_uses_recorded_text_and_chat_type_once(self):
        wrapper, server = self.fixture.open_room(self.run, 'entry')
        self.clock.advance(59.999)
        wrapper.step(.01)
        self.assertEqual(self.notices(server), [])
        self.clock.advance(.001)
        wrapper.step(.01)
        notices = self.notices(server)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['message_'], 'Firing Sequence Completes in 23 Minutes')
        self.assertEqual(notices[0]['chattype_'], 5)
        wrapper.step(.01)
        self.assertEqual(len(self.notices(server)), 1)

    def test_gen_bonus_preserves_the_original_minute_phase(self):
        wrapper, server = self.fixture.open_room(self.run, 'entry')
        self.clock.advance(60)
        wrapper.step(.01)
        self.clock.advance(31.5)
        self.run.mark_gen(0)
        wrapper.step(.01)
        self.assertEqual(len(self.notices(server)), 1)
        self.clock.advance(28.5)
        wrapper.step(.01)
        self.assertEqual([n['message_'] for n in self.notices(server)], [
            'Firing Sequence Completes in 23 Minutes',
            'Firing Sequence Completes in 24 Minutes'])

    def test_room_handoff_keeps_notice_history_and_skips_missed_minutes(self):
        wrapper, server = self.fixture.open_room(self.run, 'entry')
        self.clock.advance(60)
        wrapper.step(.01)
        wrapper, _ = self.fixture.open_room(self.run, 'engine', server)
        self.assertEqual(len(self.notices(server)), 1)
        self.clock.advance(181)
        wrapper.step(.01)
        notices = self.notices(server)
        self.assertEqual(len(notices), 2)
        self.assertEqual(notices[-1]['message_'], 'Firing Sequence Completes in 20 Minutes')
        wrapper, _ = self.fixture.open_room(self.run, 'entry', server)
        wrapper.step(.01)
        self.assertEqual(len(self.notices(server)), 2)

    def test_zero_clock_does_not_announce_or_exit_and_bonus_can_resume_it(self):
        wrapper, server = self.fixture.open_room(self.run, 'entry')
        self.clock.advance(1440)
        wrapper.step(.01)
        self.assertEqual(self.notices(server), [])
        self.assertEqual(server.exits, [])
        self.assertEqual(server.transitions, [])
        self.clock.advance(1)
        self.run.mark_gen(0)
        self.clock.advance(59)
        wrapper.step(.01)
        self.assertEqual(self.notices(server)[-1]['message_'], 'Firing Sequence Completes in 1 Minutes')
        self.assertEqual(server.exits, [])

    def test_collapse_stops_firing_countdown_notices(self):
        self.clock.advance(59)
        for index in range(4):
            self.run.mark_gen(index)
        self.clock.advance(1)
        self.assertIsNone(self.run.timer_notice())
        self.assertEqual(self.run._timer_notice_boundary, 1)


if __name__ == '__main__':
    unittest.main()
