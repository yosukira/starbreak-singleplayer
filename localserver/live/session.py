"""Live Hulk Hard Boss session: the orchestrator that drives the real client."""

import base64
import copy
import collections
import io
import json
from .. import jsonio
import math
import os
import random
import threading
import time

from .. import game_settings
from . import constants
from .bridge import PlayerBridge, PlayerState
from .enemies import EnemyManager, Enemy, BEHAVIOUR
from .gens import GenCycle
from .projectiles import ProjectileSystem
from .turrets import (build_batteries, normalize_missile_counts,
                         normalize_turret_counts, roll_turret_layout,
                         TURRET_ROW_SLOTS)
from .wire import DeltaBatch
from ..supply import SupplyDrops
from .motion import MotionTrack, LoopingTrack
from .platforms import Platforms
from .ship import ShipMotion

class EventLog(object):
    def __init__(self, limit=2048):
        self.limit = limit
        self.rows = []

    def add(self, kind, **values):
        row = {'at': round(time.time(), 3), 'kind': kind}
        row.update(values)
        self.rows.append(row)
        if len(self.rows) > self.limit:
            self.rows = self.rows[-self.limit:]
        return row

def roll_supply_drop(random_source, chance, table):
    """One kill's supply roll: ``None`` or the chosen item type.

    ``table`` is ((item_type, weight), ...) with the editor's health/ammo
    percentages as weights; ``chance`` is the gross drop probability.
    """
    if chance <= 0 or not table or random_source.random() >= chance:
        return None
    total = sum(weight for _item, weight in table)
    if total <= 0:
        return None
    roll = random_source.random() * total
    for item_type, weight in table:
        roll -= weight
        if roll < 0:
            return item_type
    return table[-1][0]

