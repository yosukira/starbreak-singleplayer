import copy
import io
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid
import zipfile

from localserver import battle_records as records, paths
from localserver.live.bridge import PlayerState
from localserver.live.bridge import PlayerBridge
from localserver.server import LocalServer, parse_args, _map_session_class


def sample():
    row=json.loads((paths.ROOT/'data/defaults/class_loadouts.json').read_text())['1']
    return dict(schema=records.SCHEMA,id=str(uuid.uuid4()),started=time.time(),duration=15,
        shell=1,map='egy_hulk',map_title='Elite Graveyard Boss',version='test',result='victory',
        seed=2**64-1,parameters=dict(loadout=row,balance={'enemy_hp':1,'spawn_rate':.66},
        drops={'health_percent':5},turrets={'upper':None},missiles={},missile_delay_ms=275),
        metrics=dict(damage_dealt=300,kills=2,health_packs=None),changes=[])


def participating(session):
    # Existing outcome tests describe played battles; idle behavior is tested
    # separately below with actual empty bridge feedback.
    if not getattr(session, 'bridge', None):
        session.bridge=SimpleNamespace(snapshot=lambda:(session.player, {'gameplay':True}))
    return session


class RecordTests(unittest.TestCase):
    def test_graveyard_combat_timer_uses_actual_ship_arrival_on_both_clients(self):
        for web in (False,True):
            with self.subTest(web=web),tempfile.TemporaryDirectory() as directory:
                initial=records.parameters(sample()['parameters']);initial['skip_intro']=False
                session=SimpleNamespace(player=PlayerState(objid=1,worldid=2,hp=100,alive=True),
                    options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=web),seed=30,
                    phase='intro',boss_time=0.,ship_controller=SimpleNamespace(arrival_time=14.))
                recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
                with mock.patch.object(records,'snapshot',return_value=initial):
                    recorder.tick(1);self.assertEqual(recorder.document['combat_duration'],0)
                    session.phase='fighting';session.boss_time=13.9
                    recorder.tick(.2);self.assertEqual(recorder.document['combat_duration'],0,'Fighting/platform deployment is earlier than ship stability')
                    session.boss_time=14.1;recorder.tick(.2)
                    self.assertEqual(recorder.document['combat_duration'],.1)
                    session.boss_time=14.3;recorder.tick(.2)
                    self.assertEqual(recorder.document['combat_duration'],.3)
                    row=records.validate(recorder.document)
                    self.assertEqual(row['duration'],1.6);self.assertEqual(row['combat_duration'],.3)
                    self.assertIsNone(records.validate(sample())['combat_duration'],'Legacy records cannot claim an invented adjusted time')
                    with self.assertRaises(ValueError):records.validate(dict(row,combat_duration=2))

    def test_roundtrip_dedup_and_snapshot_are_immutable(self):
        with tempfile.TemporaryDirectory() as directory:
            source=records.RecordStore(Path(directory)/'one');destination=records.RecordStore(Path(directory)/'two')
            value=sample();original=copy.deepcopy(value);source.save(value)
            value['parameters']['loadout']['mk_']=2
            name,blob=source.export([value['id']])
            self.assertTrue(name.startswith('Wildfire-Elite Graveyard Boss-'));self.assertTrue(name.endswith('.sbreplay'))
            exported=json.loads(blob);self.assertEqual(exported['parameters']['loadout'],records.loadout(original['parameters']['loadout']))
            self.assertEqual(exported['seed'],str(2**64-1))
            self.assertEqual(destination.import_bytes(blob),dict(imported=1,duplicates=0))
            self.assertEqual(destination.import_bytes(blob),dict(imported=0,duplicates=1))
            self.assertEqual(destination.get(value['id'])['source'],'import')
            self.assertIsNone(destination.get(value['id'])['metrics']['health_packs'])

    def test_batch_validation_is_atomic_and_rejects_archive_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory)
            for filename,blob in [('bad.sbreplay',b'{"schema":"bad"}'),('../evil.sbreplay',records.encoded(sample()))]:
                out=io.BytesIO()
                with zipfile.ZipFile(out,'w') as archive:
                    archive.writestr('good.sbreplay',records.encoded(sample()))
                    archive.writestr(filename,blob)
                with self.assertRaises(ValueError):store.import_bytes(out.getvalue())
                self.assertEqual(store.list(),[])

    def test_export_allowlist_never_includes_arbitrary_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory);value=sample()
            value.update(account='private-account',host='private-host',password='private-password')
            value['parameters']['private_file']='private-path'
            value['parameters']['loadout']['items_'][0]['account']='private-item'
            value['rooms']=[dict(role='entry',seed='123',entered=0,source_file='private-path')]
            store.save(value);_,blob=store.export([value['id']])
            self.assertNotIn(b'private-',blob)
            self.assertEqual(json.loads(blob)['rooms'],[dict(role='entry',seed='123',entered=0)])

    def test_interrupted_recovery_and_active_export_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory);value=sample();value['result']='active';store.save(value)
            with self.assertRaises(ValueError):store.export([value['id']])
            with store.connect() as db:db.execute('UPDATE records SET updated=?',(time.time()-40,))
            self.assertEqual(store.get(value['id'])['result'],'interrupted')
            self.assertEqual(json.loads(store.export([value['id']])[1])['result'],'interrupted')

    def test_zip_same_second_filenames_and_unknown_metrics(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory);a=sample();b=copy.deepcopy(a);b['id']=str(uuid.uuid4())
            store.save(a);store.save(b);name,blob=store.export([a['id'],b['id']])
            with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                self.assertEqual(len(set(archive.namelist())),2)
            self.assertTrue(name.endswith('.zip'))

    def test_shutdown_finalizes_only_active_records_owned_by_exited_child(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory)
            active=sample();active['result']='active';store.save(active,owner=100)
            finished=sample();store.save(finished,owner=100)
            other=sample();other['result']='active';store.save(other,owner=200)
            store.finish_owner(100)
            self.assertEqual(store.get(active['id'])['result'],'interrupted')
            self.assertEqual(store.get(finished['id'])['result'],'victory')
            self.assertEqual(store.get(other['id'])['result'],'active')

    def test_export_limits_expanded_data_to_import_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory)
            a=sample();b=sample();store.save(a);store.save(b)
            size=len(records.encoded(store.get(a['id'])))
            with mock.patch.object(records,'MAX_IMPORT',size+100):
                with self.assertRaisesRegex(ValueError,'select fewer'):
                    store.export([a['id'],b['id']])

    def test_recorder_waits_for_player_and_records_outcomes_and_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store=records.RecordStore(directory)
            initial=records.parameters(sample()['parameters'])
            session=SimpleNamespace(player=None,options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=True),seed=30,phase='waiting')
            recorder=records.BattleRecorder(participating(session),store)
            with mock.patch.object(records,'snapshot',return_value=initial):
                recorder.tick(.1);self.assertEqual(store.list(),[])
                session.player=PlayerState(objid=1,worldid=2,hp=100,alive=True)
                recorder.tick(.1);recorder.damage(50,True);recorder.tick(.1)
                self.assertEqual(recorder.document['metrics']['damage_dealt'],50)
                self.assertEqual(recorder.document['metrics']['kills'],1)
                changed=copy.deepcopy(initial);changed['missile_delay_ms']=500
                with mock.patch.object(records,'snapshot',return_value=changed):
                    recorder.tick(1.1)
                self.assertEqual(len(recorder.document['changes']),1)
                self.assertEqual(recorder.document['parameters']['missile_delay_ms'],275)
                session.phase='death';recorder.tick(.1);recorder.finish('interrupted')
                self.assertEqual(store.list()[0]['result'],'victory')
                self.assertIsNone(store.list()[0]['metrics']['health_packs'])

    def test_no_gameplay_input_never_creates_history_on_either_client(self):
        for web in (False, True):
            for outcome in ('victory', 'defeat', 'interrupted'):
                with self.subTest(web=web,outcome=outcome),tempfile.TemporaryDirectory() as directory:
                    bridge=PlayerBridge(0)
                    bridge._absorb(dict(seq=1,player=dict(objid=1,worldid=2,hp=100,alive=True),
                        keys=dict(gameplay=False,interact=False),events=[]))
                    session=SimpleNamespace(options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=web),
                        player=bridge.snapshot()[0],bridge=bridge,seed=30,phase='fighting')
                    recorder=records.BattleRecorder(session,records.RecordStore(directory))
                    initial=records.parameters(sample()['parameters'])
                    with mock.patch.object(records,'snapshot',return_value=initial):
                        recorder.tick(.5)
                        # Idle actors may fall, heal, get hit, or deal passive
                        # damage. Editing equipment alone is also not playing.
                        session.player.xmp=1000;session.player.hp=50
                        recorder.damage(100,True);recorder.generator_destroyed()
                        changed=copy.deepcopy(initial);changed['missile_delay_ms']=500
                        with mock.patch.object(records,'snapshot',return_value=changed):
                            recorder.tick(1)
                        self.assertEqual(recorder.store.list(),[])
                        recorder.finish(outcome)
                    self.assertTrue(recorder.closed)
                    self.assertEqual(recorder.store.list(),[])

    def test_first_gameplay_tap_saves_whole_battle_and_starting_configuration(self):
        for web in (False, True):
            with self.subTest(web=web),tempfile.TemporaryDirectory() as directory:
                bridge=PlayerBridge(0)
                feedback=dict(seq=1,player=dict(objid=1,worldid=2,hp=100,alive=True),keys=dict(gameplay=False),events=[])
                bridge._absorb(feedback)
                session=SimpleNamespace(options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=web),
                    player=bridge.snapshot()[0],bridge=bridge,seed=30,phase='fighting')
                recorder=records.BattleRecorder(session,records.RecordStore(directory))
                initial=records.parameters(sample()['parameters'])
                with mock.patch.object(records,'snapshot',return_value=initial):
                    recorder.tick(.5);recorder.damage(17)
                    self.assertEqual(recorder.store.list(),[])
                    feedback['keys']['gameplay']=True;bridge._absorb(feedback)
                    recorder.tick(1)
                    self.assertEqual(len(recorder.store.list()),1)
                    # A later room/input reset cannot discard a played battle.
                    feedback['keys']['gameplay']=False;bridge._absorb(feedback)
                    recorder.tick(.5);recorder.finish('interrupted')
                saved=recorder.store.get(recorder.identifier)
                self.assertEqual(saved['duration'],2)
                self.assertEqual(saved['metrics']['damage_dealt'],17)
                self.assertEqual(saved['parameters'],initial)

    def test_snapshot_uses_delivered_item_speeds_and_frozen_names(self):
        row=sample()['parameters']['loadout']
        kind=row['items_'][0]['type_']
        session=SimpleNamespace(options=SimpleNamespace(map='egy_hulk',player_type=1),
            server=SimpleNamespace(class_loadouts={1:row},item_descriptions={kind:{'name_':'Frozen weapon','statboosts_':[{'stat_':9,'val_':3}],'obj_':[{'speed_':222}]}}),
            skip_intro=False,projectiles=SimpleNamespace(missile_delay_seconds=.275))
        first=records.snapshot(session)
        session.server.item_descriptions[kind]['obj_'][0]['speed_']=999
        self.assertEqual(first['projectile_speeds'][str(kind)]['0'],222)
        self.assertEqual(first['items'][0]['name'],'Frozen weapon')
        self.assertEqual(first['items'][0]['base'],[{'stat_':9,'val_':3}])

    def test_cumulative_metrics_are_not_double_counted_or_lost_after_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            session=SimpleNamespace(options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=True),
                player=PlayerState(objid=1,worldid=2,hp=100,alive=True,battle_metrics={'protocol':'player-metrics-v1','health_packs':2,'ammo_packs':1,'healing':30}),phase='fighting')
            recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
            with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                recorder.tick(.1);recorder.tick(.1)
                self.assertEqual(recorder.document['metrics']['healing'],30)
                session.player.raw['battle_metrics'].update(health_packs=0,ammo_packs=0,healing=0)
                recorder.tick(.1)
                session.player.raw['battle_metrics'].update(health_packs=1,healing=10)
                recorder.tick(.1)
                self.assertEqual(recorder.document['metrics']['health_packs'],3)
                self.assertEqual(recorder.document['metrics']['healing'],40)

    def test_taken_damage_is_cumulative_survives_reload_and_keeps_legacy_unknown(self):
        for web in (False,True):
            with self.subTest(web=web),tempfile.TemporaryDirectory() as directory:
                player=PlayerState(objid=1,worldid=2,hp=100,alive=True,room_seed=1,
                    battle_metrics={'protocol':'player-metrics-v1','damage_taken':17,'healing':9})
                session=SimpleNamespace(options=SimpleNamespace(map='egy_hulk',player_type=1,web_client=web),player=player,phase='fighting')
                recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
                with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                    recorder.tick(.1);recorder.tick(.1)
                    self.assertEqual(recorder.document['metrics']['damage_taken'],17)
                    # A later room starts a fresh cumulative counter. Even a
                    # larger initial counter belongs entirely to the new room.
                    player.raw.update(room_seed=2,battle_metrics={'protocol':'player-metrics-v1','damage_taken':50,'healing':0})
                    recorder.tick(.1);recorder.finish('interrupted')
                saved=records.RecordStore(directory).get(recorder.identifier)
                self.assertEqual(saved['metrics']['damage_taken'],67)
                self.assertEqual(saved['metrics']['healing'],9)
                legacy=sample();recorder.store.save(legacy)
                self.assertIsNone(recorder.store.get(legacy['id'])['metrics']['damage_taken'])

    def test_real_full_run_room_transition_keeps_one_battle_and_resets_pickup_baseline(self):
        for map_id in ('egy_run', 'eub_run'):
            with self.subTest(map=map_id), tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, SB_BATTLE_RECORDS_PATH=directory, SB_RECORD_REPLAY='0'):
                options=parse_args(['--map',map_id,'--class-loadouts',str(paths.ROOT/'data/defaults/class_loadouts.json')])
                server=LocalServer(options)
                server.poll_live_room_inventory=lambda *args:False
                server._request_pvid_loads=mock.Mock()
                channel=SimpleNamespace(send_frame=lambda *args:None)
                bridge=PlayerBridge(0)
                kind=_map_session_class(map_id)
                first=kind(server,channel,options,bridge=bridge)
                server.live_session=first
                recorder=records.attach(first)
                def observe(session,count,healing):
                    x,y=session.run.destination_spawn(session.run.current,session.run.entry)
                    context=session.bridge.native_context
                    session.bridge._absorb(dict(seq=session._input_serial,room_context=context,
                        player=dict(objid=1,worldid=2,xmp=x,ymp=y,hp=500,maxhp=500,alive=True,
                            room_seed=context['room_seed'],ammo=12,
                            battle_metrics=dict(protocol='player-metrics-v1',health_packs=count,ammo_packs=0,healing=healing)),
                        keys=dict(interact=False,gameplay=True)))
                    session.step(.02)
                observe(first,2,30)
                self.assertIsNotNone(recorder.document)
                identifier=recorder.document['id']
                link=next(row for row in first.instance.recipe['links'].values() if row['destination'] in first.run.pack.room_names and row['destination']!=first.run.current and first.run.allows(row))
                self.assertTrue(server._commit_live_room_transition(channel,first,link,bridge.snapshot()[0]))
                self.assertFalse(recorder.closed,'An internal door is not an interrupted battle')
                second=kind(server,channel,options,bridge=bridge)
                self.assertIs(records.attach(second),recorder)
                # A newly loaded room can already have a larger cumulative
                # counter than the old room; count its whole value once.
                observe(second,4,50)
                self.assertEqual(recorder.document['metrics']['health_packs'],6)
                self.assertEqual(recorder.document['metrics']['healing'],80)
                self.assertEqual([r['role'] for r in recorder.document['rooms']],[first.instance.role,second.instance.role])
                second.current.phase='done'
                observe(second,4,50)
                self.assertFalse(recorder.closed,'Finishing one room is not winning the level')
                second.run.completed=True
                observe(second,4,50)
                self.assertEqual(recorder.store.get(identifier)['result'],'victory')
                self.assertEqual(len(recorder.store.list()),1)
                second.stop()

    def test_restart_is_a_new_battle_even_when_bridge_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ,SB_BATTLE_RECORDS_PATH=directory,SB_RECORD_REPLAY='0'):
            options=SimpleNamespace(map='egy_run',player_type=1)
            run=SimpleNamespace(seed=123,completed=False)
            player=PlayerState(objid=1,worldid=2,hp=100,alive=True)
            def wrapper():
                return SimpleNamespace(options=options,run=run,instance=SimpleNamespace(role='entry',seed=1,visits=1),
                    bridge=SimpleNamespace(snapshot=lambda:(player,{'gameplay':True}),native_context={}),_ready=True,
                    step=lambda dt:None,stop=lambda **kw:None,log=lambda message:None)
            with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                first=wrapper();a=records.attach(first);first.step(.1);first.stop(stop_bridge=False)
                self.assertTrue(a.closed)
                # Moving after a completed/interrupted run never creates a
                # second battle on that same run object. Restart creates one.
                remaining=wrapper();self.assertIs(records.attach(remaining),a);remaining.step(.1)
                self.assertEqual(len(a.store.list()),1)
                run=SimpleNamespace(seed=456,completed=False)
                second=wrapper();b=records.attach(second);second.step(.1)
                self.assertNotEqual(a.identifier,b.identifier)
                self.assertEqual(len(a.store.list()),2)
                self.assertEqual(a.store.get(a.identifier)['result'],'interrupted')
                second.stop()

    def test_target_cleared_by_controller_still_records_local_player_defeat(self):
        with tempfile.TemporaryDirectory() as directory:
            bridge=PlayerBridge(0)
            session=SimpleNamespace(options=SimpleNamespace(map='lab_hard',player_type=1),
                bridge=bridge,player=None,phase='waiting',seed=123)
            recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
            feedback=dict(seq=1,player=dict(objid=1,worldid=2,hp=100,alive=True),keys={'gameplay':True},events=[])
            bridge._absorb(feedback)
            with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                recorder.tick(.1)
                self.assertEqual(recorder.store.list(),[],'Buffered old feedback cannot start a new fight')
                session.phase='fighting';session.player=bridge.snapshot()[0];recorder.tick(.1)
                feedback['seq']=2;feedback['player'].update(hp=0,alive=False)
                bridge._absorb(feedback);session.player=None;recorder.tick(.1)
            self.assertTrue(recorder.closed)
            self.assertEqual(recorder.store.list()[0]['result'],'defeat')

    def test_home_is_not_a_battle_and_boss_death_cutscene_is_already_a_win(self):
        with mock.patch.object(records,'RecordStore') as store:
            self.assertIsNone(records.attach(SimpleNamespace(options=SimpleNamespace(map='home'))))
            store.assert_not_called()
        with tempfile.TemporaryDirectory() as directory:
            player=PlayerState(objid=1,worldid=2,hp=100,alive=True)
            session=SimpleNamespace(options=SimpleNamespace(map='egy_run',player_type=1),run=SimpleNamespace(seed=1,completed=False),
                instance=SimpleNamespace(role='boss',seed=1,visits=1),current=SimpleNamespace(phase='fight'),
                bridge=SimpleNamespace(snapshot=lambda:(player,{'gameplay':True}),native_context={}),_ready=True)
            recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
            with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                recorder.tick(.1)
                session.current.phase='death'
                recorder.tick(.1)
            self.assertEqual(recorder.store.list()[0]['result'],'victory')
            self.assertFalse(session.run.completed,'Statistics do not advance the gameplay cutscene')

    def test_est_mid_and_boss_defeated_state_is_a_victory(self):
        for map_id in ('jungle_mid','jungle_boss'):
            with self.subTest(map=map_id),tempfile.TemporaryDirectory() as directory:
                session=SimpleNamespace(options=SimpleNamespace(map=map_id,player_type=1),
                    player=PlayerState(objid=1,worldid=2,hp=100,alive=True),phase='fighting',seed=123)
                recorder=records.BattleRecorder(participating(session),records.RecordStore(directory))
                with mock.patch.object(records,'snapshot',return_value=records.parameters(sample()['parameters'])):
                    recorder.tick(.1)
                    session.phase='defeated';recorder.tick(.1)
                recorder.finish('interrupted')
                self.assertTrue(recorder.closed)
                self.assertEqual(recorder.store.list()[0]['result'],'victory')


if __name__=='__main__':unittest.main()
