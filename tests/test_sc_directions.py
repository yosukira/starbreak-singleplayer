"""Hard SC door direction rules across room budgets and seeded topologies."""
import unittest

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.layout import generate, ports


MID_ENTRY_SIDE = {'sctask01': 'left', 'sctask03': 'left',
                  'sctask02': 'right', 'sctask04': 'right'}
CHEST_ENTRY_SIDE = {'scend01': 'left', 'scend02': 'left', 'scend03': 'left',
                    'scend04': 'right', 'scend05': 'right', 'scend06': 'right'}
OPPOSITE = {'left': 'right', 'right': 'left'}


def port(room, oid):
    return next(p for p in ports(room) if p['id'] == oid)


def core(room, oid):
    return 'coredoor' in room['objects'][oid].get('vid', '')


def endpoint_pairs(plan):
    for edge in plan['edges']:
        a, b = plan['rooms'][edge['a']], plan['rooms'][edge['b']]
        yield edge, a, port(a, edge['a_port']), b, port(b, edge['b_port'])


def original_port_geometry(room):
    """Native door shape/orientation survives corridor translations."""
    result = {}
    for p in ports(room):
        obj = room['objects'][p['id']]
        result[p['id']] = dict(side=p.get('side'),
                               local_center=(p['at'][0]-obj.get('xmp',0),
                                             p['at'][1]-obj.get('ymp',0)),
                               **{name: obj.get(name) for name in
                                  ('vid', 'ymp', 'wmp', 'hmp',
                                   'facing', 'rotate', 'drawmods')})
    return result


class ScDirectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()
        cls.plans = [(budget, seed, generate(cls.content, seed, exploration_rooms=budget))
                     for budget in (13, 14, 16, 18)
                     for seed in (0, 1, 17, 42, 20261022)]

    def test_all_ordinary_edges_join_opposite_native_sides(self):
        for budget, seed, plan in self.plans:
            with self.subTest(budget=budget, seed=seed):
                for edge, a, ap, b, bp in endpoint_pairs(plan):
                    if core(a, ap['id']) or core(b, bp['id']):
                        continue
                    self.assertIn(ap['side'], OPPOSITE, edge)
                    self.assertEqual(bp['side'], OPPOSITE[ap['side']],
                                     '%s:%s cannot connect to %s:%s' %
                                     (edge['a'], ap['side'], edge['b'], bp['side']))

    def test_mid_entry_uses_its_fixed_asset_direction_and_opposite_previous_door(self):
        for budget, seed, plan in self.plans:
            with self.subTest(budget=budget, seed=seed):
                checked = set()
                for edge, a, ap, b, bp in endpoint_pairs(plan):
                    for room, entry, previous, departure in ((a, ap, b, bp), (b, bp, a, ap)):
                        template = room['metadata']['template']
                        if template not in MID_ENTRY_SIDE or core(room, entry['id']):
                            continue
                        self.assertNotIn(template, checked, 'Each Mid must have one ordinary entrance')
                        checked.add(template)
                        self.assertFalse(core(previous, departure['id']))
                        self.assertEqual(entry['side'], MID_ENTRY_SIDE[template], template)
                        self.assertEqual(departure['side'], OPPOSITE[MID_ENTRY_SIDE[template]], template)
                self.assertEqual(checked, set(MID_ENTRY_SIDE))

    def test_all_six_chests_keep_original_entry_side_with_opposite_previous_door(self):
        for budget, seed, plan in self.plans:
            with self.subTest(budget=budget, seed=seed):
                checked = set()
                for edge, a, ap, b, bp in endpoint_pairs(plan):
                    for room, entry, previous, departure in ((a, ap, b, bp), (b, bp, a, ap)):
                        template = room['metadata']['template']
                        if template not in CHEST_ENTRY_SIDE:
                            continue
                        self.assertNotIn(template, checked, 'Each Chest must have one authored entrance')
                        checked.add(template)
                        self.assertEqual(entry['side'], CHEST_ENTRY_SIDE[template], template)
                        self.assertEqual(departure['side'], OPPOSITE[CHEST_ENTRY_SIDE[template]], template)
                        originals = self.content.templates[template]
                        self.assertTrue(all(len(ports(r)) == 1 and
                                            ports(r)[0]['side'] == CHEST_ENTRY_SIDE[template]
                                            for r in originals), template)
                self.assertEqual(checked, set(CHEST_ENTRY_SIDE))

    def test_generated_room_door_shape_and_orientation_match_an_unmirrored_asset(self):
        for budget, seed, plan in self.plans:
            with self.subTest(budget=budget, seed=seed):
                for role, room in plan['rooms'].items():
                    template = room['metadata']['template']
                    actual = original_port_geometry(room)
                    originals = [original_port_geometry(r) for r in self.content.templates[template]]
                    self.assertIn(actual, originals,
                                  role+' changed original door IDs, shape, height, side or draw transform')
                loop = next(r for r in plan['rooms'].values() if r['metadata']['template'] == 'scmid05')
                self.assertEqual([p['side'] for p in ports(loop)], ['left', 'left'],
                                 'Loop must retain its two native left doors')

    def test_adjacent_four_way_rooms_pair_upper_and_lower_ports_without_removing_forks(self):
        plans = [plan for _, _, plan in self.plans] + [generate(self.content, 3)]
        paired, forks = 0, 0
        for plan in plans:
            for name, room in plan['rooms'].items():
                ps = [p for p in ports(room) if p['kind'] != 'core']
                if len(ps) != 4:
                    continue
                for side in ('left', 'right'):
                    source = sorted((p for p in ps if p['side'] == side), key=lambda p:p['at'][1])
                    self.assertEqual(len(source), 2)
                    links = [room['links'][p['id']] for p in source]
                    if links[0]['destination'] != links[1]['destination']:
                        forks += 1
                    for link in links:
                        other = plan['rooms'][link['destination']]
                        target = [p for p in ports(other) if p['kind'] != 'core']
                        if len(target) != 4:
                            continue
                        self.assertEqual(links[0]['destination'], links[1]['destination'],
                                         'A four-way neighbour must own both same-side doors')
                        target = sorted((p for p in target if p['side'] == OPPOSITE[side]),
                                        key=lambda p:p['at'][1])
                        self.assertEqual([link['entry'] for link in links], [p['entry'] for p in target],
                                         'Upper must lead to upper, lower to lower')
                        self.assertEqual([other['links'][p['id']]['entry'] for p in target],
                                         [p['entry'] for p in source])
                        paired += 1
        self.assertGreater(paired, 0, 'Exercise a generated adjacent four-way pair')
        self.assertGreater(forks, 0, 'Independent ordinary branches must remain possible')

    def test_right_to_left_edges_place_destination_strictly_right_with_a_gap(self):
        for budget, seed, plan in self.plans:
            with self.subTest(budget=budget, seed=seed):
                checked = 0
                for edge, a, ap, b, bp in endpoint_pairs(plan):
                    if core(a, ap['id']) or core(b, bp['id']):
                        continue
                    if ap['side'] == 'right' and bp['side'] == 'left':
                        source, destination = a, b
                    elif bp['side'] == 'right' and ap['side'] == 'left':
                        source, destination = b, a
                    else:
                        self.fail('An ordinary edge has incompatible sides: '+str(edge))
                    source_bounds = source['metadata']['world_map_bounds_mp']
                    destination_bounds = destination['metadata']['world_map_bounds_mp']
                    self.assertGreater(destination_bounds[0]-source_bounds[2], 0,
                                       'R-to-L edge must move right and leave a real map gap: '+str(edge))
                    checked += 1
                self.assertGreater(checked, 0)


if __name__ == '__main__':
    unittest.main()
