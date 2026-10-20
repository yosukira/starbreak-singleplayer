"""Endless encounters retain living enemies and unacknowledged deaths only."""
import unittest
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
