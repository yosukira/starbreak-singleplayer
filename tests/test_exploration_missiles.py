"""Exploration controller uses the shared life-scoped missile steering path."""
import math
import random
from types import SimpleNamespace
import unittest
from unittest import mock

from localserver import game_settings
from localserver.live.bridge import PlayerState
from localserver.maps.egy_run.enemies import ExplorationEnemies
from localserver.participants import life_identity


class ExplorationMissileTests(unittest.TestCase):
    def player(self,x,y,objid=1,life=1):
        return PlayerState(worldid=4,objid=objid,life_id=life,xmp=x,ymp=y,
                           wmp=2,hmp=2,hp=100,maxhp=100)

    def host(self):
        host=SimpleNamespace(map_id='egy_run',actors={},players={},player=None,
            rng=random.Random(123),room_time=0.,surfaces=lambda:(),batch=mock.Mock())
        return host,ExplorationEnemies(host)

    def tick(self,host,enemies,players,dt=.05):
        host.players={life_identity(p):p for p in players}
        host.room_time+=dt
        enemies.step(dt)

    def missile(self,enemies):
        rules=enemies.projectiles.rules.ENEMY_SHOTS['smissile']
        return enemies.projectiles.fire_enemy_shot(10,'smissile',(0,0),0,
            rules['damage'],rules['speed'],homing=True,turn_rate=rules['turn_rate'],
            accel=rules['accel'],max_speed=rules['max_speed'])

    def test_full_run_reuses_the_graveyard_setting_without_new_profile_keys(self):
        for value in (0,125,275,500):
            document={'missile_delay_ms':{'hulk_normal':value}}
            self.assertEqual(game_settings.missile_delay_ms('egy_run',document),value)
            self.assertEqual(set(game_settings.normalize(document)['missile_delay_ms']),
                             {'hulk_normal','egy_hulk'})

    def test_controller_turns_towards_history_but_acquires_current_position(self):
        with game_settings.event_snapshot({'missile_delay_ms':{'hulk_normal':275}}):
            host,enemies=self.host()
            old=self.player(100000,-400000)
            for _ in range(10):self.tick(host,enemies,[old])
            current=self.player(100000,100000)
            missile=self.missile(enemies);missile.lock_range=150000
            self.tick(host,enemies,[current])
            self.assertTrue(missile.target_locked,'Current player is inside acquisition range')
            self.assertAlmostEqual(math.degrees(missile.steering_angle),-5.)
            self.assertEqual(missile.target_player_key,life_identity(current))
            self.assertTrue(enemies.projectiles.target_history)
            self.tick(host,enemies,[current])
            self.assertAlmostEqual(math.degrees(missile.steering_angle),-10.)

    def test_zero_delay_and_live_setting_change_use_current_position(self):
        with game_settings.event_snapshot({'missile_delay_ms':{'hulk_normal':275}}):
            host,enemies=self.host();old=self.player(50000,-50000)
            for _ in range(10):self.tick(host,enemies,[old])
            missile=self.missile(enemies);current=self.player(50000,50000)
            self.tick(host,enemies,[current])
            self.assertAlmostEqual(math.degrees(missile.steering_angle),-5.)
        with game_settings.event_snapshot({'missile_delay_ms':{'hulk_normal':0}}):
            self.tick(host,enemies,[current])
            self.assertAlmostEqual(math.degrees(missile.steering_angle),0.)
            self.assertEqual(enemies.projectiles.missile_delay_seconds,0.)

    def test_closer_second_player_does_not_replace_locked_life(self):
        with game_settings.event_snapshot({'missile_delay_ms':{'hulk_normal':275}}):
            host,enemies=self.host();first=self.player(50000,-50000)
            for _ in range(10):self.tick(host,enemies,[first])
            missile=self.missile(enemies);self.tick(host,enemies,[first])
            other=self.player(5000,5000,objid=2)
            for _ in range(3):self.tick(host,enemies,[first,other])
            self.assertEqual(missile.target_player_key,life_identity(first))
            self.assertEqual(set(enemies.projectiles.target_history),
                             {life_identity(first),life_identity(other)})

    def test_empty_roster_does_not_restore_stale_fallback_and_respawn_has_fresh_history(self):
        with game_settings.event_snapshot({'missile_delay_ms':{'hulk_normal':275}}):
            host,enemies=self.host();old=self.player(10000,-20000)
            for _ in range(10):self.tick(host,enemies,[old])
            host.player=old
            self.tick(host,enemies,[])
            self.assertFalse(enemies.projectiles.target_history)
            new=self.player(50000,50000,life=2)
            self.tick(host,enemies,[new])
            self.assertEqual(enemies.projectiles._delayed_center(new),new.center())
            self.assertEqual(set(enemies.projectiles.target_history),{life_identity(new)})


if __name__=='__main__':unittest.main()
