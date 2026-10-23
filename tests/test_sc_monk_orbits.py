"""Refill staggering, native orbit properties and room-clock persistence."""
import math
import unittest

from test_sc_enemies import Host
import test_sc_run as sc_run_test


class MonkOrbitTests(unittest.TestCase):
    def host(self):
        h = Host()
        h.add(1, 'sc.monk', (0, 0))
        h.enemies.prepare()
        h.enemies.units[1]['active'] = True
        return h

    def test_staggered_refill_makes_eight_distinct_equally_spaced_orbits(self):
        h = self.host()
        h.advance(2.)
        orbs = h.enemies.units[1]['orbs']
        self.assertEqual(len(orbs), 8)
        parent = h.actors[1]['pos']
        angles = []
        for oid in orbs:
            x, y = h.actors[oid]['pos']
            dx, dy = x-parent[0]-5120, y-parent[1]-9216
            self.assertAlmostEqual(math.hypot(dx, dy), 20480, places=5)
            angles.append(math.atan2(dy, dx) % (2*math.pi))
            self.assertEqual(h.enemies.projectiles.shots[oid]['orbit_model']['pos'], 1000)
        angles.sort()
        gaps = [(angles[(i+1) % 8]-angles[i]) % (2*math.pi) for i in range(8)]
        for gap in gaps:
            self.assertAlmostEqual(gap, math.pi/4, places=5)

    def test_captured_phase_advances_at_its_own_period_relative_to_parent(self):
        h = Host()
        h.add(1, 'sc.monk', (500*256, 300*256))
        h.add(2, 'sc.monkb', (0, 0), changes=dict(move=dict(relativeto=dict(objid=1),
            elliptical=dict(xmp=5120, ymp=9216, radiusamp=20480, period=2000, pos=700))))
        h.enemies.prepare()
        h.advance(.1)
        phase = 2*math.pi*800/2000
        self.assertAlmostEqual(h.actors[2]['pos'][0], 500*256+5120+20480*math.cos(phase))
        self.assertAlmostEqual(h.actors[2]['pos'][1], 300*256+9216+20480*math.sin(phase))

    def test_reentry_phase_is_committed_before_first_tick(self):
        h = self.host()
        h.advance(2.)
        oid = h.enemies.units[1]['orbs'][0]
        parent = list(h.actors[1]['pos'])
        old = [h.actors[oid]['pos'][0]-parent[0]-5120, h.actors[oid]['pos'][1]-parent[1]-9216]
        h.room_time += .5
        h.enemies.resume(.5)
        point = h.actors[oid]['pos']
        self.assertAlmostEqual(point[0], parent[0]+5120-old[1], delta=1)
        self.assertAlmostEqual(point[1], parent[1]+9216+old[0], delta=1)
        # Resume writes authoritative coordinates, rather than a one-frame
        # interpolation which CreateWorld would still serialize at the old point.
        self.assertEqual(point, [h.actors[oid]['props']['xmp'], h.actors[oid]['props']['ymp']])

    def test_wire_birth_phase_and_reentry_snapshot_use_world_coordinates_once(self):
        helper = sc_run_test.ScRunTests()
        run, clock = helper.make_run()
        role = next(role for role in run.pack.room_names
                    if run.room(role).recipe['metadata']['template'] == 'scmid04')
        wrapper, server = helper.open_room(run, role)
        runtime = wrapper.current
        monk = next(oid for oid, unit in runtime.enemies.units.items() if unit['vid'] == 'sc.monk')
        runtime.enemies.units[monk].update(active=True, refill_at=runtime.room_time)
        existing = set(runtime.enemies.projectiles.shots)
        server.sent.clear()
        wrapper.step(.05)
        oid = next(oid for oid in set(runtime.enemies.projectiles.shots)-existing
                   if runtime.actors[oid]['vid'] == 'sc.monkb')
        row = runtime.actors[oid]
        shot = runtime.enemies.projectiles.shots[oid]
        self.assertEqual(shot['born'], row['_wire_born_ms']/1000)
        self.assertAlmostEqual(runtime.room_time-shot['born'], .05)

        def expected():
            model = shot['orbit_model']
            phase = 2*math.pi*((runtime.room_time-shot['born'])*1000+model['pos'])/model['period']
            parent = runtime.actors[monk]['pos']
            return (parent[0]+model['xmp']+model['radiusamp']*math.cos(phase),
                    parent[1]+model['ymp']+model['radiusamp']*math.sin(phase))

        for actual, value in zip(row['pos'], expected()):
            self.assertAlmostEqual(actual, value)
        props = wrapper.instance.snapshot.properties_at((1, oid))
        self.assertNotIn('relativeto_', props.get('move_', {}))
        self.assertEqual(props['animprops_']['pos_'], 50)
        for actual, value in zip(wrapper.instance.snapshot.position((1, oid)), expected()):
            self.assertAlmostEqual(actual, value, delta=1)

        updates = []
        for delta in server.deltas():
            world = delta.get('worldupdate_', {})
            if any(r.get('create_', {}).get('objid_') == oid
                   for r in world.get('generalupdates_', [])):
                updates.extend(r for r in world.get('objectupdates_', []) if r.get('objid_') == oid)
        movement = next(r['modifyprops_'] for r in updates if r.get('modifyprops_', {}).get('property_') == 41)
        import base64
        stopped = server.layouts.decode('Properties_Move', base64.b64decode(movement['messageval_']['raw']))
        self.assertEqual(stopped, dict(vel_=dict(xvel_=0, yvel_=0)))

        helper.open_room(run, 'entry', server)
        clock.advance(.5)
        run.current, run.entry = role, 'default'
        # Check the CreateWorld reconciliation itself, before another tick can
        # hide an old position with a new interpolation.
        sc_run_test.ScRunLiveSession.initial_snapshot(server, run, run.room(role))
        for actual, value in zip(runtime.actors[oid]['pos'], expected()):
            self.assertAlmostEqual(actual, value, delta=1)
        for actual, value in zip(run.room(role).snapshot.position((1, oid)), expected()):
            self.assertAlmostEqual(actual, value, delta=1)
        props = run.room(role).snapshot.properties_at((1, oid))
        self.assertNotIn('relativeto_', props.get('move_', {}))


if __name__ == '__main__':
    unittest.main()
