"""Engine hit gating, distinct native formations and geometry persistence."""
import math
import unittest

from test_sc_mechanics import Host, body
from localserver.maps.sc_boss.motion import NativeMotion
from localserver.maps.sc_run.mechanic_helpers import MechanismTrack
from localserver.world_snapshot import WorldSnapshot


class ScEngineTests(unittest.TestCase):
    def host(self, **config):
        defaults = dict(engine_round_pause_s=(.2, .2), engine_burst_interval_s=(.1, .1),
                        engine_seed_flight_s=(100., 100.))
        defaults.update(config)
        return Host({1: body('sc.enginecore', w=512, h=416, hitpoints=60000,
                            terraintype=1, damage=15)}, dict(mechanics=defaults))

    def core_shots(self, h):
        return {oid: row for oid, row in h.actors.items()
                if row['vid'] == 'sc.enginefire' and row.get('creator') == 1}

    def rounds(self, h):
        return [e['mode'] for e in h.events if e['kind'] == 'sc_engine_round']

    def test_proximity_or_zero_damage_never_starts_an_engine_round(self):
        h = self.host()
        h.player_at(250, 200)
        h.tick(20.)
        h.damage(1, 0, 1)
        h.tick(.05)
        self.assertEqual(self.rounds(h), [])
        self.assertEqual(len(h.actors), 1)

    def test_summon_is_hit_driven_but_has_no_two_batch_opening_or_lifetime_cap(self):
        h = self.host(engine_round_pause_s=(5.12, 5.12), engine_attack_modes=['summon'])
        h.damage(1, 1, 1)
        h.damage(1, 1, 1)  # duplicate feedback is not another real hit
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['summon'])
        self.assertEqual(h.actors[1]['hp'], 59999)
        h.damage(1, 100, 2)
        h.tick(5.13)
        self.assertEqual(self.rounds(h), ['summon'])
        h.damage(1, 1, 3)
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['summon', 'summon'])
        self.assertEqual(sum(r['vid'] == 'sc.enginefireseed' for r in h.actors.values()), 6)
        h.tick(5.13)
        self.assertEqual(self.rounds(h), ['summon', 'summon'])
        h.damage(1, 1, 4)
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['summon']*3)
        self.assertEqual(sum(r['vid'] == 'sc.enginefireseed' for r in h.actors.values()), 9)

    def test_committed_spiral_round_completes_three_bursts_after_shooting_stops(self):
        h = self.host(engine_attack_modes=['spiral'])
        h.damage(1, 1, 1)
        h.tick(.01)
        self.assertEqual(len(self.core_shots(h)), 10)
        h.tick(.1)
        second = list(self.core_shots(h).values())[10:]
        self.assertEqual(len(second), 10)
        self.assertTrue(all(r['props']['move']['elliptical']['rbvel'] == -70 for r in second))
        h.tick(.1)
        self.assertEqual(len(self.core_shots(h)), 30)
        h.tick(10.)
        self.assertEqual(self.rounds(h), ['spiral'])
        self.assertEqual(len(self.core_shots(h)), 30)
        h.damage(1, 1, 2)
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['spiral', 'spiral'])

    def test_wave_ring_keeps_native_space_wavelength_and_round_orientation(self):
        h = self.host(engine_attack_modes=['wave'])
        h.damage(1, 1, 1)
        h.tick(.01)
        first = [r['props']['move']['sin'] for r in self.core_shots(h).values()]
        self.assertEqual(len(first), 10)
        self.assertTrue(all(m['vel'] == 20000 and m['period'] == 400 and m['amp'] == 20480 for m in first))
        h.tick(.1)
        second = [r['props']['move']['sin'] for r in self.core_shots(h).values()][10:]
        self.assertEqual([m['angle'] for m in first], [m['angle'] for m in second])
        h.batch.encode()

    def test_chain_round_has_six_heads_and_nine_real_followers_per_head(self):
        h = self.host(engine_attack_modes=['chains'])
        h.damage(1, 40000, 1)
        h.tick(.01)
        shots = self.core_shots(h)
        self.assertEqual(len(shots), 60)
        heads = [oid for oid, r in shots.items() if 'elliptical' in r['props']['move']]
        followers = [oid for oid in shots if oid not in heads]
        self.assertEqual(len(heads), 6)
        for oid in followers:
            movement = shots[oid]['props']['move']
            self.assertEqual(movement['follow']['maxdist'], 50)
            self.assertEqual(movement['relativeto']['objid'], oid-1)
            self.assertNotIn(oid, h.mechanics.tracks)
        for _ in range(20):
            h.tick(.05)
        for oid in followers:
            row = shots[oid]
            parent = shots[row['props']['move']['relativeto']['objid']]
            self.assertLessEqual(math.dist(row['pos'], parent['pos']), 51*256)
        self.assertTrue(any(math.dist(shots[oid]['pos'], (256*256, 208*256)) > 30000 for oid in followers))
        h.batch.encode()

    def test_a_full_health_engine_can_start_with_three_chains_before_any_summon(self):
        h = self.host(engine_attack_modes=['chains'])
        h.damage(1, 1, 1)
        h.tick(.01)
        shots = self.core_shots(h)
        self.assertEqual(self.rounds(h), ['chains'])
        self.assertEqual(len(shots), 30)
        heads = [r['props']['move']['elliptical'] for r in shots.values() if 'elliptical' in r['props']['move']]
        self.assertEqual(len(heads), 3)
        self.assertTrue(all(m['rbvel'] == 70 for m in heads))
        self.assertFalse(any(r['vid'] == 'sc.enginefireseed' for r in h.actors.values()))

    def test_resume_preserves_opening_progress_without_replaying_a_round(self):
        h = self.host(engine_attack_modes=['summon'])
        h.damage(1, 1, 1)
        h.tick(.01)
        h.room_time += 120.
        h.mechanics.resume(120.)
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['summon'])
        self.assertEqual(h.mechanics.states[1]['engine']['summons'], 1)
        h.damage(1, 1, 2)
        h.tick(.01)
        self.assertEqual(self.rounds(h), ['summon', 'summon'])

    def test_straight_bursts_use_separate_unblocked_sprite_and_alternating_angles(self):
        h = self.host(engine_attack_modes=['straight'], engine_straight_interval_s=(.775, .775))
        h.damage(1, 1, 1)
        h.tick(.01)
        groups = []
        for index in range(5):
            shots = [r for r in h.actors.values() if r['vid'] == 'sc.enginefireub']
            self.assertEqual(len(shots), (index+1)*10)
            group = shots[-10:]
            groups.append(group)
            self.assertTrue(all(r['props']['removetype'] == 0 and r['props']['rotate'] for r in group))
            if index < 4:
                h.tick(.775)
        self.assertEqual([g[0]['props']['facing'] for g in groups][::2],
                         [groups[0][0]['props']['facing']]*3)
        self.assertEqual((groups[1][0]['props']['facing']-groups[0][0]['props']['facing']) % 360, 18)
        for row in groups[0]:
            movement = row['props']['move']['vel']
            radians = math.radians(row['props']['facing'])
            self.assertEqual(movement['xvel'], int(20000*math.cos(radians)))
            self.assertEqual(movement['yvel'], int(20000*math.sin(radians)))
        h.tick(10.)
        self.assertEqual(self.rounds(h), ['straight'])
        h.batch.encode()

    def test_lethal_hit_never_starts_an_extra_summon_or_attack(self):
        h = self.host()
        h.damage(1, 60000, 1)
        h.tick(20.)
        self.assertEqual(h.actors[1]['vid'], 'sc.denginecore')
        self.assertEqual(self.rounds(h), [])


