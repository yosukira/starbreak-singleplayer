import copy
import unittest

from localserver import pb_layout, paths
from localserver.server import LocalServer
from tools import make_create_world


class CapturedPeerTests(unittest.TestCase):
    def test_old_etl_variant_cannot_create_a_captured_player_or_projectiles(self):
        layouts = pb_layout.default_layouts()
        source = bytes.fromhex((paths.ROOT/'maps/lab_hard/variants/map03-84.hex').read_text())
        world = layouts.decode('CreateWorld', make_create_world.unwrap_decompress(source))
        original_room = copy.deepcopy(world['worldstate_']['serverworld_'])
        original_spawn = copy.deepcopy(world['spawnobj_'])
        # Model a pre-cleanup installation without committing any real name.
        world['worldstate_']['clientworld_'] = [dict(ownerid_=2, obj_=[
            dict(objid_=1, props_=dict(vid_='assassin', displayname_='CapturedGuest', hitpoints_=100)),
            dict(objid_=2, props_=dict(vid_='bullet', hitpoints_=1))])]
        server = LocalServer.__new__(LocalServer)
        server.layouts = layouts
        for player_world in (1, 2):
            world['worldid_'] = player_world
            blob = make_create_world.wrap_compress(layouts.encode('CreateWorld', world))
            cleaned = server._distinct_player_world_blob(blob)
            self.assertNotIn(b'CapturedGuest', make_create_world.unwrap_decompress(cleaned))
            result = layouts.decode('CreateWorld', make_create_world.unwrap_decompress(cleaned))
            self.assertNotIn('clientworld_', result['worldstate_'])
            self.assertEqual(result['worldstate_']['serverworld_'], original_room)
            self.assertEqual(result['spawnobj_'], original_spawn)
            self.assertNotEqual(result['worldid_'], original_room['ownerid_'])
            self.assertEqual(server._distinct_player_world_blob(cleaned), cleaned)

    def test_all_shipped_etl_terrain_variants_have_no_captured_peers(self):
        layouts = pb_layout.default_layouts()
        for path in (paths.ROOT/'maps/lab_hard').rglob('*.hex'):
            with self.subTest(file=path.name):
                world = layouts.decode('CreateWorld', make_create_world.unwrap_decompress(bytes.fromhex(path.read_text())))
                self.assertFalse(world['worldstate_'].get('clientworld_'))


if __name__ == '__main__': unittest.main()
