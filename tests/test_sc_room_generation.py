import copy
import json
from pathlib import Path
import unittest

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.generator import (ScRoomGenerator, collar, geometry_sha,
    validate, cloud_points, refs)
from localserver.maps.eub_run.generator import relocate
from localserver.maps.dumb_common.scene import normalize
from localserver import pb_layout
from test_sc_enemies import Host as EnemyHost


class ScRoomGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent(generate=False)
        cls.generator = ScRoomGenerator(cls.content.root)

    def base(self, descriptor):
        return copy.deepcopy(self.content.templates[descriptor][0])

    def test_supported_rooms_have_real_seeded_geometry_and_original_branches(self):
        expected = {'scmid%02d'%i for i in (7,8,9,10,12,13,14,15,16)}
        self.assertEqual(set(self.generator.data['recipes']), expected)
        for descriptor in sorted(expected):
            source = self.base(descriptor); original = copy.deepcopy(source)
            shapes = set()
            for seed in range(10):
                room = self.generator.generate(source, seed)
                self.assertTrue(validate(room, source))
                shapes.add(geometry_sha(room['objects']))
                self.assertNotEqual(geometry_sha(source['objects']), geometry_sha(room['objects']))
                self.assertEqual([p['id'] for p in source['metadata']['ports']],
                                 [p['id'] for p in room['metadata']['ports']])
                self.assertEqual(len(source['metadata']['ports']), len(room['metadata']['ports']))
                self.assertTrue(set(source['objects']).issubset(room['objects']))
            self.assertGreaterEqual(len(shapes), 4, descriptor)
            self.assertEqual(source, original, 'generation mutated the source recipe')

    def test_every_copy_is_a_complete_native_piece_with_matching_draw_and_collision_collars(self):
        source = self.base('scmid10')
        room = self.generator.generate(source, 17)
        proof = room['metadata']['generation']; cursor = proof['plane_mp']
        for bridge in proof['bridges']:
            template = self.generator.data['templates'][bridge['template']]
            mapping = bridge['object_id_map']; dx, dy = bridge['translation_mp']
            for oid, props in template['objects'].items():
                self.assertEqual(room['objects'][mapping[oid]], relocate(props, dx, dy, mapping))
            a = collar(room['objects'], cursor, 'left')
            b = collar(room['objects'], cursor, 'right')
            self.assertEqual(a, b)
            cursor += template['width_mp']
        self.assertEqual(collar(room['objects'], cursor, 'left'),
                         collar(room['objects'], cursor, 'right'))

    def test_room_arrivals_triggers_native_wire_and_decoration_remain_consistent(self):
        source = self.base('scmid15'); room = self.generator.generate(source, 5)
        proof = room['metadata']['generation']; moving = set(proof['moving_source_ids']); dx = proof['translation_mp'][0]
        for p, q in zip(source['metadata']['ports'], room['metadata']['ports']):
            shift = dx if p['id'] in moving else 0
            self.assertEqual(q['at'], [p['at'][0]+shift, p['at'][1]])
            self.assertEqual(room['entries'][p['entry']],
                             [source['entries'][p['entry']][0]+shift, source['entries'][p['entry']][1]])
            self.assertEqual(q['trigger_id'], p['trigger_id'])
            self.assertEqual(q['side'], p['side'])
        self.assertEqual(room['world']['worldprops_']['decoration_'], source['world']['worldprops_']['decoration_'])
        self.assertEqual(room['world']['worldprops_']['gdv_'], source['world']['worldprops_']['gdv_'])
        rows = room['world']['worldstate_']['serverworld_']['obj_']
        self.assertEqual({r['objid_']:r['props_'] for r in rows},
                         {i:normalize('Properties', p) for i,p in room['objects'].items()})
        blob = pb_layout.default_layouts().encode('CreateWorld', room['world'])
        self.assertTrue(blob)
        self.assertEqual(room['world']['spawnobj_']['objid_'], room['metadata']['spawn_marker_id'])

    def test_emitter_coordinate_clouds_move_as_whole_groups(self):
        source = self.base('scmid07')
        rule_index = min(range(len(self.generator.data['recipes']['scmid07'])),
                         key=lambda i:self.generator.data['recipes']['scmid07'][i]['plane_mp'])
        room = self.generator.generate(source, 9, rule_index=rule_index, count=2)
        proof = room['metadata']['generation']; plane = proof['plane_mp']; dx = proof['translation_mp'][0]
        old = source['metadata']['sc_wraith_emitters']; new = room['metadata']['sc_wraith_emitters']
        for a,b in zip(old,new):
            expected_shift = dx if min(p[0] for p in cloud_points(a)) >= plane else 0
            self.assertEqual(cloud_points(b), [[p[0]+expected_shift,p[1]] for p in cloud_points(a)])
            self.assertEqual(a['sin'],b['sin'])

    def test_typed_moves_references_creator_and_id_keyed_metadata_are_remapped(self):
        g = ScRoomGenerator(self.content.root)
        source = self.base('scmid10'); rule = g.data['recipes']['scmid10'][0]
        name = rule['templates'][0]; template = g.data['templates'][name]
        first, second = sorted(template['objects'])[:2]
        template['objects'][first]['move'] = dict(relativeto=dict(objid=second), to=dict(xmp=17,ymp=-29))
        template['objects'][second]['move'] = dict(sin=dict(xmp=123,ymp=456,vel=100,period=500,amp=20,angle=0))
        template['creators'][first] = second
        template.setdefault('metadata',{})['sc_mledge_velocity'] = {first:[0,-500]}
        template['metadata']['sc_surn_counts'] = {second:3}
        room = g.generate(source, 1, rule_index=0, count=1, template_ids=[name])
        proof = room['metadata']['generation']['bridges'][0]; m = proof['object_id_map']; dx,dy = proof['translation_mp']
        self.assertEqual(room['objects'][m[first]]['move'],dict(relativeto=dict(objid=m[second]),to=dict(xmp=17,ymp=-29)))
        self.assertEqual(room['objects'][m[second]]['move']['sin']['xmp'],123+dx)
        self.assertEqual(room['objects'][m[second]]['move']['sin']['ymp'],456+dy)
        self.assertEqual(room['creators'][m[first]],m[second])
        self.assertEqual(room['metadata']['sc_mledge_velocity'][m[first]],[0,-500])
        self.assertEqual(room['metadata']['sc_surn_counts'][m[second]],3)
        self.assertTrue(all(ref in room['objects'] for p in room['objects'].values() for ref in refs(p)))

    def test_cross_partition_references_are_rejected(self):
        source = self.base('scmid10'); rule = self.generator.data['recipes']['scmid10'][0]; plane=rule['plane_mp']
        left=next(i for i,p in source['objects'].items()if p.get('xmp',0)<plane)
        right=next(i for i,p in source['objects'].items()if p.get('xmp',0)>=plane)
        source['objects'][right]['move']=dict(relativeto=dict(objid=left),to=dict(xmp=0,ymp=0))
        with self.assertRaisesRegex(ValueError,'reference/creator/navigation'):
            self.generator.generate(source,1,rule_index=0,count=1)

    def test_real_module_skeletons_keep_source_id_fit_without_internal_wire_fields(self):
        name = 'scmid10-2144-2816'
        room = self.generator.generate(self.base('scmid07'), 314, rule_index=0,
                                       count=1, template_ids=[name])
        mapping = room['metadata']['generation']['bridges'][0]['object_id_map']
        sources = room['metadata']['sc_skeleton_source_ids']
        self.assertEqual((mapping[32], mapping[34]), (1126, 1128))
        expected_ids = {mapping[oid] for oid, p in self.generator.data['templates'][name]['objects'].items()
                        if p['vid'] == 'sc.skeleton'}
        self.assertEqual(set(sources), expected_ids)
        for stringify in (False, True):
            metadata = copy.deepcopy(room['metadata'])
            if stringify:
                metadata['sc_skeleton_source_ids'] = {str(k): v for k, v in sources.items()}
            h = EnemyHost(metadata)
            for original in (32, 34):
                oid = mapping[original]; p = room['objects'][oid]
                h.add(oid, 'sc.skeleton', (p['xmp'], p['ymp']), changes=p)
            h.enemies.prepare()
            self.assertEqual(h.enemies.units[mapping[32]]['variant'], 'cleave')
            self.assertEqual(h.enemies.units[mapping[34]]['variant'], 'thrust')
        for oid in expected_ids:
            self.assertNotIn('_sc_skeleton_variant', room['objects'][oid])
            self.assertNotIn('sc_skeleton_source_ids', room['objects'][oid])
        layouts = pb_layout.default_layouts()
        wire = layouts.decode('CreateWorld', layouts.encode('CreateWorld', room['world']), raw_strings=True)
        self.assertNotIn('sc_skeleton_source_ids', wire)
        for row in wire['worldstate_']['serverworld_']['obj_']:
            self.assertFalse(any(k.startswith('_sc') or k.startswith('sc_skeleton_source') for k in row['props_']))

    def test_source_identity_does_not_override_explicit_variants_or_other_policies(self):
        for policy, explicit, expected in (
                ('object_id_fit', 'thrust', 'thrust'),
                ('thrust', None, 'thrust')):
            metadata = dict(sc_skeleton_source_ids={1126: 32},
                            sc_enemy_fit=dict(skeleton_variant_policy=policy))
            if explicit:
                metadata['sc_skeleton_variants'] = {'1126': explicit}
            h = EnemyHost(metadata)
            h.add(1126, 'sc.skeleton', (0, 0))
            h.enemies.prepare()
            self.assertEqual(h.enemies.units[1126]['variant'], expected)

    def test_special_and_unproven_rooms_are_unchanged(self):
        for descriptor in ('scmid01','scmid02','scmid03','scmid04','scmid05','scmid06','scmid11','scmid17','scmid18','sctask01','sccore','scboss'):
            source = self.base(descriptor); room = self.generator.generate(source, 123)
            self.assertEqual(room['objects'],source['objects'])
            self.assertEqual(room['world'],source['world'])
            self.assertEqual(room['metadata']['generation']['mode'],'unchanged-native')

    def test_cached_results_are_stable_isolated_and_not_assembled_twice(self):
        source=self.base('scmid13');a=self.generator.generate(source,42);b=self.generator.generate(source,42)
        self.assertEqual(a,b)
        a['objects'].clear()
        self.assertTrue(self.generator.generate(source,42)['objects'])
        self.assertEqual(self.generator.generate(b,42),b)
        with self.assertRaisesRegex(ValueError,'original room recipe'):
            self.generator.generate(b,43)


if __name__=='__main__':unittest.main()
