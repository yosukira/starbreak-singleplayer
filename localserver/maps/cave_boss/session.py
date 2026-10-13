"""Cave native host using shared transport/bridge and Cave-owned decisions."""
import math
import random
import threading
import time
from pathlib import Path
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from .controller import CaveBossController
from .scene import CaveBossPack, normalize

class CaveBossLiveSession:
    HZ = 60.

    def __init__(self, server, channel, options, room=None, log=None, bridge=None, pack=None, seed=None, players=None):
        self.server, self.channel, self.options = server, channel, options
        self.log = log or (lambda text: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        path = Path(getattr(options, 'create_world_hex', '') or '').parent
        self.pack = pack or CaveBossPack(path if (path / 'entities.json').exists() else None)
        chosen = getattr(options, 'room_roll_seed', None)
        self.seed = int(chosen if chosen is not None else seed if seed is not None else random.SystemRandom().randrange(1 << 30))
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550), log=self.log)
        self.owns_bridge = bridge is None; self.players_provider = players
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self._stop = threading.Event(); self._thread = None
        self.prepare()

    def prepare(self):
        self.supplies = SupplyDrops(self, 'cave_boss')
        self.batch.clear(); self.controller = CaveBossController(self.pack, self.seed)
        self.room_time = 0.; self.phase = 'waiting'; self.bootstrapped = False; self.players = {}; self.player = None
        self._wire_ms = 0.; self._sent_ms = 0; self._frame_start = 0.; self._event_time = 0.
        self.events = []; self.frames = 0; self.native_damage = 0; self.native_destroy = 0; self.max_tick_ms = 0.
        self.created = set(); self.retired = set(); self.scale_hits = {i: 0 for i in self.pack.scales}
        self.scale_rng = random.Random(self.seed ^ 0xCA7E)
        self.sound_last = {}; self.body_damage = 0

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare(); self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True); self._thread.start()
        self.log('cave_boss: native fixed room, live weak-mouth transitions, pulse insects and rolling acid')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        dt = 1 / self.HZ; previous = time.monotonic(); deadline = previous + dt
        while not self._stop.is_set():
            self._stop.wait(max(0., deadline - time.monotonic()))
            if self._stop.is_set():
                break
            now = time.monotonic(); elapsed = now - previous; previous = now
            try:

                self.step(elapsed)
            except Exception:
                import traceback
                self.log('cave_boss tick failed: ' + traceback.format_exc()); self._stop.set(); break
            deadline += dt
            if deadline < time.monotonic() - dt:
                deadline = time.monotonic() + dt

    def _offset(self):
        return max(0, round((self._event_time - self._frame_start) * 1000))

    def record(self, kind, **values):
        self.events.append(dict(kind=kind, at=self._event_time, **values))
        if len(self.events) > 5000:
            self.events = self.events[-3500:]

    def _props(self, oid, **values):
        self.batch.props(oid, values, offset=self._offset())

    def _sound(self, oid, name, cooldown=0.):
        key = (oid, name)
        if self._event_time - self.sound_last.get(key, -math.inf) < cooldown:
            return
        self.sound_last[key] = self._event_time
        self.batch.sound(oid, name, offset=self._offset())
        self.record('sound', id=oid, sound=name)

    def _hit_sound(self, oid, species):
        prefix = self.pack.entities['sounds']['hit_prefixes'].get(species)
        if prefix:
            self._sound(oid, prefix + 'hit', .08)

    def _roller_motion(self,row):

        r=self.pack.entities['roller'];limit=round(row.get('roll_speed',abs(row['vel'][0]))*65.536)
        vertical=round(r['terminal_px_s']*65.536)
        return dict(vel=dict(xvel=round(row['vel'][0]*65.536),yvel=round(row['vel'][1]*65.536),
            xaccel=0,yaccel=round(r['gravity_px_s2']*65536/1000000),
            minxvel=-limit,maxxvel=limit,minyvel=-vertical,maxyvel=vertical))

    def _create(self, oid, props, creator=0):
        if oid in self.created:
            raise ValueError('duplicate Cave object creation %s' % oid)
        self.created.add(oid)
        self.batch.create(oid, normalize('Properties', props, self.layouts), creatorid=creator, offset=self._offset())

    def native_retirement_capability(self, oid):
        """Only issued controller actors expose their private role/deadline."""
        if type(oid) is not int or oid <= 0 or oid not in self.created or oid in self.retired:
            return None
        row = self.controller.actors.get(oid)
        if row is None:
            return None
        role = {'spray':'projectile', 'effect':'fx', 'roller':'npc',
                'insect':'npc', 'hive':'npc'}.get(row['kind'])
        if role is None:
            return None
        expiry = row.get('expiry')
        if type(expiry) not in (int, float) or not math.isfinite(expiry) or expiry < 0:
            expiry = None
        return dict(ownerid=self.worldid, objid=oid, role=role,
                    expires_at=expiry, room_time=self.controller.time,
                    lifetime_due=expiry is not None and self.controller.time >= expiry)

    def _destroy(self, oid, reason='INTERNAL', native=False):
        if oid in self.retired:
            return
        self.retired.add(oid)
        if not native or getattr(self.bridge, 'shared_room', False):
            self.batch.destroy(oid, ownerid=self.worldid, reason=reason, offset=self._offset())

    def _mouth_round(self, m, initial):
        vid = 'cave.bossclawtarget' if m['weak'] else 'cave.bossclaw'
        facing = 0 if m['side'] == 1 else 180
        if initial:
            props = self.pack.props(vid)
            props.update(xmp=round(m['pos'][0] * 256), ymp=round(m['pos'][1] * 256),
                         hitpoints=m['hp'], armor=m['armor'], damage=0, facing=facing, invulnerable=True, draw=True)
            self._create(m['id'], props)
            base = self.pack.props('cave.bossarmsegmentbase')
            base.update(xmp=round(m['home'][0] * 256), ymp=round(m['home'][1] * 256),
                        invulnerable=True, facing=facing, damage=0)
            self._create(m['base'], base)
            positions = self.controller.chain_positions(m)
            for oid, percentage, pos in zip(m['segments'], (18, 36, 54, 72, 90), positions):
                p = self.pack.props('cave.bossarmsegment')
                p.update(xmp=round(pos[0] * 256), ymp=round(pos[1] * 256), invulnerable=True, facing=facing, damage=0)
                if m['slot'] != 'R4':
                    p['move'] = dict(relativeto=dict(objid=m['base']),
                                     lerp=dict(target=dict(objid=m['id']), percentage=percentage, center=True))
                self._create(oid, p)
        else:
            self._props(m['id'], VID=vid, DRAW=True, HITPOINTS=m['hp'], ARMOR=m['armor'], DAMAGE=0, INVULNERABLE=True)
            self.batch.move_to(m['id'], round(m['pos'][0] * 256), round(m['pos'][1] * 256), dt=1, offset=self._offset())
            for oid in [m['base']] + m['segments']:
                self._props(oid, DRAW=True, DAMAGE=0, INVULNERABLE=True)

    def _body_vulnerability(self, active):
        for oid in self.pack.body_parts:
            p = self.pack.templates[str(oid)]
            if p.get('vid') == 'cave.bossbodysegment':
                self._props(oid, HITPOINTS=1 if active else 0, INVULNERABLE=not active)
        for oid, p in self.pack.scales.items():
            if self.scale_hits[oid] < self.pack.entities['scales']['hits_to_remove']:
                self._props(oid, HITPOINTS=1, INVULNERABLE=not active)

    def _action(self, event):
        self._event_time = event['at']; kind = event['kind']; oid = event.get('id')
        if kind == 'mouth_round':
            self._mouth_round(event['mouth'], event['initial'])
        elif kind == 'mouth_active':
            m = event['mouth']
            self._props(m['id'], HITPOINTS=m['hp'], ARMOR=m['armor'], INVULNERABLE=not m['weak'], DAMAGE=40)
            for part in [m['base']] + m['segments']:
                self._props(part, DAMAGE=40)
        elif kind == 'mouth_move':
            self.batch.move_to(oid, *[round(v * 256) for v in event['pos']], dt=50, offset=self._offset())
            m = self.controller.mouths[oid]
            if m['slot'] == 'R4':
                for part, pos in zip(m['segments'], self.controller.chain_positions(dict(m, pos=event['pos']))):
                    self.batch.move_to(part, *[round(v * 256) for v in pos], dt=50, offset=self._offset())
        elif kind in ('mouth_hp', 'actor_hp'):
            self._props(oid, HITPOINTS=event['hp'])
        elif kind == 'mouth_defeated':
            self._props(oid, DAMAGE=0, INVULNERABLE=True)
            self._sound(oid, self.pack.entities['sounds']['hit_prefixes']['mouth']+'death')
            self.batch.animprops(oid, 1, 462, offset=self._offset())
            m = self.controller.mouths[oid]
            for part in [m['base']] + m['segments']:
                self._props(part, DAMAGE=0)
        elif kind == 'mouth_hide':
            m = self.controller.mouths[oid]
            for part in [oid, m['base']] + m['segments']:
                self._props(part, DRAW=False, DAMAGE=0)
        elif kind == 'body_move':
            start, top = self.pack.scene['body_start'], self.pack.scene['body_top']
            points = [dict(xmp_=round(top[0] * 256), ymp_=round(top[1] * 256), dt_=round(event['up_s'] * 1000))]
            if event['down_s'] is not None:
                points.append(dict(xmp_=round(start[0] * 256), ymp_=round(start[1] * 256), dt_=round(event['down_s'] * 1000)))
            self.batch.modify(self.pack.scene['boss_id'], 'MOVE',
                              message=('Properties_Move', dict(waypoint_=dict(points_=points, loop_=False))), offset=self._offset())
            self._body_vulnerability(True)
            self.record(kind, number=event['number'], up_s=event['up_s'], down_s=event['down_s'])
        elif kind == 'body_return':

            self._body_vulnerability(True)
            for part, hits in self.scale_hits.items():
                if hits == 1:
                    self.scale_hits[part] = 0
                    self._props(part, ARMOR=self.pack.entities['scales']['first_armor'],
                                VID=self.pack.templates[str(part)]['vid'], HITPOINTS=1, DRAW=True)
            missing = [part for part, hits in self.scale_hits.items()
                       if hits >= self.pack.entities['scales']['hits_to_remove']]
            amount = min(len(missing), self.scale_rng.randint(*self.pack.entities['scales']['regen_count']))
            restored = self.scale_rng.sample(missing, amount)
            for part in restored:
                self.scale_hits[part] = 0
                self._props(part, ARMOR=self.pack.entities['scales']['first_armor'],
                            VID=self.pack.templates[str(part)]['vid'], HITPOINTS=1, DRAW=True, INVULNERABLE=False)
            self.record('scales_regrown', ids=restored, count=amount)
            self._sound(26, self.pack.entities['sounds']['boss_return'])
            self.record(kind, number=event['number'])
        elif kind == 'create':
            row = event['actor']; p = self.pack.props(row['vid'])
            p.update(xmp=round(row['pos'][0] * 256), ymp=round(row['pos'][1] * 256))
            if 'facing' in row:
                p.update(facing=row['facing'])
            if row['kind']=='roller':p['move']=self._roller_motion(row)
            self._create(row['id'], p, row.get('parent', 0))
            if row['kind'] in ('insect', 'roller'):
                self._sound(row['id'], self.pack.entities['sounds']['insect_spawn'])
            elif row['kind'] == 'effect':
                self._sound(row['id'], self.pack.entities['sounds']['explosion'])
            if row['kind'] != 'effect':
                self.record('spawn', id=row['id'], species=row['vid'], actor_kind=row['kind'], parent=row.get('parent'))
        elif kind == 'remove':
            row = event.get('actor')
            if (row and row['kind'] in ('hive', 'insect', 'roller')
                    and is_hp_death(event['reason'])):
                self.supplies.killed((row['pos'][0] + row['size'][0] / 2.) * 256,
                                     (row['pos'][1] + row['size'][1] / 2.) * 256)
            self._destroy(oid, event['reason'], event['native'])
        elif kind == 'actor_move':
            row = event['actor']; x, y = (round(v * 256) for v in row['pos'])
            if row['kind'] == 'roller':

                self.batch.move_to(row['id'], x, y, dt=0, offset=self._offset())
                self.batch.modify(row['id'], 'MOVE', message=('Properties_Move',normalize('Properties_Move',self._roller_motion(row))),offset=self._offset())
            else:
                self.batch.move_to(row['id'], x, y, dt=50, offset=self._offset())
            if row['kind'] == 'insect':
                vx, vy = row['vel']
                if abs(vx) + abs(vy) < .1:
                    vx, vy = row['heading']
                pose = 2 if abs(vx) >= abs(vy) else 3
                facing = (0 if vx >= 0 else 180) if pose == 2 else (90 if vy >= 0 else 270)
                self._props(row['id'], POSE=pose, FACING=facing)
            else:
                self._props(row['id'], FACING=0 if row['vel'][0] >= 0 else 180, POSE=2 if row['vel'][1] == 0 else 3)
        elif kind == 'death_start':
            self._body_vulnerability(False)
            for part in self.pack.scene.get('hazard_ids', [147, 148, 149]):
                self._props(part, DAMAGE=0)
            for m in self.controller.mouths.values():
                for part in [m['id'], m['base']] + m['segments']:
                    self._props(part, DAMAGE=0, INVULNERABLE=True)
                self.batch.animprops(m['id'], 1, 264, offset=self._offset())
            self.batch.animprops(26, 1, 594, offset=self._offset())
            self.batch.sound(26, 'cave.bossscream', offset=self._offset()); self.record(kind)
        elif kind == 'death_move':

            self.batch.modify(26, 'MOVE', message=('Properties_Move', dict(to_=dict(
                xmp_=round(event['pos'][0] * 256), ymp_=round(event['pos'][1] * 256), dt_=200))), offset=self._offset())
            self.batch.sound(26, 'cave.bossscream', offset=self._offset())
        elif kind == 'victory':
            for part in [26] + self.pack.body_parts:
                self._destroy(part)
            for m in self.controller.mouths.values():
                for part in [m['id'], m['base']] + m['segments']:
                    self._destroy(part)
            self.batch.modify(self.pack.scene['exit_trigger'], 27, intval=1, offset=self._offset())
            self.phase = 'done'; self.record(kind)
        elif kind in ('insect_dart', 'roller_roll'):
            name = self.pack.entities['sounds']['insect_dart' if kind == 'insect_dart' else 'roller_roll']
            self._sound(oid, name, .14 if kind == 'roller_roll' else 0.)
        elif kind in ('round', 'weak_pair_defeated', 'mouth_attack', 'roller_consumed','roller_contact'):
            event={k:v for k,v in event.items() if k!='actor'}
            self.record(kind, **{k: v for k, v in event.items() if k not in ('kind', 'at')})

    def _feedback(self, event):
        if not isinstance(event, dict) or ('ownerid' in event and int(event['ownerid'] or 0) != self.worldid):
            return
        oid = int(event.get('victim') or 0)
        if oid in self.retired:
            return
        self._event_time = self.controller.time
        if event.get('kind') == 'damage':
            value = int(event.get('value') or 0)
            if value <= 0:
                return
            self.native_damage += 1
            if oid in self.pack.scales:
                cap = self.pack.entities['scales']['hits_to_remove']
                if self.controller.state not in ('waiting', 'death', 'done') and self.scale_hits[oid] < cap:
                    self._hit_sound(oid, 'scale')
                    self.scale_hits[oid] += 1
                    count = self.scale_hits[oid]; p = self.pack.scales[oid]
                    armor = self.pack.entities['scales']['damaged_armor' if count < cap else 'removed_armor']
                    self._props(oid, ARMOR=armor, HITPOINTS=1 if count < cap else 0, DRAW=count < cap,
                                VID=p['vid'] + 'dmg', INVULNERABLE=count >= cap)
                    self.record('scale_hit', id=oid, hits=count, armor=armor)
                    if count >= cap:
                        self._sound(oid, self.pack.entities['sounds']['scale_break'])
                return
            if oid in self.pack.body_parts:
                if self.controller.state not in ('waiting', 'death', 'done'):
                    self._props(oid, HITPOINTS=1)
                    self.body_damage += value
                    self._hit_sound(oid, 'mouth')
                    self.record('body_hit', id=oid, value=value, phase=self.controller.state)
                return
            mouth = self.controller.mouths.get(oid)
            row = self.controller.actors.get(oid)
            if mouth and mouth['weak'] and not mouth['dead'] and mouth['action'] != 'enter':
                self._hit_sound(oid, 'mouth')
            elif row is not None and row.get('hp', 0):
                self._hit_sound(oid, 'hive' if row['kind'] == 'hive' else row['vid'])
            for action in self.controller.add_damage(value, oid, event.get('player_id')):
                self._action(action)
        elif event.get('kind') == 'destroy':
            self.native_destroy += 1
            if oid in self.controller.mouths or oid in self.pack.body_parts:
                return
            for action in self.controller.native_destroy(oid, int(event.get('reason') or 0)):
                self._action(action)

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('invalid dt')
        began = time.perf_counter(); self._frame_start = self.controller.time; self._event_time = self._frame_start
        state, _ = self.bridge.snapshot(); self.player = state
        if self.players_provider is not None:
            self.players = self.players_provider() if callable(self.players_provider) else self.players_provider
        elif state and state.alive and state.age() < 2.:
            self.players = {(state.worldid, state.objid): dict(center=state.center(), alive=True)}
        else:
            self.players = {}
        self.controller.set_players(self.players)
        if not self.bootstrapped and self.players:
            self.bootstrapped = True; self.controller.start(); self.phase = 'fighting'
            self._body_vulnerability(True)
            self.record('fight_start')
        for event in self.bridge.poll_events():
            self._feedback(event)
        for event in self.controller.step(dt):
            self._action(event)
        self.room_time = self.controller.time
        self._wire_ms += dt * 1000; ms = round(self._wire_ms)
        self.batch.dt = ms - self._sent_ms; self._sent_ms = ms
        self.server._send_raw(self.channel, 12, self.batch.encode(), 'cave-boss-live', record=False)
        self.batch.clear(); self.frames += 1
        self.supplies.flush()
        self.max_tick_ms = max(self.max_tick_ms, (time.perf_counter() - began) * 1000)

    def status(self):
        return dict(map='cave_boss', seed=self.seed, phase=self.phase, time=self.room_time,
                    controller=self.controller.status(), native_damage=self.native_damage, native_destroy=self.native_destroy,
                    body_damage=self.body_damage,
                    max_tick_ms=round(self.max_tick_ms, 3), frames=self.frames)
