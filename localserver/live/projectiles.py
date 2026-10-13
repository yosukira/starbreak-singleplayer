"""Server-authoritative projectile physics for the live Boss controller."""

import math
import random

from . import constants
from .wire import DESTROY_REASON
from .terrain import terrain_type, segment_hits_rect

def _angle_vector(degrees):
    rad = math.radians(degrees)
    return math.cos(rad), math.sin(rad)

def rects_overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])

class Projectile(object):
    __slots__ = ('rules', 'objid', 'vid', 'owner', 'x', 'y', 'vx', 'vy', 'w', 'h',
                 'damage', 'kind', 'age', 'lifetime', 'homing', 'turn_rate',
                 'accel', 'max_speed', 'burst_vid', 'alive', 'rotate',
                 'knockback', 'gravity', 'hit_cooldown', 'bounces', 'death_reason',
                 'prepared', 'launch_damage', 'target_locked', 'lock_range',
                 'lock_cone', 'grounded', 'velocity_command', 'remove_type',
                 'steering_angle', 'steering_speed', 'steering_time_left',
                 'support', 'support_rect', 'carry_velocity', 'owner_type',
                 'target_player_key', 'hit_cooldowns')

    def __init__(self, objid, vid, owner, x, y, vx, vy, width, height,
                 damage, kind='straight', lifetime=6.0, homing=False,
                 turn_rate=0.0, accel=0.0, max_speed=0.0, burst_vid=None,
                 rotate=True, knockback=0, gravity=0.0, owner_type=2, rules=constants):
        self.rules = rules
        self.objid = int(objid)
        self.vid = vid
        self.owner = int(owner)
        self.owner_type = int(owner_type)
        self.x = int(x)
        self.y = int(y)
        self.vx = float(vx)
        self.vy = float(vy)
        self.w = int(width)
        self.h = int(height)
        self.damage = int(damage)
        self.kind = kind
        self.age = 0.0
        self.lifetime = float(lifetime)
        self.homing = bool(homing)
        self.turn_rate = float(turn_rate)
        self.accel = float(accel)
        self.max_speed = float(max_speed)
        self.burst_vid = burst_vid
        self.rotate = bool(rotate)
        self.knockback = int(knockback)
        self.gravity = float(gravity)
        self.alive = True
        self.hit_cooldown = 0.0
        self.hit_cooldowns = {}
        self.bounces = 0
        self.death_reason = 'LIFETIME'
        self.prepared = False
        self.launch_damage = int(damage)
        self.target_locked = False
        self.target_player_key = None
        self.lock_range = self.rules.MISSILE_LOCK_RANGE
        self.lock_cone = self.rules.MISSILE_LOCK_CONE_DEG
        self.grounded = False
        self.velocity_command = None
        self.remove_type = 0

        self.steering_angle=math.atan2(self.vy,self.vx)
        self.steering_speed=math.hypot(self.vx,self.vy)
        self.steering_time_left=0.0

        self.support=None
        self.support_rect=None
        self.carry_velocity=(0.0,0.0)
        if self.homing:self._set_homing_velocity()

    def flight_pose(self):

        return 3 if abs(self.vy) > abs(self.vx) else 2

    def burst_effect(self):

        reason = (self.death_reason if isinstance(self.death_reason, int)
                  else DESTROY_REASON[self.death_reason])
        if not self.burst_vid or reason not in (
                DESTROY_REASON['BLOCKED'], DESTROY_REASON['LIFETIME']):
            return None

        if self.vid == 'hulk.dronebomb' and reason == DESTROY_REASON['BLOCKED']:
            return 'hulk.cannonshotx'
        return self.burst_vid

    def impact_burst(self):
        return self.burst_effect() is not None

    def set_velocity_command(self, command):
        """Original Move_Vel values: velocity /256 per ms, acceleration per ms.

        WorldObject::addTime (0x451c52) clamps each axis independently and
        integrates the average old/new velocity. Diagonal loader flames
        cannot be reproduced with scalar acceleration along the aim ray.
        """
        self.velocity_command = dict(command['vel_'])
        self.vx = self.velocity_command.get('xvel_',0)*1000/256
        self.vy = self.velocity_command.get('yvel_',0)*1000/256

    def _step_velocity_command(self, dt):
        vel = self.velocity_command
        for axis,position in (('x','x'),('y','y')):
            attribute='v'+axis
            previous=getattr(self,attribute)
            following=previous+vel.get(axis+'accel_',0)*1000000/256*dt
            minimum=vel.get('min'+axis+'vel_')
            maximum=vel.get('max'+axis+'vel_')
            if minimum is not None:following=max(following,minimum*1000/256)
            if maximum is not None:following=min(following,maximum*1000/256)
            setattr(self,attribute,following)
            setattr(self,position,getattr(self,position)+(previous+following)*dt/2)

    def rect(self):
        return (int(self.x), int(self.y), self.w, self.h)

    def speed(self):
        return math.hypot(self.vx, self.vy)

    def _set_homing_velocity(self):
        quantum=self.rules.MISSILE_VELOCITY_QUANTUM
        self.vx=math.floor(math.cos(self.steering_angle)*self.steering_speed/quantum+1e-9)*quantum
        self.vy=math.floor(math.sin(self.steering_angle)*self.steering_speed/quantum+1e-9)*quantum

    def _step_homing(self,dt,target):
        remaining=dt
        interval=self.rules.MISSILE_STEER_SECONDS
        while remaining>1e-9:
            if self.steering_time_left<=1e-9:
                wanted=None;delta=0.0
                if target is not None:
                    tx,ty=target[0]-(self.x+self.w/2),target[1]-(self.y+self.h/2)
                    wanted=math.atan2(ty,tx)
                    delta=(wanted-self.steering_angle+math.pi)%(2*math.pi)-math.pi
                    if (not self.target_locked and math.hypot(tx,ty)<=self.lock_range
                            and abs(math.degrees(delta))<=self.lock_cone):
                        self.target_locked=True
                if self.target_locked:
                    limit=math.radians(self.turn_rate)*interval
                    if wanted is not None:self.steering_angle+=max(-limit,min(limit,delta))
                    if self.accel:
                        self.steering_speed=min(self.max_speed or math.inf,self.steering_speed+self.accel*interval)
                self._set_homing_velocity()
                self.steering_time_left=interval
            travel=min(remaining,self.steering_time_left)
            self.x+=self.vx*travel;self.y+=self.vy*travel
            remaining-=travel;self.steering_time_left-=travel

    def step(self, dt, target=None, defer_expiry=False):
        if not self.alive:
            return
        if self.prepared:
            return

        remaining = self.lifetime-self.age if self.lifetime > 0 else math.inf
        self.age += dt
        self.hit_cooldown = max(0.0, self.hit_cooldown-dt)
        if (self.lifetime > 0 and self.age >= self.lifetime
                and (not defer_expiry or remaining <= 0)):
            self.alive = False
            return
        if defer_expiry:

            dt = min(dt, remaining)
        if self.velocity_command is not None:
            self._step_velocity_command(dt)
            return
        if self.homing:
            self._step_homing(dt,target)
            return
        previous_vy = self.vy
        if self.kind in ('falling', 'bouncing', 'napalm') and not self.grounded:
            if (self.kind == 'bouncing' and self.bounces == 0
                    and self.vy >= self.rules.GRENADE_APEX_YVEL):

                self.gravity = self.rules.GRENADE_BOUNCE_GRAVITY
            self.vy += (self.gravity or 786432) * dt
            if self.kind == 'bouncing':
                self.vy = min(self.vy, self.rules.GRENADE_FALL_CAP)
            elif self.kind == 'napalm':
                self.vy = min(self.vy, self.rules.NAPALM_FALL_CAP)
        if self.kind not in ('falling', 'bouncing', 'napalm') and not self.homing and self.accel:
            speed = self.speed()
            if speed > 0:
                speed = min(self.max_speed or 1e12, speed + self.accel * dt)
                scale = speed / self.speed()
                self.vx *= scale
                self.vy *= scale
        self.x += self.vx * dt

        travel_vy = ((previous_vy+self.vy)/2 if self.vid == 'hulk.dronebomb'
                     and self.kind == 'falling' and not self.grounded else self.vy)
        self.y += travel_vy * dt

