'lab_hard (ETL) live session.'

import collections
import itertools
import math
import random
import threading
import time

from ... import pb_layout
from ...live.bridge import PlayerBridge, PlayerState
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from .controller import LabHardBossController
from .scene import LabHardPack, normalize_props
from .arena import ArenaMotion, swept_hit, wall_normal
from .claw_attacks import ClawAttacks

MT_SERVER_DELTA = 12

BOSS_ANIM = {'SHOOT': (1, 1320), 'SHOOT4': (2, 396), 'SHOOT3': (4, 1320),
             'SUMMON': (5, 2640),
             'SHOOT2': (6, 1320), 'DESTROY': (7, 1848),
             'INTERACT': (8, 594), 'INTERACT2': (9, 264)}
CLAW_ANIM_SWING = (1, 1122)
ADD_ANIM_CREATE = (3, 396)
RING_STEP_DEG = 14.4
TRIPLE_SPREAD_DEG = 10.0

TRIPLE_AIM_HISTORY_S = 3.0
BULLET_SPAWN_PX = 142.292

class LabHardLiveSession(object):
    """60 Hz host for the lab_hard boss controller."""

    HZ = 60.0
    BULLET_SPEED = 440.0 * 256.0
    BULLET_LIFETIME = 6.5
    BULLET_MOVE_STEP = 0.05
    BULLET_MOVE_MS = 50
    ARM_MOVE_MS = 50
    ARM_MOVE_STEP = 0.05

    CLAW_SPED_MP_S = 157000
    ARM_SPED_MP_S = 76530
    SPIKE_PLATFORM_ARMING_S = 1.0
    SPIKE_GRAVITY_MP_S2 = 20000.0
    CLAW_MOVE_MS = 78
    WALL_PULL_DEPTH = 64 * 256
    WALL_MOVE_MS = 1700
    WALL_MIN_GAP_PX = 160

    WALL_NEAR_RANGE_PX = 500
    WALL_NEAR_GAP_PX = 280
    WALL_FAR_GAP_CAP_PX = 360
    WALL_MIN_MOVE_S = 0.001

    CLAW_TELEGRAPH_S = 1.122

    CLAW_REACH_MP_S = 156314.0
    WALL_LINK_DELAY_S = 0.2
    CLAW_DRAG_MP_S = 78080.0
    WALL_RETURN_SPED_MP_S = 78080.0
    WALL_HOLD_SECONDS = 14.5
    ADD_LIFETIME = 60.0
    ADD_HITPOINTS = 75

    SEEKER_SPED = 58009

    SEEKER_RETREAT_MP = 384 * 256
    SEEKER_MOVE_STEP = 0.05
    SEEKER_MOVE_MS = 50
    SEEKER_HITPOINTS = 200
    SBULLET_SPED = 77621
    SBULLET_DAMAGE = 30
    SBULLET_LIFETIME = 8.0
    SEEKER_FIRE_DELAY = 2.114
    SEEKER_FIRE_PERIOD = 2.355
    DEATH_BURSTS = 45
    DEATH_BURST_SECONDS = 2.6

    DEATH_CORPSE_DELAY = 2.765
    DEATH_LOOT_DELAY = 2.761
    DEATH_END = 6.0

    BOSS_SPEED = (0.0, 20000.0, 19000.0, 62000.0, 80000.0)

    BOSS_MOVE_STEP = 0.05
    BOSS_MOVE_MS = 50

    BOSS_CONTACT_PX = 128

    BOSS_INERTIA_TAU = 2.5
    BOSS_PURSUIT_LAG_S = 0.75

    def __init__(self, server, channel, options, room=None, log=None,
                 bridge=None):
        self.server = server
        self.channel = channel
        self.options = options
        self.room = room
        self.log = log or (lambda message: None)
        self.layouts = (getattr(server, 'layouts', None)
                        or pb_layout.default_layouts())
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self.pack = LabHardPack()

        self.pack.apply_room(room)
        self._static_terrain = None
        self.bridge = bridge or PlayerBridge(
            int(getattr(options, 'bridge_port', 21550) or 21550),
            log=self.log)
        self.owns_bridge = bridge is None
        seed=getattr(options,'live_seed',None)
        self.seed = random.SystemRandom().getrandbits(64) if seed is None else int(seed)
        self.rng = random.Random(self.seed)
        self.loot_rng = random.Random(self.seed ^ 0x4C4142)

        self.phase = 'waiting'
        self.phase_time = 0.0
        self.room_time = 0.0
        self.player = None
        self.player_type = 1
        self.native_player_seen = False
        self.controller = None

        self.boss_pos = [0, 0]
        self.claws = list(self.pack.claw_ids)
        self._ring_base = 0.0
        self._next_objid = 9000
        self.bullets = {}
        self.adds = {}
        self.arms = {}
        self.wall_pos = {}
        self.motion = None
        self.gas = None
        self.gas_fronts = []
        self.blasers = {}
        self.gas_cores = {}
        self.special_gas_origin = None
        self.spike_balls = 0
        self.seekers = {}
        self.seeker_shots = 0
        self.pending_loot = []
        self.death_time = 0.0
        self.death_bursts = 0
        self.death_fx = {}
        self.corpse_done = False
        self.loot_done = False
        self.boss_destroyed = False
        self.total_boss_damage = 0.0
        self.events = []
        self.claw_steps = []
        self.claw_facings = []
        self.claw_busy = {}
        self.claw_attacks = ClawAttacks(self)
        self.pending_seekers = []
        self.wall_groups = {}
        self.pending_wall_groups = {}
        self.pending_shakes = []
        self._player_history = collections.deque(
            maxlen=int(self.HZ * TRIPLE_AIM_HISTORY_S))
        self._player_histories = {}
        from ...participants import DamageTargetLock
        self.movement_targets = DamageTargetLock()
        self._move_tick = 0
        self._stop = threading.Event()
        self._thread = None
        self._wire_time_ms = 0.0
        self._sent_time_ms = 0
        self.frames = 0

    def prepare(self):
        self.supplies = SupplyDrops(self, 'lab_hard', rng=self.loot_rng)
        self.batch.general = []
        self.batch.objects = []
        self.batch.extra = {}
        self.phase = 'waiting'
        self.phase_time = 0.0
        self.room_time = 0.0
        self.player = None
        self.controller = None
        self.bullets.clear()
        self.adds.clear()
        self.arms.clear()
        self.pending_loot = []
        self.death_time = 0.0
        self.death_bursts = 0
        self.death_fx = {}
        self.corpse_done = False
        self.loot_done = False
        self.boss_destroyed = False
        self.total_boss_damage = 0.0
        self._demo_timer = 0.0
        self.claw_steps = []
        self.claw_facings = []
        self.claw_busy = {}
        self.claw_attacks = ClawAttacks(self)
        self.pending_seekers = []
        self.wall_groups = {}
        self.pending_wall_groups = {}
        self.pending_shakes = []
        self._player_history.clear()
        self._player_histories.clear()
        from ...participants import DamageTargetLock
        self.movement_targets = DamageTargetLock()
        self.wall_pos = {}
        self.gas = None
        self.gas_fronts = []
        self.blasers.clear()
        self.gas_cores.clear()
        self.special_gas_origin = None
        self.spike_balls = 0
        self.seekers.clear()
        self.seeker_shots = 0
        for wall in self.pack.scene.get('walls') or []:
            objid = wall.get('objid')
            if objid is not None:
                self.wall_pos[int(objid)] = [wall.get('xmp'), wall.get('ymp')]
        boss = self.pack.template(self.pack.boss_objid)
        self.boss_pos = [boss.get('xmp') or 0, boss.get('ymp') or 0]
        self.boss_vel = [0.0, 0.0]
        self._boss_path_phase = None
        self._boss_path_anchor = (0.0, 0.0)
        self._boss_path_last = tuple(self.boss_pos)
        self._wall_pull_busy_until = 0.0
        self._wall_return_park_until = 0.0
        self._boss_pull_stopped = False
        self._boss_move_due = 0.0
        self._wall_hold_until = 0.0
        self._wall_hold_phase = None
        self.motion = ArenaMotion(self.pack, self.boss_pos)
        if getattr(self.options, 'player_x', None) is not None:
            self.player = PlayerState(
                objid=int(getattr(self.options, 'player_objid', 1) or 1),
                xmp=self.options.player_x, ymp=self.options.player_y,
                wmp=int(getattr(self.options, 'player_wmp', 10 * 1024)),
                hmp=int(getattr(self.options, 'player_hmp', 20 * 1024)),
                hp=100, maxhp=100)

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.log('lab_hard live session started (phase=waiting)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        interval = 1.0 / self.HZ
        deadline = time.time()
        while not self._stop.is_set():
            deadline += interval
            try:
                self.step(interval)
            except Exception:
                import traceback
                self.log('lab_hard tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            wait = deadline - time.time()
            if wait > 0:
                time.sleep(wait)
            else:
                deadline = time.time()

    def _queue_shake(self, when, magnitude, duration=None):
        self.pending_shakes.append((float(when), int(magnitude), duration))

    def _step_shakes(self, now):
        if not self.pending_shakes:
            return
        keep = []
        for when, magnitude, duration in self.pending_shakes:
            if when <= now + 1e-9:
                self.batch.shake(magnitude, duration)
            else:
                keep.append((when, magnitude, duration))
        self.pending_shakes = keep

    def step(self, dt):
        self._sync_player()
        self._track_player()
        self.room_time += dt
        self.phase_time += dt
        if self.phase == 'waiting':
            if self.player is not None:
                self._begin_fight()
        if self.phase == 'fighting':
            self._demo_damage(dt)
            for event in self.controller.step(dt, aim_deg=self._aim_deg()):
                self._handle_event(event)
                if self.phase != 'fighting':
                    break
            if self.phase == 'fighting':
                self._step_pending_wall_groups()
                self._step_boss(dt)
                self.claw_attacks.step()
                self._step_motion(dt)
                self._step_bullets(dt)
                self._step_arms(dt)
                self._step_adds(dt)
                self._step_seekers(dt)
                self._step_gas(dt)
                self._step_blasers(dt)
        elif self.phase == 'death':
            self._step_bullets(dt)
            self._step_blasers(dt)
            self._step_motion(dt)
            self._step_death(dt)
        self._step_shakes(self.room_time)
        self._step_death_fx()
        self._send_tick(dt)

    def _demo_damage(self, dt):
        """Headless / watch-only damage feed (``--live-demo-damage``)."""
        if not getattr(self.options, 'live_demo_damage', False):
            return
        self._demo_timer += dt
        if self._demo_timer < 0.3:
            return
        self._demo_timer = 0.0
        self.controller.add_damage(700)
        self.total_boss_damage += 700

    def native_retirement_capability(self, objid):
        """Only an active issued bullet can use the room's contact validator."""
        if type(objid) is not int or objid <= 0:
            return None
        bullet = self.bullets.get(objid)
        if bullet is None:
            return None
        expiry = bullet['born'] + bullet.get('lifetime', self.BULLET_LIFETIME)
        return dict(ownerid=self.worldid, objid=objid, role='projectile',
                    expires_at=expiry, room_time=self.room_time,
                    lifetime_due=self.room_time >= expiry)

    def _retire_native_bullet(self, event):

        if not getattr(self.bridge, 'shared_room', False) or event.get('ownerid') != self.worldid:
            return False
        cap = self.native_retirement_capability(event.get('victim'))
        if cap is None:
            return False
        reason = event.get('reason')
        if not (reason == 3 and event.get('authority') == 'native-shield-contact/1'
                or reason == 6 and cap['lifetime_due']):
            return False
        objid = cap['objid']
        del self.bullets[objid]
        self.batch.destroy(objid, ownerid=self.worldid, reason=reason)
        return True

    def _sync_player(self):
        if self.bridge is None:
            return
        state, _keys = self.bridge.snapshot()
        if state is not None:
            self.player = state
            self.native_player_seen = True

        damage_players = None
        damage_reporters = None

        for event in self.bridge.poll_events(limit=4096):
            if not isinstance(event, dict):
                continue
            if 'ownerid' in event and int(event['ownerid'] or 0) != self.worldid:
                continue
            if event.get('kind') == 'destroy' and self._retire_native_bullet(event):
                continue
            if event.get('kind') == 'destroy' and is_hp_death(event.get('reason')):
                victim = int(event.get('victim') or 0)
                if victim in self.adds:
                    self._damage_add(victim, self.adds[victim]['hp'])
                elif victim in self.seekers:
                    self._damage_seeker(victim, self.seekers[victim]['hp'])
                continue
            if event.get('kind') != 'damage':
                continue
            victim = int(event.get('victim') or 0)
            value = int(event.get('value') or 0)
            if value <= 0:
                continue
            if victim == self.pack.boss_objid:
                if self.phase == 'fighting' and self.controller is not None:
                    self.controller.add_damage(value)
                    self.total_boss_damage += value
                    from ...participants import identity
                    if damage_players is None:
                        damage_players = self.combat_players()
                        damage_reporters = {identity(p): p for p in damage_players}
                    source = event.get('player_id')
                    reporter = damage_reporters.get(tuple(source)) if source is not None else None
                    if reporter is None and len(damage_players) == 1 and source is None and event.get('authority')!='environment-damage/1':
                        reporter = damage_players[0]
                    self.movement_targets.add_damage(reporter, value, self.room_time)
            elif victim in self.adds:
                self._damage_add(victim, value)
            elif victim in self.seekers:
                self._damage_seeker(victim, value)
        reach = float(self.claw_attacks.policy.get('max_reach_mp', 140000))
        self.player = self.movement_targets.select(self.combat_players(), self._boss_center(),
                                                   self.room_time, reach, self.rng)

    def combat_players(self):
        from ...participants import from_bridge
        return from_bridge(self.bridge, self.player)

    def _begin_fight(self):
        if self.phase != 'waiting':
            return
        self.phase = 'fighting'
        self.phase_time = 0.0
        self._fight_room_start = self.room_time
        self.controller = LabHardBossController(self.pack,rng=self.rng)
        self.controller.wall_clock.available_claws=self._idle_wall_claws
        for event in list(self.controller.events):
            self._handle_event(event)
        self.log('lab_hard fight started')

    def _handle_event(self, event):
        kind = event.get('kind')
        if kind == 'boss_anim':
            if event.get('animid') is not None:
                self._anim_raw(event['animid'],event['time_ms'])
            else:
                self._anim(event.get('anim') or 'SHOOT')
        elif kind == 'phase_change':
            self._anim(event.get('anim') or 'INTERACT2')
            self.batch.shake(6, 1500)
            if event.get('phase') == 'p2':
                self.batch.modify(self.pack.boss_objid, 'VID',
                                  stringval='lab.zomputer')
            self._event('phase', phase=event.get('phase'), at=event.get('at'))
        elif kind == 'volley':
            self._emit_volley(event)
        elif kind == 'arm_pull':
            self._emit_arm(event)
        elif kind == 'claw_attack':
            if not self.claw_attacks.enabled:
                self._emit_claw_attack(event)
        elif kind == 'adds':

            self._event('adds_batch', count=int(event.get('count') or 0))
        elif kind == 'seeker_spawn':
            self._spawn_seeker_wave(event)
        elif kind == 'wall_pull':
            self._queue_wall_pull(event)
        elif kind == 'gas_start':
            self._begin_gas(event)
        elif kind == 'gas_wave':
            self._emit_gas_wave(event)
        elif kind == 'gas_stop':
            self._stop_gas()
        elif kind == 'special_gas_anim':
            self._anim_raw(event.get('animid') or 5,
                           event.get('time_ms') or 2640)
            self._event('special_gas_anim',
                        animid=int(event.get('animid') or 5),
                        time_ms=int(event.get('time_ms') or 2640))
        elif kind == 'special_gas_core':
            self._spawn_special_gas_core(event.get('life_s') or 0.2)
        elif kind == 'special_gas_ring':
            self._spawn_special_gas_ring(event)
        elif kind == 'death':
            self._begin_death()

    def boss_anim_time_ms(self, name, phase_index=None):
        'runtime per-phase animation length (fallback: ``BOSS_ANIM``).'
        animid, fallback = BOSS_ANIM[name]
        specs = self.pack.phase_doc.get('phases') or []
        if phase_index is None:
            machine = getattr(self.controller, 'machine', None)
            phase_index = getattr(machine, 'index', 0) if machine else 0
        if 0 <= phase_index < len(specs):
            row = (specs[phase_index].get('boss_anims') or {}).get(name)
            if row and row.get('time_ms'):
                return int(row['time_ms'])
        return int(fallback)

    def _anim(self, name):
        if name in BOSS_ANIM:
            animid = BOSS_ANIM[name][0]
            self.batch.animprops(self.pack.boss_objid, animid,
                                 self.boss_anim_time_ms(name))

    def _anim_raw(self, animid, time_ms):
        self.batch.animprops(self.pack.boss_objid, int(animid),
                             int(time_ms))

    def _event(self, kind, **values):
        row = dict(values)
        row['kind'] = kind
        row.setdefault('at', round(self.room_time, 3))
        self.events.append(row)
        return row

    def _alloc(self):
        self._next_objid += 1
        while str(self._next_objid) in self.pack.templates:
            self._next_objid += 1
        return self._next_objid

    def _boss_center(self):
        template = self.pack.template(self.pack.boss_objid)
        return (self.boss_pos[0] + int((template.get('wmp') or 0) / 2),
                self.boss_pos[1] + int((template.get('hmp') or 0) / 2))

    def _track_player(self):
        """Keep a short player-position history for the lagged triple aim."""
        from ...participants import life_identity
        players = self.combat_players()
        live = set()
        for player in players:
            key = life_identity(player)
            live.add(key)
            history = self._player_histories.get(key)
            if history is None:
                history = (self._player_history if len(players) == 1 and not self._player_histories
                           else collections.deque(maxlen=int(self.HZ * TRIPLE_AIM_HISTORY_S)))
                self._player_histories[key] = history
            px, py = player.center()
            if history and history[-1][0] == self.room_time:
                history[-1] = (self.room_time, px, py)
            else:
                history.append((self.room_time, px, py))
        self._player_histories = {key: value for key, value in self._player_histories.items() if key in live}
        if self.player is not None:
            self._player_history = self._player_histories.get(life_identity(self.player), self._player_history)

    def _aim_angle(self, x, y):
        if self.player is None:
            return 90.0
        px, py = self.player.center()
        return math.degrees(math.atan2(py - y, px - x))

    def _aim_deg(self):
        """Bearing from the boss centre to the live player, or None."""
        if self.player is None:
            return None
        target = self._lagged_player_center(0.0)
        if target is None:
            return None
        cx, cy = self._boss_center()
        return math.degrees(math.atan2(target[1] - cy, target[0] - cx)) % 360.0

    def _aim_angle_lagged(self, x, y, lag_s):
        'Aim at where the player stood ``lag_s`` seconds ago.'
        if self.player is None:
            return 90.0
        when = self.room_time - float(lag_s or 0.0)
        px = py = None
        for sample in reversed(self._player_history):
            if sample[0] <= when + 1e-6:
                px, py = sample[1], sample[2]
                break
        if px is None:
            px, py = self.player.center()
        return math.degrees(math.atan2(py - y, px - x))

    def triple_aim_lag_s(self):
        """Measured lag between the triple-volley aim and its spawn."""
        return float(self.pack.triple_spread.get('aim_lag_s') or 0.0)

    def _emit_volley(self, event):
        pattern = event.get('pattern')
        count = int(event.get('count') or 1)
        cx, cy = self._boss_center()
        aim = self._aim_angle(cx, cy)

        spawn_x,spawn_y = cx-512,cy+2048
        if pattern == 'single':

            spread=self.pack.phase_doc.get('single_spread') or {}
            aim=self._aim_angle_lagged(cx,cy,float(spread.get('aim_lag_s',.98)))
            scatter=float(spread.get('half_width_deg',10.0))
            angles = [(aim+self.rng.uniform(-scatter,scatter))%360.0]
        elif pattern == 'triple':
            offsets = event.get('offsets_deg') or []
            if len(offsets) == 3:

                lagged = self._aim_angle_lagged(cx, cy,
                                                self.triple_aim_lag_s())
                angles = [(lagged + float(value)) % 360.0
                          for value in offsets]
            else:
                angles = [aim - TRIPLE_SPREAD_DEG, aim,
                          aim + TRIPLE_SPREAD_DEG]
        elif event.get('facing_deg') is not None:
            angles = [float(event['facing_deg']) % 360.0]
        else:

            step = float(event.get('step_deg') or RING_STEP_DEG)
            base = event.get('base_deg')
            if base is None:
                base = self._ring_base
                self._ring_base = (self._ring_base + step) % 360.0
            angles = [(float(base) + index * step) % 360.0
                      for index in range(count)]

        template = self.pack.wire_props('lab.bbullet')
        for angle in angles:
            self._spawn_bullet(spawn_x - (template.get('wmp_') or 0) / 2.0,
                               spawn_y - (template.get('hmp_') or 0) / 2.0, angle)
        self._event('volley', pattern=pattern, count=len(angles),
                    phase=event.get('phase'), facing_deg=event.get('facing_deg'),
                    base_deg=event.get('base_deg'),
                    offsets_deg=event.get('offsets_deg'))

    def boss_speed_mp_s(self, phase_index):
        """Measured per-phase drift speed (fallback: ``BOSS_SPEED``)."""
        specs = self.pack.phase_doc.get('phases') or []
        if 0 <= phase_index < len(specs):

            row = specs[phase_index].get('boss_speed') or {}
            return float(row.get('median_mp_s') or 0.0)
        return float(self.BOSS_SPEED[min(max(phase_index, 0),
                                         len(self.BOSS_SPEED) - 1)])

    def claw_move_ms(self):
        """Measured claw interpolation time (fallback 78 ms)."""
        doc = self.pack.claw_motion or {}
        return int(doc.get('dt_median_ms') or self.CLAW_MOVE_MS)

    def bullet_spawn_radius(self):
        """Measured lab.bbullet spawn radius in px (fallback 142.292)."""
        doc = self.pack.phase_doc.get('bullet_spawn') or {}
        return float(doc.get('median_radius_px') or BULLET_SPAWN_PX)

    def ring_bases(self):
        'runtime ring lattice origins, in runtime volley order.'
        for window in self.pack.phase_doc.get('attack_windows') or []:
            if window.get('pattern') == 'ring':
                return list(window.get('ring_bases') or [])
        return []

    def _spawn_bullet(self, x, y, angle):
        objid = self._alloc()
        props = self.pack.wire_props('lab.bbullet')
        props['xmp_'] = int(x)
        props['ymp_'] = int(y)
        props['facing_'] = int(angle) % 360
        props['pose_'] = 3
        self.batch.create(objid, props, creatorid=self.pack.boss_objid)
        self.batch.sound(objid, 'lab.bbullet')
        radians = math.radians(angle)
        speed = self.BULLET_SPEED
        self.bullets[objid] = {
            'x': x, 'y': y,
            'vx': math.cos(radians) * speed,
            'vy': math.sin(radians) * speed,
            'born': self.room_time, 'next_move': self.room_time,
        }
        return objid

    def _step_bullets(self, dt):
        bounds = self.pack.play_bounds

        terrain = self._blocker_boxes()
        for objid, bullet in list(self.bullets.items()):
            x, y = bullet['x'], bullet['y']
            tx, ty = x + bullet['vx'] * dt, y + bullet['vy'] * dt
            hits = [(hit, terrain_id) for terrain_id, box in terrain
                    for hit in [swept_hit(x, y, tx, ty, box,
                                          bullet.get('radius', 0))]
                    if hit is not None]
            contact = min(hits) if hits else None
            if contact is not None:
                fraction, terrain_id = contact
                bullet['x'], bullet['y'] = x + (tx-x)*fraction, y + (ty-y)*fraction

                self.batch.move_to(objid, int(bullet['x']), int(bullet['y']),
                                   dt=int(self.BULLET_MOVE_MS))
                self.batch.destroy(objid, ownerid=self.worldid, reason='BLOCKED')

                self._event('shot_blocked', terrain=terrain_id,
                            x=int(bullet['x']), y=int(bullet['y']),
                            spike=bool(bullet.get('spike')))
                del self.bullets[objid]
                continue
            bullet['x'], bullet['y'] = tx, ty
            age = self.room_time - bullet['born']
            outside = (bullet['x'] < bounds[0]
                       or bullet['x'] > bounds[2]
                       or bullet['y'] < bounds[1]
                       or bullet['y'] > bounds[3])
            if outside:
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='BLOCKED')
                del self.bullets[objid]
                continue
            if age >= bullet.get('lifetime', self.BULLET_LIFETIME):
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='LIFETIME')
                del self.bullets[objid]
                continue
            if self.room_time+1e-9 >= bullet['next_move']:
                self.batch.move_to(objid, int(bullet['x']), int(bullet['y']),
                                   dt=int(self.BULLET_MOVE_MS))
                bullet['next_move'] = self.room_time + self.BULLET_MOVE_STEP

    def _blocker_boxes(self):
        'Only walls stop runtime shots; ledges are one-way platforms.'
        result = []
        for objid in self.pack.object_ids('lab.wall'):
            props = self.pack.template(objid)
            x, y = props['xmp'], props['ymp']
            result.append((int(objid), (x, y, x + props['wmp'], y + props['hmp'])))
        for objid in self.pack.wall_ids:
            props = self.pack.template(objid)
            x, y = self.motion.position(objid, self.room_time)
            result.append((int(objid), (x, y, x + props['wmp'], y + props['hmp'])))
        return result

    def _terrain_boxes(self):
        """(objid, rect, vid): ledges are one-way, walls block any side."""
        if self._static_terrain is None:
            self._static_terrain = []
            for vid in ('lab.ledge', 'lab.wall'):
                for objid in self.pack.object_ids(vid):
                    props = self.pack.template(objid)
                    x, y = props['xmp'], props['ymp']
                    self._static_terrain.append((int(objid),
                        (x, y, x + props['wmp'], y + props['hmp']), vid))
        result = list(self._static_terrain)
        for objid in self.pack.wall_ids:
            props = self.pack.template(objid)
            x, y = self.motion.position(objid, self.room_time)
            result.append((int(objid),
                           (x, y, x + props['wmp'], y + props['hmp']),
                           'lab.crushwall'))
        return result

    def _terrain_groups(self):
        ledges, walls = [], []
        for terrain_id, box, vid in self._terrain_boxes():
            if vid == 'lab.ledge':
                ledges.append((terrain_id, box))
            else:
                walls.append((terrain_id, box))
        return ledges, walls

    @staticmethod
    def _wall_normal(box, hx, hy, radius):
        edges = ((abs(hy - (box[1] - radius)), (0, -1)),
                 (abs(hy - (box[3] + radius)), (0, 1)),
                 (abs(hx - (box[0] - radius)), (-1, 0)),
                 (abs(hx - (box[2] + radius)), (1, 0)))
        return min(edges, key=lambda row: row[0])[1]

    def _wave_recipes(self, wave):
        'runtime anchored-add recipes belonging to one spike burst.'
        wanted = float(wave.get('at') or 0.0)
        best = None
        for batch in self.pack.add_waves():
            distance = abs(float(batch.get('at') or 0.0) - wanted)
            if best is None or distance < best[0]:
                best = (distance, batch)
        if best is None or best[0] > 2.0:
            return []
        return list(best[1].get('recipes') or [])

    def _emit_arm(self, event):
        """A burst of radial spikes leaving the live boss centre.

        Paths leave the live boss centre. Platforms become solid to spikes
        after the fitted one-second penetration window; walls can stop them
        earlier. Each tendril carries its own matched ball recipe.
        """
        wave = event.get('wave') or {}
        tendrils = wave.get('tendrils') or []
        if not tendrils:
            return
        cx, cy = self._boss_center()
        ox, oy = tendrils[0]['points'][0][1], tendrils[0]['points'][0][2]
        dx, dy = int(cx - ox), int(cy - oy)
        for tendril in tendrils:
            objid = self._alloc()
            props = normalize_props('Properties', tendril['props'])
            props['xmp_'] = int(props.get('xmp_', ox)) + dx
            props['ymp_'] = int(props.get('ymp_', oy)) + dy
            self.batch.create(objid, props)
            points = [[p[0], int(p[1]) + dx, int(p[2]) + dy, p[3]]
                      for p in tendril['points']]

            recipe = tendril.get('landing') or {}
            points, anchor = self._clip_arm(points, props, recipe)
            life = max(float(tendril['life_s']), points[-1][0] + 0.1)
            start = self.room_time + tendril.get('delay_s', 0.0)
            self.arms[objid] = {
                'start': start,
                'expire': start + life,
                'next_move': start,
                'points': points, 'cursor': 0, 'landed': False,
                'anchor': anchor, 'recipe': recipe,
                'half':((props.get('wmp_') or 6144)/2.,(props.get('hmp_') or 6144)/2.),
                'poses': list(tendril.get('poses') or []),
            }
        self._event('arm', segments=len(tendrils))

    def _ledge_landing(self, x, y, tx, ty, ledges, radius):
        'One-way platform: a descending tip lands on the top face.'
        if ty <= y:
            return None
        contact = None
        for terrain_id, box in ledges:
            top = box[1] - radius
            if (y - top) * (ty - top) > 0:
                continue
            fraction = (top - y) / float(ty - y)
            hx = x + (tx - x) * fraction
            if box[0] - radius <= hx <= box[2] + radius:
                if contact is None or fraction < contact[0]:
                    contact = (fraction, terrain_id, box, (0, -1))
        return contact

    def _clip_arm(self, points, props, recipe):
        'Fit collision to three raw captures, allowing initial penetration.'
        half_x = float(props.get('wmp_') or 6144) / 2.0
        half_y = float(props.get('hmp_') or 6144) / 2.0
        radius = max(half_x, half_y)
        ledges, walls = self._terrain_groups()

        walls=[r for r in walls if r[0] not in self.pack.wall_ids]
        clipped = [list(points[0])]
        extensions = ()
        if len(points)>1:
            a,b=points[-2:]
            span=max(0.001,b[0]-a[0])
            vx=(b[1]-a[1])/span
            vy=(b[2]-a[2])/span
            def continued_points():
                for index in range(1,241):
                    t=index*self.ARM_MOVE_STEP
                    x=b[1]+vx*t
                    y=b[2]+vy*t+.5*self.SPIKE_GRAVITY_MP_S2*t*t
                    facing=int(math.degrees(math.atan2(vy+self.SPIKE_GRAVITY_MP_S2*t,vx)))%360
                    yield [b[0]+t,x,y,facing]
            extensions = continued_points()

        for point in itertools.chain(points[1:],extensions):
            previous=clipped[-1]
            t0,x0,y0,_=previous
            t1,x1,y1,facing=point
            x,y=x0+half_x,y0+half_y
            px,py=x1+half_x,y1+half_y
            contact=self._arm_contact(x,y,px,py,walls,radius)
            landing=None
            if t1 >= self.SPIKE_PLATFORM_ARMING_S:
                start=max(t0,self.SPIKE_PLATFORM_ARMING_S)
                fraction=(start-t0)/(t1-t0) if t1>t0 else 0
                ax,ay=x+(px-x)*fraction,y+(py-y)*fraction
                for terrain_id,box in ledges:
                    if (max(ax,px)+radius<box[0] or min(ax,px)-radius>box[2]
                            or max(ay,py)+radius<box[1] or min(ay,py)-radius>box[3]):
                        continue
                    hit=swept_hit(ax,ay,px,py,box,radius)
                    if hit is None:continue
                    total=fraction+(1.-fraction)*hit
                    hx,hy=x+(px-x)*total,y+(py-y)*total
                    normal=self._wall_normal(box,hx,hy,radius)
                    if landing is None or total<landing[0]:
                        landing=(total,terrain_id,box,normal)
            if landing is not None and (contact is None or landing[0]<contact[0]):
                contact=landing
            if contact is not None:
                fraction, terrain_id, box, normal = contact
                hx = x + (px - x) * fraction
                hy = y + (py - y) * fraction
                nx,ny=normal

                rest=max(-2*256,min(12*256,float(recipe.get('rest_mp',6*256))))
                if nx:hx=(box[0] if nx<0 else box[2])+nx*rest
                if ny:hy=(box[1] if ny<0 else box[3])+ny*rest
                when=t0+(t1-t0)*fraction
                clipped.append([when,hx-half_x,hy-half_y,facing])
                return clipped, dict(objid=int(terrain_id), box=box,
                                     x=int(hx - box[0]), y=int(hy - box[1]),
                                     hit=[int(hx), int(hy)], normal=normal)
            clipped.append(list(point))
        return clipped,None

    def _nearest_anchor(self, x, y):
        'Static surface a mid-air expiry hangs from (capture anchors all).'
        best = None
        for terrain_id, box, vid in self._terrain_boxes():
            dx = max(box[0] - x, 0.0, x - box[2])
            dy = max(box[1] - y, 0.0, y - box[3])
            distance = math.hypot(dx, dy)
            if best is None or distance < best[0]:
                best = (distance, terrain_id, box)
        if best is None:
            return None
        return dict(objid=int(best[1]), box=best[2],
                    x=int(x - best[2][0]), y=int(y - best[2][1]),
                    hit=[int(x), int(y)], normal=(0, -1))

    def _arm_contact(self, x, y, tx, ty, walls, radius):
        contact = None
        for terrain_id, box in walls:
            if (box[0] - radius <= x <= box[2] + radius
                    and box[1] - radius <= y <= box[3] + radius):
                continue
            hit = swept_hit(x, y, tx, ty, box, radius)
            if hit is not None and (contact is None or hit < contact[0]):
                hx = x + (tx - x) * hit
                hy = y + (ty - y) * hit
                contact = (hit, terrain_id, box,
                           self._wall_normal(box, hx, hy, radius))
        return contact

    def _inside_bounds(self, x, y):
        bounds = self.pack.play_bounds
        return (bounds[0] <= x <= bounds[2] and bounds[1] <= y <= bounds[3])

    def _claw_aim(self, claw):
        """Claw top-left target so its centre lands on the live player."""
        template = self.pack.template(claw)
        from ...participants import nearest
        x, y = self.motion.position(claw, self.room_time)
        target = nearest(self.combat_players(), (x+template['wmp']/2., y+template['hmp']/2.))
        if target is None:
            return None
        px, py = target.center()
        return (int(px - template['wmp'] / 2.0),
                int(py - template['hmp'] / 2.0))

    def _emit_claw_attack(self, event):
        'Native whip: runtime staged reach, re-aimed at the live player.'
        sx, sy = self._boss_center()
        reach = self.pack.arm_motion['player_attack']['max_observed_reach_mp']
        players = [p for p in self.combat_players() if math.dist(p.center(), (sx, sy)) <= reach]
        if not players:
            return
        eligible=[int(oid) for oid in self.pack.claw_ids
                  if self.claw_busy.get(int(oid),0)<=self.room_time]
        if not eligible:
            return

        preferred = int(event.get('claw') or 0)
        if preferred and preferred not in eligible:
            return
        from ...participants import nearest, identity
        choices = []
        for oid in ([preferred] if preferred in eligible else eligible):
            x,y = self.motion.position(oid,self.room_time)
            target = nearest(players,(x+12288.,y+12288.))
            choices.append((math.dist(target.center(),(x+12288.,y+12288.)),identity(target),oid,target))
        _,_,claw,target = min(choices,key=lambda c:c[:3])
        px,py = target.center()
        steps = list(event.get('steps') or [])
        if not steps or steps[-1].get('dx') is None:
            steps = [dict(delay_s=0.0, dt_ms=event.get('extend_ms') or 0,
                          dx=px - sx, dy=py - sy)]
        home=self.pack.template(claw)['move']
        root=self.motion.position(claw,self.room_time)
        rest_x,rest_y=root[0]-self.boss_pos[0],root[1]-self.boss_pos[1]
        half = 12288.0

        aim = math.atan2(py-root[1]-half,px-root[0]-half)
        first=next((row for row in steps if math.hypot(row.get('dx',0),row.get('dy',0))>256),steps[0])
        base=math.atan2(float(first.get('dy') or 0.),float(first.get('dx') or 0.))
        span=math.hypot(float(first.get('dx') or 0.),float(first.get('dy') or 0.)) or 1.
        want=math.hypot(px-root[0]-half,py-root[1]-half)
        scale=min(reach/span,want/span)
        turn = aim - base
        cos_turn, sin_turn = math.cos(turn), math.sin(turn)
        lead = float(event.get('anim_lead_s') or self.CLAW_TELEGRAPH_S)
        return_at = self.room_time + lead + float(event.get('return_s') or 0.0)
        self.claw_busy[claw]=return_at

        self.claw_steps = [row for row in self.claw_steps
                           if row['claw'] != claw]
        self.claw_facings = [row for row in self.claw_facings
                             if row[1] != claw]
        self.motion.cancel(claw)

        facing_pending = True
        for step in steps:
            when = self.room_time + lead + float(step.get('delay_s') or 0.0)
            if when >= return_at:
                continue
            dx = float(step.get('dx') or 0.0) * scale
            dy = float(step.get('dy') or 0.0) * scale
            row = {
                'at': when, 'claw': claw,
                'dt_ms': int(step.get('dt_ms') or 0),
                'dx': dx * cos_turn - dy * sin_turn,
                'dy': dx * sin_turn + dy * cos_turn,
                'rest': (rest_x, rest_y)}
            if facing_pending and (abs(row['dx']) > 256
                                   or abs(row['dy']) > 256):
                row['facing'] = int(round(math.degrees(
                    math.atan2(row['dy'], row['dx'])) % 360.0))
                facing_pending = False
            self.claw_steps.append(row)
        rest_facing = self.pack.template(claw).get('facing')
        if rest_facing is None:
            rest_facing = 0
        self.claw_facings.append((return_at, claw, int(rest_facing) % 360))
        self.batch.animprops(claw, CLAW_ANIM_SWING[0], CLAW_ANIM_SWING[1])
        self.motion.schedule(return_at, claw, home)
        tx, ty = self._claw_aim(claw)
        self._event('claw_attack', claw=claw, x=tx, y=ty, steps=len(steps))

    def _emit_anchored_adds(self, event):
        'Create the runtime anchored adds (one recipe per create).'
        for recipe in event.get('recipes') or []:
            props = dict(recipe.get('props') or {})
            move = recipe.get('move') or {}
            anchor = (move.get('relativeto') or {}).get('objid')
            objid = self._alloc()
            wire = self.pack.wire_props('lab.brambled')
            for key, value in props.items():
                if key in ('vid', 'ownertype', 'blocktype', 'removetype'):
                    continue
                wire[key + '_'] = value

            hp = int(event.get('hitpoints') or wire.get('hitpoints_', self.ADD_HITPOINTS))
            wire['hitpoints_'] = hp
            self.batch.create(objid, wire)
            self.batch.animprops(objid, ADD_ANIM_CREATE[0],
                                 ADD_ANIM_CREATE[1])
            if anchor is not None:
                self.batch.modify(objid, 'MOVE', message=(
                    'Properties_Move',
                    normalize_props('Properties_Move', move)))
            self.adds[objid] = {
                'hp': hp, 'max': hp,
                'expire': self.room_time + self.ADD_LIFETIME,
                'anchor': anchor,
            }
        self._event('adds', count=len(event.get('recipes') or []))

    def _emit_adds(self, event):
        self._emit_anchored_adds(event)

    def _wall_claw_idle(self, claw):
        claw=int(claw)
        if claw in self.claw_attacks.states or self.claw_busy.get(claw,0)>self.room_time:
            return False
        home=self.pack.template(claw).get('move') or {}
        root=(home.get('relativeto') or {}).get('objid')
        if root is None:return False
        move=(self.motion.tracks.get(claw) or {}).get('move',home)
        if (move.get('relativeto') or {}).get('objid')!=root:
            return False
        if float((move.get('to') or {}).get('dt') or 0)>0:
            return False
        rx,ry=self.motion.position(root,self.room_time)
        offset=home.get('to') or {}
        return math.dist(self.motion.position(claw,self.room_time),
                         (rx+offset.get('xmp',0),ry+offset.get('ymp',0)))<=5*256

    def _idle_wall_claws(self):
        clock=self.controller.wall_clock
        return [clock.claws[base] for base in clock.wall_bases.values()
                if base in clock.claws and self._wall_claw_idle(clock.claws[base])]

    def wall_pull_waiting_for(self, claw):
        if self.controller is None:return False
        clock=self.controller.wall_clock
        pull_claws={clock.claws[base] for base in clock.wall_bases.values() if base in clock.claws}
        return int(claw) in pull_claws and (clock.waiting_for_claws or bool(self.pending_wall_groups))

    def _queue_wall_pull(self, event):
        key=(event.get('phase_index'),event.get('wall_group',event.get('group_at')),event.get('loop',0))
        self.pending_wall_groups.setdefault(key,[]).append(event)

    def _step_pending_wall_groups(self):
        for key,rows in list(self.pending_wall_groups.items()):
            if self.controller is not None and key[0] != self.controller.machine.index:
                del self.pending_wall_groups[key]
                continue
            expected=len(rows[0].get('group_walls') or rows[0].get('walls') or [rows[0]['wall']])
            claws=[int(row['claw']) for row in rows]
            if len(rows)!=expected or not all(self._wall_claw_idle(claw) for claw in claws):
                continue
            del self.pending_wall_groups[key]
            for row in rows:self._emit_wall_pull(row)

    def _emit_wall_pull(self, event):
        walls = [int(objid) for objid in (event.get('walls') or [])]
        claws = [int(objid) for objid in (event.get('claws') or [])]
        if event.get('moves'):
            claw = int(event['claw'])
            wall = int(event['wall'])
            root = int((event.get('claw_home') or {}).get('relativeto', {})
                       .get('objid') or 0)
            home = self.pack.template(wall)
            wall_x,wall_y=self.motion.position(wall,self.room_time)
            nx, ny = wall_normal(home, self.pack.play_bounds)

            cx0, cy0 = self.motion.position(claw, self.room_time)
            cx0, cy0 = int(round(cx0)), int(round(cy0))
            if nx:
                face_x = wall_x + (home['wmp'] if nx > 0 else 0)
                attach_pose = [int(face_x), cy0]
            else:
                face_y = wall_y + (home['hmp'] if ny > 0 else 0)
                attach_pose = [cx0, int(face_y)]

            boss=self.pack.template(self.pack.boss_objid)
            edge_x=self.boss_pos[0]+(boss['wmp'] if nx<0 else 0)
            edge_y=self.boss_pos[1]+(boss['hmp'] if ny<0 else 0)
            face=(wall_x+(home['wmp'] if nx>0 else 0)) if nx else (wall_y+(home['hmp'] if ny>0 else 0))
            edge=edge_x if nx else edge_y
            initial_gap=(edge-face)*(nx or ny)
            measured=float(event.get('measured_gap_mp') or 280*256)
            near=(0 < initial_gap <= self.WALL_NEAR_RANGE_PX*256)
            minimum=(self.WALL_NEAR_GAP_PX if near
                     else self.WALL_MIN_GAP_PX)

            gap=max(minimum*256,
                    min(measured,self.WALL_FAR_GAP_CAP_PX*256))
            depth=(edge-face)*(nx or ny)-gap
            if nx:
                pull_pose = [attach_pose[0] + int(round(depth * nx)),
                             attach_pose[1]]
            else:
                pull_pose = [attach_pose[0],
                             attach_pose[1] + int(round(depth * ny))]
            drag_distance = abs(depth)
            cx, cy = self.motion.position(claw, self.room_time)
            reach_distance = math.hypot(attach_pose[0] - cx,
                                        attach_pose[1] - cy)
            if root:
                bx, by = self.motion.position(root, self.room_time)
            else:
                bx, by = self.motion.position(claw, self.room_time)
            rest_to = (event.get('claw_home') or {}).get('to') or {}
            rest_x = bx + int(rest_to.get('xmp') or 0)
            rest_y = by + int(rest_to.get('ymp') or 0)

            start = self.room_time + float(event.get('anim_lead_s') or self.CLAW_TELEGRAPH_S)
            reach_duration = max(self.WALL_MIN_MOVE_S,
                                 reach_distance / self.CLAW_REACH_MP_S)
            drag_duration = max(self.WALL_MIN_MOVE_S,
                                drag_distance / self.CLAW_DRAG_MP_S)
            link_at = start + reach_duration + self.WALL_LINK_DELAY_S
            drag_done = link_at + drag_duration
            retract_distance = math.hypot(rest_x - pull_pose[0],
                                          rest_y - pull_pose[1])
            retract_duration = max(self.WALL_MIN_MOVE_S,
                                   retract_distance / self.CLAW_SPED_MP_S)
            retract_done = drag_done + retract_duration
            group_key=(event.get('phase_index'),event.get('wall_group',event.get('group_at')),event.get('loop',0))
            group=self.wall_groups.get(group_key)
            if group is None:
                group=dict(start=self.room_time,
                           home_at=(0. if event.get('interactive') else
                                    self.room_time+float(event.get('group_duration_s') or 22.0)),members=[])
                self.wall_groups[group_key]=group
            previous_home=group['home_at']
            wall_home_at=max(previous_home,retract_done+
                (float(event.get('hold_after_hands_home_s',0)) if event.get('interactive') else 0.))
            if wall_home_at>previous_home:
                group['home_at']=wall_home_at
                for member in group['members']:
                    shift=wall_home_at-member['home_at']
                    self.motion.retime(member['wall'],member['home_at'],wall_home_at)
                    old=member['home_at']
                    self.pending_shakes=[(at+shift,magnitude,duration) if abs(at-old)<1e-8
                                         and magnitude==6 else (at,magnitude,duration)
                                         for at,magnitude,duration in self.pending_shakes]
                    member['home_at']=wall_home_at
                    member['home_done']+=shift
                    for recorded in reversed(self.events):
                        if recorded['kind']=='wall_motion' and recorded['wall']==member['wall']:
                            recorded.update(home_at=wall_home_at,home_done=member['home_done'])
                            break
            wall_home_duration = max(self.WALL_MIN_MOVE_S,
                                     math.hypot(wall_x+depth*nx-home['xmp'],wall_y+depth*ny-home['ymp']) / self.WALL_RETURN_SPED_MP_S)
            wall_home_done = wall_home_at + wall_home_duration
            self.claw_steps = [row for row in self.claw_steps
                               if row['claw'] != claw]
            self.claw_facings = [row for row in self.claw_facings if row[1] != claw]
            self.motion.cancel(claw)
            self.motion.cancel(wall)
            self.claw_busy[claw] = retract_done+0.05
            self.claw_attacks.reserve_wall(claw,retract_done)
            phase_index = getattr(getattr(self.controller, 'machine', None),
                                  'index', 0)

            body_release_at = drag_done if phase_index == 1 else wall_home_at
            self._wall_pull_busy_until = max(
                self._wall_pull_busy_until, body_release_at)

            self._wall_return_park_until = max(
                getattr(self, '_wall_return_park_until', 0.0), wall_home_done,
                *(member['home_done'] for member in group['members']))
            self._wall_hold_until = max(self._wall_hold_until, wall_home_done)
            self._wall_hold_phase = phase_index
            facing=int(round(math.degrees(math.atan2(attach_pose[1]-cy0,attach_pose[0]-cx0))))%360

            self.batch.animprops(claw, CLAW_ANIM_SWING[0], CLAW_ANIM_SWING[1])
            self.claw_facings.append((self.room_time, claw,
                                      int(facing) % 360))
            rest_facing = self.pack.template(claw).get('facing')
            if rest_facing is None:
                rest_facing = 0
            self.claw_facings.append((retract_done, claw,
                                      int(rest_facing) % 360))
            self.motion.schedule(start, claw, dict(
                to=dict(xmp=int(attach_pose[0]), ymp=int(attach_pose[1]),
                        dt=int(round(reach_duration * 1000.0)))))

            self.motion.schedule(link_at, wall, dict(
                to=dict(xmp=int(round(wall_x + depth * nx)),
                        ymp=int(round(wall_y + depth * ny)),
                        dt=int(round(drag_duration * 1000.0)))))
            self.motion.schedule(link_at, claw, dict(
                to=dict(xmp=int(pull_pose[0]), ymp=int(pull_pose[1]),
                        dt=int(round(drag_duration * 1000.0)))))
            self.motion.schedule(drag_done, claw, dict(
                relativeto=dict(objid=root),
                to=dict(xmp=int(rest_to.get('xmp') or 0),ymp=int(rest_to.get('ymp') or 0),
                        dt=int(round(retract_duration * 1000.0)))))

            self.motion.schedule(retract_done,claw,self.pack.template(claw)['move'])
            self.motion.schedule(wall_home_at, wall, dict(
                to=dict(xmp=int(home['xmp']), ymp=int(home['ymp']),
                        dt=int(round(wall_home_duration * 1000.0)))))

            self._queue_shake(link_at, 16, 500)
            self._queue_shake(link_at, 3,
                              int(round(drag_duration * 1000.0)))
            self._queue_shake(wall_home_at, 6, 1000)
            self.wall_hold_until = max(getattr(self, 'wall_hold_until', 0.0),
                                       wall_home_done)

            note = getattr(self.controller, 'note_wall_hold', None)
            if note is not None:
                note(wall_home_done-self._fight_room_start,
                     release_at=wall_home_at-self._fight_room_start)

            if self.controller is not None:
                live_plan=dict(event,attach_at=link_at-self._fight_room_start,
                               drag_at=link_at-self._fight_room_start,
                               freeze_at=drag_done-self._fight_room_start,
                               home_at=wall_home_at-self._fight_room_start)
                for mode in (self.controller.gas,self.controller.special_gas):
                    mode.note_wall_pull(live_plan,phase_index,self.controller.time)
                self.controller.rings.note_wall_pull(live_plan,phase_index)

            birth_rows=event.get('seekers') or []
            first_birth=min((float(r.get('delay_s') or 0.) for r in birth_rows),default=0.)
            batch_span=max((float(r.get('delay_s') or 0.)-first_birth for r in birth_rows),default=0.)

            birth_scale=min(1.,drag_duration/batch_span) if batch_span>0 else 1.
            for seeker in birth_rows:
                delay=float(seeker.get('delay_s') or 0.)-first_birth
                when=link_at+delay*birth_scale
                self.pending_seekers.append((max(link_at,when),dict(seeker,arm_base=root,arm_claw=claw,arm_segment=0)))
            group['members'].append(dict(wall=wall,claw=claw,reach_at=start,attach_at=link_at,freeze_at=drag_done,retract_at=retract_done,home_at=wall_home_at,home_done=wall_home_done,depth_px=depth/256,gap_px=gap/256))
            self._event('wall_motion',**group['members'][-1])
        else:

            if event.get('gas_trigger') and self.pack.wall_ids:
                walls = [int(self.rng.choice(self.pack.wall_ids))]
                self._gas_wall_choice = walls[0]
            for objid in walls:
                home = self.pack.template(objid)
                nx, ny = wall_normal(home, self.pack.play_bounds)
                depth = float(event.get('depth') or self.WALL_PULL_DEPTH)
                target = dict(xmp=int(home['xmp'] + nx*depth),
                              ymp=int(home['ymp'] + ny*depth), dt=self.WALL_MOVE_MS)
                self.motion.schedule(self.room_time, objid, dict(to=target))
                hold = self.room_time + float(event.get('hold_s')
                                              or self.WALL_HOLD_SECONDS)
                self.motion.schedule(hold, objid,
                                     dict(to=dict(xmp=home['xmp'], ymp=home['ymp'],
                                                  dt=self.WALL_MOVE_MS)))
                phase_index = getattr(getattr(self.controller, 'machine', None), 'index', 0)
                body_release_at = (self.room_time + self.WALL_MOVE_MS / 1000.0
                                   if phase_index == 1 else hold)
                self._wall_pull_busy_until = max(self._wall_pull_busy_until, body_release_at)
                self._wall_hold_until = max(self._wall_hold_until, hold)
            self._wall_hold_phase = phase_index
        self._event('wall_pull', walls=walls, claws=claws)

    def _step_motion(self, dt):
        now = self.room_time
        due_steps = [row for row in self.claw_steps
                     if row['at'] <= now + 1e-9 and now - row['at'] <= 0.25]
        self.claw_steps = [row for row in self.claw_steps if row['at'] > now]
        for row in due_steps:
            if row.get('x') is not None:
                tx, ty = int(row['x']), int(row['y'])
            elif row.get('root'):
                rx, ry = self.motion.position(row['root'], now)
                tx = rx + row.get('dx', 0.0)
                ty = ry + row.get('dy', 0.0)
                if row.get('wall') and row.get('normal'):
                    att = (row.get('attachment') or {}).get('to') or {}
                    wall_home = self.pack.template(row['wall'])
                    wx = tx + float(att.get('xmp') or 0.0)
                    wy = ty + float(att.get('ymp') or 0.0)
                    nx, ny = row['normal']

                    depth = ((wx - wall_home['xmp']) * nx
                             + (wy - wall_home['ymp']) * ny)
                    proj_x = wall_home['xmp'] + nx * depth
                    proj_y = wall_home['ymp'] + ny * depth
                    tx += proj_x - wx
                    ty += proj_y - wy
                    wx, wy = proj_x, proj_y
                    bcx, bcy = self._boss_center()
                    if nx:
                        face = wx + (wall_home['wmp'] if nx > 0 else 0)
                        gap = (bcx - face) if nx > 0 else (face - bcx)
                    else:
                        face = wy + (wall_home['hmp'] if ny > 0 else 0)
                        gap = (bcy - face) if ny > 0 else (face - bcy)
                    want = float(row.get('min_gap_mp') or 0.0)
                    if want > gap:
                        deficit = want - gap
                        tx -= nx * deficit
                        ty -= ny * deficit
            else:
                rest = row.get('rest') or (0.0, 0.0)
                tx = int(self.boss_pos[0] + rest[0] + row.get('dx', 0.0))
                ty = int(self.boss_pos[1] + rest[1] + row.get('dy', 0.0))
            cx, cy = self.motion.position(row['claw'], now)
            distance = math.hypot(tx - cx, ty - cy)

            dt_ms = max(1, int(round(distance / self.CLAW_SPED_MP_S * 1000.0)))
            self.motion.schedule(now, row['claw'],
                                 dict(to=dict(xmp=tx, ymp=ty, dt=dt_ms)))
            if row.get('facing') is not None:
                self.batch.modify(row['claw'], 'FACING',
                                  intval=int(row['facing']))
        due_facings = [row for row in self.claw_facings
                       if row[0] <= now + 1e-9 and now - row[0] <= 0.25]
        self.claw_facings = [row for row in self.claw_facings
                             if row[0] > now]
        for _at, claw, facing in due_facings:
            self.batch.modify(claw, 'FACING', intval=int(facing))
        for objid, move in self.motion.due(self.room_time):
            if move is None:
                self.batch.modify(objid, 'MOVE')
            else:
                self.batch.modify(objid, 'MOVE', message=(
                    'Properties_Move', normalize_props('Properties_Move', move)))
        keep=[]
        for when,event in self.pending_seekers:
            if when<=now+1e-9:
                self._spawn_seeker_wave(event)
            else:keep.append((when,event))
        self.pending_seekers=keep
        for objid in self.pack.wall_ids:
            self.wall_pos[int(objid)] = list(self.motion.position(objid, self.room_time))

    def _arm_point(self, arm, age):
        'Interpolated spike position along its clipped runtime path.'
        points = arm['points']
        last = points[-1][0]
        if age >= last:
            return points[-1][1], points[-1][2], points[-1][3]
        index = arm['cursor']
        while index>0 and points[index][0]>age:
            index-=1
        while index + 1 < len(points) and points[index + 1][0] <= age + 1e-9:
            index += 1
        arm['cursor'] = index
        when, x, y, facing = points[index]
        if index + 1 >= len(points):
            return x, y, facing
        nxt = points[index + 1]
        span = nxt[0] - when
        if span <= 0:
            return nxt[1], nxt[2], nxt[3]
        fraction = (age - when) / span
        return (x + (nxt[1] - x) * fraction,
                y + (nxt[2] - y) * fraction,
                nxt[3] if fraction >= 0.5 else facing)

    def _arm_moving_wall_contact(self,arm,age,aim):
        if aim<=age:return
        x,y,_=self._arm_point(arm,age)
        tx,ty,facing=self._arm_point(arm,aim)
        half_x,half_y=arm['half'];x+=half_x;y+=half_y;tx+=half_x;ty+=half_y
        radius=max(half_x,half_y);best=None
        for objid in self.pack.wall_ids:
            props=self.pack.template(objid)
            wx,wy=self.motion.position(objid,self.room_time)
            ex,ey=self.motion.projected_position(objid,self.room_time+aim-age)
            box=(wx,wy,wx+props['wmp'],wy+props['hmp'])

            hit=swept_hit(x,y,tx-(ex-wx),ty-(ey-wy),box,radius)
            if hit is None or (best is not None and hit>=best[0]):continue
            live_box=tuple(v+(ex-wx)*hit if i%2==0 else v+(ey-wy)*hit for i,v in enumerate(box))
            best=(hit,int(objid),live_box,wall_normal(props,self.pack.play_bounds))
        if best is None:return
        fraction,objid,box,normal=best
        hx,hy=x+(tx-x)*fraction,y+(ty-y)*fraction
        nx,ny=normal

        rest=max(-2*256,min(12*256,float(arm['recipe'].get('rest_mp',6*256))))
        if nx:hx=(box[0] if nx<0 else box[2])+nx*rest
        if ny:hy=(box[1] if ny<0 else box[3])+ny*rest
        when=age+(aim-age)*fraction
        arm['points']=[p for p in arm['points'] if p[0]<when]+[[when,hx-half_x,hy-half_y,facing]]
        arm['cursor']=min(arm['cursor'],len(arm['points'])-1)
        arm['anchor']=dict(objid=objid,box=box,x=int(hx-box[0]),y=int(hy-box[1]),hit=[int(hx),int(hy)],normal=normal)

    def _step_arms(self, dt):
        lead = self.ARM_MOVE_MS / 1000.0
        for objid, arm in list(self.arms.items()):
            age = self.room_time - arm['start']
            if age < 0:
                continue
            while arm.get('poses') and arm['poses'][0][0] <= age + 1e-9:
                _when, pose = arm['poses'].pop(0)
                self.batch.modify(objid, 'POSE', intval=int(pose))
            last = arm['points'][-1][0]
            if age + 1e-9 >= last:

                if not arm['landed']:
                    arm['landed'] = True
                    self._land_spike(objid, arm)
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='INTERNAL')
                del self.arms[objid]
                continue
            if self.room_time + 1e-9 >= arm['next_move']:
                aim = min(age + lead, last)
                self._arm_moving_wall_contact(arm,age,aim)
                last=arm['points'][-1][0]
                aim=min(aim,last)
                x, y, facing = self._arm_point(arm, aim)

                dt_ms = max(1, int(round((aim - age) * 1000.0)))
                self.batch.move_to(objid, int(round(x)), int(round(y)),
                                   dt=dt_ms)
                self.batch.modify(objid, 'FACING', intval=int(facing))
                arm['next_move'] = self.room_time + self.ARM_MOVE_STEP
            elif self.room_time >= arm['expire']:

                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='INTERNAL')
                del self.arms[objid]

    def _land_spike(self, arm_objid, arm):
        """A spike that reached a surface turns into its spike ball."""
        anchor = arm.get('anchor')
        if not anchor:
            return
        recipe = arm.get('recipe') or {}
        props = dict(recipe.get('props') or {})
        objid = self._alloc()
        wire = self.pack.wire_props('lab.brambled')
        wire['hitpoints_'] = int(props.get('hitpoints')
                                 or self.ADD_HITPOINTS)
        wire['lifetime_'] = int(props.get('lifetime') or 60000)
        for key in ('wmp', 'hmp', 'hitboxdamage'):
            if props.get(key) is not None:
                wire[key + '_'] = int(props[key])
        half_w = float(wire.get('wmp_') or 12800) / 2.0
        half_h = float(wire.get('hmp_') or 12800) / 2.0
        centre_x, centre_y = anchor['hit']
        left, top = int(round(centre_x - half_w)), int(round(centre_y - half_h))
        wire['xmp_'] = left
        wire['ymp_'] = top
        self.batch.create(objid, wire)
        self.batch.animprops(objid, ADD_ANIM_CREATE[0], ADD_ANIM_CREATE[1])
        self.batch.modify(objid, 'MOVE', message=(
            'Properties_Move', {
                'relativeto_': {'objid_': int(anchor['objid'])},
                'to_': {'xmp_': int(left - anchor['box'][0]),
                        'ymp_': int(top - anchor['box'][1])},
            }))
        self.adds[objid] = {
            'hp': wire['hitpoints_'], 'max': wire['hitpoints_'],
            'expire': self.room_time + self.ADD_LIFETIME,
            'anchor': int(anchor['objid']),
        }
        self.spike_balls += 1
        self._event('spike_ball', terrain=anchor['objid'],
                    x=anchor['hit'][0], y=anchor['hit'][1], source=arm_objid)
        self._event('adds', count=1, source='spike_ball',
                    terrain=anchor['objid'])

    def _step_adds(self, dt):

        for objid, add in list(self.adds.items()):
            if self.room_time >= add['expire']:
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='LIFETIME')
                del self.adds[objid]

    def _spawn_seeker_wave(self, event):
        props = dict(event.get('props') or {})

        origin = None
        segment = int(event.get('arm_segment') or 0)
        if segment and self.pack.template(segment):
            try:
                origin = self.motion.position(segment, self.room_time)
            except (KeyError, ValueError):
                origin = None
        claw = event.get('arm_claw',event.get('claw'))
        if origin is None and claw is not None and int(claw) in self.pack.claw_ids:
            tip=self.motion.position(int(claw),self.room_time)
            base=int(event.get('arm_base') or 0)
            if base:
                start=self.motion.position(base,self.room_time)
                fraction=float(event.get('arm_fraction',.5))
                origin=(start[0]+(tip[0]-start[0])*fraction,start[1]+(tip[1]-start[1])*fraction)
            else:origin=tip
        if origin is not None:
            props['xmp'] = int(round(origin[0]))
            props['ymp'] = int(round(origin[1]))
        objid = self._alloc()
        wire = self.pack.wire_props('lab.seeker')
        for key, value in props.items():
            if key in ('vid', 'ownertype', 'blocktype', 'removetype'):
                continue
            wire[key + '_'] = value
        wire['hitpoints_'] = int(props.get('hitpoints')
                                 or self.SEEKER_HITPOINTS)
        wire['damage_'] = int(props.get('damage') or self.SBULLET_DAMAGE)
        self.batch.create(objid, wire)
        self.batch.sound(objid, 'lab.seekerc')
        self.batch.animprops(objid,1,792)
        delay = float(event.get('first_shot_delay_s') or self.SEEKER_FIRE_DELAY)
        self.seekers[objid] = {
            'hp': wire['hitpoints_'], 'max': wire['hitpoints_'],
            'x': float(props.get('xmp') or 0),
            'y': float(props.get('ymp') or 0),
            'next_move': self.room_time,
            'next_shot': self.room_time + delay,
            'period': float(event.get('shot_period_s')
                            or self.SEEKER_FIRE_PERIOD),
            'mode':'approach','pause_until':0.,'last_shot':-999.,
            'wmp':float(wire.get('wmp_') or 12288),
            'hmp':float(wire.get('hmp_') or 12288),
        }
        self._event('seeker_spawn', objid=objid, x=props.get('xmp'),
                    y=props.get('ymp'),arm_claw=event.get('arm_claw'),
                    arm_base=event.get('arm_base'),arm_fraction=event.get('arm_fraction'))

    def _step_seekers(self, dt):
        bounds = self.pack.play_bounds
        margin = 1 * 1024
        for objid, seeker in list(self.seekers.items()):
            from ...participants import nearest
            target_player = nearest(self.combat_players(),
                (seeker['x']+seeker.get('wmp',12288)/2, seeker['y']+seeker.get('hmp',12288)/2))
            if target_player is None:
                continue
            px, py = target_player.center()
            dx = px - seeker['x']-seeker.get('wmp',12288)/2
            dy = py - seeker['y']-seeker.get('hmp',12288)/2
            dist = math.hypot(dx, dy)
            if dist < 1.0:
                dx, dy, dist = 1.0, 0.0, 1.0
            policy=self.pack.seeker_policy.get('fit') or {}
            recoil=self.pack.seeker_policy.get('fire_recoil') or {}
            recoil_enabled=bool(recoil.get('enabled'))

            while self.room_time + 1e-9 >= seeker['next_shot']:
                seeker['next_shot'] += seeker['period']
                seeker['last_shot']=self.room_time
                self.batch.animprops(objid,2,1782)
                self._spawn_seeker_bullet(objid, seeker, px, py)
                if recoil_enabled:
                    target=self._lagged_player_center(float(recoil.get('target_history_s',.5)), target_player) or (px,py)
                    rx=target[0]-seeker['x']-seeker.get('wmp',12288)/2
                    ry=target[1]-seeker['y']-seeker.get('hmp',12288)/2
                    component=float(recoil.get('component_speed_mp_s',20000))
                    velocity=(-component if rx>=0 else component,
                              -component if ry>=0 else component)
                    duration=float(recoil.get('duration_s',.56))
                    seeker.update(mode='fire_recoil',recoil_velocity=velocity,
                                  recoil_until=self.room_time+duration,pause_until=0.)
                    seeker['next_move']=min(seeker['next_move'],self.room_time)
                    self._event('seeker_recoil',objid=objid,velocity_mp_s=velocity,
                                duration_s=duration,x=seeker['x'],y=seeker['y'])
            vx=vy=0.
            if seeker.get('mode')=='fire_recoil' and self.room_time<seeker['recoil_until']:
                remaining=min(dt,seeker['recoil_until']-self.room_time)
                vx,vy=(value*remaining for value in seeker['recoil_velocity'])
            elif self.room_time>=seeker.get('pause_until',0):
                if seeker.get('mode')=='retreat':
                    remaining=seeker['retreat_left']
                    step=min(remaining,float(policy.get('retreat_speed_mp_s',83200))*dt)
                    vx,vy=seeker['retreat_dir'][0]*step,seeker['retreat_dir'][1]*step
                    seeker['retreat_left']-=step
                    if (seeker['retreat_left']<=1 or self.room_time>=seeker['retreat_end'] or dist>=float(policy.get('retreat_clear_mp',300*256))):
                        seeker['mode']='pause'
                        seeker['pause_until']=self.room_time+float(policy.get('pause_s',.16))
                        self._event('seeker_pause',objid=objid,duration_s=seeker['pause_until']-self.room_time)
                elif dist<=float(policy.get('retreat_trigger_mp',200*256))+1:
                    seeker.update(mode='retreat',retreat_dir=(-dx/dist,-dy/dist),retreat_left=float(policy.get('max_retreat_distance_mp',150*256)),retreat_end=self.room_time+float(policy.get('max_retreat_duration_s',.65)))
                    self._event('seeker_retreat',objid=objid,distance_px=dist/256)
                else:
                    seeker['mode']='approach'
                    speed=float(policy.get('approach_speed_mp_s',self.SEEKER_SPED))
                    if not recoil_enabled and (seeker['next_shot']-.10<=self.room_time<=seeker['next_shot'] or self.room_time-seeker.get('last_shot',-999)<float(policy.get('fire_slow_window_s',.55))):
                        speed=float(policy.get('slow_speed_mp_s',28000))

                    step=min(speed*dt,max(0.,dist-float(policy.get('retreat_trigger_mp',200*256))))
                    vx,vy=dx/dist*step,dy/dist*step
            seeker['x']=min(max(seeker['x']+vx,bounds[0]+margin),bounds[2]-seeker.get('wmp',12288)-margin)
            seeker['y']=min(max(seeker['y']+vy,bounds[1]+margin),bounds[3]-seeker.get('hmp',12288)-margin)
            self._push_seeker_out_of_walls(seeker)
            if self.room_time+1e-9>=seeker['next_move']:
                target=(int(seeker['x']),int(seeker['y']))
                if target!=seeker.get('sent_pose'):
                    self.batch.move_to(objid,*target,dt=self.SEEKER_MOVE_MS)
                    seeker['sent_pose']=target
                seeker['next_move']=self.room_time+self.SEEKER_MOVE_STEP

    def _push_seeker_out_of_walls(self, seeker):
        'Local battle simulator runtime.'
        width = float(seeker.get('wmp') or 12288)
        height = float(seeker.get('hmp') or 12288)
        for _terrain_id, box in self._terrain_groups()[1]:
            left, top = seeker['x'], seeker['y']
            right, bottom = left + width, top + height
            if (right <= box[0] or left >= box[2]
                    or bottom <= box[1] or top >= box[3]):
                continue
            depth, side = min((right - box[0], 'left'),
                              (box[2] - left, 'right'),
                              (bottom - box[1], 'top'),
                              (box[3] - top, 'bottom'))
            if side == 'left':
                seeker['x'] = box[0] - width
            elif side == 'right':
                seeker['x'] = box[2]
            elif side == 'top':
                seeker['y'] = box[1] - height
            else:
                seeker['y'] = box[3]

    def _spawn_seeker_bullet(self, seeker_objid, seeker, px, py):
        angle = math.degrees(math.atan2(py - seeker['y'],
                                        px - seeker['x']))
        objid = self._alloc()
        props = self.pack.wire_props('lab.sbullet')
        props['xmp_'] = int(seeker['x'])
        props['ymp_'] = int(seeker['y'])
        props['facing_'] = int(angle) % 360
        props['damage_'] = self.SBULLET_DAMAGE
        self.batch.create(objid, props, creatorid=seeker_objid)
        self.batch.sound(objid, 'lab.bullet')
        radians = math.radians(angle)
        speed = self.SBULLET_SPED
        self.bullets[objid] = {
            'x': seeker['x'], 'y': seeker['y'],
            'vx': math.cos(radians) * speed,
            'vy': math.sin(radians) * speed,
            'born': self.room_time, 'next_move': self.room_time,
            'lifetime': self.SBULLET_LIFETIME, 'spike': False,
        }
        self.seeker_shots += 1
        return objid

    def _damage_seeker(self, objid, value):
        seeker = self.seekers.get(objid)
        if seeker is None:
            return
        seeker['hp'] -= value
        if seeker['hp'] <= 0:
            self.batch.destroy(objid, ownerid=self.worldid,
                               reason='HITPOINTS')
            del self.seekers[objid]
            self.supplies.killed(seeker['x'] + seeker.get('wmp', 0) / 2.,
                                 seeker['y'] + seeker.get('hmp', 0) / 2.)
        else:
            self.batch.modify(objid,'HITPOINTS',intval=int(seeker['hp']))

    def _damage_add(self, objid, value):

        add = self.adds.get(objid)
        if add is None:
            return
        add['hp'] -= value
        if add['hp'] <= 0:
            self.batch.destroy(objid, ownerid=self.worldid, reason='HITPOINTS')
            del self.adds[objid]
        else:
            self.batch.modify(objid,'HITPOINTS',intval=int(add['hp']))

    def _begin_gas(self, event):
        self._anim(event.get('anim') or 'SHOOT3')
        cx, cy = self._boss_center()
        wall = getattr(self, '_gas_wall_choice', None) or event.get('wall')
        axis = event.get('axis') or 'horizontal'
        if wall in self.pack.wall_ids:
            nx, _ny = wall_normal(self.pack.template(wall),
                                  self.pack.play_bounds)
            axis = 'vertical' if nx else 'horizontal'
        self.gas = {
            'active': True,
            'axis': axis,
            'origin': [cx, cy],
            'front_speed': float(event.get('front_speed') or 102400),
            'spawn_interval': float(event.get('spawn_interval') or 0.2),
            'first_offset': float(event.get('first_offset_mp') or 56576),
            'cell_mp': float(event.get('cell_mp') or 40960),
            'gap_mp': float(event.get('gap_mp') or 3328),
            'lifetime_s': float(event.get('lifetime_s') or 1.0),
            'fronts': [],
        }
        self._event('gas_start', wall=event.get('wall'),
                    axis=self.gas['axis'], origin=self.gas['origin'])

    def _emit_gas_wave(self, event):
        if self.gas is None:
            self._begin_gas(event)

        origin=self._boss_center()
        self.gas['origin'] = origin
        wire=self.pack.line_gas
        trail=list(wire.get('trail') or [dict(delay_s=.204,offset_mp=56576)])
        for direction in (-1,1):
            core_offset=direction*float(wire.get('first_core_offset_mp') or 36096)
            cx=origin[0]+(core_offset if self.gas['axis']=='horizontal' else 0)
            cy=origin[1]+(core_offset if self.gas['axis']=='vertical' else 0)
            core=self._spawn_blaser(cx-20480,cy-20480,damage=None,lifetime=trail[0]['delay_s'],creator=self.pack.boss_objid)
            self.gas_cores[core]=self.room_time+trail[0]['delay_s']
            self.gas['fronts'].append(dict(origin=origin,dir=direction,born=self.room_time,index=0,trail=trail,sound=True))
        self._event('gas_wave', fronts=2, axis=self.gas['axis'],
                    origin=list(self.gas['origin']))

    def _stop_gas(self):
        if self.gas is not None:
            self.gas['active'] = False
            self.gas['fronts'] = []
        self._gas_wall_choice = None
        self._event('gas_stop')

    def _spawn_blaser(self, x, y, damage=30, lifetime=1.0,creator=0):
        objid = self._alloc()
        props = self.pack.wire_props('lab.blaser')
        props['xmp_'] = int(round(x))
        props['ymp_'] = int(round(y))
        props['wmp_'] = 40960
        props['hmp_'] = 40960
        props['ownertype_'] = 2
        if damage is None:
            props.pop('damage_', None)
            props.pop('lifetime_', None)
            props.pop('ownertype_',None)
        else:
            props['damage_'] = int(damage)
            props['lifetime_'] = int(max(1, lifetime * 1000))
        self.batch.create(objid, props,creatorid=creator)
        self.blasers[objid] = self.room_time + float(lifetime)
        return objid

    def _step_gas(self, dt):
        if self.gas is None or not self.gas.get('active'):
            return
        bounds = self.pack.play_bounds
        half = float(self.gas['cell_mp']) / 2.0
        axis = self.gas['axis']
        keep=[]
        for front in list(self.gas['fronts']):
            ox,oy=front['origin']
            trail=front['trail']
            done=False
            while True:
                i=front['index']
                if i<len(trail):entry=trail[i]
                else:

                    entry=dict(delay_s=trail[-1]['delay_s']+(i-len(trail)+1)*.225,offset_mp=trail[-1]['offset_mp']+(i-len(trail)+1)*20480)
                if front['born']+entry['delay_s']>self.room_time+1e-9:break
                offset=front['dir']*entry['offset_mp']
                if axis == 'horizontal':
                    x, y = ox + offset - half, oy - half
                else:
                    x, y = ox - half, oy + offset - half
                objid=self._spawn_blaser(x, y, damage=30,lifetime=self.gas['lifetime_s'])
                if front['sound']:
                    self.batch.sound(objid,'lab.blaser');front['sound']=False
                front['index']+=1
                if axis == 'horizontal':
                    outside = not (bounds[0] - half <= x <= bounds[2] + half)
                else:
                    outside = not (bounds[1] - half <= y <= bounds[3] + half)
                if outside:
                    done=True
                    break
            if not done:keep.append(front)
        self.gas['fronts']=keep

    def _spawn_special_gas_core(self, life_s):
        cx, cy = self._boss_center()
        self.special_gas_origin = (cx, cy)
        objid = self._spawn_blaser(cx - 20480, cy - 20480,
                                   damage=None, lifetime=life_s,creator=self.pack.boss_objid)
        self.gas_cores[objid] = self.room_time + float(life_s)

        self._event('special_gas_core', objid=objid, x=int(cx),
                    y=int(cy), life_s=float(life_s))
        return objid

    def _spawn_special_gas_ring(self, event):
        origin = self.special_gas_origin or self._boss_center()
        offsets = list(event.get('offsets') or [])
        for index, offset in enumerate(offsets):
            cx = origin[0] + float(offset[0])
            cy = origin[1] + float(offset[1])
            objid = self._spawn_blaser(cx - 20480, cy - 20480,
                                       damage=30, lifetime=1.0)
            if index == 0 and event.get('sound'):
                self.batch.sound(objid, event['sound'])
        self._event('special_gas_ring', ring=event.get('ring'),
                    count=len(offsets), sound=bool(event.get('sound')))

    def _step_blasers(self, dt):
        for objid, expire in list(self.blasers.items()):
            if self.room_time >= expire:
                reason = ('INTERNAL' if objid in self.gas_cores
                          else 'LIFETIME')
                self.batch.destroy(objid, ownerid=self.worldid, reason=reason)
                self.blasers.pop(objid, None)
                self.gas_cores.pop(objid, None)

    @staticmethod
    def _boss_path_point(points, age):
        """Linear position on the measured 50 ms path, with held endpoints."""
        if not points:
            return None
        if age <= points[0][0]:
            return float(points[0][1]), float(points[0][2])
        if age >= points[-1][0]:
            return float(points[-1][1]), float(points[-1][2])
        lo, hi = 0, len(points) - 1
        while lo + 1 < hi:
            mid = (lo + hi) // 2
            if points[mid][0] <= age:
                lo = mid
            else:
                hi = mid
        a, b = points[lo], points[hi]
        span = b[0] - a[0]
        fraction = 0.0 if span <= 0 else (age - a[0]) / span
        return (a[1] + (b[1] - a[1]) * fraction,
                a[2] + (b[2] - a[2]) * fraction)

    def _lagged_player_center(self, lag_s, player=None):
        """Player centre at a measured pursuit lag (latest fit: 0.75 s)."""
        player = player or self.player
        if player is None:
            return None
        from ...participants import life_identity
        history = self._player_histories.get(life_identity(player), self._player_history if player is self.player else ())
        if not history:
            return player.center()
        when = self.room_time - float(lag_s or 0.0)
        for sample in reversed(history):
            if sample[0] <= when + 1e-9:
                return sample[1], sample[2]
        return history[0][1], history[0][2]

    def _boss_motion_allowed(self, phase, age):
        'Pursuit resumes when the actual hand action releases the body.'
        return phase>0

    def _clamp_boss_to_walls(self):
        """Keep the whole body inside fixed walls and live crushwall faces.

        The room bounds are the crushwall home faces, outside the fixed
        lab.wall frame. In the long recording the body's top-left reaches
        exactly (-24576, 8192)..(655360, 393216): the fixed inner frame minus
        its 65536-square body. Ledges do not constrain this flying boss.

        Both ends of the next native MoveTo leg must be inside the frame;
        include scheduled wall legs in its 50 ms interpolation window.
        """
        bounds = list(self.pack.play_bounds)
        for wall in self.pack.object_ids('lab.wall'):
            props = self.pack.template(wall)
            nx, ny = wall_normal(props, self.pack.play_bounds)
            if nx > 0:
                bounds[0] = max(bounds[0], props['xmp'] + props['wmp'])
            elif nx < 0:
                bounds[2] = min(bounds[2], props['xmp'])
            elif ny > 0:
                bounds[1] = max(bounds[1], props['ymp'] + props['hmp'])
            else:
                bounds[3] = min(bounds[3], props['ymp'])
        until = self.room_time + self.BOSS_MOVE_MS / 1000.0
        for wall in self.pack.wall_ids:
            props = self.pack.template(wall)
            nx, ny = wall_normal(props, self.pack.play_bounds)
            times = {self.room_time, until}

            for at, _seq, objid, move, _hold in self.motion.queue:
                if objid != wall:
                    continue
                end = at + float((move.get('to') or {}).get('dt') or 0) / 1000.0
                times.update(t for t in (at, end) if self.room_time <= t <= until)
            for when in times:
                x, y = self.motion.projected_position(wall, when)
                if nx > 0:
                    bounds[0] = max(bounds[0], x + props['wmp'])
                elif nx < 0:
                    bounds[2] = min(bounds[2], x)
                elif ny > 0:
                    bounds[1] = max(bounds[1], y + props['hmp'])
                else:
                    bounds[3] = min(bounds[3], y)
        body = self.pack.template(self.pack.boss_objid)
        changed = False
        for axis, lower, upper, size in (
                (0, bounds[0], bounds[2], body['wmp']),
                (1, bounds[1], bounds[3], body['hmp'])):

            lower, upper = math.ceil(lower), math.floor(upper - size)
            clipped = max(lower, min(upper, self.boss_pos[axis]))
            if clipped != self.boss_pos[axis]:
                self.boss_vel[axis] = 0.0
                changed = True
            self.boss_pos[axis] = clipped
        return changed

    def _step_boss(self, dt):
        phase = self.controller.machine.index
        if phase <= 0:
            self.boss_vel = [0.0, 0.0]
            return
        if self.player is None:
            return

        rows = self.pack.boss_movement.get('phases') or []
        row = rows[phase] if 0 <= phase < len(rows) else {}
        speed = float(row.get('speed_mp_s') or self.boss_speed_mp_s(phase))
        age = self.controller.machine.time_in_phase

        blocked = self.room_time < getattr(self, '_wall_pull_busy_until', 0.0)
        if blocked:

            self.boss_vel[:]=[0.,0.]
            corrected = self._clamp_boss_to_walls()
            if corrected or not getattr(self,'_boss_pull_stopped',False):
                self.batch.move_to(self.pack.boss_objid,int(self.boss_pos[0]),int(self.boss_pos[1]),dt=self.BOSS_MOVE_MS)
                self._boss_pull_stopped=True
            return
        resuming=getattr(self,'_boss_pull_stopped',False)
        self._boss_pull_stopped=False
        allowed = (not blocked) and self._boss_motion_allowed(phase, age)
        target_speed = speed * 1.10 if allowed else 0.0
        pursuit=(getattr(self.pack,'attack_policy',{}) or {}).get('pursuit',{})
        fits=pursuit.get('phases') or []
        fit=fits[phase-1] if pursuit.get('enabled') and 0<phase<=len(fits) else None
        lag=float(fit['target_history_s']) if fit else self.BOSS_PURSUIT_LAG_S
        target = self._lagged_player_center(lag)
        if target is None:
            return
        template = self.pack.template(self.pack.boss_objid)
        half_w = (template.get('wmp') or 0) / 2.0
        half_h = (template.get('hmp') or 0) / 2.0
        cx = self.boss_pos[0] + half_w
        cy = self.boss_pos[1] + half_h
        dx, dy = target[0] - cx, target[1] - cy
        distance = math.hypot(dx, dy) or 1.0
        ux, uy = dx / distance, dy / distance
        if resuming and allowed:

            live = self.player.center()
            lx, ly = live[0] - cx, live[1] - cy
            length = math.hypot(lx, ly)
            if length > 1.0:
                ux, uy = lx / length, ly / length
            self.boss_vel[:]=[ux*target_speed,uy*target_speed]
            self._boss_move_due=self.room_time
        if fit:
            spring=float(fit['spring_s2']);drag=float(fit['drag_s1'])
            self.boss_vel[0]+=(spring*dx-drag*self.boss_vel[0])*dt
            self.boss_vel[1]+=(spring*dy-drag*self.boss_vel[1])*dt

            live_cap=float(fit.get('live_cap_mp_s') or 0.0)
            if live_cap:
                speed_limit=live_cap
            else:
                speed_limit=min(float(fit['speed_limit_mp_s']), speed * 1.20)
        else:
            alpha = 1.0 - math.exp(-dt / self.BOSS_INERTIA_TAU)
            self.boss_vel[0] += (ux * target_speed - self.boss_vel[0]) * alpha
            self.boss_vel[1] += (uy * target_speed - self.boss_vel[1]) * alpha
            speed_limit = max(target_speed, speed) * 1.20
        current = math.hypot(self.boss_vel[0], self.boss_vel[1])
        if current > speed_limit and current > 0:
            self.boss_vel[0] *= speed_limit / current
            self.boss_vel[1] *= speed_limit / current
        step_x = self.boss_vel[0] * dt
        step_y = self.boss_vel[1] * dt
        displacement = math.hypot(step_x, step_y)
        budget = speed_limit * dt
        if displacement > budget and displacement > 0:
            step_x *= budget / displacement
            step_y *= budget / displacement
        self.boss_pos[0] += step_x
        self.boss_pos[1] += step_y
        self._clamp_boss_to_walls()
        if self.room_time+1e-9 >= getattr(self, '_boss_move_due', 0.0):
            self.batch.move_to(self.pack.boss_objid,
                               int(self.boss_pos[0]), int(self.boss_pos[1]),
                               dt=int(self.BOSS_MOVE_MS))
            self._boss_move_due = self.room_time + self.BOSS_MOVE_STEP

    def _begin_death(self):
        if self.phase in ('death', 'done'):
            return
        self.phase = 'death'
        self.phase_time = 0.0
        self.death_time = 0.0
        self.death_bursts = 0
        self.corpse_done = False
        self.loot_done = False

        self._anim('INTERACT2')
        self.batch.modify(self.pack.boss_objid, 'DAMAGE', intval=0)

        self.batch.shake(12)
        self.motion.queue.clear()
        self.pending_seekers=[]
        self.claw_steps=[]
        self.claw_facings=[]
        self.boss_vel[:]=[0.,0.]
        self.pending_shakes=[]
        for objid in self.pack.wall_ids:
            props = self.pack.template(objid)
            x,y=self.motion.position(objid,self.room_time)
            duration=max(1,int(round(math.hypot(props['xmp']-x,props['ymp']-y)/self.WALL_RETURN_SPED_MP_S*1000)))
            self.motion.schedule(self.room_time, objid, dict(to=dict(
                xmp=props['xmp'], ymp=props['ymp'], dt=duration)))
        for objid in self.pack.claw_ids:
            x,y=self.motion.position(objid,self.room_time)
            home=self.pack.template(objid)['move']
            root=(home.get('relativeto') or {})['objid']
            rx,ry=self.motion.position(root,self.room_time)
            target=home.get('to') or {}
            duration=max(1,int(round(math.hypot(rx+target.get('xmp',0)-x,ry+target.get('ymp',0)-y)/self.CLAW_SPED_MP_S*1000)))
            self.motion.schedule(self.room_time, objid,
                                 dict(home,to=dict(target,dt=duration)))
            self.motion.schedule(self.room_time+duration/1000.,objid,home)
        for objid in list(self.bullets):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
            del self.bullets[objid]
        for objid in list(self.adds):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
            del self.adds[objid]
        for objid in list(self.seekers):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
            del self.seekers[objid]
        for objid in list(self.arms):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
            del self.arms[objid]
        for objid in list(self.blasers):
            self.batch.destroy(objid, ownerid=self.worldid, reason='CANCEL')
            del self.blasers[objid]
        self.gas_cores.clear()
        self.special_gas_origin = None
        self.gas = None
        self.gas_fronts = []
        self._event('death')

    def _spawn_burst(self):
        "One death blast, placed like the capture's 45 lab.bossx objects."
        objid = self._alloc()
        props = self.pack.wire_props('lab.bossx')
        angle = self.rng.random() * math.pi * 2
        distance = (15.0 + math.sqrt(self.rng.random()) * 155.0) * 256.0
        cx = self.boss_pos[0] + 128 * 256
        cy = self.boss_pos[1] + 128 * 256
        props['xmp_'] = int(cx + math.cos(angle) * distance)
        props['ymp_'] = int(cy + math.sin(angle) * distance)
        self.batch.create(objid, props)
        self.batch.sound(objid, 'lab.bossx')
        self.death_fx[objid] = self.room_time + float(props.get('lifetime_', 540)) / 1000.0

    def _step_death_fx(self):
        'Shared effects retire at their runtime lifetime, also after done.'
        for objid, expires in list(self.death_fx.items()):
            if self.room_time + 1e-9 >= expires:
                self.batch.destroy(objid, ownerid=self.worldid, reason='LIFETIME')
                del self.death_fx[objid]

    def death_offsets(self):
        """Measured burst schedule (seconds from the first burst)."""
        doc = self.pack.death_sequence or {}
        return [float(value) for value in doc.get('burst_offsets_s') or []]

    def death_corpse_delay(self):
        doc = self.pack.death_sequence or {}
        value = doc.get('corpse_delay_s')
        return float(value) if value is not None else self.DEATH_CORPSE_DELAY

    def death_loot_delay(self):
        doc = self.pack.death_sequence or {}
        value = doc.get('loot_delay_s')
        return float(value) if value is not None else self.DEATH_LOOT_DELAY

    def _step_death(self, dt):
        'Local battle simulator runtime.'
        self.death_time += dt
        doc = self.pack.death_sequence or {}
        explosions = bool(doc.get('explosions_enabled', True))
        corpse = bool(doc.get('corpse_enabled', True))
        loot = bool(doc.get('loot_enabled', True))
        end_delay = doc.get('end_delay_s')
        if explosions:
            schedule = self.death_offsets()
            if schedule:

                while (self.death_bursts < len(schedule)
                       and self.death_time >= schedule[self.death_bursts]):
                    self._spawn_burst()
                    self.death_bursts += 1
            else:
                cadence = self.DEATH_BURST_SECONDS / float(self.DEATH_BURSTS)
                while (self.death_bursts < self.DEATH_BURSTS
                       and self.death_time >= self.death_bursts * cadence):
                    self._spawn_burst()
                    self.death_bursts += 1
        else:
            self.death_bursts = 0
        if not self.boss_destroyed:
            if corpse and not self.corpse_done and self.death_time >= self.death_corpse_delay():
                self.corpse_done = True
                self._destroy_boss_parts()
                self.batch.destroy(self.pack.boss_objid, ownerid=self.worldid,
                                   reason='INTERNAL')
                objid = self._alloc()
                props = self.pack.wire_props('lab.bosscorpse')

                props['xmp_'] = int(self.boss_pos[0]) + 22570
                props['ymp_'] = int(self.boss_pos[1]) + 25023
                self.batch.create(objid, props)
                self.boss_destroyed = True
            elif not corpse:

                destroy_at = (float(end_delay) if end_delay is not None
                              else self.death_corpse_delay())
                if self.death_time >= destroy_at:
                    self._destroy_boss_parts()
                    self.batch.destroy(self.pack.boss_objid,
                                       ownerid=self.worldid,
                                       reason='HITPOINTS')
                    self.boss_destroyed = True
                    self.corpse_done = True
        if loot:
            if not self.loot_done and self.death_time >= self.death_loot_delay():
                self.loot_done = True
                self._queue_loot()
        else:
            self.loot_done = True
        end_at = (float(end_delay) if end_delay is not None and not corpse
                  else self.DEATH_END)
        if self.boss_destroyed and self.death_time >= end_at:
            self.phase = 'done'
            self._event('done')

    def _destroy_boss_parts(self):
        'Local battle simulator runtime.'
        if getattr(self, '_boss_parts_removed', False):
            return
        self._boss_parts_removed = True
        parts = list(self.pack.claw_ids) + list(
            self.pack.object_ids('lab.barmsegment'))
        for objid in parts:
            self.batch.destroy(int(objid), ownerid=self.worldid,
                               reason='INTERNAL')

    def _queue_loot(self):
        known = getattr(self.server, 'item_types', None)
        table = ((206, 'boss-reward'), (18, 'health'), (21, 'ammo'))
        placed = 0
        for item_type, _label in table:
            if known and item_type not in known:
                continue
            objid = self._alloc()
            x = self.boss_pos[0] + (placed - 1) * 40 * 1024
            y = self.boss_pos[1] + 24 * 1024
            self.pending_loot.append({
                'props_': {'xmp_': int(x), 'ymp_': int(y)},
                'itemprops_': [{'type_': int(item_type)}],
            })
            placed += 1
        self._event('loot', count=placed)

    def _send_tick(self, dt):
        if self.pending_loot:
            loot = self.layouts.encode(
                'Delta', {'createloot_': self.pending_loot.pop(0)})
            self.server._send_raw(self.channel, MT_SERVER_DELTA, loot,
                                  'lab-loot', record=False)
        self._wire_time_ms += dt * 1000.0
        target_ms = int(round(self._wire_time_ms))
        self.batch.dt = target_ms - self._sent_time_ms
        self._sent_time_ms = target_ms
        payload = self.batch.encode()
        try:
            self.server._send_raw(self.channel, MT_SERVER_DELTA, payload,
                                  'lab-live', record=False)
            self.frames += 1
        except Exception as error:
            self.log('lab_hard send failed: ' + str(error))
            self._stop.set()
        self.batch.general = []
        self.batch.objects = []
        self.batch.extra = {}
        self.batch.dt = 0
        self.supplies.flush()

    def status(self):
        info = {'map': 'lab_hard', 'phase': self.phase,
                'time': round(self.room_time, 3),
                'bullets': len(self.bullets), 'adds': len(self.adds),
                'arms': len(self.arms), 'boss_damage': self.total_boss_damage,
                'spike_balls': self.spike_balls,
                'seekers': len(self.seekers),
                'seeker_shots': self.seeker_shots,
                'blasers': len(self.blasers),
                'gas_active': bool(self.gas and self.gas.get('active')),
                'frames': self.frames}
        if self.controller is not None:
            info['boss'] = self.controller.status()
        if self.player is not None:
            info['player'] = {'x': self.player.xmp, 'y': self.player.ymp,
                              'hp': self.player.hp}
        return info
