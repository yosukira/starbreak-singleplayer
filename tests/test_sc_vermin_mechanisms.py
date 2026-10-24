"""Regression examples for SC's moving terrain mechanisms.

The host is already an active room; preloaded rooms do not invoke this step.
"""
import copy
import unittest
from types import SimpleNamespace

from localserver.maps.hulk_mid.scene import normalize
from localserver.maps.run_common.actors import ActorSession
from localserver.maps.sc_run.enemies import ScEnemies
from localserver.maps.sc_run.mechanics import ScMechanics
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.maps.sc_run.scene import ScRunPack
from localserver.pb_layout import default_layouts
from localserver.world_snapshot import WorldSnapshot, decode_delta

from test_sc_mechanics import Host, MP, body


class ScMeatballMechanismTests(unittest.TestCase):
    @staticmethod
    def resident_host(objects, creators=None):
        layouts, pack = default_layouts(), ScRunPack()
        document = dict(worldid_=2, worldprops_=dict(seed_=7), worldstate_=dict(
            serverworld_=dict(ownerid_=1, obj_=[
                dict(objid_=oid, props_=normalize('Properties', props, layouts))
                for oid, props in objects.items()]), clientworld_=[]))
        snapshot = WorldSnapshot(layouts, document)
        recipe = dict(metadata={}, creators=creators or {}, objects=copy.deepcopy(objects),
                      world=document)
        instance = SimpleNamespace(seed=7, role='meatball', recipe=recipe,
                                   snapshot=snapshot, flags={})

        def send(_channel, kind, payload, label='', record=False):
            if kind == 12:
                snapshot.apply(decode_delta(layouts, payload))

        server = SimpleNamespace(layouts=layouts, _send_raw=send)
        bridge = SimpleNamespace(snapshot=lambda: (None, {}), poll_events=lambda: [])
        return ActorSession(server, None, SimpleNamespace(), instance, bridge, pack,
            map_id='sc_run', enemies_factory=ScEnemies, mechanics_factory=ScMechanics,
            presentation_factory=ScPresentation, players={})

    def test_long_lived_child_starts_full_replacement_wait_only_after_its_death(self):
        for event in ('damage', 'destroy'):
            with self.subTest(event=event):
                h = self.resident_host({1: body('sc.tshooter', w=128, h=128,
                                               damage=30, terraintype=1, facing=90)})
                h.step(.01)
                h.step(.416)
                state = h.mechanics.states[1]
                first = state['child']
                self.assertIsNone(state['next'])
                h.step(20.)
                self.assertEqual(state['child'], first)
                death = h.room_time
                h.feedback(dict(kind=event, victim=first, value=1000,
                                reason='HITPOINTS', seq=1))
                self.assertNotIn(first, h.actors)
                self.assertGreaterEqual(state['next']-death, 5.)
                self.assertLessEqual(state['next']-death, 6.)
                self.assertEqual(state['child_lost_at'], death)
                h.step(state['next']-h.room_time-.001)
                self.assertEqual(state['phase'], 'idle')
                self.assertNotIn((1, first), h.instance.snapshot.objects)
                h.step(.0011)
                self.assertEqual(state['phase'], 'windup')
                self.assertIsNone(state['child'])
                h.step(.415)
                self.assertIsNone(state['child'])
                h.step(.0011)
                replacement = state['child']
                self.assertNotEqual(replacement, first)
                self.assertIn((1, replacement), h.instance.snapshot.objects)
                self.assertEqual(h.actors[replacement]['creator'], 1)
                self.assertIsNone(state['next'])

    def test_retirement_and_observed_host_removal_do_not_reuse_birth_deadline(self):
        for direct_host in (False, True):
            with self.subTest(direct_host=direct_host):
                h = Host({1: body('sc.tshooter', w=128, h=128, facing=90)})
                h.tick(.01)
                h.tick(.416)
                h.tick(20.)
                state = h.mechanics.states[1]
                child = state['child']
                if direct_host:
                    h._retire(child, 'HITPOINTS')
                    h.tick(.05)
                else:
                    h.mechanics.retire(child, 'HITPOINTS')
                deadline = state['next']
                self.assertGreaterEqual(deadline-h.room_time, 5.)
                h.tick(.2)
                h.mechanics.retire(child, 'HITPOINTS')
                self.assertEqual(state['next'], deadline)
                self.assertEqual(state['phase'], 'idle')

    def test_resident_child_binds_explicit_creator_in_either_snapshot_order(self):
        nest = body('sc.tshooter', w=128, h=128, facing=90)
        ball = body('sc.tshot', y=256, w=128, h=128, facing=90, hitpoints=1000)
        for objects in ({2: ball, 1: nest}, {1: nest, 2: ball}):
            with self.subTest(order=list(objects)):
                h = self.resident_host(objects, {'2': 1})
                state = h.mechanics.states[1]
                self.assertEqual(state['child'], 2)
                self.assertEqual(h.mechanics.states[2]['owner'], 1)
                self.assertIsNone(state['next'])
                h.step(12.)
                h.room_time += 30.
                h.instance.snapshot.advance(30.)
                h.mechanics.resume(30.)
                self.assertEqual(state['child'], 2)
                self.assertIsNone(state['next'])
                self.assertEqual(sum(r['vid'] == 'sc.tshot' for r in h.actors.values()), 1)
                h.feedback(dict(kind='damage', victim=2, value=1000, seq=2))
                self.assertGreaterEqual(state['next']-h.room_time, 5.)

        unknown = self.resident_host({2: ball, 1: nest})
        self.assertIsNone(unknown.mechanics.states[1]['child'])
        self.assertNotIn('owner', unknown.mechanics.states[2])

    def test_absence_ages_death_deadline_without_restarting_or_catchup_birth(self):
        h = Host({1: body('sc.tshooter', w=128, h=128, facing=90)})
        h.tick(.01)
        h.tick(.416)
        state = h.mechanics.states[1]
        h.mechanics.retire(state['child'], 'HITPOINTS')
        deadline = state['next']
        h.room_time += 2.
        h.mechanics.resume(2.)
        self.assertEqual(state['next'], deadline)
        self.assertEqual(state['phase'], 'idle')
        h.room_time += 20.
        h.mechanics.resume(20.)
        self.assertEqual(state['next'], deadline)
        self.assertIsNone(state['child'])
        h.tick(.01)
        self.assertEqual(state['phase'], 'windup')
        self.assertIsNone(state['child'])
        h.tick(.416)
        self.assertIsNotNone(state['child'])

    def test_permanent_nest_ignores_damage_and_destroy_without_losing_its_child(self):
        h = Host({1: body('sc.tshooter', w=128, h=128, facing=90, hitpoints=100)})
        h.tick(.01)
        h.tick(.416)
        state = h.mechanics.states[1]
        child = state['child']
        h.damage(1, 1000, 1)
        h.mechanics.feedback(dict(kind='destroy', objid=1, reason='HITPOINTS', seq=2))
        self.assertIn(1, h.actors)
        self.assertIn(child, h.actors)
        self.assertEqual(state['child'], child)
        self.assertIsNone(state['next'])

    def test_active_room_starts_far_nests_together_without_player_range_gate(self):
        for with_player in (False, True):
            with self.subTest(with_player=with_player):
                # Fresh native scmid03 coordinates, including a nest whose
                # centre is >4400px from the recorded player's centre.
                points = [(2624, 768), (576, -768), (2176, 960), (-192, -704),
                          (-448, -704), (1216, -576), (-960, -704), (2368, 256),
                          (1472, -320), (1728, -64), (2752, 1216), (1216, 960),
                          (320, 640), (64, 640), (1472, 2240), (2112, 1536),
                          (-192, 320), (1600, 2432), (-448, 256), (2496, 2496)]
                h = Host({oid: body('sc.tshooter', x=x, y=y, w=128, h=128, facing=90)
                          for oid, (x, y) in enumerate(points, 1)})
                if with_player:
                    h.player_at(3229, 944)
                h.tick(.01)
                self.assertEqual([h.mechanics.states[i]['phase'] for i in range(1, 21)], ['windup']*20)
                h.tick(.416)
                children = [r for r in h.actors.values() if r['vid'] == 'sc.tshot']
                self.assertEqual(len(children), 20)
                self.assertEqual({r['creator'] for r in children}, set(range(1, 21)))
                for _ in range(20):
                    h.tick(.1)
                self.assertEqual(sum(r['vid'] == 'sc.tshot' for r in h.actors.values()), 20)

    def test_meatball_accelerates_from_rest_at_native_sample_rate(self):
        h = Host({1: body('sc.tshot', w=128, h=128, facing=0)})
        h.tick(.5)
        # Native samples after birth follow roughly .5*300*t^2 px.
        self.assertAlmostEqual(h.actors[1]['pos'][0]/MP, 37.5)
        self.assertAlmostEqual(h.mechanics.states[1]['speed']/MP, 150.)
        self.assertEqual(h.actors[1]['props']['pose'], 2)

    def test_meatball_reaches_observed_speed_without_frame_rate_drift(self):
        positions = []
        for step in (.02, .1):
            h = Host({1: body('sc.tshot', w=128, h=128, facing=90)})
            for _ in range(round(2/step)):
                h.tick(step)
            positions.append(h.actors[1]['pos'][1])
            self.assertAlmostEqual(h.mechanics.states[1]['speed'], 79000.)
            self.assertEqual(h.actors[1]['props']['pose'], 3)
        self.assertLessEqual(abs(positions[0]-positions[1]), 2)
        maximum = 79000/MP
        expected = maximum*2-maximum**2/(2*300)
        self.assertAlmostEqual(positions[0]/MP, expected, delta=.01)

    def test_meatball_stops_at_corridor_end_for_observed_pause(self):
        h = Host({1: body('sc.tshot', w=128, h=128, facing=0),
                  2: body('sc.twall', x=400, y=-200, w=32, h=600, terraintype=1)})
        for _ in range(100):
            h.tick(.05)
            if h.mechanics.states[1].get('wait_until', 0) > h.room_time:
                break
        state = h.mechanics.states[1]
        self.assertEqual(state['speed'], 0.)
        self.assertIsNone(state['heading'])
        self.assertAlmostEqual(h.actors[1]['pos'][0]/MP, 272., delta=.01)
        self.assertEqual(h.actors[1]['props']['pose'], 1)
        stopped = tuple(h.actors[1]['pos'])
        h.tick(.9)
        self.assertEqual(tuple(h.actors[1]['pos']), stopped)


if __name__ == '__main__':
    unittest.main()