class ScEngineNativeMotionTests(unittest.TestCase):
    def model(self, rbvel=70):
        return dict(xmp=335872, ymp=61440, radiusamp=0, radiusbmp=0,
                    ravel=70, rbvel=rbvel, period=12000, angle=0, pos=8693)

    def test_native_expanding_radius_uses_birth_age_and_independent_angular_phase(self):
        m = self.model()
        track = MechanismTrack((331776, 57344), dict(elliptical=m), 0)
        point = track.position(125)
        # Original native client sample is (335038,52732), radius8747.85 MP.
        self.assertAlmostEqual(point[0], 335038, delta=3.)
        self.assertAlmostEqual(point[1], 52732, delta=3.)
        self.assertAlmostEqual(math.dist(point, (335872, 61440)), 8750.)
        self.assertEqual(track.position(0), (335872., 61440.))

    def test_snapshot_rebase_keeps_both_growing_radii_and_phase(self):
        h = Host({1: body('sc.enginefire', x=1296, y=224, w=32, h=32,
                          lifetime=4500, move=dict(elliptical=self.model(-70)))})
        snapshot = WorldSnapshot(h.layouts, dict(worldstate_=dict(serverworld_=dict(ownerid_=1,
            obj_=[dict(objid_=1, props_=h.normalize('Properties', h.actors[1]['props']))]))))
        snapshot.advance(.5)
        expected = snapshot.position((1, 1))
        props = snapshot.properties_at((1, 1))
        self.assertEqual(props['move_']['elliptical_']['radiusamp_'], 35000)
        self.assertEqual(props['move_']['elliptical_']['radiusbmp_'], -35000)
        restored = WorldSnapshot(h.layouts, dict(worldstate_=dict(serverworld_=dict(ownerid_=1,
            obj_=[dict(objid_=1, props_=props)]))))
        for a, b in zip(restored.position((1, 1)), expected):
            self.assertAlmostEqual(a, b)
        snapshot.advance(.25)
        restored.advance(.25)
        for a, b in zip(restored.position((1, 1)), snapshot.position((1, 1))):
            self.assertAlmostEqual(a, b)

    def test_room_server_and_collision_mirror_agree_for_real_six_chain_wire(self):
        import test_sc_run as fixtures
        fixture = fixtures.ScRunTests()
        run, _clock = fixture.make_run(seed=23)
        wrapper, _server = fixture.open_room(run, 'engine')
        runtime = wrapper.current
        runtime.mechanics.config.update(engine_attack_modes=['chains'])
        core = next(oid for oid, row in runtime.actors.items() if row['vid'] == 'sc.enginecore')
        runtime.feedback(dict(kind='damage', victim=core, value=40000, seq=700))
        for _ in range(12):
            wrapper.step(.05)
        shots = {oid: row for oid, row in runtime.actors.items()
                 if row['vid'] == 'sc.enginefire' and row.get('creator') == core}
        self.assertEqual(len(shots), 60)
        for oid, row in shots.items():
            native = wrapper.instance.snapshot.position((1, oid))
            for actual, expected in zip(row['pos'], native):
                self.assertAlmostEqual(actual, expected, delta=1., msg='Native chain and server collision positions diverged')

    def test_straight_native_constant_velocity_shifts_each_axis_before_time(self):
        model = NativeMotion(331776, 57344)
        model.move = dict(vel=dict(xvel=19405, yvel=4838))
        self.assertEqual(model.position(2.327), (506301., 99230.))
        model.move = dict(vel=dict(xvel=-19405, yvel=-4838))
        self.assertEqual(model.position(2.327), (331776-76*2327., 57344-19*2327.))

    def test_unblocked_fire_has_fresh_lifetime_and_is_cleared_on_room_return(self):
        import test_sc_run as fixtures
        from localserver.maps.sc_run.catalogue import ScContent
        content = ScContent()
        self.assertEqual(content.props('sc.enginefireub')['lifetime'], 4500)
        self.assertTrue(all(p['lifetime'] == 4500 for p in content.props_variants('sc.enginefireub')))
        fixture = fixtures.ScRunTests()
        run, clock = fixture.make_run(seed=23)
        wrapper, server = fixture.open_room(run, 'engine')
        runtime = wrapper.current
        runtime.mechanics.config['engine_attack_modes'] = ['straight']
        core = next(oid for oid, r in runtime.actors.items() if r['vid'] == 'sc.enginecore')
        runtime.feedback(dict(kind='damage', victim=core, value=1, seq=701))
        wrapper.step(.05)
        shots = {oid for oid, r in runtime.actors.items() if r['vid'] == 'sc.enginefireub'}
        self.assertEqual(len(shots), 10)
        sample = next(iter(shots))
        runtime.feedback(dict(kind='destroy', objid=sample, reason=1, seq=702))
        self.assertIn(sample, runtime.actors, 'The unblockable variant must not retire as an ordinary wall-blocked shot')
        fixture.open_room(run, 'entry', server)
        clock.advance(.2)
        fixture.open_room(run, 'engine', server)
        self.assertFalse(shots & set(runtime.actors))
        self.assertTrue(all((1, oid) not in run.room('engine').snapshot.objects for oid in shots))


if __name__ == '__main__':
    unittest.main()
