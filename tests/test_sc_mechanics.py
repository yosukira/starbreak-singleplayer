import copy
import random
import unittest
from types import SimpleNamespace

from localserver.live.wire import DeltaBatch
from localserver.maps.hulk_mid.scene import normalize
from localserver.maps.sc_run.mechanics import ScMechanics, WORKERS, BOOKS
from localserver.maps.sc_run.mechanic_helpers import overlaps
from localserver.world_snapshot import WorldSnapshot


MP = 256


def body(vid, x=0, y=0, w=64, h=64, **props):
    return dict(vid=vid, xmp=x*MP, ymp=y*MP, wmp=w*MP, hmp=h*MP, **props)


class Host:
    def __init__(self, objects, metadata=None):
        self.batch = DeltaBatch()
        self.layouts = self.batch.layouts
        self.actors = {oid: self.row(oid, copy.deepcopy(p)) for oid, p in objects.items()}
        self.room_time = 0.
        self.recipe = dict(metadata=metadata or {})
        self.instance = SimpleNamespace(flags={})
        self.mechanics_config = {}
        self.rng = random.Random(9)
        self.retired, self.events, self._lifetimes = set(), [], {}
        self.players, self.player = {}, None
        self._next_id = 10000
        templates = {
            'sc.libworker': body('sc.libworker', w=64, h=96, hitpoints=200, damage=20),
            'sc.libworkerb': body('sc.libworkerb', w=64, h=96, hitpoints=200, damage=20),
            'sc.libworkerr': body('sc.libworkerr', w=64, h=96, hitpoints=200, damage=20),
            'sc.enginespawner': body('sc.enginespawner', ownertype=2),
            'sc.enginefireseed': body('sc.enginefireseed', w=16, h=16, lifetime=5000),
            'sc.enginefire': body('sc.enginefire', w=32, h=32, lifetime=4500, damage=15),
            'sc.tshot': body('sc.tshot', w=128, h=128, hitpoints=1000, damage=30, blocktype=2, terraintype=1),
            'sc.vermin': body('sc.vermin', w=48, h=48, hitpoints=100, damage=20),
            'sc.mledge64': body('sc.mledge64', w=64, h=96, terraintype=2),
        }
        self.pack = SimpleNamespace(props=lambda vid: copy.deepcopy(templates[vid]))
        self.mechanics = ScMechanics(self)
        self.mechanics.prepare()

    @staticmethod
    def row(oid, props):
        return dict(id=oid, objid=oid, props=props, vid=props['vid'],
                    pos=[props.get('xmp', 0), props.get('ymp', 0)], vel=[0., 0.],
                    hp=props.get('hitpoints'), dead=False)

    def normalize(self, typ, props):
        return normalize(typ, props, self.layouts)

    def _offset(self):
        return 0

    def _create(self, vid, point, creator=0, changes=None):
        props = self.pack.props(vid)
        props.update(copy.deepcopy(changes or {}))
        props.update(vid=vid, xmp=round(point[0]), ymp=round(point[1]))
        self._next_id += 1
        oid = self._next_id
        self.actors[oid] = self.row(oid, props)
        self.actors[oid]['creator'] = creator
        self.batch.create(oid, self.normalize('Properties', props), creatorid=creator)
        return oid

    def _retire(self, oid, reason='INTERNAL', native=False):
        row = self.actors.pop(oid, None)
        if row:
            row['dead'] = True
        self.retired.add(oid)
        self.batch.destroy(oid, ownerid=1, reason=reason)

    def unlock(self, key, **evidence):
        if key in self.instance.flags:
            return False
        self.instance.flags[key] = True
        self.record('unlock', key=key, **evidence)
        return True

    def record(self, kind, **values):
        self.events.append(dict(kind=kind, **values))

    def tick(self, dt):
        self.room_time += dt
        self.mechanics.step(dt)

    def damage(self, oid, value, seq):
        self.mechanics.feedback(dict(kind='damage', victim=oid, value=value, seq=seq))

    def player_at(self, x, y):
        self.player = dict(xmp=x*MP, ymp=y*MP, wmp=24*MP, hmp=64*MP, alive=True)
        self.players = {1: self.player}
        return self.player


