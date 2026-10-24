"""Fresh SC content and persistent runtime state have separate lifetimes."""
import copy
import unittest

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.scene import ScRunPack
from localserver.maps.sc_run.run import ScLevelRun
from localserver.maps.sc_run.session import ScRunLiveSession
import test_sc_run as sc_test


class ScFreshContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()

    def test_source_task_progress_does_not_enter_a_new_recipe(self):
        # The captured core room has one completed task from its source run.
        # Both public recipe representations must begin with all four empty.
        source = self.content._read('rooms/sccore.json')
        self.assertEqual(sum(p.get('vid') == 'sc.taskfull'
                             for p in source['objects'].values()), 1)
        recipe = self.content.recipe('sccore')
        self.assertEqual(sum(p.get('vid') == 'sc.taskempty'
                             for p in recipe['objects'].values()), 4)
        rows = recipe['world']['worldstate_']['serverworld_']['obj_']
        self.assertEqual(sum(row['props_'].get('vid_') == 'sc.taskempty' for row in rows), 4)
        self.assertFalse(any(row['props_'].get('vid_') == 'sc.taskfull' for row in rows))

    def test_native_manifest_births_keep_absent_hp_and_animation(self):
        expected = {'scend01': ('sc.titan', 4, 1650, 5),
                    'scend06': ('sc.bomber', 1, 858, 3),
                    'scmid06': ('sc.bomber', 1, 858, 6)}
        for name, (vid, anim, duration, count) in expected.items():
            with self.subTest(room=name):
                rows = [p for p in self.content.recipe(name)['objects'].values()
                        if p.get('vid') == vid]
                self.assertEqual(len(rows), count)
                for props in rows:
                    self.assertNotIn('hitpoints', props)
                    self.assertEqual(props['animprops'], dict(animid=anim, time=duration))

    def test_fresh_content_preserves_authored_moving_terrain(self):
        for name in ('scmid01', 'scend03', 'scend04', 'sctask02'):
            source = self.content._read('rooms/'+name+'.json')
            expected = {int(oid): copy.deepcopy(p['move']) for oid, p in source['objects'].items()
                        if p.get('terraintype') in (1, 2) and 'move' in p
                        and not p.get('vid', '').startswith('sc.mledge')}
            recipe = self.content.recipe(name)
            if name == 'scmid01':
                self.assertTrue(recipe['metadata']['sc_mledge_stream']['streams'])
                self.assertFalse(any(p.get('vid', '').startswith('sc.mledge') for p in recipe['objects'].values()))
                self.assertFalse(any(row['props_'].get('vid_', '').startswith('sc.mledge')
                                     for row in recipe['world']['worldstate_']['serverworld_']['obj_']))
            else:
                self.assertTrue(expected, name)
            self.assertEqual(expected, {oid: recipe['objects'][oid]['move'] for oid in expected})

    def test_new_run_has_four_undamaged_generators_and_no_completed_tasks(self):
        run = ScLevelRun(ScRunPack(), seed=1234, clock=sc_test.Clock())
        server = sc_test.Transport(run)
        self.assertEqual(run.gens, set())
        self.assertEqual(run.chests_rewarded, set())
        self.assertEqual(run.remaining(), 1440)
        for index in range(4):
            room = run.room('gen_'+str(index))
            ScRunLiveSession.initial_snapshot(server, run, room)
            self.assertFalse(room.flags)
            rows = [row['props'] for row in room.snapshot.objects.values()]
            self.assertEqual([p['hitpoints_'] for p in rows if p.get('vid_') == 'sc.core'], [5000])
            self.assertEqual(sum(p.get('vid_') == 'sc.taskempty' for p in rows), 4)
            self.assertFalse(any(p.get('vid_') == 'sc.taskfull' for p in rows))

    def test_damage_and_jump_completion_survive_visits_but_not_a_new_run(self):
        helper = sc_test.ScRunTests()
        run, clock = helper.make_run()
        wrapper, server = helper.open_room(run, 'engine')
        core = next(oid for oid, row in wrapper.current.actors.items() if row['vid'] == 'sc.enginecore')
        wrapper.current.feedback(dict(kind='damage', victim=core, value=1000, seq=21))
        wrapper.step(.02)
        self.assertEqual(wrapper.current.actors[core]['hp'], 59000)
        jump, _ = helper.open_room(run, 'mid_jump', server)
        switch = next(oid for oid, row in jump.current.actors.items() if row['vid'] == 'sc.switch')
        row = jump.current.actors[switch]
        player = dict(xmp=row['pos'][0], ymp=row['pos'][1], wmp=32*256, hmp=64*256, alive=True)
        jump.current.mechanics.interact(switch, player)
        jump.step(.02)
        clock.advance(121)
        revisited, _ = helper.open_room(run, 'engine', server)
        self.assertIs(revisited.current, wrapper.current)
        self.assertEqual(revisited.current.actors[core]['hp'], 59000)
        self.assertTrue(run.room('mid_jump').flags['mid_cleared'])
        fresh, _ = helper.make_run()
        fresh_engine, _ = helper.open_room(fresh, 'engine')
        fresh_core = next(row for row in fresh_engine.current.actors.values() if row['vid'] == 'sc.enginecore')
        self.assertEqual(fresh_core['hp'], 60000)
        self.assertFalse(fresh.room('mid_jump').flags)

    def test_ordinary_death_refills_once_after_120_seconds_across_visits(self):
        helper = sc_test.ScRunTests()
        run, clock = helper.make_run()
        room, server = helper.open_room(run, 'statue')
        runtime = room.current
        tank = next(oid for oid, row in runtime.actors.items() if row['vid'] == 'sc.tank')
        initial = sum(row['vid'] == 'sc.tank' for row in runtime.actors.values())
        runtime.feedback(dict(kind='damage', victim=tank, value=3000, seq=22))
        room.step(.02)
        self.assertNotIn(tank, runtime.actors)
        helper.open_room(run, 'entry', server)
        clock.advance(119)
        helper.open_room(run, 'statue', server)
        self.assertEqual(sum(row['vid'] == 'sc.tank' for row in runtime.actors.values()), initial-1)
        helper.open_room(run, 'entry', server)
        clock.advance(1.1)
        returned, _ = helper.open_room(run, 'statue', server)
        self.assertIs(returned.current, runtime)
        self.assertEqual(sum(row['vid'] == 'sc.tank' for row in runtime.actors.values()), initial)
        self.assertFalse(runtime.enemies.respawns)
        helper.open_room(run, 'entry', server)
        clock.advance(1)
        helper.open_room(run, 'statue', server)
        self.assertEqual(sum(row['vid'] == 'sc.tank' for row in runtime.actors.values()), initial)


if __name__ == '__main__':
    unittest.main()
