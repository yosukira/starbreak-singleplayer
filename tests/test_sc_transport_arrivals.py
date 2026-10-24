"""Exercise the transport paths that actually write native CreatePlayer.pos."""
from types import SimpleNamespace
import unittest
from unittest import mock

import test_sc_run as run_test
from localserver.maps.run_common.ground_host import GroundEgressHost
from localserver.maps.sc_run.doors import overlaps
from localserver.maps.sc_run.physics import surface
from localserver.maps.sc_run.session import ScRunLiveSession
from localserver.server import LocalServer


class ScTransportArrivalTests(unittest.TestCase):
    def destination(self):
        helper = run_test.ScRunTests()
        run, _clock = helper.make_run(seed=5)
        wrapper, _server = helper.open_room(run, 'entry')
        instance = next(room for room in run.rooms.values()
                        if room.recipe['metadata']['template'] == 'scmid08')
        port = next(p for p in instance.recipe['metadata']['ports'] if p['id'] == 1)
        return helper, run, wrapper, instance, port

    def assert_landing(self, instance, port, point, size):
        x, y = point
        w, h = size
        self.assertFalse(overlaps((x,y,w,h), instance.recipe['objects'][port['trigger_id']]))
        tops = [surface(tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp')),
                        p.get('shapetype',0), x, w)
                for p in instance.recipe['objects'].values() if p.get('terraintype') in (1,2)]
        self.assertTrue(any(top is not None and abs(top-y-h) <= 1 for top in tops),
                        'Native CreatePlayer must stand on the entrance ledge')

    def test_actual_server_transition_serializes_sc_landing_on_the_high_ledge(self):
        _helper, run, wrapper, instance, port = self.destination()
        layouts = wrapper.layouts
        player, _keys = wrapper.bridge.snapshot()
        created = dict(createplayer_=dict(charprops_=dict(hitpoints_=500)))
        server = SimpleNamespace(options=SimpleNamespace(map='sc_run',player_type=1),
            level_run=run, _restart_in_progress=False, live_session=wrapper, layouts=layouts,
            _distinct_player_world_blob=lambda blob:blob,
            player_delta_blob=layouts.encode('Delta',created),
            _load_bootstrap_delta=lambda options:None,
            _request_pvid_loads=mock.Mock())
        link = dict(destination=instance.role,entry=port['entry'])
        with mock.patch('localserver.server.log'):
            self.assertTrue(LocalServer._commit_live_room_transition(server,None,wrapper,link,player))
        actual = layouts.decode('Delta',server.player_delta_blob)['createplayer_']['pos_']
        point = actual['xmp_'], actual['ymp_']
        self.assertEqual(point,ScRunLiveSession.entry_position(run,instance.role,port['entry'],player.wmp,player.hmp))
        self.assert_landing(instance,port,point,(actual['wmp_'],actual['hmp_']))
        self.assertEqual(actual['ymp_']+actual['hmp_'],736*256)

    def test_final_ground_commit_uses_the_same_map_specific_native_landing(self):
        _helper, run, wrapper, instance, port = self.destination()
        player, _keys = wrapper.bridge.snapshot()
        captured = {}

        class PlayerSerialized(Exception):
            pass

        def encode(kind, message):
            captured.update(message['createplayer_']['pos_'])
            raise PlayerSerialized()

        transaction = SimpleNamespace(candidate=lambda:dict(create_player=dict(charprops_={}),
            vitals=dict(wmp=player.wmp,hmp=player.hmp)))
        host = SimpleNamespace(run=run,source_role=run.current,source_entry=run.entry,
            source=wrapper,source_token=wrapper._input_serial,tx=transaction,purpose='room',
            link=dict(destination=instance.role,entry=port['entry']),options=SimpleNamespace(map='sc_run'),
            server=SimpleNamespace(player_delta_blob=b'',layouts=SimpleNamespace(
                decode=lambda *args:{},encode=encode)))
        # Stop after the actual native position has been serialized; no ground
        # checkpoint commit, network send or native client is needed here.
        with self.assertRaises(PlayerSerialized):
            GroundEgressHost._commit(host,0.)
        point = captured['xmp_'],captured['ymp_']
        self.assertEqual(point,ScRunLiveSession.entry_position(run,instance.role,port['entry'],player.wmp,player.hmp))
        self.assert_landing(instance,port,point,(captured['wmp_'],captured['hmp_']))


if __name__ == '__main__':
    unittest.main()
