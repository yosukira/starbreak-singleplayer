"""Late ordinary turrets join a shared GY/EGY volley, without catch-up shots."""
from types import SimpleNamespace
import unittest

from localserver.live import constants
from localserver.live.bridge import PlayerState
from localserver.live.turrets import TurretSlot, build_batteries
from localserver.maps.hulk_normal.rules import HulkNormalRules


class TurretVolleyTests(unittest.TestCase):
    def battery(self, rules=constants, ammo='csbullet', front=True):
        centers = {44: (0, 0), 46: (2000 * 256, 0), 52: (0, 0)}
        room = SimpleNamespace(turret_center=lambda oid: centers[oid], object=lambda oid: {})
        layout = {oid: TurretSlot(oid, row, 0, 1, (0, 0), ammo, fixed)
                  for oid, row, fixed in [(44, 'upper', False), (46, 'lower', False)]
                  + ([(52, 'front', True)] if front else [])}
        battery, = build_batteries(room, layout=layout, rules=rules)
        player = PlayerState(objid=1, worldid=1, xmp=-20 * 256, ymp=-40 * 256,
                             wmp=40 * 256, hmp=80 * 256, hp=100, maxhp=100)
        events = []

        def step(phase, active=True):
            start = len(events)
            battery.step(.05, phase, player,
                         lambda oid, ammo, aim, stage: events.append((phase, oid, stage)),
                         players=(player,) if active else ())
            return events[start:]
        return battery, centers, step, events

    def test_gy_and_egy_late_turret_joins_next_volley(self):
        for rules in (HulkNormalRules(), constants):
            with self.subTest(rules=type(rules).__name__):
                battery, centers, step, events = self.battery(rules)
                first, period = battery.first_phase, battery.period
                charge = rules.TURRET_CHARGE_SECONDS['csbullet']
                self.assertEqual({e[1] for e in step(first)}, {44, 52})
                self.assertEqual({e[1] for e in step(first + charge + .001)}, {44, 52})
                centers[46] = (0, 0)
                self.assertEqual(step(first + period / 2), [], 'No off-beat activation shot')
                self.assertEqual({e[1] for e in step(first + period)}, {44, 46, 52})
                released = step(first + period + charge + .001)
                self.assertEqual({e[1] for e in released}, {44, 46, 52})
                self.assertTrue(all(e[2] == 'release' for e in released))

    def test_empty_battle_keeps_the_clock_without_catch_up_volleys(self):
        battery, centers, step, events = self.battery(front=False)
        first, period = battery.first_phase, battery.period
        step(first + 20.25 * period, active=False)
        centers[46] = (0, 0)
        self.assertEqual(step(first + 20.5 * period), [])
        volley = step(first + 21 * period)
        self.assertEqual({e[1] for e in volley}, {44, 46})
        self.assertEqual(len(volley), 2, 'Exactly one volley after missed cycles')
        self.assertEqual(step(first + 21 * period + .01), [])

    def test_reacquired_target_waits_for_the_shared_clock(self):
        battery, centers, step, events = self.battery(front=False)
        centers[46] = (0, 0)
        first, period = battery.first_phase, battery.period
        step(first)
        # Already charged rounds still release even if their target disappears.
        release = step(first + constants.TURRET_CHARGE_SECONDS['csbullet'] + .001, active=False)
        self.assertEqual({e[1] for e in release}, {44, 46})
        step(first + period, active=False)
        self.assertEqual(step(first + 1.5 * period), [])
        self.assertEqual({e[1] for e in step(first + 2 * period)}, {44, 46})

    def test_step_rounding_does_not_shift_individual_turrets(self):
        battery, centers, step, events = self.battery()
        first, period = battery.first_phase, battery.period
        step(first + .03)
        step(first + 1.2)
        centers[46] = (0, 0)
        self.assertEqual(step(first + 1.3), [])
        self.assertEqual({e[1] for e in step(first + period + .047)}, {44, 46, 52})
        self.assertGreater(battery.next_volley, first + period + .047)

    def test_scatter_and_missile_activation_timing_is_unchanged(self):
        for ammo in ('scattershot', 'bossmissile'):
            with self.subTest(ammo=ammo):
                battery, centers, step, events = self.battery(ammo=ammo, front=False)
                first, period = battery.first_phase, battery.period
                step(first)
                step(first + constants.TURRET_CHARGE_SECONDS[ammo] + .001)
                centers[46] = (0, 0)
                late = step(first + period / 2)
                self.assertEqual([(e[1], e[2]) for e in late], [(46, 'prepare')])


if __name__ == '__main__':
    unittest.main()
