"""SC projectile regressions through the actual actor host and wire schema."""
import copy
import unittest
from types import SimpleNamespace

from localserver.live.wire import DeltaBatch
from localserver.maps.run_common.actors import ActorSession
from localserver.maps.sc_run.enemies import ScEnemies
from localserver.maps.sc_run.presentation import ScPresentation


class IdleMechanics:
    def __init__(self, host):
        pass

    def prepare(self):
        pass

    def step(self, dt):
        pass

    def feedback(self, event):
        return False


class ScProjectileRegressionTests(unittest.TestCase):
    def host(self):
        templates = {
            '': {},
            'sc.wraith': dict(wmp=16384, hmp=16384, lifetime=30000,
                              ownertype=2, damage=20, hitpoints=75),
            'sc.monkb': dict(wmp=6144, hmp=6144, invulnerable=True),
            'sc.tank': dict(wmp=32768, hmp=32768, hitpoints=3000),
            'sc.aim': dict(wmp=16384, hmp=12288, lifetime=2000),
            'sc.aimend': dict(wmp=16384, hmp=12288, lifetime=2000),
        }
        layouts = DeltaBatch().layouts
        sent = []
        server = SimpleNamespace(layouts=layouts,
            _send_raw=lambda channel, kind, payload, label='', record=False: sent.append(payload))
        instance = SimpleNamespace(seed=7, recipe=dict(metadata={}, creators={}),
                                   snapshot=SimpleNamespace(objects={}), flags={})
        bridge = SimpleNamespace(snapshot=lambda: (None, {}), poll_events=lambda: [])
        pack = SimpleNamespace(props=lambda vid: dict(vid=vid, **copy.deepcopy(templates[vid])))
        host = ActorSession(server, None, SimpleNamespace(), instance, bridge, pack,
                            map_id='sc_run', enemies_factory=ScEnemies,
                            mechanics_factory=IdleMechanics, presentation_factory=ScPresentation)
        return host, sent

    def test_laser_warning_has_no_damage_then_creates_native_unnamed_volumes(self):
        host, _ = self.host()
        parent = host._create('sc.tank', (0, 0))
        host.enemies._laser(parent, (300000, 16384))
        visible = [r for r in host.actors.values() if r['vid'] in ('sc.aim', 'sc.aimend')]
        centres = [(r['pos'][0]+r['props']['wmp']/2, r['pos'][1]+r['props']['hmp']/2) for r in visible]
        host.step(.99)
        self.assertFalse([r for r in host.actors.values() if r['vid'] == ''])
        host.step(.02)
        damage = [r for r in host.actors.values() if r['vid'] == '']
        self.assertEqual(len(damage), len(visible))
        self.assertEqual([(r['pos'][0]+2048, r['pos'][1]+2048) for r in damage], centres)
        for r in damage:
            self.assertEqual(r['props']['damage'], 35)
            self.assertEqual(r['props']['lifetime'], 990)
            self.assertFalse(r['props']['draw'])
            self.assertEqual((r['props']['wmp'], r['props']['hmp']), (4096, 4096))
            self.assertNotIn('ownertype', r['props'])
        host.step(1.01)
        self.assertFalse([r for r in host.actors.values() if r['vid'] in ('', 'sc.aim', 'sc.aimend')])

    def test_laser_does_not_create_delayed_damage_after_parent_dies(self):
        host, _ = self.host()
        parent = host._create('sc.tank', (0, 0))
        host.enemies._laser(parent, (300000, 16384))
        host._retire(parent, 'HITPOINTS')
        host.step(1.1)
        self.assertFalse([r for r in host.actors.values() if r['vid'] == ''])

    def test_ground_wave_expires_at_a_gap_instead_of_flying_over_it(self):
        from test_sc_enemies import Host
        h = Host()
        h.add(91, 'sc.wall', (0, 256*256), changes=dict(wmp=200*256, hmp=32*256, terraintype=1))
        h.add(92, 'sc.wall', (300*256, 256*256), changes=dict(wmp=200*256, hmp=32*256, terraintype=1))
        shot = h.enemies.projectiles.spawn('sc.golemw', (64*256, 192*256), (148500., 0.), 0)
        h.advance(.1)
        self.assertIn(shot, h.actors)
        h.advance(.2)
        self.assertNotIn(shot, h.actors)
        self.assertEqual(h.retired[shot], 'INTERNAL')

    def test_wraith_can_leave_source_wall_but_expires_at_the_next_wall(self):
        from test_sc_enemies import Host
        h = Host()
        h.add(91, 'sc.wall', (-64*256, -128*256), changes=dict(wmp=128*256, hmp=512*256, terraintype=1))
        h.add(92, 'sc.wall', (220*256, -128*256), changes=dict(wmp=64*256, hmp=512*256, terraintype=1))
        oid = h.enemies.projectiles.spawn_sinusoid('sc.wraith', (0, 0),
            dict(vel=8000, period=750, amp=0, angle=0, offset=0), lifetime_s=7.5)
        h.advance(.1)
        self.assertIn(oid, h.actors)
        h.advance(3.)
        self.assertNotIn(oid, h.actors)
        self.assertEqual(h.retired[oid], 'INTERNAL')

    @staticmethod
    def spawn_wraith(host, cleanup=7.5):
        return host.enemies.projectiles.spawn_sinusoid('sc.wraith', (1035008, 260864),
            dict(vel=8000, period=750, amp=10000, angle=141, offset=82), lifetime_s=cleanup)

    @staticmethod
    def destroys(host, payload):
        world = host.layouts.decode('Delta', payload)['worldupdate_']
        return [row['destroy_'] for row in world.get('generalupdates_', []) if 'destroy_' in row]

    def test_wraith_local_cleanup_overrides_a_not_yet_due_wire_lifetime(self):
        host, sent = self.host()
        oid = self.spawn_wraith(host)
        self.assertEqual(host.actors[oid]['props']['lifetime'], 30000)
        self.assertEqual(host.wire_lifetime_deadline(oid), 30000)
        host.step(7.49)
        self.assertIn(oid, host.actors)
        self.assertIs(host.wire_lifetime_due(oid), False)
        host.step(.02)
        self.assertNotIn(oid, host.actors)
        self.assertEqual(self.destroys(host, sent[-1]),
                         [dict(ownerid_=1, objid_=oid, reason_=1)])

    def test_wraith_authored_deadline_still_uses_lifetime_destruction(self):
        host, sent = self.host()
        oid = self.spawn_wraith(host, cleanup=None)
        host.step(29.99)
        self.assertIn(oid, host.actors)
        host.step(.02)
        self.assertNotIn(oid, host.actors)
        self.assertEqual(self.destroys(host, sent[-1]),
                         [dict(ownerid_=1, objid_=oid, reason_=6)])

    def test_room_resume_applies_local_wraith_cleanup_without_expiry_cue(self):
        host, _sent = self.host()
        oid = self.spawn_wraith(host)
        host.batch.clear()
        host.room_time = 7.6
        host.wire_end_ms = 7600
        host.enemies.resume(7.6)
        self.assertNotIn(oid, host.actors)
        self.assertEqual(self.destroys(host, host.batch.encode()),
                         [dict(ownerid_=1, objid_=oid, reason_=1)])

    def test_monk_wire_velocity_shifts_before_integration_in_both_directions(self):
        host, _sent = self.host()
        oid = host._create('sc.monkb', (0, 0), changes=dict(
            move=dict(vel=dict(xvel=7700, yvel=-7700))))
        host.enemies.projectiles.adopt(oid)
        host.step(.125)
        # Native values become +30 and -31 mp/ms before elapsed time applies.
        self.assertEqual(host.actors[oid]['pos'], [3750., -3875.])

    def test_monk_release_converts_the_native_20000_wire_magnitude(self):
        host, _sent = self.host()
        parent = host._create('sc.tank', (0, 0))
        oid = host._create('sc.monkb', (0, 0), creator=parent)
        host.enemies.projectiles.adopt(oid, owner=parent, orbit=0.)
        host.enemies._emit(dict(owner=parent, kind='monk_release', child=oid,
                                angle=0., last=True))
        host.step(.05)
        self.assertEqual(host.actors[oid]['pos'], [3900., 0.])

    def test_weapon_beam_create_callbacks_emit_one_flash_per_plain_segment(self):
        host, _sent = self.host()
        tank = host._create('sc.tank', (0, 0))
        host.enemies._laser(tank, (300000, 16384))
        world = host.layouts.decode('Delta', host.batch.encode())['worldupdate_']
        animations = [row['objid_'] for row in world.get('objectupdates_', [])
                      if row.get('modifyprops_', {}).get('property_') == 49]
        plain = [oid for oid, row in host.actors.items() if row['vid'] == 'sc.aim']
        ends = [oid for oid, row in host.actors.items() if row['vid'] == 'sc.aimend']
        self.assertTrue(plain)
        self.assertEqual(len(ends), 1)
        self.assertEqual(sorted(animations), sorted(plain))


if __name__ == '__main__':
    unittest.main()
