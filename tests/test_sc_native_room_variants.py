"""Whole native variants participate in the same seeded live room pipeline."""
import unittest

from localserver.maps.sc_run.catalogue import ScContent
from localserver.maps.sc_run.generator import geometry_sha
from localserver.maps.sc_run.doors import arrival, overlaps
from localserver.maps.sc_run.physics import surface
from test_sc_ground_platform_regressions import host


class ScNativeRoomVariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.content=ScContent()

    def test_native_families_select_distinct_real_geometry_deterministically(self):
        for name,minimum in (('scmid04',5),('scmid05',3),('scmid17',3),('scmid18',7)):
            with self.subTest(template=name):
                variants=self.content.templates[name]
                self.assertGreaterEqual(len(variants),minimum)
                selected=[self.content.recipe(name,seed) for seed in range(len(variants))]
                self.assertEqual(len({geometry_sha(r['objects']) for r in selected}),len(variants))
                self.assertEqual(selected[1],self.content.recipe(name,1))
                for room in selected:
                    for p in room['metadata']['ports']:
                        if p['kind']=='core':continue
                        for w,h in ((30*256,70*256),(40*256,80*256),(60*256,84*256)):
                            x,y=arrival(room,p['entry'],w,h)
                            self.assertFalse(overlaps((x,y,w,h),room['objects'][p['trigger_id']]))
                            tops=[surface(tuple(q.get(k,0) for k in ('xmp','ymp','wmp','hmp')),
                                          q.get('shapetype',0),x,w)
                                  for q in room['objects'].values() if q.get('terraintype') in (1,2)]
                            self.assertTrue(any(t is not None and abs(t-y-h)<=1 for t in tops))

    def test_new_four_door_variants_have_live_platform_supported_tanks(self):
        for name in ('scmid17','scmid18'):
            for seed in range(1,len(self.content.templates[name])):
                h=host(name,seed=seed)
                tanks=[oid for oid,u in h.enemies.units.items() if u['vid']=='sc.tank']
                self.assertTrue(tanks)
                if name=='scmid17':
                    core=next(r for r in h.actors.values() if r['vid']=='sc.enginecore')
                    self.assertEqual(core['hp'],60000)
                    self.assertEqual(sum(r['vid']=='sc.engineball' for r in h.actors.values()),4)
                for _ in range(10):h.step(.05)
                for oid in tanks:
                    row=h.actors[oid];u=h.enemies.units[oid]
                    self.assertTrue(u['grounded'])
                    self.assertIn(u['support'],h.actors)
                    q=h.actors[u['support']]
                    top=surface((*q['pos'],q['props'].get('wmp',0),q['props'].get('hmp',0)),
                                q['props'].get('shapetype',0),row['pos'][0],row['props']['wmp'])
                    self.assertAlmostEqual(row['pos'][1]+row['props']['hmp'],top,delta=1.1)
                    for actual,expected in zip(h.instance.snapshot.position((1,oid)),row['pos']):
                        self.assertAlmostEqual(actual,expected,delta=1.1)


if __name__=='__main__':
    unittest.main()
