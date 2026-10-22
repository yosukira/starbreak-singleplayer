"""SC graph invariants across seeds, independent of the exploration length."""
import collections
import unittest

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.layout import CHESTS, ENCOUNTERS, FIXED, generate, ports


class ScLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content = ScContent()
        cls.plans = [generate(cls.content, seed) for seed in (0, 1, 9, 17, 104729)]

    def test_each_mid_occurs_once_and_each_gen_has_one_gated_mid_door(self):
        for plan in self.plans:
            with self.subTest(seed=plan['seed']):
                rooms = plan['rooms']
                mids = {role: r for role, r in rooms.items() if role.startswith('mid_')}
                self.assertEqual(collections.Counter(r['metadata']['encounter_kind'] for r in mids.values()),
                                 collections.Counter(ENCOUNTERS.values()))
                self.assertEqual(collections.Counter(r['metadata']['template'] for r in mids.values()),
                                 collections.Counter(ENCOUNTERS.keys()))
                gens = {role: r for role, r in rooms.items() if r['metadata'].get('encounter_kind') == 'gen'}
                self.assertEqual(len(gens), 4)
                attached = []
                for role, r in gens.items():
                    self.assertEqual(len(r['links']), 1, 'A Gen is an exclusive one-door Mid pocket')
                    back = next(iter(r['links'].values()))
                    self.assertIn(back['destination'], mids)
                    attached.append(back['destination'])
                    forward = [link for link in rooms[back['destination']]['links'].values()
                               if link['destination'] == role]
                    self.assertEqual(len(forward), 1)
                    self.assertEqual(forward[0].get('requires'), 'mid_cleared')
                self.assertEqual(collections.Counter(attached), collections.Counter(mids.keys()))

    def test_every_connected_port_has_a_reciprocal_destination_and_arrival(self):
        for plan in self.plans:
            with self.subTest(seed=plan['seed']):
                rooms = plan['rooms']
                occupied = set()
                for edge in plan['edges']:
                    for src, dst, door, other in ((edge['a'], edge['b'], edge['a_port'], edge['b_port']),
                                                  (edge['b'], edge['a'], edge['b_port'], edge['a_port'])):
                        self.assertNotIn((src, door), occupied, 'A physical port cannot lead to two rooms')
                        occupied.add((src, door))
                        link = rooms[src]['links'][door]
                        self.assertEqual(link['destination'], dst)
                        arrival = next(p for p in ports(rooms[dst]) if p['id'] == other)
                        self.assertEqual(link['entry'], arrival['entry'])
                        self.assertIn(link['entry'], rooms[dst]['entries'])
                self.assertEqual(len(occupied), sum(len(r['links']) for r in rooms.values()))
                self.assertEqual(occupied, {(role, p['id']) for role, r in rooms.items() for p in ports(r)})

    def test_normal_rooms_are_reachable_and_world_map_footprints_do_not_overlap(self):
        for plan in self.plans:
            with self.subTest(seed=plan['seed']):
                rooms = plan['rooms']
                reached, pending = {'entry'}, ['entry']
                while pending:
                    role = pending.pop()
                    for link in rooms[role]['links'].values():
                        if link['destination'] not in reached:
                            reached.add(link['destination'])
                            pending.append(link['destination'])
                # The final Boss is reached by the four-Gen completion portal;
                # ordinary authored door traversal must reach every other room.
                self.assertTrue(set(rooms)-{'boss'} <= reached)
                self.assertEqual(rooms['boss']['metadata']['encounter_kind'], 'boss')
                rows = list(rooms.items())
                for index, (name, r) in enumerate(rows):
                    x, y, right, bottom = r['metadata']['world_map_bounds_mp']
                    self.assertLess(x, right)
                    self.assertLess(y, bottom)
                    for other, q in rows[index+1:]:
                        qx, qy, qr, qb = q['metadata']['world_map_bounds_mp']
                        self.assertFalse(min(right, qr) > max(x, qx) and min(bottom, qb) > max(y, qy),
                                         name+' overlaps '+other)

    def test_seed_changes_the_layout_and_repeating_a_seed_is_deterministic(self):
        def graph(plan):
            rooms = tuple(sorted((role, r['metadata']['template'], tuple(plan['cells'][role]))
                                 for role, r in plan['rooms'].items()))
            edges = tuple(sorted((e['a'], e['b'], e['a_port'], e['b_port'], str(e.get('requires')))
                                 for e in plan['edges']))
            return rooms, edges
        signatures = [graph(plan) for plan in self.plans]
        self.assertGreater(len(set(signatures)), 1, 'Different seeds must change real room connectivity or placement')
        self.assertEqual(self.plans[0], generate(self.content, self.plans[0]['seed']))

    def test_required_fixed_rooms_all_six_chests_and_one_cycle_survive_seed_changes(self):
        for plan in self.plans:
            with self.subTest(seed=plan['seed']):
                counts = collections.Counter(r['metadata']['template'] for r in plan['rooms'].values())
                for descriptor in FIXED+CHESTS:
                    self.assertEqual(counts[descriptor], 1, descriptor)
                connected = len(plan['rooms'])-1  # Boss uses the completion portal.
                self.assertEqual(len(plan['edges'])-connected+1, 1)
                self.assertEqual(plan['topology']['port_rotation_genus'], 0)
                self.assertEqual(plan['topology']['port_rotation_faces'], 2)
                arms = plan['topology']['arms']
                self.assertEqual(len(arms), 4)
                self.assertEqual(len({arm['entry_port'] for arm in arms}), 4)
                routed = [name for arm in arms for name in arm['rooms']]
                exploration = {name for name,r in plan['rooms'].items()
                               if r['metadata']['layout_category']=='exploration'}
                self.assertEqual(set(routed), exploration)
                self.assertTrue(all(arm['root'] in arm['rooms'] for arm in arms))
                for a in arms:
                    for b in arms:
                        if a['root'] == b['root']:
                            self.assertEqual(a['rooms'], b['rooms'])
                loop = next(r for r in plan['rooms'].values() if r['metadata']['template'] == 'scmid05')
                self.assertEqual([p['side'] for p in ports(loop)], ['left', 'left'])

    def test_local_numbering_is_unique_and_port_entry_names_are_wire_compatible(self):
        for plan in self.plans:
            main = [r for r in plan['rooms'].values() if r['metadata']['layout_category'] not in ('gen', 'boss')]
            self.assertEqual(sorted(r['metadata']['main_index'] for r in main), list(range(25)))
            self.assertEqual(sorted(r['world']['worldprops_']['floor_'] for r in main), list(range(25)))
            self.assertEqual(plan['rooms']['entry']['metadata']['main_index'], 0)
            self.assertIn('FIT', plan['generation_policy'])
            for r in plan['rooms'].values():
                for entry in r['entries']:
                    self.assertRegex(entry, r'^[A-Za-z0-9_-]+$')
                for p in ports(r):
                    self.assertEqual(p['entry'], 'door_'+str(p['id']))
                if r['metadata']['layout_category'] in ('gen', 'boss'):
                    self.assertEqual(r['world']['worldprops_']['floor_'], 0)
                    self.assertNotIn('main_index', r['metadata'])

    def test_chest02_reward_uses_highest_tank_and_other_guards_have_explicit_identity(self):
        for plan in self.plans:
            for r in plan['rooms'].values():
                if r['metadata']['layout_category'] != 'chest':
                    continue
                meta = r['metadata']
                if meta['template'] == 'scend02':
                    self.assertNotIn('chest_reward_unresolved', meta)
                    tanks = {oid: p for oid, p in r['objects'].items() if p.get('vid') == 'sc.tank'}
                    self.assertEqual(len(tanks), 27)
                    self.assertEqual(meta['chest_guardian'], min(tanks, key=lambda oid:tanks[oid]['ymp']))
                    self.assertIn('USER', meta['chest_guardian_evidence'])
                elif meta['template'] == 'scend06':
                    self.assertEqual(r['objects'][meta['chest_guardian']]['vid'], 'sc.tank')
                    self.assertIn('FIT', meta['chest_guardian_evidence'])
                else:
                    self.assertEqual(r['objects'][meta['chest_guardian']]['vid'], 'sc.egolem')

    def test_exploration_room_count_is_a_configurable_fit_without_omitting_required_rooms(self):
        from localserver.maps.sc_run.scene import ScRunPack
        for budget in (13, 16):
            plan = generate(self.content, 42, exploration_rooms=budget)
            self.assertEqual(sum(r['metadata']['layout_category'] == 'exploration' for r in plan['rooms'].values()), budget)
            self.assertEqual(plan['topology']['main_room_count'], budget+11)
            self.assertEqual(plan['local_layout_fit']['exploration_rooms'], budget)
        pack = ScRunPack(exploration_rooms=13).configure(42)
        self.assertEqual(pack.plan['topology']['main_room_count'], 24)
        self.assertEqual(pack.room('entry')['world']['worldprops_']['floor_'], 0)
        for invalid in (10, 12, 13.5, 19):
            with self.assertRaises(ValueError):
                generate(self.content, 0, exploration_rooms=invalid)


if __name__ == '__main__':
    unittest.main()
