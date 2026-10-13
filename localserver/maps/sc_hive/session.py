"""SC Hive live host: native sprites/physics/sounds plus fitted curved flights.

No Hulk or ETL encounter modules are imported. Native resolved damage and
destroy feedback is authoritative; wall sweeps bound server-side actors.
"""
import math
import os
import random
import threading
import time

from ... import pb_layout
from ...live.bridge import PlayerBridge, PlayerState
from ...live.wire import DeltaBatch
from .controller import ScHiveController
from .scene import ScHivePack, VELOCITY_TO_MP_S

def sweep_entry(start, end, size, rect, one_way=False):
    """First fraction where a moving AABB enters a solid rectangle.

    ``one_way`` marks TERRAIN_LEDGE platforms: they only block a body that
    was above the top edge and is moving down onto it, so bullets and slivers
    can still pass upward through a platform like the player does.
    """
    x, y, w, h = rect
    bw, bh = size
    lo, hi = 0., 1.
    for a, b, lower, upper in ((start[0], end[0], x-bw, x+w),
                               (start[1], end[1], y-bh, y+h)):
        delta = b-a
        if abs(delta) < 1e-12:
            if a < lower or a > upper:
                return None
            continue
        near, far = (lower-a)/delta, (upper-a)/delta
        lo, hi = max(lo, min(near, far)), min(hi, max(near, far))
        if lo > hi:
            return None
    if not (0 <= lo <= 1):
        return None
    if one_way:
        start_bottom = start[1]+bh
        end_bottom = end[1]+bh
        if end_bottom <= y+1e-9 or start_bottom > y+1e-9:
            return None
    return lo

