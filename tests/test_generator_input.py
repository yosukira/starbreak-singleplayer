"""The normal keyboard/controller feedback path must open GY/EGY panels."""
from types import SimpleNamespace
import unittest

from localserver.live.bridge import PlayerState
from plugins._hardlab_livebridge import LiveBridge
from test_replay import encounter


class GeneratorInputTests(unittest.TestCase):
    def test_consumed_controller_bit_survives_once_until_release(self):
        bridge=LiveBridge.__new__(LiveBridge)
        bridge._input_interact_down=False;bridge._input_interact_edge=False;bridge._old_interact=False
        bridge.interact_down=lambda _:False
        bridge.observe_input(128,True)
        self.assertEqual(bridge.interact_edge(),(False,True))
        bridge.observe_input(128,True)
        self.assertEqual(bridge.interact_edge(),(False,False))
        bridge.observe_input(128,False);bridge.observe_input(128,True)
        self.assertEqual(bridge.interact_edge(),(False,True))
        bridge.interact_down=lambda _:True
        self.assertEqual(bridge.interact_edge(),(True,False),'A later totalInput sample cannot duplicate the same press')

    def test_native_combined_controller_input_does_not_depend_on_the_keyboard_binding(self):
        bridge=LiveBridge.__new__(LiveBridge)
        world=SimpleNamespace(total=128)
        bridge._current_world=lambda:world
        bridge.ffi=SimpleNamespace(NULL=None)
        keyboard=bytearray(512)
        bridge.key_states=lambda:(keyboard,len(keyboard))
        bridge.physical_input_mask=lambda:0
        bridge._old_interact=False
        for scancode in (0,15,8):
            self.assertTrue(bridge.interact_down(scancode),'Controller uses the engine mask even with a custom keyboard binding')
        self.assertEqual(bridge.interact_edge(),(True,True))
        self.assertEqual(bridge.interact_edge(),(True,False))
        world.total=0;self.assertEqual(bridge.interact_edge(),(False,False))
        keyboard[8]=1;self.assertEqual(bridge.interact_edge(8),(True,True))

    def test_panel_opens_after_a_short_press_and_release_between_server_ticks(self):
        for map_id in ('egy_hulk','hulk_normal'):
            for web in (False,True):
                with self.subTest(map=map_id,web=web):
                    session,bridge,_=encounter(map_id);session.options.web_client=web
                    cycle=session.gens;cycle.start();cycle._light_panel()
                    x,y,w,h=session.room.object(cycle.active_panel).rect()
                    player=dict(objid=1,worldid=2,xmp=x+w//2-3840,ymp=y+h-17920,
                        wmp=7680,hmp=17920,hp=150,maxhp=150,alive=True,vid='soldier')
                    session.player=PlayerState(**player)
                    cycle.step(session.rules.PANEL_ACTIVATION_DELAY+.01)
                    self.assertTrue(cycle.panel_interactable)
                    for seq,down in ((1,True),(2,False)):
                        bridge._absorb(dict(seq=seq,player=player,keys=dict(interact=down,interact_edge=down)))
                    session._sync_player();cycle.step(.016)
                    self.assertEqual(cycle.panel_state,'activating')
                    for _ in range(120):
                        bridge._absorb(dict(seq=3+_,player=player,keys=dict(interact=False)))
                        session._sync_player();cycle.step(.016)
                    self.assertEqual(cycle.gen_state,'armed')
                    self.assertTrue(cycle.apply_damage(cycle.active_gen,100)['hp']<session.rules.GEN_HP)


if __name__=='__main__':unittest.main()