class ProjectileSystem(object):
    """Owns every live projectile and resolves player collisions."""

    def __init__(self, room, first_objid=40000, seed=None, rules=constants):
        self.rules = rules
        self.room = room
        self.projectiles = []
        self.next_objid = int(first_objid)
        self.random = random.Random(seed)

    def allocate(self):
        self.next_objid += 1
        return self.next_objid

    def spawn(self, **values):
        values.setdefault('rules', self.rules)
        projectile = Projectile(values.pop('objid', self.allocate()),
                                **values)
        self.projectiles.append(projectile)
        return projectile

    def fire_csbullet(self, owner, origin, aim_degrees):
        vx, vy = _angle_vector(aim_degrees)
        speed = self.rules.CSBULLET_SPEED
        return self.spawn(
            vid='hulk.csbullet', owner=owner, x=origin[0] - 3584,
            y=origin[1] - 3584, vx=vx * speed, vy=vy * speed, width=7168,
            height=7168, damage=self.rules.CSBULLET_DAMAGE, lifetime=40.0,
            burst_vid=None, knockback=0)

    def fire_scattershot(self, owner, origin, aim_degrees):
        pellets = []
        spread = self.rules.SCATTERSHOT_SPREAD_DEG
        for index in range(self.rules.SCATTERSHOT_PELLETS):

            angle = aim_degrees + self.random.randint(-int(spread / 2), int(spread / 2))
            vx, vy = _angle_vector(angle)
            speed = self.rules.SCATTERSHOT_SPEED
            pellets.append(self.spawn(
                vid='hulk.scattershot', owner=owner,
                x=origin[0] - 2048, y=origin[1] - 2048,
                vx=vx * speed, vy=vy * speed, width=4096, height=4096,
                damage=self.rules.SCATTERSHOT_DAMAGE, lifetime=6.0,
                burst_vid='hulk.scattershotx'))
        return pellets

    def fire_bossmissile(self, owner, origin, aim_degrees):
        vx, vy = _angle_vector(aim_degrees)
        speed = self.rules.BOSS_MISSILE_SPEED
        return self.spawn(
            vid='hulk.bossmissile', owner=owner, x=origin[0] - 4096,
            y=origin[1] - 4096, vx=vx * speed, vy=vy * speed, width=8192,
            height=8192, damage=self.rules.BOSS_MISSILE_DAMAGE,
            lifetime=self.rules.BOSS_MISSILE_LIFETIME, homing=True,
            turn_rate=self.rules.BOSS_MISSILE_TURN_DEG,
            accel=self.rules.BOSS_MISSILE_ACCEL,
            max_speed=self.rules.BOSS_MISSILE_MAX_SPEED,
            burst_vid='hulk.bossmissilex', knockback=0, rotate=False)

    def fire_enemy_shot(self, owner, vid, origin, aim_degrees, damage,
                        speed, homing=False, turn_rate=90.0, kind='straight',
                        gravity=0.0, accel=0.0, max_speed=0.0):
        spec = self.rules.ENEMY_SHOTS.get(vid) or {}
        vx, vy = _angle_vector(aim_degrees)
        launch_direction = 1 if vx >= 0 else -1
        size = int(spec.get('size', 2048))
        height = int(spec.get('height', size))
        if vid == 'boundinggrenade':
            vx = self.rules.GRENADE_THROW_XVEL * (1 if vx >= 0 else -1)
            vy = self.rules.GRENADE_THROW_YVEL
        else:
            vx, vy = vx * speed, vy * speed
        projectile = self.spawn(
            vid=spec.get('vid', 'hulk.' + vid), owner=owner,
            x=origin[0] - size // 2, y=origin[1] - height // 2,
            vx=vx, vy=vy, width=size, height=height,
            damage=damage,
            lifetime=spec.get('lifetime', 5.0), homing=homing,
            turn_rate=turn_rate, kind=kind, gravity=gravity, accel=accel,
            max_speed=max_speed,
            burst_vid=spec.get('burst_vid'), rotate=not homing)
        if projectile.vid == 'hulk.dronebomb':

            projectile.rotate = False
        if spec.get('launch_yvel') is not None:
            if spec.get('horizontal_speeds'):
                projectile.vx = self.random.choice(spec['horizontal_speeds']) * launch_direction
            projectile.vy = spec['launch_yvel']

            projectile.set_velocity_command({'vel_': {
                'xvel_': int(round(projectile.vx*256/1000)),
                'yvel_': int(round(projectile.vy*256/1000)),
                'yaccel_': int(round(spec.get('gravity', 0)*256/1000000))}})
        if vid == 'rpg':

            facing = -1 if vx < 0 else 1
            projectile.rotate = False
            projectile.set_velocity_command({'vel_': {
                'xvel_': 10000 * facing, 'yvel_': 0,
                'xaccel_': 12 * facing, 'minxvel_': -40000,
                'maxxvel_': 40000}})
        elif vid == 'ngrenade':

            facing = -1 if vx < 0 else 1
            projectile.rotate = False
            projectile.set_velocity_command({'vel_': {
                'xvel_': 12500 * facing, 'yvel_': -20000, 'yaccel_': 25}})
        return projectile

    def step(self, dt, player, events, resolve_player=True, target_for=None, players=None):
        'Advance every projectile; append player-damage events.'
        from ..participants import active_players, nearest, life_identity
        candidates = active_players(players if players is not None else (player,))
        target = player.center() if player is not None and player.alive else None
        surfaces = [(item.objid, item.rect(), terrain_type(item))
                    for item in self.room.objects.values()
                    if terrain_type(item) in (1, 2)]
        for projectile in list(self.projectiles):
            if not projectile.alive:
                continue
            if projectile.prepared:
                continue
            self._ride_support(projectile, dt)
            old = projectile.rect()
            selected = None
            if target_for is not None:
                steering = target_for(projectile, target)
            elif projectile.homing:
                if projectile.target_player_key is not None:
                    selected = next((p for p in candidates
                                     if life_identity(p) == projectile.target_player_key), None)
                else:
                    eligible = []
                    for candidate in candidates:
                        tx, ty = candidate.center()
                        dx, dy = tx-projectile.x-projectile.w/2, ty-projectile.y-projectile.h/2
                        error = (math.atan2(dy, dx)-projectile.steering_angle+math.pi) % (2*math.pi)-math.pi
                        if (math.hypot(dx, dy) <= projectile.lock_range
                                and abs(math.degrees(error)) <= projectile.lock_cone
                                and not any(segment_hits_rect(
                                    (projectile.x+projectile.w/2, projectile.y+projectile.h/2),
                                    (tx, ty), rect) for _, rect, kind in surfaces if kind == 1)):
                            eligible.append(candidate)
                    selected = nearest(eligible, (projectile.x+projectile.w/2,
                                                  projectile.y+projectile.h/2))
                    if selected is None and len(candidates) == 1:

                        selected = candidates[0]
                steering = selected.center() if selected is not None else None
            elif projectile.target_locked:
                selected = nearest(candidates, (projectile.x+projectile.w/2, projectile.y+projectile.h/2))
                steering = selected.center() if selected is not None else None
            else:

                steering = None
            if (projectile.homing and not projectile.target_locked and steering is not None
                    and any(segment_hits_rect((projectile.x+projectile.w/2,
                                               projectile.y+projectile.h/2), steering, rect)
                            for _objid, rect, kind in surfaces if kind == 1)):
                steering = None
            fire_fuse = projectile.vid in ('hulk.dronebomb', 'hulk.ngrenade')
            projectile.step(dt, steering, defer_expiry=fire_fuse)
            if projectile.target_locked and selected is not None and projectile.target_player_key is None:
                projectile.target_player_key = life_identity(selected)
            if not projectile.alive:
                continue
            if self._outside_room(projectile):
                projectile.alive = False
                projectile.death_reason = 'CANCEL'
                continue
            if projectile.kind != 'hazard':
                start=(old[0]+old[2]/2,old[1]+old[3]/2)
                end=(projectile.x+projectile.w/2,projectile.y+projectile.h/2)

                half_w, half_h = projectile.w/2, projectile.h/2
                sweep_left = min(start[0], end[0])-half_w
                sweep_right = max(start[0], end[0])+half_w
                sweep_top = min(start[1], end[1])-half_h
                sweep_bottom = max(start[1], end[1])+half_h
                hits = []
                for objid, rect, terrain in surfaces:
                    if terrain == 2 and (projectile.kind != 'napalm' or projectile.vy <= 0
                            or old[1]+old[3] > rect[1]+1):
                        continue
                    if (rect[0] > sweep_right or rect[0]+rect[2] < sweep_left
                            or rect[1] > sweep_bottom or rect[1]+rect[3] < sweep_top):
                        continue

                    expanded=(rect[0]-half_w,rect[1]-half_h,
                              rect[2]+projectile.w,rect[3]+projectile.h)
                    impact = segment_hit(start, end, expanded)
                    if impact is None:
                        continue
                    hits.append((impact[0], impact[1], rect, objid, terrain))
                if hits:
                    fraction, normal, rect, surface_id, surface_kind = min(
                        hits, key=lambda hit: hit[0])
                    projectile.x = old[0] + (projectile.x-old[0])*fraction
                    projectile.y = old[1] + (projectile.y-old[1])*fraction
                    if projectile.kind == 'napalm':

                        if normal[1] == -1:
                            projectile.y = rect[1]-projectile.h
                            projectile.grounded = True
                            projectile.vx = projectile.vy = 0.0
                            projectile.kind = 'hazard'

                            projectile.support = (surface_id if surface_kind == 2
                                                  else None)
                            projectile.support_rect = (rect if surface_kind == 2
                                                       else None)
                            projectile.carry_velocity = (0.0, 0.0)
                            events.append({'kind':'napalm-landed','projectile':projectile})
                        else:
                            projectile.vx = 0.0
                    if projectile.kind == 'bouncing':
                        if normal[1] == -1:
                            projectile.y = rect[1]-projectile.h
                            projectile.vy = -self.rules.GRENADE_BOUNCE_YVEL
                            projectile.gravity = self.rules.GRENADE_BOUNCE_GRAVITY
                            projectile.bounces += 1
                            events.append({'kind': 'projectile-pulse', 'projectile': projectile})
                        elif normal[0]:

                            projectile.vx = -projectile.vx
                        else:
                            projectile.vy = abs(projectile.vy)
                    elif projectile.kind not in ('napalm', 'hazard'):
                        projectile.alive=False
                        projectile.death_reason='BLOCKED'
            if not projectile.alive:
                continue
            if fire_fuse and projectile.age >= projectile.lifetime:
                projectile.alive = False
                projectile.death_reason = 'LIFETIME'
                continue
            if (resolve_player and projectile.owner_type == 2
                    and candidates):
                live_keys = {life_identity(p) for p in candidates}
                projectile.hit_cooldowns = {k: max(0., v-dt) for k, v in projectile.hit_cooldowns.items() if k in live_keys}
                hits = []
                for participant in candidates:
                    player_rect = participant.rect()
                    swept = (player_rect[0]-projectile.w/2, player_rect[1]-projectile.h/2,
                             player_rect[2]+projectile.w, player_rect[3]+projectile.h)
                    impact = (segment_hit((old[0]+old[2]/2, old[1]+old[3]/2),
                        (projectile.x+projectile.w/2, projectile.y+projectile.h/2), swept)
                        if projectile.kind != 'hazard' else None)
                    if rects_overlap(projectile.rect(), player_rect) or impact is not None:
                        hits.append((impact[0] if impact is not None else 0., life_identity(participant)))
                for _, key in sorted(hits):
                    if projectile.hit_cooldowns.get(key, 0.) > 0:
                        continue

                    projectile.alive = not (projectile.remove_type &
                                            DESTROY_REASON['DAMAGES'])
                    if not projectile.alive:
                        projectile.death_reason = 'DAMAGES'
                    projectile.hit_cooldown=0.8
                    projectile.hit_cooldowns[key] = .8
                    events.append({
                        'kind': 'player_damage',
                        'value': projectile.damage,
                        'owner': projectile.owner,
                        'player_key': key,
                        'x': int(projectile.vx * 0.02),
                        'y': int(projectile.vy * 0.02),
                        'burst': projectile.burst_vid,
                        'at': (projectile.x, projectile.y),
                    })
                    if not projectile.alive:
                        break
        if len(self.projectiles) > 512:
            self.projectiles = [p for p in self.projectiles if p.alive]

    def _ride_support(self, projectile, dt):
        """Carry settled fire with the moving shelf it landed on.

        The original server never sends a support id for the fire object; the
        landing surface is inferred from the same native commands/MTC anchors
        as ground bodies. A retracted/hidden shelf clears the support so the
        fire falls again and can settle on a lower surface.
        """
        if projectile.support is None:
            return
        item=self.room.objects.get(projectile.support)
        if item is None or terrain_type(item)!=2:
            projectile.support=None
            projectile.support_rect=None
            projectile.carry_velocity=(0.0,0.0)
            if projectile.kind=='hazard':
                projectile.kind='napalm'
                projectile.grounded=False
                projectile.vy=max(0.0,projectile.vy)
            return
        rect=item.rect()
        if projectile.support_rect is not None and dt>0:
            dx=rect[0]-projectile.support_rect[0]
            dy=rect[1]-projectile.support_rect[1]
            projectile.x+=dx
            projectile.y+=dy
            projectile.carry_velocity=(dx/dt,dy/dt)
        projectile.support_rect=rect

    def _outside_room(self, projectile):
        bounds = self.room.bounds()
        margin = self.rules.PROJECTILE_CULL_MARGIN
        return (projectile.x < bounds[0] - margin
                or projectile.y < bounds[1] - margin
                or projectile.x > bounds[0] + bounds[2] + margin
                or projectile.y > bounds[1] + bounds[3] + margin)

    def live(self):
        return [p for p in self.projectiles if p.alive]

    def remove(self, projectile):
        projectile.alive = False

def segment_hit(start, end, rect):
    """First swept contact and its face, rather than an end-of-tick impact."""
    lo, hi, normal = -float('inf'), float('inf'), (0, 0)
    for axis in (0, 1):
        delta = end[axis]-start[axis]
        lower, upper = rect[axis], rect[axis]+rect[axis+2]
        if abs(delta) < 1e-9:

            if start[axis] <= lower or start[axis] >= upper:
                return None
            continue
        a, b = (lower-start[axis])/delta, (upper-start[axis])/delta
        face = -1 if delta > 0 else 1
        if a > b:
            a, b = b, a
        if a >= lo:
            lo = a
            normal = (face,0) if axis == 0 else (0,face)
        hi = min(hi,b)
        if lo > hi:
            return None
    if hi <= 0 or lo > 1:
        return None
    return (max(0.0, lo), normal)
