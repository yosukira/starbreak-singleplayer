"""SC room mechanisms, responding to current players and real damage inputs.

D values retain native fields and observed action timings. FIT values below
are local policies where captures do not establish a retail decision rule.
They are adjustable through recipe.metadata.mechanics / host.mechanics_config.
"""
import copy
import math
from collections import Counter

from ..efb_run.abilities import Ownership, center, nearest, rect, prop, move, changed
from ..efb_run.physics import TriangleTerrain
from .physics import geometry, sweep as solid_sweep
from .mechanic_helpers import (MechanismTrack, animate, clear, distance_to_rect,
                               legal_book_sites, place, set_motion, stop)

DEFAULTS = {
    # D native bodies and attack lanes; these are independent from Gen HP.
    'engine_hp': 60000, 'gen_hp': 5000, 'wall_hp': 5000,
    'wall_yaccel': 40, 'wall_maxyvel': 50000,
    'engine_seed_count': 3, 'engine_seed_wire_speed': 26000,
    'engine_spawner_birth_s': .416, 'engine_spawner_hp': 300,
    'engine_spawner_orbit_ms': 32000, 'engine_fire_lifetime_s': 4.5,
    'engine_ring_count': 10, 'engine_ring_wire_speed': 20000,
    'engine_ring_period_ms': 400, 'engine_ring_amplitude': 20480,
    'tshooter_windup_s': .416,
    # FIT: observed stop-to-removetype range 7.005..7.049s. Damage accumulates
    # before this minimum existence gate; it is never a delayed damage gate.
    'wall_min_settled_s': 7.05,
    'wall_trigger_x_px': 160, 'wall_trigger_y_px': 640,
    'wall_fallback_drop_px': 512,
    # FIT decision/range/cadence; not a replay of either captured encounter.
    'engine_active_radius_px': 1800, 'engine_first_action_s': 1.,
    'engine_summon_cooldown_s': (5.10, 5.14),
    'engine_shoot_cooldown_s': (1.02, 1.05),
    'engine_cycle_pause_s': 3., 'engine_ring_bursts': 3,
    'engine_seed_flight_s': (2.35, 3.95),
    'engine_seed_radius_px': (900, 1400),
    'engine_spawner_fire_s': (1.0, 1.05),
    'tshooter_range_px': 1800, 'tshooter_initial_s': (.356, 1.5),
    'tshooter_respawn_s': (3., 6.),
    'tshot_speed_px_s': 200, 'tshot_accel_px_s2': 600,
    'tshot_turn_pause_s': (.25, .65), 'tshot_decide_s': (1., 2.5),
    'worker_speed_px_s': 124, 'worker_wait_s': (.25, 1.),
    'worker_carry_radius_px': 900, 'worker_place_random_nearest': 6,
    'worker_place_search_cells': 24,
    'mledge_speed_px_s': 150,
    'jump_turn_deg_s': 90, 'switch_float_px': 12, 'switch_float_period_s': 2.5,
    'switch_interact_radius_px': 160,
    'hive_active_radius_px': 1200, 'hive_max_children': 4,
    'hive_spawn_s': (4., 7.), 'hive_first_s': (.5, 2.),
    'vermin_speed_px_s': 180, 'vermin_jump_px_s': 390,
    'vermin_gravity_px_s2': 750, 'vermin_jump_cooldown_s': (1.3, 2.5),
}

