"""SC room mechanisms, responding to current players and real damage inputs.

D values retain native fields and observed action timings. FIT values below
are local policies where captures do not establish a retail decision rule.
They are adjustable through recipe.metadata.mechanics / host.mechanics_config.
"""
import copy
import math
from collections import Counter

from ..efb_run.abilities import Ownership, center, nearest, players, rect, prop, move, changed
from ..efb_run.physics import TriangleTerrain
from .physics import clear as geometry_clear, geometry, surface, sweep as solid_sweep
from .mechanic_helpers import (MechanismTrack, animate, clear, distance_to_rect,
                               legal_book_sites, place, set_motion, stop)
from .engine import ScEngine

DEFAULTS = {
    # D native bodies and attack lanes; these are independent from Gen HP.
    'engine_hp': 60000, 'gen_hp': 5000, 'wall_hp': 5000,
    'wall_yaccel': 40, 'wall_maxyvel': 50000,
    'engine_seed_count': 3, 'engine_seed_wire_speed': 26000,
    'engine_spawner_birth_s': .416, 'engine_spawner_hp': 300,
    'engine_spawner_orbit_ms': 32000, 'engine_fire_lifetime_s': 4.5,
    'engine_ring_count': 10, 'engine_ring_wire_speed': 20000,
    'engine_wave_period_px': 400, 'engine_ring_amplitude': 20480,
    'tshooter_windup_s': .416,
    # FIT: observed stop-to-removetype range 7.000..7.050s. USER requires
    # both damage and this existence gate. The local damage ledger is a
    # policy, not a reconstruction of the retail server's private HP state.
    'wall_min_settled_s': 7.05,
    # USER: approach triggers the column before standing directly beneath it.
    # Oct23 native scmid18 starts a fall with the solo player centre 495px
    # from the column centre, still 448px clear of its body. A 500px lateral
    # window fits this approach; it is independent of the 7s damage gate.
    'wall_trigger_x_px': 500, 'wall_trigger_y_px': 640,
    'wall_fallback_drop_px': 512,
    # FIT decision/range/cadence; not a replay of either captured encounter.
    # A newer recording starts with chains before its first summon. Summon
    # participates in the same decision window, without an opening quota,
    # HP gate or automatic five-second summon clock. Equal choice is FIT.
    'engine_attack_modes': ('summon', 'spiral', 'wave', 'chains', 'straight'),
    'engine_round_pause_s': (5.10, 5.14),
    'engine_burst_interval_s': (1.02, 1.05), 'engine_ring_bursts': 3,
    # At least five 10-shot bursts are visible before death truncates the
    # recording. Five is the configurable local round-length fit.
    'engine_straight_bursts': 5, 'engine_straight_interval_s': (.77, .79),
    'engine_chain_phase_hp_fraction': .5,
    'engine_seed_flight_s': (2.35, 3.95),
    'engine_seed_radius_px': (900, 1400),
    'engine_spawner_fire_s': (1.0, 1.05),
    # OBS: fresh scmid03 starts all 20 nests at owner t=0; births follow at
    # t=410ms, including nests over 4400px from the recorded player. Runtime
    # activation belongs to room entry, not an individual player's distance.
    'tshooter_initial_s': 0.,
    # FIT death-to-next-windup delay. Four explicit native child destroys
    # precede the next nest animation by 5.559..6.089s in recorder time;
    # birth follows that animation by a separate observed .416s.
    'tshooter_respawn_s': (5., 6.),
    # OBS native client position samples: sustained 79000mp/s, acceleration
    # close to 300px/s^2, and non-transient pose1 waits of 946..1000ms. The
    # pause range remains a local fit; rapid blocked pose changes also exist.
    'tshot_speed_px_s': 79000/256, 'tshot_accel_px_s2': 300,
    'tshot_turn_pause_s': (.95, 1.), 'tshot_decide_s': (1., 2.5),
    'worker_speed_px_s': 124, 'worker_wait_s': (.25, 1.),
    'worker_carry_radius_px': 900, 'worker_place_random_nearest': 6,
    'worker_place_search_cells': 24,
    'mledge_speed_px_s': 150,
    'jump_turn_deg_s': 90, 'switch_float_px': 12, 'switch_float_period_s': 2.5,
    'switch_interact_radius_px': 160,
    'hive_active_radius_px': 1200, 'hive_max_children': 4,
    'hive_spawn_s': (4., 7.), 'hive_first_s': (.5, 2.),
    # OBS Oct7 endpoints: common walking is +78/-79MP per ms, matching
    # native signed fixed-point +/-20000 >> 8. Captures do not name the AI
    # states: sharing that common speed for patrol/chase remains a FIT.
    'vermin_speed_px_s': 20000*1000/256/256,
    'vermin_patrol_speed_px_s': 20000*1000/256/256,
    # OBS separate short (~53px) and row (~206px) rises. FIT ballistic
    # parameters cover these two shapes; route geometry selects the mode.
    'vermin_jump_px_s': 375,
    'vermin_gravity_px_s2': 340000/256, 'vermin_jump_cooldown_s': (1.3, 2.5),
    'vermin_jump_clearance_px': 8,
    # FIT: USER-described Cathedral vermin behaviour. Aggro envelope, idle
    # patrol speed, ledge-change cadence and the chase jump height are local
    # policies; captures do not establish a retail decision rule.
    'vermin_aggro_px': 900, 'vermin_release_px': 1400,
    # USER: a vermin chases for a while, gives up, and only re-acquires later;
    # these windows/scan rules are not broadcast in the observed motion.
    'vermin_chase_s': (2.5, 5.), 'vermin_reaggro_s': (3., 7.), 'vermin_aim_s': (.3, .7), 'vermin_hop_s': (2.5, 6.),
    'vermin_climb_px': 48, 'vermin_climb_jump_px_s': 750,
}

WORKERS = {'sc.libworker', 'sc.libworkerb', 'sc.libworkerr'}
BOOKS = {'sc.book', 'sc.evilbook'}
JUMP_HAZARDS = {'sc.peg32', 'sc.evilpeg32', 'sc.wheel128', 'sc.wheel256'}
MECHANISMS = {'sc.enginecore', 'sc.denginecore', 'sc.engineball', 'sc.enginefireseed', 'sc.enginefire', 'sc.enginefireub',
              'sc.enginespawner', 'sc.enginefire', 'sc.dropwall', 'sc.tshooter', 'sc.tshot',
              'sc.hive', 'sc.vermin', 'sc.switch', 'sc.switchdone', 'sc.core', 'sc.dcore'} | WORKERS | BOOKS | JUMP_HAZARDS


