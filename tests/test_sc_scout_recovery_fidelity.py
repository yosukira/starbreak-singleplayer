"""Long scout dives and inertial recovery through real actor/wire snapshots."""
import math
import unittest

from test_sc_ghost_volley_fidelity import actor_host


def committed_scout(host, position, velocity):
    oid = host._create('sc.scout', position, changes=dict(hitpoints=750, damage=35))
    host.enemies.add_actor(oid)
    speed = math.hypot(*velocity)
    state = host.enemies.units[oid]
    state.update(active=True, phase='chase', target=None, target_point=None,
        scan=100., next=100., scout_phase='dive', dive_started=0., dive_cue=0.,
        dive_until=host.enemies.fit['scout_dive_s'], dive_retries=0,
        dive_heading=tuple(value/speed for value in velocity),
        dive_target=(position[0], position[1]+64*256),
        vx=velocity[0], vy=velocity[1], direction=-1 if velocity[0]<0 else 1)
    return oid, state


class ScScoutRecoveryFidelityTests(unittest.TestCase):
    def assert_wire_position(self, host, snapshot, sent, oid):
        actual = snapshot.position((1, oid))
        for value, expected in zip(actual, host.actors[oid]['pos']):
            self.assertAlmostEqual(value, expected, delta=1.)
        self.assertTrue(any(update['objid_'] == oid and 'movetoxmp_' in update
            for update in sent[-1]['worldupdate_']['objectupdates_']))

    def test_committed_dive_remains_airborne_after_two_seconds(self):
        host, snapshot, sent, _births = actor_host()
        oid, state = committed_scout(host, (0, 0), (-106000., 140000.))
        for _ in range(42):
            host.step(.05)
        self.assertGreater(host.room_time, 2.)
        self.assertEqual(state['scout_phase'], 'dive')
        self.assertLess(state['vx'], -100000.)
        self.assertGreater(state['vy'], 135000.)
        self.assert_wire_position(host, snapshot, sent, oid)
        for _ in range(20):
            host.step(.05)
        self.assertEqual(state['scout_phase'], 'recover', 'The safety bound still ends an unblocked dive')

    def test_native_floor_case_keeps_horizontal_motion_while_recovering_up(self):
        host, snapshot, sent, _births = actor_host()
        host._create('sc.wall', (237568, 278528), changes=dict(
            wmp=163840, hmp=8192, terraintype=1))
        # Oct23 first 17:1:13664:1, last clear point before its 2.279s landing.
        oid, state = committed_scout(host, (290862, 259900), (-106000., 140000.))
        host.step(.051)
        self.assertEqual(state['scout_phase'], 'recover')
        self.assertLess(host.room_time, host.enemies.fit['scout_dive_s'])
        self.assertAlmostEqual(host.actors[oid]['pos'][1]+16384, 278528, delta=1.)
        # Native full-slice endpoint. Normalized continuous speed differs
        # slightly from its quantized integer vector, by less than 32MP here.
        self.assertAlmostEqual(host.actors[oid]['pos'][0], 285456, delta=32.)
        self.assertAlmostEqual(host.actors[oid]['pos'][1], 262144, delta=1.)
        self.assertLess(state['vx'], -100000.)
        self.assertEqual(state['vy'], 0.)
        landed = tuple(host.actors[oid]['pos'])
        before = state['vx']
        host.step(.054)
        self.assertLess(host.actors[oid]['pos'][0], landed[0]-4000)
        self.assertLess(host.actors[oid]['pos'][1], landed[1])
        self.assertLess(state['vx'], 0.)
        self.assertGreater(state['vx'], before)
        self.assertLess(state['vy'], 0.)
        # Continuous recovery approximates the next native54ms endpoint.
        for value, expected in zip(host.actors[oid]['pos'], (279840, 262144)):
            self.assertAlmostEqual(value, expected, delta=3*256)
        self.assert_wire_position(host, snapshot, sent, oid)
        before = state['vx']
        host.step(.05)
        self.assertGreater(state['vx'], before)
        self.assertLess(state['vx'], 0.)
        self.assert_wire_position(host, snapshot, sent, oid)

    def test_remaining_floor_motion_is_clipped_by_a_side_wall(self):
        host, snapshot, sent, _births = actor_host()
        host._create('sc.wall', (237568, 278528), changes=dict(
            wmp=163840, hmp=8192, terraintype=1))
        wall_right = 286000
        host._create('sc.wall', (wall_right-8192, 100000), changes=dict(
            wmp=8192, hmp=178528, terraintype=1))
        oid, state = committed_scout(host, (290862, 259900), (-106000., 140000.))
        host.step(.051)
        self.assertEqual(state['scout_phase'], 'recover')
        self.assertGreaterEqual(host.actors[oid]['pos'][0], wall_right-1.)
        self.assertAlmostEqual(host.actors[oid]['pos'][1], 262144, delta=1.)
        self.assertEqual(state['vx'], 0.)
        self.assert_wire_position(host, snapshot, sent, oid)
        host.step(.05)
        self.assertGreaterEqual(host.actors[oid]['pos'][0], wall_right-1.)
        self.assert_wire_position(host, snapshot, sent, oid)

    def test_side_wall_still_stops_motion_instead_of_becoming_floor_inertia(self):
        host, snapshot, sent, _births = actor_host()
        wall_x = 80*256
        host._create('sc.wall', (wall_x, -500*256), changes=dict(
            wmp=32*256, hmp=2000*256, terraintype=1))
        oid, state = committed_scout(host, (0, 0), (120000., 120000.))
        host.step(.05)
        self.assertEqual(state['scout_phase'], 'recover')
        self.assertEqual(state['vx'], 0.)
        self.assertLessEqual(host.actors[oid]['pos'][0]+16384, wall_x+1)
        self.assert_wire_position(host, snapshot, sent, oid)
        host.step(.05)
        self.assertLessEqual(host.actors[oid]['pos'][0]+16384, wall_x+1)
        self.assert_wire_position(host, snapshot, sent, oid)


if __name__ == '__main__':
    unittest.main()
