"""A determined skeleton keeps its attack identity when respawn allocates an ID."""
import unittest

from test_sc_ghost_volley_fidelity import actor_host


class ScSkeletonRespawnIdentityTests(unittest.TestCase):
    def skeleton(self, variant=None, *, integer_key=False, fit_cleave=False):
        host, snapshot, sent, _births = actor_host()
        host._create('sc.wall', (-1000*256, 128*256), changes=dict(
            wmp=2000*256, hmp=64*256, terraintype=1))
        if fit_cleave:
            # Give this initial unit the existing modulo-four FIT identity.
            # These inert actors exercise the real allocator without replacing it.
            host._create('', (10000*256, 0))
            host._create('', (10000*256, 0))
        oid = host._create('sc.skeleton', (0, 0))
        if variant is not None:
            key = oid if integer_key else str(oid)
            host.recipe['metadata']['sc_skeleton_variants'] = {key: variant}
        host.enemies.add_actor(oid)
        return host, snapshot, sent, oid

    def assert_attack(self, host, snapshot, sent, oid, duration):
        host.players_provider = {7: dict(xmp=100*256, ymp=32*256,
            wmp=32*256, hmp=64*256, alive=True)}
        host.step(.15)
        animation = snapshot.properties_at((1, oid))['animprops_']
        self.assertEqual((animation['animid_'], animation['time_']), (6, duration))
        updates = sent[-1]['worldupdate_']['objectupdates_']
        self.assertTrue(any(update['objid_'] == oid
            and update.get('modifyprops_', {}).get('property_') == 49 for update in updates))
        host.players_provider = {}

    def assert_two_revivals(self, host, snapshot, sent, oid, variant, duration):
        self.assertEqual(host.enemies.units[oid]['variant'], variant)
        self.assert_attack(host, snapshot, sent, oid, duration)
        ids = [oid]
        for generation in (1, 2):
            host.feedback(dict(kind='damage', ownerid=1, victim=oid,
                               value=1000000, event_id='kill-'+str(generation)))
            self.assertEqual(host.enemies.respawns[0]['at'], host.room_time+120.)
            self.assertFalse(any('_sc_skeleton_variant' in key
                                 for key in host.enemies.respawns[0]['props']))
            host.step(119.9)
            self.assertFalse(host.enemies.units, 'A determined type does not shorten the 120s wait')
            host.step(.2)
            oid = next(iter(host.enemies.units))
            ids.append(oid)
            self.assertIn((1, oid), snapshot.objects)
            self.assertEqual(host.enemies.units[oid]['variant'], variant)
            self.assert_attack(host, snapshot, sent, oid, duration)
        self.assertEqual(len(set(ids)), 3)

    def test_explicit_metadata_variant_keeps_its_actual_attack_after_two_revivals(self):
        for variant, duration, integer_key in (('cleave', 726, False), ('thrust', 594, True)):
            with self.subTest(variant=variant):
                host, snapshot, sent, oid = self.skeleton(variant, integer_key=integer_key)
                self.assert_two_revivals(host, snapshot, sent, oid, variant, duration)

    def test_existing_id_fit_is_selected_once_then_kept_for_the_same_unit(self):
        host, snapshot, sent, oid = self.skeleton(fit_cleave=True)
        self.assertEqual(oid % 4, 0)
        self.assertNotIn('sc_skeleton_variants', host.recipe['metadata'])
        self.assert_two_revivals(host, snapshot, sent, oid, 'cleave', 726)


if __name__ == '__main__':
    unittest.main()