class LiveSession(object):
    """Runs the Boss fight as a live state machine.

    The session owns one 60 Hz server tick.  Every tick produces a single
    ``MT_SERVER_DELTA`` carrying world time plus whatever object updates the
    state machine generated.
    """

    HZ = 60.0
    RULES = constants

    def __init__(self, server, channel, options, room, log=None,
                 bridge=None):
        self.rules = self.RULES
        self.server = server
        self.channel = channel
        self.options = options
        self.room = room
        self.log = log or (lambda message: None)
        self.layouts = server.layouts
        self.worldid = server._worldid()
        self.batch = DeltaBatch(self.layouts, self.worldid)
        configured_seed = getattr(options, 'live_seed', None)
        self.seed = (random.SystemRandom().getrandbits(64) if configured_seed is None
                     else int(configured_seed))
        self.projectiles = ProjectileSystem(room, seed=self.seed + 0xB011,
                                             rules=self.rules)
        self.loot_random = random.Random(self.seed + 0x10A7)
        self.pending_loot = []

        self._supply_settings = None
        self._supply_settings_mtime = None

        self.map_id = str(getattr(options, 'map', None)
                          or getattr(self, 'MAP_ID', game_settings.ORIGINAL_MAP))
        self.skip_intro = game_settings.skip_intro(self.map_id)
        self.projectiles.missile_delay_seconds = game_settings.missile_delay_ms(self.map_id)/1000.
        self.supplies = SupplyDrops(self, self.map_id, rng=self.loot_random)
        self.supplies.pending = self.pending_loot
        self.balance = game_settings.Balance(
            game_settings.balance(self.map_id))
        self._balance_mtime = game_settings.settings_mtime()
        self.enemies = EnemyManager(room, self.projectiles,
                                    seed=self.seed, rules=self.rules,
                                    behaviour=getattr(self.rules, 'BEHAVIOUR', None),
                                    balance=self.balance,

                                    spawn_baseline=2.0 if self.map_id in ('hulk_normal','egy_hulk') else 1.0)
        self.gens = GenCycle(self, auto_interact=bool(
            getattr(options, 'live_auto_interact', False)))

        self.turret_random = random.Random(self.seed + 0x7A11)
        self.turret_counts = self._load_turret_counts()
        self.turret_missiles = self._load_turret_missiles()
        self.turret_layout = roll_turret_layout(
            self.turret_random, counts=self.turret_counts,
            missile_counts=self.turret_missiles,
            missile_chance=getattr(self.rules, 'TURRET_MISSILE_CHANCE', .30),
            missile_floor=getattr(self.rules, 'TURRET_MISSILE_FLOOR', 2),
            count_ranges=getattr(self.rules, 'TURRET_COUNT_RANGES', None))

        template = room.object(54)
        self.turret_template = (copy.deepcopy(dict(template.props))
                                if template is not None else {})
        self.batteries = build_batteries(
            room, layout=self.turret_layout, rules=self.rules)
        self.events = EventLog()
        self.damage_feedback_counts=dict(received=0,zero=0,other_world=0,
            intro_applied=0,gen_applied=0,enemy_applied=0,terrain_applied=0,unhandled=0)
        self.damage_ignored_samples=collections.deque(maxlen=8)
        self.platforms = Platforms(self)
        self.bridge = bridge or PlayerBridge(
            getattr(options, 'bridge_port', self.rules.DEFAULT_BRIDGE_PORT),
            log=self.log)
        self.owns_bridge = bridge is None
        self.player_type = int(getattr(options, 'player_type', 1) or 1)
        self._restart_request = None
        self.suppress_send = False
        self.fallback_player = None
        if getattr(options, 'player_x', None) is not None:
            self.fallback_player = PlayerState(
                objid=int(getattr(options, 'player_objid', 1) or 1),
                xmp=options.player_x, ymp=options.player_y,
                wmp=options.player_wmp, hmp=options.player_hmp,
                hp=100, maxhp=100)
        self.player = self.fallback_player
        self.interact_was_down = False
        self.interact_latch = False
        self.last_player_log = 0.0
        self.phase = 'booting'
        self.phase_time = 0.0
        self.boss_time = 0.0
        self.room_time = 0.0

        self.destructible_terrain_hp={i:int(o.get('hitpoints_',0) or 0)
            for i,o in room.objects.items() if int(o.get('hitpoints_',0) or 0)>0
            and int(o.get('terraintype_',0) or 0) in (1,2)
            and i not in self.rules.GEN_IDS}
        self.ledge_drop_until = None
        self.ledge_redeploy_at = None
        self.ledge_terrain_at = None
        self.player_invuln_until = 0.0
        self.player_invuln = {}
        self.native_player_seen = False
        self.death_records = []
        self.death_index = 0
        self.death_started = None
        self.death_fx_ids = set()
        self.death_offset = (0, 0)
        self.death_ship_start = None
        self.death_motion = None
        self.death_motion_end = 0.0
        self.death_motion_due = 0.0
        self._stop = threading.Event()
        self._thread = None
        self._player_rect_reported = False
        self._player_missing_since = None

        self.telemetry_paused = False
        self._telemetry_mark = None

        self._spawn_blocked_since = None
        self._spawn_status_at = -30.0
        self.telemetry_missing_logged = False
        self.emitted_projectiles = {}
        self.preparing_projectiles = {}
        self.turret_render_facing = {}
        self.demo_damage_timer = 0.0
        self._wire_time_ms = 0.0
        self._sent_time_ms = 0

        self.intro_units = {}
        self.intro_unused = []
        self.intro_destroyed = []
        self._roll_intro_units()
        self._prepare_intro_ai()

        self.loader_emit_points = {}
        self.entrance_events = []
        self.entrance_index = 0
        self.entrance_loaded = False
        self.motion_tracks = {}
        self.ship_motion_due = 0.0
        self.ship_sent_track = None
        self.ship_recorded_end = 0.0
        self.ship_patrol = None
        self.ship_extension = None
        self.ship_recorded_final = None
        self.ship_controller = None
        self.projectile_motion_due = {}
        self.unit_render_state = {}
        self.entrance_parts = list(self.rules.BOSS_PART_IDS)
        self._load_loader_emit_points()
        self._load_boss_entrance()

    def prepare(self):
        """Reset the room and arm the sequence (no threads)."""
        for key in self.damage_feedback_counts:self.damage_feedback_counts[key]=0
        self.damage_ignored_samples.clear()
        self._prepare_room()
        self.ship_sent_track = None
        self.phase = 'waiting'
        self.phase_time = 0.0

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.log('live session started (phase=waiting)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        interval = 1.0 / self.HZ
        deadline = time.time()
        last = deadline
        while not self._stop.is_set():
            deadline += interval
            now = time.time()
            elapsed = now - last
            last = now

            scaled = min(max(elapsed, interval), interval * 2.0)
            try:
                self.step(scaled)
            except Exception as error:
                import traceback
                self.log('live tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            wait = deadline - time.time()
            if wait > 0:
                time.sleep(wait)
            elif time.time() - deadline > interval * 4.0:

                deadline = time.time()
                last = deadline

    def _prepare_room(self):
        batch = DeltaBatch(self.layouts, self.worldid)
        for objid, item in self.room.objects.items():
            vid = item.vid
            if vid in self.rules.COMBAT_SOUND_FALLBACKS and not item.get('soundprefix_'):

                batch.modify(objid, 'SOUNDPREFIX',
                    stringval=self.rules.COMBAT_SOUND_FALLBACKS[vid])
            if objid in self.rules.PANEL_IDS:
                batch.modify(objid, 'VID',
                             stringval='hulk.accesspaneloff')
                batch.modify(objid, 'INTERACT', intval=0)
            elif objid in self.rules.GEN_IDS:
                batch.modify(objid, 'VID',
                             stringval='hulk.weakspotclosed')
                batch.modify(objid, 'HITPOINTS')
                batch.modify(objid, 'TERRAINTYPE', intval=0)
                batch.modify(objid, 'INVULNERABLE', boolval=True)
                batch.modify(objid, 'BLOCKTYPE', intval=0)
                batch.modify(objid, 'OWNERTYPE', intval=0)
                item.set(hitpoints_=-1, terraintype_=0, invulnerable_=True,
                         blocktype_=0, ownertype_=0)
            elif objid in self.rules.LEDGE_IDS:
                batch.modify(objid, 'VID',
                             stringval='hulk.bossledgeshorttelegraph')
                batch.modify(objid, 'TERRAINTYPE', intval=0)
                item.set(terraintype_=0)

        for objid in self.intro_unused:
            batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
        for objid, unit in self.intro_units.items():
            item = self.room.object(objid)
            if item is not None and item.vid != unit['vid']:
                item.set(vid_=unit['vid'])
        self._queue(batch)

    def _queue(self, batch):
        """Fold a batch into this tick's outgoing delta."""
        if batch is self.batch:
            return
        self.batch.general.extend(batch.general)
        self.batch.objects.extend(batch.objects)

    def _load_loader_emit_points(self):
        "Load the client's animation emit points (attack schedules)."
        path = os.path.join(self.rules.LOADER_EMIT_POINTS_DIR,
                            'eloader-flame-points.json')
        if not os.path.exists(path):
            self.log('loader emit points missing: ' + path)
            return
        try:
            with io.open(path, 'r', encoding='utf-8') as handle:
                document = json.load(handle)
        except Exception as error:
            self.log('loader emit points load failed: ' + str(error))
            return
        points = []
        for item in document.get('points') or []:
            points.append((float(item.get('timeMs') or 0) / 1000.0,
                           int(item.get('x') or 0),
                           int(item.get('y') or 0),
                           int(item.get('angle') or 0)))
        if points:
            self.loader_emit_points[document.get('vid') or 'hulk.eloader'] = {
                'vid': document.get('emitVid') or 'hulk.flame',
                'scale': int(document.get('pointScale') or 128),
                'y_bias': int(document.get('pointYBias') or 0),
                'x_bias': int(document.get('pointXBias') or 0),
                'points': points,
            }
            self.log('loader emit points: {0} {1}'.format(
                document.get('vid'), len(points)))

    def _load_boss_entrance(self):
        'Load the runtime dropship choreography.'
        path = getattr(self.options, 'boss_entrance_timeline',
                       self.rules.BOSS_ENTRANCE_TIMELINE_PATH)
        if not path or not os.path.exists(path):
            self.log('boss entrance timeline missing; DRAW-only arrival')
            return
        try:
            with io.open(path, 'r', encoding='utf-8') as handle:
                document = json.load(handle)
        except Exception as error:
            self.log('boss entrance timeline load failed: ' + str(error))
            return
        events = []
        for item in document.get('timeline') or []:
            try:
                at = float(item.get('at') or 0.0)
                objid = int(item.get('objid') or 0)
            except (TypeError, ValueError):
                continue
            if not objid:
                continue
            kind = item.get('kind')
            if kind == 'move':
                blob = self.layouts.encode('Properties_Move', item.get('movement') or {})
                if blob:
                    events.append((at, 0 if at <= 0 else 1, 'move', objid,
                                   blob))
            elif kind == 'pos':
                events.append((at, 1, 'pos', objid,
                               (int(item.get('x') or 0),
                                int(item.get('y') or 0))))
        events.sort(key=lambda row: (row[0], row[1]))
        parts = [int(value) for value in document.get('parts') or []]
        if parts:
            self.entrance_parts = parts
        self.entrance_events = events
        self.entrance_loaded = bool(events)
        cycle_path = os.path.join(self.rules.LAB_ROOT, 'data', 'mechanics', 'combat-cycles.json')
        try:
            with io.open(cycle_path, 'r', encoding='utf8') as handle:
                cycles = json.load(handle)['ship']
            from .terrain import terrain_type
            roofs = [item.rect()[1]+item.rect()[3]
                     for item in self.room.objects.values()
                     if terrain_type(item) == 1 and item.rect()[1] < -200000
                     and item.rect()[2] > 100000 and item.rect()[3] <= 8192]
            self.ship_controller = ShipMotion(self.room.ship.position(), cycles,
                random.Random(self.seed + 0xB055),
                ceiling_y=min(roofs) if roofs else -319488)

            self.entrance_events = [e for e in events if e[3] != self.rules.SHIP_ID]
        except (OSError, ValueError, KeyError, IndexError) as error:
            self.log('reusable ship evidence unavailable; legacy path fallback: '+str(error))

        patrol_points = []
        for at, _, kind, objid, payload in events:
            if kind != 'move' or objid != self.rules.SHIP_ID:
                continue
            movement = self.layouts.decode('Properties_Move', payload)
            track = MotionTrack((0, 0), movement)
            self.ship_recorded_end = max(self.ship_recorded_end,
                                         at + track.times[-1])
            self.ship_recorded_final = track.position(track.times[-1])
            if 49.5 <= at < 89.1:
                patrol_points.extend((movement.get('waypoint_') or {}).get('points_') or [])
        if patrol_points:
            last = patrol_points[-1]
            self.ship_patrol = MotionTrack((last['xmp_'], last['ymp_']),
                {'waypoint_': {'points_': patrol_points}})
        self.log('boss entrance timeline: {0} events, {1} parts'.format(
            len(events), len(self.entrance_parts)))

    def _step_boss_entrance(self,dt=1/60):
        """Advance ship motion states and the original independent door moves."""
        if not self.entrance_loaded:
            return
        if self.ship_controller is not None:
            cues = self.ship_controller.step(self.boss_time)
            x, y = self.ship_controller.position(self.boss_time)
            self.room.move_object(self.rules.SHIP_ID, x, y)
            for cue in cues:
                if cue['kind'] == 'ship-two-rows':
                    self.platforms.retract_two_rows(self.ship_controller.rng,
                        'horizontal-{0}-{1}'.format(cue['serial'],cue['state']))
                elif cue['kind']=='ship-pass-complete':
                    self.enemies.ship_pass_completed()
                self.event(cue['kind'], state=cue['state'], serial=cue['serial'])
            if self.ship_controller.return_window(self.boss_time):
                jumped=self.enemies.ship_bottom_jump(
                    lambda seconds:self.ship_controller.position(self.boss_time+seconds),
                    dt=dt, pass_id=self.ship_controller.leg)
                if jumped:
                    self.event('ship-return-jump',units=jumped,state=self.ship_controller.state)
            track_key=(self.ship_controller.track,self.ship_controller.started)
            if self.ship_sent_track!=track_key:

                frame_start=max(0.,self.boss_time-dt)
                started=self.ship_controller.started
                offset=max(0,round((started-frame_start)*1000))
                self.batch.modify(self.rules.SHIP_ID,'MOVE',message=('Properties_Move',
                    self.ship_controller.track.native_movement(max(0.,frame_start-started))),offset=offset)
                self.ship_sent_track=track_key
        emitted = 0
        while (self.entrance_index < len(self.entrance_events)
               and self.entrance_events[self.entrance_index][0]
               <= self.boss_time):
            at, _order, kind, objid, payload =                 self.entrance_events[self.entrance_index]
            if kind == 'move':
                if objid != self.rules.SHIP_ID:
                    self.batch.modify(objid, 'MOVE', message=payload)
                item = self.room.object(objid)
                if item is not None:
                    movement = self.layouts.decode('Properties_Move', payload)
                    self.motion_tracks[objid] = (
                        at, MotionTrack(item.position(), movement))
            else:
                self.batch.move_to(objid, payload[0], payload[1])
                self.room.move_object(objid, payload[0], payload[1])
            self.entrance_index += 1
            emitted += 1
        for objid, (started, track) in self.motion_tracks.items():
            elapsed = self.boss_time - started
            ship_loop = (objid == self.rules.SHIP_ID and self.ship_patrol is not None
                         and self.boss_time >= self.ship_recorded_end)
            if ship_loop:
                if self.ship_extension is None:
                    self.ship_extension = LoopingTrack(track.position(track.times[-1]),
                                                      self.ship_patrol)
                    self.event('boss-patrol-loop')
                elapsed = self.boss_time - self.ship_recorded_end
                active_track = self.ship_extension
            else:
                active_track = track
            x, y = active_track.position(elapsed)
            self.room.move_object(objid, x, y)
            if objid == self.rules.SHIP_ID and self.boss_time >= self.ship_motion_due:
                end = active_track.position(elapsed + 0.05)
                self.batch.linear_motion(objid, (x, y), end, 50)
                self.ship_motion_due = self.boss_time + 0.05 - 1e-8
        if emitted:
            self.event('boss-timeline', events=emitted,
                       remaining=len(self.entrance_events)
                       - self.entrance_index)

    def _reveal(self):
        batch = self.batch

        for objid in sorted(self.intro_units):
            item = self.room.object(objid)
            planned = self.intro_units[objid]
            if item is None:

                props = self._unit_props(planned['actor'])
                props['armor_'] = self.rules.UNIT_STATS[planned['vid']].get('armor', 75)
                props['blocktype_'] = 1
                props['ownertype_'] = {'name': 'OWNERTYPE_ENEMY',
                                       'value': 2}
                props['pose_'] = self.rules.LOADER_POSE_STAND
                props['draw_'] = True
                batch.create(objid, props)
                continue
            move_from = planned.pop('move_from', None)
            if move_from is not None:

                batch.linear_motion(objid, move_from,
                                    (planned['x'], planned['y']), 1)
                self.room.move_object(objid, planned['x'], planned['y'])
            batch.modify(objid, 'VID', stringval=item.vid)
            batch.modify(objid, 'HITPOINTS', intval=planned['hp'])
            batch.modify(objid, 'MAXHITPOINTS', intval=planned['max'])
            batch.modify(objid, 'ARMOR', intval=self.rules.UNIT_STATS[planned['vid']].get('armor', 75))
            batch.modify(objid, 'BLOCKTYPE', intval=1)
            batch.modify(objid, 'OWNERTYPE', intval=2)
            batch.modify(objid, 'DRAW', boolval=True)
            batch.modify(objid, 'POSE', intval=self.rules.LOADER_POSE_STAND)
        self.phase = 'intro'
        self.phase_time = 0.0
        self.event('room-revealed')
        self.event('intro-started', units=sorted(self.intro_units))

    def _deploy_ledges(self):
        self.platforms.start()
        self.phase = 'fighting'
        self.phase_time = 0.0
        self.ledge_drop_until = None
        self.event('ledges-deployed')

    def _skip_opening(self):
        """Start at the real ship entrance endpoint, without simulating old attacks."""
        if self.ship_controller is None:
            self.log('Skip opening unavailable: ship entrance motion is missing')
            self._reveal()
            return
        arrived = self.ship_controller.track.times[-1]
        for objid in sorted(self.intro_units):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
        self.intro_destroyed = sorted(self.intro_units)
        self._begin_boss_arrival()
        # Advance choreography/state only. Never transmit the skipped movement
        # or generate loader loot, survivors, turret volleys or enemy waves.
        outgoing = self.batch
        self.batch = DeltaBatch(self.layouts, self.worldid)
        try:
            # Later door moves start where their previous move finished.
            checkpoints = {0., arrived, *(row[0] for row in self.entrance_events if row[0] <= arrived)}
            for at in sorted(checkpoints):
                for objid, (started, track) in self.motion_tracks.items():
                    self.room.move_object(objid, *track.position(at - started))
                self.boss_time = at
                self._step_boss_entrance(dt=0)
            self._deploy_ledges()
        finally:
            self.batch = outgoing
        self.phase_time = max(0., arrived - self.rules.LEDGE_DEPLOY_DELAY)
        ship = self.ship_controller
        self.batch.modify(self.rules.SHIP_ID, 'MOVE', message=('Properties_Move',
            ship.track.native_movement(arrived - ship.started)))
        for objid, (started, track) in self.motion_tracks.items():
            self.batch.modify(objid, 'MOVE', message=('Properties_Move',
                track.native_movement(arrived - started)))
        self.platforms.terrain_due.clear()
        for objid in self.rules.LEDGE_IDS:
            self.room.object(objid).set(terraintype_=self.rules.LEDGE_TERRAIN_TYPE)
            self.batch.modify(objid, 'VID', stringval='hulk.bossledgeshort')
            self.batch.modify(objid, 'TERRAINTYPE', intval=self.rules.LEDGE_TERRAIN_TYPE)
        self.gens.start()
        self.gens.next_panel_delay = max(0., self.rules.PANEL_FIRST_DELAY - arrived)
        self.enemies.start()
        self.enemies.wave_timer = max(0., self.enemies.first_wave_delay() - arrived)
        self.event('opening-skipped', boss_time=arrived, ship=list(self.room.ship.position()))

    def _check_restart_request(self):
        """Return True when the bridge queued a restart/class hold.

        The event is left in the bridge queue; the session is about to stop
        and be replaced by a fresh one after the client reloads the zone.
        """
        if self._restart_request is not None:
            return True
        try:
            events = self.bridge.peek_events()
        except Exception:
            events = []
        for item in events:
            if not isinstance(item, dict):
                continue
            kind = item.get('kind')
            if kind not in ('restart', 'switch_character'):
                continue
            try:
                char_type = int(self.player_type if kind == 'restart'
                                else item.get('type') or self.player_type)
            except (TypeError, ValueError):
                char_type = self.player_type
            self._restart_request = char_type
            self.event('restart-request', type=char_type,
                       key=item.get('key'))
            return True
        return False

    def step(self, dt):

        pending = getattr(self.server, 'class_loadout_apply_pending', None)
        if pending is not None and pending():
            handler = getattr(self.server, 'request_live_restart', None)
            if handler is not None:
                self.event('gear-apply-reload')
                handler(self.channel, self, self.player_type)
                return
        if self._check_restart_request():
            handler = getattr(self.server, 'request_live_restart', None)
            if handler is not None:
                handler(self.channel, self, self._restart_request)
            else:
                self._restart_request = None
            return
        self._refresh_balance()
        self._sync_player()
        self.room_time += dt
        self.phase_time += dt
        if self.phase in ('deploying', 'fighting'):
            self.boss_time += dt
        events = []

        if self.phase == 'waiting':
            if self.player is None and self.room_time > self._demo_wait():
                if (getattr(self.options, 'live_auto_interact', False)
                        or getattr(self.options, 'live_demo_damage', False)):
                    self._create_demo_player()
            if self.player is not None:

                if self.skip_intro:
                    self._skip_opening()
                else:
                    self._reveal()
            self._send_tick(dt)
            return

        if self.phase == 'intro':
            self._step_intro(dt, events)
            self._send_tick(dt)
            return

        if self.phase == 'deploying':

            self._apply_bridge_events(events)
            self._step_boss_entrance(dt)
            self.enemies.step(dt, self.player, events, players=self.combat_players())
            self._emit_units(dt)
            self._step_turrets(dt)
            self.projectiles.step(dt, self.player, events,
                                  resolve_player=not self._native_damage_active(),
                                  players=self.combat_players())
            self._emit_projectiles(dt)
            self._resolve_events(events,offset=self._frame_end_offset(dt))
            if self.phase_time >= self.rules.LEDGE_DEPLOY_DELAY:
                self._deploy_ledges()
                self.gens.start()
                self.gens.next_panel_delay = max(
                    0.0, self.rules.PANEL_FIRST_DELAY - self.boss_time)
                self.enemies.start()
                self.enemies.wave_timer = max(
                    0.0, self.enemies.first_wave_delay() - self.boss_time)
            self._send_tick(dt)
            return

        if self.phase == 'fighting':
            self._step_fighting(dt, events)
            self._send_tick(dt)
            return

        if self.phase == 'death':
            self._step_death(dt, events)
            self._send_tick(dt)
            return

        if self.phase in ('done', 'failed'):
            self._send_tick(dt)

    def _step_fighting(self, dt, events):
        batch = self.batch
        self._step_boss_entrance(dt)
        self._apply_bridge_events(events)
        self._demo_drive(dt)
        self.gens.step(dt)
        if self.phase != 'fighting':
            return

        player = self.player
        self._apply_ledge_terrain()

        self._step_turrets(dt)

        self.enemies.step(dt, player, events, players=self.combat_players())
        self._log_spawn_pressure()
        self._log_spawn_status()
        self._emit_new_units()
        self.projectiles.step(dt, player, events,
                              resolve_player=not self._native_damage_active(),
                              players=self.combat_players())
        self._emit_projectiles(dt)
        self._emit_units(dt)
        self._resolve_events(events,offset=self._frame_end_offset(dt))

    def _log_spawn_pressure(self):
        """Report why the wave spawner is not producing more bodies.

        The cap counts live ground monsters, so a room that looks empty can
        still be full: bodies the player cannot reach (stuck on ledges, below
        the arena, inside the spawn cover) keep the slots.  Logging where the
        live bodies are makes that visible in ``events``/logs.
        """
        manager = self.enemies
        if not manager.enabled:
            self._spawn_blocked_since = None
            return
        limit = manager.max_alive()
        if manager.population_count() + manager.reserved() < limit:
            self._spawn_blocked_since = None
            return
        if self._spawn_blocked_since is None:
            self._spawn_blocked_since = self.room_time
            return
        if self.room_time - self._spawn_blocked_since < 20.0:
            return
        self._spawn_blocked_since = self.room_time
        alive = manager.live()
        floor = sum(1 for unit in alive
                    if unit.motion.grounded
                    and unit.motion.support == self.rules.ARENA_FLOOR_ID)
        ship = sum(1 for unit in alive
                   if unit.motion.grounded
                   and unit.motion.support not in (None,
                                                   self.rules.ARENA_FLOOR_ID))
        air = sum(1 for unit in alive if not unit.motion.grounded)
        self.event('spawn-blocked', limit=limit, alive=len(alive),
                   floor=floor, ship=ship, air=air)

    def _log_spawn_status(self):
        """Periodic spawner heartbeat for play-session logs.

        Keeps a readable trail of how many waves ran and how many bodies are
        live, so "the room stopped spawning" can be read from the log instead
        of guessed from a recording.
        """
        if self.room_time - self._spawn_status_at < 30.0:
            return
        self._spawn_status_at = self.room_time
        manager = self.enemies
        self.event('spawn-status', enabled=bool(manager.enabled),
                   waves=manager.waves, alive=manager.population_count(),
                   reserved=manager.reserved(), limit=manager.max_alive())

    def _step_turrets(self, dt):

        phase = self.boss_time
        fired = 0
        for battery in self.batteries:
            fired += battery.step(
                dt, phase, self.player,
                lambda turret_id, ammo, aim, stage: self._fire_battery(
                    turret_id, ammo, aim, stage), players=self.combat_players())
            for turret_id, facing in battery.facing.items():
                angle = int(round(facing)) % 360
                if self.turret_render_facing.get(turret_id) != angle:
                    self.batch.modify(turret_id, 'FACING', intval=angle)
                    self.turret_render_facing[turret_id] = angle
                    item = self.room.object(turret_id)
                    if item is not None:
                        item.set(facing_=angle)
                for projectile in self.preparing_projectiles.get(turret_id, []):
                    cx, cy = self._muzzle(turret_id, facing)
                    projectile.x, projectile.y = cx-projectile.w/2, cy-projectile.h/2
        if fired:
            self.event('volley', shots=fired)

    def _demo_wait(self):
        """Seconds to wait for live telemetry before faking a player.

        A watch-only run attached to the retail client must not spawn the
        synthetic player first: the client reports its player a few seconds
        after connecting, and hijacking that would lose player damage.
        """
        if self.bridge.datagrams:
            return float(getattr(self.options, 'live_bridge_wait', 25.0))
        return 3.0

    def _create_demo_player(self):
        """Synthetic player for headless/demo runs without the bridge."""
        floor = self.room.object(1)
        if floor is None:
            return
        x, y, w, h = floor.rect()
        self.fallback_player = PlayerState(
            objid=1, xmp=x + w // 2 - 5120, ymp=y - 17920,
            wmp=10240, hmp=17920, hp=100, maxhp=100)
        self.player = self.fallback_player
        self.log('demo player created on arena floor')

    def _demo_drive(self, dt):
        'Headless/demo helpers (no bridge plugin attached).'
        forced = bool(getattr(self.options, 'live_force_demo', False))
        if self.bridge.connected() and not forced:
            return
        if (self.fallback_player is not None
                and self.player is self.fallback_player
                and getattr(self.options, 'live_auto_interact', False)
                and self.gens.active_panel is not None):
            panel = self.room.object(self.gens.active_panel)
            if panel is not None:
                x, y, w, h = panel.rect()
                self.fallback_player.xmp = x + w // 2 - 5120
                self.fallback_player.ymp = y + h // 2 - 8960
                self.player = self.fallback_player
        if not getattr(self.options, 'live_demo_damage', False):
            return
        self.demo_damage_timer += dt
        if self.demo_damage_timer < 0.3:
            return
        self.demo_damage_timer = 0.0
        if self.phase == 'intro':
            for objid in sorted(self.intro_units):
                if objid not in self.intro_destroyed:
                    self._damage_intro_unit(objid, 700)
                    break
            return
        if self.gens.gen_state != 'armed' or self.gens.active_gen is None:
            return
        self.gens.apply_damage(self.gens.active_gen, 700)

    def _roll_intro_units(self):
        'Roll 1-4 opening big robots and their random types.'

        rng = random.Random(self.seed + 0x1D10)
        forced = int(getattr(self.options, 'live_intro_count', 0) or 0)
        if forced > 0:
            count = forced
        else:
            count = rng.randint(self.rules.INTRO_LOADER_MIN,
                                self.rules.INTRO_LOADER_MAX)
        count = max(self.rules.INTRO_LOADER_MIN,
                    min(self.rules.INTRO_LOADER_MAX, count))
        ids = list(self.rules.INTRO_LOADER_IDS)
        rects = {}
        for objid in ids:
            item = self.room.object(objid)
            rects[objid] = (item.rect() if item is not None
                            else (0, 0, 20480, 40960))

        def center(rect):
            return rect[0] + rect[2] / 2.0, rect[1] + rect[3] / 2.0

        first_cx, first_cy = center(rects[ids[0]])
        last_cx, last_cy = center(rects[ids[-1]])

        def anchor(t):
            return (first_cx + (last_cx - first_cx) * t,
                    first_cy + (last_cy - first_cy) * t)

        if count == 1:
            plan = [(ids[0], 0.0)]
        elif count == 2:
            plan = [(ids[0], 0.0), (ids[-1], 1.0)]
        elif count == 3:
            plan = [(ids[0], 0.0), (ids[1], 0.5), (ids[-1], 1.0)]
        else:

            record_cx, _ = center(rects[ids[1]])
            leaves = [1.0 / 3.0, 2.0 / 3.0]
            nearest = min(leaves,
                          key=lambda t: abs(anchor(t)[0] - record_cx))
            other = [t for t in leaves if t != nearest][0]
            plan = [(ids[0], 0.0), (ids[1], nearest), (None, other),
                    (ids[-1], 1.0)]

        pinned = getattr(self.options, 'live_intro_vids', None) or ()
        if isinstance(pinned, str):
            pinned = tuple(part.strip() for part in pinned.split(',')
                           if part.strip())

        def pick_type(index):
            if pinned:
                return pinned[index % len(pinned)]
            return rng.choice(self.rules.INTRO_LOADER_VIDS)

        for index, (objid, t) in enumerate(plan):
            if objid is None:
                objid = self.enemies.allocate()
            source = rects.get(objid, rects[ids[1]])
            _x, _y, w, h = source
            cx, cy = anchor(t)
            px = int(round(cx - w / 2.0))
            py = int(round(cy - h / 2.0))
            intro_hp = self.balance.hp(
                self.rules.UNIT_STATS['hulk.scavengerloader']['hp'])
            unit = {
                'hp': intro_hp,
                'max': intro_hp,
                'vid': pick_type(index),
                'x': px, 'y': py, 'w': int(w), 'h': int(h),
            }
            item = self.room.object(objid)
            if item is not None:
                ix, iy, _iw, _ih = item.rect()
                if (int(ix), int(iy)) != (px, py):

                    unit['move_from'] = (int(ix), int(iy))
            self.intro_units[objid] = unit
        self.intro_unused = [objid for objid in ids
                             if objid not in self.intro_units]

    def _prepare_intro_ai(self):
        """Per-loader behaviour state (walk/run/attack poses)."""
        for objid, unit in self.intro_units.items():
            room_unit = self.room.object(objid)
            x, y = (room_unit.position() if room_unit is not None else (0, 0))
            unit.setdefault('x', int(x))
            unit.setdefault('y', int(y))

            unit.setdefault('home_x', int(unit['x']))
            unit.setdefault('vid', room_unit.vid if room_unit is not None
                            else 'hulk.scavengerloader')
            unit.setdefault('pose', self.rules.LOADER_POSE_STAND)
            unit.setdefault('facing', None)
            unit.setdefault('attack_until', 0.0)

            unit.setdefault('aggro', False)
            unit.setdefault('next_attack', 0.0)
            unit.setdefault('attack_started', 0.0)
            unit.setdefault('emits_done', 0)
            rect = room_unit.rect() if room_unit is not None else (0, 0, 20480, 40960)
            unit.setdefault('w', rect[2])
            unit.setdefault('h', rect[3])
            if 'actor' not in unit:
                stats = dict(self.rules.UNIT_STATS[unit['vid']])
                stats.update(self.enemies.behaviour[unit['vid']])
                stats['rule'] = dict(stats['rule'], threshold=self._loader_rule(unit)['attack_range']/256,
                                     idle_patrol=False)
                unit['actor'] = Enemy(objid, unit['vid'], stats,
                                      unit['x'], unit['y'], rect[2], rect[3],
                                      rng=self.enemies.random, room=self.room,
                                      rules=self.rules)

    def _step_intro(self, dt, events):
        """Three loader robots first; the ship arrives when they are dead.

        Opening and portal robots use the same Enemy state machine, including
        wind-up/settle/charge. The native client resolves their hit regions.
        """
        self._apply_bridge_events(events)
        self._demo_drive(dt)
        self._step_intro_loaders(dt, events)
        self.enemies.step(dt, self.player, events, players=self.combat_players())
        self._emit_new_units()
        self._emit_units(dt)
        self.projectiles.step(dt, self.player, events,
                              resolve_player=not self._native_damage_active(),
                              players=self.combat_players())
        self._emit_projectiles(dt)
        self._resolve_events(events,offset=self._frame_end_offset(dt))
        for item in events:
            if item.get('kind') == 'wave':
                continue

    def _loader_rule(self, unit):
        return self.rules.LOADER_BEHAVIOUR.get(
            unit.get('vid'), self.rules.LOADER_BEHAVIOUR[
                'hulk.scavengerloader'])

    def _step_intro_loaders(self, dt, events=None):
        """Share robot action/physics states; opening aggro gates remain fitted."""
        from ..participants import nearest
        players = self.combat_players()
        dt_ms = int(round(dt * 1000))
        produced = []
        for objid in sorted(self.intro_units):
            unit = self.intro_units[objid]
            if objid in self.intro_destroyed:
                continue
            rule = self._loader_rule(unit)
            actor = unit['actor']
            player = nearest(players, actor.center())
            actor.hp = unit['hp']
            actor.shot_timer = max(0, unit['next_attack']-self.room_time)
            if player is None or not player.alive:
                unit['aggro'] = False
            elif not unit['aggro']:
                distance = abs(player.center()[0]-actor.center()[0])
                if distance <= rule['aggro_range']:
                    unit['aggro'] = True
                    actor.shot_timer = 0
                    self.event('loader-aggro', objid=int(objid),
                               distance=int(distance))
            target = player if unit['aggro'] else None
            produced.extend(actor.step(dt, target, self.room_time))
            if (int(actor.x),int(actor.y)) != (unit['x'],unit['y']):
                self.batch.linear_motion(objid, (unit['x'],unit['y']), (actor.x,actor.y), dt_ms)
                self.room.move_object(objid, actor.x, actor.y)
            unit['x'],unit['y'] = int(actor.x),int(actor.y)
            if actor.facing != unit['facing']:
                unit['facing'] = actor.facing
                self.batch.modify(objid, 'FACING',
                                  intval=180 if actor.facing < 0 else 0)
            self._set_loader_pose(objid, unit, actor.pose)
            unit.update(attack_started=actor.attack_started, attack_until=actor.attack_until,
                        next_attack=self.room_time+actor.shot_timer, state=actor.state)
            if player is not None and player.alive and not actor.melee_hit_done:
                from .projectiles import rects_overlap
                if any(rects_overlap(rect,player.rect()) for rect in actor.attack_rects(self.room_time)):
                    actor.melee_hit_done = True
                    produced.append(dict(kind='player_damage',owner=objid,value=actor.contact,x=0,y=0))
        for item in produced:
            if item['kind'] == 'enemy-attack':
                self.event('loader-attack', objid=item['objid'], vid=item['vid'],
                           anim=item['anim'], time=item['time_ms'])
        if events is None:
            self._resolve_events(produced)
        else:
            events.extend(produced)

    def _set_loader_pose(self, objid, unit, pose):
        if unit['pose'] == pose:
            return
        unit['pose'] = pose
        self.batch.modify(objid, 'POSE', intval=int(pose))
        self.event('loader-pose', objid=int(objid), pose=int(pose))

    def _spawn_flame(self, objid, unit, table, index, rule=None):
        props = self.room.template_props(table['vid'])
        if not props:
            return
        _time, px, py, _angle = table['points'][index]
        scale = int(table['scale'])
        facing = unit['facing'] or -1

        x = unit['x'] + unit['w'] / 2 + facing * px * scale - props.get('wmp_', 0) / 2
        y = unit['y'] + unit['h'] - 128 - py * scale - props.get('hmp_', 0) / 2
        props = dict(props)
        props.pop('move', None)
        props.pop('move_', None)
        props['vid_'] = table['vid']
        props['xmp_'] = int(x)
        props['ymp_'] = int(y)
        props['facing_'] = 180 if facing < 0 else 0
        props['ownertype_'] = {'name': 'OWNERTYPE_ENEMY', 'value': 2}
        projectile = self.projectiles.fire_enemy_shot(objid, 'flame',
            (x+props['wmp_']/2,y+props['hmp_']/2),
            180 if facing<0 else 0, 40, self.rules.ENEMY_SHOTS['flame']['speed'])
        from .enemies import loader_flame_move
        projectile.set_velocity_command(loader_flame_move((rule or {}).get('attack_anim',4), facing))
        flame_id = projectile.objid
        self._emit_projectile(projectile)

        self.event('loader-emit', objid=int(objid), emit=flame_id,
                   vid=table['vid'], x=int(x), y=int(y))

    def _damage_intro_unit(self, objid, value):
        unit = self.intro_units.get(int(objid))
        if unit is None:
            return None
        if int(objid) in self.intro_destroyed:
            return None
        unit['hp'] = max(0, unit['hp'] - max(0, int(value)))
        self.event('intro-damage', objid=int(objid), hp=unit['hp'])
        if unit['hp'] <= 0:
            self.intro_destroyed.append(int(objid))
            self.batch.destroy(int(objid), ownerid=1, reason='HITPOINTS')
            self._spawn_loader_survivor(unit['x'], unit['y'], unit['w'],
                                        unit['facing'])
            if self.room.object(int(objid)) is not None:
                x, y, w, h = self.room.object(int(objid)).rect()
            else:

                x, y, w, h = unit['x'], unit['y'], unit['w'], unit['h']
            self._maybe_drop_supply(x + w // 2, y + h // 2)
            self.event('intro-destroyed', objid=int(objid))
            if len(self.intro_destroyed) >= len(self.intro_units):
                self._begin_boss_arrival()
            return {'destroyed': True, 'hp': 0}
        return {'destroyed': False, 'hp': unit['hp']}

    def _spawn_loader_survivor(self, x, y, width, facing, parent=None):
        """Loader destruction releases one melee body of this difficulty."""
        facing = facing or -1
        vid = self.enemies.random.choice((
            'hulk.scavengershredder', 'hulk.scavengershredderenergy'))
        child = self.enemies.spawn(vid, portal_id=getattr(parent, 'portal_id', None), position=(
            int(x + width // 2 - 6400), int(y)))
        if child is None:
            return
        if parent is not None:
            self.enemies.inherit_portal(child, parent)
        child.hp = self.rules.UNIT_STATS[vid]['hp']
        child.facing = facing
        child.launch_vx = facing * (child._travel_speed(facing, True)
                                   if self.rules is not constants else 94000)
        child.launch_vy = -59000
        child.launch_until = 0.8
        child.pose = 3
        self._emit_new_units()
        self.event('loader-survivor', objid=child.objid, vid=vid, hp=child.hp)

    def _begin_boss_arrival(self):

        self._apply_turret_layout()
        parts = (set(self.entrance_parts or self.rules.BOSS_PART_IDS)
                 - set(self.rules.TURRET_IDS))
        for objid in sorted(parts):
            self.batch.modify(objid, 'DRAW', boolval=True)
        self.entrance_index = 0
        self.phase = 'deploying'
        self.phase_time = 0.0
        self.boss_time = 0.0
        self.event('boss-arriving', parts=len(self.entrance_parts),
                   timeline=len(self.entrance_events))

    def _load_turret_counts(self):
        "Read the editor's optional per-row count override."
        document = getattr(self.server, 'class_loadouts', None) or {}
        value = document.get('_turret_counts') if isinstance(document, dict) else None
        if not value:
            return None
        try:
            return normalize_turret_counts(value)
        except (TypeError, ValueError) as error:
            self.log('ignored invalid turret counts: {0}'.format(error))
            return None

    def _load_turret_missiles(self):
        """Read optional exact upper/lower tracking-turret counts."""
        document = getattr(self.server, 'class_loadouts', None) or {}
        value = (document.get('_turret_missiles')
                 if isinstance(document, dict) else None)
        if not value:
            return None
        try:
            return normalize_missile_counts(value)
        except (TypeError, ValueError) as error:
            self.log('ignored invalid turret missile counts: {0}'.format(error))
            return None

    def _apply_turret_layout(self):
        "Create exactly this battle's rolled turrets on the dropship."
        ship = self.room.object(self.rules.SHIP_ID)
        ship_x, ship_y = ship.position() if ship is not None else (0, 0)
        for objid, slot in sorted(self.turret_layout.items()):
            offset_x, offset_y = slot.offset
            props = copy.deepcopy(self.turret_template)
            props['xmp_'] = int(ship_x + offset_x)
            props['ymp_'] = int(ship_y + offset_y)
            movement = dict(props.get('move_') or {})
            movement['relativeto_'] = {'objid_': self.rules.SHIP_ID}
            movement['to_'] = {'xmp_': int(offset_x),
                               'ymp_': int(offset_y)}
            props['move_'] = movement
            props['draw_'] = True
            facing = 180 if slot.fixed_forward else 0
            props['facing_'] = int(facing)
            self.batch.create(objid, props)
            self.room.add_turret(objid, props)
            self.turret_render_facing[objid] = int(facing)

    def native_retirement_capability(self, objid):
        """Classify only an active, emitted projectile for shared validation."""
        if type(objid) is not int or objid <= 0:
            return None
        projectile = self.emitted_projectiles.get(objid)
        if projectile is None or not projectile.alive or projectile.prepared:
            return None
        lifetime = projectile.lifetime
        return dict(ownerid=self.worldid, objid=objid, role='projectile',
                    expires_at=(self.room_time + max(0., lifetime-projectile.age)
                                if lifetime > 0 else None),
                    room_time=self.room_time,
                    lifetime_due=lifetime > 0 and projectile.age >= lifetime)

    def _apply_bridge_events(self, events):
        for item in self.bridge.poll_events():
            kind = item.get('kind')
            if kind == 'destroy' and int(item.get('ownerid') or 0) == self.worldid:
                victim = int(item.get('victim') or 0)
                if victim in self.destructible_terrain_hp:
                    self._retire_destructible_terrain(victim)
                    continue
                projectile = self.emitted_projectiles.get(victim)
                if projectile is not None and projectile.alive:
                    projectile.alive = False
                    projectile.death_reason = int(item.get('reason') or 7)
                    if projectile.death_reason in (3, 7):
                        projectile.burst_vid = None
                    self.event('client-projectile-destroyed', objid=victim,
                               reason=projectile.death_reason)
                continue
            if kind == 'damage':
                self.damage_feedback_counts['received']+=1
                victim = int(item.get('victim') or item.get('objid') or 0)
                value = int(item.get('value') or item.get('damage') or 0)
                if value <= 0:
                    self.damage_feedback_counts['zero']+=1
                    continue
                ownerid = item.get('ownerid')
                if ownerid is not None and int(ownerid) != self.worldid:
                    self.damage_feedback_counts['other_world']+=1
                    if (self.player is not None and victim == self.player.objid
                            and int(ownerid) == self.player.worldid):
                        self.event('native-player-hit', value=value)
                    else:
                        self.event('damage-other-world', victim=victim,
                                   ownerid=int(ownerid), value=value)
                    continue
                result = self._damage_intro_unit(victim, value)
                applied='intro_applied'
                if result is None:
                    result = self.gens.apply_damage(victim, value)
                    applied='gen_applied'
                if result is None:
                    result = self.enemies.apply_damage(victim, value)
                    applied='enemy_applied'
                    if result is not None and result.get('destroyed'):
                        unit = result.get('unit')
                        self.batch.destroy(unit.objid, ownerid=1,
                                           reason='HITPOINTS')
                        unit.emitted = False
                        self.unit_render_state.pop(unit.objid, None)
                        x, y = unit.center()

                        if not getattr(unit, 'flying', False):
                            self._maybe_drop_supply(x, y)
                        if unit.vid in ('hulk.scavengerloader', 'hulk.eloader'):
                            self._spawn_loader_survivor(unit.x, unit.y,
                                                        unit.w, unit.facing, parent=unit)
                if result is None:
                    result=self._damage_destructible_terrain(victim,value)
                    applied='terrain_applied'
                if result is None:
                    self.damage_feedback_counts['unhandled']+=1
                    unit=next((u for u in self.enemies.units if u.objid==victim),None)
                    reason=('inactive-generator' if victim in self.gens.hp else
                        'unarmed-bomb-rover' if unit is not None and unit.alive and unit.kind=='bomb-rover' and not unit.bomb_armed else
                        'retired-intro' if victim in self.intro_destroyed else 'unhandled-target')
                    source=item.get('source');source=source if isinstance(source,dict) else {}
                    props=source.get('props');props=props if isinstance(props,dict) else {};vid=props.get('vid_')
                    source_id=source.get('objid');life=item.get('life_id')
                    self.damage_ignored_samples.append(dict(reason=reason,victim=victim,value=value,
                        sourceVid=vid[:128] if isinstance(vid,str) else None,
                        sourceObjid=source_id if type(source_id)is int and 0<source_id<2**31 else None,
                        lifeId=life if type(life)is int and 0<life<2**31 else None,
                        genState=self.gens.gen_state,activeGen=self.gens.active_gen))
                else:self.damage_feedback_counts[applied]+=1
                if result is not None:

                    if isinstance(result, dict) and 'hp' in result and\
                            result.get('hp') is not None and not\
                            result.get('destroyed'):
                        self.batch.modify(victim, 'HITPOINTS',
                                          intval=int(result['hp']))
                events.append({'kind': 'client-damage', 'victim': victim,
                               'value': value})

    def _spawn_barrel_explosion(self,item):
        'Barrel death creates the runtime ``hulk.barrelx`` blast.'
        if item is None:
            return None
        if item.vid not in ('hulk.standingbarrel', 'hulk.leaningbarrel'):
            return None
        props = dict(self.room.template_props('hulk.barrelx') or {})
        width = int(props.get('wmp_') or 24576)
        height = int(props.get('hmp_') or 24576)
        x, y, w, h = item.rect()
        blast = self.projectiles.spawn(
            vid='hulk.barrelx', owner=0,
            x=x + w/2.0 - width/2.0, y=y + h/2.0 - height/2.0,
            vx=0, vy=0, width=width, height=height,
            damage=int(props.get('damage_') or 1000),
            lifetime=float(props.get('lifetime_') or 500)/1000.0,
            kind='hazard', rotate=False, owner_type=1)
        self._emit_projectile(blast)
        self.batch.sound(blast.objid, 'hulk.barrelx')
        self.event('terrain-explosion', objid=blast.objid,
                   source=item.objid, vid='hulk.barrelx')
        return blast

    def _retire_destructible_terrain(self,objid):
        if objid not in self.destructible_terrain_hp:
            return
        item=self.room.object(objid)
        self._spawn_barrel_explosion(item)
        del self.destructible_terrain_hp[objid]
        item.set(terraintype_=0,hitpoints_=0,draw_=False)
        self.batch.modify(objid,'TERRAINTYPE',intval=0)
        self.event('destructible-terrain-removed',objid=objid)

    def _damage_destructible_terrain(self,objid,value):
        if objid not in self.destructible_terrain_hp:
            return None
        hp=max(0,self.destructible_terrain_hp[objid]-value)
        if hp<=0:
            self._retire_destructible_terrain(objid)
            self.batch.destroy(objid,ownerid=self.worldid,reason='HITPOINTS')
            return dict(destroyed=True,hp=0)
        self.destructible_terrain_hp[objid]=hp
        self.room.object(objid).set(hitpoints_=hp)
        return dict(destroyed=False,hp=hp)

    def _resolve_events(self, events, offset=0):
        for item in events:
            kind = item.get('kind')
            if kind == 'projectile-pulse':
                self._emit_burst(item['projectile'], 'hulk.grenadepulse',offset=offset)
                self.batch.sound(item['projectile'].objid, 'hulk.grenadebounce',offset=offset)
                continue
            if kind == 'napalm-landed':
                projectile = item['projectile']
                self.batch.linear_motion(projectile.objid, (projectile.x, projectile.y),
                                         (projectile.x, projectile.y), 1,offset=offset)
                self.batch.modify(projectile.objid, 'POSE', intval=4,offset=offset)
                self.projectile_motion_due.pop(projectile.objid, None)
                continue
            if kind == 'player_damage':

                if self._native_damage_active():
                    continue
                now = self.room_time
                from ..participants import life_identity
                key = item.get('player_key')
                player = (next((p for p in self.combat_players() if life_identity(p) == key), None)
                          if key is not None else self.player)
                if player is None:
                    continue
                key = life_identity(player)
                if now < self.player_invuln.get(key, 0.):
                    continue
                self.player_invuln[key] = now+self.rules.PLAYER_INVULN
                self.player_invuln_until = now+self.rules.PLAYER_INVULN
                if (not self._player_rect_reported or player.objid <= 0
                        or player.worldid <= 0):

                    self.event('player-damage-demo', value=item.get('value'),
                               owner=item.get('owner'))
                    continue
                self.batch.damage(
                    ownerid=player.worldid,
                    objid=player.objid, value=int(item.get('value') or 0),
                    x=int(item.get('x') or 0), y=int(item.get('y') or 0),
                    fx=self.rules.DAMAGE_FX_DEFAULT)
                self.event('player-damage', value=item.get('value'),
                           owner=item.get('owner'))
            elif kind == 'enemy-attack':

                self.batch.animprops(item.get('objid'), item.get('anim', 0),
                                     item.get('time_ms', 1000))
                name = {
                    ('hulk.eloader', 4): 'hulk.loaderdrill',
                    ('hulk.scavengerloader', 5): 'hulk.loaderdrill',
                    ('hulk.scavengershredder', 2): 'hulk.punch',
                    ('hulk.scavengershredder', 4): 'hulk.tail',
                    ('hulk.scavengershredderenergy', 2): 'hulk.epunch',
                    ('hulk.scavengershredderenergy', 4): 'hulk.etail',
                    ('hulk.scavengerengineer', 3): 'hulk.tail',
                    ('hulk.scavengercommando', 0): 'hulk.throw',
                }.get((item.get('vid'), item.get('anim')))
                if name:
                    self.batch.sound(item.get('objid'), name)
                self.event('enemy-attack', objid=item.get('objid'),
                           vid=item.get('vid'), anim=item.get('anim'))
            elif kind == 'enemy-shot':
                self._fire_enemy_shot(item,offset=offset)
            elif kind == 'enemy-bomb-explode':
                self._emit_burst(item['unit'], 'hulk.bombroverx',offset=offset)
                self.event('bomb-rover-exploded', objid=item['unit'].objid)
            elif kind == 'enemy-summon':
                self.event('enemy-summon', owner=item['owner'], objid=item['objid'], vid=item['vid'])
            elif kind in ('enemy-charge-start', 'enemy-charge-end'):
                self.event(kind, objid=item['objid'], vid=item['vid'], reason=item.get('reason'))
            elif kind == 'wave':
                self.event('wave', count=item.get('count'),
                           queued=item.get('queued', item.get('count')),
                           nextDelay=round(item.get('next_delay', 0.0), 3))
            elif kind == 'portal-preview':
                self._emit_spawn_preview(item)
            elif kind == 'portal-preview-expired':

                self.batch.destroy(item['objid'], ownerid=self.worldid, reason='LIFETIME')
            elif kind == 'portal-preview-move':

                self.batch.linear_motion(item['objid'], item['from'], item['to'], item['ms'])

    def _emit_spawn_preview(self, item):
        from .enemies import BEHAVIOUR
        portal = self.room.object(item['portal'])
        if portal is None:
            return
        x, y, width, _ = portal.rect()
        vid = item['vid'] or 'hulk.pathlighton'
        w, h = (self.enemies.behaviour.get(vid, {}).get('size', (12800, 23040))
                if item['vid'] else (8192, 8192))
        dx, dy = width // 2 - w // 2, self.rules.SPAWN_PREVIEW_FEET - h
        preview = {'vid_': vid, 'xmp_': x+dx, 'ymp_': y+dy,
                   'wmp_': w, 'hmp_': h, 'z_': 49, 'draw_': True,
                   'facing_': 180, 'blocktype_': 1, 'lifetime_': 500,
                   'move_': {'relativeto_': {'objid_': item['portal']},
                             'to_': {'xmp_': dx, 'ymp_': dy}}}
        self.batch.create(item['preview_id'], preview)
        cover = {'vid_': 'hulk.spawncover', 'xmp_': x+14080, 'ymp_': y+69120,
                 'wmp_': 38912, 'hmp_': 23040, 'z_': 48, 'draw_': True,
                 'lifetime_': int(self.rules.SPAWN_COVER_DELAY*1000),
                 'move_': {'relativeto_': {'objid_': item['portal']},
                           'to_': {'xmp_': 14080, 'ymp_': 69120}}}
        self.batch.create(item['cover_id'], cover)

    def _fire_battery(self, turret_id, ammo, aim, stage='release'):
        center = self.room.turret_center(turret_id)
        if center is None:
            return
        origin = self._muzzle(turret_id, aim)
        if stage == 'release' and turret_id in self.preparing_projectiles:
            for projectile in self.preparing_projectiles.pop(turret_id):
                projectile.prepared = False
                projectile.x = origin[0]-projectile.w/2
                projectile.y = origin[1]-projectile.h/2
                projectile.damage = self._enemy_damage(
                    projectile, projectile.launch_damage)
                self.batch.modify(projectile.objid, 'DAMAGE', intval=projectile.damage)

                self.batch.modify(projectile.objid, 'REMOVETYPE', intval=projectile.remove_type)
                if ammo == 'bossmissile':
                    self.batch.modify(projectile.objid, 'POSE', intval=2)
                self._start_projectile_motion(projectile)
                if ammo == 'csbullet':
                    self.batch.sound(projectile.objid, 'hulk.bulletshoot')
            return
        rounds = []
        if ammo == 'csbullet':
            rounds = [self.projectiles.fire_csbullet(turret_id, origin, aim)]
        elif ammo == 'scattershot':
            rounds = self.projectiles.fire_scattershot(turret_id, origin, aim)
        elif ammo == 'bossmissile':
            rounds = [self.projectiles.fire_bossmissile(turret_id, origin, aim)]
        if stage == 'prepare':
            self.preparing_projectiles[turret_id] = rounds
        for projectile in rounds:
            if stage == 'prepare':
                projectile.prepared = True
                projectile.damage = 0
            self._emit_projectile(projectile)
            if stage == 'prepare' and ammo == 'csbullet':
                self.batch.sound(projectile.objid, 'hulk.bullettelegraph')
            elif stage == 'prepare' and ammo == 'scattershot':
                self.batch.animprops(projectile.objid, 1, 528)

    def _fire_enemy_shot(self, item, offset=0):
        vid = item.get('vid')
        shot = self.rules.ENEMY_SHOTS.get(vid)
        if shot is None:

            vid = str(vid).split('.')[-1]
            shot = self.rules.ENEMY_SHOTS.get(vid)
        if shot is None:
            return
        speed = shot.get('speed', 300 * 256)
        projectile = self.projectiles.fire_enemy_shot(
            item.get('owner'), vid, item.get('origin'),
            item.get('aim'), shot.get('damage', 20), speed,
            homing=bool(shot.get('homing')),
            turn_rate=shot.get('turn_rate', 90),
            kind=shot.get('kind', 'straight'),
            gravity=shot.get('gravity', 0.0),
            accel=shot.get('accel', 0.0),
            max_speed=shot.get('max_speed', 0.0))
        if item.get('native_move'):
            projectile.set_velocity_command(item['native_move'])
        self._emit_projectile(projectile,offset=offset)
        name = {'sgmbullet':'hulk.bulletgen', 'sshot':'hulk.shotgungen',
                'smissile':'hulk.missilegen', 'cannonshot':'hulk.mortargen'}.get(vid)
        if name:
            self.batch.sound(projectile.objid, name,offset=offset)

    def _muzzle(self, turret_id, aim):
        center = self.room.turret_center(turret_id)
        item = self.room.object(turret_id)
        radius = 0
        if item is not None:
            radius = max(item.rect()[2], item.rect()[3]) // 2
        rad = math.radians(aim)
        return (center[0] + int(math.cos(rad) * radius),
                center[1] + int(math.sin(rad) * radius))

    def _enemy_damage(self, projectile, value):
        """Editor's enemy-damage multiplier for hostile objects only."""
        if int(getattr(projectile, 'owner_type', 2) or 0) != 2:
            return int(value)
        return self.balance.damage(int(value))

    def _projectile_props(self, projectile):
        props = (self.room.template_props(projectile.vid) or
                 (self.room.template_props('hulk.cannonshot')
                  if projectile.vid == 'hulk.dronebomb' else None) or {})
        props.pop('move', None)
        props.pop('move_', None)

        props.pop('lifetime', None)
        props.pop('lifetime_', None)
        props['vid_'] = projectile.vid
        props['xmp_'] = int(projectile.x)
        props['ymp_'] = int(projectile.y)
        props['wmp_'] = int(projectile.w)
        props['hmp_'] = int(projectile.h)
        props['ownertype_'] = projectile.owner_type

        hitbox_only = (projectile.kind == 'hazard' and 'hitboxdamage_' in props
                       and 'damage_' not in props)
        props['damage_'] = 0 if hitbox_only else int(projectile.damage)
        if hitbox_only:
            hitbox = int(props.get('hitboxdamage_') or 0)
            if hitbox:
                props['hitboxdamage_'] = self._enemy_damage(projectile, hitbox)
        if projectile.knockback:
            props['knockback_'] = True

        remove = props.get('removetype_', 1)
        projectile.remove_type = int(remove.get('value', 0) if isinstance(remove, dict) else remove)
        if projectile.prepared:

            props['removetype_'] = projectile.remove_type & ~2
        props['rotate_'] = bool(projectile.rotate)
        if projectile.lifetime:
            props['lifetime_'] = int(projectile.lifetime * 1000)
        if projectile.rotate:
            props['facing_'] = int(math.degrees(
                math.atan2(projectile.vy, projectile.vx)))
        if projectile.homing:

            props['facing_'] = 180 if projectile.vx < 0 else 0
            props['pose_'] = projectile.flight_pose()
        elif projectile.kind == 'napalm':

            props['facing_'] = 180 if projectile.vx < 0 else 0
            props['pose_'] = 5
        elif projectile.vid in ('hulk.rpg', 'hulk.ngrenade'):
            props['facing_'] = 180 if projectile.vx < 0 else 0
        elif projectile.vid == 'hulk.dronebomb':
            props['rotate_'] = False
            props['facing_'] = 0
        return props

    def _emit_projectile(self, projectile, offset=0):
        self.emitted_projectiles[projectile.objid] = projectile

        if projectile.damage > 0:
            projectile.damage = self._enemy_damage(projectile, projectile.damage)
        props = self._projectile_props(projectile)
        if projectile.prepared:
            parent = self.room.object(projectile.owner)
            px, py = parent.position()
            props['move_'] = {'relativeto_': {'objid_': projectile.owner},
                              'to_': {'xmp_': int(projectile.x-px),
                                      'ymp_': int(projectile.y-py)}}
        self.batch.create(projectile.objid, props, creatorid=projectile.owner,offset=offset)
        if projectile.prepared:
            return
        self._start_projectile_motion(projectile,offset=offset)

    def _start_projectile_motion(self, projectile, offset=0):
        if projectile.kind == 'hazard':
            return
        if projectile.velocity_command is not None:
            self.batch.modify(projectile.objid, 'MOVE',
                              message=('Properties_Move',{'vel_':projectile.velocity_command}),offset=offset)
            return
        duration = (0.05 if projectile.homing or projectile.kind in ('falling', 'bouncing', 'napalm')
                    or projectile.accel else max(0.001, projectile.lifetime-projectile.age))
        self.batch.linear_motion(projectile.objid,
                                 (projectile.x, projectile.y),
                                 (projectile.x + projectile.vx * duration,
                                  projectile.y + projectile.vy * duration),
                                 duration * 1000,offset=offset)
        self.projectile_motion_due[projectile.objid] = (
            self.room_time + duration - 1e-8)

    def _frame_end_offset(self, dt):
        return max(0,int(round(self._wire_time_ms+dt*1000))-self._sent_time_ms)

    def _emit_projectiles(self, dt):
        offset=self._frame_end_offset(dt)
        for projectile in self.projectiles.live():
            if projectile.prepared:

                projectile.age += dt
                continue
            if projectile.velocity_command is not None:
                continue
            carried = projectile.kind == 'hazard' and projectile.support is not None
            if not (projectile.homing or projectile.kind in ('falling', 'bouncing', 'napalm')
                    or projectile.accel or carried):
                continue
            if self.room_time < self.projectile_motion_due.get(projectile.objid, 0):
                continue
            if carried:

                end=(projectile.x + projectile.carry_velocity[0] * 0.05,
                     projectile.y + projectile.carry_velocity[1] * 0.05)
            else:
                end=(projectile.x + projectile.vx * 0.05,
                     projectile.y + projectile.vy * 0.05)
            self.batch.linear_motion(projectile.objid,
                                     (projectile.x, projectile.y), end, 50,offset=offset)
            if projectile.homing:
                self.batch.modify(projectile.objid, 'FACING',
                                  intval=180 if projectile.vx < 0 else 0,offset=offset)
                self.batch.modify(projectile.objid, 'POSE', intval=projectile.flight_pose(),offset=offset)
            self.projectile_motion_due[projectile.objid] = self.room_time + 0.05 - 1e-8
        for objid, projectile in list(self.emitted_projectiles.items()):
            if projectile.alive:
                continue
            burst=projectile.impact_burst()
            if burst:
                self._emit_burst(projectile,offset=offset)
            self.batch.destroy(objid, ownerid=self.worldid,
                               reason=projectile.death_reason,offset=offset if burst else 0)
            del self.emitted_projectiles[objid]
            self.projectile_motion_due.pop(objid, None)

    def _emit_burst(self, projectile, effect_vid=None, offset=0):
        effect_vid = effect_vid or projectile.burst_effect()
        if effect_vid == 'hulk.napalm':

            drone = projectile.vid == 'hulk.dronebomb'
            count = (3 if drone else self.projectiles.random.randint(
                *self.rules.SCAVENGER_FIRE_COUNT_RANGE))
            spread = 30000 if drone else self.rules.SCAVENGER_FIRE_SPREAD
            for index in range(count):
                vx = -spread + 2*spread*index/max(1,count-1)
                if not drone:
                    vx = math.floor(vx/1000)*1000
                child = self.projectiles.spawn(vid='hulk.napalm', owner=projectile.objid,
                    x=projectile.x+projectile.w/2-3072,
                    y=projectile.y+projectile.h/2-3072,
                    vx=vx, vy=0, width=6144,height=6144,damage=25,
                    kind='napalm', lifetime=3.0,gravity=self.rules.NAPALM_GRAVITY,
                    rotate=False,owner_type=projectile.owner_type)
                self._emit_projectile(child,offset=offset)
            return
        props = (getattr(self.rules, 'BURST_PROPS', {}).get(effect_vid) or
                 self.room.template_props(effect_vid) or
                 (self.rules.DRONE_IMPACT_PROPS if effect_vid == 'hulk.cannonshotx' else {}))
        import copy as _copy
        props = _copy.deepcopy(props)
        props.pop('move', None)
        props.pop('move_', None)
        w,h=props.get('wmp_',10240),props.get('hmp_',10240)

        center = ((projectile.x, projectile.y) if effect_vid == 'hulk.cannonshotx'
                  else (projectile.x+projectile.w/2, projectile.y+projectile.h/2))
        hazard=self.projectiles.spawn(vid=effect_vid, owner=projectile.objid,
            x=center[0]-w/2, y=center[1]-h/2, vx=0,vy=0,width=w,height=h,
            damage=props.get('damage_') or props.get('hitboxdamage_') or 0,
            lifetime=props.get('lifetime_',500)/1000.0, kind='hazard', rotate=False,

            owner_type=getattr(projectile, 'owner_type', 2))
        self._emit_projectile(hazard,offset=offset)
        name = {'hulk.sshotx':'hulk.shotx', 'hulk.smissilex':'hulk.missilex',
                'hulk.grenadex':'hulk.grenadex', 'hulk.barrelx':'hulk.barrelx',
                'hulk.cannonshotx':'hulk.mortarx'}.get(hazard.vid)
        if name:
            self.batch.sound(hazard.objid, name,offset=offset)

    def _emit_new_units(self):
        for unit in self.enemies.live():
            if getattr(unit, 'emitted', False):
                continue
            unit.emitted = True
            props = self._unit_props(unit)
            self.batch.create(unit.objid, props)
            if unit.create_vid and unit.create_vid != unit.vid:
                self.batch.modify(unit.objid, 'VID', stringval=unit.vid)
            unit.create_vid = None
            self.unit_render_state[unit.objid] = {
                'x': unit.x, 'y': unit.y, 'facing': unit.facing, 'pose': None, 'vid':unit.vid}

    def _unit_props(self, unit):
        from .enemies import _template_props

        props = _template_props(self.room, unit.create_vid or unit.vid)
        props['vid_'] = unit.create_vid or unit.vid
        props['xmp_'] = int(unit.x)
        props['ymp_'] = int(unit.y)
        props['wmp_'] = int(unit.w)
        props['hmp_'] = int(unit.h)
        props['hitpoints_'] = int(unit.hp)
        props['maxhitpoints_'] = int(unit.hp)
        props['ownertype_'] = {'name': 'OWNERTYPE_ENEMY', 'value': 2}
        props['damage_'] = int(unit.contact)
        props['armor_'] = int(unit.armor)
        props['draw_'] = True
        props['facing_'] = 180 if unit.facing < 0 else 0
        props['pose_'] = unit.pose
        if unit.kind == 'bomb-rover' and not unit.bomb_armed:
            props.pop('hitpoints_', None)
            props.pop('maxhitpoints_', None)
            props['damage_'] = 0
        return props

    def _emit_units(self, dt):
        dt_ms = int(round(dt * 1000))
        for unit in self.enemies.units:
            if not getattr(unit, 'emitted', False):
                continue
            if unit.alive:
                previous = self.unit_render_state.setdefault(unit.objid, {
                    'x': unit.x, 'y': unit.y, 'facing': None, 'pose': None, 'vid': unit.vid})
                if previous.get('vid') != unit.vid:
                    self.batch.modify(unit.objid, 'VID', stringval=unit.vid)
                    self.batch.modify(unit.objid, 'HITPOINTS', intval=unit.hp)
                    self.batch.modify(unit.objid, 'MAXHITPOINTS', intval=unit.hp)
                    self.batch.modify(unit.objid, 'DAMAGE', intval=unit.contact)
                moved = (int(previous['x']), int(previous['y'])) != (int(unit.x), int(unit.y))
                if moved:
                    self.batch.linear_motion(unit.objid,
                                             (previous['x'], previous['y']),
                                             (unit.x, unit.y), dt_ms)
                if previous['facing'] != unit.facing:
                    self.batch.modify(unit.objid, 'FACING',
                                      intval=180 if unit.facing < 0 else 0)
                pose = unit.pose
                if previous['pose'] != pose:
                    self.batch.modify(unit.objid, 'POSE', intval=pose)
                previous.update(x=unit.x, y=unit.y, facing=unit.facing, pose=pose, vid=unit.vid)
            else:
                self.batch.destroy(unit.objid, ownerid=1,
                    reason='INTERNAL' if unit.state in ('owner-death','bomb-detonated') else
                           'CANCEL' if unit.state == 'removed' else 'HITPOINTS')
                unit.emitted = False
                self.unit_render_state.pop(unit.objid, None)

    def _extend_ledges(self):
        self.platforms.holds.pop('generator',None)
        self.platforms._reconcile()
        self.event('ledges-extended')

    def _schedule_ledge_terrain(self, value):
        for objid in self.rules.LEDGE_IDS:
            self.platforms.terrain_due[objid] = (
                self.room_time+self.rules.LEDGE_TERRAIN_DELAY,int(value))

    def _apply_ledge_terrain(self):
        self.platforms.step()

    def _retract_ledges(self, seconds):
        self.platforms.retract(self.rules.LEDGE_IDS,seconds,'generator',
                               delay=self.rules.GEN_LEDGE_DELAY)
        self.event('ledges-retract-scheduled', seconds=seconds,
                   delay=self.rules.GEN_LEDGE_DELAY)

    def on_gen_destroyed(self, gen_id):
        self._retract_ledges(self.rules.LEDGE_RETRACT_SECONDS)
        if len(self.gens.destroyed) >= len(self.rules.GEN_IDS):
            self._begin_death()

    def _begin_death(self):
        if self.phase in ('death', 'done'):
            return
        self.phase = 'death'
        self.phase_time = 0.0
        self.death_started = time.time()
        self.death_index = 0
        self.death_records = self._load_death_records()
        self._clear_combat()
        self._prepare_death_motion()
        self.event('boss-clear', records=len(self.death_records))
        if not self.death_records:
            self.batch.shake(self.rules.DEATH_SHAKE_MAGNITUDE,
                             self.rules.DEATH_SHAKE_DURATION)

    def _clear_combat(self):
        """No frozen attackers or projectiles survive a win/player death."""
        self.enemies.enabled = False
        for item in self.enemies.pending:
            if item['shown']:
                for objid in (item['preview_id'], item['cover_id']):
                    self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
        self.enemies.pending.clear()
        self.enemies.portal_refills.clear()
        self.gens.enabled = False
        for unit in self.enemies.units:
            unit.alive = False
            unit.state = 'dead'
            if unit.emitted:
                self.batch.destroy(unit.objid, ownerid=self.worldid, reason='CANCEL')
                unit.emitted = False
        self.unit_render_state.clear()
        for objid in self.emitted_projectiles:
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
        for projectile in self.projectiles.projectiles:
            projectile.alive = False
        self.emitted_projectiles.clear()
        self.projectile_motion_due.clear()
        self.preparing_projectiles.clear()
        for battery in self.batteries:

            battery.preparing_until = dict(
                (turret_id, None) for turret_id in battery.turret_ids)
        for objid in self.rules.LEDGE_IDS:
            self.batch.modify(objid, 'TERRAINTYPE', intval=0)
            item = self.room.object(objid)
            if item is not None:
                item.set(terraintype_=0)
        self.ledge_terrain_at = self.ledge_drop_until = None
        self.platforms.stop()

    def _prepare_death_motion(self):
        ship = self.room.object(self.rules.SHIP_ID)
        if ship is None:
            return
        start = ship.position()
        reference = self.ship_recorded_final or start
        self.death_offset = tuple(a-b for a,b in zip(start, reference))
        positions = []
        for row in self.death_records:
            delta = self.layouts.decode('Delta', row['payload'], raw_strings=True)
            for update in (delta.get('worldupdate_') or {}).get('objectupdates_') or []:
                if update.get('objid_') == self.rules.SHIP_ID and 'movetoymp_' in update:
                    positions.append((row['at'], update.get('movetoxmp_', reference[0]),
                                      update['movetoymp_']))
        if not positions:
            return
        self.death_motion_end = positions[-1][0]
        points, previous = [], 0.0
        dx, dy = self.death_offset
        for at, x, y in positions:

            points.append({'dt_': max(1, int(round((at-previous)*1000))),
                           'xmp_': x+dx,
                           'ymp_': int(y+dy*max(0,1-at/self.death_motion_end))})
            previous = at
        self.death_motion = MotionTrack(start, {'waypoint_': {'points_': points}})

    def _load_death_records(self):
        path = getattr(self.options, 'death_sequence',
                       self.rules.DEATH_SEQUENCE_PATH)
        if not path or not os.path.exists(path):
            return []
        rows = []
        try:
            for item in jsonio.load_json(path)[0]:
                delta = {'worldupdate_': {'generalupdates_': item['general'],
                                         'objectupdates_': item['objects']}}
                rows.append({'at': float(item['at']), 'type': 12,
                             'payload': self.layouts.encode('Delta', delta)})
        except (OSError, ValueError, KeyError) as error:
            self.log('death presentation load failed: ' + str(error))
            return []
        rows.sort(key=lambda row: row['at'])
        return rows

    def _step_death(self, dt, events):
        if self.death_motion is not None and self.phase_time >= self.death_motion_due:
            start = self.death_motion.position(self.phase_time)
            end = self.death_motion.position(self.phase_time + 0.05)
            self.room.move_object(self.rules.SHIP_ID, *start)
            self.batch.linear_motion(self.rules.SHIP_ID, start, end, 50)
            self.death_motion_due = self.phase_time + 0.05 - 1e-8
        while (self.death_index < len(self.death_records)
               and self.death_records[self.death_index]['at']
               <= self.phase_time):
            row = self.death_records[self.death_index]
            try:
                if row['type'] == 12:
                    delta = self.layouts.decode('Delta', row['payload'],
                                                raw_strings=True)
                    world = delta.get('worldupdate_') or {}
                    parts = set(self.rules.BOSS_PART_IDS +
                                self.rules.BOSS_DOOR_IDS + (self.rules.SHIP_ID,))

                    for update in world.get('generalupdates_') or []:
                        create = update.get('create_')
                        destroy = update.get('destroy_')
                        if create:
                            props = create.get('props_') or {}
                            vid = props.get('vid_')
                            if isinstance(vid, dict):
                                vid = base64.b64decode(vid['raw']).decode('utf-8')
                            if vid not in ('hulk.bossx', 'hulk.dropshipruin', 'lobbyportal'):
                                continue
                            self.death_fx_ids.add(create['objid_'])
                            if vid != 'lobbyportal':
                                props['xmp_'] = props.get('xmp_', 0) + self.death_offset[0]
                                if vid == 'hulk.bossx' and self.death_motion_end > 0:
                                    props['ymp_'] = int(props.get('ymp_', 0) + self.death_offset[1] *
                                        max(0, 1-row['at']/self.death_motion_end))
                        elif destroy:
                            if destroy.get('objid_') not in parts | self.death_fx_ids:
                                continue
                            if destroy.get('objid_') == self.rules.SHIP_ID:
                                self.death_motion = None
                        elif not update.get('screenshake_'):
                            continue
                        update.pop('t_', None)
                        update['offset_'] = 0
                        self.batch.general.append(update)
                    for update in world.get('objectupdates_') or []:
                        objid = update.get('objid_')
                        if objid not in parts:

                            if not (objid in self.death_fx_ids
                                    and 'playsound_' in update):
                                continue
                        if update.get('objid_') == self.rules.SHIP_ID and (
                                'movetoxmp_' in update or 'movetoymp_' in update or
                                (update.get('modifyprops_') or {}).get('property_') in (41, 42)):
                            continue
                        update['offset_'] = 0
                        self.batch.objects.append(update)
                    loot = delta.get('createloot_')
                    if loot and len(loot.get('itemprops_') or []) > 1:
                        known = getattr(self.server, 'item_types', None)
                        if known:
                            loot['itemprops_'] = [item for item in loot['itemprops_']
                                                 if item.get('type_') in known]
                        loot['props_']['xmp_'] += self.death_offset[0]
                        if loot['itemprops_']:
                            self.pending_loot.append(loot)
            except Exception as error:
                self.log('death presentation decode failed: ' + str(error))
            self.death_index += 1
        if self.death_index >= len(self.death_records):
            self.phase = 'done'
            self.event('death-sequence-finished')

    def _sync_player(self):
        consume = getattr(self.bridge, 'consume_interactions', None)
        self._frame_interactions = tuple(consume()) if callable(consume) else None
        if getattr(self.options, 'live_force_demo', False):

            if self.fallback_player is None:
                self._create_demo_player()
            self.player = self.fallback_player
            return
        state, keys = self.bridge.snapshot()
        if state is not None and state.objid > 0 and state.worldid > 0:
            self.native_player_seen = True

        if self.telemetry_paused and state is not None:
            self._resume_telemetry()
        if state is None:

            if self._player_rect_reported:
                now = time.time()
                if self._player_missing_since is None:
                    self._player_missing_since = now
                timeout = float(getattr(self.options, 'live_player_gap', 3.0))
                if (now - self._player_missing_since >= timeout
                        and self.phase in ('intro', 'deploying', 'fighting')
                        and not self.telemetry_paused):
                    self.telemetry_paused = True
                    self.player = None
                    self.event('player-telemetry-gap',
                               seconds=round(now - self._player_missing_since, 2),
                               action='fight continues without the local '
                                      'player until the client reports again')
        if state is not None:
            if self.telemetry_paused:
                self._resume_telemetry()
            self.player = state
            if self.bridge.datagrams and not getattr(self.bridge, 'shared_room', False):
                for row in state.raw.get('terrainObjects',[]):
                    objid=int(row.get('objid',0))
                    if objid not in self.destructible_terrain_hp:continue
                    if row.get('present') is False or row.get('hp',1)<=0:
                        self._retire_destructible_terrain(objid)
                        self.batch.destroy(objid,ownerid=self.worldid,
                            reason='CANCEL' if row.get('present') is False else 'HITPOINTS')
            self._player_rect_reported = True
            self._player_missing_since = None
            if (not state.alive and not self.combat_players()
                    and self.phase in ('intro', 'deploying', 'fighting')):
                self.phase = 'failed'
                self.phase_time = 0.0
                self._clear_combat()
                self.event('practice-ended', reason='player-dead',
                           restart='Close the game and run play.cmd again')
            if self.room_time - self.last_player_log >= 5.0:
                self.last_player_log = self.room_time
                self.event('player', x=state.xmp, y=state.ymp,
                           hp=state.hp, objid=state.objid,
                           raw=dict((key, value) for key, value in
                                    state.raw.items() if key != 'received_at'))
        interact = bool(keys.get('interact'))
        edge = bool(keys.get('interact_edge'))
        self.interact_latch = edge or (interact and not self.interact_was_down)
        self.interact_was_down = interact
        if (self.player is None and not self.telemetry_missing_logged
                and self.room_time > 5.0):
            self.telemetry_missing_logged = True
            self.log('live bridge not connected yet; using fallback player')

    def _resume_telemetry(self):
        """Leave the paused state after a bridge silence."""
        since = self._player_missing_since
        self.telemetry_paused = False
        self._player_missing_since = None
        self._telemetry_mark = None
        self.event('player-telemetry-restored',
                   seconds=round(time.time() - since, 2) if since else 0.0)

    def _native_damage_active(self):

        return self.native_player_seen or self.bridge.connected()

    def interact_edge(self):
        from ..interactions import can_interact
        edge = self.interact_latch
        self.interact_latch = False
        return edge and can_interact(self.player)

    def combat_players(self):
        from ..participants import from_bridge
        return from_bridge(self.bridge, self.player)

    def interacting_players(self):
        from ..interactions import can_interact
        current = getattr(self, '_frame_interactions', None)
        if current is not None:
            self._frame_interactions = ()
            return tuple(p for p in current if can_interact(p))
        consume = getattr(self.bridge, 'consume_interactions', None)
        if callable(consume):
            return tuple(p for p in consume() if can_interact(p))
        return tuple(p for p in self.combat_players() if can_interact(p)) if self.interact_edge() else ()

    def _send_tick(self, dt):
        batch = self.batch
        if self.suppress_send:
            batch.general = []
            batch.objects = []
            batch.extra = {}
            batch.dt = 0
            return
        if self.pending_loot:

            loot = self.layouts.encode('Delta', {'createloot_': self.pending_loot.pop(0)})
            self.server._send_raw(self.channel, 12, loot, 'live-loot', record=False)
        self._wire_time_ms += dt * 1000.0
        target_ms = int(round(self._wire_time_ms))
        batch.dt = target_ms - self._sent_time_ms
        self._sent_time_ms = target_ms
        payload = batch.encode()
        try:
            self.server._send_raw(self.channel, 12, payload, 'live',
                                  record=False)
        except Exception as error:
            self.log('live send failed: ' + str(error))
            self._stop.set()
        batch.general = []
        batch.objects = []
        batch.extra = {}
        batch.dt = 0

    def _refresh_balance(self):
        """Re-read the editor's per-map balance knobs on an mtime change.

        Values are consulted when a body or projectile is born, so a saved
        change reaches the next spawn; the room cap and the next portal
        roll use the fresh numbers too.  One stat() per tick, not per unit.
        """
        mtime = game_settings.settings_mtime()
        if mtime == self._balance_mtime:
            return
        self._balance_mtime = mtime
        self.projectiles.missile_delay_seconds = game_settings.missile_delay_ms(self.map_id)/1000.
        values = game_settings.balance(self.map_id)
        if values != self.balance.values:
            self.balance.update(values)
            self.event('balance-changed', **values)

    def _supply_rates(self):
        """(drop chance, weights) from game_settings.json, mtime-cached.

        The gear editor writes the health/ammo percentages; re-reading on
        an mtime change means a drop-rate edit applies to the running game
        without a restart, while the normal path stays one stat() call.
        """
        path = game_settings.PATH
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        previous = self._supply_settings
        if previous is None or mtime != self._supply_settings_mtime:
            self._supply_settings_mtime = mtime
            chance, table = game_settings.supply_rates(
                game_settings.load(path), self.map_id)
            self._supply_settings = (chance, table)
            if previous != (chance, table):
                self.log('supply rates: {0:g}% total, health/ammo/EC {1}'.format(
                    chance * 100.0, table))
        return self._supply_settings or (0.0, ())

    def _maybe_drop_supply(self, x, y):

        self.supplies.rng = self.loot_random
        item_type = self.supplies.killed(x, y)
        if item_type is None:
            return
        self.event('supply-drop', itemType=int(item_type),
                   x=int(x), y=int(y))

    def event(self, kind, **values):
        row = self.events.add(kind, **values)
        self.log('live event: ' + json.dumps(row, sort_keys=True))
        return row

    def status(self):
        return {
            'phase': self.phase,
            'phaseTime': round(self.phase_time, 2),
            'roomTime': round(self.room_time, 2),
            'gensDestroyed': list(self.gens.destroyed),
            'enemies': len(self.enemies.live()),
            'projectiles': len(self.projectiles.live()),
            'bridge': self.bridge.connected(),
        }

    def damage_feedback_snapshot(self):
        """Private bounded diagnostics; counts never authorize or reroll hits."""
        return dict(protocol='controller-damage-stages/1',counts=dict(self.damage_feedback_counts),
            ignoredSamples=list(self.damage_ignored_samples))
