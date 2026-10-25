'lab_mid (ETL LabHard5) live session.'

import math
import random
import threading
import time

from ... import pb_layout
from ...live.bridge import PlayerBridge, PlayerState
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from ..etl_motion import NativeMotion, Terrain
from .controller import LabMidController
from .scene import LabMidPack

MT_SERVER_DELTA = 12

BOSS_ANIM = {'IDLE': (0, 528), 'SHOOT': (1, 528), 'DESTROY': (60, 726)}
BOSS_SHOOT_ANIM = (1, 2112)

ZOMBIE_ANIM = (0, 726)
ZOMBIE_POSES = {'walk': 5, 'air': 3, 'fall': 1}
ZOMBIE_STEP_HZ = 20.0

ZOMBIE_AGGRO_MP = 307200.0

ZOMBIE_AGGRO_VERTICAL_MP = 256000.0
ZOMBIE_HOP_MIN_ABOVE_MP = 25600
ZOMBIE_HOP_MAX_DX_MP = 307200
ZOMBIE_HOP_BELOW_MIN_DX_MP = 204800.0
ZOMBIE_CONTACT_RANGE = 40 * 256

ZOMBIE_RAM_VGAP_MP = 64 * 256

ZOMBIE_DROP_NUDGE_MP = 4.0

ZOMBIE_PACE_MP = 280 * 256

ZOMBIE_LIFETIME = 1800.0
ZOMBIE_STAND_POSE = 4
ZOMBIE_STAND_S = 0.3

ZOMBIE_FALL_STAND_S = 0.08

ZOMBIE_LAND_WALK_S = {'climb': 1.749, 'flat': 2.948, 'drop': 2.181,
                      'fall': 3.082}
ZOMBIE_LAND_STEP_MP = 20 * 256

ZOMBIE_HOP_LAUNCH_MP_S = 198000.0
ZOMBIE_HOP_GRAVITY_MP_S2 = 352000.0
SEED_STEP_S = 1.0 / 60.0
SEED_DEATH_JITTER_MP = 8192
SEED_HMP = 8192
ZOMBIE_WMP = 16384
ZOMBIE_HMP = 24576
EXIT_TARGET_MP = (634880, 184320)
EXIT_SPEED_MP_S = 74143.0
DEATH_BURSTS_PER_WAVE = 4
DEATH_WAVE_PERIOD_S = 0.5

