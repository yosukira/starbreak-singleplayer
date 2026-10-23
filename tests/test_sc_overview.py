"""The installed SC overview needs no optional graph-layout package."""
import builtins
import collections
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.layout import generate
from localserver.maps.sc_run import overview


class ScOverviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()
        cls.plan = generate(cls.content, 23)

    def test_seed23_renders_without_networkx_and_retains_every_door_edge(self):
        original_import = builtins.__import__
        def installed_import(name, *args, **kwargs):
            if name == 'networkx' or name.startswith('networkx.'):
                raise ModuleNotFoundError('networkx is absent from the installed runtime')
            return original_import(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=installed_import):
            source = overview.svg(self.plan)
        root = ET.fromstring(source)
        edges = [e for e in root.iter() if 'data-edge' in e.attrib]
        self.assertEqual(len(edges), 29)
        self.assertEqual(len({e.attrib['data-edge-id'] for e in edges}), 29)
        for edge in self.plan['edges']:
            key = '%s:%s--%s:%s' % (edge['a'],edge['a_port'],edge['b'],edge['b_port'])
            self.assertEqual(sum(e.attrib['data-edge'] == key for e in edges), 1)
        self.assertEqual(sum('data-role' in e.attrib for e in root.iter()), 30)
        self.assertEqual(sum('data-door' in e.attrib for e in root.iter()), 58)

    def test_adjacent_four_way_rooms_keep_both_ordered_connections(self):
        root = ET.fromstring(overview.svg(self.plan))
        paired = [e for e in root.iter() if e.attrib.get('data-pair') == 'P1']
        self.assertEqual({e.attrib['data-edge'] for e in paired},
                         {'entry:4--statue:197', 'entry:12--statue:152'})
        paths = [next(child.attrib['d'] for child in edge if child.tag.endswith('path')) for edge in paired]
        self.assertEqual(len(set(paths)), 2)

    def test_cards_and_routed_relations_do_not_cover_unrelated_rooms(self):
        drawing = overview._thumbnail_drawing(self.plan)
        boxes = {role: card['box'] for role,card in drawing['cards'].items()}
        for a,box in boxes.items():
            for b,other in boxes.items():
                if a != b:
                    self.assertFalse(overview._rect_overlap(box,other), a+'/'+b)
        for route in drawing['routes']:
            edge = route['edge']
            for a,b in overview._segments(route['points']):
                for role,box in boxes.items():
                    if role not in (edge['a'],edge['b']):
                        self.assertFalse(overview._inside_segment(a,b,box), route['id']+'/'+role)

    def test_numbering_follows_the_configured_room_count(self):
        for budget,last in ((13,23),(16,26)):
            with self.subTest(budget=budget):
                source = overview.svg(generate(self.content,23,exploration_rooms=budget))
                self.assertIn('本地主关 00–%02d' % last,source)
                self.assertNotIn('本地主关 00–24',source)
                self.assertIn('编号与房数为本地拟合',source)
                self.assertIn('精确总数仍未知',source)

    def test_labels_distinguish_loop_gen_and_abstract_transitions(self):
        source = overview.svg(self.plan)
        self.assertIn('Loop',source)
        self.assertIn('线段仅表示双向传送，交叉或共线不连通',source)
        self.assertNotIn('零交线',source)
        root = ET.fromstring(source)
        templates = collections.Counter(e.attrib['data-template'] for e in root.iter() if 'data-template' in e.attrib)
        self.assertEqual(templates['scmid05'],1)
        self.assertEqual(templates['sccore'],4)
        self.assertEqual(templates['scboss'],1)


if __name__ == '__main__':
    unittest.main()