class ScMechanics(Ownership):
    def __init__(self, host):
        super().__init__(host, '_sc_mechanic')
        self.states, self.tracks, self.pending = {}, {}, []
        self.now = host.room_time
        self.config = copy.deepcopy(DEFAULTS)
        self.config.update(host.recipe.get('metadata', {}).get('mechanics', {}))
        self.config.update(getattr(host, 'mechanics_config', {}))
        self.engine = ScEngine(self)
        self._physics_clock = None
        self.platform_deltas = {}
        self.guardian = int(host.recipe.get('metadata', {}).get('chest_guardian') or 0)
        self.guardian_row = None
        self.worker_sites = []
        self.mledge_bounds = host.recipe.get('metadata', {}).get('sc_mledge_bounds')
        self.mledge_stream = copy.deepcopy(host.recipe.get('metadata', {}).get('sc_mledge_stream'))
        self.mledge_stream_started = False

    def _range(self, key):
        value = self.config[key]
        return self.host.rng.uniform(*value) if isinstance(value, (tuple, list)) else float(value)

    def prepare(self):
        # Capture each lane's native MOVE before adoption moves it into a track.
        natives = {oid: copy.deepcopy(row['props'].get('move') or {})
                   for oid, row in self.host.actors.items()
                   if row['vid'].startswith('sc.mledge')}
        for oid in list(self.host.actors):
            self.add_actor(oid)
        # A resident child's creator can precede or follow its nest in the
        # snapshot. Bind only explicit creation identities, never proximity.
        for oid, state in self.states.items():
            if state['vid'] == 'sc.tshooter':
                self._tshooter_child(oid, state)
        self.guardian_row = self.host.actors.get(self.guardian)
        self.worker_sites = [tuple(r['pos']) for r in self.host.actors.values() if r['vid'] in WORKERS]
        ledges = [r for r in self.host.actors.values() if r['vid'].startswith('sc.mledge')]
        if ledges and not self.mledge_bounds:
            # FIT streaming boundary, never inferred as a bounce instruction.
            self.mledge_bounds = dict(left=min(r['pos'][0] for r in ledges)-256*256,
                                      right=max(r['pos'][0]+r['props'].get('wmp', 0) for r in ledges)+256*256)
        # USER: the moving-platform room opens empty; both sides only start
        # streaming their lanes once a player is inside. Park every authored
        # ledge just outside its own lane and release it on the first player.
        self.mledge_hold = {}
        if self.mledge_stream:
            # Authored stock came from an already running room. A fresh local
            # instance starts generators at the actual two entry boundaries;
            # it must not park that stock a full room-width away or replenish
            # just one platform for each retirement.
            for row in ledges:
                oid = row['id']
                self.retire(oid, 'INTERNAL')
                self.tracks.pop(oid, None)
                self.states.pop(oid, None)
        elif self.mledge_bounds:
            stock = sorted((oid for oid, r in self.host.actors.items()
                            if r['vid'].startswith('sc.mledge')),
                           key=lambda oid: self.host.actors[oid]['pos'][0])
            left = right = 0
            for oid in stock:
                row = self.host.actors[oid]
                width = row['props'].get('wmp', 0)
                facing = row['props'].get('facing')
                direction = -1 if facing == 180 else 1
                movement = natives.get(oid) or copy.deepcopy(row['props'].get('move') or {})
                velocity = (movement.get('vel') or {}).get('xvel') or 0
                if velocity:
                    direction = -1 if velocity < 0 else 1
                # A custom lane without a stream profile starts at its own
                # edge; no additional room-width delay is introduced.
                x = self.mledge_bounds['left']-width if direction > 0 else self.mledge_bounds['right']
                place(self.host, oid, (x, row['pos'][1]))
                self.tracks.pop(oid, None)
                row['props'].pop('move', None)
                self.mledge_hold[oid] = (direction, movement)
                self.states[oid]['native_move'] = movement
        # Authored vermin can overlap a wall in the snapshot; move them out.
        for oid, state in list(self.states.items()):
            if state['vid'] != 'sc.vermin':
                continue
            row = self.host.actors.get(oid)
            if row is None:
                continue
            w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
            if geometry_clear(self.host, (row['pos'][0], row['pos'][1], w, h), (oid,)):
                continue
            for dx in (0, -16*256, 16*256, -32*256, 32*256, 0, 0):
                for dy in (0, 0, 0, 0, 0, -16*256, 16*256):
                    if geometry_clear(self.host, (row['pos'][0]+dx, row['pos'][1]+dy, w, h), (oid,)):
                        place(self.host, oid, (row['pos'][0]+dx, row['pos'][1]+dy))
                        state['body'].update(x=row['pos'][0], y=row['pos'][1])
                        break
                else:
                    continue
                break
        # A carried book is a separate terrain entity, with a real carrier ID.
        for oid, state in self.states.items():
            if state['vid'] not in BOOKS:
                continue
            movement = self.host.actors[oid]['props'].get('move', {})
            carrier = movement.get('relativeto', {}).get('objid')
            if carrier in self.states and self.states[carrier]['vid'] in WORKERS:
                state['carrier'] = carrier
                self.states[carrier]['book'] = oid
        self._sync_carried_books()
        self._bind_jump_turns()
        self._sync_relative_hazards(round(self.now*1000))
        if self.host.instance.flags.get('mid_cleared'):
            self._stop_jump()
        self.host.record('sc_mechanism_policy', evidence='D native fields/actions; USER damage/existence gates, workers and Jump; configurable FIT decision policies')

    def add_actor(self, oid):
        row = self.host.actors.get(oid)
        if row is None or row.get('dead') or row.get('_sc_enemy'):
            return False
        if oid in self.owned:
            return True
        vid = row['vid']
        movement = row['props'].get('move')
        # OBS: the authored Cathedral rooms carry hundreds of terrain bodies
        # with their own native MOVE (floating ledges, sine platforms). A
        # mechanism-only whitelist left every one of them frozen at birth, so
        # any independently moving terrain body keeps its native track.
        moving_terrain = bool(movement and not movement.get('relativeto')
                              and row['props'].get('terraintype') in (1, 2))
        if vid not in MECHANISMS and not vid.startswith('sc.mledge') and not moving_terrain:
            return False
        self.own(oid)
        s = self.states[oid] = dict(vid=vid, origin=tuple(row['pos']), next=self.now, phase='idle')
        if movement and not movement.get('relativeto'):
            self.tracks[oid] = MechanismTrack(row['pos'], movement, round(self.now*1000))
        if vid in ('sc.enginecore', 'sc.core'):
            hp = self.config['engine_hp' if vid == 'sc.enginecore' else 'gen_hp']
            row['hp'] = hp
            prop(self.host, oid, 'hitpoints', hp)
            if vid == 'sc.enginecore':
                self.engine.prepare(s)
                # This body physically blocks passage until its own death.
                prop(self.host, oid, 'terraintype', 1)
        elif vid == 'sc.dropwall':
            active = row.get('hp') is not None
            s.update(phase='settled' if active else 'waiting', damage=0,
                     settled=self.now if active else None)
            if active:
                # A completed capture snapshot is already past its native gate.
                s['open_at'] = self.now if row['props'].get('removetype') == 1 else self.now+self.config['wall_min_settled_s']
        elif vid == 'sc.enginespawner':
            active = row.get('hp') is not None
            s.update(phase='active' if active else 'birth',
                     ready=self.now if active else self.now+self.config['engine_spawner_birth_s'],
                     next=self.now+self._range('engine_spawner_fire_s'))
        elif vid == 'sc.enginefireseed':
            s['plant_at'] = self.now+self._range('engine_seed_flight_s')
        elif vid == 'sc.tshooter':
            s.update(next=self.now+self._range('tshooter_initial_s'), child=None)
        elif vid == 'sc.tshot':
            s.update(speed=0., heading=int(row['props'].get('facing', 90))%360,
                     next=self.now+self._range('tshot_decide_s'))
            # It is a 1000HP moving terrain body, never an expiring small shot.
            row['hp'] = row.get('hp') if row.get('hp') is not None else 1000
            prop(self.host, oid, 'hitpoints', row['hp'])
            prop(self.host, oid, 'terraintype', 1)
            prop(self.host, oid, 'blocktype', 2)
        elif vid in WORKERS:
            s.update(book=None, destination=None, next=self.now+self._range('worker_wait_s'))
            # A retained native initial leg is evidence, not a new FIT route.
            # Integrate it once in the worker controller, rather than also
            # resetting its position from an independent track every frame.
            self.tracks.pop(oid, None)
            leg = (movement or {}).get('to')
            if leg:
                destination = (leg.get('xmp', row['pos'][0]), leg.get('ymp', row['pos'][1]))
                remaining = max(1, leg.get('dt', 1)-leg.get('pos', 0))/1000.
                s.update(destination=destination, leg_speed=math.dist(row['pos'], destination)/remaining)
        elif vid in BOOKS:
            s['carrier'] = None
            prop(self.host, oid, 'terraintype', 2)
        elif vid == 'sc.hive':
            s.update(next=self.now+self._range('hive_first_s'), children=set())
        elif vid == 'sc.vermin':
            x, y, w, h = rect(row)
            # USER: a vermin walks its own ledge while idle; acquisition turns
            # it toward the player and lets it cross ledges vertically.
            s.update(body=dict(x=x, y=y, w=w, h=h, vy=0., grounded=False),
                     next_jump=self.now+self._range('vermin_jump_cooldown_s'),
                     aggro=False, direction=-1 if row['props'].get('facing') == 180 else 1,
                     next_hop=self.now+self._range('vermin_hop_s'))
        elif vid.startswith('sc.mledge') and not movement:
            # Native snapshots often contain only the current MoveTo position.
            # FIT continuous horizontal travel is used only without native MOVE.
            s.update(direction=-1 if row['props'].get('facing') == 180 else 1)
        return True

    def _spawn(self, vid, point, owner=0, changes=None):
        oid = self.host._create(vid, point, owner, changes)
        self.add_actor(oid)
        return oid

    def _movement(self, oid, movement):
        set_motion(self.host, oid, movement)
        if movement.get('relativeto'):
            self.tracks.pop(oid, None)
            return
        self.tracks[oid] = MechanismTrack(self.host.actors[oid]['pos'], movement, round(self.now*1000))

    def _bind_jump_turns(self):
        # Wheel facing follows the signed angular speed of its attached
        # orbiters. Authored periods differ per wheel, including reverse turns.
        rates = {}
        for row in self.host.actors.values():
            movement = row['props'].get('move') or {}
            parent = (movement.get('relativeto') or {}).get('objid')
            ellipse = movement.get('elliptical') or {}
            a = ellipse.get('radiusamp', 0)
            b = ellipse.get('radiusbmp', a)
            if parent in self.states and ellipse.get('period', 0) > 0 and a*b:
                rates.setdefault(parent, set()).add(math.copysign(360000./ellipse['period'], a*b))
        for oid, state in self.states.items():
            if state['vid'] not in ('sc.wheel128', 'sc.wheel256'):
                continue
            choices = rates.get(oid, set())
            if len(choices) == 1:
                state.update(turn_rate=next(iter(choices)), turn_at=0., turn_facing=0.)
            else:
                # Custom wheels without a consistent authored orbit retain
                # the configurable local fallback, without frame-rounding drift.
                state.update(turn_rate=self.config['jump_turn_deg_s'], turn_at=self.now,
                             turn_facing=self.host.actors[oid]['props'].get('facing', 0))

    def pre_physics(self, dt):
        if self.mledge_hold and players(self.host):
            # USER: the room's lanes start only after a player is inside.
            self._release_ledges()
        clock = round(self.host.room_time*1000)
        if clock == self._physics_clock:
            return
        self._physics_clock = clock
        self.now = self.host.room_time
        self._stream_ledges()
        self.platform_deltas = {}
        for oid, track in list(self.tracks.items()):
            row = self.host.actors.get(oid)
            if row is None:
                self.tracks.pop(oid, None)
                continue
            evaluated_clock = clock
            if row['vid'] == 'sc.enginefireseed':
                evaluated_clock = min(clock, round(self.states[oid]['plant_at']*1000))
            old, new = row['pos'], list(track.position(evaluated_clock))
            row['pos'] = new
            if old != new and row['props'].get('terraintype') in (1, 2):
                changed(self.host)
                self.platform_deltas[oid] = (old, new, (row['props'].get('wmp', 0), row['props'].get('hmp', 0)))
        self._sync_relative_hazards(clock)
        self._sync_engine_followers(dt)
        if not self.host.instance.flags.get('mid_cleared'):
            for oid, state in self.states.items():
                if 'turn_rate' in state and oid in self.host.actors:
                    angle = state['turn_facing']+state['turn_rate']*(clock/1000.-state['turn_at'])
                    prop(self.host, oid, 'facing', math.trunc(math.fmod(angle, 360.)))

    def _sync_relative_hazards(self, clock_ms):
        # Jump has nested native orbits: wheel -> wheel -> solid peg. Keep
        # that MOVE on the client and evaluate the same committed hierarchy
        # for server geometry, including before a switch freezes the room.
        snapshot = getattr(self.host.instance, 'snapshot', None)
        if snapshot is None:
            return
        for oid, row in self.host.actors.items():
            movement = row['props'].get('move') or {}
            key = (1, oid)
            if row['vid'] not in JUMP_HAZARDS or not movement.get('relativeto') or key not in snapshot.objects:
                continue
            old = tuple(row['pos'])
            new = snapshot.position(key, at=clock_ms/1000.)
            row['pos'] = list(new)
            if old != new and row['props'].get('terraintype') in (1, 2):
                changed(self.host)
                self.platform_deltas[oid] = (old, new, (row['props'].get('wmp', 0), row['props'].get('hmp', 0)))

    def _core_center(self, owner=None):
        row = self.host.actors.get(owner)
        if row is None:
            row = next((r for r in self.host.actors.values() if r['vid'] in ('sc.enginecore', 'sc.denginecore')), None)
        if row:
            x, y, w, h = rect(row)
            return x+w/2, y+h/2
        return 0., 0.

    def _sync_engine_followers(self, dt):
        # Native follow uses pixel-quantized deltas and a fixed-point clamp.
        # Resolve each chain from its moving head; preserve previous follower
        # positions instead of resetting them to their creation point.
        done = set()
        def follow(oid, visiting):
            row = self.host.actors.get(oid)
            if row is None or oid in done or oid in visiting:
                return
            movement = row['props'].get('move') or {}
            model = movement.get('follow')
            if row['vid'] != 'sc.enginefire' or not model:
                return
            parent = (movement.get('relativeto') or {}).get('objid')
            if parent not in self.host.actors:
                return
            follow(parent, visiting | {oid})
            point, old = self.host.actors[parent]['pos'], row['pos']
            dx, dy = ((int(old[i]) >> 8)-(int(point[i]) >> 8) for i in (0, 1))
            maximum = max(0, model.get('maxdist', 0))
            distance2 = dx*dx+dy*dy
            if distance2 > maximum*maximum:
                scale = (maximum << 10)//max(1, math.isqrt(distance2))
                new = [point[0]+math.trunc(dx*scale/1024)*256,
                       point[1]+math.trunc(dy*scale/1024)*256]
                row['pos'] = new
                row['vel'] = [(new[i]-old[i])/dt if dt else 0. for i in (0, 1)]
            done.add(oid)
        for oid in self.owned:
            follow(oid, set())

    def _plant_seed(self, oid, s):
        row = self.host.actors.get(oid)
        if row is None:
            return
        cx, cy = self._core_center(s.get('core'))
        x, y = row['pos']
        child = self._spawn('sc.enginespawner', (x-24*256, y-24*256), s.get('core', 0))
        cs = self.states[child]
        cs['core_center'] = (cx-32*256, cy-32*256)
        cs['orbit_radius'] = math.dist(row['pos'], cs['core_center'])
        cs['orbit_angle'] = math.degrees(math.atan2(y-cs['core_center'][1], x-cs['core_center'][0]))
        animate(self.host, child, 1, 396)
        self.retire(oid, 'INTERNAL')

    def _spawner(self, oid, s):
        row = self.host.actors[oid]
        if s['phase'] == 'birth':
            if self.now < s['ready']:
                return
            s['phase'] = 'active'
            row['hp'] = self.config['engine_spawner_hp']
            prop(self.host, oid, 'hitpoints', row['hp'])
            prop(self.host, oid, 'damage', 15)
            cx, cy = s.get('core_center', self._core_center())
            radius = s.get('orbit_radius', self._range('engine_seed_radius_px')*256)
            self._movement(oid, {'elliptical': dict(xmp=round(cx), ymp=round(cy), radiusamp=round(radius),
                                                   angle=round(s.get('orbit_angle', 0)), period=self.config['engine_spawner_orbit_ms'])})
            s['next'] = self.now+.1
        if self.now < s['next']:
            return
        animate(self.host, oid, 3, 528)
        cx, cy = self._core_center()
        child = self._spawn('sc.enginefire', (row['pos'][0]+16*256, row['pos'][1]+16*256), oid)
        self._movement(child, {'to': dict(xmp=round(cx-16*256), ymp=round(cy-16*256),
                                         dt=round(self.config['engine_fire_lifetime_s']*1000))})
        s['next'] = self.now+self._range('engine_spawner_fire_s')

    def _start_wall(self, oid, s):
        row = self.host.actors[oid]
        row['hp'] = self.config['wall_hp']
        prop(self.host, oid, 'hitpoints', row['hp'])
        s.update(phase='falling', velocity=0., damage=0, started=self.now)
        x, y, w, h = rect(row)
        floors = [r['pos'][1]-h for other, r in self.host.actors.items() if other != oid and
                  r['props'].get('terraintype') in (1, 2) and r['pos'][0] < x+w and
                  r['pos'][0]+r['props'].get('wmp', 0) > x and r['pos'][1] >= y+h-1]
        s['floor'] = min(floors) if floors else y+self.config['wall_fallback_drop_px']*256
        set_motion(self.host, oid, dict(vel=dict(yaccel=self.config['wall_yaccel'], maxyvel=self.config['wall_maxyvel'])))
        self.host.record('sc_dropwall_trigger', id=oid, gate='damage_and_minimum_settled_time')

    def _wall(self, oid, s, dt, target):
        row = self.host.actors[oid]
        if s['phase'] == 'waiting':
            x, y, w, h = rect(row)
            # A player above/behind the socket must not hide another player
            # approaching the passage. Test every live player's corridor box,
            # rather than whichever one is nearest the tall sprite's centre.
            for player in players(self.host):
                px, py = center(player)
                feet = (player.get('ymp', 0)+player.get('hmp', 0) if isinstance(player, dict)
                        else player.ymp+player.hmp)
                if (abs(px-x-w/2) <= self.config['wall_trigger_x_px']*256
                        and feet >= y+h-16*256
                        and py <= y+h+self.config['wall_trigger_y_px']*256):
                    self._start_wall(oid, s)
                    break
        elif s['phase'] == 'falling':
            accel = self.config['wall_yaccel']*1000000/256
            maximum = self.config['wall_maxyvel']*1000/256
            before = s['velocity']
            s['velocity'] = min(maximum, before+accel*dt)
            y = min(s['floor'], row['pos'][1]+(before+s['velocity'])*dt/2)
            if y >= s['floor']-1:
                place(self.host, oid, (row['pos'][0], s['floor']))
                s.update(phase='settled', settled=self.now, open_at=self.now+self.config['wall_min_settled_s'])
            else:
                move(self.host, oid, (row['pos'][0], y), dt)
        elif s['phase'] == 'settled' and self.now+1e-9 >= s['open_at']:
            prop(self.host, oid, 'removetype', 1)
            if row.get('hp') is not None and row['hp'] <= 0:
                self._killed(oid)

    def _tshooter_child(self, oid, s):
        """Reconcile the single live child before advancing a nest's clock."""
        child = s.get('child')
        row = self.host.actors.get(child)
        if row is not None and not row.get('dead') and row['vid'] == 'sc.tshot':
            return child
        for other, row in self.host.actors.items():
            if (row['vid'] == 'sc.tshot' and not row.get('dead')
                    and row.get('creator') == oid):
                if other in self.states:
                    self.states[other]['owner'] = oid
                s.update(child=other, phase='idle', next=None)
                return other
        if child is not None:
            # Retirement calls this at the actual death clock. If an external
            # host removed the child, start conservatively at first observation
            # instead of backdating a death that the controller did not see.
            s.update(child=None, phase='idle', child_lost_at=self.now,
                     next=self.now+self._range('tshooter_respawn_s'))
        return None

    def _tshot_spawn(self, oid, s):
        row = self.host.actors[oid]
        x, y, w, h = rect(row)
        heading = int(row['props'].get('facing', 90))%360
        angle = math.radians(heading)
        size = self.host.pack.props('sc.tshot')
        terrain = geometry(self.host)
        # Never birth a meatball inside a wall: try the muzzle, then the
        # shooter, then small offsets. A blocked device retries later.
        candidates = [(x+54*256*math.cos(angle), y+54*256*math.sin(angle)),
                      (x+w/2-size.get('wmp', 0)/2, y+h/2-size.get('hmp', 0)/2)]
        for step in range(0, 360, 45):
            candidates.append((x+w/2+math.cos(math.radians(step))*48*256,
                               y+h/2+math.sin(math.radians(step))*48*256))
        point = None
        for candidate in candidates:
            if not solid_sweep(terrain, candidate, candidate,
                               (size.get('wmp', 0), size.get('hmp', 0)), ignore=(oid,)):
                point = candidate
                break
        if point is None:
            s.update(phase='idle', next=self.now+self._range('tshooter_respawn_s'))
            return
        child = self._spawn('sc.tshot', point, oid, dict(facing=heading, lifetime=0))
        self.states[child]['owner'] = oid
        self.states[child]['heading'] = None
        # A living child has no pending replacement deadline. The full wait
        # starts when this child is retired, however long its life lasted.
        s.update(child=child, phase='idle', next=None)

    def _tshot_heading(self, oid, s, terrain, point=None):
        """Pick a heading only while stopped, preferring a clear corridor.

        USER: the meatball runs to the end of a corridor, pauses, chooses a
        direction and only then moves; it never changes direction mid-run.
        """
        row = self.host.actors[oid]
        w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
        x, y = point if point is not None else row['pos']
        ignore = (oid, s.get('owner', 0))
        options = [0, 90, 180, 270]
        self.host.rng.shuffle(options)
        target = s.get('target_center')
        if target:
            options.sort(key=lambda a: -((target[0]-x-w/2)*math.cos(math.radians(a))+
                                         (target[1]-y-h/2)*math.sin(math.radians(a))))
        for a in options:
            probe = (x+math.cos(math.radians(a))*8*256, y+math.sin(math.radians(a))*8*256)
            if not solid_sweep(terrain, (x, y), probe, (w, h), ignore=ignore):
                return a
        return None

    def _tshot_unstick(self, oid, s):
        """A meatball that cannot move must never stay inside a wall."""
        row = self.host.actors[oid]
        owner = self.host.actors.get(s.get('owner'))
        terrain = geometry(self.host)
        w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
        seeds = []
        if owner is not None:
            seeds.append(tuple(owner['pos']))
        seeds.append(tuple(row['pos']))
        for x, y in seeds:
            for radius in (0, 32*256, 64*256, 128*256):
                for step in range(0, 360, 45):
                    point = (x+math.cos(math.radians(step))*radius,
                             y+math.sin(math.radians(step))*radius)
                    if not solid_sweep(terrain, point, point, (w, h), ignore=(oid,)):
                        place(self.host, oid, point)
                        s.update(speed=0., wait_until=self.now+self._range('tshot_turn_pause_s'))
                        return True
        self.retire(oid, 'INTERNAL')
        return False

    def _tshot(self, oid, s, dt, target):
        row = self.host.actors[oid]
        if s.get('wait_until', 0) > self.now:
            return
        terrain = geometry(self.host)
        if target is not None:
            s['target_center'] = center(target)
        if s.get('heading') is None:
            heading = self._tshot_heading(oid, s, terrain)
            if heading is None:
                self._tshot_unstick(oid, s)
                return
            s['heading'] = heading
        before = s['speed']
        maximum = self.config['tshot_speed_px_s']*256
        acceleration = self.config['tshot_accel_px_s2']*256
        ramp = min(dt, max(0., maximum-before)/acceleration) if acceleration > 0 else 0.
        travel = before*ramp+.5*acceleration*ramp*ramp+maximum*(dt-ramp)
        s['speed'] = min(maximum, before+acceleration*dt)
        x, y, w, h = rect(row)
        heading = s['heading']
        angle = math.radians(heading)
        end = (x+math.cos(angle)*travel, y+math.sin(angle)*travel)
        hit = solid_sweep(terrain, (x, y), end, (w, h), ignore=(oid, s.get('owner', 0)))
        if hit:
            f = max(0., hit[0]-1e-6)
            end = (x+(end[0]-x)*f, y+(end[1]-y)*f)
            s.update(goal=end)
            # Stop at the end of the corridor, pause, then choose a direction.
            s.update(speed=0., heading=None, wait_until=self.now+self._range('tshot_turn_pause_s'))
            if f <= 0.:
                self._tshot_unstick(oid, s)
                return
        # Native pose2 is horizontal travel and pose3 is vertical travel.
        move(self.host, oid, end, dt, pose=1 if hit else 2 if heading%180 == 0 else 3,
             facing=heading)

    def _book_attach(self, worker, book):
        row = self.host.actors[book]
        self.states[book]['carrier'] = worker
        self.states[book].pop('reserved', None)
        self.states[worker]['book'] = book
        prop(self.host, book, 'draw', False)
        prop(self.host, book, 'terraintype', 2)
        set_motion(self.host, book, dict(relativeto=dict(objid=worker), to=dict(xmp=16*256, ymp=64*256)))
        self._sync_carried_books()

    def _sync_carried_books(self):
        for oid, state in self.states.items():
            if state['vid'] not in BOOKS:
                continue
            carrier = self.host.actors.get(state.get('carrier'))
            row = self.host.actors.get(oid)
            if row is None or carrier is None:
                continue
            offset = row['props'].get('move', {}).get('to', {})
            point = [carrier['pos'][0]+offset.get('xmp', 16*256),
                     carrier['pos'][1]+offset.get('ymp', 64*256)]
            if row['pos'] != point:
                row['pos'] = point
                changed(self.host)

    def _book_place(self, worker, point):
        s = self.states[worker]
        book = s.get('book')
        row = self.host.actors.get(book)
        if row is None:
            s['book'] = None
            return True
        sites = legal_book_sites(self.host, point, (row['props'].get('wmp', 8192), row['props'].get('hmp', 8192)),
                                 ignore=(book,), radius_cells=self.config['worker_place_search_cells'])
        if not sites:
            return False
        # USER nearest / random placement: sample only the closest legal sites.
        destination = self.host.rng.choice(sites[:max(1, self.config['worker_place_random_nearest'])])
        place(self.host, book, destination)
        prop(self.host, book, 'draw', True)
        prop(self.host, book, 'terraintype', 2)
        self.states[book]['carrier'] = None
        s['book'] = None
        return True

    def _worker(self, oid, s, dt):
        row = self.host.actors[oid]
        dest = s.get('destination')
        if dest is not None:
            delta = (dest[0]-row['pos'][0], dest[1]-row['pos'][1])
            distance = math.hypot(*delta)
            travel = s.get('leg_speed', self.config['worker_speed_px_s']*256)*dt
            arrived = distance <= travel
            point = dest if arrived else (row['pos'][0]+delta[0]*travel/distance, row['pos'][1]+delta[1]*travel/distance)
            move(self.host, oid, point, dt, pose=5, facing=180 if delta[0] < 0 else 0)
            book = s.get('book')
            if book in self.host.actors:
                self.host.actors[book]['pos'] = [point[0]+16*256, point[1]+64*256]
                changed(self.host)
            if not arrived:
                return
            stop(self.host, oid)
            s.pop('leg_speed', None)
            s.update(destination=None, next=self.now+self._range('worker_wait_s'))
            if s.get('pickup') in self.host.actors and self.states[s['pickup']].get('carrier') is None:
                self._book_attach(oid, s.pop('pickup'))
            elif s.get('book') is not None:
                self._book_place(oid, (point[0]+16*256, point[1]+64*256))
            return
        if self.now < s['next']:
            return
        if s.get('book') is None:
            choices = [bid for bid, bs in self.states.items() if bs['vid'] in BOOKS and bs.get('carrier') is None and not bs.get('reserved') and bid in self.host.actors]
            if not choices:
                s['next'] = self.now+1.
                return
            nearby = sorted(choices, key=lambda bid: math.dist(self.host.actors[bid]['pos'], row['pos']))[:20]
            book = self.host.rng.choice(nearby)
            self.states[book]['reserved'] = oid
            bx, by = self.host.actors[book]['pos']
            s.update(pickup=book, destination=(bx-16*256, by-64*256))
        else:
            radius = self.config['worker_carry_radius_px']*256
            desired = (row['pos'][0]+self.host.rng.uniform(-radius, radius), row['pos'][1]+self.host.rng.uniform(-radius, radius))
            # Carry destinations stay within the authored library's book field.
            books = [bs['origin'] for bs in self.states.values() if bs['vid'] in BOOKS]
            if books:
                desired = (min(max(desired[0], min(p[0] for p in books)), max(p[0] for p in books)),
                           min(max(desired[1], min(p[1] for p in books)), max(p[1] for p in books)))
            s['destination'] = (desired[0]-16*256, desired[1]-64*256)
        distance = math.dist(row['pos'], s['destination'])
        duration = max(1, round(distance/(self.config['worker_speed_px_s']*256)*1000))
        set_motion(self.host, oid, dict(to=dict(xmp=round(s['destination'][0]), ymp=round(s['destination'][1]),
                                              dt=duration, start=dict(xmp=round(row['pos'][0]), ymp=round(row['pos'][1])))))

    def _hive(self, oid, s, target):
        s['children'].intersection_update(self.host.actors)
        if self.now < s['next'] or len(s['children']) >= self.config['hive_max_children'] or target is None:
            return
        row = self.host.actors[oid]
        if distance_to_rect(center(target), rect(row)) > self.config['hive_active_radius_px']*256:
            return
        x, y, w, h = rect(row)
        base = (x-48*256 if row['props'].get('facing') == 180 else x+w, y+h-48*256)
        # USER: a vermin must never be born inside a wall; try nearby openings
        # and wait for the next cycle when there is none.
        size = (48*256, 48*256)
        point = None
        for dx in (0, -32*256, 32*256, -64*256, 64*256):
            for dy in (0, -32*256, 32*256):
                candidate = (base[0]+dx, base[1]+dy)
                if geometry_clear(self.host, (candidate[0], candidate[1], size[0], size[1]), (oid,)):
                    point = candidate
                    break
            if point:
                break
        if point is None:
            s['next'] = self.now+self._range('hive_spawn_s')
            return
        child = self._spawn('sc.vermin', point, oid)
        s['children'].add(child)
        s['next'] = self.now+self._range('hive_spawn_s')

    def _vermin_routes(self, oid, b):
        """Reachable vertical landings, never a path through solid terrain.

        USER supplies idle up/down choice; selection among legal candidates
        is local policy, not a recovered native probability or target ID.
        """
        terrain = geometry(self.host)
        x, y, w, h = b['x'], b['y'], b['w'], b['h']
        feet = y+h
        gravity = self.config['vermin_gravity_px_s2']*256
        clearance = self.config['vermin_jump_clearance_px']*256
        support = next((entry for entry in terrain if entry[0] == b.get('support')), None)
        up, below = [], []
        for sid, bounds, kind, shape in terrain:
            top = surface(bounds, shape, x, w)
            if top is None or sid in (oid, b.get('support')):
                continue
            if top > feet+1:
                below.append((top, sid))
            elif top < feet-1:
                for key in ('vermin_jump_px_s', 'vermin_climb_jump_px_s'):
                    speed = self.config[key]*256
                    rise = speed*speed/(2*gravity)
                    if feet-top+clearance > rise:
                        continue
                    apex = (x, y-rise)
                    if (solid_sweep(terrain, (x, y), apex, (w, h), ignore=(oid,)) is None
                            and geometry_clear(self.host, (x, top-h, w, h), (oid,))):
                        up.append(dict(action='up', support=sid, top=top, speed=speed))
                        break
        routes = sorted(up, key=lambda route: feet-route['top'])
        if support and support[2] == 2 and below:
            # Native Oct7 drop: YMP += 1 from the current kind2 support;
            # x stays fixed. Only the first reachable lower surface counts.
            top, sid = min(below)
            other_support = any(entry[0] != support[0]
                                and (value := surface(entry[1], entry[3], x, w)) is not None
                                and abs(value-feet) <= 1 for entry in terrain)
            if (not other_support
                    and solid_sweep(terrain, (x, y), (x, top-h), (w, h), ignore=(oid,)) is None
                    and geometry_clear(self.host, (x, top-h, w, h), (oid,))):
                routes.append(dict(action='down', support=support[0], landing=sid, top=top))
        return routes

    def _vermin(self, oid, s, dt, target, terrain):
        b = s['body']
        cx, cy = b['x']+b['w']/2, b['y']+b['h']/2
        px = py = None
        reach = None
        if target is not None:
            px, py = center(target)
            reach = math.hypot(px-cx, py-cy)
        # USER: acquisition and release are independent of the wake distance;
        # an idle vermin keeps patrolling instead of standing at a wall.
        if s['aggro']:
            if (reach is None or reach > self.config['vermin_release_px']*256
                    or self.now >= s.get('aggro_until', math.inf)):
                s['aggro'] = False
                s['reaggro_at'] = self.now+self._range('vermin_reaggro_s')
        elif (reach is not None and reach <= self.config['vermin_aggro_px']*256
              and self.now >= s.get('reaggro_at', 0.)):
            s['aggro'] = True
            s['aggro_until'] = self.now+self._range('vermin_chase_s')
            s['direction'] = -1 if px < cx else 1
        if not s['aggro'] and b.get('blocked'):
            s['direction'] *= -1
        speed_key = 'vermin_speed_px_s' if s['aggro'] else 'vermin_patrol_speed_px_s'
        wire_speed = round(self.config[speed_key]*256*256/1000)
        vx = 0.
        if s['aggro'] and px is not None:
            # USER: pick a direction, commit to it for a while, then re-aim.
            if self.now >= s.get('aim_at', 0.):
                s['direction'] = -1 if px < cx else 1
                s['aim_at'] = self.now+self._range('vermin_aim_s')
            if abs(px-cx) > 8*256:
                vx = (s['direction']*wire_speed >> 8)*1000
        elif not s['aggro']:
            vx = (s['direction']*wire_speed >> 8)*1000
        climbing = bool(s['aggro'] and py is not None and py < b['y']-self.config['vermin_climb_px']*256)
        descending = bool(s['aggro'] and py is not None and py > cy+self.config['vermin_climb_px']*256)
        if b.get('grounded'):
            s.pop('air_vx', None)
            route = None
            if s['aggro'] and self.now >= s['next_jump'] and (climbing or descending):
                desired = 'up' if climbing else 'down'
                choices = [r for r in self._vermin_routes(oid, b) if r['action'] == desired]
                route = choices[0] if choices else None
            elif not s['aggro'] and self.now >= s['next_hop']:
                choices = self._vermin_routes(oid, b)
                route = self.host.rng.choice(choices) if choices else None
                s['next_hop'] = self.now+self._range('vermin_hop_s')
            if route:
                s['air_vx'] = vx = 0.
                b.update(vy=-route['speed'] if route['action'] == 'up' else 0., grounded=False)
                if route['action'] == 'down':
                    b['drop_support'] = route['support']
                    b['y'] += 1
                    place(self.host, oid, (b['x'], b['y']))
                s['next_jump'] = self.now+self._range('vermin_jump_cooldown_s')
        elif 'air_vx' in s:
            vx = s['air_vx']
        drop_support = b.get('drop_support')
        if drop_support is not None:
            support = self.host.actors.get(drop_support)
            if support is None or b['y'] > support['pos'][1]+support['props'].get('hmp', 0)+1:
                b.pop('drop_support', None)
            elif support['props'].get('terraintype') == 2:
                # Filter exactly this one-way support from a local view; all
                # solid walls, other floors and other vermin keep full contact.
                terrain = copy.copy(terrain)
                terrain.surfaces = [r for r in terrain.surfaces if r[0] != drop_support]
                terrain.triangles = [r for r in terrain.triangles if r[0] != drop_support]
        terrain.step(b, dt, vx, self.config['vermin_gravity_px_s2']*256)
        move(self.host, oid, (b['x'], b['y']), dt,
             pose=(5 if vx else 4) if b.get('grounded') else 3 if b['vy'] < 0 else 1,
             facing=180 if s['direction'] < 0 else 0)

    def _stop_jump(self):
        self._sync_relative_hazards(round(self.host.room_time*1000))
        for oid, row in list(self.host.actors.items()):
            if row['vid'] in JUMP_HAZARDS:
                place(self.host, oid, row['pos'])
                self.tracks.pop(oid, None)
            elif row['vid'] in ('sc.ccoredoor', 'sc.coredoor'):
                prop(self.host, oid, 'vid', 'sc.coredoor')
                prop(self.host, oid, 'interact', 1)
            elif row['vid'] in ('sc.switch', 'sc.switchdone'):
                prop(self.host, oid, 'vid', 'sc.switchdone')
                prop(self.host, oid, 'interact', 0)

    def interact(self, oid, player=None):
        row = self.host.actors.get(oid)
        if row is None or row['vid'] not in ('sc.switch', 'sc.switchdone'):
            return False
        if row['vid'] == 'sc.switchdone' or self.host.instance.flags.get('mid_cleared'):
            return True
        # Interaction is intentional; reaching or approaching never completes it.
        if player is None or distance_to_rect(center(player), rect(row)) > self.config['switch_interact_radius_px']*256:
            return True
        # Authored switches already carry native interactreaction.sound.
        # The recorded activation has no additional server PlaySound; retain
        # a fallback only for custom switches lacking a reaction sound.
        reaction = row['props'].get('interactreaction') or {}
        if not (isinstance(reaction, dict) and reaction.get('sound')):
            self.host.batch.sound(oid, 'sc.switch', offset=self.host._offset())
        self.host.unlock('mid_cleared', switch=oid)
        self._stop_jump()
        return True

    def guardian_defeated(self, oid):
        if int(oid) == self.guardian and self.guardian:
            if not self.host.instance.flags.get('chest_cleared'):
                row = self.host.actors.get(oid) or self.guardian_row
                if row is not None:
                    # Capture before retirement removes the moving body. A
                    # later respawn must never relocate the original reward.
                    self.host.instance.flags['chest_drop_position_mp'] = tuple(row['pos'])
            self.host.unlock('chest_cleared', guardian=self.guardian)
            return True
        return False

    def _release_ledges(self):
        """Start every parked lane once the room has a live player."""
        speed = self.config['mledge_speed_px_s']*256*256/1000
        for oid, (direction, movement) in list(self.mledge_hold.items()):
            row = self.host.actors.get(oid)
            if row is None:
                self.mledge_hold.pop(oid, None)
                continue
            self._movement(oid, movement or dict(vel=dict(xvel=round(direction*speed))))
        self.mledge_hold.clear()

    def _stream_ledges(self):
        if not self.mledge_stream or getattr(self, '_mledge_resuming', False):
            return
        streams = self.mledge_stream['streams']
        if not self.mledge_stream_started:
            if not players(self.host):
                return
            self.mledge_stream_started = True
            for stream in streams:
                stream['next'] = self.now
        for stream in streams:
            if self.now+1e-9 < stream['next']:
                continue
            self._stream_birth(stream, self.host.rng.choice(stream['joint_samples']))
            stream['next'] = self.now+self.host.rng.choice(stream['interval_ms_pool'])/1000.
            # Delayed frames never replay a backlog of births.

    def _stream_birth(self, stream, sample, age=0.):
        y, width, height, velocity, vid = sample
        x = stream['spawn_xmp']-(width if stream['direction'] > 0 else 0)+velocity*age
        edge = stream['retire_xmp']
        if stream['direction'] > 0 and x > edge or stream['direction'] < 0 and x < edge:
            return
        # All sampled velocities are whole MP/ms. Re-encode them in the
        # native fixed-point unit used by the platform's MOVE track.
        movement = dict(vel=dict(xvel=round(velocity*256/1000)))
        oid = self._spawn(vid, (x, y), 0, dict(wmp=width, hmp=height, z=62,
                          facing=0 if velocity > 0 else 180, terraintype=2, move=movement))
        self.states[oid]['stream_direction'] = stream['direction']

    def _resume_ledges(self):
        if not self.mledge_stream or not self.mledge_stream_started:
            return
        for stream in self.mledge_stream['streams']:
            while stream['next'] < self.now:
                born = stream['next']
                self._stream_birth(stream, self.host.rng.choice(stream['joint_samples']), self.now-born)
                stream['next'] += self.host.rng.choice(stream['interval_ms_pool'])/1000.
        # Only surviving platforms are restored at their current positions;
        # no old birth burst or already-retired platform is sent on re-entry.

    def _mledge(self, oid, s, dt):
        if oid in self.mledge_hold:
            return
        row = self.host.actors[oid]
        movement = row['props'].get('move', {})
        velocity = movement.get('vel', {}).get('xvel')
        direction = (-1 if velocity < 0 else 1) if velocity else s.get('direction', 1)
        if oid not in self.tracks:
            # No native lane supplied: explicit FIT streaming motion.
            velocity = round(direction*self.config['mledge_speed_px_s']*256*256/1000)
            self._movement(oid, dict(vel=dict(xvel=velocity)))
        x = row['pos'][0]
        width = row['props'].get('wmp', 0)
        if self.mledge_stream:
            stream = next(s for s in self.mledge_stream['streams'] if s['direction'] == direction)
            edge = stream['retire_xmp']
            if direction > 0 and x > edge or direction < 0 and x < edge:
                self.retire(oid, 'INTERNAL')
                self.tracks.pop(oid, None)
                self.states.pop(oid, None)
            return
        bounds = self.mledge_bounds
        if not bounds:
            return
        if direction > 0 and x > bounds['right'] or direction < 0 and x+width < bounds['left']:
            # Captured platforms stream in/out; they do not bounce. Replenish
            # the same native speed/height lane, with a new object identity.
            point = (bounds['left']-width if direction > 0 else bounds['right'], s['origin'][1])
            props = copy.deepcopy(row['props'])
            props.pop('xmp', None)
            props.pop('ymp', None)
            vid = row['vid']
            native = s.get('native_move')
            self.retire(oid, 'INTERNAL')
            child = self._spawn(vid, point, 0, props)
            if native:
                self.states[child]['native_move'] = copy.deepcopy(native)
                self._movement(child, copy.deepcopy(native))

    def resume(self, elapsed_s):
        """Reconcile an unoccupied interval without replaying attack history.

        The session advances room/wire clocks first. Existing native lanes and
        minimum existence deadlines age; there is no catch-up projectile burst.
        """
        self.now = self.host.room_time
        # A zero-time return may hit pre_physics's same-clock guard. Never
        # carry bodies again using the displacement from the last live tick.
        self.platform_deltas = {}
        self._mledge_resuming = True
        self.pre_physics(0.)
        self._mledge_resuming = False
        for oid, s in list(self.states.items()):
            row = self.host.actors.get(oid)
            if row is None:
                continue
            vid = row['vid']
            if vid == 'sc.dropwall':
                if s['phase'] == 'falling':
                    # Recover the physical landing time from the same native
                    # acceleration/terminal lane, rather than counting the
                    # entire absence as time spent already on the floor.
                    distance = max(0., s['floor']-s['origin'][1])
                    acceleration = self.config['wall_yaccel']*1000000/256
                    maximum = self.config['wall_maxyvel']*1000/256
                    ramp_s = maximum/acceleration
                    ramp_distance = .5*acceleration*ramp_s*ramp_s
                    flight = math.sqrt(2*distance/acceleration) if distance <= ramp_distance else ramp_s+(distance-ramp_distance)/maximum
                    started = s.get('started', self.now-elapsed_s)
                    age = max(0., self.now-started)
                    ramp = min(age, ramp_s)
                    fallen = .5*acceleration*ramp*ramp+maximum*max(0., age-ramp_s)
                    place(self.host, oid, (row['pos'][0], min(s['floor'], s['origin'][1]+fallen)))
                    s['velocity'] = min(maximum, acceleration*age)
                    if self.now >= started+flight:
                        s.update(phase='settled', settled=started+flight,
                                 open_at=started+flight+self.config['wall_min_settled_s'])
                    else:
                        set_motion(self.host, oid, dict(vel=dict(yvel=round(s['velocity']*256/1000),
                                                               yaccel=self.config['wall_yaccel'], maxyvel=self.config['wall_maxyvel'])))
                if s['phase'] == 'settled':
                    self._wall(oid, s, 0., None)
            elif vid == 'sc.enginecore':
                self.engine.resume(s)
            elif vid == 'sc.enginespawner':
                s['next'] = self.now+self._range('engine_spawner_fire_s')
            elif vid == 'sc.tshooter':
                if s['phase'] == 'windup':
                    clear(self.host, oid, 'animprops')
                    s.update(phase='idle', next=self.now+self._range('tshooter_respawn_s'))
                # Keep an existing death deadline through an absence. A live
                # resident child blocks replacement without aging a cooldown.
                self._tshooter_child(oid, s)
            elif vid == 'sc.hive':
                s['next'] = self.now+self._range('hive_spawn_s')
            elif vid == 'sc.enginefireseed' and self.now >= s['plant_at']:
                self._plant_seed(oid, s)
            elif vid in WORKERS and s.get('destination') is not None:
                # Finish at most the already-started leg. Do not simulate a
                # sequence of new pickup/placement decisions while absent.
                self._worker(oid, s, elapsed_s)
                place(self.host, oid, row['pos'])
                if s.get('destination') is not None:
                    destination = s['destination']
                    speed = s.get('leg_speed', self.config['worker_speed_px_s']*256)
                    duration = max(1, round(math.dist(row['pos'], destination)/max(1., speed)*1000))
                    set_motion(self.host, oid, dict(to=dict(xmp=round(destination[0]), ymp=round(destination[1]), dt=duration)))
            elif vid.startswith('sc.mledge'):
                self._mledge(oid, s, 0.)
        self._sync_carried_books()

        self._resume_ledges()

    def step(self, dt):
        self.now = self.host.room_time
        self.pre_physics(dt)
        terrain = None
        for oid, s in list(self.states.items()):
            row = self.host.actors.get(oid)
            if row is None:
                self.states.pop(oid, None)
                continue
            vid = s['vid']
            target = nearest(self.host, (row['pos'][0]+row['props'].get('wmp', 0)/2,
                                         row['pos'][1]+row['props'].get('hmp', 0)/2))
            if vid == 'sc.enginecore' and row['vid'] == vid:
                self.engine.step(oid, s)
            elif vid == 'sc.enginefireseed' and self.now >= s['plant_at']:
                self._plant_seed(oid, s)
            elif vid == 'sc.enginespawner':
                self._spawner(oid, s)
            elif vid == 'sc.dropwall':
                self._wall(oid, s, dt, target)
            elif vid == 'sc.tshooter':
                if self._tshooter_child(oid, s) is not None:
                    continue
                if s['phase'] == 'windup' and self.now >= s['release_at']:
                    self._tshot_spawn(oid, s)
                elif s['phase'] == 'idle' and self.now >= s['next']:
                    animate(self.host, oid, 1, 990)
                    s.update(phase='windup', release_at=self.now+self.config['tshooter_windup_s'])
            elif vid == 'sc.tshot':
                self._tshot(oid, s, dt, target)
            elif vid in WORKERS:
                self._worker(oid, s, dt)
            elif vid == 'sc.hive':
                self._hive(oid, s, target)
            elif vid == 'sc.vermin':
                if terrain is None:
                    terrain = TriangleTerrain({i: dict(r['props'], xmp=r['pos'][0], ymp=r['pos'][1])
                                               for i, r in self.host.actors.items() if r['props'].get('terraintype') in (1, 2)})
                self._vermin(oid, s, dt, target, terrain)
            elif vid.startswith('sc.mledge'):
                self._mledge(oid, s, dt)
            elif vid in ('sc.switch', 'sc.switchdone') and oid not in self.tracks:
                ox, oy = s['origin']
                y = oy+math.sin(self.now*2*math.pi/self.config['switch_float_period_s'])*self.config['switch_float_px']*256
                move(self.host, oid, (ox, y), dt)
        if self.guardian_row and self.guardian_row.get('_sc_killed'):
            self.guardian_defeated(self.guardian)

    def _dead_core(self, oid, engine):
        row = self.host.actors[oid]
        row['hp'] = None
        clear(self.host, oid, 'hitpoints')
        clear(self.host, oid, 'damage')
        if engine:
            prop(self.host, oid, 'terraintype', 0)
            prop(self.host, oid, 'vid', 'sc.denginecore')
            animate(self.host, oid, 0, 990, 'sc.enginecoredeath')
            for child, body in list(self.host.actors.items()):
                if body['vid'] == 'sc.engineball':
                    self.retire(child, 'INTERNAL')
            self.host.record('sc_engine_destroyed', id=oid, spawners_preserved=True)
        else:
            prop(self.host, oid, 'vid', 'sc.dcore')
            animate(self.host, oid, 0, 2970, 'sc.coredeath')
            self.host.unlock('gen_destroyed', core=oid)

    def _killed(self, oid, native=False):
        row, s = self.host.actors[oid], self.states[oid]
        vid = row['vid']
        if vid == 'sc.dropwall':
            # OBS: the visible native wall can keep negative HP while its
            # removetype gate is closed. A client destroy hint must not
            # manufacture the USER damage condition in the local ledger.
            if (row.get('hp') is not None and row['hp'] <= 0 and
                    s.get('open_at') is not None and self.now+1e-9 >= s['open_at']):
                prop(self.host, oid, 'removetype', 1)
                row['_sc_killed'] = True
                self.retire(oid, 'HITPOINTS', native)
            return
        row['_sc_killed'] = True
        if vid in ('sc.enginecore', 'sc.core'):
            self._dead_core(oid, vid == 'sc.enginecore')
        elif vid in WORKERS:
            if s.get('pickup') in self.states:
                self.states[s['pickup']].pop('reserved', None)
            self._book_place(oid, (row['pos'][0]+16*256, row['pos'][1]+64*256))
            point = self.host.rng.choice(self.worker_sites or [s['origin']])
            props = {k: copy.deepcopy(v) for k, v in row['props'].items() if k not in ('xmp', 'ymp', 'move', 'animprops', 'pose')}
            props['hitpoints'] = 200
            self.retire(oid, 'HITPOINTS', native)
            self._spawn(vid, point, 0, props)
        else:
            self.guardian_defeated(oid)
            self.retire(oid, 'HITPOINTS', native)

    def feedback(self, event):
        oid = int(event.get('victim', event.get('objid', 0)) or 0)
        if oid not in self.owned and oid not in self.retired:
            return False
        if oid in self.retired:
            return True
        identity = event.get('event_id', event.get('seq'))
        key = (event.get('kind'), oid, str(identity))
        if identity is not None:
            if key in self.seen:
                return True
            self.seen.add(key)
        elif any(event is old for old in self.recent):
            return True
        self.recent = (self.recent+[event])[-256:]
        row = self.host.actors.get(oid)
        if row is None:
            return True
        self.now = self.host.room_time
        kind = event.get('kind')
        if kind == 'interact':
            return self.interact(oid, event.get('player', getattr(self.host, 'player', None)))
        if row['vid'] in ('sc.dcore', 'sc.denginecore'):
            return True
        if row['vid'] == 'sc.tshooter' and kind in ('damage', 'destroy'):
            # The permanent device is not its destructible child. Native
            # damage/destroy hints must not remove it or orphan its live ball.
            return True
        if kind == 'damage':
            if row.get('hp') is None or row['props'].get('invulnerable'):
                return True
            value = max(0, int(event.get('value', 0)))
            before = row['hp']
            if row['vid'] == 'sc.dropwall':
                row['hp'] -= value
                self.states[oid]['damage'] += value
            else:
                row['hp'] = max(0, row['hp']-value)
            prop(self.host, oid, 'hitpoints', row['hp'])
            if row['vid'] == 'sc.enginecore':
                self.engine.damage(self.states[oid], before, row['hp'])
            if row['hp'] <= 0:
                self._killed(oid)
        elif kind == 'destroy':
            reason = event.get('reason', 'INTERNAL')
            if reason in (2, 'HITPOINTS', 'hitpoints') and row.get('hp') is not None:
                self._killed(oid, native=True)
            elif row['vid'] == 'sc.enginefireseed':
                self._plant_seed(oid, self.states[oid])
            elif row['vid'] in ('sc.enginefire', 'sc.vermin'):
                self.retire(oid, reason, native=True)
            # Native blocked/lifetime hints never remove permanent mechanisms.
        return True

    def retire(self, oid, reason='INTERNAL', native=False):
        state = self.states.pop(oid, None)
        row = self.host.actors.get(oid)
        parent = None
        if state and state['vid'] == 'sc.tshot':
            parent = state.get('owner', (row or {}).get('creator'))
        self.tracks.pop(oid, None)
        super().retire(oid, reason, native)
        if parent in self.states and self.states[parent]['vid'] == 'sc.tshooter':
            self.now = self.host.room_time
            self._tshooter_child(parent, self.states[parent])

    def expire(self, oid):
        if oid not in self.owned:
            return False
        row = self.host.actors.get(oid)
        if row and row['vid'] == 'sc.enginefireseed':
            self._plant_seed(oid, self.states[oid])
        elif row and row['vid'] in ('sc.enginefire', 'sc.enginefireub'):
            self.retire(oid, 'LIFETIME')
        else:
            # Native mechanism bodies have no local fabricated timeout.
            getattr(self.host, '_lifetimes', {}).pop(oid, None)
        return True

    def status(self):
        return dict(objects=len(self.states), movingPlatforms=len(self.tracks),
                    inventory=dict(Counter(self.host.actors[i]['vid'] for i in self.states if i in self.host.actors)),
                    chestGuardian=self.guardian, mledgeBoundsFit=self.mledge_bounds,
                    evidence='D native fields/actions; USER progression and damage gates; configurable FIT decisions',
                    fit=dict(self.config))
