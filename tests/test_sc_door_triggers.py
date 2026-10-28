"""Purple SC door triggers and shell-safe native arrival anchors."""
import copy
import unittest
from types import SimpleNamespace

from localserver.maps.efb_run.physics import swept_shape,surface
from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.doors import arrival, bind_triggers, overlaps
from localserver.maps.sc_run.layout import generate
from localserver.maps.sc_run.session import ScRunLiveSession
from localserver.pb_layout import default_layouts
from localserver.world_snapshot import WorldSnapshot


MP = 256


class ScDoorTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()
        cls.plans = [generate(cls.content, seed) for seed in (0, 3)]

    @staticmethod
    def ordinary(room):
        return [p for p in room['metadata']['ports'] if p['kind'] != 'core']

    def assert_clear(self, room, point, size):
        for p in self.ordinary(room):
            self.assertFalse(overlaps((*point, *size), room['objects'][p['trigger_id']]))
        for oid, row in room['objects'].items():
            if row.get('terraintype') not in (1, 2):
                continue
            bounds = tuple(row.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))
            self.assertIsNone(swept_shape(bounds, row.get('shapetype', 0), point,
                point, size, penetrating=True, tolerance=1.), 'Arrival intersects terrain '+str(oid))

    @staticmethod
    def fixture(*terrain):
        room = dict(objects={1: dict(xmp=0, ymp=0, wmp=160*MP, hmp=192*MP, vid='sc.door')},
                    entries={'door_1': [94*MP, 122*MP]},
                    metadata=dict(spawn_footprint_mp=[30*MP, 70*MP],
                        ports=[dict(id=1, side='left', kind='door', entry='door_1')]))
        for i, row in enumerate(terrain, 10):
            room['objects'][i] = dict(row)
        bind_triggers(room)
        return room

    def test_canonical_mid_door_triggers_are_outer_half_and_binding_is_idempotent(self):
        for template in ('sctask01', 'sctask03', 'sctask04'):
            room = self.content.recipe(template)
            for p in self.ordinary(room):
                door = room['objects'][p['id']]
                trigger = room['objects'][p['trigger_id']]
                self.assertEqual((trigger['wmp'], trigger['hmp']), (80*MP, 192*MP))
                self.assertEqual(trigger['xmp'], door['xmp'] if p['side']=='left' else door['xmp']+80*MP)
                self.assertEqual(trigger['ymp'], door['ymp'])
                self.assertEqual(trigger['trigger'], 255)
                self.assertIs(trigger['draw'], False)
                self.assertNotIn('treaction', trigger)
            saved = copy.deepcopy(room)
            bind_triggers(room)
            self.assertEqual(room, saved)

    def test_authored_markers_and_feet_survive_shell_resize_without_solid_overlap(self):
        for plan in self.plans:
            for role, room in plan['rooms'].items():
                width, height = room['metadata'].get('spawn_footprint_mp', (30*MP, 70*MP))
                for p in self.ordinary(room):
                    marker = room['entries'][p['entry']]
                    self.assertEqual(arrival(room, p['entry'], width, height), tuple(marker))
                    for w, h in ((30, 70), (40, 80), (48, 96), (64, 128)):
                        with self.subTest(seed=plan['seed'], role=role, door=p['id'], shell=(w,h)):
                            point = arrival(room, p['entry'], w*MP, h*MP)
                            self.assertEqual(point[1]+h*MP, marker[1]+height)
                            self.assert_clear(room, point, (w*MP, h*MP))

    def test_arrival_in_yellow_frame_is_outside_actual_snapshot_trigger(self):
        # Reproduce a lawful arrival within the decorative frame. The actual
        # session hook must use the separate purple object in the snapshot.
        room = self.plans[0]['rooms']['entry']
        p = self.ordinary(room)[0]
        document = dict(worldstate_=dict(serverworld_=dict(ownerid_=1, obj_=[
            dict(objid_=oid, props_={k+'_': row[k] for k in ('xmp','ymp','wmp','hmp')})
            for oid, row in room['objects'].items() if oid in (p['id'],p['trigger_id'])])))
        snapshot = WorldSnapshot(default_layouts(), document)
        wrapper = SimpleNamespace(instance=SimpleNamespace(snapshot=snapshot))
        native = ScRunLiveSession.boundary_properties(wrapper, p['id'], room['links'][p['id']])
        point = arrival(room, p['entry'], 40*MP, 80*MP)
        self.assertTrue(overlaps((*point, 40*MP, 80*MP), room['objects'][p['id']]))
        self.assertFalse(overlaps((*point, 40*MP, 80*MP), {k[:-1]: v for k,v in native.items()}))

    def test_resized_shell_moves_inward_past_solid_wall_and_keeps_floor_anchor(self):
        wall = dict(xmp=100*MP, ymp=64*MP, wmp=40*MP, hmp=128*MP, terraintype=1)
        floor = dict(xmp=-100*MP, ymp=192*MP, wmp=1000*MP, hmp=32*MP, terraintype=1)
        room = self.fixture(wall, floor)
        point = arrival(room, 'door_1', 64*MP, 128*MP)
        self.assertGreaterEqual(point[0], 140*MP)
        self.assertEqual(point[1]+128*MP, 192*MP)
        self.assert_clear(room, point, (64*MP, 128*MP))

    def test_platform_intersection_lands_on_its_top_instead_of_inside_it(self):
        platform = dict(xmp=80*MP, ymp=170*MP, wmp=150*MP, hmp=12*MP, terraintype=2)
        floor = dict(xmp=-100*MP, ymp=192*MP, wmp=1000*MP, hmp=32*MP, terraintype=1)
        room = self.fixture(platform, floor)
        point = arrival(room, 'door_1', 30*MP, 70*MP)
        self.assertEqual(point, (94*MP, 100*MP))
        self.assert_clear(room, point, (30*MP, 70*MP))

    def test_empty_half_of_slope_does_not_displace_native_marker(self):
        slope = dict(xmp=90*MP, ymp=120*MP, wmp=120*MP, hmp=120*MP,
                     terraintype=1, shapetype=1)
        room = self.fixture(slope)
        point = arrival(room, 'door_1', 30*MP, 70*MP)
        self.assertTrue(overlaps((*point, 30*MP, 70*MP), slope))
        self.assertEqual(point, (94*MP, 122*MP))
        self.assert_clear(room, point, (30*MP, 70*MP))

    def test_slope_landing_uses_surface_over_full_shell_width(self):
        slope = dict(xmp=90*MP, ymp=120*MP, wmp=120*MP, hmp=120*MP,
                     terraintype=1, shapetype=1)
        room = self.fixture(slope)
        room['entries']['door_1'] = [140*MP, 160*MP]
        point = arrival(room, 'door_1', 30*MP, 70*MP)
        self.assertEqual(point, (140*MP, 90*MP))
        self.assert_clear(room, point, (30*MP, 70*MP))

    def test_every_gen_return_uses_core_marker_feet_for_all_four_shells(self):
        from localserver.maps.sc_run.scene import ScRunPack
        from localserver.maps.sc_run.run import ScLevelRun
        from pathlib import Path
        import json
        characters=json.loads((Path(__file__).resolve().parents[1]/'data/catalogue/characters.json').read_text())
        pack=ScRunPack()
        for seed in (0,1,3,5,42,123,1234):
            run=ScLevelRun(pack,seed)
            for gen in range(4):
                room=run.room('gen_'+str(gen)).recipe
                for link in room['links'].values():
                    destination=run.room(link['destination']).recipe
                    for shell in ('1','2','3','5'):
                        body=characters[shell]['data'];size=(body['w']*MP,body['h']*MP)
                        with self.subTest(seed=seed,gen=gen,shell=shell):
                            point=ScRunLiveSession.entry_position(run,link['destination'],link['entry'],*size)
                            self.assert_clear(destination,point,size)
                            marker=destination['entries'][link['entry']]
                            width,height=destination['metadata']['spawn_footprint_mp']
                            self.assertEqual(point[0]+size[0]/2,marker[0]+width/2)
                            self.assertEqual(point[1]+size[1],marker[1]+height)
                            floors=[surface(tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp')),
                                p.get('shapetype',0),point[0],size[0]) for p in destination['objects'].values()
                                if p.get('terraintype')in(1,2) and p.get('boundsworld')is not False]
                            self.assertTrue(any(top is not None and abs(top-point[1]-size[1])<=1 for top in floors))


if __name__ == '__main__':
    unittest.main()
