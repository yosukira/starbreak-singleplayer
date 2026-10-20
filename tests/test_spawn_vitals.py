"""Starting vitals must include equipped bonuses on both client transports."""
import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest

from localserver.server import LocalServer, parse_args

ROOT = Path(__file__).resolve().parents[1]


class SpawnVitalsTests(unittest.TestCase):
    def test_equipped_max_hp_starts_full_without_double_counting_current_hp_affix(self):
        original = json.loads((ROOT/'data/defaults/class_loadouts.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory)/'loadouts.json'
            for web in (False, True):
                for shell in (1, 2, 3, 5):
                    with self.subTest(web=web, shell=shell):
                        rows = copy.deepcopy(original)
                        boots = next(item for item in rows[str(shell)]['items_'] if item['slotpos_']==1280)
                        boots['statboosts_'] += [dict(stat_=1,val_=9850),dict(stat_=2,val_=9850)]
                        file.write_text(json.dumps(rows),encoding='utf8')
                        args = ['--map','hulk_mid','--create-world-hex',str(ROOT/'maps/hulk_mid/createworld.hex'),
                                '--class-loadouts',str(file),'--player-type',str(shell)]
                        if web:args.append('--web-client')
                        with contextlib.redirect_stdout(io.StringIO()):
                            server=LocalServer(parse_args(args))
                        player=server.layouts.decode('Delta',server.player_delta_blob)['createplayer_']['charprops_']
                        # Compare against the same class with the same gear before
                        # the two added modifiers; current HP must rise exactly once.
                        baseline=copy.deepcopy(rows[str(shell)])
                        next(item for item in baseline['items_'] if item['slotpos_']==1280)['statboosts_'] = boots['statboosts_'][:-2]
                        server.class_loadouts[shell]=baseline
                        with contextlib.redirect_stdout(io.StringIO()):
                            normal=server.layouts.decode('Delta',server._load_player_delta(server.options))['createplayer_']['charprops_']
                        self.assertEqual(player['hitpoints_'],normal['hitpoints_']+9850)
                        self.assertEqual(player['maxhitpoints_'],normal['maxhitpoints_'],'Client applies equipment to base maximum')
                        self.assertLess(player['hitpoints_'],normal['hitpoints_']+19700)

    def test_explicit_current_value_is_preserved_for_direct_server_loadouts(self):
        server=LocalServer.__new__(LocalServer)
        server.character_library={1:{"type_":1}}
        server.item_descriptions={}
        message=dict(charprops=dict(hitpoints=17,maxhitpoints=130,items=[]))
        server._fill_loadout_vitals(message,dict(type_=1,hitpoints_=17),1)
        self.assertEqual(message['charprops']['hitpoints'],17)


if __name__=='__main__':unittest.main()
