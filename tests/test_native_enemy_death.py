"""Native HP retirement must stop invisible shooters and engineer summons."""
import unittest
import contextlib
import io
from unittest import mock

from localserver.live.bridge import PlayerState
from localserver.live.enemies import BEHAVIOUR
from test_replay import encounter, input_at
from localserver import game_settings
from localserver.replay import FULL_RUN_MAPS, SUPPORTED_MAPS
from localserver.server import _map_session_class
from test_run_replay import WholeRun


class NativeEnemyDeathTests(unittest.TestCase):
    def setup_enemy(self, vid, map_id='egy_hulk', web=False):
        session, bridge, _ = encounter(map_id)
        session.options.web_client = web
        floor = session.room.object(session.rules.ARENA_FLOOR_ID).rect()
        h = BEHAVIOUR[vid]['size'][1]
        unit = session.enemies.spawn(vid, position=(floor[0]+100*256, floor[1]-h))
        unit.emitted = True
        player = PlayerState(objid=1, worldid=2, hp=150, alive=True,
                            xmp=round(unit.x+200*256), ymp=round(unit.y),
                            wmp=40*256, hmp=80*256)
        session._maybe_drop_supply = mock.Mock()
        session._spawn_loader_survivor = mock.Mock()
        session.battle_recorder = mock.Mock()
        return session, bridge, unit, player

    def native_death(self, session, bridge, unit, **changes):
        event = dict(kind='destroy', ownerid=session.worldid,
                     victim=unit.objid, reason=2)
        event.update(changes)
        bridge.events.append(event)
        session._apply_bridge_events([])

    def test_all_emitted_enemy_hp_deaths_retire_once_without_damage_feedback(self):
        for map_id in ('egy_hulk', 'hulk_normal'):
            for web in (False, True):
                for vid in BEHAVIOUR:
                    with self.subTest(map=map_id, web=web, vid=vid):
                        s, b, unit, _ = self.setup_enemy(vid, map_id, web)
                        self.native_death(s, b, unit)
                        self.native_death(s, b, unit)
                        self.assertFalse(unit.alive)
                        self.assertFalse(unit.emitted)
                        self.assertEqual(unit.hp, 0)
                        s.battle_recorder.damage.assert_called_once_with(0, killed=True)
                        self.assertEqual(s._maybe_drop_supply.call_count, 0 if unit.flying else 1)

    def test_retired_shooters_never_release_their_committed_windups(self):
        for vid in ('hulk.rpgscavenger', 'hulk.scavengershotgunner',
                    'hulk.scavengermissiler', 'hulk.scavengercommando'):
            with self.subTest(vid=vid):
                s, b, unit, player = self.setup_enemy(vid)
                unit._begin_attack(0., player, [])
                self.assertTrue(unit.emits)
                self.native_death(s, b, unit)
                events = []
                for _ in range(200):
                    s.enemies.step(.02, player, events)
                self.assertFalse(any(e['kind'] == 'enemy-shot' for e in events))

    def test_dead_engineer_cannot_summon_or_leave_an_invisible_firing_child(self):
        for existing_child in (False, True):
            with self.subTest(existing_child=existing_child):
                s, b, engineer, player = self.setup_enemy('hulk.scavengerengineer')
                engineer.summon_timer = 0.
                if existing_child:
                    child = s.enemies.spawn('hulk.bdrone', position=(engineer.x, engineer.y))
                    child.emitted = True
                    engineer.summoned_objid = child.objid
                    child._begin_attack(0., player, [])
                self.native_death(s, b, engineer)
                events = []
                for _ in range(200):
                    s.enemies.step(.02, player, events)
                self.assertFalse(any(e['kind'] in ('enemy-shot', 'enemy-summon') for e in events))
                self.assertEqual(s.enemies.live(), [])

    def test_other_world_non_hp_and_unemitted_deaths_do_not_remove_a_live_enemy(self):
        s, b, unit, _ = self.setup_enemy('hulk.rpgscavenger')
        for event in (dict(ownerid=999), dict(reason=6), dict(reason=7)):
            self.native_death(s, b, unit, **event)
            self.assertTrue(unit.alive)
        unit.emitted = False
        self.native_death(s, b, unit)
        self.assertTrue(unit.alive)
        s.battle_recorder.damage.assert_not_called()

    def test_shots_already_in_flight_survive_their_shooters_death(self):
        s, b, unit, _ = self.setup_enemy('hulk.rpgscavenger')
        s._fire_enemy_shot(dict(owner=unit.objid, vid='hulk.rpg',
                               origin=unit.center(), aim=0))
        projectile = s.projectiles.projectiles[-1]
        self.assertTrue(projectile.alive)
        self.native_death(s, b, unit)
        self.assertFalse(unit.alive)
        self.assertTrue(projectile.alive)
        self.assertIn(projectile.objid, s.emitted_projectiles)

    def test_living_rpg_shooter_still_releases_its_attack(self):
        s, _, unit, player = self.setup_enemy('hulk.rpgscavenger')
        unit._begin_attack(0., player, [])
        events = []
        for _ in range(80):
            s.enemies.step(.02, player, events)
        self.assertTrue(any(e['kind'] == 'enemy-shot' and e['owner'] == unit.objid
                            for e in events))

    def test_lab_mid_native_zombie_death_stops_updates_and_drops_once(self):
        for web in (False, True):
            with self.subTest(web=web):
                s, b, _ = encounter('lab_mid')
                s.options.web_client = web
                oid = s._spawn_zombie(100000, 100000)
                s._drop_zombie_supply = mock.Mock()
                for _ in range(2):
                    b.events.append(dict(kind='destroy', ownerid=s.worldid, victim=oid, reason=2))
                    s._sync_player()
                self.assertNotIn(oid, s.zombies)
                s._drop_zombie_supply.assert_called_once()
                s.batch.clear()
                s._step_zombies(.05)
                self.assertFalse(any(row['objid_'] == oid for row in s.batch.objects))

    def test_lab_mid_rejects_foreign_world_death_and_damage(self):
        s, b, _ = encounter('lab_mid')
        oid = s._spawn_zombie(100000, 100000)
        for kind in ('damage', 'destroy'):
            b.events.append(dict(kind=kind, ownerid=999, victim=oid, reason=2, value=999999))
            s._sync_player()
            self.assertEqual(s.zombies[oid]['hp'], 400)
        b.events.append(dict(kind='destroy', ownerid=s.worldid, victim=oid, reason=6))
        s._sync_player()
        self.assertIn(oid, s.zombies)

    def test_full_run_room_enemy_deaths_retire_all_loaded_species(self):
        # Exercise actual recipes and controllers in every room, including
        # offscreen enemies; do not mistake boss phase proxies or scenery for NPCs.
        checked = set()
        with contextlib.redirect_stdout(io.StringIO()):
            for mid in FULL_RUN_MAPS:
                with game_settings.event_snapshot(game_settings.normalize(dict(map=mid))):
                    source = WholeRun(mid)
                    run = source.session.run
                    for role in run.pack.room_names:
                        run.current, run.entry = role, 'default'
                        wrapper = _map_session_class(mid)(source.server, source.channel,
                            source.options, bridge=source.bridge)
                        host = wrapper.current
                        if not callable(getattr(host, 'feedback', None)):
                            continue  # standalone mid/boss controllers tested separately
                        enemies = host.enemies
                        state_ids = set(getattr(enemies, 'units', {}))
                        state_ids.update(getattr(enemies, 'states', {}))
                        state_ids.update(oid for oid, row in host.actors.items()
                                         if row.get('kind') == 'npc')
                        # EFB uses ownership for its species and separate shots.
                        if mid in ('efb_run', 'est_run'):
                            state_ids.update(getattr(enemies, 'owned', ()))
                        for oid in sorted(state_ids):
                            row = host.actors.get(oid)
                            if row is None or not isinstance(row.get('hp'), (int, float)) or row['hp'] <= 0:
                                continue
                            if row['props'].get('invulnerable'):
                                continue
                            vid = row['vid']
                            with self.subTest(map=mid, room=role, vid=vid, oid=oid):
                                event = dict(kind='destroy', ownerid=host.worldid,
                                             victim=oid, reason=2)
                                host.feedback(event)
                                host.feedback(event)
                                self.assertNotIn(oid, host.actors)
                                checked.add((mid, vid))
                        # Delayed attacks must not resurrect removed owners.
                        retired = set(host.retired)
                        host.batch.clear()
                        with mock.patch.object(host.batch, 'create', wraps=host.batch.create) as creates:
                            for _ in range(40):
                                host.step(.05)
                        for call in creates.call_args_list:
                            self.assertNotIn(call.kwargs.get('creatorid', 0), retired)
        self.assertEqual({mid for mid, _ in checked}, set(FULL_RUN_MAPS))
        self.assertGreater(len(checked), 70)

    def test_standalone_map_native_deaths_stop_all_active_ordinary_actors(self):
        # Full-run recipes exercise the species catalogue. Here exercise each
        # standalone adapter too, including shared-controller maps and waves.
        ordinary = {'npc', 'hive', 'insect', 'flyer', 'infantry', 'skiff',
                    'hawk', 'copter', 'sentry', 'frog', 'bug', 'cargo'}
        checked = set()
        for mid in SUPPORTED_MAPS:
            if mid in ('egy_hulk', 'hulk_normal', 'lab_mid', 'lab_hard'):
                continue  # dedicated damage-loss cases above/below
            with self.subTest(map=mid), game_settings.event_snapshot(
                    game_settings.normalize(dict(map=mid))):
                s, bridge, _ = encounter(mid)
                s.prepare()
                for tick in range(200):
                    bridge._absorb(input_at(tick))
                    s.step(.1)
                controller = getattr(s, 'controller', None)
                hosts = [s] + ([controller] if controller is not None else [])
                for host in hosts:
                    for oid, row in list(getattr(host, 'actors', {}).items()):
                        if row.get('kind') not in ordinary or not isinstance(row.get('hp'), (int, float)) or row['hp'] <= 0:
                            continue
                        with self.subTest(oid=oid, vid=row.get('vid')):
                            s._feedback(dict(kind='destroy', ownerid=s.worldid,
                                             victim=oid, reason=2))
                            self.assertNotIn(oid, host.actors)
                            checked.add(mid)
                if mid == 'hulk_mid':
                    for oid in list(s.traps):
                        s._feedback(dict(kind='destroy', ownerid=s.worldid,
                                         victim=oid, reason=2))
                    self.assertEqual(s.traps, {})
                # Run beyond windups without UI/native rendering.
                for tick in range(200, 230):
                    bridge._absorb(input_at(tick))
                    s.step(.1)
        self.assertTrue({'cave_boss', 'city_mid', 'city_boss', 'dumb_boss',
                         'dumb_wave', 'dumb_normal_wave', 'forest_mid',
                         'forest_boss', 'jungle_mid', 'jungle_boss'} <= checked)

    def test_lab_hard_native_seeker_death_retires_without_damage_report(self):
        s, bridge, _ = encounter('lab_hard')
        s.prepare()
        s._spawn_seeker_wave({})
        ids = set(s.seekers)
        self.assertTrue(ids)
        for oid in ids:
            bridge.events.append(dict(kind='destroy', ownerid=s.worldid,
                                       victim=oid, reason=2))
        s._sync_player()
        self.assertFalse(ids.intersection(s.seekers))


if __name__ == '__main__':
    unittest.main()
