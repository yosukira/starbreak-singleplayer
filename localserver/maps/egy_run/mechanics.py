"""EGY exploration mechanisms, independent of room routing and enemy AI.

D values: danger animation 990 ms, fire damage30/lifetime1572, fgen HP3000,
barrel HP100/rolling150/blast1000, hook HP10, falling large1000/medium500.
SIM values below are configurable policies, not recovered server constants.
Only native collision objects deal damage; this module never ticks player HP.
Persistent mechanism state lives on actors so revisiting a room preserves it.
"""
import copy
import math

from ..hulk_mid.scene import normalize

SIM_DEFAULTS = dict(fire_initial_delay_s=2.0, fire_phase_spread_s=2.0,
                    fire_cooldown_s=4.0, sensor_radius_mp=240 * 256,
                    sensor_delay_s=.75, cargo_small_damage=250,
                    field_pair_tolerance_mp=1024)
FIRE_WARNING_S = .990
FIRE_LIFETIME_MS = 1572
GRAVITY = 340000.0
TERMINAL = 160000.0
BARREL_SPEED = 59000.0
BARREL_HOP = -78000.0
CARGO_DAMAGE = {'hulk.cargopodlarge': 1000, 'hulk.cargopodmedium': 500}
BARRELS = {'hulk.standingbarrel', 'hulk.leaningbarrel', 'hulk.rollingbarrel'}
HOOKS = {'hulk.cranehooklarge', 'hulk.cranehooksmall'}
SENSORS = {'hulk.unstableseekercanister'}
REASONS = {1: 'INTERNAL', 2: 'HITPOINTS', 3: 'SHIELD', 4: 'DAMAGES',
           5: 'BLOCKED', 6: 'LIFETIME', 7: 'CANCEL'}

def _rect(row):
    p = row['props']
    return (*row['pos'], p.get('wmp', 0), p.get('hmp', 0))

def _value(player, key, default=0):
    return player.get(key, default) if isinstance(player, dict) else getattr(player, key, default)

