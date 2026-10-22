"""SC progression through real room actors, snapshots and transport wrappers."""
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from localserver.live.bridge import PlayerBridge
from localserver.pb_layout import default_layouts
from localserver.server import LocalServer
from localserver.world_snapshot import decode_delta
from localserver.maps.sc_run.run import ScLevelRun
from localserver.maps.sc_run.scene import ScRunPack
from localserver.maps.sc_run.session import ScRunLiveSession


class DoorGateTests(unittest.TestCase):
    """A lost native egress ACK must not disable every later door and restart."""

    @staticmethod
    def host(since, status=None):
        bridge = mock.Mock()
        bridge.ground_egress_status.return_value = status or {'schema': 'full-run-egress-ready-v1',
                                                             'status': 'pending'}
        host = SimpleNamespace(
            _ground_egress_ack_pending=dict(bridge=bridge, since=since,
                                            control={'request_id': 1}, ack={'receipt': 1}),
            GROUND_EGRESS_ACK_TIMEOUT_S=LocalServer.GROUND_EGRESS_ACK_TIMEOUT_S)
        host._release_ground_ack = lambda reason: setattr(host, '_ground_egress_ack_pending', None)
        return host, bridge

    def test_fresh_ack_keeps_the_gate_closed(self):
        host, bridge = self.host(time.time())
        self.assertFalse(LocalServer._ground_ack_ready(host, bridge))

    def test_missing_ack_releases_the_gate_after_the_deadline(self):
        host, bridge = self.host(time.time()-LocalServer.GROUND_EGRESS_ACK_TIMEOUT_S-1.)
        self.assertTrue(LocalServer._ground_ack_ready(host, bridge))
        self.assertIsNone(host._ground_egress_ack_pending)

    def test_retired_bridge_releases_the_gate_immediately(self):
        host, _bridge = self.host(time.time())
        self.assertTrue(LocalServer._ground_ack_ready(host, object()))
        self.assertIsNone(host._ground_egress_ack_pending)


class Clock:
    def __init__(self):
        self.now = 100.

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Transport:
    """Real RoomServer commits and protobufs, with no sockets or client."""
    def __init__(self, run):
        self.level_run, self.layouts = run, default_layouts()
        self.sent, self.transitions, self.exits = [], [], []

    def _send_raw(self, channel, kind, payload, label='', record=False):
        self.sent.append((kind, payload, label))

    def poll_live_room_inventory(self, channel, wrapper):
        return False

    def request_live_room_transition(self, channel, wrapper, link, player):
        self.transitions.append(dict(link))
        return True

    def request_live_world_exit(self, channel, wrapper, link, player):
        self.exits.append(dict(link))
        return True

    def deltas(self):
        return [decode_delta(self.layouts, payload) for kind, payload, _ in self.sent if kind == 12]