WORKERS = {'sc.libworker', 'sc.libworkerb', 'sc.libworkerr'}
BOOKS = {'sc.book', 'sc.evilbook'}
JUMP_HAZARDS = {'sc.peg32', 'sc.evilpeg32', 'sc.wheel128', 'sc.wheel256'}
MECHANISMS = {'sc.enginecore', 'sc.denginecore', 'sc.engineball', 'sc.enginefireseed',
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
        self._physics_clock = None
        self.platform_deltas = {}
        self.guardian = int(host.recipe.get('metadata', {}).get('chest_guardian') or 0)
        self.guardian_row = None
        self.worker_sites = []
        self.mledge_bounds = host.recipe.get('metadata', {}).get('sc_mledge_bounds')

    def _range(self, key):
        value = self.config[key]
        return self.host.rng.uniform(*value) if isinstance(value, (tuple, list)) else float(value)

    def prepare(self):
        for oid in list(self.host.actors):
            self.add_actor(oid)
        self.guardian_row = self.host.actors.get(self.guardian)
        self.worker_sites = [tuple(r['pos']) for r in self.host.actors.values() if r['vid'] in WORKERS]
        ledges = [r for r in self.host.actors.values() if r['vid'].startswith('sc.mledge')]
        if ledges and not self.mledge_bounds:
            # FIT streaming boundary, never inferred as a bounce instruction.
            self.mledge_bounds = dict(left=min(r['pos'][0] for r in ledges)-256*256,
                                      right=max(r['pos'][0]+r['props'].get('wmp', 0) for r in ledges)+256*256)
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
        if vid not in MECHANISMS and not vid.startswith('sc.mledge'):
            return False
        self.own(oid)
        s = self.states[oid] = dict(vid=vid, origin=tuple(row['pos']), next=self.now, phase='idle')
        movement = row['props'].get('move')
        if movement and not movement.get('relativeto'):
            self.tracks[oid] = MechanismTrack(row['pos'], movement, round(self.now*1000))
        if vid in ('sc.enginecore', 'sc.core'):
            hp = self.config['engine_hp' if vid == 'sc.enginecore' else 'gen_hp']
            row['hp'] = hp
            prop(self.host, oid, 'hitpoints', hp)
            s.update(next=self.now+self.config['engine_first_action_s'], action=0)
            if vid == 'sc.enginecore':
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
            s.update(body=dict(x=x, y=y, w=w, h=h, vy=0., grounded=False),
                     next_jump=self.now+self._range('vermin_jump_cooldown_s'))
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
        self.tracks[oid] = MechanismTrack(self.host.actors[oid]['pos'], movement, round(self.now*1000))

    def pre_physics(self, dt):
        clock = round(self.host.room_time*1000)
        if clock == self._physics_clock:
            return
        self._physics_clock = clock
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

    def _core_center(self, owner=None):
        row = self.host.actors.get(owner)
        if row is None:
            row = next((r for r in self.host.actors.values() if r['vid'] in ('sc.enginecore', 'sc.denginecore')), None)
        if row:
            x, y, w, h = rect(row)
            return x+w/2, y+h/2
        return 0., 0.

    def _engine_action(self, oid, s):
        # FIT two summons then three radial bursts, with observed native lanes.
        cx, cy = self._core_center(oid)
        action = s['action']
        if action < 2:
            animate(self.host, oid, 2, 990, 'sc.enginesummon')
            for _ in range(self.config['engine_seed_count']):
                angle = self.host.rng.uniform(0, 2*math.pi)
                speed = self.config['engine_seed_wire_speed']
                movement = dict(vel=dict(xvel=round(math.cos(angle)*speed), yvel=round(math.sin(angle)*speed)))
                child = self._spawn('sc.enginefireseed', (cx-8*256, cy-8*256), oid)
                self._movement(child, movement)
                self.states[child]['core'] = oid
            s['next'] = self.now+self._range('engine_summon_cooldown_s')
        else:
            animate(self.host, oid, 1, 990, 'sc.engineshoot')
            rotation = self.host.rng.uniform(0, 360)
            for i in range(self.config['engine_ring_count']):
                movement = {'sin': dict(xmp=round(cx), ymp=round(cy), vel=self.config['engine_ring_wire_speed'],
                                        period=self.config['engine_ring_period_ms'], amp=self.config['engine_ring_amplitude'],
                                        angle=round(rotation+i*360/self.config['engine_ring_count']))}
                child = self._spawn('sc.enginefire', (cx-16*256, cy-16*256), oid)
                self._movement(child, movement)
            s['next'] = self.now+self._range('engine_shoot_cooldown_s')
        s['action'] += 1
        if s['action'] >= 2+self.config['engine_ring_bursts']:
            s.update(action=0, next=self.now+self.config['engine_cycle_pause_s'])

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
            if target:
                px, py = center(target)
                x, y, w, h = rect(row)
                if x-self.config['wall_trigger_x_px']*256 <= px <= x+w+self.config['wall_trigger_x_px']*256 and y-h <= py <= y+h+self.config['wall_trigger_y_px']*256:
                    self._start_wall(oid, s)
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
                self.retire(oid, 'HITPOINTS')

    def _tshot_spawn(self, oid, s):
        row = self.host.actors[oid]
        x, y, w, h = rect(row)
        heading = int(row['props'].get('facing', 90))%360
        angle = math.radians(heading)
        child = self._spawn('sc.tshot', (x+54*256*math.cos(angle), y+54*256*math.sin(angle)), oid,
                            dict(facing=heading, lifetime=0))
        self.states[child]['owner'] = oid
        s.update(child=child, phase='idle', next=self.now+self._range('tshooter_respawn_s'))

    def _tshot(self, oid, s, dt, target):
        row = self.host.actors[oid]
        if s.get('wait_until', 0) > self.now:
            return
        if self.now >= s['next']:
            headings = [0, 90, 180, 270]
            self.host.rng.shuffle(headings)
            if target:
                px, py = center(target)
                x, y, w, h = rect(row)
                headings.sort(key=lambda a: -((px-x-w/2)*math.cos(math.radians(a))+(py-y-h/2)*math.sin(math.radians(a))))
            s['heading'] = headings[0]
            s['next'] = self.now+self._range('tshot_decide_s')
        s['speed'] = min(self.config['tshot_speed_px_s']*256, s['speed']+self.config['tshot_accel_px_s2']*256*dt)
        x, y, w, h = rect(row)
        angle = math.radians(s['heading'])
        end = (x+math.cos(angle)*s['speed']*dt, y+math.sin(angle)*s['speed']*dt)
        terrain = geometry(self.host)
        hit = solid_sweep(terrain, (x, y), end, (w, h), ignore=(oid, s.get('owner', 0)))
        if hit:
            f = max(0., hit[0]-1e-6)
            end = (x+(end[0]-x)*f, y+(end[1]-y)*f)
            candidates = [a for a in (0, 90, 180, 270) if a != s['heading']]
            self.host.rng.shuffle(candidates)
            for a in candidates:
                probe = (end[0]+math.cos(math.radians(a))*4*256, end[1]+math.sin(math.radians(a))*4*256)
                if not solid_sweep(terrain, end, probe, (w, h), ignore=(oid, s.get('owner', 0))):
                    s['heading'] = a
                    break
            s.update(speed=0., wait_until=self.now+self._range('tshot_turn_pause_s'),
                     next=self.now+self._range('tshot_decide_s'))
        move(self.host, oid, end, dt, pose=1 if hit else 3, facing=s['heading'])

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
        point = (x-48*256 if row['props'].get('facing') == 180 else x+w, y+h-48*256)
        child = self._spawn('sc.vermin', point, oid)
        s['children'].add(child)
        s['next'] = self.now+self._range('hive_spawn_s')

    def _vermin(self, oid, s, dt, target, terrain):
        b = s['body']
        vx = 0.
        if target:
            px, py = center(target)
            direction = -1 if px < b['x']+b['w']/2 else 1
            vx = direction*self.config['vermin_speed_px_s']*256
            if b.get('grounded') and self.now >= s['next_jump'] and (b.get('blocked') or py < b['y'] or abs(px-b['x']) < 200*256):
                b['vy'] = -self.config['vermin_jump_px_s']*256
                s['next_jump'] = self.now+self._range('vermin_jump_cooldown_s')
        terrain.step(b, dt, vx, self.config['vermin_gravity_px_s2']*256)
        move(self.host, oid, (b['x'], b['y']), dt,
             pose=5 if b.get('grounded') else 1 if b['vy'] < 0 else 3, facing=180 if vx < 0 else 0)

    def _stop_jump(self):
        for oid, row in list(self.host.actors.items()):
            if row['vid'] in JUMP_HAZARDS:
                stop(self.host, oid)
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
            self.host.unlock('chest_cleared', guardian=self.guardian)
            return True
        return False

    def _mledge(self, oid, s, dt):
        row = self.host.actors[oid]
        movement = row['props'].get('move', {})
        velocity = movement.get('vel', {}).get('xvel')
        direction = (-1 if velocity < 0 else 1) if velocity else s.get('direction', 1)
        if oid not in self.tracks:
            # No native lane supplied: explicit FIT streaming motion.
            velocity = round(direction*self.config['mledge_speed_px_s']*256*256/1000)
            self._movement(oid, dict(vel=dict(xvel=velocity)))
        bounds = self.mledge_bounds
        if not bounds:
            return
        x = row['pos'][0]
        width = row['props'].get('wmp', 0)
        if direction > 0 and x > bounds['right'] or direction < 0 and x+width < bounds['left']:
            # Captured platforms stream in/out; they do not bounce. Replenish
            # the same native speed/height lane, with a new object identity.
            point = (bounds['left']-width if direction > 0 else bounds['right'], s['origin'][1])
            props = copy.deepcopy(row['props'])
            props.pop('xmp', None)
            props.pop('ymp', None)
            vid = row['vid']
            self.retire(oid, 'INTERNAL')
            self._spawn(vid, point, 0, props)

    def resume(self, elapsed_s):
        """Reconcile an unoccupied interval without replaying attack history.

        The session advances room/wire clocks first. Existing native lanes and
        minimum existence deadlines age; there is no catch-up projectile burst.
        """
        self.now = self.host.room_time
        self.pre_physics(0.)
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
                s['next'] = self.now+self._range('engine_summon_cooldown_s')
            elif vid == 'sc.enginespawner':
                s['next'] = self.now+self._range('engine_spawner_fire_s')
            elif vid == 'sc.tshooter':
                if s['phase'] == 'windup':
                    clear(self.host, oid, 'animprops')
                s.update(phase='idle', next=self.now+self._range('tshooter_respawn_s'))
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
            if vid == 'sc.enginecore' and row['vid'] == vid and self.now >= s['next'] and target and distance_to_rect(center(target), rect(row)) <= self.config['engine_active_radius_px']*256:
                self._engine_action(oid, s)
            elif vid == 'sc.enginefireseed' and self.now >= s['plant_at']:
                self._plant_seed(oid, s)
            elif vid == 'sc.enginespawner':
                self._spawner(oid, s)
            elif vid == 'sc.dropwall':
                self._wall(oid, s, dt, target)
            elif vid == 'sc.tshooter':
                if s['phase'] == 'windup' and self.now >= s['release_at']:
                    self._tshot_spawn(oid, s)
                elif s['phase'] == 'idle' and self.now >= s['next'] and s.get('child') not in self.host.actors and target and distance_to_rect(center(target), rect(row)) <= self.config['tshooter_range_px']*256:
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
            elif vid in JUMP_HAZARDS and not self.host.instance.flags.get('mid_cleared') and 'wheel' in vid:
                prop(self.host, oid, 'facing', round((row['props'].get('facing', 0)+self.config['jump_turn_deg_s']*dt)%360))
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
        row['_sc_killed'] = True
        if vid in ('sc.enginecore', 'sc.core'):
            self._dead_core(oid, vid == 'sc.enginecore')
        elif vid == 'sc.dropwall':
            # Lethal input before the gate remains accumulated on the live wall.
            row['hp'] = 0
            prop(self.host, oid, 'hitpoints', 0)
            if s.get('open_at') is not None and self.now+1e-9 >= s['open_at']:
                self.retire(oid, 'HITPOINTS', native)
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
        if kind == 'damage':
            if row.get('hp') is None or row['props'].get('invulnerable'):
                return True
            value = max(0, int(event.get('value', 0)))
            row['hp'] = max(0, row['hp']-value)
            prop(self.host, oid, 'hitpoints', row['hp'])
            if row['vid'] == 'sc.dropwall':
                self.states[oid]['damage'] += value
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
        self.states.pop(oid, None)
        self.tracks.pop(oid, None)
        super().retire(oid, reason, native)

    def expire(self, oid):
        if oid not in self.owned:
            return False
        row = self.host.actors.get(oid)
        if row and row['vid'] == 'sc.enginefireseed':
            self._plant_seed(oid, self.states[oid])
        elif row and row['vid'] == 'sc.enginefire':
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
