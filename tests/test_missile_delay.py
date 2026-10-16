"""Delayed steering uses life-scoped position history, not a slower turn timer."""
import json
import math
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

from localserver import game_settings
from localserver.live import constants
from localserver.maps.hulk_normal.rules import HulkNormalRules
from localserver.live.bridge import PlayerState
from localserver.live.projectiles import Projectile, ProjectileSystem
from localserver.participants import life_identity
from tools.gear_editor import apply_import_document
from tools.web_workspace import Workspace


class MissileDelayTests(unittest.TestCase):
    def system(self, rules=constants):
        room = SimpleNamespace(objects={},bounds=lambda:(-10000000,-10000000,20000000,20000000))
        return ProjectileSystem(room,rules=rules)

    def player(self, x, y=0, objid=1, life=1):
        return PlayerState(worldid=1,objid=objid,life_id=life,xmp=x,ymp=y,
                           wmp=2,hmp=2,hp=100,maxhp=100)

    def test_graveyard_default_spawn_rate_and_custom_values_are_preserved(self):
        for map_id in ('egy_hulk','hulk_normal'):
            self.assertEqual(game_settings.balance(map_id,{})['spawn_rate'],.66)
            self.assertEqual(game_settings.balance(map_id,{'balance':{map_id:{'spawn_rate':1.5}}})['spawn_rate'],1.5)
        self.assertEqual(game_settings.balance('hulk_mid',{})['spawn_rate'],1.)

    def test_settings_defaults_limits_and_scope(self):
        for map_id in ('egy_hulk','hulk_normal'):
            self.assertEqual(game_settings.missile_delay_ms(map_id,{}),275)
            for value,expected in ((0,0),(500,500),(-5,0),(999,500),('125',125),
                                   (None,275),(True,275),(math.nan,275),(math.inf,275)):
                settings=game_settings.normalize({'missile_delay_ms':{map_id:value,'hulk_mid':400}})
                self.assertEqual(game_settings.missile_delay_ms(map_id,settings),expected)
                self.assertNotIn('hulk_mid',settings['missile_delay_ms'])
        self.assertEqual(game_settings.missile_delay_ms('hulk_mid',{}),0)

    def test_interpolation_and_zero_delay(self):
        system=self.system()
        for i in range(1,21):
            player=self.player(i*100)
            system.step(.05,player,[],resolve_player=False)
        system.missile_delay_seconds=.275
        self.assertAlmostEqual(system._delayed_center(player)[0],1451)
        system.missile_delay_seconds=.5
        self.assertAlmostEqual(system._delayed_center(player)[0],1001)
        system.missile_delay_seconds=0
        self.assertEqual(system._delayed_center(player),player.center())
        self.assertLessEqual(len(next(iter(system.target_history.values()))),12)

    def test_lives_players_and_missing_history_do_not_mix(self):
        system=self.system();system.missile_delay_seconds=.5
        first=self.player(100);second=self.player(9000,objid=2)
        system.step(.05,first,[],resolve_player=False,players=(first,second))
        self.assertEqual(system._delayed_center(second),second.center())
        moved=self.player(500)
        system.step(.05,moved,[],resolve_player=False)
        self.assertEqual(system._delayed_center(moved),first.center())
        self.assertNotIn(life_identity(second),system.target_history)
        respawn=self.player(8000,life=2)
        system.step(.05,respawn,[],resolve_player=False)
        self.assertEqual(system._delayed_center(respawn),respawn.center())
        self.assertNotIn(life_identity(first),system.target_history)
        system.step(.05,None,[],resolve_player=False,players=())
        self.assertFalse(system.target_history)

    def missile(self, system, kind):
        if kind=='boss':return system.fire_bossmissile(9,(0,0),0)
        shot=system.rules.ENEMY_SHOTS['smissile']
        return system.fire_enemy_shot(10,'smissile',(0,0),0,shot['damage'],shot['speed'],
            homing=shot['homing'],turn_rate=shot['turn_rate'],accel=shot['accel'],max_speed=shot['max_speed'])

    def test_boss_and_mob_turn_towards_history_every_50ms_in_both_maps(self):
        for rules in (constants,HulkNormalRules()):
            for kind in ('boss','mob'):
                with self.subTest(map=type(rules).__name__,kind=kind):
                    system=self.system(rules);system.missile_delay_seconds=.275
                    old=self.player(100000,-400000)
                    for _ in range(10):system.step(.05,old,[],resolve_player=False)
                    current=self.player(100000,100000)
                    missile=self.missile(system,kind)
                    # Acquire using current range, even though the old position is outside it.
                    missile.lock_range=150000
                    system.step(.05,current,[],resolve_player=False)
                    self.assertTrue(missile.target_locked)
                    self.assertAlmostEqual(math.degrees(missile.steering_angle),-5)
                    system.step(.05,current,[],resolve_player=False)
                    self.assertAlmostEqual(math.degrees(missile.steering_angle),-10)
                    system.missile_delay_seconds=0
                    system.step(.05,current,[],resolve_player=False)
                    self.assertAlmostEqual(math.degrees(missile.steering_angle),-5)

    def test_enemy_homing_missiles_share_one_delay_without_delaying_other_shots(self):
        for delay in (0,.275,.5):
            with self.subTest(delay=delay):
                system=self.system();system.missile_delay_seconds=delay
                old=self.player(100000,-100000);current=self.player(100000,100000)
                system.step(.5,old,[],resolve_player=False)
                self.missile(system,'boss');self.missile(system,'mob')
                future=self.missile(system,'mob');future.vid='other.enemy.seeking'
                friendly=self.missile(system,'boss');friendly.owner_type=1;friendly.vid='friendly.seeking'
                straight=system.fire_enemy_shot(9,'rpg',(0,0),0,30,39062.5)
                calls={}
                def capture(projectile,dt,target=None,**kwargs):
                    calls[projectile.objid]=(target,kwargs.get('lock_target'))
                with mock.patch.object(Projectile,'step',capture):
                    system.step(.05,current,[],resolve_player=False)
                expected=system._delayed_center(current)
                for missile in system.projectiles[:3]:
                    self.assertEqual(calls[missile.objid],(expected,current.center()))
                self.assertEqual(calls[friendly.objid],(current.center(),None))
                self.assertEqual(calls[straight.objid],(None,None))

    def test_delayed_boss_and_mob_missiles_still_hit_the_current_player(self):
        for kind in ('boss','mob'):
            with self.subTest(kind=kind):
                system=self.system();system.missile_delay_seconds=.275
                old=self.player(100000,-100000)
                system.step(.5,old,[],resolve_player=False)
                current=self.player(1000,0)
                self.missile(system,kind)
                events=[];system.step(.05,current,events)
                hits=[event for event in events if event['kind']=='player_damage']
                self.assertTrue(hits)
                self.assertEqual(hits[0]['player_key'],life_identity(current))

    def test_browser_save_materialize_import_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace=Workspace(Path(directory)/'web',Path(directory)/'absent')
            profile=workspace.read()
            self.assertEqual(profile['settings']['missile_delay_ms'],{'egy_hulk':275,'hulk_normal':275})
            profile['settings']['missile_delay_ms']={'egy_hulk':0,'hulk_normal':500}
            saved=workspace.save(profile);workspace.materialize('hulk_normal')
            effective=json.loads((workspace.directory/'game_settings.json').read_text())
            self.assertEqual(effective['missile_delay_ms'],saved['settings']['missile_delay_ms'])
            other=Workspace(Path(directory)/'other',Path(directory)/'absent')
            imported=dict(saved,revision=0)
            self.assertEqual(other.save(imported)['settings']['missile_delay_ms'],effective['missile_delay_ms'])
            restored=workspace.restore_defaults(saved['revision'])
            self.assertEqual(restored['settings']['missile_delay_ms'],{'egy_hulk':275,'hulk_normal':275})
            _overrides,settings=apply_import_document({'_missile_delay_ms':{'egy_hulk':123,'hulk_normal':456}}, {}, {})
            self.assertEqual(settings['missile_delay_ms'],{'egy_hulk':123,'hulk_normal':456})
