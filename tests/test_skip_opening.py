from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from localserver import game_settings, pb_layout, paths
from localserver.live.room import BossRoom
from localserver.live.session import LiveSession
from localserver.maps.hulk_normal.session import HulkNormalLiveSession
from localserver.server import parse_args
from tools.web_workspace import Workspace


class SkipOpeningTests(unittest.TestCase):
    def session(self, map_id, enabled):
        source = (paths.ROOT/'maps/hulk_normal/createworld.hex' if map_id == 'hulk_normal'
                  else paths.ROOT/'data/catalogue/real_hulkboss_createworld_flags.hex')
        options = parse_args(['--map',map_id,'--create-world-hex',str(source)])
        options.live_seed = 23
        options.player_x, options.player_y = 0, 0
        layouts = pb_layout.default_layouts()
        server = SimpleNamespace(layouts=layouts,_worldid=lambda:1,class_loadouts={})
        cls = HulkNormalLiveSession if map_id == 'hulk_normal' else LiveSession
        document = game_settings.normalize({'map':map_id,'skip_intro':{map_id:enabled}})
        with game_settings.event_snapshot(document):
            session = cls(server,None,options,BossRoom.from_capture(str(source), options.boss_templates))
            session.prepare()
        return session

    def test_off_uses_original_robot_intro_for_both_difficulties(self):
        for map_id in ('hulk_normal','egy_hulk'):
            with self.subTest(map=map_id):
                session = self.session(map_id,False)
                self.assertEqual(session.projectiles.missile_delay_seconds,.275)
                session.suppress_send = True
                session.step(1/60)
                self.assertEqual(session.phase,'intro')
                self.assertFalse(session.intro_destroyed)
                self.assertEqual(session.boss_time,0)
                self.assertFalse(session.gens.enabled)

    def test_on_starts_at_parked_ship_without_skipped_combat_side_effects(self):
        for map_id in ('hulk_normal','egy_hulk'):
            with self.subTest(map=map_id):
                session = self.session(map_id,True)
                session._skip_opening()
                self.assertEqual(session.phase,'fighting')
                self.assertEqual(session.ship_controller.state,'home-hold')
                self.assertEqual(session.room.ship.position(),session.ship_controller.home)
                self.assertEqual(set(session.intro_destroyed),set(session.intro_units))
                self.assertTrue(session.platforms.enabled)
                self.assertFalse(session.platforms.terrain_due)
                self.assertTrue(all(session.room.object(i).get('terraintype_')==session.rules.LEDGE_TERRAIN_TYPE for i in session.rules.LEDGE_IDS))
                self.assertTrue(session.gens.enabled)
                self.assertEqual(session.gens.next_panel_delay,max(0,session.rules.PANEL_FIRST_DELAY-session.boss_time))
                self.assertFalse(session.gens.destroyed)
                self.assertFalse(session.pending_loot)
                self.assertFalse(session.projectiles.projectiles)
                # Closing gates must resume from their fully open positions,
                # rather than replaying their opening or starting closed.
                self.assertEqual(session.motion_tracks[70][1].points[0][2], -286720)
                self.assertEqual(session.motion_tracks[71][1].points[0][2], 57344)
                self.assertEqual(session.enemies.waves,0)
                self.assertFalse(any(row['kind'] in ('loader-survivor','intro-started') for row in session.events.rows))
                self.assertTrue(session.batch.encode())
                before = session.boss_time
                session.suppress_send = True
                for _ in range(120):session.step(1/60)
                self.assertAlmostEqual(session.boss_time,before+2)
                self.assertEqual(session.phase,'fighting')
                self.assertEqual(session.ship_controller.state,'home-hold')

    def test_settings_are_strict_per_map_and_round_trip_with_browser_profile(self):
        self.assertFalse(game_settings.skip_intro('egy_hulk',{}))
        normalized = game_settings.normalize({'skip_intro':{'egy_hulk':'false','hulk_normal':True,'hulk_mid':True}})
        self.assertFalse(game_settings.skip_intro('egy_hulk',normalized))
        self.assertTrue(game_settings.skip_intro('hulk_normal',normalized))
        self.assertNotIn('hulk_mid',normalized['skip_intro'])
        with tempfile.TemporaryDirectory() as directory:
            workspace = Workspace(Path(directory)/'web',Path(directory)/'none')
            profile = workspace.read()
            profile['settings']['skip_intro'] = {'egy_hulk':True,'hulk_normal':False}
            saved = workspace.save(profile)
            workspace.materialize('egy_hulk')
            self.assertTrue(game_settings.skip_intro('egy_hulk',game_settings.load(str(workspace.directory/'game_settings.json'))))
            self.assertEqual(workspace.read()['settings']['skip_intro'],saved['settings']['skip_intro'])
            restored = workspace.restore_defaults(saved['revision'])
            self.assertFalse(game_settings.skip_intro('egy_hulk',restored['settings']))
