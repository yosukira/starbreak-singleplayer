import random
from types import SimpleNamespace
import unittest

from localserver import game_settings, pb_layout, paths
from localserver.live.bridge import PlayerState
from localserver.live.enemies import Enemy, BEHAVIOUR
from localserver.live.room import BossRoom
from localserver.live.session import LiveSession
from localserver.maps.hulk_normal.session import HulkNormalLiveSession
from localserver.server import parse_args


class ShredderPatrolTests(unittest.TestCase):
    def test_far_wall_does_not_pin_shredders_to_left_ship_rim(self):
        for map_id in ('egy_hulk', 'hulk_normal'):
            source = paths.ROOT/('data/catalogue/real_hulkboss_createworld_flags.hex' if map_id == 'egy_hulk' else 'maps/hulk_normal/createworld.hex')
            options = parse_args(['--map', map_id, '--create-world-hex', str(source)])
            options.live_seed = 23
            server = SimpleNamespace(layouts=pb_layout.default_layouts(), _worldid=lambda: 1, class_loadouts={})
            cls = LiveSession if map_id == 'egy_hulk' else HulkNormalLiveSession
            with game_settings.event_snapshot(game_settings.normalize({'map': map_id, 'skip_intro': {map_id: True}})):
                session = cls(server, None, options, BossRoom.from_capture(str(source), options.boss_templates))
                session.prepare(); session._skip_opening()
                x, y, _, _ = session.room.object(6).rect()
                for px in (0, 2500):  # Unactivated and nearby/activated high targets.
                    for vid in ('hulk.scavengershredder', 'hulk.scavengershredderenergy'):
                        with self.subTest(map=map_id, player_x=px, vid=vid):
                            stats = getattr(session.rules, 'BEHAVIOUR', BEHAVIOUR)[vid]
                            w, h = stats['size']
                            unit = Enemy(999, vid, stats, x+30*256, y-h, w, h,
                                         rng=random.Random(20), room=session.room, rules=session.rules)
                            unit.patrol_direction = -1
                            player = PlayerState(objid=2, worldid=2, xmp=px*256, ymp=-700*256, hp=100)
                            positions = []
                            for tick in range(1200):
                                unit.step(1/60, player, (tick+1)/60)
                                if tick >= 1080: positions.append(unit.x)
                            self.assertGreater(max(positions)-min(positions), 50*256)
                            self.assertGreaterEqual(unit.deck_rim_turns, 4)
                            self.assertIsNone(unit.climb_route)
                            self.assertTrue(unit.motion.grounded)

    def test_adjacent_wall_remains_climbable_but_gap_is_not(self):
        room = BossRoom([
            {'objid_':1,'props_':{'xmp_':0,'ymp_':0,'wmp_':500*256,'hmp_':20*256,'terraintype_':2}},
            {'objid_':2,'props_':{'xmp_':-20*256,'ymp_':-600*256,'wmp_':20*256,'hmp_':600*256,'terraintype_':1}},
        ], {})
        stats = BEHAVIOUR['hulk.scavengershredder']; w,h = stats['size']
        unit = Enemy(99, 'hulk.scavengershredder', stats, 30*256, -h, w,h, rng=random.Random(20), room=room)
        self.assertEqual(unit._nearest_climb_route(), (-1, 0))
        room.object(2).set(xmp_=-300*256)
        self.assertIsNone(unit._nearest_climb_route())
        # Loss of a target also preserves bounded patrol instead of walking off.
        positions=[]
        for tick in range(600):
            unit.step(1/60, None, (tick+1)/60); positions.append(unit.center()[0])
        self.assertGreater(max(positions)-min(positions), 200*256)
        self.assertTrue(unit.motion.grounded)


if __name__ == '__main__': unittest.main()