class ScMechanicsTests(unittest.TestCase):
    @staticmethod
    def snapshot(host):
        rows = [dict(objid_=oid, props_=host.normalize('Properties', row['props']))
                for oid, row in host.actors.items()]
        result = WorldSnapshot(host.layouts, dict(worldid_=2, worldprops_=dict(seed_=1),
            worldstate_=dict(serverworld_=dict(ownerid_=1, obj_=rows))))
        host.batch.clear()
        return result

    @staticmethod
    def commit(host, snapshot, dt=0.):
        host.batch.dt = round(dt*1000)
        snapshot.apply(host.batch.message())
        host.batch.clear()

    def test_wall_accumulates_lethal_damage_before_minimum_existence_gate(self):
        h = Host({1: body('sc.dropwall', y=-544, w=64, h=544, terraintype=1, removetype=0),
                  2: body('sc.wall', x=-100, y=512, w=400, h=32, terraintype=1)})
        h.player_at(0, 200)
        h.tick(.01)
        h.damage(1, 5000, 1)
        self.assertIn(1, h.actors)
        self.assertEqual(h.actors[1]['hp'], 0)
        for _ in range(150):
            h.tick(.01)
        s = h.mechanics.states[1]
        self.assertEqual(s['phase'], 'settled')
        h.room_time = s['open_at']-.001
        h.mechanics.step(0.)
        self.assertIn(1, h.actors)
        h.tick(.002)
        self.assertNotIn(1, h.actors)

    def test_wall_waiting_gate_alone_does_not_destroy_and_duplicate_damage_is_ignored(self):
        h = Host({1: body('sc.dropwall', hitpoints=5000, terraintype=1, removetype=0)})
        h.damage(1, 4999, 1)
        h.damage(1, 4999, 1)
        h.tick(8.)
        self.assertEqual(h.actors[1]['hp'], 1)
        self.assertEqual(h.actors[1]['props']['removetype'], 1)
        h.damage(1, 1, 2)
        self.assertNotIn(1, h.actors)

    def test_jump_requires_interaction_opens_gen_immediately_and_preserves_damage(self):
        h = Host({1: body('sc.switch', x=100, y=-1000, interact=1),
                  2: body('sc.ccoredoor', x=100),
                  3: body('sc.evilpeg32', damage=20, terraintype=1,
                          move=dict(vel=dict(xvel=1000))),
                  4: body('sc.wheel128', facing=50, damage=20, terraintype=1)})
        h.player_at(100, -1000)
        h.tick(.1)
        self.assertNotIn('mid_cleared', h.instance.flags)
        self.assertTrue(h.mechanics.interact(1, h.player))
        self.assertTrue(h.instance.flags['mid_cleared'])
        self.assertEqual(h.actors[2]['props']['interact'], 1)
        self.assertEqual(h.actors[2]['vid'], 'sc.coredoor')
        self.assertNotIn('move', h.actors[3]['props'])
        self.assertEqual(h.actors[3]['props']['damage'], 20)
        facing = h.actors[4]['props']['facing']
        frozen = list(h.actors[3]['pos'])
        h.tick(1.)
        self.assertEqual(h.actors[4]['props']['facing'], facing)
        h.room_time += 120.
        h.mechanics.resume(120.)
        h.tick(.1)
        self.assertEqual(h.actors[3]['pos'], frozen)
        self.assertEqual(h.actors[3]['props']['terraintype'], 1)
        self.assertEqual(h.actors[3]['props']['damage'], 20)
        self.assertEqual(h.actors[4]['props']['facing'], facing)

    def test_switch_native_reaction_has_no_duplicate_wire_sound_and_custom_switch_has_fallback(self):
        for reaction, expected in ((dict(sound='sc.switch'), []), (None, ['sc.switch'])):
            with self.subTest(reaction=reaction):
                switch = body('sc.switch', interact=1)
                if reaction is not None:
                    switch['interactreaction'] = reaction
                h = Host({1: switch, 2: body('sc.ccoredoor')})
                player = h.player_at(0, 0)
                self.assertTrue(h.mechanics.interact(1, player))
                sounds = [u['playsound_']['soundname_'] for u in h.batch.objects if 'playsound_' in u]
                self.assertEqual(sounds, expected)
                self.assertTrue(h.instance.flags['mid_cleared'])
                self.assertEqual(h.actors[1]['vid'], 'sc.switchdone')
                self.assertEqual(h.actors[1]['props']['interact'], 0)
                self.assertEqual(h.actors[2]['vid'], 'sc.coredoor')
                self.assertEqual(h.actors[2]['props']['interact'], 1)
                h.batch.encode()
    def test_engine_is_a_60000hp_solid_and_death_preserves_existing_fire_devices(self):
        h = Host({1: body('sc.enginecore', w=512, h=416, hitpoints=60000, terraintype=1, damage=15),
                  2: body('sc.engineball', x=700, damage=15, terraintype=1),
                  3: body('sc.enginespawner', x=900, hitpoints=300, damage=15)})
        h.damage(1, 59999, 1)
        self.assertEqual(h.actors[1]['props']['terraintype'], 1)
        h.damage(1, 1, 2)
        self.assertEqual(h.actors[1]['vid'], 'sc.denginecore')
        self.assertEqual(h.actors[1]['props']['terraintype'], 0)
        self.assertNotIn('hitpoints', h.actors[1]['props'])
        self.assertNotIn('damage', h.actors[1]['props'])
        self.assertNotIn(2, h.actors)
        self.assertIn(3, h.actors)
        h.tick(1.1)
        self.assertTrue(any(r['vid'] == 'sc.enginefire' for r in h.actors.values()))
        self.assertNotIn('gen_destroyed', h.instance.flags)
        h.batch.encode()  # Property-clearing, native MOVE and sound messages encode.

    def test_gen_death_is_independent_persistent_and_only_unlocks_once(self):
        h = Host({1: body('sc.core', hitpoints=5000, removetype=0)})
        h.damage(1, 5000, 1)
        h.mechanics.feedback(dict(kind='destroy', objid=1, reason=2, seq=2))
        self.assertEqual(h.actors[1]['vid'], 'sc.dcore')
        self.assertNotIn('hitpoints', h.actors[1]['props'])
        self.assertEqual(len([e for e in h.events if e.get('key') == 'gen_destroyed']), 1)
        self.assertEqual(h.actors[1]['props']['animprops']['time'], 2970)

    def test_killing_carrier_places_its_standable_book_and_replaces_only_that_worker(self):
        h = Host({1: body('sc.libworkerb', w=64, h=96, hitpoints=200),
                  2: body('sc.book', x=16, y=64, w=32, h=32, terraintype=2, draw=False,
                          move=dict(relativeto=dict(objid=1), to=dict(xmp=16*MP, ymp=64*MP))),
                  3: body('sc.wall', x=16, y=64, w=32, h=32, terraintype=1)})
        h.damage(1, 200, 1)
        self.assertNotIn(1, h.actors)
        workers = [r for r in h.actors.values() if r['vid'] in WORKERS]
        self.assertEqual(len(workers), 1)
        self.assertEqual(workers[0]['hp'], 200)
        self.assertEqual(len([r for r in h.actors.values() if r['vid'] in BOOKS]), 1)
        self.assertTrue(h.actors[2]['props']['draw'])
        self.assertEqual(h.actors[2]['props']['terraintype'], 2)
        self.assertNotIn('move', h.actors[2]['props'])
        self.assertNotEqual(h.actors[2]['pos'], [16*MP, 64*MP])

    def test_tshooter_native_windup_precedes_permanent_collidable_tshot(self):
        h = Host({1: body('sc.tshooter', w=128, h=128, damage=30, terraintype=1, facing=90)},
                 dict(mechanics=dict(tshooter_initial_s=0.)))
        h.player_at(300, 0)
        h.tick(.01)
        self.assertFalse(any(r['vid'] == 'sc.tshot' for r in h.actors.values()))
        h.tick(.400)
        self.assertFalse(any(r['vid'] == 'sc.tshot' for r in h.actors.values()))
        h.tick(.017)
        shot = next(r for r in h.actors.values() if r['vid'] == 'sc.tshot')
        self.assertEqual(shot['hp'], 1000)
        self.assertEqual(shot['props']['terraintype'], 1)
        self.assertEqual(shot['props']['blocktype'], 2)
        self.assertEqual(shot['props']['lifetime'], 0)

    def test_tshot_can_travel_tangent_to_a_floor(self):
        h = Host({1: body('sc.tshot', w=128, h=128, hitpoints=1000,
                          damage=30, blocktype=2, terraintype=1, facing=0),
                  2: body('sc.wall', x=-100, y=128, w=1000, h=32, terraintype=1)})
        h.tick(.1)
        self.assertGreater(h.actors[1]['pos'][0], 0)
        self.assertEqual(h.actors[1]['pos'][1], 0)

    def test_tshooter_cancelled_absent_windup_does_not_leave_a_firing_animation(self):
        h = Host({1: body('sc.tshooter', w=128, h=128, damage=30, terraintype=1, facing=90)},
                 dict(mechanics=dict(tshooter_initial_s=0.)))
        h.player_at(300, 0)
        snapshot = self.snapshot(h)
        h.tick(.01)
        self.commit(h, snapshot, .01)
        self.assertIn('animprops', h.actors[1]['props'])
        snapshot.advance(.2)
        h.room_time += .2
        h.mechanics.resume(.2)
        self.commit(h, snapshot)
        self.assertNotIn('animprops', h.actors[1]['props'])
        self.assertNotIn('animprops_', snapshot.properties_at((1, 1)))
        h.tick(.3)
        self.assertFalse(any(r['vid'] == 'sc.tshot' for r in h.actors.values()))

    def test_chest_guardian_uses_explicit_kill_instead_of_absence_or_other_enemies(self):
        h = Host({7: body('sc.egolem', hitpoints=5000), 8: body('sc.tank', hitpoints=50)},
                 dict(chest_guardian=7))
        h._retire(8, 'HITPOINTS')
        h._retire(7, 'LIFETIME')
        h.tick(.1)
        self.assertNotIn('chest_cleared', h.instance.flags)
        h.mechanics.guardian_defeated(8)
        self.assertNotIn('chest_cleared', h.instance.flags)
        h.mechanics.guardian_defeated(7)
        self.assertTrue(h.instance.flags['chest_cleared'])

    def test_resume_ages_existing_wall_gate_without_catching_up_fire(self):
        h = Host({1: body('sc.dropwall', hitpoints=5000, removetype=0, terraintype=1),
                  2: body('sc.enginespawner', x=900, hitpoints=300, damage=15)})
        h.damage(1, 5000, 1)
        h.room_time = 120.
        h.mechanics.resume(120.)
        self.assertNotIn(1, h.actors)
        self.assertFalse(any(r['vid'] == 'sc.enginefire' for r in h.actors.values()))
        h.tick(.1)
        self.assertFalse(any(r['vid'] == 'sc.enginefire' for r in h.actors.values()))

    def test_wall_settling_and_absent_fall_commit_actual_floor_position(self):
        for leave_during_fall in (False, True):
            with self.subTest(leave_during_fall=leave_during_fall):
                h = Host({1: body('sc.dropwall', w=64, h=64, terraintype=1, removetype=0),
                          2: body('sc.wall', x=-100, y=576, w=400, h=32, terraintype=1)})
                snapshot = self.snapshot(h)
                h.player_at(0, 200)
                h.tick(.05)
                self.commit(h, snapshot, .05)
                h.damage(1, 4999, 1)
                self.commit(h, snapshot)
                if leave_during_fall:
                    snapshot.advance(10.)
                    h.room_time += 10.
                    h.mechanics.resume(10.)
                    self.commit(h, snapshot)
                else:
                    for _ in range(30):
                        h.tick(.05)
                        self.commit(h, snapshot, .05)
                self.assertEqual(h.mechanics.states[1]['phase'], 'settled')
                self.assertEqual(h.actors[1]['pos'][1], 512*MP)
                self.assertEqual(snapshot.position((1, 1))[1], 512*MP)
                self.assertEqual(h.actors[1]['hp'], 1)
                if leave_during_fall:
                    self.assertEqual(h.actors[1]['props']['removetype'], 1)
                    h.damage(1, 1, 2)
                    self.assertNotIn(1, h.actors)

    def test_worker_existing_native_leg_resumes_once_and_keeps_book_standable(self):
        h = Host({1: body('sc.libworkerb', w=64, h=96, hitpoints=200,
                          move=dict(to=dict(xmp=100*MP, ymp=0, dt=1000))),
                  2: body('sc.book', x=16, y=64, w=32, h=32, terraintype=2, draw=False,
                          move=dict(relativeto=dict(objid=1), to=dict(xmp=16*MP, ymp=64*MP)))})
        snapshot = self.snapshot(h)
        h.tick(.25)
        self.commit(h, snapshot, .25)
        self.assertEqual(h.actors[1]['pos'][0], 25*MP)
        snapshot.advance(.25)
        h.room_time += .25
        h.mechanics.resume(.25)
        self.commit(h, snapshot)
        self.assertEqual(h.actors[1]['pos'][0], 50*MP)
        self.assertEqual(snapshot.position((1, 1))[0], 50*MP)
        self.assertEqual(h.actors[2]['pos'][0], 66*MP)
        self.assertEqual(snapshot.position((1, 2))[0], 66*MP)
        snapshot.advance(2.)
        h.room_time += 2.
        h.mechanics.resume(2.)
        self.commit(h, snapshot)
        self.assertEqual(h.actors[1]['pos'][0], 100*MP)
        self.assertIsNone(h.mechanics.states[2]['carrier'])
        self.assertTrue(h.actors[2]['props']['draw'])
        self.assertEqual(h.actors[2]['props']['terraintype'], 2)
        self.assertEqual(snapshot.position((1, 2)), tuple(h.actors[2]['pos']))
        self.assertEqual(sum(r['vid'] in WORKERS for r in h.actors.values()), 1)
        self.assertEqual(sum(r['vid'] in BOOKS for r in h.actors.values()), 1)

    def test_killed_worker_cannot_orphan_a_book_when_the_ordinary_search_radius_is_full(self):
        h = Host({1: body('sc.libworkerb', w=64, h=96, hitpoints=200),
                  2: body('sc.book', x=16, y=64, w=32, h=32, terraintype=2, draw=False,
                          move=dict(relativeto=dict(objid=1), to=dict(xmp=16*MP, ymp=64*MP))),
                  3: body('sc.wall', x=-800, y=-800, w=1600, h=1600, terraintype=1)},
                 dict(mechanics=dict(worker_place_search_cells=1)))
        h.damage(1, 200, 1)
        self.assertIsNone(h.mechanics.states[2]['carrier'])
        self.assertNotIn('move', h.actors[2]['props'])
        self.assertEqual(h.actors[2]['props']['terraintype'], 2)
        block = (*h.actors[2]['pos'], h.actors[2]['props']['wmp'], h.actors[2]['props']['hmp'])
        wall = (*h.actors[3]['pos'], h.actors[3]['props']['wmp'], h.actors[3]['props']['hmp'])
        self.assertFalse(overlaps(block, wall))
        self.assertEqual(sum(r['vid'] in WORKERS for r in h.actors.values()), 1)

    def test_mledge_native_velocity_survives_stream_out_replacement(self):
        h = Host({1: body('sc.mledge64', w=64, h=96, terraintype=2, move=dict(vel=dict(xvel=7168))),
                  2: body('sc.mledge64', y=200, w=64, h=96, terraintype=2, move=dict(vel=dict(xvel=-12032)))},
                 dict(sc_mledge_bounds=dict(left=-10000, right=10000)))
        h.tick(.05)
        # Original observed endpoint deltas: +1400 / -2350 mp in 50ms.
        self.assertEqual(h.actors[1]['pos'][0], 1400)
        self.assertEqual(h.actors[2]['pos'][0], -2350)
        h.tick(.5)
        self.assertNotIn(1, h.actors)
        self.assertEqual(len(h.actors), 2)
        replacement = next(row for oid, row in h.actors.items() if oid not in (1, 2))
        self.assertEqual(replacement['props']['move']['vel']['xvel'], 7168)
        self.assertLess(replacement['pos'][0], -10000)


if __name__ == '__main__':
    unittest.main()
