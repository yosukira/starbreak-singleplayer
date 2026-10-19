import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

from plugins._hardlab_livebridge import LiveBridge


class BattleActivityTests(unittest.TestCase):
    def bridge(self):
        bridge=LiveBridge.__new__(LiveBridge)
        player=NS(asClientObject=NS(asNativeObject=NS(asWorldObject=NS(props=NS(hitpoints=100)))))
        world=NS(player=player,total=0,asWorld=NS(props=NS(seed=5)))
        bridge.ffi=NS(NULL=None,cast=lambda kind,pointer:1000)
        bridge._current_world=lambda:world
        bridge._overlay_open=lambda:False
        return bridge,world

    def test_native_gameplay_latches_short_tap_and_resets_for_new_world(self):
        bridge,world=self.bridge()
        bridge.sample_battle_activity();self.assertFalse(bridge._battle_activity)
        # Character/editor shortcuts and menu/emote actions are not gameplay.
        world.total=1<<8|1<<10|1<<13
        bridge.sample_battle_activity();self.assertFalse(bridge._battle_activity)
        world.total=1
        bridge.sample_battle_activity();self.assertTrue(bridge._battle_activity)
        world.total=0
        bridge.sample_battle_activity();self.assertTrue(bridge._battle_activity)
        world.asWorld.props.seed+=1
        bridge.sample_battle_activity();self.assertFalse(bridge._battle_activity)
        for bit in (0,1,2,3,4,5,6,7,9):
            world.asWorld.props.seed+=1;world.total=1<<bit
            bridge.sample_battle_activity();self.assertTrue(bridge._battle_activity)

    def test_native_menu_and_dead_player_cannot_start_a_played_battle(self):
        bridge,world=self.bridge();world.total=1
        bridge._overlay_open=lambda:True
        bridge.sample_battle_activity();self.assertFalse(bridge._battle_activity)
        bridge._overlay_open=lambda:False
        world.player.asClientObject.asNativeObject.asWorldObject.props.hitpoints=0
        bridge.sample_battle_activity();self.assertFalse(bridge._battle_activity)

    def test_plugin_keeps_embedded_python_35_syntax(self):
        ast.parse(Path('plugins/_hardlab_livebridge.py').read_text(),feature_version=(3,5))


if __name__=='__main__':unittest.main()
