"""Endless encounters retain living enemies and unacknowledged deaths only."""
import unittest
import base64
from types import SimpleNamespace
from unittest import mock

from localserver import game_settings
from localserver.live.enemies import EnemyManager, UnitList
from test_replay import encounter, input_at


class EnemyRetentionTests(unittest.TestCase):
    def test_pending_deaths_are_destroyed_once_before_reaping_and_index_releases_them(self):
        session,_,_=encounter('egy_hulk')
        dead=SimpleNamespace(objid=100,alive=False,emitted=True,state='removed')
        live=SimpleNamespace(objid=101,alive=True,emitted=False)
        session.enemies.units=UnitList([dead,live])
        self.assertIs(session.enemies.find_unit(100),dead)
        session.enemies.reap()
        self.assertIs(session.enemies.find_unit(100),dead)
        session.batch=mock.Mock();session.unit_render_state={100:{}}
        session._emit_units(.02)
        session.batch.destroy.assert_called_once_with(100,ownerid=1,reason='CANCEL')
        session.enemies.reap()
        self.assertEqual(session.enemies.units,[live])
        self.assertIsNone(session.enemies.find_unit(100))
        session._emit_units(.02)
        session.batch.destroy.assert_called_once()

    def test_victory_retains_survivors_and_targets_protection_to_player_world(self):
        with game_settings.event_snapshot(game_settings.normalize(dict(map='egy_hulk',skip_intro={'egy_hulk':True}))):
            session,bridge,server=encounter('egy_hulk');packets=[]
            server._send_raw=lambda channel,kind,payload,*a,**kw:packets.append((kind,payload))
            session.prepare()
            for tick in range(240):bridge._absorb(input_at(tick));session.step(.05)
            survivors=list(session.enemies.live());self.assertTrue(survivors)
            projectiles=list(session.projectiles.projectiles)
            packets.clear();session.batch.general=[];session.batch.objects=[]
            session._begin_death()
            self.assertTrue(all(unit.alive for unit in survivors))
            self.assertFalse(session.enemies.enabled)
            self.assertEqual(session.enemies.pending,[])
            self.assertEqual(session.projectiles.projectiles,projectiles)
            self.assertEqual(bridge.player_protection,dict(room_seed=session.player.raw.get('room_seed'),
                epoch=session.player.raw.get('player_epoch'),worldid=session.player.worldid,
                objid=session.player.objid,enabled=True))
            self.assertEqual(bridge.acknowledgement(4)['player_protection'],bridge.player_protection)
            self.assertEqual(packets,[],'A native-owned player cannot receive a foreign WorldUpdate')
            before=session.enemies.room_time;session.step(.05)
            self.assertGreater(session.enemies.room_time,before,'Survivors continue simulating')
            session.batch.general=[]
            session._resolve_events([dict(kind='player_damage',value=999,owner=survivors[0].objid)])
            self.assertFalse(any('damage_' in row for row in session.batch.general),'Victory is invulnerable')
            session.phase_time=6;session._step_death(.05,[])
            destroyed={row['destroy_']['objid_'] for row in session.batch.general if 'destroy_' in row}
            self.assertTrue(set(session.rules.TURRET_IDS).issubset(destroyed),'Extra turrets retire with the ship')
            self.assertFalse(any(unit.objid in destroyed for unit in survivors))

    def test_ship_death_animation_payload_is_binary_and_plays_at_captured_duration(self):
        session,_,_=encounter('egy_hulk')
        records=session._load_death_records();animations=[]
        for row in records:
            delta=session.layouts.decode('Delta',row['payload'],raw_strings=True)
            for update in delta['worldupdate_'].get('objectupdates_',[]):
                props=update.get('modifyprops_') or {}
                if update['objid_']==session.rules.SHIP_ID and props.get('property_')==49:
                    raw=base64.b64decode(props['messageval_']['raw'])
                    animations.append(session.layouts.decode('AnimationProperties',raw))
        self.assertEqual(len(animations),10)
        self.assertEqual(animations,[dict(animid_=1,time_=528)]*10)
        session._legacy_victory_presentation=True
        legacy=session._load_death_records()
        self.assertNotEqual(legacy,records,'Historical journals retain their original packet bytes')

    def test_ten_thousand_completed_deaths_do_not_accumulate(self):
        session,_,_=encounter('egy_hulk')
        for start in range(0,10000,100):
            session.enemies.units.extend(SimpleNamespace(objid=i,alive=False,emitted=False) for i in range(start,start+100))
            session.enemies.reap()
            self.assertEqual(len(session.enemies.units),0)

    def test_reaping_preserves_encounter_packets_randomness_and_refills(self):
        def run(reap):
            with game_settings.event_snapshot(game_settings.normalize(dict(map='egy_hulk',skip_intro={'egy_hulk':True}))):
                session,bridge,server=encounter('egy_hulk')
                packets=[];server._send_raw=lambda c,k,p,*a,**kw:packets.append((k,p))
                with mock.patch.object(EnemyManager,'reap',reap):
                    session.prepare()
                    deaths=0
                    for tick in range(1200):
                        bridge._absorb(input_at(tick))
                        if tick%10==0:
                            for unit in session.enemies.live():
                                if unit.emitted:
                                    session.enemies.apply_damage(unit.objid,10**8);deaths+=1
                        session.step(.05)
                return packets,session.enemies.random.getstate(),session.enemies.next_objid,deaths,len(session.enemies.units)
        old=run(lambda _:None);new=run(EnemyManager.reap)
        self.assertGreater(new[3],10)
        self.assertEqual(old[:4],new[:4])
        self.assertLess(new[4],old[4])


if __name__=='__main__':unittest.main()
