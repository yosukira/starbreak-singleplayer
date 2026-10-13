'Native Dumb Hard host driven by live AI.'
import copy
import heapq
import math
import random
import threading
import time
from pathlib import Path

from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops, is_hp_death
from . import params
from .controller import DumbBossController
from .scene import DumbBossPack, normalize, motion_position
from .physics import sweep_entry

SOLID_VIDS = {'dumb.wall', 'dumb.ledge', 'dumb.door'}
DESTROY_INTERNAL = 1
DESTROY_HITPOINTS = 2
DESTROY_LIFETIME = 6

from ..dumb_common.actors import DumbActorHost

class DumbBossLiveSession(DumbActorHost):
    HZ = 60.

    NATIVE_BOSS_HP = 90000

    RIDE_MOUNT_MP = 6144
    RIDE_BAND_MP = 8192

    def __init__(self, server, channel, options, room=None, log=None, bridge=None,
                 pack=None, seed=None):
        self.server, self.channel, self.options, self.room = server, channel, options, room
        self.log = log or (lambda message: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        root = Path(getattr(options, 'create_world_hex', '') or '').parent
        self.pack = pack or DumbBossPack(root if (root / 'programs.json').exists() else None)
        self.npc_regions=params.REGIONS
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550),
                                             log=self.log)
        self.owns_bridge = bridge is None
        self.seed = 0xC0FFEE if seed is None else int(seed)
        self._stop, self._thread = threading.Event(), None
        self.prepare()

    def prepare(self):
        self.supplies = SupplyDrops(self, 'dumb_boss')
        self.phase = 'waiting'
        self.room_time = 0.
        self.controller, self.player = None, None
        self.actors, self.retired, self.queue = {}, set(), []
        self.drone_children, self.drone_parents = {}, {}
        self.sequence, self.frames = 0, 0
        self.events = []
        self.rng = random.Random(self.seed)
        self.counts = dict(spawns=0, npcs=0, shots=0, native_damage=0, native_destroy=0,
                           splits=0, cap_skips=0, wall_hits=0, lifetime=0)
        self._next_id = max(map(int, self.pack.templates)) + 1000
        self._wire_ms, self._sent_ms = 0., 0
        self._frame_start = 0.
        self.lifts = {}
        self.boss_eye = None
        self.solids = self._solid_rects()
        self.walls = [row for row in self.solids if row[4] == 1]
        self.bounds = tuple(self.pack.scene.get('bounds_mp') or (-16384, -16384, 663552, 294912))
        self.batch.clear()
        for identity, props in self.pack.templates.items():
            self._add_actor(int(identity), props, 0., kind='static')

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log('dumb_boss live session started (live AI, seeded spawns)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        dt, deadline = 1. / self.HZ, time.monotonic()
        while not self._stop.is_set():
            deadline += dt
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log('dumb_boss tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            self._stop.wait(max(0., deadline - time.monotonic()))
            if deadline < time.monotonic() - dt:
                deadline = time.monotonic()

    def _nearest_pad(self, x, y):
        """Telepad graphic closest to a player point (pads 59/62/65/68)."""
        best, best_d = None, None
        for objid, row in self.actors.items():
            if row['props'].get('vid') != 'dumb.telepad':
                continue
            px, py = self.position(objid, self.room_time)
            d = math.hypot(px + row['props'].get('wmp', 0) / 2 - x,
                           py + row['props'].get('hmp', 0) / 2 - y)
            if best_d is None or d < best_d:
                best, best_d = objid, d
        return best

    def _teleport_fx(self, x, y, at):
        'runtime dumb.teleportfx: 70x150 px at z -5 for 594 ms.'
        pad = self._nearest_pad(x, y)
        if pad is not None:
            row = self.actors[pad]
            px, py = self.position(pad, at)
            offset = (row['props'].get('wmp', 0) - 17920) / 2
            x, y = px + offset, py + offset
            self.batch.sound(pad, 'dumb.teleportenemy', offset=self._offset(at))
        self._next_id += 1
        objid = self._next_id
        props = dict(xmp=float(x), ymp=float(y), wmp=17920, hmp=38400, z=-5,
                     lifetime=594, vid='dumb.teleportfx')
        self._add_actor(objid, props, at, kind='fx')
        self.actors[objid]['expire'] = at + 0.594
        self.batch.create(objid, self.pack.wire_props(props), creatorid=0,
                          offset=self._offset(at))
        return objid

    def _spawn_heavy(self, at):
        """Activate the room's own heavy (static object 32) - never create one.

        The retail CreateWorld already contains the far-right big robot at
        (352256, 187904).  Spawning another copy is what put two heavies in the
        opening frame.
        """
        objid = None
        for i, row in self.actors.items():
            if row['props'].get('vid') == 'dumb.heavy' and row['kind'] == 'static':
                objid = i
                break
        if objid is None:
            return None
        row = self.actors[objid]
        row['kind'] = 'npc'
        row['npc'] = True
        row['hp'] = row['props'].get('hitpoints')

        target_x, target_y = params.ARENA_CENTER_HEAVY
        if int(row['props'].get('xmp', target_x)) != target_x:
            row['props']['xmp'] = target_x
            row['pos'] = [float(target_x), float(target_y)]
            row['start'] = (float(target_x), float(target_y))
            row['sent'] = [float(target_x), float(target_y)]
            self.batch.move_to(objid, int(target_x), int(target_y), dt=1,
                               offset=self._offset(at))
        row['ai'] = dict(species='heavy', dir=-1, next_shot_at=at + 1.2,
                         min_x=row['props'].get('xmp', 0) - 24576,
                         max_x=row['props'].get('xmp', 0) + 24576)
        if self.controller is not None:
            self.controller.heavy_id = objid
        self._record('heavy_activate', at, objid=objid,
                     x=row['props'].get('xmp'), y=row['props'].get('ymp'))
        return objid

    def _create_boss(self, at):
        pattern = self.pack.programs.get('intro') or []
        boss_id = self.pack.boss_id
        parts = []
        for item in pattern:
            props = copy.deepcopy(item['props'])
            if item['objid'] == boss_id:
                props['hitpoints'] = self.NATIVE_BOSS_HP
                props['maxhitpoints'] = self.NATIVE_BOSS_HP
            self._add_actor(item['objid'], props, at,
                            kind='boss' if item['objid'] == boss_id else 'part')
            self.batch.create(item['objid'], self.pack.wire_props(props), offset=self._offset(at))
            if item['objid'] != boss_id:
                parts.append(item['objid'])

        self.pack.scene['boss_parts'] = parts

        self.boss_eye = next(
            (oid for oid in parts
             if self.actors[oid]['props'].get('wmp') == 37120
             and not self.actors[oid]['props'].get('vid')), None)
        self._record('boss_create', at, boss=boss_id, parts=parts,
                     eye=self.boss_eye)

    def _wave_spawn(self, region, at):
        spec = params.REGIONS.get(region)
        if not spec:
            return
        names, weights = zip(*params.species_weights(self.pack.behaviour, region, spec['allowed']))
        species = self.rng.choices(list(names), weights=list(weights), k=1)[0]
        props = self._species_props(species) or {}
        rect = spec['rect']
        lo = max(self.bounds[0] + 8192, rect[0] + 2048)
        hi = min(self.bounds[2] - 8192 - props.get('wmp', 0), rect[0] + rect[2] - props.get('wmp', 0))
        width = max(0, hi - lo)
        x = lo + int(self.rng.random() * max(1, width))
        surface = rect[1]
        npc_y = surface - props.get('hmp', 0)
        fx = params.TELEPORT_FX
        large = species=='heavy'
        fx_width = props['wmp'] if large else fx['wmp']
        fx_height = props['hmp'] if large else fx['hmp']
        fx_props = dict(xmp=x + (props.get('wmp', 0) - fx['wmp']) // 2,
                        ymp=surface-fx_height,wmp=fx_width,hmp=fx_height,
                        z=-5,lifetime=fx['lifetime_ms'],
                        vid='dumb.largeteleportfxenemy' if large else fx['vid'])
        fx_props['xmp']=x+(props['wmp']-fx_width)//2
        self._next_id += 1
        fxid = self._next_id
        row = self._add_actor(fxid, fx_props, at, kind='fx')
        row['expire'] = at + fx['lifetime_ms'] / 1000.
        self.batch.create(fxid, self.pack.wire_props(fx_props), offset=self._offset(at))

        self.batch.sound(fxid, 'dumb.teleportenemy', offset=self._offset(at))
        self._enqueue(at + fx['delay_s'], 'npc_spawn', species=species, x=x, y=npc_y)
        self._record('wave_fx', at, region=region, species=species, x=x, surface=surface)

    def _controller_event(self, event):
        kind, at = event['kind'], event['at']
        if kind == 'heavy_create':
            self._spawn_heavy(at)
        elif kind == 'boss_create':
            self._create_boss(at)
        elif kind == 'boss_move':
            decoded = event['move'] if 'move' in event else dict(to=event['to'])
            self._command(self.pack.boss_id,
                          dict(modifyprops=dict(property=41, decoded=decoded)), at)
            if event.get('eye'):
                self._boss_eye_plan(decoded, at)
        elif kind == 'boss_attack':
            self._boss_attack(event['attack'], at, event.get('stage'),
                              event.get('slot'))
        elif kind == 'boss_charge':
            self._boss_charge(event, at)
        elif kind == 'boss_hide':
            self._boss_pose(hidden=True, at=at)
        elif kind == 'boss_show':
            self._boss_pose(hidden=False, at=at)
        elif kind == 'boss_eye_show':
            self._boss_eye_show(event['frame'],at)
        elif kind == 'wave_spawn':
            self._wave_spawn(event['region'], at)
        elif kind == 'stage':
            self._apply_stage(event['index'], event['armor'], at)
        elif kind == 'boss_defeated':
            self._boss_defeated(at)
        elif kind == 'curtain_open':
            opening=self.pack.programs.get('opening',[])
            origin=min((c['t'] for c in opening),default=0.)
            for command in opening:
                self._enqueue(at+command['t']-origin,'script_command',command=command)
        self._record('controller', at, event=kind,
                     **{k: v for k, v in event.items() if k not in ('kind', 'at')})

    def _apply_stage(self, stage, armor, at):
        boss = self.actors.get(self.pack.boss_id)
        if boss is None:
            return
        self.batch.modify(self.pack.boss_id, 'ARMOR', offset=self._offset(at),
                          intval=params.BOSS_ARMOR if armor else 0)
        boss['props']['armor'] = params.BOSS_ARMOR if armor else 0
        vid = 'dumb.bossshield' if armor else 'dumb.boss'
        if boss['vid'] != vid:
            self._command(self.pack.boss_id,
                          dict(modifyprops=dict(property=43, stringval=vid)), at)
        if not armor and stage == 3:

            self.batch.sound(self.pack.boss_id, 'dumb.bossreveal', offset=self._offset(at))
            self._anim(self.pack.boss_id, 9, 396, at)
        self._record('stage', at, stage=stage, armor=armor)

    def _boss_pose(self, hidden, at):
        'runtime wave/attack transition: back sprite + hidden hardware.'
        boss = self.actors.get(self.pack.boss_id)
        if boss is None:
            return
        stage = self.controller.stage if self.controller else 1
        if self.controller and self.controller.pending_stage and self.controller.state=='descend':
            stage=getattr(self.controller,'cycle_stage',stage)
        if hidden:
            armored=getattr(self.controller,'cycle_stage',stage)<=2
            vid = 'dumb.bossshieldback' if armored else 'dumb.bossback'
            prop11,draw=params.BOSS_PROP11_HIDDEN,False
        else:
            vid = 'dumb.boss' if stage >= 3 else 'dumb.bossshield'
            prop11, draw = params.BOSS_PROP11_ACTIVE, True
        if boss['vid'] != vid:
            self._command(self.pack.boss_id,
                          dict(modifyprops=dict(property=43, stringval=vid)), at)
        self.batch.modify(self.pack.boss_id, 11, intval=prop11, offset=self._offset(at))
        if hidden:
            self._boss_eye_hide(at)
        for part in (86, 87, 88, 89, 90):
            if part in self.actors:
                self.actors[part]['props']['draw'] = draw
                self.batch.modify(part, 'DRAW', boolval=draw, offset=self._offset(at))
        self._record('boss_pose', at, hidden=hidden, vid=vid)

    def _boss_eye_plan(self, decoded, at):
        'runtime eye schedule: frame 0.71 s before the hop, hide 1.5 s'
        if self.boss_eye is None or self.boss_eye not in self.actors:
            return
        stage = self.controller.stage if self.controller else 1
        if stage < 3:
            return
        points = ((decoded.get('waypoint') or {}).get('points') or [])
        to = decoded.get('to') or {}
        if len(points) >= 2:
            dx = points[-1].get('xmp', 0) - points[-2].get('xmp', 0)
            dy = points[-1].get('ymp', 0) - points[-2].get('ymp', 0)
            duration = sum(float(p.get('dt') or 0) for p in points) / 1000.
        elif points:
            bx, by = self.position(self.pack.boss_id, at)
            dx = points[-1].get('xmp', bx) - bx
            dy = points[-1].get('ymp', by) - by
            duration = sum(float(p.get('dt') or 0) for p in points) / 1000.
        elif 'xmp' in to:
            bx, by = self.position(self.pack.boss_id, at)
            dx, dy = to['xmp'] - bx, to['ymp'] - by
            duration = float(to.get('dt') or 0) / 1000.
        else:
            return
        frame = params.boss_eye_frame(dx, dy)
        if frame is None:
            return
        show_at = max(self.room_time, at - params.BOSS_EYE_LEAD_S)
        if show_at <= at + 1e-9:
            self._boss_eye_show(frame, at)
        else:
            self._enqueue(show_at, 'boss_eye', frame=frame)
        self._enqueue(at + duration + params.BOSS_EYE_HOLD_S, 'boss_eye_hide')

    def _boss_eye_show(self, frame, at):
        eye = self.boss_eye
        row = self.actors.get(eye) if eye is not None else None
        if row is None:
            return
        if row.get('vid') != frame:
            self._command(eye, dict(modifyprops=dict(property=43,
                                                     stringval=frame)), at)
        self.batch.modify(eye, 'DRAW', boolval=True, offset=self._offset(at))
        row['props']['draw'] = True
        self._record('boss_eye', at, frame=frame)

    def _boss_eye_hide(self, at):
        eye = self.boss_eye
        row = self.actors.get(eye) if eye is not None else None
        if row is None or not row['props'].get('draw'):
            return
        self.batch.modify(eye, 'DRAW', boolval=False, offset=self._offset(at))
        row['props']['draw'] = False
        self._record('boss_eye_hide', at)

    def _parts(self, vid):
        return [row['objid'] for row in self.actors.values()
                if row['kind'] == 'part' and row['props'].get('vid') == vid]

    def _boss_charge(self, event, at):
        attack = event.get('attack')
        animid, time_ms = int(event.get('anim') or 0), int(event.get('time_ms') or 0)
        slot = event.get('slot')
        if attack == 'laser':
            if event.get('sound'):
                self.batch.sound(self.pack.boss_id, event['sound'], offset=self._offset(at))
            if animid:
                self._anim(self.pack.boss_id, int(slot if slot is not None else animid),
                           time_ms, at)
        elif attack == 'drop':
            for part in self._parts('dumb.launcher'):
                self._anim(part, animid or 3, time_ms or 1980, at)
        self._record('boss_charge', at, attack=attack, anim=animid)
    def _boss_attack(self, attack, at, stage=None, slot=None):
        boss = self.actors.get(self.pack.boss_id)
        if boss is None:
            return
        stage = int(stage or (self.controller.stage if self.controller else 1))
        bx, by = self.position(self.pack.boss_id, at)
        bw = boss['props'].get('wmp', 98304)
        bh = boss['props'].get('hmp', 81920)
        if attack == 'laser':
            template = params.PROJECTILES['laserbullet']
            vid = boss['vid']
            if slot is None:
                slot = 1 if vid == 'dumb.bossshield' else 6
            slot = int(slot)

            try:
                points = list(self.pack.emit_points(vid, slot, (bx, by),
                                                    (bw, bh)))
            except (ValueError, KeyError):
                points = []
            if not points:
                points = [dict(x=bx + bw / 2 - 214 * 256, y=by + bh - 239 * 256,
                               angle=180),
                          dict(x=bx + bw / 2 - 212 * 256, y=by + bh - 123 * 256,
                               angle=180),
                          dict(x=bx + bw / 2 + 197 * 256, y=by + bh - 239 * 256,
                               angle=0),
                          dict(x=bx + bw / 2 + 196 * 256, y=by + bh - 123 * 256,
                               angle=0)]
            self._anim(self.pack.boss_id, slot,
                       100 if vid == 'dumb.bossshield' else 75, at)
            for point in points:
                facing = int(point.get('angle') or 0)
                cx, cy = float(point['x']), float(point['y'])
                vel = (params.LASER_SPED_MP_S if facing == 0
                       else -params.LASER_SPED_MP_S)
                objid = self._spawn_shot('laserbullet', self.pack.boss_id, at,
                                         position=(cx - template['wmp'] / 2,
                                                   cy - template['hmp'] / 2),
                                         vel=(vel, 0.))
                self.batch.sound(objid, 'dumb.lasergen', offset=self._offset(at))
                self.batch.modify(objid, 'POSE', intval=2, offset=self._offset(at))
                self.batch.modify(objid, 'FACING', intval=facing,
                                  offset=self._offset(at))
        elif attack == 'drop':
            template = params.PROJECTILES['munitions']
            for part in self._parts('dumb.launcher'):
                row = self.actors.get(part)
                if row is None:
                    continue
                px, py = self.position(part, at)
                pw = row['props'].get('wmp', 0)
                ph = row['props'].get('hmp', 0)
                self._anim(part, 0, 594, at)

                for offset in params.DROP_MOUTHS_MP:
                    shot = self._spawn_shot('munitions', part, at,
                                            position=(px + pw / 2 + offset
                                                      - 8192 / 2,
                                                      py + ph / 2
                                                      - 8192 / 2),
                                            vel=(0., params.MUNITIONS_DROP_SPED))

                    self.actors[shot]['bounce'] = True
        elif attack == 'lob':
            template = params.PROJECTILES['tankmunitions']
            for part in self._parts('dumb.lobber'):
                row = self.actors.get(part)
                if row is None:
                    continue
                px, py = self.position(part, at)
                pw = row['props'].get('wmp', 0)
                ph = row['props'].get('hmp', 0)
                self._anim(part, 0, 462, at)

                vx = self.rng.uniform(*params.LOB_VX_UNITS)
                if self.rng.random() < 0.5:
                    vx = -vx
                vy = -self.rng.uniform(*params.LOB_VY_UNITS)
                accel = self.rng.uniform(*params.LOB_ACCEL_UNITS)
                self._spawn_shot('tankmunitions', part, at,
                                 position=(px + pw / 2 - template['wmp'] / 2,
                                           py + ph / 2 - template['hmp'] / 2),
                                 vel=(vx * params.LOB_VELOCITY_MP,
                                      vy * params.LOB_VELOCITY_MP),
                                 accel=(0., accel * params.LOB_ACCEL_MP))
        self._record('boss_attack', at, attack=attack, stage=stage)

    def _boss_hitpoints(self):
        """Client-facing boss HP: the five gates minus the accepted damage."""
        if self.controller is None:
            return self.NATIVE_BOSS_HP
        total = float(sum(self.controller.gates))
        left = total - float(self.controller.total_damage)
        if getattr(self.controller, 'final_stage_cleared', False):
            return 0
        return max(1, int(round(left)))

    def _boss_defeated(self, at):
        self.batch.modify(self.pack.boss_id, 'HITPOINTS', intval=0,
                          offset=self._offset(at))
        self.batch.modify(self.pack.boss_id, 'INVULNERABLE', boolval=True,
                          offset=self._offset(at))
        self._boss_eye_hide(at)
        program=self.pack.programs.get('programs',{}).get('death',{})
        for event in program.get('events',[]):
            self._enqueue(at+event['t'],'death_script',event=event)
        self._record('boss_defeated', at)

    MUNITIONS_GRAVITY = 340000.
    MUNITIONS_TERMINAL = 160000.
    MUNITIONS_BOUNCE = 1.0
    MUNITIONS_SIDE_SPEED = 59000.

    def _step_interactions(self, start, end, keys):
        from ..dumb_common.elevators import step_elevators
        step_elevators(self,start,end,self.pack.entities['elevator'],self.pack.scene['elevators'])

    def _feedback(self, event):
        if not isinstance(event, dict):
            return
        if 'ownerid' in event and int(event.get('ownerid') or 0) != self.worldid:
            return
        if event.get('kind') == 'teleport':
            source = event.get('from')
            landing = event.get('to')
            if (isinstance(source, (list, tuple)) and len(source) >= 2
                    and isinstance(landing, (list, tuple)) and len(landing) >= 2):
                self._teleport_fx(float(source[0]), float(source[1]),
                                  self.room_time)
                self._teleport_fx(float(landing[0]), float(landing[1]),
                                  self.room_time)
                self._record('teleport', self.room_time,
                             source=event.get('source'),
                             destination=event.get('destination'))
            return
        victim = int(event.get('victim') or 0)
        row = self.actors.get(victim)
        if row is None:
            return
        if event.get('kind') == 'damage':
            value = int(event.get('value') or 0)
            if value <= 0:
                return
            self.counts['native_damage'] += 1
            if victim == self.pack.boss_id and self.controller:
                self.controller.add_damage(value, victim)
                self.batch.modify(victim, 'HITPOINTS',
                                  intval=self._boss_hitpoints())
            elif row['hp'] is not None:
                self._note_npc_damage(row,self.room_time)
                row['hp'] -= value
                if row['hp'] <= 0:
                    self._retire(victim, DESTROY_HITPOINTS, self.room_time)
                else:
                    self.batch.modify(victim, 'HITPOINTS', intval=row['hp'])
        elif event.get('kind') == 'destroy':
            value = int(event.get('reason') or 1)
            if row['vid']=='dumb.drone' and value==DESTROY_LIFETIME and row['expire'] is None:

                self._record('drone_ignored_destroy',self.room_time,objid=victim,reason=value)
                return
            self.counts['native_destroy'] += 1
            if row['kind'] in ('npc', 'projectile', 'fx'):
                self._retire(victim, value, self.room_time, native=True)

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        state, keys = self.bridge.snapshot()
        if state is not None:
            self.player = state
        self._frame_start = self.room_time
        if self.controller is None and state is not None and state.alive and state.age() < 2.:
            self.controller = DumbBossController(self.pack, seed=self.seed)
            self.room_time = self._frame_start = 0.
            for event in self.controller.begin():
                self._controller_event(event)
            self._record('fight_start', 0.)
        for feedback in self.bridge.poll_events():
            self._feedback(feedback)
        if self.controller:
            start, end = self.room_time, self.room_time + dt
            for event in self.controller.step(dt):
                self._enqueue(event['at'], 'controller', event=event)
            while self.queue and self.queue[0][0] <= end + 1e-9:
                at, _seq, action = heapq.heappop(self.queue)
                if action['kind'] == 'controller':
                    self._controller_event(action['event'])
                elif action['kind'] == 'npc_spawn':
                    self._spawn_npc(action['species'], at, (action['x'], action['y']))
                elif action['kind'] == 'boss_eye':
                    self._boss_eye_show(action['frame'], at)
                elif action['kind'] == 'boss_eye_hide':
                    self._boss_eye_hide(at)
                elif action['kind'] == 'script_command':
                    command=action['command']
                    self._command(command['objid'],command,at)
                elif action['kind'] == 'death_script':
                    event=action['event']
                    if event['kind']=='command':
                        self._command(event['objid'],event,at)
                    elif event['kind']=='spawn':
                        profile=self.pack.programs['profiles'][event['profile']]
                        props=copy.deepcopy(profile['props'])
                        self._next_id+=1
                        row=self._add_actor(self._next_id,props,at,kind='fx')
                        row['expire']=at+props.get('lifetime',500)/1000.
                        self.batch.create(row['objid'],self.pack.wire_props(props),offset=self._offset(at))
            self._step_dynamics(start, end)
            self._step_interactions(start, end, keys)
            self.room_time, self.phase = end, self.controller.state
        self._wire_ms += dt * 1000.
        target = round(self._wire_ms)
        self.batch.dt = target - self._sent_ms
        self._sent_ms = target
        self.server._send_raw(self.channel, 12, self.batch.encode(), 'dumb-boss-live', record=False)
        self.frames += 1
        self.batch.clear()
        self.supplies.flush()

    def status(self):
        return dict(map='dumb_boss', phase=self.phase, time=round(self.room_time, 3),
                    frames=self.frames,
                    controller=self.controller.status() if self.controller else None,
                    npcs=sum(row['kind'] == 'npc' for row in self.actors.values()),
                    projectiles=sum(row['kind'] == 'projectile' for row in self.actors.values()),
                    actors=len(self.actors), pending=len(self.queue), counts=dict(self.counts))