class ScRunTests(unittest.TestCase):
    def make_run(self, seed=1234):
        clock = Clock()
        return ScLevelRun(ScRunPack(), seed=seed, clock=clock), clock

    def open_room(self, run, role, server=None):
        run.current, run.entry = role, 'default'
        server = server or Transport(run)
        bridge = PlayerBridge(0)
        options = SimpleNamespace(player_type=1, bridge_port=0)
        wrapper = ScRunLiveSession(server, None, options, bridge=bridge)
        self.feedback(wrapper)
        wrapper.step(.001)
        self.assertTrue(wrapper._ready, 'The ordinary native feedback readiness gate must open at the authored spawn')
        return wrapper, server

    def feedback(self, wrapper, position=None):
        x, y = position or wrapper.run.destination_spawn(wrapper.instance.role)
        context = wrapper.bridge.native_context
        wrapper.bridge._absorb(dict(seq=1, room_context=context, keys=dict(gameplay=True),
            player=dict(worldid=2, objid=1, xmp=x, ymp=y, wmp=10240, hmp=20480,
                        hp=500, maxhp=500, alive=True, room_seed=context['room_seed'])))

    def test_room_ticks_without_any_player_telemetry(self):
        # USER: every room reached through a door stayed inert. A session only
        # exists after the client answered CreateWorld, so the room must run
        # even when no player frame ever arrives.
        run, _ = self.make_run(seed=5)
        run.current, run.entry = 'path_0_1', 'default'
        server = Transport(run)
        wrapper = ScRunLiveSession(server, None, SimpleNamespace(player_type=1, bridge_port=0),
                                   bridge=PlayerBridge(0))
        self.assertTrue(wrapper._ready)
        for _ in range(20):
            wrapper.step(.05)
        self.assertGreater(wrapper.current.room_time, .5)
        self.assertFalse(server.transitions)

    def test_room_arms_by_frame_grace_when_the_handoff_position_differs(self):
        # USER: one door followed by a frozen room. The native client may adopt
        # a different position than the committed arrival point, so a short run
        # of fresh frames must arm the room instead of leaving it tickless.
        run, _ = self.make_run(seed=5)
        run.current, run.entry = 'engine', 'default'
        server = Transport(run)
        wrapper = ScRunLiveSession(server, None, SimpleNamespace(player_type=1, bridge_port=0),
                                   bridge=PlayerBridge(0))
        x, y = run.destination_spawn('engine')
        far = (x + 1024*256, y)
        for _ in range(ScRunLiveSession.READY_FRAME_GRACE):
            self.feedback(wrapper, far)
            wrapper.step(.02)
        self.assertTrue(wrapper._ready)
        self.feedback(wrapper, far)
        wrapper.step(.02)
        self.assertGreater(wrapper.current.room_time, 0., 'the room must actually tick')

    def test_every_planned_room_runs_without_errors(self):
        # Broad regression net over every generated room: activations, moving
        # ledges, projectiles and doors must survive a few real ticks.
        run, _ = self.make_run(seed=5)
        for role in list(run.pack.room_names):
            with self.subTest(role=role):
                wrapper, _server = self.open_room(run, role)
                for _ in range(12):
                    self.feedback(wrapper)
                    wrapper.step(.05)
                if not hasattr(wrapper.current, 'actors'):
                    continue  # dedicated boss/Mid sessions own their own actors
                for oid, row in wrapper.current.actors.items():
                    x, y = row['pos']
                    self.assertFalse(x != x or y != y, 'non-finite actor position %s' % oid)

    def test_initial_twenty_four_minutes_and_gen_bonus_use_one_global_display_clock(self):
        run, clock = self.make_run()
        self.assertEqual(run.remaining(), 1440)
        clock.advance(100)
        gen = run.room('gen_0')
        gen.flags['gen_destroyed'] = True
        run.observe(gen)
        self.assertEqual(run.remaining(), 1460)
        for _ in range(4):
            run.observe(gen)
        self.assertEqual(run.remaining(), 1460)
        self.assertEqual(len([event for event in run.events if event['kind'] == 'gen_destroyed']), 1)
        run.current = 'entry'
        clock.advance(10)
        self.assertEqual(run.remaining(), 1450)

    def test_zero_display_clock_keeps_ordinary_doors_open_without_forced_home(self):
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'entry')
        clock.advance(1440)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertEqual(run.remaining(), 0)
        self.assertFalse(server.exits)
        self.assertFalse(server.transitions)
        for oid, props in wrapper.instance.recipe['objects'].items():
            if props.get('vid', '').startswith('sc.min'):
                self.assertEqual(wrapper.instance.snapshot.properties_at((1, oid))['vid_'], 'sc.min0')
        oid, link = next(iter(wrapper.instance.recipe['links'].items()))
        self.assertTrue(run.allows(link))
        # USER: side doors are walked through; the boundary contact performs the
        # transition and no interact press is available on that door.
        self.assertEqual(link.get('trigger'), 'boundary')
        props = wrapper.instance.snapshot.properties_at((1, oid))
        self.assertEqual(props['interact_'], 0)
        self.feedback(wrapper, (props['xmp_']-props['wmp_']*2, props['ymp_']))
        wrapper.step(.02)
        self.feedback(wrapper, (props['xmp_'], props['ymp_']))
        wrapper.step(.02)
        self.assertEqual(server.transitions[-1]['destination'], link['destination'])
        player, _ = wrapper.bridge.snapshot()
        self.assertTrue(run.move(link, player))
        self.assertEqual(run.current, link['destination'])
        self.assertFalse(server.exits)

    def test_zero_clock_during_initial_loading_still_accepts_player_feedback(self):
        run, clock = self.make_run()
        server = Transport(run)
        wrapper = ScRunLiveSession(server,None,SimpleNamespace(player_type=1,bridge_port=0),bridge=PlayerBridge(0))
        clock.advance(1441)
        wrapper.step(.02)
        # The room arms as soon as its world exists; a missing native character
        # is still never fabricated, so no door may fire without telemetry.
        self.assertTrue(wrapper._ready)
        self.assertEqual(run.remaining(),0)
        self.assertTrue(run.allows(next(iter(wrapper.instance.recipe['links'].values()))))
        # A missing native character must not be fabricated as an alive one.
        self.assertFalse(server.transitions)
        self.assertFalse(server.exits)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertTrue(wrapper._ready)
        self.assertFalse(server.exits)
        self.assertFalse(server.transitions)

    def test_zero_clock_preserves_an_ordinary_pending_door_handoff(self):
        from localserver.maps.run_common.inventory_handoff import InventoryHandoff
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'entry')
        link = next(iter(wrapper.instance.recipe['links'].values()))
        scope = dict(map='sc_run', room_token=wrapper._input_serial, room_seed=1,
                     worldid=2, objid=1, charid=1, player_type=1)
        pending = InventoryHandoff(12, link, scope, 100.)
        wrapper._inventory_handoff = pending
        wrapper.bridge.native_context['inventory_request'] = pending.control()
        clock.advance(1440)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertIs(wrapper._inventory_handoff, pending)
        self.assertIn('inventory_request', wrapper.bridge.native_context)
        self.assertTrue(run.allows(link))
        self.assertFalse(server.exits)

    def test_due_boss_warp_cancels_an_old_door_handoff_and_blocks_ordinary_travel(self):
        from localserver.maps.run_common.inventory_handoff import InventoryHandoff
        run, clock = self.make_run()
        wrapper, server = self.open_room(run,'entry')
        link=next(iter(wrapper.instance.recipe['links'].values()))
        scope=dict(map='sc_run',room_token=wrapper._input_serial,room_seed=1,
                   worldid=2,objid=1,charid=1,player_type=1)
        pending=InventoryHandoff(12,link,scope,100.)
        wrapper._inventory_handoff=pending
        wrapper.bridge.native_context['inventory_request']=pending.control()
        for index in range(4):run.mark_gen(index)
        clock.advance(run.COLLAPSE_SECONDS)
        self.assertFalse(run.allows(link))
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertEqual(pending.state,'rejected')
        self.assertIsNone(wrapper._inventory_handoff)
        self.assertNotIn('inventory_request',wrapper.bridge.native_context)
        self.assertEqual(server.transitions[-1]['destination'],'boss')

    def test_four_distinct_gens_start_one_collapse_then_automatic_boss_transition(self):
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'entry')
        for index in range(4):
            self.assertTrue(run.mark_gen(index))
        started = run.collapse_started
        self.assertFalse(run.mark_gen(3))
        self.assertEqual(run.collapse_started, started)
        self.assertEqual(run.remaining(), 1920)
        clock.advance(run.COLLAPSE_SECONDS-.001)
        self.assertFalse(run.boss_due())
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertFalse(server.transitions)
        clock.advance(.002)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertEqual(server.transitions[-1]['destination'], 'boss')
        # The display can reach zero without blocking the pending Boss warp.
        clock.advance(3000)
        run.observe(run.room('entry'))
        self.assertEqual(run.remaining(), 0)
        self.assertTrue(run.boss_due())

    def test_four_gens_destroyed_after_clock_zero_still_collapse_into_boss(self):
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'entry')
        clock.advance(3000)
        for index in range(4):
            self.assertTrue(run.mark_gen(index))
        self.assertEqual(run.remaining(), 0)
        self.assertFalse(run.boss_due())
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertFalse(server.exits)
        self.assertFalse(server.transitions)
        clock.advance(run.COLLAPSE_SECONDS)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertEqual(server.transitions[-1]['destination'], 'boss')
        self.assertFalse(server.exits)

    def test_mid_door_reads_only_its_own_completion_flag(self):
        run, _ = self.make_run()
        jump = run.room('mid_jump')
        other = run.room('mid_hive')
        jump_link = next(link for link in jump.recipe['links'].values() if link.get('requires') == 'mid_cleared')
        hive_link = next(link for link in other.recipe['links'].values() if link.get('requires') == 'mid_cleared')
        self.assertFalse(run.allows(jump_link, role=jump.role))
        jump.flags['mid_cleared'] = True
        self.assertTrue(run.allows(jump_link, role=jump.role))
        self.assertFalse(run.allows(hive_link, role=other.role))
        self.assertNotIn('mid', run.defeated)

    def test_cleared_mid_core_door_is_an_interact_door_gated_by_its_own_flag(self):
        # USER: a core door inside the room is an interact action, unlike the
        # walked-through room-edge doors.
        run, _ = self.make_run()
        wrapper, server = self.open_room(run, 'mid_jump')
        oid, link = next((o, l) for o, l in wrapper.instance.recipe['links'].items()
                         if l.get('requires') == 'mid_cleared')
        self.assertEqual(link.get('trigger'), 'interact')
        props = wrapper.instance.snapshot.properties_at((1, oid))
        self.feedback(wrapper, (props['xmp_'], props['ymp_']))
        player, _ = wrapper.bridge.snapshot()
        self.assertFalse(wrapper.interact(oid, player))
        self.assertFalse(server.transitions)
        run.room('mid_jump').flags['mid_cleared'] = True
        self.assertTrue(wrapper.interact(oid, player))
        self.assertEqual(server.transitions[-1]['destination'], link['destination'])

    def test_failed_boss_world_preparation_does_not_cancel_the_automatic_retry(self):
        from localserver.server import LocalServer
        run, clock = self.make_run()
        wrapper, _ = self.open_room(run, 'entry')
        for index in range(4):
            run.mark_gen(index)
        clock.advance(run.COLLAPSE_SECONDS)
        player, _ = wrapper.bridge.snapshot()
        host = SimpleNamespace(options=SimpleNamespace(map='sc_run'),
            level_run=run, _restart_in_progress=False, _distinct_player_world_blob=lambda blob: blob)
        link = dict(destination='boss', entry='default', trigger='automatic')
        with mock.patch.object(wrapper, 'world_blob', side_effect=RuntimeError('world preparation failed')):
            with self.assertRaisesRegex(RuntimeError, 'world preparation failed'):
                LocalServer._commit_live_room_transition(host, None, wrapper, link, player)
        self.assertEqual(run.current, 'entry')
        self.assertFalse(host._restart_in_progress)
        self.assertFalse(run.boss_entered)
        self.assertTrue(run.boss_due())

    def test_gen_death_after_clock_zero_adds_time_and_commits_before_tick_returns(self):
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'gen_0')
        clock.advance(1440)
        self.feedback(wrapper)
        wrapper.step(.02)
        self.assertEqual(run.remaining(), 0)
        self.assertTrue(all(run.allows(link) for link in wrapper.instance.recipe['links'].values()))
        self.assertFalse(server.exits)
        oid = next(oid for oid, row in wrapper.current.actors.items() if row['vid'] == 'sc.core')
        wrapper.current.feedback(dict(kind='damage', victim=oid, value=5000, seq=7))
        wrapper.step(.02)
        self.assertEqual(run.gens, {0})
        self.assertEqual(run.remaining(), 120)
        self.assertFalse(server.exits)
        self.assertFalse(server.transitions)
        saved = wrapper.instance.snapshot.properties_at((1, oid))
        self.assertEqual(saved['vid_'], 'sc.dcore')
        self.assertNotIn('hitpoints_', saved)
        self.assertTrue(wrapper.commit_status()['local_committed'], wrapper.commit_status())
        self.assertFalse(wrapper.current.batch.objects)
        same, _ = self.open_room(run, 'gen_0', server)
        same.step(.02)
        self.assertIs(same.current, wrapper.current)
        self.assertEqual(len(run.gens), 1)
        self.assertEqual(len([e for e in run.events if e['kind'] == 'gen_destroyed']), 1)

    def test_jump_stopping_native_move_survives_proxy_commit_and_revisit(self):
        run, _ = self.make_run()
        wrapper, server = self.open_room(run, 'mid_jump')
        switch = next(oid for oid, row in wrapper.current.actors.items() if row['vid'] == 'sc.switch')
        row = wrapper.current.actors[switch]
        self.feedback(wrapper, tuple(row['pos']))
        player, _ = wrapper.bridge.snapshot()
        wrapper.current.mechanics.interact(switch, player)
        wrapper.step(.02)
        self.assertTrue(wrapper.instance.flags['mid_cleared'])
        gen_oid = next(oid for oid, link in wrapper.instance.recipe['links'].items() if link.get('requires') == 'mid_cleared')
        self.assertEqual(wrapper.instance.snapshot.properties_at((1, gen_oid))['interact_'], 1)
        for oid, row in wrapper.current.actors.items():
            if row['vid'] == 'sc.evilpeg32':
                saved = wrapper.instance.snapshot.properties_at((1, oid))
                self.assertNotIn('move_', saved)
                self.assertEqual(saved['damage_'], 20)
        self.assertTrue(wrapper.commit_status()['local_committed'], wrapper.commit_status())
        revisit, _ = self.open_room(run, 'mid_jump', server)
        self.assertIs(revisit.current, wrapper.current)
        self.assertEqual(revisit.current.actors[switch]['vid'], 'sc.switchdone')

    def test_chest_reward_is_emitted_once_through_new_wrappers(self):
        run, _ = self.make_run()
        role = next(role for role in run.pack.room_names if role.startswith('chest_') and
                    run.pack.plan['rooms'][role]['metadata'].get('chest_guardian'))
        wrapper, server = self.open_room(run, role)
        guardian = wrapper.instance.recipe['metadata']['chest_guardian']
        wrapper.current.mechanics.guardian_defeated(guardian)
        wrapper.step(.02)
        wrapper.step(.02)
        again, _ = self.open_room(run, role, server)
        again.step(.02)
        chest = [d['createloot_'] for d in server.deltas() if 'createloot_' in d]
        self.assertEqual(len(chest), 1)
        self.assertEqual(len(chest[0]['itemprops_']), 4)
        self.assertEqual(chest[0]['itemprops_'][-1]['type_'], 96)
        self.assertEqual(run.chests_rewarded, {role})
        self.assertTrue(again.commit_status()['local_committed'], again.commit_status())

    def test_boss_victory_opens_original_hidden_portal_and_requires_manual_home_interaction(self):
        from tools.make_create_world import unwrap_decompress
        run, _ = self.make_run()
        wrapper, server = self.open_room(run, 'boss')
        portal = wrapper.instance.snapshot.properties_at((1, 20))
        self.assertIsNotNone(portal, 'The original preplaced victory portal must survive content compilation')
        self.assertEqual(portal['vid_'], 'lobbyportal')
        self.assertEqual((portal['xmp_'], portal['ymp_'], portal['wmp_'], portal['hmp_']),
                         (352256, 532480, 28160, 40960))
        self.assertFalse(portal['draw_'])
        self.assertEqual(portal['interact_'], 0)
        self.feedback(wrapper, (portal['xmp_'], portal['ymp_']))
        player, _ = wrapper.bridge.snapshot()
        self.assertFalse(wrapper.interact(20, player))
        self.assertFalse(server.exits)

        # Start at the last stage's damage/time gate, then use the real Boss
        # feedback, controller event and wrapper commit to complete the fight.
        runtime = wrapper.current
        controller = runtime.controller
        controller.stage, controller.state, controller.hp = 5, 'fighting', 1.
        controller.stage_started = controller.time-controller.rule['duration_s']
        runtime._feedback(dict(kind='damage', victim=runtime.head_id, value=1))
        runtime._feedback(dict(kind='damage', victim=runtime.head_id, value=1))
        wrapper.step(.02)
        self.assertTrue(run.completed)
        self.assertNotIn((1, runtime.head_id), wrapper.instance.snapshot.objects)
        portal = wrapper.instance.snapshot.properties_at((1, 20))
        self.assertTrue(portal['draw_'])
        self.assertEqual(portal['interact_'], 1)
        self.assertFalse(server.exits, 'Winning must open the portal without automatically leaving')
        self.assertTrue(wrapper.commit_status()['local_committed'], wrapper.commit_status())
        document = server.layouts.decode('CreateWorld', unwrap_decompress(wrapper.world_blob(server, run)))
        saved = next(row['props_'] for row in document['worldstate_']['serverworld_']['obj_']
                     if row['objid_'] == 20)
        self.assertTrue(saved['draw_'])
        self.assertEqual(saved['interact_'], 1)
        self.assertTrue(wrapper.interact(20, player))
        self.assertEqual(len(server.exits), 1)
        self.assertEqual(server.exits[0]['destination'], 'home')

    def test_tank_tower_drops_chest_on_highest_tank_with_lower_tanks_still_alive(self):
        run, _ = self.make_run()
        role = next(name for name, room in run.pack.plan['rooms'].items()
                    if room['metadata']['template'] == 'scend02')
        wrapper, server = self.open_room(run, role)
        runtime = wrapper.current
        tanks = {oid: row for oid, row in runtime.actors.items() if row['vid'] == 'sc.tank'}
        top = min(tanks, key=lambda oid:tanks[oid]['pos'][1])
        lower = max(tanks, key=lambda oid:tanks[oid]['pos'][1])
        self.assertEqual(len(tanks), 27)
        runtime.feedback(dict(kind='damage', victim=lower, value=tanks[lower]['hp'], seq=100))
        wrapper.step(.02)
        self.assertFalse(any('createloot_' in d for d in server.deltas()))
        runtime.feedback(dict(kind='damage', victim=top, value=tanks[top]['hp'], seq=101))
        wrapper.step(.02)
        rewards = [d['createloot_'] for d in server.deltas() if 'createloot_' in d]
        self.assertEqual(len(rewards), 1)
        self.assertEqual(len([r for r in runtime.actors.values() if r['vid'] == 'sc.tank']), 25)
        self.assertEqual(run.chests_rewarded, {role})

    def test_revisit_preserves_damage_ages_deadlines_and_does_not_replay_engine_attacks(self):
        run, clock = self.make_run()
        wrapper, server = self.open_room(run, 'engine')
        runtime = wrapper.current
        core = next(oid for oid, row in runtime.actors.items() if row['vid'] == 'sc.enginecore')
        row = runtime.actors[core]
        self.feedback(wrapper, (row['pos'][0]-100*256, row['pos'][1]))
        runtime.feedback(dict(kind='damage', victim=core, value=321, seq=11))
        # One real summon is pending before leaving, rather than injecting an
        # artificial scheduler entry into the controller under test.
        wrapper.step(.5)
        wrapper.step(.5)
        seeds = [r for r in runtime.actors.values() if r['vid'] == 'sc.enginefireseed']
        self.assertEqual(len(seeds), 3)
        clock.advance(120.)
        revisit, _ = self.open_room(run, 'engine', server)
        self.assertIs(revisit.current, runtime)
        self.assertEqual(runtime.actors[core]['hp'], 59679)
        self.assertFalse(any(r['vid'] == 'sc.enginefireseed' for r in runtime.actors.values()))
        self.assertFalse(any(r['vid'] == 'sc.enginefire' for r in runtime.actors.values()))
        self.assertEqual(sum(r['vid'] == 'sc.enginespawner' for r in runtime.actors.values()), 3)
        revisit.step(.02)
        self.assertFalse(any(r['vid'] == 'sc.enginefire' for r in runtime.actors.values()))
        self.assertAlmostEqual(runtime.room_time, runtime._sent_ms/1000.)
        self.assertAlmostEqual(runtime.instance.snapshot.time, runtime.room_time)

    def test_revisit_new_streamed_platforms_have_the_same_position_in_cw_and_runtime(self):
        run, clock = self.make_run()
        role = next(role for role, room in run.pack.plan['rooms'].items()
                    if room['metadata']['template'] == 'scmid01')
        wrapper, server = self.open_room(run, role)
        runtime = wrapper.current
        before = {oid for oid, row in runtime.actors.items() if row['vid'].startswith('sc.mledge')}
        clock.advance(120.)
        revisit, _ = self.open_room(run, role, server)
        after = {oid for oid, row in runtime.actors.items() if row['vid'].startswith('sc.mledge')}
        self.assertEqual(len(after), len(before))
        self.assertTrue(after-before, 'Exercise real stream-out replacement while the room is absent')
        self.assertIs(revisit.current, runtime)
        for oid in after-before:
            expected = runtime.actors[oid]['pos']
            saved = revisit.instance.snapshot.position((1, oid))
            self.assertAlmostEqual(saved[0], expected[0], delta=1., msg='A newly born platform must not be aged by the entire absent interval')
            self.assertAlmostEqual(saved[1], expected[1], delta=1.)


if __name__ == '__main__':
    unittest.main()
