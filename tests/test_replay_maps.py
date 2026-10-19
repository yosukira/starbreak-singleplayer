"""Real encounter adapters must reproduce packets, not just a mock clock."""
import json
from types import SimpleNamespace
import unittest

from localserver import game_settings
from localserver.replay import ServerCapture, ServerReplay, SUPPORTED_MAPS
from localserver.supply import SupplyDrops
from test_replay import encounter, input_at


class OtherMapReplayTests(unittest.TestCase):
    def test_other_encounters_preserve_rolled_rooms_and_continue_exact_packets(self):
        for map_id in SUPPORTED_MAPS:
            if map_id in ('egy_hulk','hulk_normal','lab_hard','lab_mid'):continue
            for room_seed in (None, 98765):
                with self.subTest(map=map_id,room_seed=room_seed), game_settings.event_snapshot(
                        game_settings.normalize(dict(map=map_id))):
                    session,bridge,server=encounter(map_id,room_seed,worldid=7)
                    capture=ServerCapture(session);capture.run('prepare')
                    for tick in range(720):
                        bridge._absorb(input_at(tick));capture.run('step',.05)
                    self.assertIsNone(capture.failed)
                    document=json.loads(json.dumps(capture.document()))
                    self.assertEqual(document.get('room_seed'),None if room_seed is None else str(room_seed))
                    rebuilt=ServerReplay(document)
                    while rebuilt.advance():pass
                    self.assertEqual(rebuilt.session.worldid,7)
                    self.assertEqual(rebuilt.session.room_time,session.room_time)
                    self.assertEqual(rebuilt.session.phase,session.phase)
                    self.assertGreater(sum(frame['packets'] for frame in capture.frames),600)
                    capture.detach()
                    original_packets=[];restored_packets=[]
                    session.server=SimpleNamespace(**dict(vars(server),_send_raw=lambda c,k,p,*a,**kw:original_packets.append((k,p))))
                    restored=rebuilt.continue_live(bridge,server,None)
                    restored.server=SimpleNamespace(**dict(vars(server),_send_raw=lambda c,k,p,*a,**kw:restored_packets.append((k,p))))
                    for tick in range(720,840):
                        bridge._absorb(input_at(tick));session.step(.05)
                        bridge._absorb(input_at(tick));restored.step(.05)
                    self.assertEqual(original_packets,restored_packets)

    def test_supply_rolls_are_reproducible_and_independent_of_enemy_rng(self):
        first=SupplyDrops(SimpleNamespace(seed=123),'city_mid')
        second=SupplyDrops(SimpleNamespace(seed=123),'city_mid')
        other=SupplyDrops(SimpleNamespace(seed=124),'city_mid')
        rolls=[first.rng.random() for _ in range(100)]
        self.assertEqual(rolls,[second.rng.random() for _ in range(100)])
        self.assertNotEqual(rolls,[other.rng.random() for _ in range(100)])
