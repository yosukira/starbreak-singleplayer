"""Client HP death is final for GY/EGY mechs, without duplicate rewards."""
import unittest
from unittest import mock

from test_replay import encounter


class NativeMechDeathTests(unittest.TestCase):
    def test_mech_hp_destroy_retires_once_even_when_server_still_has_hp(self):
        for map_id in ('egy_hulk','hulk_normal'):
            for web in (False,True):
                with self.subTest(map=map_id,web=web):
                    session,bridge,_=encounter(map_id);session.options.web_client=web
                    unit=session.enemies.spawn('hulk.eloader',portal_id=session.enemies.portal_ids[0],position=(0,0))
                    unit.emitted=True;unit.hp=1000
                    session.batch=mock.Mock();session._maybe_drop_supply=mock.Mock()
                    session._spawn_loader_survivor=mock.Mock();session.battle_recorder=mock.Mock()
                    death=dict(kind='destroy',ownerid=session.worldid,victim=unit.objid,reason=2)
                    bridge.poll_events=mock.Mock(return_value=[death,death,dict(kind='damage',ownerid=session.worldid,victim=unit.objid,value=50)])
                    session._apply_bridge_events([])
                    self.assertFalse(unit.alive);self.assertFalse(unit.emitted);self.assertEqual(unit.hp,0)
                    self.assertEqual(len(session.enemies.portal_refills),1)
                    session._maybe_drop_supply.assert_called_once()
                    session._spawn_loader_survivor.assert_called_once()
                    session.battle_recorder.damage.assert_called_once_with(0,killed=True)
                    session.batch.destroy.assert_not_called()
                    session.batch.modify.assert_not_called()

    def test_other_world_non_hp_removals_and_unemitted_units_do_not_award_a_kill(self):
        session,bridge,_=encounter('egy_hulk')
        unit=session.enemies.spawn('hulk.scavengerloader',position=(0,0));unit.emitted=True
        for event in (dict(ownerid=999,reason=2),dict(ownerid=session.worldid,reason=6),dict(ownerid=session.worldid,reason=7)):
            bridge.poll_events=mock.Mock(return_value=[dict(event,kind='destroy',victim=unit.objid)])
            session._apply_bridge_events([]);self.assertTrue(unit.alive)
        unit.emitted=False
        bridge.poll_events=mock.Mock(return_value=[dict(kind='destroy',ownerid=session.worldid,victim=unit.objid,reason=2)])
        session._apply_bridge_events([]);self.assertTrue(unit.alive)

    def test_intro_mech_death_preserves_one_pilot_and_one_supply_roll(self):
        session,bridge,_=encounter('egy_hulk');session.prepare()
        oid=next(iter(session.intro_units));unit=session.intro_units[oid]
        session.batch=mock.Mock();session._spawn_loader_survivor=mock.Mock();session._maybe_drop_supply=mock.Mock()
        bridge.poll_events=mock.Mock(return_value=[dict(kind='destroy',ownerid=session.worldid,victim=oid,reason=2)]*2)
        session._apply_bridge_events([])
        self.assertEqual(unit['hp'],0);self.assertEqual(session.intro_destroyed.count(oid),1)
        session._spawn_loader_survivor.assert_called_once();session._maybe_drop_supply.assert_called_once()


if __name__=='__main__':unittest.main()
