"""Guardian progress survives handoff without singleplayer equipment chests."""
import unittest

import test_sc_run as run_tests
from localserver.maps.sc_run.mechanic_helpers import place


class ScChestHandoffTests(unittest.TestCase):
    def room(self):
        helper=run_tests.ScRunTests()
        run,clock=helper.make_run(seed=5)
        role=next(name for name,r in run.pack.plan['rooms'].items()
                  if r['metadata']['template']=='scend02')
        wrapper,server=helper.open_room(run,role)
        guardian=wrapper.instance.recipe['metadata']['chest_guardian']
        row=wrapper.current.actors[guardian]
        point=(round(row['pos'][0]+128*256),round(row['pos'][1]-90*256))
        place(wrapper.current,guardian,point)
        wrapper.bridge.events.append(dict(kind='damage',victim=guardian,value=row['hp'],seq=101))
        return helper,run,clock,wrapper,server,guardian,point

    @staticmethod
    def rewards(wrapper,server):
        return [wrapper.layouts.decode('Delta',payload)['createloot_']
                for kind,payload,label in server.sent if kind==12 and label=='sc-chest-equipment']

    def test_guardian_progress_persists_without_equipment_on_kill_or_return(self):
        helper,run,clock,wrapper,server,guardian,point=self.room()
        wrapper.step(.02)
        self.assertNotIn(guardian,wrapper.current.actors)
        reward=self.rewards(wrapper,server)
        self.assertEqual(reward,[])
        self.assertTrue(wrapper.instance.flags['chest_cleared'])
        self.assertEqual(wrapper.instance.flags['chest_drop_position_mp'],point)
        role=wrapper.instance.role
        helper.open_room(run,'entry',server)
        clock.advance(121.)
        returned,_=helper.open_room(run,role,server)
        returned.step(.02)
        self.assertEqual(self.rewards(returned,server),[])
        self.assertTrue(returned.instance.flags['chest_cleared'])
        self.assertEqual(returned.instance.flags['chest_drop_position_mp'],point)

    def test_same_tick_inventory_commit_preserves_progress_without_equipment(self):
        _helper,run,_clock,wrapper,server,_guardian,point=self.room()
        observed=[]

        def commit_handoff(_channel,session):
            observed.extend(self.rewards(session,server))
            session.suppress_send=True
            return True

        server.poll_live_room_inventory=commit_handoff
        wrapper.step(.02)
        self.assertEqual(observed,[])
        self.assertTrue(wrapper.instance.flags['chest_cleared'])
        self.assertFalse(run.chests_rewarded)

    def test_same_tick_walked_door_preserves_progress_without_equipment(self):
        helper,run,_clock,wrapper,server,_guardian,point=self.room()
        oid,link=next(iter(wrapper.instance.recipe['links'].items()))
        trigger=wrapper.boundary_properties(oid,link)
        helper.feedback(wrapper,(trigger['xmp_'],trigger['ymp_']))
        wrapper._boundary_armed.add(oid)
        observed=[]

        def transition(_channel,session,_link,_player):
            observed.extend(self.rewards(session,server))
            session.suppress_send=True
            return True

        server.request_live_room_transition=transition
        wrapper.step(.02)
        self.assertTrue(wrapper.suppress_send)
        self.assertEqual(observed,[])
        self.assertTrue(wrapper.instance.flags['chest_cleared'])


if __name__=='__main__':
    unittest.main()
