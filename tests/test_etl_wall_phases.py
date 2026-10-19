import itertools
import random
from types import SimpleNamespace
import unittest

from localserver.maps.lab_hard.attack_clocks import WallPullClock
from localserver.maps.lab_hard.scene import LabHardPack
from localserver.maps.lab_hard.scheduling import encounter_schedules
from localserver.maps.lab_hard.session import LabHardLiveSession


class ETLWallPhaseTests(unittest.TestCase):
    def test_four_hand_phase_never_randomly_falls_back_to_three(self):
        pack = LabHardPack()
        observed = {phase: set() for phase in range(1, 5)}
        for seed in range(20):
            clock = WallPullClock(pack, random.Random(seed))
            now = 0.
            for phase in range(1, 5):
                for cycle in range(30):
                    now += 100
                    events = clock.step(now, phase, 100)
                    self.assertEqual(len(events), phase, (seed, phase, cycle))
                    self.assertEqual(len({e['wall'] for e in events}), phase)
                    self.assertTrue(all(len(e['group_walls']) == phase for e in events))
                    observed[phase].add(tuple(sorted(e['wall'] for e in events)))
                    clock.note_release(now+2)
        # Motion examples must not constrain the randomly chosen directions:
        # all four singles, six pairs, four triples and the four-wall pull occur.
        for phase in range(1, 5):
            self.assertEqual(observed[phase], set(itertools.combinations(sorted(pack.wall_ids), phase)))

    def test_final_phase_waits_for_four_free_claws(self):
        clock = WallPullClock(LabHardPack(), random.Random(5))
        claws = list(clock.claws.values())
        clock.available_claws = lambda: claws[:3]
        self.assertEqual(clock.step(100, 4, 100), [])
        self.assertTrue(clock.waiting_for_claws)
        clock.available_claws = lambda: claws
        self.assertEqual(len(clock.step(101, 4, 101)), 4)

    def test_legacy_schedule_obeys_the_same_phase_counts(self):
        pack = LabHardPack()
        _, structures, _ = encounter_schedules(pack, pack.cadence)
        for phase in range(1, 5):
            events = structures.step(500, phase, 500)
            pulls = [row for row in events if row['kind'] == 'wall_pull']
            self.assertTrue(pulls)
            self.assertTrue(all(len(row['group_walls']) == phase for row in pulls))

    def test_old_pending_three_hand_action_is_discarded_after_phase_change(self):
        session = LabHardLiveSession.__new__(LabHardLiveSession)
        session.controller = SimpleNamespace(machine=SimpleNamespace(index=4))
        session.pending_wall_groups = {(3, 1, 1): [dict(wall=w, claw=w+100, group_walls=[1,2,3]) for w in (1,2,3)]}
        emitted = []
        session._wall_claw_idle = lambda claw: True
        session._emit_wall_pull = emitted.append
        session._step_pending_wall_groups()
        self.assertFalse(emitted)
        self.assertFalse(session.pending_wall_groups)


if __name__ == '__main__': unittest.main()
