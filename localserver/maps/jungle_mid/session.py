"""Live EST mid: native worm links, damage driven rings and platform fauna.

The client owns player combat and contact damage. This host only consumes
native hit/destroy feedback and supplies enemy decisions and wire updates.
"""
import copy
import math
import random
import threading
import time
from pathlib import Path

from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ..jungle_common.scene import normalize, platform_slabs
from ..jungle_common.session import JungleRoomActors, DESTROY_INTERNAL, DESTROY_HITPOINTS, DESTROY_LIFETIME
from . import params
from .controller import JungleMidController
from .scene import JungleMidPack
from .orbits import Orbit

def roll_create_world(blob, seed):
    return bytes.fromhex(JungleMidPack().rolled_world(seed))

class JungleMidLiveSession(JungleRoomActors):
    HZ = 60.
    fauna = params

    def __init__(self, server, channel, options, room=None, log=None, bridge=None,
                 pack=None, seed=None, players=None):
        self.server, self.channel, self.options, self.room = server, channel, options, room
        self.log = log or (lambda message: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        root = Path(getattr(options, 'create_world_hex', '') or '').parent
        self.pack = pack or JungleMidPack(root if (root / 'dynamic-templates.json').exists() else None)
        self.seed = int(getattr(options, 'room_roll_seed', None) if getattr(options, 'room_roll_seed', None) is not None
                        else seed if seed is not None else random.SystemRandom().randrange(1 << 30))
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550), log=self.log)
        self.owns_bridge = bridge is None
        self._stop, self._thread = threading.Event(), None
        self.prepare()

    def prepare(self):
        self.supplies = SupplyDrops(self, 'jungle_mid')
        self.pack.rolled_world(self.seed)
        self.rng = random.Random(self.seed)
        self.room_time, self._frame_start = 0., 0.
        self._wire_ms, self._sent_ms, self.frames = 0., 0, 0
        self.controller, self.player, self.phase = None, None, 'waiting'
        self.actors, self.retired, self.events = {}, set(), []
        self.counts = dict(spawns=0, pods=0, hoppers=0, hives=0, shots=0, cap_skips=0,
                           lifetime=0, wall_hits=0, pincer_slashes=0, native_damage=0,
                           native_destroy=0, rings=0, eggs=0, transforms=0)
        self._next_id = max(self.pack.run_objects) + 1000
        self.batch.clear()
        for oid, props in self.pack.run_objects.items():
            self._add_actor(oid, props, 0.)
        self.platforms = platform_slabs(self.pack.run_objects)
        self.protected_platforms = set(self.pack.protected_platforms)
        self.solids = [(p.get('xmp', 0), p.get('ymp', 0), p.get('wmp', 0), p.get('hmp', 0))
                       for p in self.pack.run_objects.values() if p.get('terraintype') in (1, 2)]
        self.bounds = self.pack.scene['bounds_mp']
        self.pit_y = self.bounds[3]
        self.mouth_id = self.pack.scene['mouth']
        mouth = self.actors[self.mouth_id]
        mouth['kind'], mouth['hp'] = 'boss', 575
        self.mouth_position = list(mouth['pos'])
        self.base_position = list(self.actors[self.pack.scene['base']]['pos'])
        self.rest_mouth = list(self.mouth_position)
        self.worm_motion, self.worm_velocity = None, [0., 0.]
        self.motion_decision = math.inf
        self.pincer_pairs = copy.deepcopy(self.pack.pincers)
        self.pincer_timers = {p['left']: dict(remain=self.rng.uniform(0., params.PINCER_CD_S),
                                              pending=None, armed=False) for p in self.pincer_pairs}
        self._pincer_at, self._pincer_last = 0., 0.
        self.pushers = copy.deepcopy(self.pack.pushers)
        for oid in self.pack.hives:
            self._activate_hive(oid, 1125)
        for oid in self.pack.skitters:
            self._activate_skitter(oid)
        self._body_positions()

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log('jungle_mid live session started (13 bars, live AI)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        dt, deadline = 1/self.HZ, time.monotonic()
        while not self._stop.is_set():
            deadline += dt
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log('jungle_mid tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            self._stop.wait(max(0., deadline-time.monotonic()))
            if deadline < time.monotonic()-dt:
                deadline = time.monotonic()

    def position(self, objid, at=None):
        return tuple(self.actors[objid]['pos']) if objid in self.actors else (0., 0.)

    def spawn_props(self, vid):
        props = super().spawn_props(vid)

        props.update(self.pack.species_props(vid) or {})
        return props

    def _body_positions(self):

        base_center = [v+51200 for v in self.base_position]
        mouth_center = [v+12288 for v in self.mouth_position]
        for oid, fraction in zip(self.pack.scene['segments'], (.14, .28, .42, .57, .71, .85)):
            if oid in self.actors:
                self.actors[oid]['pos'] = [base_center[j]+(mouth_center[j]-base_center[j])*fraction-25600
                                           for j in range(2)]
        if self.pack.scene['head'] in self.actors:
            self.actors[self.pack.scene['head']]['pos'] = [v-13312 for v in self.mouth_position]

    def _action(self, event):
        kind, at = event['kind'], event['at']
        if kind == 'bar_break':
            self.phase = 'finishing' if event['bar'] == 13 else 'recovering'
            row = self.actors.get(self.mouth_id)
            if row:
                self.mouth_position = list(row['pos'])
                row['hp'] = 575
                row['props']['invulnerable'] = True
                self.batch.modify(self.mouth_id, 'INVULNERABLE', boolval=True, offset=self._offset(at))
                self.batch.modify(self.mouth_id, 'HITPOINTS', intval=575, offset=self._offset(at))
                self.batch.animprops(self.mouth_id, 2, 924, offset=self._offset(at))
            for orb in self.actors.values():
                if orb.get('orbit'):
                    orb['orbit'].expand()
                    self._send_orbit(orb, at)
            self._record(kind, at, bar=event['bar'])
        elif kind == 'recover':
            row = self.actors.get(self.mouth_id)
            if row:
                row['props']['invulnerable'] = False
                self.batch.modify(self.mouth_id, 'INVULNERABLE', boolval=False, offset=self._offset(at))
            self.phase = 'sweep' if self.controller.broken >= 2 else 'idle'
            self._record('mouth_recover', at, objid=self.mouth_id)
        elif kind == 'motion':
            self._choose_motion(at)
        elif kind == 'wave':
            self._spawn_ring(event['ring'], at)
        elif kind == 'egg':
            self._spawn_egg(at)
        elif kind == 'egg_cast':
            self.batch.animprops(self.mouth_id, 2, 924, offset=self._offset(at))
            self._record('egg_cast', at)
        elif kind == 'victory':
            self._victory(at)

    def _spawn_ring(self, ring, at):
        props = self.spawn_props('jungle.swirlb')
        for n in range(5):
            oid = self._create('jungle.swirlb', dict(props, xmp=round(self.mouth_position[0]+4096),
                              ymp=round(self.mouth_position[1]+4096)), at, hp=600, parent=self.mouth_id,
                              ai=dict(species='orbiter', ring=ring, born=at,
                                      next_shot=at+self.rng.uniform(1., 4.)))
            row = self.actors[oid]
            row['native_motion'] = True
            row['orbit'] = Orbit(n, ring, at, self.pack.entities['orbiters'])
            self.batch.animprops(oid, 1, 528, offset=self._offset(at))
            self._send_orbit(row, at)
        self.counts['rings'] += 1
        self._record('ring', at, ring=ring, count=5, position=list(self.mouth_position))

    def _send_orbit(self, row, at):
        move = row['orbit'].wire(self.mouth_id, at)
        self.batch.modify(row['objid'], 'MOVE', message=('Properties_Move', normalize('Properties_Move', move)),
                          offset=self._offset(at))

    def _choose_motion(self, at):
        """D: stage 2 transit, stage 6 ellipse, stage 10 acceleration."""
        bars = self.controller.broken
        if bars < 2:
            return
        row, config = self.actors[self.mouth_id], self.pack.entities['movement']
        if bars < 6:
            angle = self.rng.uniform(0, math.tau)
            target = [self.rest_mouth[0]+config['transit_radius_mp']*math.cos(angle),
                      self.rest_mouth[1]+config['transit_radius_mp']*math.sin(angle)]
            self.worm_motion = dict(kind='transit', epoch=at, start=list(row['pos']), target=target)
            move = dict(to=dict(xmp=round(target[0]), ymp=round(target[1]), dt=4000,
                                start=dict(xmp=round(row['pos'][0]), ymp=round(row['pos'][1]))))
            self.motion_decision = at+config['decision_s']
        elif bars < 10:
            center = [self.base_position[j]+config['ellipse_center_offset_mp'][j] for j in range(2)]
            radius = math.dist(row['pos'], center)
            angle = math.atan2(row['pos'][1]-center[1], row['pos'][0]-center[0])
            self.worm_motion = dict(kind='ellipse', center=center, rx=radius, ry=radius,
                                    phase=(angle/math.tau) % 1,
                                    drx=self.rng.randint(5, 9)*1000., dry=self.rng.randint(5, 9)*1000.)
            self.motion_decision = math.inf
            move = self._ellipse_wire()
        else:

            angle = self.rng.uniform(0, math.tau)
            target = [self.rest_mouth[0]+102400*math.cos(angle), self.rest_mouth[1]+102400*math.sin(angle)]
            dx, dy = target[0]-row['pos'][0], target[1]-row['pos'][1]
            distance = max(1., math.hypot(dx, dy))
            accel = [round(9*dx/distance), round(9*dy/distance)]
            self.worm_motion = dict(kind='velocity', accel=[v*3906.25 for v in accel])
            self.motion_decision = at+self.rng.uniform(2., 3.5)

            move = dict(to=dict(xmp=round(row['pos'][0]), ymp=round(row['pos'][1]), dt=1,
                                start=dict(xmp=round(row['pos'][0]), ymp=round(row['pos'][1]))))
            row['sent'] = list(row['pos'])
            row['sent_at'], row['send_at'] = at-.05, at
        row['native_motion'] = self.worm_motion['kind'] != 'velocity'
        self.batch.modify(self.mouth_id, 'MOVE', message=('Properties_Move', normalize('Properties_Move', move)),
                          offset=self._offset(at))
        self._record('mouth_motion', at, mode=self.worm_motion['kind'], bar=bars)

    def _ellipse_wire(self):
        motion = self.worm_motion
        return dict(elliptical=dict(xmp=round(motion['center'][0]), ymp=round(motion['center'][1]),
                                   radiusamp=round(motion['rx']), radiusbmp=round(motion['ry']),
                                   period=6000, pos=round(motion['phase']*6000),
                                   ravel=round(motion['drx']/1000), rbvel=round(motion['dry']/1000)))

    def _step_worm(self, dt, at):
        row = self.actors.get(self.mouth_id)
        if row is None or self.worm_motion is None:
            return
        previous = list(row['pos'])
        motion = self.worm_motion
        if motion['kind'] == 'transit':
            f = min(1., max(0., (at-motion['epoch'])/4.))
            row['pos'] = [motion['start'][j]+(motion['target'][j]-motion['start'][j])*f for j in range(2)]
        elif motion['kind'] == 'ellipse':
            motion['phase'] = (motion['phase']+dt/6.) % 1
            motion['rx'] += motion['drx']*dt
            motion['ry'] += motion['dry']*dt
            angle = motion['phase']*math.tau
            row['pos'] = [motion['center'][0]+motion['rx']*math.cos(angle),
                          motion['center'][1]+motion['ry']*math.sin(angle)]
            if (max(motion['rx'], motion['ry']) >= 102400 and motion['drx'] > 0
                    or min(motion['rx'], motion['ry']) <= 6144 and motion['drx'] < 0):
                motion['drx'], motion['dry'] = -motion['drx'], -motion['dry']
                self.batch.modify(self.mouth_id, 'MOVE', message=('Properties_Move', normalize(
                    'Properties_Move', self._ellipse_wire())), offset=self._offset(at))
        else:
            config = self.pack.entities['movement']
            offset = [row['pos'][j]-self.rest_mouth[j] for j in range(2)]
            radius = math.hypot(*offset)
            if radius > config['turn_distance_mp']:
                normal = [v/max(1., radius) for v in offset]
                strength = config['accel_wire']*3906.25
                motion['accel'] = [-v*strength for v in normal]
                radial = sum(self.worm_velocity[j]*normal[j] for j in range(2))
                allowed = math.sqrt(2*strength*max(0., config['max_distance_mp']-radius))
                if radial > allowed:
                    self.worm_velocity = [self.worm_velocity[j]-normal[j]*(radial-allowed) for j in range(2)]
            for j in range(2):
                velocity = max(-156250., min(156250., self.worm_velocity[j]+motion['accel'][j]*dt))
                row['pos'][j] += (velocity+self.worm_velocity[j])*dt/2
                self.worm_velocity[j] = velocity
            offset = [row['pos'][j]-self.rest_mouth[j] for j in range(2)]
            radius = math.hypot(*offset)
            if radius > config['max_distance_mp']:
                normal = [v/radius for v in offset]
                row['pos'] = [self.rest_mouth[j]+normal[j]*config['max_distance_mp'] for j in range(2)]
                radial = max(0., sum(self.worm_velocity[j]*normal[j] for j in range(2)))
                self.worm_velocity = [self.worm_velocity[j]-normal[j]*radial for j in range(2)]
            self._sync_actor(row, at, .05)
        if motion['kind'] != 'velocity':
            self.worm_velocity = [(row['pos'][j]-previous[j])/dt for j in range(2)]
        self.mouth_position = list(row['pos'])
        self._body_positions()
        if at >= self.motion_decision:
            self._choose_motion(at)

    def _step_pushers(self, at):
        for pusher in self.pushers:
            row = self.actors.get(pusher['objid'])
            if row is None:
                continue
            span = pusher['hi']-pusher['lo']
            phase = (pusher['phase']+at*25600/span)%2
            row['pos'][0] = pusher['lo']+span*(phase if phase <= 1 else 2-phase)
            self._sync_actor(row, at, .05)

    def _aimed_shot(self, row, at):
        center = [row['pos'][j]+row['props'].get(('wmp', 'hmp')[j], 0)/2 for j in range(2)]
        target = self._player_center()
        angle = math.atan2(target[1]-center[1], target[0]-center[0])
        speed = params.SWIRL_SPEED_MP_S
        self._spawn_shot('swirlbb', row['objid'], at, (center[0]-2048, center[1]-2048),
                         (math.cos(angle)*speed, math.sin(angle)*speed))

    def _npc_tick(self, row, dt, at):
        species = (row['ai'] or {}).get('species')
        if species == 'skitter':
            self._skitter_tick(row, dt, at)
        elif species == 'pod':
            self._pod_tick(row, at)
        elif species == 'hive':
            self._hive_tick(row, at)
        elif species == 'hopper':
            self._hopper_tick(row, dt, at)
        elif species == 'orbiter':
            self._orbiter_tick(row, dt, at)
        elif species in ('egg', 'larva', 'chrysalis', 'spitter'):
            self._cocoon_tick(row, dt, at)

    def _orbiter_tick(self, row, dt, at):
        ai = row['ai']
        if row['orbit'].advance(at-dt, at):
            self._send_orbit(row, at)
        row['pos'] = row['orbit'].position(self.mouth_position)
        if at >= ai['next_shot']:
            self._aimed_shot(row, at)
            ai['next_shot'] = at+self.rng.uniform(*self.pack.entities['orbiters']['shot_interval_s'])

    def _spawn_egg(self, at, creator=None):
        if sum(bool(r['ai']) and r['ai']['species'] in ('egg', 'larva', 'chrysalis', 'spitter')
               for r in self.actors.values()) >= 16:
            self.counts['cap_skips'] += 1
            return
        source = self.actors.get(creator or self.mouth_id)
        if source is None:
            return
        props = self.spawn_props('jungle.spitegg')
        x = source['pos'][0]+source['props']['wmp']/2-props['wmp']/2
        y = source['pos'][1]+source['props']['hmp']

        vx, vy = 0., 0.
        stopped = dict(vel=dict(xvel=0, yvel=0, xaccel=0, yaccel=0))
        egg_id = self._create('jungle.spitegg', dict(props, xmp=round(x), ymp=round(y), move=stopped), at, hp=750,
                     parent=source['objid'], ai=dict(species='egg', platform=None,
                     vx=vx, vy=vy, transform=None))
        self.counts['eggs'] += 1
        self._record('egg', at, objid=egg_id, creator=source['objid'], position=[x, y], velocity=[vx, vy])
        return egg_id

    def _transform(self, row, vid, species, hp, at):
        if row['objid'] not in self.actors:
            return
        old_center = row['pos'][0]+row['props']['wmp']/2
        props = self.spawn_props(vid)
        plat = row['ai']['platform']
        x = min(max(old_center-props['wmp']/2, plat[0]), plat[0]+plat[2]-props['wmp'])
        y = plat[1]-props['hmp']
        ai = dict(species=species, platform=plat, born=at)
        if species == 'larva':
            ai.update(transform=at+1.4, direction=self.rng.choice((-1, 1)))
        elif species == 'chrysalis':
            life = self.pack.entities['cocoons']['chrysalis_lifetime_s']
            ai['transform'] = at+life
            props['lifetime'] = round(life*1000)
            props['animprops'] = dict(animid=0, time=1452)
        else:
            ai['next_shot'] = at+3.
            props['pose'], props['facing'] = 1, 0
        self._retire(row['objid'], DESTROY_INTERNAL, at)
        new = self._create(vid, dict(props, xmp=round(x), ymp=round(y), hitpoints=hp), at, hp=hp, ai=ai)
        self.counts['transforms'] += 1
        self._record('transform', at, before=row['vid'], after=vid,
                     before_objid=row['objid'], after_objid=new)

    def _cocoon_tick(self, row, dt, at):
        ai, props = row['ai'], row['props']
        species = ai['species']
        if species == 'egg':
            if ai['transform'] is None:
                old_x, old_y = row['pos']
                gravity = self.pack.entities['cocoons']['egg_gravity_mp_s2']
                nx = old_x+ai['vx']*dt
                ny = old_y+ai['vy']*dt+gravity*dt*dt/2
                ai['vy'] = min(160000., ai['vy']+gravity*dt)
                row['pos'] = [nx, ny]
                if ai['vy'] > 0:
                    crossed = [p for p in self.platforms if p[0] <= nx+props['wmp']/2 <= p[0]+p[2]
                               and old_y+props['hmp'] <= p[1]+1 and ny+props['hmp'] >= p[1]]
                    if crossed:
                        platform = min(crossed, key=lambda p: p[1])
                        if platform in self.protected_platforms:
                            self._retire(row['objid'], DESTROY_INTERNAL, at)
                            return
                        row['pos'][1] = platform[1]-props['hmp']
                        ai['platform'], ai['transform'] = platform, at+.3
                if ny >= self.pit_y:
                    self._retire(row['objid'], DESTROY_INTERNAL, at)
            elif at >= ai['transform']:
                self._transform(row, 'jungle.larva', 'larva', 180, at)
        elif species == 'larva':
            lo, _, width, _ = ai['platform']
            x = row['pos'][0]+ai['direction']*32768*dt
            if not lo <= x <= lo+width-props['wmp']:
                ai['direction'] *= -1
            row['pos'][0] = min(max(x, lo), lo+width-props['wmp'])
            if at >= ai['transform']:
                self._transform(row, 'jungle.chrysalis', 'chrysalis', 1125, at)
        elif species == 'chrysalis':
            if at >= ai['transform']:
                self._transform(row, 'jungle.spitter', 'spitter', 600, at)
        else:

            target = self._player_center()
            center = [row['pos'][0]+props['wmp']/2, row['pos'][1]+props['hmp']/2]
            acquired = (self.player is not None and self.player.alive and math.dist(target, center)
                        <= self.pack.entities['cocoons']['spitter_acquire_radius_mp'])
            ai['acquired'] = acquired
            if acquired:
                desired = [target[0]-props['wmp']/2+math.cos(at)*24576,
                           target[1]-props['hmp']/2-32768]
                distance = math.dist(desired, row['pos'])
                blend = min(1., 64000*dt/max(1., distance))
                old_x = row['pos'][0]
                row['pos'] = [row['pos'][j]+(desired[j]-row['pos'][j])*blend for j in range(2)]
                dx = row['pos'][0]-old_x
                if abs(dx) > 1:
                    facing = 0 if dx > 0 else 180
                    if facing != row['facing']:
                        row['facing'], row['props']['facing'] = facing, facing
                        self.batch.modify(row['objid'], 'FACING', intval=facing, offset=self._offset(at))
            if ai.get('release_at') is not None and at >= ai['release_at']:
                self._spawn_egg(at, creator=row['objid'])
                ai['release_at'] = None
            if at >= ai['next_shot']:

                self.batch.animprops(row['objid'], 1, 1716, offset=self._offset(at))
                ai['release_at'] = at+self.pack.entities['cocoons']['spitter_windup_s']
                ai['next_shot'] = at+self.rng.uniform(3.5, 6.)

    def _step_projectiles(self, start, end):
        dt = end-start
        for oid, row in list(self.actors.items()):
            if row['kind'] == 'projectile':
                row['pos'] = [row['pos'][j]+row['vel'][j]*dt for j in range(2)]
                if end >= row['expire'] or self._blocked(*row['pos'], row['props'], row['shot']):
                    self._retire(oid, DESTROY_LIFETIME if end >= row['expire'] else DESTROY_INTERNAL, end)
                else:
                    self._sync_actor(row, end, .05)
            elif row['kind'] == 'fx' and row['expire'] is not None and end >= row['expire']:
                self._retire(oid, DESTROY_LIFETIME, end)

    def _npc_died(self, objid, row, at):
        if (row['ai'] or {}).get('species') == 'hopper':
            self._hopper_burst(row, at)
        self._retire(objid, DESTROY_HITPOINTS, at)

    def _feedback(self, event):
        if not isinstance(event, dict) or int(event.get('ownerid') or self.worldid) != self.worldid:
            return
        victim = int(event.get('victim') or 0)
        row = self.actors.get(victim)
        if row is None:
            return
        kind = event.get('kind')
        if victim == self.mouth_id:
            if self.controller is None:
                return
            value = int(event.get('value') or 0) if kind == 'damage' else 0
            if kind == 'destroy' and int(event.get('reason') or 1) == DESTROY_HITPOINTS:
                value = self.controller.hp
            if value > 0:
                self.counts['native_damage'] += 1
                for action in self.controller.add_damage(value, victim):
                    self._action(action)
                if victim in self.actors:
                    row['hp'] = self.controller.hp
                    self.batch.modify(victim, 'HITPOINTS', intval=math.ceil(row['hp']))
            return

        if row['kind'] not in ('npc', 'projectile', 'fx'):
            return
        if kind == 'damage' and row['hp'] is not None:
            value = int(event.get('value') or 0)
            if value <= 0:
                return
            self.counts['native_damage'] += 1
            row['hp'] -= value
            if row['hp'] <= 0:
                self._npc_died(victim, row, self.room_time)
            else:
                self.batch.modify(victim, 'HITPOINTS', intval=math.ceil(row['hp']))
        elif kind == 'destroy':
            reason = int(event.get('reason') or 1)
            self.counts['native_destroy'] += 1
            species = (row['ai'] or {}).get('species')
            if species == 'pod' and reason == DESTROY_LIFETIME:
                self._pod_fire(row, self.room_time)
            elif species == 'chrysalis' and reason == DESTROY_LIFETIME:
                self._transform(row, 'jungle.spitter', 'spitter', 600, self.room_time)
            else:
                self._retire(victim, reason, self.room_time, native=True)

    def _victory(self, at):
        self.phase = 'defeated'
        parts = set(self.pack.scene['segments']+[self.pack.scene['head'], self.mouth_id])
        for oid, row in list(self.actors.items()):
            if oid in parts or row['kind'] in ('npc', 'projectile', 'fx'):
                self._retire(oid, DESTROY_INTERNAL, at)
        for oid in self.pack.scene['doors']:
            self.batch.modify(oid, 'VID', stringval='jungle.door', offset=self._offset(at))
            self.batch.modify(oid, 'INTERACT', intval=1, offset=self._offset(at))
            self.actors[oid]['vid'] = 'jungle.door'

        known = getattr(self.server, 'item_types', None)
        pool = [n for n in (126, 94, 250, 164, 176, 170, 247) if known is None or n in known]
        types = self.rng.sample(pool, min(3, len(pool)))
        if known is None or 18 in known:
            types.append(18)
        if types:
            self.batch.extra['createloot_'] = dict(props_={'xmp_': round(self.mouth_position[0]),
                                                          'ymp_': round(self.mouth_position[1])},
                                                   itemprops_=[{'type_': n} for n in types])
        self._record('victory', at, bars=13, reward_types=types)

    def _advance_world(self, start, end):
        if end-start <= 1e-10:
            return
        self.room_time = end
        if self.phase != 'defeated':
            self._step_worm(end-start, end)
            self._step_npcs(start, end)
            self._step_projectiles(start, end)
            self._step_pincers(start, end)
        self._step_pushers(end)

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        state, _ = self.bridge.snapshot()
        if state is not None:
            self.player = state
        self._frame_start = self.room_time
        if self.controller is None and state is not None and state.alive and state.age() < 2.:
            self.controller = JungleMidController(self.pack, self.seed)
            self.phase = 'idle'
            for event in self.controller.begin():
                self._action(event)
            self._record('fight_start', 0.)
        for event in self.bridge.poll_events():
            self._feedback(event)
        if self.controller is not None:
            end = self.room_time+dt
            while self.room_time < end-1e-9:
                start = self.room_time
                target = min(end, start+1/self.HZ)
                actions = self.controller.step(target-start)
                for event in actions:
                    self._advance_world(self.room_time, event['at'])
                    self._action(event)
                self._advance_world(self.room_time, target)
        self._wire_ms += dt*1000
        target_ms = round(self._wire_ms)
        self.batch.dt, self._sent_ms = target_ms-self._sent_ms, target_ms
        self.server._send_raw(self.channel, 12, self.batch.encode(), 'jungle-mid-live', record=False)
        self.frames += 1
        self.batch.clear()
        self.supplies.flush()

    def status(self):
        return dict(map='jungle_mid', phase=self.phase, time=round(self.room_time, 3),
                    controller=self.controller.status() if self.controller else None,
                    actors=len(self.actors), counts=dict(self.counts), frames=self.frames,
                    mouth_id=self.mouth_id, seed=self.seed,
                    alive_orbiters=sum((r['ai'] or {}).get('species') == 'orbiter' for r in self.actors.values()))