class ExplorationMechanics:
    def __init__(self, host):
        self.host = host
        from .presentation import Presentation
        self.presentation = getattr(host, 'presentation', None) or Presentation(host)
        self.config = dict(SIM_DEFAULTS, **getattr(host, 'mechanics_config', {}))
        self.rows = {}
        self.states = {}
        self.effects = {}

    def _record(self, kind, **values):
        self.host.record(kind, **values)

    def _created_at_end(self, oid):
        callback = getattr(self.host, 'created_at_end', None)
        if callback is not None:
            callback(oid)

    def _lifetime_due(self, oid, expires):
        callback = getattr(self.host, 'wire_lifetime_due', None)
        due = callback(oid) if callback is not None else None
        return self.host.room_time + 1e-9 >= expires if due is None else due

    def _state(self, oid, row, kind):
        row['_mechanic'] = kind
        state = row.setdefault('_mechanic_state', dict(kind=kind))
        state.setdefault('kind', kind)
        self.rows[oid], self.states[oid] = row, state
        return state

    def _template(self, vid):
        try:
            p = self.host.pack.props(vid)
        except KeyError as error:
            raise ValueError('EGY mechanism requires missing template: ' + vid) from error
        if not p or p.get('vid', vid) != vid:
            raise ValueError('EGY mechanism requires missing template: ' + vid)
        return copy.deepcopy(p)

    def _set(self, oid, **values):
        row = self.rows.get(oid) or self.host.actors.get(oid)
        if row is None or row.get('dead'):
            return
        for key, value in values.items():
            row['props'][key.lower()] = value
            if key == 'VID':
                row['vid'] = value
            self.host.batch.modify(oid, key, offset=self.host._offset(),
                                   **({'boolval': value} if isinstance(value, bool) else
                                      {'stringval': value} if isinstance(value, str) else
                                      {'intval': int(value)}))

    def _retire(self, oid, reason='INTERNAL', native=False):
        row = self.rows.get(oid) or self.host.actors.get(oid)
        state = self.states.get(oid)
        if state and state.get('retired'):
            return
        if state is not None:
            state['retired'] = True
        self.host._retire(oid, reason=reason, native=native)
        if row is not None:
            row['dead'] = True

    def prepare(self):
        """Discover initial actors once; links are persisted, never re-guessed per tick."""
        now = self.host.room_time
        for oid, row in list(self.host.actors.items()):
            if row.get('dead'):
                continue
            self.presentation.body_created(oid)
            vid = row['vid']
            if vid.startswith('hulk.danger') and vid not in ('hulk.dangerfire', 'hulk.dangerpanel'):
                self._template('hulk.dangerfire')
                st = self._state(oid, row, 'danger')
                st.setdefault('phase', 'cooldown')
                st.setdefault('deadline', now + self.config['fire_initial_delay_s'] +
                              self.host.rng.random() * self.config['fire_phase_spread_s'])
                st.setdefault('children', [])
                self._set(oid, DAMAGE=0, HITBOXDAMAGE=0)
            elif vid == 'hulk.ffield':
                self._state(oid, row, 'field')
            elif vid == 'hulk.fgen':
                self._state(oid, row, 'generator')
                row['hp'] = row.get('hp') if row.get('hp') is not None else row['props'].get('hitpoints', 3000)
            elif vid in BARRELS:
                self._template('hulk.barrelx')
                self._template('hulk.rollingbarrel')
                st = self._state(oid, row, 'barrel')
                st.setdefault('phase', 'rolling' if vid.endswith('rollingbarrel') else 'standing')
                if st['phase'] == 'standing':
                    row['hp'] = row.get('hp') if row.get('hp') is not None else row['props'].get('hitpoints', 100)

                    self._set(oid, INTERACT=1)
                else:
                    if not row.get('vel') or not any(row['vel']):
                        row['vel'] = [BARREL_SPEED * (-1 if row['props'].get('facing') == 180 else 1), 0.]
            elif vid in HOOKS:
                st = self._state(oid, row, 'hook')
                st.setdefault('cargo', [])
                row['hp'] = row.get('hp') if row.get('hp') is not None else row['props'].get('hitpoints', 10)
            elif vid.startswith('hulk.cargopod'):
                st = self._state(oid, row, 'cargo')
                parent = (row['props'].get('move') or {}).get('relativeto', {}).get('objid')
                st.setdefault('parent', parent)
                st.setdefault('phase', 'hanging' if parent is not None else 'falling')
                if st['phase'] == 'hanging':
                    self._set(oid, DAMAGE=0, HITBOXDAMAGE=0)
            elif vid in SENSORS or row.get('sensor_canister'):
                self._template('hulk.miniseeker')
                st = self._state(oid, row, 'sensor')
                st.setdefault('phase', 'idle')
            elif vid in ('hulk.barrelx', 'hulk.dangerfire'):
                self._effect(oid, row, 'fire' if vid == 'hulk.dangerfire' else 'blast')
        self._pair_fields()

        fire_points = {}
        for oid, st in self.states.items():
            if st['kind'] == 'danger':
                for point in self._fire_points(self.rows[oid]):
                    fire_points.setdefault(tuple(point), oid)
        for child, effect in self.effects.items():
            if effect['kind'] != 'fire':
                continue
            parent = fire_points.get(tuple(self.rows[child]['pos']))
            if parent is None:
                self._record('initial_fire_unpaired', objid=child, evidence='D-preserve-remaining-lifetime')
                continue
            st = self.states[parent]
            if child not in st['children']:
                st['children'].append(child)
            st['deadline'] = max(effect['expires'], st['deadline'] if st['phase']=='active' else 0)
            if effect.get('expires_ms') is not None:
                st['deadline_ms'] = max(effect['expires_ms'], st.get('deadline_ms', 0))
            st['phase'] = 'active'
        for oid, st in list(self.states.items()):
            if st['kind'] != 'cargo' or st.get('phase') != 'hanging':
                continue
            parent = st['parent']
            hook = self.states.get(parent)
            if hook is None or hook['kind'] != 'hook':
                raise ValueError('EGY cargo %s references missing cranehook %s' % (oid, parent))
            if oid not in hook['cargo']:
                hook['cargo'].append(oid)
        self._record('mechanics_prepared', counts=self.status()['counts'], sim=dict(self.config))

    def _pair_fields(self):
        used = set()
        for oid, st in self.states.items():
            if st['kind'] != 'field' or st.get('retired'):
                continue
            field = self.rows[oid]
            if st.get('ends'):
                ends = list(st['ends'])
            else:
                fx, fy, fw, fh = _rect(field)
                ends = []
                for side in (-1, 1):
                    ranked = []
                    for gid, gen in self.rows.items():
                        if self.states[gid]['kind'] != 'generator' or gid in used or gid in ends:
                            continue
                        gx, gy, gw, gh = _rect(gen)
                        error = abs((gx + gw if side < 0 else gx) - (fx if side < 0 else fx + fw))
                        error += abs(gy + gh - fy - fh)
                        ranked.append((error, gid))
                    if not ranked or min(ranked)[0] > self.config['field_pair_tolerance_mp']:
                        raise ValueError('EGY ffield %s has no adjacent %s fgen' % (oid, 'left' if side < 0 else 'right'))
                    ends.append(min(ranked)[1])
                st['ends'] = ends
            for gid in ends:
                if gid not in self.states or self.states[gid]['kind'] != 'generator':
                    raise ValueError('EGY ffield %s has missing saved endpoint %s' % (oid, gid))
                self.states[gid]['field'] = oid
                used.add(gid)

    def _effect(self, oid, row, kind):
        self._created_at_end(oid)
        st = self._state(oid, row, kind)
        st.setdefault('expires', self.host.room_time + row['props'].get('lifetime', 500)/1000.)
        deadline = getattr(self.host, 'wire_lifetime_deadline', lambda _oid: None)(oid)
        if deadline is not None:
            st.setdefault('expires_ms', deadline)
            st['expires'] = st['expires_ms'] / 1000.
        self.effects[oid] = st

    def _open_field(self, gid, native=False):
        st = self.states[gid]
        fid = st.get('field')
        group = self.states.get(fid)
        if group is None:
            raise ValueError('EGY generator %s is not linked to a field' % gid)
        if group.get('opened'):
            return
        group['opened'] = True
        for oid in [fid] + group['ends']:
            self._set(oid, TERRAINTYPE=0, BLOCKTYPE=0, DAMAGE=0)
            self._retire(oid, 'HITPOINTS' if oid == gid else 'INTERNAL', native=native and oid == gid)
        self._record('field_opened', field=fid, endpoints=list(group['ends']), destroyed=gid, evidence='USER')

    def _fire_points(self, row):
        x, y, w, h = _rect(row)

        for iy in range(max(1, math.ceil((h - 12288)/8192) + 1)):
            for ix in range(max(1, math.ceil((w - 12288)/8192) + 1)):
                yield (x + min(ix * 8192, max(0, w - 12288)),
                       y + min(iy * 8192, max(0, h - 12288)))

    def _step_fire(self, oid, st, now):
        if st['phase'] == 'active' and st.get('deadline_ms') is not None:
            due = self.host.wire_end_ms >= st['deadline_ms']
        else:
            due = now + 1e-9 >= st['deadline']
        if not due:
            return
        row = self.rows[oid]
        if st['phase'] == 'cooldown':
            st.update(phase='warning', deadline=now + FIRE_WARNING_S)
            self.presentation.danger_warning(oid, token=st['deadline'])
            self._record('danger_warning', objid=oid, duration=FIRE_WARNING_S, evidence='D')
        elif st['phase'] == 'warning':
            p = self._template('hulk.dangerfire')
            lifetime = p.get('lifetime', FIRE_LIFETIME_MS)
            st.update(phase='active', deadline=now + lifetime/1000., children=[])
            for pos in self._fire_points(row):
                child = self.host._create('hulk.dangerfire', pos, creator=oid,
                    changes=dict(damage=p.get('damage', 30), hitboxdamage=0, lifetime=lifetime))
                self._effect(child, self.host.actors[child], 'fire')
                st['children'].append(child)
            deadlines = [self.effects[child].get('expires_ms') for child in st['children']]
            if deadlines and all(value is not None for value in deadlines):
                st['deadline_ms'] = max(deadlines)
                st['deadline'] = st['deadline_ms'] / 1000.
            self._record('danger_active', objid=oid, children=list(st['children']), evidence='D+SIM-cycle')
        else:
            for child in st['children']:
                if child in self.host.actors and not self.host.actors[child].get('dead'):
                    self._set(child, DAMAGE=0, HITBOXDAMAGE=0)
                    self._retire(child, 'LIFETIME')
            st.update(phase='cooldown', deadline=now + self.config['fire_cooldown_s'], children=[])
            st.pop('deadline_ms', None)
            self._record('danger_cooldown', objid=oid, evidence='SIM')

    def _explode(self, oid, reason='DAMAGES', native=False):
        st, row = self.states[oid], self.rows[oid]
        if st.get('spent'):
            return
        template = self._template('hulk.barrelx')
        st['spent'] = True
        x, y, w, h = _rect(row)
        bw, bh = template.get('wmp', 24576), template.get('hmp', 24576)
        blast = self.host._create('hulk.barrelx', (x + (w-bw)/2, y + (h-bh)/2), creator=oid,
                                  changes=dict(damage=template.get('damage', 1000),
                                               lifetime=template.get('lifetime', 500), ownertype=1))
        self._effect(blast, self.host.actors[blast], 'blast')
        self.presentation.effect_created(blast, 'hulk.barrelx')
        self._retire(oid, reason, native=native)
        self._record('barrel_exploded', objid=oid, blast=blast, evidence='D')

    def _release_cargo(self, oid):
        st, row = self.states[oid], self.rows[oid]
        if st.get('phase') != 'hanging':
            return
        row['pos'] = list(self.host.position(oid))
        row['props'].pop('move', None)
        row['vel'] = [0., 0.]
        st.update(phase='falling', released_at=self.host.room_time)
        damage = CARGO_DAMAGE.get(row['vid'], self.config['cargo_small_damage'])
        self._set(oid, DAMAGE=damage, REMOVETYPE=7)
        self.host.batch.modify(oid, 'MOVE', message=('Properties_Move',
            normalize('Properties_Move', dict(vel=dict(xvel=0, yvel=0)))), offset=self.host._offset())
        self._record('cargo_released', objid=oid, parent=st['parent'], damage=damage,
                     evidence='D' if row['vid'] in CARGO_DAMAGE else 'SIM-small-damage')

    def _players(self):
        players = getattr(self.host, 'players', None)
        if isinstance(players, dict):
            players = list(players.values())
        if callable(players):
            players = players()
        return [p for p in (players or [self.host.player]) if p is not None and _value(p, 'alive', True)]

    def _spawn_sensor(self, oid, native=False):
        st, row = self.states[oid], self.rows[oid]
        if st.get('spent'):
            return
        template = self._template('hulk.miniseeker')
        ai = getattr(self.host, 'enemies', None)
        if ai is None or not hasattr(ai, 'add_actor'):
            raise RuntimeError('EGY sensor miniseeker requires host.enemies.add_actor(oid)')
        x, y, w, h = _rect(row)
        mw, mh = template.get('wmp', 16384), template.get('hmp', 16384)
        child = self.host._create('hulk.miniseeker', (x + (w-mw)/2, y + h-mh), creator=oid,
                                  changes=dict(hitpoints=350, damage=40, ownertype=2, blocktype=2))
        st.update(spent=True, phase='spent', child=child)
        self._retire(oid, 'INTERNAL', native=native)
        self.host.actors[child].pop('_mechanic', None)
        ai.add_actor(child)
        self.presentation.canister_opened(child)
        self._created_at_end(child)
        self._record('sensor_split', objid=oid, child=child, evidence='USER+SIM-delay')

    def _geometry(self, row):
        own = _rect(row)
        result = []
        for rect in self.host.surfaces():
            if len(rect) < 5 or rect[4] not in (1, 2):
                continue
            if all(abs(a-b) < 1 for a,b in zip(rect[:4], own)):
                continue
            result.append(rect)
        return result

    def _fall(self, oid, dt, barrel=False):
        row = self.rows[oid]
        remaining = dt
        while remaining > 1e-8 and not row.get('dead'):
            step = min(.02, remaining)
            remaining -= step
            x, y, w, h = _rect(row)
            vx, vy = row.setdefault('vel', [0., 0.])
            nv = min(TERMINAL, vy + GRAVITY * step)
            nx, ny = x + vx*step, y + (vy+nv)*step/2.
            hit = False
            for sx, sy, sw, sh, terrain in self._geometry(row):

                if y+h <= sy+1 and ny+h >= sy and nv >= 0:
                    frac = max(0., min(1., (sy-y-h)/(ny-y))) if ny != y else 0.
                    crossx = x + (nx-x)*frac
                    if crossx < sx+sw and crossx+w > sx:
                        ny, hit = min(ny, sy-h), True
                if terrain != 1 or max(y, ny) >= sy+sh or min(y+h, ny+h) <= sy:
                    continue
                if vx > 0 and x+w <= sx+1 and nx+w >= sx:
                    nx, hit = min(nx, sx-w), True
                elif vx < 0 and x >= sx+sw-1 and nx <= sx+sw:
                    nx, hit = max(nx, sx+sw), True
            row['pos'] = [nx, ny]
            row['props'].update(xmp=round(nx), ymp=round(ny))
            row['vel'] = [vx, nv]
            if hit:
                self.host.batch.move_to(oid, round(nx), round(ny), dt=max(1, round(step*1000)), offset=self.host._offset())
                if barrel:
                    self._explode(oid, 'BLOCKED')
                else:
                    self._set(oid, DAMAGE=0, HITBOXDAMAGE=0, TERRAINTYPE=0)
                    self._retire(oid, 'BLOCKED')
                    self._record('cargo_impact', objid=oid)
                break
        if not row.get('dead') and dt > 0:
            self.host.batch.move_to(oid, round(row['pos'][0]), round(row['pos'][1]),
                                    dt=max(1, round(dt*1000)), offset=self.host._offset())

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('EGY mechanics dt must be finite and nonnegative')
        now = self.host.room_time
        for oid, st in list(self.states.items()):
            row = self.rows[oid]
            if row.get('dead') or st.get('retired'):
                continue
            kind = st['kind']
            if kind == 'danger':
                self._step_fire(oid, st, now)
            elif kind == 'barrel' and st['phase'] == 'rolling':
                self._fall(oid, dt, barrel=True)
            elif kind == 'cargo' and st['phase'] == 'falling':
                self._fall(oid, dt)
            elif kind == 'sensor':
                if st['phase'] == 'idle':
                    x, y, w, h = _rect(row)
                    for player in self._players():
                        px = _value(player, 'xmp') + _value(player, 'wmp')/2
                        py = _value(player, 'ymp') + _value(player, 'hmp')/2
                        if math.hypot(max(x-px, 0, px-x-w), max(y-py, 0, py-y-h)) <= self.config['sensor_radius_mp']:
                            st.update(phase='triggered', deadline=now+self.config['sensor_delay_s'])
                            self._record('sensor_triggered', objid=oid, evidence='USER+SIM-range-delay')
                            break
                if st['phase'] == 'triggered' and now >= st['deadline']:
                    self._spawn_sensor(oid)
        for oid, st in list(self.effects.items()):
            if st.get('retired') or not self._lifetime_due(oid, st['expires']):
                continue
            self._set(oid, DAMAGE=0, HITBOXDAMAGE=0)
            self._retire(oid, 'LIFETIME')

    def interact(self, oid, player):
        from ...interactions import can_interact
        if not can_interact(player):
            return False
        st = self.states.get(oid)
        if st is None or st['kind'] != 'barrel' or st.get('phase') != 'standing' or st.get('spent'):
            return False
        row = self.rows[oid]
        if row.get('dead') or not _value(player, 'alive', True):
            return False
        p = self._template('hulk.rollingbarrel')
        oldh = row['props'].get('hmp', 24576)
        row['props'].update(p)
        row['props'].pop('move', None)
        row['pos'][1] += oldh-p.get('hmp',16384)
        row['props'].update(xmp=round(row['pos'][0]), ymp=round(row['pos'][1]))
        facing = int(_value(player, 'facing', 0)) % 360
        direction = -1 if 90 < facing < 270 else 1
        row['hp'], row['vel'] = None, [direction*BARREL_SPEED, BARREL_HOP]
        st['phase'] = 'rolling'
        self._set(oid, VID='hulk.rollingbarrel', WMP=p.get('wmp',16384), HMP=p.get('hmp',16384),
                  DAMAGE=p.get('damage',150), HITBOXDAMAGE=0, OWNERTYPE=1, TERRAINTYPE=2,
                  BLOCKTYPE=1, REMOVETYPE=7, HITPOINTS=-1, INTERACT=0, FACING=180 if direction<0 else 0)
        self.host.batch.modify(oid, 'MOVE', message=('Properties_Move', normalize('Properties_Move',
            dict(vel=dict(xvel=0,yvel=0)))), offset=self.host._offset())
        self.host.batch.modify(oid, 'POSE', intval=3, offset=self.host._offset())
        self.host.batch.move_to(oid, round(row['pos'][0]), round(row['pos'][1]), dt=1, offset=self.host._offset())
        self._record('barrel_rolled', objid=oid, direction=direction, damage=150, evidence='D+USER-direction')
        return True

    def feedback(self, event):
        kind = event.get('kind', '')
        oid = int(event.get('victim', event.get('objid', event.get('id', 0))) or 0)
        st = self.states.get(oid)
        if st is None:
            return False
        if kind not in ('damage', 'destroy', 'client-damage', 'client-destroy'):
            return False
        if st.get('retired') or st.get('spent') or self.rows[oid].get('dead'):
            return True
        row = self.rows[oid]
        destroy = 'destroy' in kind
        if not destroy:
            value = int(event.get('value', event.get('val', 0)) or 0)
            if value <= 0 or row.get('hp') is None:
                return True
            row['hp'] -= value
            if row['hp'] > 0:
                self._set(oid, HITPOINTS=row['hp'])
                return True
        reason = event.get('reason', 'HITPOINTS')
        if isinstance(reason, dict):
            reason = reason.get('name', reason.get('value', 'HITPOINTS'))
        reason = REASONS.get(reason, str(reason).replace('DESTROYREASON_', ''))
        if st['kind'] == 'generator':
            self._open_field(oid, native=destroy)
        elif st['kind'] == 'hook':
            for cargo in st['cargo']:
                self._release_cargo(cargo)
            self._retire(oid, reason, native=destroy)
        elif st['kind'] == 'barrel' and reason not in ('INTERNAL',):
            self._explode(oid, reason, native=destroy)
        elif st['kind'] == 'sensor' and reason in ('HITPOINTS', 'BLOCKED', 'DAMAGES', 'LIFETIME'):
            self._spawn_sensor(oid, native=destroy)
        else:
            self._retire(oid, reason, native=destroy)
        return True

    def status(self):
        counts = {}
        for oid, st in self.states.items():
            if not self.rows[oid].get('dead') and not st.get('retired'):
                counts[st['kind']] = counts.get(st['kind'], 0) + 1
        return dict(counts=counts, fieldsOpened=sum(bool(s.get('opened')) for s in self.states.values()),
                    barrelsExploded=sum(s['kind']=='barrel' and bool(s.get('spent')) for s in self.states.values()),
                    sensorsSplit=sum(s['kind']=='sensor' and bool(s.get('spent')) for s in self.states.values()),
                    sim=dict(self.config))
