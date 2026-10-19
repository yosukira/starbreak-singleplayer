"""Combat counters follow real persistent rooms without changing their replay."""
import contextlib
import io
import json
import tempfile
import unittest

from localserver import game_settings
from localserver.battle_records import BattleRecorder, RecordStore
from localserver.run_replay import RunReplay
from localserver.server import _map_session_class
from test_run_replay import WholeRun


FULL_RUNS = ('egy_run', 'eub_run', 'etl_run', 'est_run', 'efb_run', 'eff_run')


class RunCombatMetricTests(unittest.TestCase):
    def test_egy_shield_generators_have_health_but_are_not_monster_kills(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()), \
                game_settings.event_snapshot(game_settings.normalize(dict(map='egy_run'))):
            source = WholeRun('egy_run')
            recorder = BattleRecorder(source.session, RecordStore(directory))
            source.feedback()
            recorder.tick(.02)
            host = source.session.current
            expected = 0
            for index in range(2):
                # Opening a field also retires its linked helper objects.
                oid = next(oid for oid, state in host.mechanics.states.items()
                           if state['kind'] == 'generator' and oid in host.actors
                           and host.actors[oid].get('hp', 0) > 0)
                expected += host.actors[oid]['hp']
                self.assertIsNone(host.native_retirement_capability(oid), 'Terrain collision is unrelated to actual HP')
                event = (dict(kind='damage', victim=oid, value=1000000, ownerid=1) if index == 0
                         else dict(kind='destroy', victim=oid, reason=2, ownerid=1))
                source.feedback(events=[event])
                source.feedback(events=[dict(kind='destroy', victim=oid, reason=2, ownerid=1)])
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics']['damage_dealt'], expected)
                self.assertEqual(recorder.document['metrics']['kills'], 0)
                self.assertEqual(recorder.document['metrics']['generators_destroyed'], index + 1)
            self.assertFalse(recorder.combat_observer.failed)

    def test_native_hp_retirement_counts_remaining_hp_and_expiration_is_not_a_kill(self):
        for map_id in FULL_RUNS:
            with self.subTest(map=map_id), tempfile.TemporaryDirectory() as directory, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                source = WholeRun(map_id)
                recorder = BattleRecorder(source.session, RecordStore(directory))
                source.feedback()
                recorder.tick(.02)
                host = source.session.current
                observer = recorder.combat_observer.current
                targets = [(oid, row) for oid, row in host.actors.items()
                    if observer.target(oid) is not None and observer.target(oid).npc
                    and not row['props'].get('invulnerable')]
                oid, actor = targets[0]
                hp = actor['hp']
                source.feedback(events=[dict(kind='damage', victim=oid, value=7, ownerid=1)])
                source.feedback(events=[dict(kind='destroy', victim=oid, reason=2, ownerid=1)])
                source.feedback(events=[dict(kind='destroy', victim=oid, reason=2, ownerid=1)])
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics']['damage_dealt'], hp)
                self.assertEqual(recorder.document['metrics']['kills'], 1)
                other = next(oid for oid, row in targets[1:] if oid in host.actors)
                source.feedback(events=[dict(kind='destroy', victim=other, reason=6, ownerid=1)])
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics']['damage_dealt'], hp)
                self.assertEqual(recorder.document['metrics']['kills'], 1)
                self.assertFalse(recorder.combat_observer.failed)

    def test_real_doors_keep_counters_and_revisiting_does_not_credit_old_kills(self):
        for map_id in FULL_RUNS:
            with self.subTest(map=map_id), tempfile.TemporaryDirectory() as directory, \
                    contextlib.redirect_stdout(io.StringIO()), \
                    game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                source = WholeRun(map_id)
                recorder = BattleRecorder(source.session, RecordStore(directory))
                source.feedback()
                recorder.tick(.02)
                host = source.session.current
                observer = recorder.combat_observer.current
                oid, actor = next((oid, row) for oid, row in host.actors.items()
                    if observer.target(oid) is not None and observer.target(oid).npc
                    and not row['props'].get('invulnerable'))
                hp = actor['hp']
                source.feedback(events=[dict(kind='damage', victim=oid, value=1000000, ownerid=7)])
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics']['damage_dealt'], 0)
                source.feedback(events=[dict(kind='damage', victim=oid, value=1000000, ownerid=1)])
                source.feedback(events=[dict(kind='damage', victim=oid, value=1000000, ownerid=1)])
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics']['damage_dealt'], hp)
                self.assertEqual(recorder.document['metrics']['kills'], 1)
                first = source.session.instance
                if map_id == 'est_run':
                    source.feedback(events=[dict(kind='damage', victim=104, value=100000, ownerid=1)])
                    recorder.tick(.02)
                expected = dict(recorder.document['metrics'])
                next_room = next(link['destination'] for link in first.recipe['links'].values()
                    if link['destination'] in source.session.run.pack.room_names
                    and link['destination'] != first.role and source.session.run.allows(link))
                source.travel(next_room)
                recorder.bind(source.session)
                recorder.tick(.02)
                self.assertEqual(recorder.document['metrics'], expected)
                self.assertGreaterEqual(len(recorder.document['rooms']), 2)
                back = any(link['destination'] == first.role and source.session.run.allows(link)
                           for link in source.session.instance.recipe['links'].values())
                if back:
                    source.travel(first.role)
                    recorder.bind(source.session)
                    source.feedback(events=[dict(kind='damage', victim=oid, value=1000000, ownerid=1)])
                    recorder.tick(.02)
                    self.assertIs(source.session.current, host)
                    self.assertIs(recorder.combat_observer.current, observer)
                    self.assertEqual(recorder.document['metrics'], expected)
                self.assertFalse(recorder.combat_observer.failed)
                self.assertEqual(recorder.document['result'], 'active')
                # Every emitted packet must rebuild without a recorder/observer.
                replay = RunReplay(json.loads(json.dumps(source.capture.document())))
                try:
                    while replay.advance(): pass
                    self.assertEqual(replay.session.instance.role, source.session.instance.role)
                finally:
                    replay.close()

    def test_full_run_encounters_use_the_actual_room_bodies_and_fresh_counters(self):
        for map_id in FULL_RUNS:
            for role in ('mid', 'boss'):
                with self.subTest(map=map_id, role=role), tempfile.TemporaryDirectory() as directory, \
                        contextlib.redirect_stdout(io.StringIO()), \
                        game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                    source = WholeRun(map_id)
                    # A controller fixture starts in the requested native room;
                    # real-door progression is exercised in the test above.
                    run = source.session.run
                    room_role = (next(name for name, node in run.pack.plan['nodes'].items()
                                      if node['encounter_kind'] == 'mid')
                                 if map_id == 'eub_run' and role == 'mid' else role)
                    run.current, run.entry = room_role, 'default'
                    source.session = _map_session_class(map_id)(source.server, source.channel,
                        source.options, bridge=source.bridge)
                    source.server.live_session = source.session
                    source.capture.enter(source.session)
                    recorder = BattleRecorder(source.session, RecordStore(directory))
                    for _ in range(180): source.feedback()
                    recorder.tick(.02)
                    host = source.session.current
                    c = getattr(host, 'controller', None)
                    child = recorder.combat_observer.current
                    self.assertFalse(recorder.combat_observer.failed)
                    if map_id == 'egy_run' and role == 'boss':
                        candidates = [oid for oid, row in host.intro_units.items() if row['hp'] > 0]
                    else:
                        candidates = list(getattr(c, 'bosses', {})) + list(getattr(c, 'parts', {}))
                        candidates += list(getattr(c, 'blocks', {}))
                        candidates += [getattr(host, 'mouth_id', None), getattr(getattr(host, 'pack', None), 'boss_objid', None)]
                        candidates += [getattr(c, 'boss', {}).get('id')]
                        candidates += [getattr(c, 'small', {}).get('id')]
                        candidates += list(getattr(c, 'actors', {})) + list(getattr(host, 'actors', {}))
                        candidates = [oid for oid in candidates if oid is not None and child.target(oid) is not None]
                    for oid in candidates:
                        source.feedback(events=[dict(kind='damage', victim=oid, value=13, ownerid=1)])
                        if recorder.pending_damage > 0: break
                    recorder.tick(.02)
                    self.assertEqual(recorder.document['metrics']['damage_dealt'], 13)
                    self.assertEqual(recorder.document['metrics']['kills'], 0)
                    self.assertFalse(recorder.combat_observer.failed)
                    self.assertEqual(recorder.document['result'], 'active')
                    if map_id == 'egy_run' and role == 'boss':
                        host._begin_death()
                        recorder.tick(.02)
                        recorder.tick(.02)
                        self.assertEqual(recorder.document['metrics']['kills'], 1)


if __name__ == '__main__':
    unittest.main()
