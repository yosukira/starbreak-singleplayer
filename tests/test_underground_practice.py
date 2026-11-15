import unittest
import os
from types import SimpleNamespace as NS
from unittest import mock
from localserver import game_settings, equipment
from localserver.maps.dumb_wave.controller import DumbWaveController
from localserver.maps.dumb_normal_wave.scene import DumbNormalWavePack


class UndergroundPracticeTests(unittest.TestCase):
    def test_selected_wave_uses_real_wave_budget_and_final_pattern_after_twenty(self):
        for wave in (1, 7, 20, 21, 25):
            with self.subTest(wave=wave), game_settings.event_snapshot(
                    {'balance': {'dumb_wave': {'start_wave': wave}}}):
                controller = DumbWaveController()
                controller.begin()
                self.assertEqual(controller.wave, wave)
                self.assertEqual(controller.pattern_wave(), min(wave, 20))
                controller.step(controller.until)
                self.assertEqual(controller.state, 'wave')
                self.assertEqual(sum(controller.groups), controller.quota())

    def test_normal_ub_still_starts_at_one(self):
        with game_settings.event_snapshot({'balance': {'dumb_wave': {'start_wave': 20}}}):
            controller = DumbWaveController(DumbNormalWavePack())
            controller.begin()
            self.assertEqual(controller.wave, 1)

    def test_blockers_recommendations_match(self):
        priority = equipment.build_affix_priority(equipment.load_catalogue())
        self.assertEqual(priority['exact'][192], priority['exact'][257])
        self.assertEqual(priority['defaults'][192], priority['defaults'][257])

    def test_native_selected_map_owns_pad_ids_even_when_zone_is_empty(self):
        from plugins import hardlab_live
        for selected, x, y, source, destination in (
                ('dumb_normal_wave', 49152, 120000, 20, 23),
                ('dumb_wave', 49152, 120000, 16, 19),
                ('dumb_boss', 40960, 230000, 62, 58)):
            placed = []
            props = NS(xmp=x, ymp=y, wmp=7680, hmp=17920)
            player = NS(asClientObject=NS(asNativeObject=NS(asWorldObject=NS(props=props))))
            world = NS(player=player, asWorld=NS(props=NS(music='dumb', zone='')))
            bridge = NS(interaction_blocked=lambda: False, input_context={},
                _current_world=lambda: world, place_player=lambda x,y: placed.append((x,y)) or True)
            plugin = NS(bridge=bridge, native_capture=None, _teleport_trace=lambda message: None)
            with self.subTest(map=selected), mock.patch.dict(os.environ, HARDLAB_PRACTICE_MAP=selected), \
                    mock.patch.object(hardlab_live, 'ffi', NS(NULL=None)), \
                    mock.patch.object(hardlab_live, 'util', NS(getstr=lambda value:value)):
                result = hardlab_live.Plugin._dumb_boss_teleport(plugin)
            self.assertEqual((result['source'],result['destination']),(source,destination))
            self.assertEqual(len(placed),1)


if __name__ == '__main__':
    unittest.main()
