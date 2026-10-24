"""Falling columns sense the approach lane, then retain native fall timing."""
import unittest

from test_sc_mechanics import Host, body


class ScColumnApproachTests(unittest.TestCase):
    def host(self):
        return Host({1:body('sc.dropwall',y=-544,w=64,h=544,terraintype=1,removetype=0),
                     2:body('sc.wall',x=-1000,y=512,w=2000,h=32,terraintype=1)})

    def test_approach_triggers_while_still_clear_of_column(self):
        h=self.host()
        # A 30px body at x512 has centre527, 495px from column centre32;
        # it is still 448px from the 64px column's physical edge.
        h.player=dict(xmp=512*256,ymp=384*256,wmp=30*256,hmp=80*256,alive=True)
        h.tick(.01)
        self.assertEqual(h.mechanics.states[1]['phase'],'falling')
        self.assertEqual(h.actors[1]['props']['move'],dict(vel=dict(yaccel=40,maxyvel=50000)))
        self.assertEqual(h.actors[1]['hp'],5000)

    def test_a_player_above_the_socket_does_not_trigger_the_passage(self):
        h=self.host()
        h.player=dict(xmp=0,ymp=-900*256,wmp=30*256,hmp=80*256,alive=True)
        h.tick(.01)
        self.assertEqual(h.mechanics.states[1]['phase'],'waiting')

    def test_nearer_player_above_does_not_mask_eligible_player_in_corridor(self):
        h=self.host()
        h.players={1:dict(xmp=0,ymp=-350*256,wmp=30*256,hmp=80*256,alive=True),
                   2:dict(xmp=460*256,ymp=384*256,wmp=30*256,hmp=80*256,alive=True)}
        h.tick(.01)
        self.assertEqual(h.mechanics.states[1]['phase'],'falling')

    def test_distant_players_do_not_trigger_or_damage_the_waiting_column(self):
        h=self.host()
        h.player=dict(xmp=600*256,ymp=384*256,wmp=30*256,hmp=80*256,alive=True)
        h.tick(.01)
        self.assertEqual(h.mechanics.states[1]['phase'],'waiting')
        self.assertNotIn('hitpoints',h.actors[1]['props'])


if __name__=='__main__':
    unittest.main()
