"""Wall-emitted wraiths retain their native trajectory and damage ownership."""
import unittest

from test_sc_ghost_volley_fidelity import actor_host


class ScWraithNativeMotionTests(unittest.TestCase):
    def assert_synced(self, host, snapshot, oid):
        for actual, expected in zip(snapshot.position((1, oid)), host.actors[oid]['pos']):
            self.assertAlmostEqual(actual, expected, delta=1.)

    def test_birth_during_tick_keeps_sinusoid_and_same_wire_clock(self):
        host, snapshot, sent, _births = actor_host()
        host.enemies.emitters = [dict(next=.1, interval=(10., 10.), profile=dict(
            angle=69, sin=dict(vel=8000, period=750, amp=10000),
            births=[dict(pos_mp=[1270272, 96000], offset=6)]))]
        host.step(.05)
        host.step(.05)
        oid = next(iter(host.enemies.projectiles.shots))
        shot = host.enemies.projectiles.shots[oid]
        self.assertEqual(shot['born'], .05)
        for _ in range(20):
            self.assert_synced(host, snapshot, oid)
            props = snapshot.properties_at((1, oid))
            self.assertIn('sin_', props['move_'])
            self.assertEqual((props['hitpoints_'], props['damage_'], props['z_']), (75, 20, 61))
            host.step(.05)
        updates = [u for message in sent
                   for u in message['worldupdate_'].get('objectupdates_', [])
                   if u['objid_'] == oid]
        self.assertFalse(any('movetoxmp_' in u or u.get('modifyprops_', {}).get('property_') in (41, 42)
                             for u in updates), 'Do not stop or replace the native sinusoid')

    def test_native_trajectory_crosses_floor_without_disappearing(self):
        host, snapshot, _sent, _births = actor_host()
        # A native engine-room snapshot contains an alive wraith's centre
        # inside this intervening floor; terrain contact cannot retire it.
        host._create('sc.wall', (1236992, 155648), changes=dict(
            wmp=172032, hmp=8192, terraintype=1))
        oid = host.enemies.projectiles.spawn_sinusoid('sc.wraith', (1260288, 84736),
            dict(vel=8000, period=750, amp=10000, angle=69, offset=207), lifetime_s=7.5)
        inside = False
        for _ in range(70):
            host.step(.05)
            self.assertIn(oid, host.actors)
            self.assert_synced(host, snapshot, oid)
            cy = host.actors[oid]['pos'][1]+8192
            inside |= 155648 < cy < 163840
        self.assertTrue(inside)
        self.assertGreater(host.actors[oid]['pos'][1], 163840)

    def test_native_moving_wraith_can_still_be_killed(self):
        host, snapshot, sent, _births = actor_host()
        oid = host.enemies.projectiles.spawn_sinusoid('sc.wraith', (0, 0),
            dict(vel=8000, period=750, amp=10000, angle=38, offset=326), lifetime_s=7.5)
        host.step(.1)
        host.feedback(dict(kind='damage', victim=oid, value=25, seq=1))
        host.step(.05)
        self.assertEqual(snapshot.properties_at((1, oid))['hitpoints_'], 50)
        self.assert_synced(host, snapshot, oid)
        host.feedback(dict(kind='damage', victim=oid, value=50, seq=2))
        host.step(.05)
        self.assertNotIn(oid, host.actors)
        self.assertNotIn((1, oid), snapshot.objects)
        destroys = [u['destroy_'] for message in sent
                    for u in message['worldupdate_'].get('generalupdates_', [])
                    if 'destroy_' in u and u['destroy_']['objid_'] == oid]
        self.assertEqual(len(destroys), 1)
        self.assertEqual(destroys[0]['reason_'], 2)


if __name__ == '__main__':
    unittest.main()