class LabMidLiveSession(object):
    """60 Hz host for the ETL mid-boss controller."""

    HZ = 60.0
    BULLET_MOVE_STEP = 0.05
    BOSS_MOVE_STEP = 0.1
    DEATH_END_EXTRA = 2.0

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
        self.pack = LabMidPack()
        self.bridge = bridge or PlayerBridge(
            int(getattr(options, 'bridge_port', 21550) or 21550),
            log=self.log)
        self.owns_bridge = bridge is None
        live_seed = getattr(options, 'live_seed', None)
        seed = random.SystemRandom().getrandbits(64) if live_seed is None else int(live_seed)
        self.seed = seed

        self.rng = random.Random(seed)
        self.loot_rng = random.Random(seed ^ 0x4D4944)

        self.phase = 'waiting'
        self.phase_time = 0.0
        self.room_time = 0.0
        self.player = None
        self.player_type = 1
        self.controller = None

        self.boss_pos = [0, 0]
        self.boss_template = {}
        self.boss_motion = NativeMotion((0, 0))
        self._boss_phase = 'patrol'
        self._patrol_move = None
        self._patrol_ends = 0.0
        self._patrol_next_at = None
        self._patrol_hover = 12.9
        self._dance_state = None
        self._dance_due = 0.0
        self._dance_pending = None
        self._dance_mode = None
        self._dance_hold = 0.0
        self._boss_phase2 = False
        self._pair_index = 0
        self._dense_index = 0
        self.terrain = Terrain({})
        self.blocks = {}
        self.seeds = {}
        self.zombies = {}
        self.bullets = {}
        self.pending_loot = []
        self.supplies = SupplyDrops(self, 'lab_mid', rng=self.loot_rng)
        self.supplies.pending = self.pending_loot
        self.motion_commands = []
        self.death_time = 0.0
        self.death_bursts = 0
        self.corpse_done = False
        self.loot_done = False
        self.door_open = False
        self.total_block_damage = 0.0
        self.total_boss_damage = 0.0
        self.events = []
        self._next_objid = 9000
        self._stop = threading.Event()
        self._thread = None
        self._wire_time_ms = 0.0
        self._sent_time_ms = 0
        self._boss_move_due = 0.0
        self.frames = 0

    def prepare(self):
        self.batch.general = []
        self.batch.objects = []
        self.batch.extra = {}
        self.phase = 'waiting'
        self.phase_time = 0.0
        self.room_time = 0.0
        self.player = None
        self.controller = None
        self.boss_template = self.pack.template(self.pack.boss_objid)
        self.boss_pos = [self.boss_template.get('xmp') or 0,
                         self.boss_template.get('ymp') or 0]
        self.boss_motion = NativeMotion(self.boss_pos)
        self.terrain = Terrain(self.pack.templates)
        self._boss_phase = 'patrol'
        self._patrol_move = None
        self._patrol_ends = 0.0
        self._patrol_next_at = None
        self._patrol_hover = 12.9
        self._dance_state = None
        self._dance_due = 0.0
        self._dance_pending = None
        self._dance_mode = None
        self._dance_hold = 0.0
        self._boss_phase2 = False
        self._pair_index = 0
        self._dense_index = 0
        self.blocks = {}
        for row in self.pack.scene['blocks']:
            self.blocks[int(row['objid'])] = dict(row)
        self.seeds.clear()
        self.zombies.clear()
        self.bullets.clear()
        self.motion_commands = []
        self.pending_loot = []
        self.supplies = SupplyDrops(self, 'lab_mid', rng=self.loot_rng)
        self.supplies.pending = self.pending_loot
        self.death_time = 0.0
        self.death_bursts = 0
        self.corpse_done = False
        self.loot_done = False
        self.door_open = False
        self.total_block_damage = 0.0
        self.total_boss_damage = 0.0
        self._boss_move_due = 0.0
        self._demo_timer = 0.0
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
        self.log('lab_mid live session started (phase=waiting)')

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
                self.log('lab_mid tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            wait = deadline - time.time()
            if wait > 0:
                time.sleep(wait)
            else:
                deadline = time.time()

    def step(self, dt):
        self._sync_player()
        self.room_time += dt
        self.phase_time += dt
        if self.phase == 'waiting':
            if self.player is not None:
                self._begin_fight()
        if self.phase == 'fighting':
            self._demo_damage(dt)
            for event in self.controller.step(dt):
                self._handle_event(event)
                if self.phase != 'fighting':
                    break
            if self.phase == 'fighting':
                self._step_boss_script(dt)
                self._step_seeds(dt)
                self._step_zombies(dt)
                self._step_bullets(dt)
                self._step_motion(dt)
        elif self.phase == 'death':
            self._step_seeds(dt)
            self._step_zombies(dt)
            self._step_bullets(dt)
            self._step_motion(dt)
            self._step_death(dt)
        elif self.phase == 'done':

            self._step_seeds(dt)
            self._step_zombies(dt)
            self._step_bullets(dt)
        self._send_tick(dt)

    def _demo_damage(self, dt):
        """Headless / watch-only damage feed (``--live-demo-damage``)."""
        if not getattr(self.options, 'live_demo_damage', False):
            return
        self._demo_timer += dt
        if self._demo_timer < 0.3:
            return
        self._demo_timer = 0.0
        if self.controller is None:
            return
        if self.controller.vulnerable:

            self._damage_boss(700)
            return

        for objid in self.pack.block_ids:
            row = self.controller.blocks[objid]
            if row['alive'] and not row['pending']:
                self.controller.add_damage(700, objid)
                self.total_block_damage += 700
                break

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0:return None
        expiry=None
        if objid==self.pack.boss_objid or objid in self.blocks:
            role='npc'
        elif objid in self.zombies:
            role='npc';expiry=self.zombies[objid].get('expire')
        elif objid in self.bullets:
            role='projectile'
        elif objid in self.seeds:
            role='fx'
        else:return None
        if type(expiry)not in (int,float) or not math.isfinite(expiry) or expiry<0:expiry=None
        return dict(ownerid=self.worldid,objid=objid,role=role,expires_at=expiry,
            room_time=self.room_time,lifetime_due=expiry is not None and self.room_time>=expiry)

    def _retire_native_bullet(self,event):
        if not getattr(self.bridge,'shared_room',False) or event.get('ownerid')!=self.worldid:return False
        objid=event.get('victim')
        if objid not in self.bullets or event.get('reason')!=3 or event.get('authority')!='native-shield-contact/1':return False
        del self.bullets[objid]
        self.batch.destroy(objid,ownerid=self.worldid,reason='SHIELD')
        return True

    def _sync_player(self):
        if self.bridge is None:
            return
        state, _keys = self.bridge.snapshot()
        if state is not None:
            self.player = state
        for event in self.bridge.poll_events():
            if not isinstance(event, dict):
                continue
            if 'ownerid' in event and int(event['ownerid'] or 0) != self.worldid:
                continue
            if event.get('kind') == 'destroy':
                self._retire_native_bullet(event)
                victim = int(event.get('victim') or 0)
                if is_hp_death(event.get('reason')) and victim in self.zombies:
                    self._damage_zombie(victim, self.zombies[victim]['hp'])
                continue
            if event.get('kind') != 'damage':
                continue
            victim = int(event.get('victim') or 0)
            value = int(event.get('value') or 0)
            if value <= 0:
                continue
            if victim in self.blocks:
                if self.phase == 'fighting' and self.controller is not None:
                    self.controller.add_damage(value, victim)
                    self.total_block_damage += value
            elif victim == self.pack.boss_objid:
                if self.phase == 'fighting':
                    self._damage_boss(value)
            elif victim in self.zombies:
                self._damage_zombie(victim, value)

    def _begin_fight(self):
        if self.phase != 'waiting':
            return
        self.phase = 'fighting'
        self.phase_time = 0.0
        self.controller = LabMidController(self.pack, rng=self.rng)
        self._boss_phase2 = False
        patrol = ((self.pack.mechanics.get('boss_motion') or {})
                  .get('patrol') or {})
        self._patrol_next_at = (self.room_time
                                + float(patrol.get('first_patrol_s')
                                        or 20.5))
        for event in list(self.controller.events):
            self._handle_event(event)
        self.log('lab_mid fight started')

    def _handle_event(self, event):
        kind = event.get('kind')
        if kind == 'seed_spawn':
            self._emit_seeds(event)
        elif kind == 'block_destroy':
            self._destroy_block(event)
        elif kind == 'volley':
            self._emit_volley(event)
        elif kind == 'boss_segment':
            anim = (self.controller.boss_phase2_anim
                    if self._boss_phase2 else list(BOSS_SHOOT_ANIM))
            self.batch.animprops(self.pack.boss_objid, int(anim[0]),
                                 int(anim[1]))
            self._event('boss_segment', alive=event.get('alive'),
                        pattern=event.get('pattern'),
                        resume_s=event.get('resume_s'),
                        anim=[int(anim[0]), int(anim[1])])
        elif kind == 'boss_phase':
            self._boss_phase2 = True
            anim = list(event.get('anim') or [2, 4224])
            self.batch.animprops(self.pack.boss_objid, int(anim[0]),
                                 int(anim[1]))
            self._event('boss_phase', phase=event.get('phase'), anim=anim)
        elif kind == 'boss_vulnerable':
            self.batch.modify(self.pack.boss_objid, 'INVULNERABLE',
                              boolval=False)
            self._event('boss_vulnerable')
        elif kind == 'death':
            self._begin_death(event)

    def _damage_boss(self, value):
        'Apply player damage to the vulnerable body and mirror the'
        if self.controller is None:
            return
        self.controller.add_damage(value, self.pack.boss_objid)
        if self.phase == 'fighting':
            self.total_boss_damage += value
            self.batch.modify(self.pack.boss_objid, 'HITPOINTS',
                              intval=int(round(self.controller.boss_hp)))

    def _sound(self, key, default):
        table = self.pack.mechanics.get('sounds') or {}
        return str(table.get(key) or default)

    def _event(self, kind, **values):
        row = dict(values)
        row['kind'] = kind
        row['at'] = round(self.room_time, 3)
        self.events.append(row)
        return row

    def _alloc(self):
        self._next_objid += 1
        while str(self._next_objid) in self.pack.templates:
            self._next_objid += 1
        return self._next_objid

    def _boss_center(self):
        return (self.boss_pos[0] + int((self.boss_template.get('wmp') or 0) / 2),
                self.boss_pos[1] + int((self.boss_template.get('hmp') or 0) / 2))

    def _aim_angle(self, x, y):
        if self.player is None:
            return 90.0
        px, py = self.player.center()
        return math.degrees(math.atan2(py - y, px - x))

    def _boss_rule(self):
        return self.pack.mechanics.get('boss_motion') or {}

    def _boss_speed(self):
        rule = self._boss_rule().get('patrol') or {}
        dead = len(self.pack.block_ids)
        if self.controller is not None:
            dead -= self.controller.alive_count
        level = max(0, dead - 1)
        level = min(level, int(rule.get('speed_level_max') or 7))
        base = float(rule.get('speed_base_mp_s') or 54690)
        step = float(rule.get('speed_step_mp_s') or 5860)
        return base + level * step

    @staticmethod
    def _leg_dt(x0, y0, x1, y1, speed):
        if speed <= 0:
            return 1
        return max(1, int(round(
            math.hypot(x1 - x0, y1 - y0) / speed * 1000.0)))

    def _next_patrol_move(self):
        rule = self._boss_rule().get('patrol') or {}
        bands = rule.get('bands_mp') or [[-122880, 0, 122880]]
        ymin, ymid, ymax = [int(v) for v in self.rng.choice(bands)]
        left = int(rule.get('x_left_mp') or 81920)
        mid = int(rule.get('x_mid_mp') or 311296)
        right = int(rule.get('x_right_mp') or 540672)
        speed = self._boss_speed()
        x0, y0 = int(self.boss_pos[0]), int(self.boss_pos[1])
        points = [{'dt': self._leg_dt(x0, y0, mid, ymin, speed),
                   'xmp': mid, 'ymp': ymin}]

        if self.rng.random() < 0.5:
            tour = [(right, ymin), (right, ymid), (right, ymax),
                    (mid, ymax), (left, ymax), (left, ymid), (left, ymin),
                    (mid, ymin)]
        else:
            tour = [(left, ymin), (left, ymid), (left, ymax),
                    (mid, ymax), (right, ymax), (right, ymid), (right, ymin),
                    (mid, ymin)]
        px, py = mid, ymin
        for tx, ty in tour:
            points.append({'dt': self._leg_dt(px, py, tx, ty, speed),
                           'xmp': tx, 'ymp': ty})
            px, py = tx, ty
        rests = rule.get('rest_points_mp') or [[303104, -73728]]
        rest = self.rng.choice(rests)
        points.append({'dt': self._leg_dt(px, py, int(rest[0]), int(rest[1]),
                                          speed),
                       'xmp': int(rest[0]), 'ymp': int(rest[1])})
        move = {'waypoint': {'points': points}}
        duration = sum(point['dt'] for point in points) / 1000.0
        gaps = rule.get('hover_gaps_s') or [12.9]
        return move, duration, float(self.rng.choice(gaps))

    def _next_dance_radius(self):
        rule = self._boss_rule().get('dance') or {}

        sample = self.rng.choice(rule.get('orbit_samples') or
                                 [{'radiusamp': 103168, 'period': 5251}])
        return int(sample['radiusamp']), int(sample['period'])

    def _body_damage_fraction(self):
        if self.controller is None:
            return 0.0
        return max(0.0, self.controller.boss_damage /
                   max(1.0, self.controller.boss_body_hp))

    def _body_movement_mode(self):
        rule = self._boss_rule().get('dance') or {}
        fraction = self._body_damage_fraction()
        if fraction >= float(rule.get('orbit_damage_fraction', 0.7)):
            return 'orbit'
        if fraction >= float(rule.get('rapid_damage_fraction', 0.5)):
            return 'rush'
        return 'wander'

    def _dance_begin_move(self):
        mode = self._body_movement_mode()
        if mode != self._dance_mode:
            self._dance_mode = mode
            self._event('boss_body_mode', mode=mode,
                        damage=self.controller.boss_damage if self.controller else 0)
        if mode == 'orbit':
            self._dance_dash()
        else:
            self._dance_wander(mode)

    def _dance_wander(self, mode):
        rule = self._boss_rule().get('dance') or {}
        left, top, right, bottom = rule.get('wander_bounds_mp') or [81920, -122880, 540672, 122880]
        grid = max(1, int(rule.get('wander_grid_mp', 256)))
        target = (self.rng.randrange(int(left) // grid, int(right) // grid + 1) * grid,
                  self.rng.randrange(int(top) // grid, int(bottom) // grid + 1) * grid)
        speed = float(rule.get('dash_speed_mp_s') or 105470)
        if self._body_damage_fraction() < float(rule.get('speed_damage_fraction', 0.25)):
            speed = float(rule.get('first_dash_speed_mp_s') or 95730)
        x0, y0 = self.boss_motion.position(self.room_time)
        duration = self._leg_dt(x0, y0, target[0], target[1], speed)
        hold_key = 'short_hold_s' if mode == 'rush' else 'long_hold_s'
        self._dance_hold = float(self.rng.choice(rule.get(hold_key) or [1.4 if mode == 'rush' else 4.6]))
        self._dance_pending = None
        self._apply_boss_move({'to': {'xmp': target[0], 'ymp': target[1], 'dt': duration}})
        self._dance_state = 'wander_dash'
        self._dance_due = self.room_time + duration / 1000.0
        self._event('boss_body_target', mode=mode, target=list(target),
                    speed_mp_s=speed, hold_s=self._dance_hold)

    def _enter_dance(self):
        self._boss_phase = 'post'
        self._dance_state = 'idle'
        rule = self._boss_rule().get('dance') or {}
        self._dance_due = self.room_time + float(rule.get('entry_delay_s', 1.08))
        self._dance_pending = None
        self._dance_mode = None
        self._dance_hold = 0.0
        self._event('boss_dance', state='enter')

    def _dance_dash(self):
        rule = self._boss_rule().get('dance') or {}
        center = rule.get('center_mp') or [319488, 73728]
        radius, period = self._next_dance_radius()
        target = (int(center[0]), int(center[1]) - radius)
        speed = float(rule.get('dash_speed_mp_s') or 105470)
        x0, y0 = self.boss_motion.position(self.room_time)
        dt = self._leg_dt(x0, y0, target[0], target[1], speed)
        self._apply_boss_move({'to': {'xmp': target[0], 'ymp': target[1],
                                      'dt': dt}})
        self._dance_state = 'dash'
        self._dance_due = self.room_time + dt / 1000.0
        self._dance_pending = (radius, period)

    def _dance_ellipse(self):
        rule = self._boss_rule().get('dance') or {}
        center = rule.get('center_mp') or [319488, 73728]
        radius, period = self._dance_pending or self._next_dance_radius()
        self._apply_boss_move({'elliptical': {
            'xmp': int(center[0]), 'ymp': int(center[1]),
            'radiusamp': int(radius),
            'angle': float(rule.get('angle_deg') or 270),
            'period': int(period)}})
        self._dance_state = 'ellipse'
        self._dance_due = self.room_time + max(0.05, period / 1000.0)
        self._dance_pending = None

    def _apply_boss_move(self, move):
        self.batch.modify(self.pack.boss_objid, 'MOVE',
                          message=('Properties_Move', move))
        self.boss_motion.set(move, self.room_time)
        kind = ('waypoint' if 'waypoint' in move
                else ('elliptical' if 'elliptical' in move else 'to'))
        self._event('boss_move', form=kind,
                    duration=self._move_duration(move))

    def _move_duration(self, move):
        if not isinstance(move, dict):
            return 0.0
        if 'waypoint' in move:
            return sum(p.get('dt', 0)
                       for p in move['waypoint']['points']) / 1000.0
        if 'elliptical' in move:
            return move['elliptical'].get('period', 0) / 1000.0
        return (move.get('to') or {}).get('dt', 0) / 1000.0

    def _step_boss_script(self, dt):
        now = self.room_time
        if self._boss_phase == 'patrol':
            if self._patrol_next_at is None:
                self._patrol_next_at = now
            if self._patrol_move is not None:
                if now + 1e-9 >= self._patrol_ends:

                    self._patrol_move = None
                    self._patrol_next_at = (now
                                            + float(self._patrol_hover or 12.9))
            elif (self.controller is not None
                    and self.controller.vulnerable):

                self._enter_dance()
            elif now + 1e-9 >= self._patrol_next_at:
                move, duration, hover = self._next_patrol_move()
                self._apply_boss_move(move)
                self._patrol_move = move
                self._patrol_ends = now + duration
                self._patrol_hover = hover
        if self._boss_phase == 'post':
            guard = 0
            while guard < 4:
                guard += 1
                if now + 1e-9 < self._dance_due:
                    break
                if self._dance_state in (None, 'idle'):
                    self._dance_begin_move()
                    continue
                if self._dance_state == 'wander_dash':
                    self._dance_state = 'wander_hold'
                    self._dance_due += self._dance_hold
                    continue
                if self._dance_state == 'wander_hold':
                    self._dance_begin_move()
                    continue
                if self._dance_state == 'dash':
                    rule = self._boss_rule().get('dance') or {}
                    self._dance_state = 'orbit_start'
                    self._dance_due += float(self.rng.choice(rule.get('orbit_start_gaps_s') or [0.1]))
                    continue
                if self._dance_state == 'orbit_start':
                    self._dance_ellipse()
                    continue
                if self._dance_state == 'ellipse':
                    rule = self._boss_rule().get('dance') or {}
                    self._dance_state = 'orbit_end'
                    self._dance_due += float(self.rng.choice(rule.get('orbit_end_gaps_s') or [0.05]))
                    continue
                if self._dance_state == 'orbit_end':
                    self._dance_begin_move()
                    continue
                break
        position = self.boss_motion.position(now)
        self.boss_pos = [int(position[0]), int(position[1])]

    def _destroy_block(self, event):
        objid = int(event.get('objid'))
        if objid not in self.blocks:
            return
        self.batch.destroy(objid, ownerid=self.worldid, reason='HITPOINTS')
        self.blocks[objid]['alive'] = False
        self._event('block_destroy', objid=objid)

    def _seed_model(self):
        return self.pack.motion.get('seeds') or {}

    def _emit_seeds(self, event):
        """Launch ballistic ``lab.zombieseed`` objects.

        The create props carry the measured ``move.vel {xvel,yvel,yaccel}``
        so the native client integrates the parabola itself; the session
        mirrors the same curve with the fitted constants to know when/where
        the seed lands and the zombie appears.
        """
        count = int(event.get('count') or 0)
        block = self.blocks.get(int(event.get('objid') or 0)) or {}

        bx = int(block.get('xmp') or 0)
        by = int(block.get('ymp') or 0)
        if block.get('relative_to') is not None:
            bx = int(self.boss_pos[0] + int(block.get('offset_x') or 0))
            by = int(self.boss_pos[1] + int(block.get('offset_y') or 0))
        half_w = int((block.get('wmp') or 65536) / 2)
        half_h = int((block.get('hmp') or 65536) / 2)
        jitter = int((self.pack.mechanics.get('seeds') or {})
                     .get('spawn_jitter_mp') or SEED_DEATH_JITTER_MP)
        cx = bx + half_w
        cy = by + half_h
        model = self._seed_model()
        physics = model.get('mirror_physics') or {}
        kx = float(physics.get('kx') or 3.93)
        ky = float(physics.get('ky') or 4.66)
        death = event.get('source') == 'death'
        for _index in range(count):
            if death:

                x = cx
                y = cy
            else:

                x = cx + self.rng.randint(-jitter, jitter)
                y = cy + self.rng.randint(-jitter, jitter)
            xvel = self.rng.randint(-7500, 7500)
            yvel = self.rng.randint(-25000, -20000)
            objid = self._alloc()
            props = self.pack.wire_props('lab.zombieseed')
            props['xmp_'] = int(x)
            props['ymp_'] = int(y)
            props['move_'] = {'vel_': {'xvel_': xvel, 'yvel_': yvel,
                                       'yaccel_': 20}}
            self.batch.create(objid, props)
            self.seeds[objid] = {
                'x': float(x), 'y': float(y),
                'vx': kx * xvel, 'vy': ky * yvel,
                'age': 0.0, 'death': death,
                'source': event.get('source') or 'damage',
            }
        self._event('seed_spawn', count=count, source=event.get('source'),
                    block=event.get('objid'), x=bx, y=by)

    def _seed_hit(self, seed, old_y, new_y):
        """True when the falling seed crosses a platform top this step."""
        w, h = 16384, 8192
        old_feet = old_y + h
        new_feet = new_y + h
        for _objid, (sx, sy, sw, _sh), _kind in self.terrain.surfaces:
            if seed['x'] + w <= sx or seed['x'] >= sx + sw:
                continue
            if min(old_feet, new_feet) <= sy <= max(old_feet, new_feet):
                return sy - h
        return None

    def _step_seeds(self, dt):
        gravity = float((self._seed_model().get('mirror_physics') or {})
                        .get('gravity_mp_s2') or 78664.0)
        for objid, seed in list(self.seeds.items()):
            old_y = seed['y']
            seed['age'] += dt
            seed['vy'] += gravity * dt
            seed['x'] += seed['vx'] * dt
            seed['y'] += seed['vy'] * dt
            landing = None
            if seed['vy'] > 0:
                landing = self._seed_hit(seed, old_y, seed['y'])
            if seed['age'] >= 6.0 or landing is not None:
                if landing is not None:

                    chance = float((self.pack.mechanics.get('sounds') or {})
                                   .get('zspawn_chance') or 0.0)
                    if self.rng.random() < chance:
                        self.batch.sound(objid, self._sound('zombie_spawn',
                                                            'lab.zspawn'))
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='BLOCKED')
                del self.seeds[objid]
                if landing is not None:

                    surface = landing + SEED_HMP
                    self._spawn_zombie(seed['x'], surface - ZOMBIE_HMP)

    def _zombie_rules(self):
        motion = getattr(self.pack, 'zombie_motion', None) or {}
        return motion.get('rules') or {}

    def _zombie_ai(self):
        rule = self.pack.mechanics.get('zombie') or {}
        return {
            'aggro': float(rule.get('aggro_range_mp') or ZOMBIE_AGGRO_MP),
            'aggro_y': float(rule.get('aggro_vertical_range_mp')
                             or ZOMBIE_AGGRO_VERTICAL_MP),
            'hop_above': float(rule.get('hop_min_above_mp')
                               or ZOMBIE_HOP_MIN_ABOVE_MP),
            'hop_dx': float(rule.get('hop_max_dx_mp')
                            or ZOMBIE_HOP_MAX_DX_MP),
            'hop_below_dx': float(rule.get('hop_min_dx_below_mp')
                                  or ZOMBIE_HOP_BELOW_MIN_DX_MP),
            'land_walk': dict(ZOMBIE_LAND_WALK_S, **(
                rule.get('land_walk_s') or {})),
        }

    def _zombie_may_hop(self, zombie, ai, adx, py):
        '[D] may this zombie pogo right now?'
        if adx > ai['hop_dx']:
            return False
        if zombie['y'] + zombie['h'] * .5 - py >= ai['hop_above']:
            return True
        return adx >= ai['hop_below_dx']

    def _zombie_over_ledge(self, zombie):
        """[D] true when the tile under the feet is a one-way ledge."""
        objid = self.terrain.support(zombie['x'], zombie['y'],
                                     zombie['w'], zombie['h'])
        if objid is None:
            return False
        for oid, _row, kind in self.terrain.surfaces:
            if oid == objid:
                return kind == 2
        return False

    def _zombie_drop_through(self, objid, zombie):
        """[D] 4 -> 1: fall straight through the ledge the zombie stands on.

        ``nudge`` pushes the feet just past the surface so the one-way floor
        test in ``terrain.step`` lets the body through; the fall itself is the
        normal ballistic integration with POSE 1 (straight down: measured
        median drift ~19 px).
        """
        zombie['grounded'] = False
        zombie['vy'] = 0.0
        zombie['vx'] = 0.0
        zombie['air_kind'] = 'fall'
        zombie['walk_time'] = 0.0
        zombie['y'] += ZOMBIE_DROP_NUDGE_MP
        self._zombie_pose(objid, zombie, ZOMBIE_POSES['fall'])

    def _zombie_in_aggro(self, zombie, ai, px, py):
        dx = px - (zombie['x'] + zombie['w'] * .5)
        dy = py - (zombie['y'] + zombie['h'] * .5)
        return (dx / ai['aggro']) ** 2 + (dy / ai['aggro_y']) ** 2 <= 1.0

    def _zombie_pace(self, objid, zombie, dx, walk):
        '[D] a locked zombie never stands still.'
        if abs(dx) > ZOMBIE_PACE_MP:
            facing = 1 if dx > 0 else -1
        else:
            facing = zombie.get('facing') or 1
        self._zombie_facing(objid, zombie, facing)
        return walk * facing

    def _zombie_patrol(self, objid, zombie, walk):
        """Outside aggro: walk one direction, turn at the platform end."""
        facing = zombie.get('facing') or 1
        vx = walk * facing
        ahead = zombie['x'] + (zombie['w'] if vx >= 0 else -zombie['w'])
        if self.terrain.support(ahead, zombie['y'], zombie['w'],
                                zombie['h']) is None:
            facing = -facing
            self._zombie_facing(objid, zombie, facing)
            vx = walk * facing
        return vx

    def _zombie_pose(self, objid, zombie, pose):
        if zombie.get('pose') == pose:
            return
        zombie['pose'] = pose
        self.batch.modify(objid, 'POSE', intval=int(pose))

    def _zombie_facing(self, objid, zombie, facing):
        if zombie.get('facing') == facing:
            return
        zombie['facing'] = facing
        self.batch.modify(objid, 'FACING', intval=0 if facing > 0 else 180)

    def _spawn_zombie(self, x, y):
        objid = self._alloc()
        props = self.pack.wire_props('lab.pogozombie')
        props['xmp_'] = int(x)
        props['ymp_'] = int(y)
        props['hitpoints_'] = int((self.pack.motion.get('zombies') or {})
                                  .get('props', {}).get('hitpoints') or 400)
        self.batch.create(objid, props)

        rules = self._zombie_rules()
        anim = rules.get('animprops') or {}
        self.batch.animprops(objid, int(anim.get('animid') or ZOMBIE_ANIM[0]),
                             int(anim.get('time') or ZOMBIE_ANIM[1]))
        self.zombies[objid] = {
            'hp': 400, 'max': 400,
            'x': float(x), 'y': float(y),
            'w': ZOMBIE_WMP, 'h': ZOMBIE_HMP,
            'vy': 0.0, 'vx': 0.0, 'grounded': True,
            'air_kind': None, 'hop_from_y': float(y),
            'walk_time': 0.0, 'pose': ZOMBIE_STAND_POSE, 'facing': 1,
            'stand_left': ZOMBIE_STAND_S,
            'born': self.room_time,
            'expire': (self.room_time + float(
                ((self.pack.motion.get('zombies') or {})
                 .get('lifetime_s') or ZOMBIE_LIFETIME))),
            'next_move': 0.0,
        }

        self.batch.modify(objid, 'POSE', intval=ZOMBIE_STAND_POSE)
        self._event('zombie', x=int(x), y=int(y))
        return objid

    def _zombie_hop(self, objid, zombie, vx):
        'runtime pogo launch: ~198k mp/s up under ~352k mp/s^2.'
        rules = self._zombie_rules()
        launch = float(rules.get('hop_launch_mp_s')
                       or ZOMBIE_HOP_LAUNCH_MP_S)
        hop_v = float(rules.get('hop_horizontal_mp_s') or 0.0)
        if vx and hop_v:
            vx = hop_v if vx > 0 else -hop_v
        zombie['vy'] = -launch
        zombie['vx'] = float(vx)
        zombie['grounded'] = False
        zombie['walk_time'] = 0.0
        zombie['air_kind'] = 'hop'
        zombie['hop_from_y'] = zombie['y']
        self._zombie_pose(objid, zombie, ZOMBIE_POSES['air'])

    def _step_zombies(self, dt):
        rules = self._zombie_rules()
        model = self.pack.motion.get('zombies') or {}
        gravity = float(rules.get('hop_gravity_mp_s2')
                        or model.get('gravity_mp_s2')
                        or ZOMBIE_HOP_GRAVITY_MP_S2)
        walk = float(rules.get('walk_speed_mp_s')
                     or model.get('walk_speed_mp_s') or 29411.0)
        walk_duration = float(rules.get('walk_duration_s') or 1.7)
        for objid, zombie in list(self.zombies.items()):
            if self.room_time >= zombie['expire']:
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='LIFETIME')
                del self.zombies[objid]
                continue
            was_airborne = not zombie['grounded']
            vx = 0.0
            chasing = False
            ai = self._zombie_ai()
            px = py = None
            if zombie.get('stand_left', 0.0) > 0:
                zombie['stand_left'] -= dt
                if zombie['stand_left'] > 0:
                    body = {'x': zombie['x'], 'y': zombie['y'],
                            'w': zombie['w'], 'h': zombie['h'],
                            'vy': zombie['vy'],
                            'grounded': zombie['grounded']}
                    self.terrain.step(body, dt, 0.0, gravity)
                    zombie['x'] = body['x']
                    zombie['y'] = body['y']
                    zombie['vy'] = body['vy']
                    zombie['grounded'] = body['grounded']
                    continue
                self._zombie_pose(objid, zombie, ZOMBIE_POSES['walk'])
            if zombie['grounded']:
                zombie['walk_time'] = zombie.get('walk_time', 0.0) + dt
                chasing = False
                if self.player is not None:
                    px, py = self.player.center()
                if px is None:
                    vx = self._zombie_patrol(objid, zombie, walk)
                else:
                    dx = px - (zombie['x'] + zombie['w'] * .5)
                    adx = abs(dx)
                    chasing = self._zombie_in_aggro(zombie, ai, px, py)
                    if not chasing:

                        vx = self._zombie_patrol(objid, zombie, walk)
                    else:
                        gap_y = (zombie['y'] + zombie['h'] * 0.5) - py
                        may_hop = self._zombie_may_hop(zombie, ai, adx, py)
                        below = gap_y < -ZOMBIE_RAM_VGAP_MP
                        if (below and not may_hop
                                and self.room_time >= zombie.get(
                                    'hop_cool', 0.0)
                                and self._zombie_over_ledge(zombie)):

                            self._zombie_drop_through(objid, zombie)
                        else:

                            vx = self._zombie_pace(objid, zombie, dx, walk)

                        if (may_hop and self.room_time >= zombie.get(
                                'hop_cool', 0.0)):

                            toward = ((1 if dx > 0 else -1)
                                      if adx > ZOMBIE_CONTACT_RANGE
                                      else zombie.get('facing', 1))
                            self._zombie_hop(objid, zombie, walk * toward)
                            zombie['hop_cool'] = (self.room_time
                                                  + walk_duration + 0.15)
            else:
                vx = zombie.get('vx', 0.0)
            body = {'x': zombie['x'], 'y': zombie['y'],
                    'w': zombie['w'], 'h': zombie['h'],
                    'vy': zombie['vy'], 'grounded': zombie['grounded']}
            self.terrain.step(body, dt, vx, gravity)
            zombie['x'] = body['x']
            zombie['y'] = body['y']
            zombie['vy'] = body['vy']
            zombie['grounded'] = body['grounded']
            if body.get('blocked') and zombie['grounded']:

                if (chasing and px is not None
                        and self._zombie_may_hop(zombie, ai,
                                                 abs(px - zombie['x']
                                                     - zombie['w'] * .5), py)
                        and self.room_time >= zombie.get(
                            'hop_cool', 0.0)):
                    self._zombie_hop(objid, zombie, 0.0)
                    zombie['hop_cool'] = self.room_time + walk_duration + 0.15
                else:
                    facing = -(zombie.get('facing') or 1)
                    self._zombie_facing(objid, zombie, facing)
                    vx = walk * facing
                    body = {'x': zombie['x'], 'y': zombie['y'],
                            'w': zombie['w'], 'h': zombie['h'],
                            'vy': zombie['vy'],
                            'grounded': zombie['grounded']}
                    self.terrain.step(body, dt, vx, gravity)
                    zombie['x'] = body['x']
                    zombie['y'] = body['y']
                    zombie['vy'] = body['vy']
                    zombie['grounded'] = body['grounded']
            if not zombie['grounded']:
                if zombie.get('air_kind') is None:

                    zombie['air_kind'] = ('hop' if zombie['vy'] < 0
                                          else 'fall')
                    if zombie['air_kind'] == 'fall':
                        self._zombie_pose(objid, zombie, ZOMBIE_POSES['fall'])
            if zombie['grounded'] and was_airborne:

                air_kind = zombie.pop('air_kind', None)
                zombie['walk_time'] = 0.0
                zombie['vx'] = 0.0
                if air_kind == 'fall':
                    zombie['stand_left'] = ZOMBIE_FALL_STAND_S
                    zombie['hop_cool'] = (self.room_time
                                          + ai['land_walk']['fall'])
                    self._zombie_pose(objid, zombie, ZOMBIE_STAND_POSE)
                else:
                    dy = zombie['y'] - zombie.get('hop_from_y', zombie['y'])
                    if dy < -ZOMBIE_LAND_STEP_MP:
                        step = 'climb'
                    elif dy > ZOMBIE_LAND_STEP_MP:
                        step = 'drop'
                    else:
                        step = 'flat'
                    zombie['hop_cool'] = (self.room_time
                                          + ai['land_walk'][step])
                    self._zombie_pose(objid, zombie, ZOMBIE_POSES['walk'])

                self.batch.sound(objid, self._sound('zombie_land', 'lab.pogo'))
            if self.room_time >= zombie['next_move']:

                self.batch.move_to(objid, int(zombie['x']), int(zombie['y']),
                                   dt=int(round(1000.0 / ZOMBIE_STEP_HZ)))
                zombie['next_move'] = self.room_time + 1.0 / ZOMBIE_STEP_HZ

    def _damage_zombie(self, objid, value):
        zombie = self.zombies.get(objid)
        if zombie is None:
            return
        zombie['hp'] -= value
        if zombie['hp'] <= 0:
            self.batch.destroy(objid, ownerid=self.worldid, reason='HITPOINTS')
            del self.zombies[objid]
            self._drop_zombie_supply(zombie)
        else:
            self.batch.modify(objid, 'HITPOINTS', intval=zombie['hp'])

    def _zombie_drop_entries(self):
        """This map's health/ammo/EC settings; no extra hard-coded EC roll."""
        return [dict(item_type=item, chance=chance)
                for item, chance in self.supplies.outcomes()]

    def _drop_zombie_supply(self, zombie):
        """One exclusive roll per HP death using the selected map's three rates."""
        self.supplies.rng = self.loot_rng
        x, y = int(zombie['x'] + zombie['w'] * .5), int(zombie['y'] + zombie['h'] * .5)
        item_type = self.supplies.killed(x, y)
        if item_type is not None:
            self._event('zombie_supply', item_type=item_type, x=x, y=y)

    def _bullet_model(self):
        return self.pack.motion.get('bullets') or {}

    def _emit_volley(self, event):
        'Fire the runtime fixed-direction pattern.'
        model = self._bullet_model()
        count = int(event.get('count') or 2)
        cx, cy = self._boss_center()
        if count >= 5:
            patterns = (model.get('five_shot') or {}).get('angles') or []
            if patterns:
                angles = list(patterns[self._dense_index % len(patterns)])
            else:
                angles = [(i * 72.0) for i in range(5)]
            self._dense_index += 1
            origins = [(cx, cy)] * len(angles)
        else:
            patterns = (model.get('two_shot') or {}).get('angles') or []
            if patterns:
                angles = list(patterns[self._pair_index % len(patterns)])
            else:
                angles = [0.0, 180.0]
            self._pair_index += 1
            mounts = model.get('mounts') or {}
            right = mounts.get('right_mp') or [0, 0]
            left = mounts.get('left_mp') or [0, 0]
            origins = [(cx + right[0], cy + right[1]),
                       (cx + left[0], cy + left[1])]
            while len(origins) < len(angles):
                origins.append((cx, cy))
        for (ox, oy), angle in zip(origins, angles):
            self._spawn_bullet(ox, oy, angle)
        self._event('volley', count=len(angles), pattern=event.get('pattern'))

    def _spawn_bullet(self, x, y, angle):
        objid = self._alloc()
        props = self.pack.wire_props('lab.sbullet')
        props['xmp_'] = int(x)
        props['ymp_'] = int(y)
        props['facing_'] = int(angle) % 360
        self.batch.create(objid, props, creatorid=self.pack.boss_objid)

        self.batch.sound(objid, self._sound('bullet_fire', 'lab.bullet'))
        radians = math.radians(angle)
        model = self._bullet_model()
        speed = float(model.get('speed_mp_s')
                      or self.pack.mechanics['bullets']['speed'])
        self.bullets[objid] = {
            'x': float(x), 'y': float(y),
            'vx': math.cos(radians) * speed,
            'vy': math.sin(radians) * speed,
            'born': self.room_time,
            'next_move': self.room_time,
        }

    def _bullet_hit(self, bullet, start=None):
        """Swept rectangle against solid walls; ledges never stop a shot."""
        w = h = 5120
        x, y = start if start is not None else (bullet['x'], bullet['y'])
        dx, dy = bullet['x'] - x, bullet['y'] - y
        for _objid, (sx, sy, sw, sh), kind in self.terrain.surfaces:
            if kind != 1:
                continue
            enter, leave = 0.0, 1.0
            for origin, delta, lo, hi in ((x, dx, sx - w, sx + sw),
                                           (y, dy, sy - h, sy + sh)):
                if abs(delta) < 1e-12:
                    if origin < lo or origin > hi:
                        enter, leave = 1.0, 0.0
                        break
                else:
                    a, b = (lo - origin) / delta, (hi - origin) / delta
                    enter, leave = max(enter, min(a, b)), min(leave, max(a, b))
            if enter <= leave:
                return True
        return False

    def _step_bullets(self, dt):
        for objid, bullet in list(self.bullets.items()):
            start = bullet['x'], bullet['y']
            bullet['x'] += bullet['vx'] * dt
            bullet['y'] += bullet['vy'] * dt
            if self._bullet_hit(bullet, start):
                self.batch.destroy(objid, ownerid=self.worldid,
                                   reason='BLOCKED')
                del self.bullets[objid]
                continue
            if self.room_time >= bullet['next_move']:
                self.batch.move_to(objid, int(bullet['x']), int(bullet['y']),
                                   dt=int(round(self.BULLET_MOVE_STEP * 1000)))
                bullet['next_move'] = self.room_time + self.BULLET_MOVE_STEP

    def _step_motion(self, dt):
        due = [row for row in self.motion_commands if row[0] <= self.room_time]
        self.motion_commands = [row for row in self.motion_commands
                                if row[0] > self.room_time]
        for _when, objid, x, y, duration in due:
            self.batch.modify(objid, 'MOVE', message=(
                'Properties_Move',
                {'to_': {'xmp_': int(x), 'ymp_': int(y),
                         'dt_': int(duration)}}))

    def _begin_death(self, event):
        if self.phase in ('death', 'done'):
            return
        self.phase = 'death'
        self.phase_time = 0.0
        self.death_time = 0.0
        self.death_bursts = 0
        self.corpse_done = False
        self.loot_done = False
        gates = (getattr(self.pack, 'timegates', None) or {}).get('rules') or {}
        rule = gates.get('death') or {}
        target = rule.get('exit_target_mp') or list(EXIT_TARGET_MP)
        speed = float(rule.get('exit_speed_mp_s') or EXIT_SPEED_MP_S)
        self.death_wave_bursts = int(rule.get('bursts_per_wave')
                                     or DEATH_BURSTS_PER_WAVE)
        self.death_wave_period = float(rule.get('wave_period_s')
                                       or DEATH_WAVE_PERIOD_S)
        self.exit_from = (int(self.boss_pos[0]), int(self.boss_pos[1]))
        self.exit_to = (int(target[0]), int(target[1]))
        distance = math.hypot(self.exit_to[0] - self.exit_from[0],
                              self.exit_to[1] - self.exit_from[1])

        self.death_duration = max(0.1, distance / speed)
        self.death_waves = max(1, int(math.ceil(self.death_duration
                                                / self.death_wave_period)))
        self.death_wave_index = 0
        self.death_total = self.death_waves * self.death_wave_bursts
        self.loot_delay = self.death_duration + 1.0
        self.batch.modify(
            self.pack.boss_objid, 'MOVE',
            message=('Properties_Move', {
                'to_': {'xmp_': self.exit_to[0], 'ymp_': self.exit_to[1],
                        'dt_': int(round(self.death_duration * 1000))}}))
        self._event('death', bursts=self.death_total,
                    duration_s=round(self.death_duration, 3),
                    waves=self.death_waves,
                    exit=list(self.exit_to))

    def _spawn_burst(self):
        objid = self._alloc()
        props = self.pack.wire_props('lab.bossx')
        angle = self.rng.random() * math.pi * 2
        distance = self.rng.random() * 96 * 256
        props['xmp_'] = int(self.boss_pos[0] + math.cos(angle) * distance)
        props['ymp_'] = int(self.boss_pos[1] + math.sin(angle) * distance)
        self.batch.create(objid, props)

        self.batch.sound(objid, self._sound('death_burst', 'lab.bossx'))

    def _open_door(self):
        for objid in self.pack.object_ids('lab.closedprevdoor'):
            self.batch.modify(int(objid), 'VID', stringval='lab.prevdoor')
        for objid in self.pack.object_ids('lab.falsewall'):
            self.batch.destroy(int(objid), ownerid=self.worldid,
                               reason='INTERNAL')
        self.door_open = True
        self._event('door_open')

    def _step_death(self, dt):
        self.death_time += dt
        progress = min(1.0, self.death_time / self.death_duration)
        self.boss_pos = [
            int(self.exit_from[0] + (self.exit_to[0] - self.exit_from[0])
                * progress),
            int(self.exit_from[1] + (self.exit_to[1] - self.exit_from[1])
                * progress),
        ]
        due = min(self.death_waves,
                  int(self.death_time / self.death_wave_period) + 1)
        while self.death_wave_index < due:
            for _index in range(self.death_wave_bursts):
                self._spawn_burst()
                self.death_bursts += 1
            self.death_wave_index += 1
        if not self.corpse_done and self.death_time >= 0.5:
            self.corpse_done = True
            self._open_door()
        if not self.loot_done and self.death_time >= self.loot_delay:
            self.loot_done = True
            self._queue_loot()
        if self.death_time >= self.death_duration + self.DEATH_END_EXTRA:
            self.phase = 'done'
            self._event('done')

    def _queue_loot(self):
        known = getattr(self.server, 'item_types', None)
        table = ((206, 'boss-reward'), (18, 'health'), (21, 'ammo'))
        placed = 0
        for item_type, _label in table:
            if known and item_type not in known:
                continue
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
                                  'labmid-loot', record=False)
        self._wire_time_ms += dt * 1000.0
        target_ms = int(round(self._wire_time_ms))
        self.batch.dt = target_ms - self._sent_time_ms
        self._sent_time_ms = target_ms
        payload = self.batch.encode()
        try:
            self.server._send_raw(self.channel, MT_SERVER_DELTA, payload,
                                  'labmid-live', record=False)
            self.frames += 1
        except Exception as error:
            self.log('lab_mid send failed: ' + str(error))
            self._stop.set()
        self.batch.general = []
        self.batch.objects = []
        self.batch.extra = {}
        self.batch.dt = 0

    def status(self):
        info = {'map': 'lab_mid', 'phase': self.phase,
                'time': round(self.room_time, 3),
                'seeds': len(self.seeds), 'zombies': len(self.zombies),
                'bullets': len(self.bullets),
                'block_damage': self.total_block_damage,
                'boss_damage': self.total_boss_damage,
                'frames': self.frames}
        if self.controller is not None:
            info['mid'] = self.controller.status()
        if self.player is not None:
            info['player'] = {'x': self.player.xmp, 'y': self.player.ymp,
                              'hp': self.player.hp}
        return info
