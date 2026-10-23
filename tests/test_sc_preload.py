"""Room changes consume prepared data without rereading encounter files."""
import unittest
from unittest.mock import patch
from localserver.maps.sc_run.run import ScLevelRun
from localserver.maps.sc_run.scene import ScRunPack
from localserver.maps.sc_run.encounters import bound_pack


class PreloadTests(unittest.TestCase):
    def test_all_rooms_exist_and_encounter_templates_are_isolated_before_entry(self):
        run = ScLevelRun(ScRunPack(), seed=23)
        self.assertEqual(set(run.rooms), set(run.pack.room_names))
        self.assertTrue(all(room.runtime is None and room.visits == 0 for room in run.rooms.values()))
        with patch('builtins.open', side_effect=AssertionError('Door tried to reread a pack')):
            for kind in ('patriarch', 'bonesaw', 'hive', 'boss'):
                role = 'boss' if kind == 'boss' else 'mid_'+kind
                first = bound_pack(run.pack, run.room(role))
                first.scene['spawn_id'] = -123
                second = bound_pack(run.pack, run.room(role))
                self.assertNotEqual(second.scene['spawn_id'], -123)
                self.assertNotEqual(run.pack.encounter_packs[kind].scene.get('spawn_id'), -123)


if __name__ == '__main__':
    unittest.main()
