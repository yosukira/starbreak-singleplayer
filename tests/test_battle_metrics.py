"""Accepted damage is tested against real encounter controllers and wire replay."""
import json
import tempfile
import unittest
from unittest import mock

from localserver import game_settings
from localserver.battle_metrics import SUPPORTED
from localserver.battle_records import BattleRecorder, RecordStore
from localserver.replay import ServerCapture, ServerReplay
from test_replay import encounter, input_at


class CombatMetricTests(unittest.TestCase):
    def setup_encounter(self, map_id):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))))
        session, bridge, _ = encounter(map_id)
        session.prepare()
        recorder = BattleRecorder(session, RecordStore(directory))
        for tick in range(180):
            bridge._absorb(input_at(tick))
            session.step(.05)
        recorder.tick(.05)
        self.assertIsNotNone(recorder.document)
        return session, recorder

    def hit(self, session, oid, value, **fields):
        event = dict(kind='damage', victim=oid, value=value, ownerid=session.worldid, **fields)
        if session.options.map in ('lab_mid', 'lab_hard'):
            session._metric_test_seq = getattr(session, '_metric_test_seq', 180) + 1
            raw = input_at(session._metric_test_seq)
            raw['events'] = [event]
            session.bridge._absorb(raw)
            session._sync_player()
        elif hasattr(session, '_apply_bridge_events'):
            session.bridge.events.append(event)
            session._apply_bridge_events([])
        else: session._feedback(event)

    def test_graveyard_generators_are_separate_from_kills_and_takeover_starts_at_zero(self):
        for map_id in ('egy_hulk', 'hulk_normal'):
            with self.subTest(map=map_id):
                session, recorder = self.setup_encounter(map_id)
                ids = list(session.gens.hp)
                self.assertEqual(recorder.document['metrics']['generators_destroyed'], 0)
                for index, oid in enumerate(ids[:2]):
                    session.gens.active_gen = oid
                    session.gens.gen_state = 'armed'
                    hp = session.gens.hp[oid]
                    self.hit(session, oid, hp - 1)
                    recorder.tick(.02)
                    self.assertEqual(recorder.document['metrics']['generators_destroyed'], index)
                    self.hit(session, oid, 999999)
                    self.hit(session, oid, 999999)
                    recorder.tick(.02)
                    recorder.tick(.02)
                    self.assertEqual(recorder.document['metrics']['generators_destroyed'], index + 1)
                    self.assertEqual(recorder.document['metrics']['kills'], 0)
                # A branch must not claim generators destroyed before takeover.
                branch = BattleRecorder(session, recorder.store)
                branch.tick(.02)
                self.assertEqual(branch.document['metrics']['generators_destroyed'], 0)
                oid = ids[2]
                session.gens.active_gen = oid; session.gens.gen_state = 'armed'
                self.hit(session, oid, 999999)
                branch.tick(.02)
                self.assertEqual(branch.document['metrics']['generators_destroyed'], 1)

    def test_real_npc_overkill_duplicate_owner_and_native_retirement(self):
        for map_id in ('city_mid', 'city_boss', 'forest_mid', 'ice_boss'):
            with self.subTest(map=map_id):
                s, r = self.setup_encounter(map_id)
                c = s.controller
                if map_id.startswith('city_'):
                    actor = c.spawn_flyer()
                elif map_id == 'forest_mid':
                    actor = next(a for a in c.actors.values() if a['kind'] == 'frog')
                else:
                    actor = c._new('npc', 'ice.maxethrower', [100, -100])
                hp, oid = actor['hp'], actor['id']
                s._feedback(dict(kind='damage', victim=oid, value=100000, ownerid=s.worldid+1))
                self.assertEqual(r.pending_damage, 0)
                self.hit(s, oid, 100000)
                self.hit(s, oid, 100000)
                r.tick(.05)
                self.assertFalse(r.combat_observer.failed)
                self.assertEqual(r.document['metrics']['damage_dealt'], hp)
                self.assertEqual(r.document['metrics']['kills'], 1)
                if map_id.startswith('city_'):
                    actor = c.spawn_flyer()
                    hp2 = actor['hp']
                    s._feedback(dict(kind='destroy', victim=actor['id'], reason=6, ownerid=s.worldid))
                    self.assertIn(actor['id'], c.actors)
                    s._feedback(dict(kind='destroy', victim=actor['id'], reason=2, ownerid=s.worldid))
                    s._feedback(dict(kind='destroy', victim=actor['id'], reason=2, ownerid=s.worldid))
                    r.tick(.05)
                    self.assertEqual(r.document['metrics']['damage_dealt'], hp+hp2)
                    self.assertEqual(r.document['metrics']['kills'], 2)

    def test_forest_phase_caps_refills_invulnerability_and_single_boss_kill(self):
        s, r = self.setup_encounter('forest_boss')
        c = s.controller
        oid = c.boss['id']
        required = c.pack.entities['boss']['required_damage']
        self.hit(s, oid, 10, event_id=1)
        self.hit(s, oid, 10, event_id=1)
        self.assertEqual(r.pending_damage, 10)
        self.hit(s, oid, 100000)
        self.hit(s, oid, 100000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], required)
        self.assertEqual(r.document['metrics']['kills'], 0)
        c.phase_transition()
        self.hit(s, oid, 100000)  # cocoon is invulnerable
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], required)
        # Complete the real cocoon clock, then hit the next refillable bar.
        for _ in range(int(c.pack.entities['boss']['cocoon_s']/.05)+2):
            c.step(.05)
        self.assertEqual(c.phase, 1)
        self.assertNotEqual(c.action, 'cocoon')
        self.hit(s, oid, 17)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], required+17)
        c.begin_death()
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 1)

    def test_ice_phase_gate_does_not_count_sentinel_or_overflow(self):
        for map_id in ('ice_mid', 'ice_boss'):
            with self.subTest(map=map_id):
                s, r = self.setup_encounter(map_id)
                c = s.controller
                oid = 39 if map_id == 'ice_mid' else c.small['id']
                required = c.pack.entities['boss']['required_damage'] if map_id == 'ice_mid' else c.pack.entities['small']['body_damage']
                self.hit(s, oid, 1000000)
                self.hit(s, oid, 1000000)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], required)
                self.assertEqual(r.document['metrics']['kills'], 0)
                if map_id == 'ice_mid':
                    c.time = c.phase_started+c.pack.entities['boss']['phase_min_s']
                    c._phase_tick()
                else:
                    c.time = c.phase_started+c.pack.entities['small']['body_min_s']
                    c._small_phase_tick()
                self.hit(s, oid, 13)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], required+13)

    def test_patriarch_final_hp_and_hive_refill_floor(self):
        s, r = self.setup_encounter('sc_patriarch')
        hp = s.controller.hp
        self.hit(s, 4, 1000000)
        self.hit(s, 4, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        s, r = self.setup_encounter('sc_hive')
        c = s.controller
        hp = c.hp
        self.hit(s, s.overlay_objid, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp-1)
        self.assertEqual(r.document['metrics']['kills'], 0)
        # The client removing the one-HP display repairs it, never kills Hive.
        s._feedback(dict(kind='destroy', victim=s.overlay_objid, reason=2, ownerid=s.worldid))
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 0)
        c.step(c.pack.entities['hp']['last_refill_s']-c.time)
        self.assertTrue(c.kill_unlocked)
        refill = c.hp
        self.hit(s, s.overlay_objid, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp-1+refill)
        self.assertEqual(r.document['metrics']['kills'], 1)

    def test_city_gate_fractional_damage_and_projectiles_are_not_monsters(self):
        s, r = self.setup_encounter('city_boss')
        c = s.controller
        c.begin_tanks()
        tank = c.actors[c.tanks[0]]
        self.hit(s, tank['id'], 1000000)
        self.hit(s, tank['id'], 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], c.pack.entities['encounter']['tank_hp'])
        self.assertEqual(r.document['metrics']['kills'], 0)
        cargo = next(a for a in c.actors.values() if a['kind'] == 'cargo')
        prior = r.document['metrics']['damage_dealt']
        self.hit(s, cargo['id'], 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], prior)
        # A time gate can leave fractional server HP despite integer display HP.
        claw = c.new('claw', 'city.pincerleft', [100, -100], hp=10000, born=c.time-.123)
        floor = max(0, c.pack.entities['encounter']['claw_floor_start']*(1-.123/c.pack.entities['encounter']['claw_min_s']))
        before = claw['hp']
        self.hit(s, claw['id'], 1000000)
        r.tick(.05)
        self.assertAlmostEqual(r.document['metrics']['damage_dealt'], prior+max(0, before-floor))
        shot = c.new('shot', 'city.jtroop', [0, 0], hp=1)
        self.hit(s, shot['id'], 100)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 0)

    def test_observer_failure_does_not_retry_game_callback_or_invent_zero(self):
        s, r = self.setup_encounter('forest_mid')
        observer = r.combat_observer
        original = observer.original
        with mock.patch.object(observer, 'target', side_effect=ValueError('bad metric')), mock.patch.object(observer, 'original', wraps=original) as callback:
            self.hit(s, s.controller.boss['id'], 7)
            self.assertEqual(callback.call_count, 1)
        r.tick(.05)
        self.assertIsNone(r.document['metrics']['damage_dealt'])
        self.assertIsNone(r.document['metrics']['kills'])
        self.assertEqual(s.controller.damage, 7)

    def test_wave_npc_deaths_ignore_lifetime_and_projectiles(self):
        for map_id in ('dumb_wave','dumb_normal_wave'):
            with self.subTest(map=map_id):
                s,r=self.setup_encounter(map_id)
                oid=s._spawn_npc('sniper',s.room_time)
                actor=s.actors[oid];hp=actor['hp']
                self.hit(s,oid,1000000);self.hit(s,oid,1000000)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'],hp)
                self.assertEqual(r.document['metrics']['kills'],1)
                oid=s._spawn_npc('sniper',s.room_time)
                s._feedback(dict(kind='destroy',victim=oid,reason=6,ownerid=s.worldid))
                r.tick(.05)
                self.assertNotIn(oid,s.actors)
                self.assertEqual(r.document['metrics']['kills'],1,'A despawn is not a kill')
                oid=s._spawn_shot('rocketbullet',0,s.room_time,position=(0,0),vel=(0,0))
                s._feedback(dict(kind='destroy',victim=oid,reason=2,ownerid=s.worldid))
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'],hp)
                self.assertEqual(r.document['metrics']['kills'],1,'Projectile destruction is not a kill')

    def test_jungle_mouth_counts_capped_bars_and_one_delayed_boss_death(self):
        s,r=self.setup_encounter('jungle_mid')
        c=s.controller;rule=c.pack.entities['mouth'];hp=rule['hp_per_bar']
        for bar in range(rule['bars']):
            self.assertIn(c.state,('idle','sweep'))
            if bar%2:
                s._feedback(dict(kind='destroy',victim=s.mouth_id,reason=2,ownerid=s.worldid))
            else:self.hit(s,s.mouth_id,1000000)
            self.hit(s,s.mouth_id,1000000)  # recovery / finishing rejects hits
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'],(bar+1)*hp)
            self.assertEqual(r.document['metrics']['kills'],0)
            if bar+1<rule['bars']:s.step(rule['recovery_s']+.01)
        self.assertEqual(c.state,'finishing')
        s.step(rule['finish_delay_s']+.01);r.tick(.05)
        self.assertEqual(c.state,'defeated')
        self.assertEqual(r.document['result'],'victory')
        self.assertEqual(r.document['metrics']['kills'],1)
        self.assertFalse(r.combat_observer.failed)

    def test_jungle_npc_transformation_does_not_count_as_death(self):
        s,r=self.setup_encounter('jungle_mid')
        platform=s.platforms[0]
        oid=s._create('jungle.chrysalis',s.spawn_props('jungle.chrysalis'),s.room_time,hp=1125,
                      ai=dict(species='chrysalis',platform=platform))
        s._feedback(dict(kind='destroy',victim=oid,reason=6,ownerid=s.worldid))
        r.tick(.05)
        self.assertNotIn(oid,s.actors)
        self.assertEqual(r.document['metrics']['kills'],0)
        self.assertEqual(r.document['metrics']['damage_dealt'],0)
        spitter=next(row for row in s.actors.values() if row['vid']=='jungle.spitter')
        self.hit(s,spitter['objid'],1000000);r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'],600)
        self.assertEqual(r.document['metrics']['kills'],1)

    def test_bonesaw_counts_two_independent_capped_bosses_once_each(self):
        s,r=self.setup_encounter('sc_bonesaw')
        c=s.controller;required=c.pack.entities['boss']['required_damage']
        bosses=list(c.bosses)
        self.assertEqual(len(bosses),2)
        for number,oid in enumerate(bosses,1):
            self.hit(s,oid,17,event_id=number)
            self.hit(s,oid,17,event_id=number)
            self.hit(s,oid,1000000)
            self.hit(s,oid,1000000)
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'],number*required)
            self.assertEqual(r.document['metrics']['kills'],number-1,'The damage cap alone does not kill a timed boss')
            seconds=c.pack.entities['boss']['min_s']+c.pack.entities['boss']['death_s']+.2
            for _ in range(int(seconds/.05)+1):s.step(.05)
            r.tick(.05);r.tick(.05)
            self.assertTrue(c.bosses[oid]['dead'])
            self.assertEqual(r.document['metrics']['kills'],number)
        self.assertEqual(r.document['result'],'victory')
        self.assertFalse(r.combat_observer.failed)

    def test_hulk_mid_independent_bars_time_gates_and_minis(self):
        s, r = self.setup_encounter('hulk_mid')
        c = s.controller
        rule = c.rule
        expected = 0
        for oid, boss in c.bosses.items():
            self.hit(s, oid, 1000000)
            self.hit(s, oid, 1000000)
            expected += rule['first_damage']
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertFalse(boss['dead'])
            for _ in range(1400):
                s.bridge._absorb(input_at(180 + round(c.time * 20)))
                s.step(.05)
                if boss['phase'] == 2: break
            self.assertEqual(boss['phase'], 2)
            self.hit(s, oid, 1000000)
            self.hit(s, oid, 1000000)
            expected += rule['remaining_damage']
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertFalse(boss['dead'], 'Damage alone cannot bypass the death clock')
            for _ in range(800):
                s.bridge._absorb(input_at(180 + round(c.time * 20)))
                s.step(.05)
                if boss['dead']: break
            self.assertTrue(boss['dead'])
            r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 2)
        # The homing minis are actual NPCs; trails and plasma are not.
        s, r = self.setup_encounter('hulk_mid')
        s._action(dict(kind='mini', id=next(iter(s.controller.bosses)), index=0,
                       pos=[100, -100], angle=0))
        mini = next(a for a in s.actors.values() if a.get('kind') == 'mini')
        hp = mini['hp']
        self.hit(s, mini['id'], 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        self.assertFalse(r.combat_observer.failed)

    def test_hulk_mid_destructible_traps_count_actual_health_but_not_monster_kills(self):
        s, r = self.setup_encounter('hulk_mid')
        oid, trap = next(iter(s.traps.items()))
        hp = trap['hp']
        self.hit(s, oid, 29)
        self.hit(s, oid, 33)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], 62)
        self.assertEqual(r.document['metrics']['kills'], 0)
        self.hit(s, oid, 1000000)
        self.hit(s, oid, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp)
        self.assertEqual(r.document['metrics']['kills'], 0)
        self.assertNotIn(oid, s.traps)
        oid, trap = next(iter(s.traps.items()))
        hp2 = trap['hp']
        s._feedback(dict(kind='destroy', victim=oid, reason=2, ownerid=s.worldid))
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp + hp2)
        self.assertEqual(r.document['metrics']['kills'], 0)
        oid = next(iter(s.traps))
        s._feedback(dict(kind='destroy', victim=oid, reason=6, ownerid=s.worldid))
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp + hp2)
        self.assertEqual(r.document['metrics']['kills'], 0)
        self.assertFalse(r.combat_observer.failed)

    def test_sc_boss_refill_trigger_is_not_damage_and_repair_is_not_kill(self):
        for map_id in ('sc_boss', 'sc_boss_elite'):
            with self.subTest(map=map_id):
                s, r = self.setup_encounter(map_id)
                c = s.controller
                self.hit(s, s.head_id, 1000000)  # intro rejects damage
                self.assertEqual(r.pending_damage, 0)
                c._start_stage(1)
                expected = 0
                for stage in range(1, 4):
                    hp = c.hp
                    self.hit(s, s.head_id, 1000000)
                    self.hit(s, s.head_id, 1000000)
                    expected += hp
                    c.time = c.stage_started + c.rule['duration_s']
                    self.hit(s, s.head_id, 1000000)  # advances, does not hit the refill
                    self.assertEqual(c.stage, stage + 1)
                    self.assertEqual(c.hp, c.rule['hp'])
                    r.tick(.05)
                    self.assertEqual(r.document['metrics']['damage_dealt'], expected)
                self.assertEqual(r.document['metrics']['kills'], 0)
                c._start_stage(5)
                hp = c.hp
                self.hit(s, s.head_id, 1000000)
                c.time = c.stage_started + c.rule['duration_s']
                self.hit(s, s.head_id, 1000000)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], expected + hp)
                self.assertEqual(r.document['metrics']['kills'], 1)

                # Use a separate live encounter: cleanup after boss death is not
                # a minion kill, and early native retirement repairs the squid.
                s, r = self.setup_encounter(map_id)
                s._spawn_pair(s.room_time)
                oid, squid = next(iter(s.squids.items()))
                squid.update(active=True, active_at=s.room_time)
                hp = squid['hp']
                self.hit(s, oid, 1000000)
                s._feedback(dict(kind='destroy', victim=oid, reason=2, ownerid=s.worldid))
                new_oid = next(i for i, row in s.squids.items() if row is squid)
                self.assertNotEqual(new_oid, oid)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], hp)
                self.assertEqual(r.document['metrics']['kills'], 0)
                s.room_time += .6
                self.hit(s, new_oid, 1)
                self.hit(s, new_oid, 1)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], hp)
                self.assertEqual(r.document['metrics']['kills'], 1)
                self.assertFalse(r.combat_observer.failed)

    def test_jungle_boss_effective_hp_not_raw_overkill(self):
        from localserver.maps.jungle_boss import params
        s, r = self.setup_encounter('jungle_boss')
        c = s.controller
        c.activate()
        oid = next(iter(s.boss['parts']))
        expected = 0
        for phase in range(1, 5):
            self.assertEqual(c.phase, phase)
            s._feedback(dict(kind='damage', victim=oid, value=1000000, ownerid=0))
            self.hit(s, oid, 1000000)
            expected += params.PHASE_POOLS[phase - 1]
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 0)
            for _ in range(2000):
                c.step(.05)
                if c.phase > phase or c.kill_unlocked: break
            if phase < 4: self.assertEqual(c.phase, phase + 1)
        self.assertTrue(c.kill_unlocked)
        self.hit(s, oid, 1)  # the final clock still requires a confirming hit
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected)
        self.assertEqual(r.document['metrics']['kills'], 1)
        self.assertFalse(r.combat_observer.failed)

    def test_dumb_boss_overkill_does_not_spill_into_later_stage(self):
        s, r = self.setup_encounter('dumb_boss')
        c = s.controller
        heavy = s.actors[c.heavy_id]
        hp = heavy['hp']
        self.hit(s, c.heavy_id, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        for _ in range(60):
            s.bridge._absorb(input_at(180 + round(c.time * 20)))
            s.step(.05)
            if c.vulnerable: break
        self.assertTrue(c.vulnerable)
        expected = hp
        for stage in range(1, len(c.gates) + 1):
            self.assertEqual(c.stage, stage)
            self.hit(s, c.pack.boss_id, 1000000)
            self.hit(s, c.pack.boss_id, 1000000)
            expected += c.gate
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 1)
            c._leave_attack()
        self.assertTrue(c.final_stage_cleared)
        c._enter('death')
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 2)
        self.assertFalse(r.combat_observer.failed)

    def test_jungle_boss_segmented_enemy_is_one_kill(self):
        s, r = self.setup_encounter('jungle_boss')
        oid = next(iter(s.controller.worms))
        actor = s.actors[oid]
        hp = actor['hp']
        chain = list(actor.get('chain') or [])
        self.assertGreater(len(chain), 1)
        self.hit(s, oid, 1000000)
        for part in chain: self.hit(s, part, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        self.assertFalse(r.combat_observer.failed)

    def test_cave_weak_mouth_refills_and_roller_replacement(self):
        s, r = self.setup_encounter('cave_boss')
        c = s.controller
        expected = 0
        for round_number in (1, 2):
            if round_number == 2:
                c._round(round_number)
                for event in c.drain(): s._action(event)
                for event in c.step(2.5): s._action(event)
            for oid, mouth in c.mouths.items():
                hp = mouth['hp']
                self.hit(s, oid, 1000000)
                self.hit(s, oid, 1000000)
                if mouth['weak']: expected += hp
            for oid in list(c.pack.scales)[:1] + list(c.pack.body_parts)[:1]:
                self.hit(s, oid, 1000000)  # hit-count armor / sentinel body HP
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 0)
        roller = c._new('roller', 'cave.rollersnake', [100, -100],
                        roll_speed=100, roll_direction=-1)
        for event in c.drain(): s._action(event)
        hp, oid = roller['hp'], roller['id']
        s._feedback(dict(kind='destroy', victim=oid, reason=5, ownerid=s.worldid))
        r.tick(.05)
        self.assertNotIn(oid, c.actors)
        self.assertEqual(r.document['metrics']['kills'], 0)
        replacement = next(a for a in c.actors.values() if a['kind'] == 'roller')
        self.assertEqual(replacement['hp'], hp)
        self.hit(s, replacement['id'], 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected + hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        self.assertFalse(r.combat_observer.failed)

    def test_dumb_mid_refill_sentinel_fragment_grace_and_single_encounter_death(self):
        s, r = self.setup_encounter('dumb_mid')
        c = s.controller
        expected = 0
        while c.group:
            c._activate()
            for event in c.take_events(): s._controller_event(event)
            for oid in list(c.group):
                self.hit(s, oid, 1000000)
                self.hit(s, oid, 1000000)
                expected += c.config['piece_damage_gate']
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 0)
            c._scatter()
            for event in c.take_events(): s._controller_event(event)
            for oid in c.group: self.hit(s, oid, 1000000)  # regrouping is invulnerable
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            if c.group:
                c._assemble()
                for event in c.take_events(): s._controller_event(event)
        tiers = set()
        while c.fragments:
            oid = next(iter(c.fragments))
            piece = c.parts[oid]
            if piece['invulnerable']:
                self.hit(s, oid, 1000000)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], expected)
                for event in c.step(c.config['fragment_scatter_grace_s'] + .01):
                    s._controller_event(event)
            tiers.add(piece['tier'])
            expected += max(0, piece['hp'])
            if piece['tier'] == 1:
                # Native HP retirement follows the same accepted health path.
                s._feedback(dict(kind='destroy', victim=oid, reason=2, ownerid=s.worldid))
            else: self.hit(s, oid, 1000000)
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 0)
        s.step(.01)
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(tiers, {0, 1, 2})
        self.assertEqual(r.document['metrics']['kills'], 1)
        self.assertEqual(r.document['result'], 'victory')
        self.assertFalse(r.combat_observer.failed)

    def test_lab_mid_blocks_pending_gate_zombie_and_vulnerable_boss(self):
        s, r = self.setup_encounter('lab_mid')
        c = s.controller
        self.hit(s, s.pack.boss_objid, 1000)
        self.assertEqual(r.pending_damage, 0, 'The body is invulnerable before the blocks die')
        expected = 0
        for oid, block in c.blocks.items():
            expected += block['hp']
            self.hit(s, oid, block['hp'] + 100)
            self.hit(s, oid, 100)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected)
        self.assertEqual(r.document['metrics']['kills'], 0)
        for event in c.step(c.check_period * len(c.blocks) + c.segment_period + 1):
            s._handle_event(event)
        self.assertEqual(c.state, 'vulnerable')
        self.assertTrue(c.all_blocks_dead)
        oid = s._spawn_zombie(100, -100)
        hp = s.zombies[oid]['hp']
        self.hit(s, oid, 1000000)
        self.hit(s, oid, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected + hp)
        self.assertEqual(r.document['metrics']['kills'], 1)
        expected += hp + c.boss_hp
        self.hit(s, s.pack.boss_objid, c.boss_hp + 100)
        self.hit(s, s.pack.boss_objid, 100)
        for event in c.step(.01): s._handle_event(event)
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected)
        self.assertEqual(r.document['metrics']['kills'], 2)
        self.assertEqual(r.document['result'], 'active')
        s._step_death(s.death_duration + s.DEATH_END_EXTRA + .01)
        r.tick(.05)
        self.assertEqual(r.document['result'], 'victory')
        self.assertFalse(r.combat_observer.failed)

    def test_lab_hard_phase_caps_and_both_native_minion_death_paths(self):
        s, r = self.setup_encounter('lab_hard')
        c = s.controller
        s._emit_adds(dict(recipes=[dict(props=dict(xmp=100, ymp=-100))]))
        s._spawn_seeker_wave(dict(props=dict(xmp=100, ymp=-100)))
        expected = 0
        for actors in (s.adds, s.seekers):
            oid = next(iter(actors))
            expected += actors[oid]['hp']
            self.hit(s, oid, 1000000)
            self.hit(s, oid, 1000000)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['damage_dealt'], expected)
        self.assertEqual(r.document['metrics']['kills'], 2)
        s._spawn_seeker_wave(dict(props=dict(xmp=100, ymp=-100)))
        oid = next(iter(s.seekers))
        hp = s.seekers[oid]['hp']
        raw = input_at(1000)
        raw['events'] = [dict(kind='destroy', victim=oid, reason=2, ownerid=s.worldid)]
        s.bridge._absorb(raw)
        s._sync_player()
        expected += hp
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 3)
        for index in range(len(c.machine.specs)):
            self.assertEqual(c.machine.index, index)
            need = c.machine.spec.damage_required
            self.hit(s, s.pack.boss_objid, need + 1000000)
            self.hit(s, s.pack.boss_objid, 1000000)
            expected += need
            r.tick(.05)
            self.assertEqual(r.document['metrics']['damage_dealt'], expected)
            self.assertEqual(r.document['metrics']['kills'], 3)
            # Isolate the metric adapter from the separately tested wall clock.
            with mock.patch.object(c.machine, 'hold', False):
                c.machine.step(c.machine.spec.duration_s + .01)
        self.assertTrue(c.machine.final_ready or c.machine.finished)
        s._handle_event(dict(kind='death'))
        r.tick(.05)
        r.tick(.05)
        self.assertEqual(r.document['metrics']['kills'], 4)
        self.assertEqual(r.document['result'], 'active')
        s._step_death(120)
        r.tick(.05)
        self.assertEqual(r.document['result'], 'victory')
        self.assertFalse(r.combat_observer.failed)

    def test_lab_observation_error_never_retries_damage_or_propagates_to_game(self):
        s, r = self.setup_encounter('lab_mid')
        oid = s._spawn_zombie(100, -100)
        hp = s.zombies[oid]['hp']
        observer = r.combat_observer
        with mock.patch.object(observer, 'target', side_effect=ValueError('bad metric')):
            self.hit(s, oid, 7)
        self.assertEqual(s.zombies[oid]['hp'], hp - 7)
        r.tick(.05)
        self.assertIsNone(r.document['metrics']['damage_dealt'])
        self.assertIsNone(r.document['metrics']['kills'])
        callback = mock.Mock(side_effect=RuntimeError('original game callback'))
        with self.assertRaisesRegex(RuntimeError, 'original game callback'):
            observer.watch_call(lambda: None, callback)
        self.assertEqual(callback.call_count, 1)

    def test_lab_late_binding_only_credits_new_damage_once(self):
        for map_id in ('lab_mid', 'lab_hard'):
            with self.subTest(map=map_id):
                with game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                    s, bridge, _ = encounter(map_id)
                    s.prepare()
                    for tick in range(180):
                        bridge._absorb(input_at(tick))
                        s.step(.05)
                    oid = next(iter(s.controller.blocks)) if map_id == 'lab_mid' else s.pack.boss_objid
                    s.controller.add_damage(31, oid)
                    directory = self.enterContext(tempfile.TemporaryDirectory())
                    recorder = BattleRecorder(s, RecordStore(directory))
                    recorder.bind(s)
                    recorder.tick(.05)
                    self.hit(s, oid, 7)
                    recorder.tick(.05)
                    self.assertEqual(recorder.document['metrics']['damage_dealt'], 7)
                    self.assertEqual(recorder.document['metrics']['kills'], 0)
                    self.assertFalse(recorder.combat_observer.failed)

    def test_gy_native_host_counts_npc_and_boss_death_once_each(self):
        for map_id in ('egy_hulk', 'hulk_normal'):
            with self.subTest(map=map_id):
                s, r = self.setup_encounter(map_id)
                oid, actor = next((oid, actor) for oid, actor in s.intro_units.items()
                                  if oid not in s.intro_destroyed)
                hp = actor['hp']
                event = dict(kind='damage', victim=oid, value=hp + 100, ownerid=s.worldid)
                raw = input_at(181)
                raw['events'] = [event, event]
                s.bridge._absorb(raw)
                s._apply_bridge_events([])
                r.tick(.05)
                self.assertEqual(r.document['metrics']['damage_dealt'], hp)
                self.assertEqual(r.document['metrics']['kills'], 1)
                s._begin_death()
                r.tick(.05)
                r.tick(.05)
                self.assertEqual(r.document['metrics']['kills'], 2)
                self.assertEqual(r.document['result'], 'victory')

    def test_real_session_packets_rebuild_without_metric_observer(self):
        for map_id in sorted(SUPPORTED):
            with self.subTest(map=map_id):
                directory = self.enterContext(tempfile.TemporaryDirectory())
                with game_settings.event_snapshot(game_settings.normalize(dict(map=map_id))):
                    s, bridge, _ = encounter(map_id)
                    r = BattleRecorder(s, RecordStore(directory))
                    capture = ServerCapture(s)
                    capture.run('prepare')
                    for tick in range(720 if map_id in ('sc_boss', 'sc_boss_elite') else 240):
                        raw = input_at(tick)
                        c = s.controller
                        if tick % 30 == 20 and c is not None:
                            targets = list(getattr(c, 'actors', {}))
                            targets += list(getattr(s, 'actors', {}))
                            if map_id=='jungle_mid': targets += [s.mouth_id]
                            targets += [getattr(c, 'boss', None).get('id')] if getattr(c, 'boss', None) else []
                            if map_id == 'ice_boss': targets += [c.small['id']]
                            if map_id == 'sc_patriarch': targets += [4]
                            if map_id == 'sc_hive': targets += [s.overlay_objid]
                            if map_id in ('sc_bonesaw', 'hulk_mid'): targets += list(c.bosses)
                            if map_id in ('sc_boss', 'sc_boss_elite'): targets += [s.head_id]
                            if map_id == 'dumb_mid': targets += list(c.parts)
                            if map_id == 'lab_mid': targets += list(c.blocks)
                            if map_id in ('lab_mid', 'lab_hard'): targets += [s.pack.boss_objid]
                            oid = next((oid for oid in targets if r.combat_observer.target(oid) is not None), None)
                            if oid is not None:
                                raw['events'] = [dict(kind='damage', victim=oid, value=77, ownerid=s.worldid)]
                        bridge._absorb(raw)
                        capture.run('step', .05)
                        r.tick(.05)
                    self.assertFalse(r.combat_observer.failed)
                    self.assertGreater(r.document['metrics']['damage_dealt'], 0)
                    replay = ServerReplay(json.loads(json.dumps(capture.document())))
                    while replay.advance(): pass
                    self.assertIsNone(capture.failed)
                    capture.detach()
