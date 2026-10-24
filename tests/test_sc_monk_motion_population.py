"""Shared monk motion, continuous entry and native-slot hall population."""
import copy
import math
import unittest

from localserver.maps.dumb_common.scene import normalize
from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.generator import geometry_sha
from localserver.maps.sc_run.monks import position
from localserver.maps.sc_run.physics import clear
from test_sc_enemies import Host
import test_sc_run as live


class MonkMotionTests(unittest.TestCase):
    def host(self, home=(0, 0), metadata=None):
        host = Host(metadata)
        host.add(1, 'sc.monk', home)
        host.enemies.prepare()
        host.enemies.units[1]['active'] = True
        return host

    def expected(self, host):
        unit = host.enemies.units[1]
        return position(unit['home'], host.room_time-unit['monk_motion_epoch'], unit['monk_motion_heading'])

    def test_continuous_activation_and_translated_rule_in_every_room(self):
        for descriptor, home in (('scmid04', (0, 0)), ('scmid09', (2624*256, -256*256)),
                                 ('scmid18', (-4512*256, 832*256))):
            with self.subTest(descriptor=descriptor):
                host = self.host(home, dict(template=descriptor))
                host.advance(.05)
                self.assertLessEqual(math.dist(home, host.actors[1]['pos']), 11*256)
                epoch = host.enemies.units[1]['monk_motion_epoch']
                for _ in range(400):
                    host.advance(.05)
                    self.assertEqual(tuple(host.actors[1]['pos']), self.expected(host))
                    self.assertLessEqual(math.dist(home, host.actors[1]['pos']), 212*256)
                self.assertEqual(host.enemies.units[1]['monk_motion_epoch'], epoch)
                self.assertNotIn('patrol_goal', host.enemies.units[1])

    def test_absent_room_preserves_phase_and_commits_position_before_orbits(self):
        host = self.host()
        host.advance(2.)
        unit = host.enemies.units[1]
        epoch, heading = unit['monk_motion_epoch'], unit['monk_motion_heading']
        old = tuple(host.actors[1]['pos'])
        host.room_time += 5.31
        host.enemies.resume(5.31)
        self.assertNotEqual(tuple(host.actors[1]['pos']), old)
        self.assertEqual(tuple(host.actors[1]['pos']), self.expected(host))
        self.assertEqual((unit['monk_motion_epoch'], unit['monk_motion_heading']), (epoch, heading))
        self.assertEqual(host.actors[1]['pos'], [host.actors[1]['props'][key] for key in ('xmp', 'ymp')])
        for oid in unit['orbs']:
            model = host.enemies.projectiles.shots[oid]['orbit_model']
            relative = [host.actors[oid]['pos'][i]-host.actors[1]['pos'][i]-model[key]
                        for i, key in enumerate(('xmp', 'ymp'))]
            self.assertAlmostEqual(math.hypot(*relative), model['radiusamp'], delta=1.)

    def test_move_to_uses_slice_endpoint_duration_and_offset(self):
        host = self.host()
        host._in_step, host._slice_ms = True, 50
        host._offset = lambda: 137
        host.advance(.05)
        rows = [row for row in host.batch.objects if row['objid_'] == 1 and 'movetodt_' in row]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['offset_'], 137)
        self.assertEqual(rows[0]['movetodt_'], 50)
        self.assertEqual((rows[0]['movetoxmp_'], rows[0]['movetoymp_']), self.expected(host))

    def test_real_room_reentry_snapshot_commits_moving_parent(self):
        helper = live.ScRunTests()
        run, clock = helper.make_run()
        role = next(role for role in run.pack.room_names
                    if run.room(role).recipe['metadata']['template'] == 'scmid04')
        wrapper, server = helper.open_room(run, role)
        host = wrapper.current
        oid, unit = next((oid, u) for oid, u in host.enemies.units.items() if u['vid'] == 'sc.monk')
        unit['active'] = True
        wrapper.step(.05)
        self.assertLessEqual(math.dist(host.actors[oid]['pos'], unit['home']), 11*256)
        helper.open_room(run, 'entry', server)
        clock.advance(3.27)
        run.current, run.entry = role, 'default'
        live.ScRunLiveSession.initial_snapshot(server, run, run.room(role))
        expected = position(unit['home'], host.room_time-unit['monk_motion_epoch'], unit['monk_motion_heading'])
        self.assertEqual(tuple(host.actors[oid]['pos']), expected)
        self.assertEqual(tuple(run.room(role).snapshot.position((1, oid))), expected)
        self.assertTrue(clear(host, (*expected, 64*256, 96*256), (oid,)))


class MonkPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()
        cls.raw = ScContent(generate=False)

    def test_seeded_native_population_varies_without_filling_or_moving_geometry(self):
        counts, layouts = set(), set()
        for seed in range(60):
            room = self.content.recipe('scmid04', seed)
            original = self.raw.recipe('scmid04', seed)
            population = room['metadata']['sc_monk_population']
            bodies = {oid: p for oid, p in room['objects'].items() if p.get('vid') == 'sc.monk'}
            self.assertIn(len(bodies), (5, 6, 7, 8))
            self.assertLess(len(bodies), population['legal_columns'])
            self.assertEqual(len({p['xmp'] for p in bodies.values()}), len(bodies))
            self.assertEqual(geometry_sha(room['objects']), geometry_sha(original['objects']))
            self.assertEqual(room['entries'], original['entries'])
            self.assertEqual(room, self.content.recipe('scmid04', seed))
            counts.add(len(bodies))
            layouts.add(tuple(sorted((p['xmp'], p['ymp']) for p in bodies.values())))
            wire = {row['objid_']: row['props_'] for row in room['world']['worldstate_']['serverworld_']['obj_']}
            self.assertEqual(set(wire), set(room['objects']))
            for oid, props in bodies.items():
                point = props['xmp'], props['ymp']
                self.assertIn(point, self.content.monk_population.slots)
                self.assertEqual(wire[oid], normalize('Properties', props))
                self.assertIn(point, self.content.monk_population.legal_slots(room)[point[0]])
        self.assertEqual(counts, {5, 6, 7, 8})
        self.assertGreater(len(layouts), 40)

    def test_each_native_variant_retains_an_unoccupied_safe_column(self):
        for room in self.raw.templates['scmid04']:
            for seed in (4, 16, 27):
                result = self.content.monk_population.populate(copy.deepcopy(room), seed)
                meta = result['metadata']['sc_monk_population']
                self.assertLess(meta['count'], meta['legal_columns'])
                # The whole analytic envelope, not just the birth rectangle,
                # was checked. Ledges are one-way and do not obstruct flight.
                h = Host()
                for oid, props in result['objects'].items():
                    h.add(oid, props.get('vid', ''), (props.get('xmp', 0), props.get('ymp', 0)), changes=props)
                for oid, row in h.actors.items():
                    if row['vid'] == 'sc.monk':
                        bounds = (row['pos'][0]-210*256, row['pos'][1]-210*256, (64+420)*256, (96+420)*256)
                        self.assertTrue(clear(h, bounds, (oid,)))

    def test_original_fixtures_and_nonhall_monk_population_are_unchanged(self):
        self.assertNotIn('sc_monk_population', self.raw.recipe('scmid04', 0)['metadata'])
        for descriptor in ('scmid09', 'scmid13', 'scmid14', 'scmid16', 'scmid18'):
            room = self.content.recipe(descriptor, 7)
            self.assertNotIn('sc_monk_population', room['metadata'])


if __name__ == '__main__':
    unittest.main()