class ScHiveLiveSession(object):
    HZ = 60.

    def __init__(self, server, channel, options, room=None, log=None,
                 bridge=None, pack=None):
        self.server, self.channel, self.options, self.room = server, channel, options, room
        self.log = log or (lambda message: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        root = os.path.dirname(getattr(options, 'create_world_hex', '') or '')
        self.pack = pack or ScHivePack(root or None)
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550), log=self.log)
        self.owns_bridge = bridge is None
        self.player_type = 1
        self._stop = threading.Event()
        self._thread = None
        self.prepare()

    def prepare(self):
        self.batch.clear()
        self.phase = 'waiting'
        self.room_time = 0.
        self.fight_started = None
        self.player = None
        self.controller = None
        self.spirits, self.slivers, self.effects = {}, {}, {}
        self.events = []
        self.frames = 0
        self.total_damage = 0
        self.overlay_objid = self.pack.overlay_id
        self._removed_overlay_ids = set()

        self.static_ids = set(int(i) for i in self.pack.templates)
        self.static_ids.discard(self.overlay_objid)

        self._guide_rng = random.Random(0x5C1B)
        self._demo_early_damage = self._demo_final_damage = False
        self._next_objid = max(int(i) for i in self.pack.templates) + 1000
        self._wire_time_ms = 0.
        self._sent_time_ms = 0
        rect = lambda p: tuple(p.get(key, 0) for key in ('xmp', 'ymp', 'wmp', 'hmp'))

        self.terrain = [rect(p) for p in self.pack.templates.values()
                        if p.get('terraintype') == 1]
        self.ledges = [rect(p) for p in self.pack.templates.values()
                       if p.get('terraintype') == 2]
        if getattr(self.options, 'player_x', None) is not None:
            self.player = PlayerState(objid=int(getattr(self.options, 'player_objid', 1) or 1),
                xmp=self.options.player_x, ymp=self.options.player_y,
                wmp=10240, hmp=20480, hp=150, maxhp=150)

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.log('sc_hive live session started (phase=waiting)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        dt, deadline = 1./self.HZ, time.time()
        while not self._stop.is_set():
            deadline += dt
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log('sc_hive tick failed: '+traceback.format_exc())
                self._stop.set()
                break
            wait = deadline-time.time()
            if wait > 0:
                self._stop.wait(wait)
            else:
                deadline = time.time()

    def _event(self, kind, at=None, **values):
        self.events.append(dict(values, kind=kind,
                                at=self.room_time if at is None else at))

    def _alloc(self):
        self._next_objid += 1
        return self._next_objid

    def _ownerid(self):

        return self.worldid

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        state, _keys = self.bridge.snapshot()
        if state is not None:
            self.player = state
        if self.phase == 'waiting' and self.player is not None:
            self.fight_started = self.room_time
            self.controller = ScHiveController(self.pack)
            self.phase = 'fighting'
            self._event('fight_start')
        for event in self.bridge.poll_events():
            self._feedback(event)
        start = self.room_time
        self.room_time += dt
        if self.controller:

            if getattr(self.options, 'live_demo_damage', False) and self.controller.state == 'fighting':
                if self.controller.time >= 16. and not self._demo_early_damage:
                    self._demo_early_damage = True
                    self.controller.add_damage(self.controller.hp, self.pack.overlay_id)
                if self.controller.time >= 61.25 and not self._demo_final_damage:
                    self._demo_final_damage = True
                    self.controller.add_damage(self.controller.hp, self.pack.overlay_id)
            for event in self.controller.step(dt):
                self._handle_event(event)
            self.phase = self.controller.state
        self._step_actors(start, self.room_time)
        for objid, expiry in list(self.effects.items()):
            if expiry <= self.room_time:
                self.batch.destroy(objid, ownerid=self._ownerid(), reason='LIFETIME')
                del self.effects[objid]
        self._wire_time_ms += dt*1000.
        target_ms = int(round(self._wire_time_ms))
        self.batch.dt = target_ms-self._sent_time_ms
        self._sent_time_ms = target_ms
        self.server._send_raw(self.channel, 12, self.batch.encode(), 'schive-live', record=False)
        self.frames += 1
        self.batch.clear()

    def native_retirement_capability(self,objid):
        if type(objid)is not int or objid<=0:return None
        if objid==self.overlay_objid:
            return dict(ownerid=self.worldid,objid=objid,role='npc',expires_at=None,
                room_time=self.room_time,lifetime_due=False)
        if objid in self.spirits or objid in self.slivers:

            return dict(ownerid=self.worldid,objid=objid,role='projectile',expires_at=None,
                room_time=self.room_time,lifetime_due=False)
        if objid in self.effects:
            expiry=self.effects[objid]
            return dict(ownerid=self.worldid,objid=objid,role='fx',expires_at=expiry,
                room_time=self.room_time,lifetime_due=self.room_time>=expiry)
        return None

    def _feedback(self, event):
        if not isinstance(event, dict):
            return

        if 'ownerid' in event and int(event['ownerid'] or 0) != self.worldid:
            return
        victim = int(event.get('victim') or 0)
        if event.get('kind') == 'damage' and self.controller:
            value = int(event.get('value') or 0)
            if victim == self.overlay_objid and value > 0 and self.controller.state == 'fighting':
                self.total_damage += value
                self.controller.add_damage(value, self.pack.overlay_id)
        elif event.get('kind') == 'destroy':
            reason = int(event.get('reason') or 0)
            if victim in self.spirits or victim in self.slivers:
                actors = self.spirits if victim in self.spirits else self.slivers

                if reason in (3, 4, 5):
                    if victim in self.spirits:
                        self._retire_spirit(victim, reason, self.room_time)
                    else:
                        self._retire_sliver(victim, reason, self.room_time)
                elif reason in (2, 6):
                    self._revive_projectile(actors, victim, reason, self.room_time)
            elif (self.controller and victim == self.overlay_objid and reason == 2
                  and self.controller.state == 'fighting'):
                self._removed_overlay_ids.add(victim)

                display_hp = max(1, self.controller.hp)
                self._restore_overlay(display_hp)
                self._event('overlay_repair', hp=int(display_hp))
            elif victim in self.static_ids and reason in (2, 3, 4, 5):

                self._event('static_destroyed', at=self.room_time, objid=victim,
                            reason=reason)

    def _revive_projectile(self, actors, objid, reason, at):
        """Put a projectile the client removed by mistake back on the wire.

        No hitpoints has-bit makes the original shots unattackable. A stale
        HITPOINTS or LIFETIME removal must not impose an accidental range cap.
        """
        row = actors.pop(objid, None)
        if row is None:
            return
        speed = math.hypot(row['vx'], row['vy']) or self.pack.entities['spirit']['speed_mp_s']
        heading = math.degrees(math.atan2(row['vy'], row['vx']))
        fresh = self._create_actor(row['vid'], row['x'], row['y'], row['facing'],
                                   speed, row['creator'], at, velocity_deg=heading)
        for key in ('rolled', 'steer_until', 'turn_dir', 'turn_step', 'pose', 'next_turn'):
            if key in row:
                fresh[key] = row[key]
        actors[fresh['objid']] = fresh
        self._event('projectile_revive', at=at, objid=fresh['objid'],
                    previous=objid, reason=reason)

    def _restore_overlay(self, hp):
        'Recreate an overlay the client already removed, with a fresh id.'
        props = self.pack.wire_props(self.pack.overlay_id)
        props.update(hitpoints_=int(hp), invulnerable_=False)
        self.overlay_objid = self._alloc()
        self.batch.create(self.overlay_objid, props)

    def _handle_event(self, event):
        kind, at = event['kind'], event['at']+self.fight_started
        if kind in ('hive_hp', 'hive_refill'):
            self.batch.modify(self.overlay_objid, 'HITPOINTS', intval=event['hp'])
            if kind == 'hive_refill':
                self.batch.modify(self.overlay_objid, 'INVULNERABLE', boolval=False)
        elif kind == 'summon':
            rule = self.pack.entities['spawner']
            self.batch.modify(event['spawner'], 'ANIMPROPS', message=('AnimationProperties',
                dict(animid_=rule['animid'], time_=rule['anim_time_ms'], pos_=event['anim_pos_ms'])))
        elif kind == 'spirit_spawn':
            self._spawn_spirit(event['spawner'], at)
        elif kind == 'hive_destroy':
            owner = self._ownerid()
            self.batch.destroy(self.overlay_objid, ownerid=owner, reason='HITPOINTS')
            for objid in [event['hive']] + event['spawners']:
                self.batch.destroy(objid, ownerid=owner, reason='INTERNAL')
        elif kind == 'doors_open':
            for row in self.pack.scene['doors']:
                if row['property'] == 43:
                    self.batch.modify(row['objid'], 'VID', stringval=row['value'])
                elif row['property'] == 25:
                    self.batch.modify(row['objid'], 'INTERACT', intval=row['value'])
        self._event(kind, at=at, **{key: value for key, value in event.items()
                                   if key not in ('kind', 'at')})

    def _create_actor(self, vid, x, y, facing, speed, creator, at, velocity_deg=None):
        objid = self._alloc()

        angle = math.radians(facing if velocity_deg is None else velocity_deg)
        vx, vy = speed*math.cos(angle), speed*math.sin(angle)
        wx, wy = int(round(vx/VELOCITY_TO_MP_S)), int(round(vy/VELOCITY_TO_MP_S))
        props = self.pack.wire_props(vid)
        if vid in ('sc.spirit', 'sc.sliver'):

            props.pop('lifetime_', None)
        props.update(xmp_=int(round(x)), ymp_=int(round(y)), facing_=int(round(facing)),
                     move_={'vel_': {'xvel_': wx, 'yvel_': wy}})
        if vid == 'sc.spirit':
            props['pose_'] = 2
        self.batch.create(objid, props, creatorid=creator)
        self.batch.sound(objid, vid)
        actor = dict(objid=objid, vid=vid, x=float(x), y=float(y), w=props['wmp_'], h=props['hmp_'],
                     facing=float(facing), vx=wx*VELOCITY_TO_MP_S, vy=wy*VELOCITY_TO_MP_S,
                     born=at, creator=creator)
        self._event('actor_create', at=at, objid=objid, vid=vid, creator=creator)
        return actor

    def _spawn_spirit(self, spawner, at):
        spec = next(row for row in self.pack.spawners if row['objid'] == spawner)
        parent = self.pack.template(spawner)
        dx, dy = spec['spawn_offset_mp']
        rule = self.pack.entities['spirit']
        facing = float(spec.get('initial_facing', spec['facing']))
        row = self._create_actor('sc.spirit', parent['xmp']+dx, parent['ymp']+dy,
                                facing, rule['speed_mp_s'], spawner, at)
        windows = self.pack.steering_windows(spawner)
        if not windows:
            raise ValueError('no complete steering-window evidence for pod %s' % spawner)
        window = self._guide_rng.choice(windows)
        row['steer_until'] = at + window

        row.update(turn_dir=spec.get('turn_dir', 1),
                   turn_step=rule['turn_deg_s']*rule['decision_s'],
                   rolled=0, pose=2, next_turn=at+rule['decision_s'])
        self.spirits[row['objid']] = row
        self._event('spirit_steering_window', at=at, objid=row['objid'], duration_s=window)

    def _retire_spirit(self, objid, reason, at):
        row = self.spirits.pop(objid, None)
        if row is None:
            return
        self.batch.move_to(objid, int(round(row['x'])), int(round(row['y'])))
        self._event('spirit_destroy', at=at, objid=objid, reason=reason)
        if reason not in (3, 4, 5):
            self.batch.destroy(objid, ownerid=self._ownerid(), reason=reason)
            return
        spec = self.pack.entities['spirit']
        props = self.pack.dynamic_templates['sc.sliver']
        x = row['x']+(row['w']-props['wmp'])/2.
        y = row['y']+(row['h']-props['hmp'])/2.

        x, y = self._clear_split_origin(x, y, props, row['facing'])
        children = []
        for sign in (1, -1):

            heading = row['facing']+sign*spec['split_velocity_deg']
            child = self._create_actor('sc.sliver', x, y, heading,
                self.pack.entities['sliver']['speed_mp_s'], objid, at,
                velocity_deg=heading)
            self.slivers[child['objid']] = child
            children.append(child['objid'])

        self.batch.destroy(objid, ownerid=self._ownerid(), reason=reason)
        self._event('spirit_split', at=at, objid=objid, children=children,
                    facing=row['facing'], reason=reason)

    def _retire_sliver(self, objid, reason, at):
        row = self.slivers.pop(objid, None)
        if row is None:
            return
        self.batch.move_to(objid, int(round(row['x'])), int(round(row['y'])))
        self._event('sliver_destroy', at=at, objid=objid, reason=reason)
        if reason in (3, 4, 5):

            fxid = self._alloc()
            props = self.pack.wire_props('sc.sliverx')
            props.update(xmp_=int(round(row['x']+(row['w']-props['wmp_'])/2.)),
                         ymp_=int(round(row['y']+(row['h']-props['hmp_'])/2.)))
            self.batch.create(fxid, props, creatorid=objid)
            self.batch.sound(fxid, 'sc.sliverx')
            self.effects[fxid] = at+props['lifetime_']/1000.
            self._event('sliver_fx', at=at, objid=fxid, creator=objid)
        self.batch.destroy(objid, ownerid=self._ownerid(), reason=reason)

    def _clear_split_origin(self, x, y, props, facing):
        """Slide a split origin backwards until the child box leaves terrain.

        Like ``sweep_entry``, positions are top-left and sizes are full extents.
        """
        bw, bh = props['wmp'], props['hmp']
        blockers = list(self.terrain) + list(self.ledges)
        step = 256.
        for _ in range(64):
            if not any(rect[0] < x+bw and rect[1] < y+bh
                       and x < rect[0]+rect[2] and y < rect[1]+rect[3]
                       for rect in blockers):
                return x, y
            x -= math.cos(math.radians(facing))*step
            y -= math.sin(math.radians(facing))*step
        return x, y

    def _turn(self, row):
        """Limited turn toward a player anywhere in the room, then straight."""
        rule = self.pack.entities['spirit']
        if row['next_turn'] <= row['steer_until'] + 1e-9:
            row['rolled'] += 1
            delta = row['turn_dir'] * row['turn_step']
            from ...participants import from_bridge, nearest
            target = nearest(from_bridge(self.bridge, self.player),
                             (row['x']+row['w']/2., row['y']+row['h']/2.))
            if target is not None:
                tx, ty = target.center()
                bearing = math.degrees(math.atan2(ty-row['y']-row['h']/2.,
                                                  tx-row['x']-row['w']/2.))
                error = (bearing-row['facing']+180.) % 360. - 180.
                delta = max(-row['turn_step'], min(row['turn_step'], error))
            old_facing = int(round(row['facing']))
            row['facing'] += delta
            if int(round(row['facing'])) != old_facing:
                self.batch.modify(row['objid'], 'FACING', intval=int(round(row['facing'])))
            pose = 3 if 45 < row['facing'] % 180 <= 135 else 2
            if pose != row['pose']:
                row['pose'] = pose
                self.batch.modify(row['objid'], 'POSE', intval=pose)
        angle = math.radians(row['facing'])
        row['vx'] = rule['speed_mp_s']*math.cos(angle)
        row['vy'] = rule['speed_mp_s']*math.sin(angle)
        wx = int(round(row['vx']/VELOCITY_TO_MP_S))
        wy = int(round(row['vy']/VELOCITY_TO_MP_S))
        row['vx'], row['vy'] = wx*VELOCITY_TO_MP_S, wy*VELOCITY_TO_MP_S
        self.batch.modify(row['objid'], 'MOVE', message=('Properties_Move',
            {'vel_': {'xvel_': wx, 'yvel_': wy}}))
        self.batch.move_to(row['objid'], int(round(row['x'])), int(round(row['y'])),
                           dt=max(1, int(round(rule['decision_s']*1000))))
        row['next_turn'] += rule['decision_s']

    def _step_actors(self, start, end):
        for actors, spirit in ((self.spirits, True), (self.slivers, False)):
            for objid, row in list(actors.items()):
                cursor = max(start, row['born'])
                while cursor < end-1e-9 and objid in actors:
                    if spirit and row['next_turn'] <= cursor+1e-9:
                        self._turn(row)
                    boundary = min(end, row['next_turn']) if spirit else end
                    duration = max(0., boundary-cursor)
                    origin = (row['x'], row['y'])
                    target = (row['x']+row['vx']*duration, row['y']+row['vy']*duration)
                    if spirit:
                        sweep = [(rect, False) for rect in self.terrain]
                    else:
                        sweep = ([(rect, False) for rect in self.terrain]
                                 + [(rect, True) for rect in self.ledges])
                    size = (row['w'], row['h'])
                    hits = [sweep_entry(origin, target, size, rect,
                                        one_way=one_way)
                            for rect, one_way in sweep]
                    hits = [fraction for fraction in hits if fraction is not None]
                    fraction = min(hits) if hits else 1.
                    row['x'] = origin[0]+(target[0]-origin[0])*fraction
                    row['y'] = origin[1]+(target[1]-origin[1])*fraction
                    if hits:
                        when = cursor+duration*fraction
                        if spirit:
                            self._retire_spirit(objid, 5, when)
                        else:
                            self._retire_sliver(objid, 5, when)
                        break
                    cursor = boundary

    def status(self):
        info = dict(map='sc_hive', phase=self.phase, time=round(self.room_time, 3),
                    spirits=len(self.spirits), slivers=len(self.slivers), effects=len(self.effects),
                    damage=self.total_damage, frames=self.frames)
        info['overlay_objid'] = self.overlay_objid
        if self.controller:
            info['hive'] = self.controller.status()
        if self.player:
            info['player'] = dict(x=self.player.xmp, y=self.player.ymp, hp=self.player.hp)
        return info
