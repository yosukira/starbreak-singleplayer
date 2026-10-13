'Native actor transport and shared Jungle fauna, parameterized per map.'
import copy
import math
from ...supply import is_hp_death

DESTROY_INTERNAL = 1
DESTROY_HITPOINTS = 2
DESTROY_LIFETIME = 6

class JungleRoomActors:
    def native_retirement_capability(self,objid):
        """Private server actor classification, not a client permission flag.

        Shield/block contact proof belongs to the Room/attack validator. This
        query only reports an issued actor's actual role and scripted clock.
        """
        if type(objid)is not int or objid<=0:return None
        row=self.actors.get(objid)
        if row is None:return None
        role=row['kind'];species=(row.get('ai')or{}).get('species')
        expiry=row.get('expire')
        if role=='npc':
            expiry=(row.get('ai')or{}).get('transform')if species=='chrysalis' else expiry if species=='pod' else None
        elif role not in ('projectile','fx'):expiry=None
        if type(expiry)not in(int,float)or not math.isfinite(expiry)or expiry<0:expiry=None
        return dict(ownerid=self.worldid,objid=objid,role=role,species=species,
            expires_at=expiry,room_time=self.room_time,
            lifetime_due=expiry is not None and self.room_time>=expiry,
            native_shield_retirement=role=='npc' and species=='hopper' and row['vid']=='jungle.hopper'
                and row['props'].get('removetype')==7)

    def native_retirement_allowed(self,objid,reason):
        """Controller backstop, not proof of a shield/wall contact."""
        if not getattr(self.bridge,'shared_room',False):return True
        cap=self.native_retirement_capability(objid)
        if cap is None or type(reason)is not int:return False
        if reason==6:return cap['lifetime_due']
        row=self.actors[objid];mask=row['props'].get('removetype',1)
        return (reason in (3,4,5)and (cap['role']=='projectile' or reason==3 and cap.get('native_shield_retirement')is True)and type(mask)is int
                and bool(mask&{3:2,4:4,5:8}[reason]))

    def _record(self, kind, at, **values):
        self.events.append(dict(kind=kind, at=round(at, 6), **values))
        if len(self.events) > 5000:
            self.events = self.events[-4000:]

    def _offset(self, at):
        return max(0, int(round((at - self._frame_start) * 1000.)))

    def _add_actor(self, objid, props, at, kind='static', hp=None, ai=None, vel=None,
                   accel=None, parent=None):
        row = dict(objid=objid, props=copy.deepcopy(props), kind=kind,
                   start=(props.get('xmp', 0), props.get('ymp', 0)), epoch=at,
                   move=copy.deepcopy(props.get('move') or {}), hp=hp, ai=ai,
                   vid=props.get('vid', ''), npc=kind == 'npc', parent=parent,
                   pos=[float(props.get('xmp', 0)), float(props.get('ymp', 0))],
                   vel=list(vel or (0., 0.)), accel=list(accel or (0., 0.)),
                   sent=[float(props.get('xmp', 0)), float(props.get('ymp', 0))],
                   sent_at=at, send_at=at, expire=None, facing=props.get('facing', 0),
                   tick_s=0.)
        self.actors[objid] = row
        return row

    def _sync_actor(self, row, at, interval=None):
        """Send a native MOVE delta when a simulated actor has drifted."""
        if row.get('native_motion'):
            return
        if at < row['send_at']:
            return
        if abs(row['pos'][0] - row['sent'][0]) < 256 and abs(row['pos'][1] - row['sent'][1]) < 256:
            return
        dt_ms = max(1, int(round((at - row['sent_at']) * 1000.)))
        self.batch.move_to(row['objid'], int(round(row['pos'][0])), int(round(row['pos'][1])),
                           dt=dt_ms, offset=self._offset(at))
        row['sent'] = list(row['pos'])
        row['sent_at'] = at
        row['send_at'] = at + (interval if interval is not None
                               else (0.05 if row['kind'] == 'projectile' else 0.08))

    def _retire(self, objid, reason, at, native=False):
        if native and not self.native_retirement_allowed(objid,reason):return
        row = self.actors.pop(objid, None)
        if row is None:
            return
        self.retired.add(objid)
        if (row['kind'] == 'npc' and (row.get('ai') or {}).get('species') != 'hopper'
                and is_hp_death(reason)):
            self.supplies.killed(row['pos'][0] + row['props'].get('wmp', 0) / 2.,
                                 row['pos'][1] + row['props'].get('hmp', 0) / 2.)
        shared = getattr(self.bridge, 'shared_room', False)
        if not native or shared:
            name = {DESTROY_INTERNAL: 'INTERNAL', DESTROY_HITPOINTS: 'HITPOINTS',
                    DESTROY_LIFETIME: 'LIFETIME'}.get(reason, 'INTERNAL')

            self.batch.destroy(objid, ownerid=self.worldid, reason=reason if shared else name, offset=self._offset(at))
        self._record('retire', at, objid=objid, vid=row['vid'], reason=reason)

    def _next_objid(self):
        self._next_id += 1
        return self._next_id

    def _create(self, vid, props, at, kind='npc', hp=None, ai=None, vel=None, accel=None,
                parent=None):
        objid = self._next_objid()
        full = dict(props or {})
        full['vid'] = vid
        full.setdefault('ownertype', 2)
        self._add_actor(objid, full, at, kind=kind, hp=hp, ai=ai, vel=vel, accel=accel,
                        parent=parent)
        self.batch.create(objid, self.pack.wire_props(full), creatorid=parent or 0,
                          offset=self._offset(at))
        self.counts['spawns'] += 1
        return objid

    def _activate_hive(self, objid, hp):
        row = self.actors.get(objid)
        if row is None:
            return
        row['kind'] = 'npc'
        row['npc'] = True
        row['hp'] = hp
        row['ai'] = dict(species='hive', next_toss=self.room_time + self.rng.uniform(1.0, 3.0))

    def _activate_skitter(self, objid):
        row = self.actors.get(objid)
        if row is None:
            return
        row['kind'] = 'npc'
        row['npc'] = True
        row['hp'] = int(row['props'].get('hitpoints') or self.fauna.SKITTER_HP)
        w = row['props'].get('wmp', 15360)
        h = row['props'].get('hmp', 12288)
        ai = dict(species='skitter', dir=1, active=False)
        platform = self._platform_of(row['pos'][0] + w / 2, row['pos'][1] + h / 2)
        if platform:
            ai['platform'] = platform
            corners = self._corners(platform)

            row['pos'] = [max(corners[1][0], min(corners[0][0], row['pos'][0])),
                          float(corners[0][1])]
            ai['corner'] = 1
        row['ai'] = ai

    def _corners(self, platform):
        """The four sprite positions of one platform loop (measured offsets)."""
        px, py, pw, ph = platform
        w = 15360
        left = px - self.fauna.SKITTER_EDGE_X_MP
        right = px + pw - w + self.fauna.SKITTER_EDGE_X_MP
        top = py - 12288
        bottom = py + self.fauna.SKITTER_BOTTOM_MP
        return [(right, top), (left, top), (left, bottom), (right, bottom)]

    def spawn_props(self, vid):
        'runtime create payload for a spawned species (params table first).'
        props = dict(self.fauna.SPAWN_PROPS.get(vid) or {})
        recorded = self.pack.species_props(vid) or {}
        for key, value in recorded.items():
            props.setdefault(key, value)
        props['ownertype'] = 2
        return props

    def _spawn_pod(self, at, x, y, parent=None):
        props = self.spawn_props('jungle.skitterp')
        props = dict(props, xmp=int(x), ymp=int(y), lifetime=self.fauna.SKITTER_POD_LIFETIME_S * 1000)
        objid = self._create('jungle.skitterp', props, at, kind='npc', hp=self.fauna.SKITTER_POD_HP,
                             ai=dict(species='pod', expire=at + self.fauna.SKITTER_POD_LIFETIME_S),
                             parent=parent)

        self.batch.animprops(objid, 1, self.fauna.POD_CREATE_ANIM_MS, offset=self._offset(at))
        row = self.actors[objid]
        row['expire'] = at + self.fauna.SKITTER_POD_LIFETIME_S
        self.counts['pods'] += 1
        return objid

    def _spawn_hopper(self, at, x, y, hp=None):
        hp = self.fauna.HOPPER_HP if hp is None else hp
        alive = sum(1 for r in self.actors.values()
                    if r['ai'] and r['ai'].get('species') == 'hopper')
        if alive >= self.fauna.HOPPER_CAP:
            self.counts['cap_skips'] += 1
            return None
        props = self.spawn_props('jungle.hopper')
        props = dict(props, xmp=int(x), ymp=int(y), hitpoints=hp, damage=0)
        objid = self._create('jungle.hopper', props, at, kind='npc', hp=hp,
                             ai=dict(species='hopper', vel=[0., self.fauna.HOPPER_BIRTH_VY_MP_S],
                                     birth_delay=self.rng.choice(self.fauna.HOPPER_BIRTH_GRAVITY_DELAY_S),
                                     birth_arc=True, grounded=False,
                                     next_decision=at,
                                     dir=self.rng.choice((-1, 1))))
        self.batch.modify(objid, 'POSE', intval=3, offset=self._offset(at))
        self.batch.sound(objid, 'jungle.hopperspawn', offset=self._offset(at))
        self.counts['hoppers'] += 1
        return objid

    def _spawn_hive(self, at, x, y):
        alive = sum(1 for r in self.actors.values()
                    if r['ai'] and r['ai'].get('species') == 'hive')
        if alive >= self.fauna.HIVE_CAP:
            self.counts['cap_skips'] += 1
            return None
        props = self.spawn_props('jungle.hopperhive')
        props = dict(props, xmp=int(x), ymp=int(y), hitpoints=self.fauna.HIVE_SMALL_HP)
        objid = self._create('jungle.hopperhive', props, at, kind='npc', hp=self.fauna.HIVE_SMALL_HP)
        self._activate_hive(objid, self.fauna.HIVE_SMALL_HP)
        self.counts['hives'] += 1
        return objid

    def _spawn_shot(self, key, creator, at, position, vel, accel=(0., 0.), facing=None):

        if self.fauna.PROJECTILE_CAP is not None:
            live = sum(1 for r in self.actors.values() if r['kind'] == 'projectile')
            if live >= self.fauna.PROJECTILE_CAP:
                self.counts['cap_skips'] += 1
                return None
        spec = self.fauna.SHOTS[key]
        props = dict(spec['props'], xmp=int(position[0]), ymp=int(position[1]))
        heading = math.degrees(math.atan2(vel[1], vel[0]))
        props['facing'] = int(round(heading if facing is None else facing))
        objid = self._create(spec['vid'], props, at, kind='projectile', hp=None, vel=vel,
                             accel=accel, parent=creator)
        row = self.actors[objid]
        row['shot'] = key
        row['expire'] = at + spec['lifetime_s']
        row['creator'] = creator
        row['split_facing'] = props['facing']

        if not spec.get('children'):
            self.batch.modify(objid, 'LIFETIME', intval=int(spec['lifetime_s']*1000),
                              offset=self._offset(at))
        sound = 'jungle.shotgen' if key.startswith('fireshot') else 'jungle.swirlbshoot'
        if sound:
            self.batch.sound(objid, sound, offset=self._offset(at))
        self.counts['shots'] += 1
        return objid

    def combat_players(self):
        from ...participants import from_bridge
        return from_bridge(self.bridge, self.player)

    def _target_player(self, origin):
        from ...participants import nearest
        return nearest(self.combat_players(), origin)

    def _player_center(self, origin=None):
        player = self._target_player(origin) if origin is not None else self.player
        if player is None or not player.alive:
            return (0., 0.)
        return (player.xmp + player.wmp / 2, player.ymp + player.hmp / 2)

    def _step_npcs(self, start, end):
        dt = max(0., end - start)
        for objid, row in list(self.actors.items()):
            if row['kind'] != 'npc' or objid in self.retired:
                continue
            try:
                self._npc_tick(row, dt, end)
            except Exception as error:
                self._record('npc_error', end, objid=objid, error=str(error))
                continue
            if objid in self.actors:
                self._sync_actor(row, end)

    def _skitter_tick(self, row, dt, at):
        """Permanent activation, perimeter movement, independent pod clock."""
        ai = row['ai']
        player = self._target_player(row['pos'])
        center = player.center() if player is not None else None
        if not ai.get('active'):
            if center is None or math.hypot(center[0]-row['pos'][0], center[1]-row['pos'][1]) > self.fauna.SKITTER_RANGE_MP:
                return
            ai['active'] = True
            ai['next_pod'] = at + self.fauna.SKITTER_POD_INTERVAL_S
        w = row['props'].get('wmp', 15360)
        h = row['props'].get('hmp', 12288)
        platform = ai.get('platform')
        if platform is None or not self._platform_index(platform):
            platform = ai['platform'] = self._platform_of(row['pos'][0] + w / 2,
                                                          row['pos'][1] + h / 2)
            if platform is None:
                return
            nearest = self._nearest_corner(platform, row['pos'])
            row['pos'] = [float(self._corners(platform)[nearest][0]),
                          float(self._corners(platform)[nearest][1])]
            ai['corner'] = (nearest + 1) % 4
        corners = self._corners(platform)
        remaining = dt
        while remaining > 1e-9:
            corner = ai.get('corner', 1)
            tx, ty = corners[corner]
            x, y = row['pos']
            dx, dy = tx-x, ty-y
            distance = math.hypot(dx, dy)
            if corner in (0, 2):
                speed = abs(corners[2][1]-corners[1][1])/self.fauna.SKITTER_SIDE_S
            elif corner == 3:
                span = corners[0][0]-corners[1][0]
                speed = span/max(.05, span/self.fauna.SKITTER_SPEED_MP_S-self.fauna.SKITTER_BOTTOM_LEAD_S)
            else:
                speed = self.fauna.SKITTER_SPEED_MP_S
            travel_s = distance/speed if speed > 0 else 0
            if travel_s > remaining:
                row['pos'] = [x+dx/distance*speed*remaining, y+dy/distance*speed*remaining]
                break
            row['pos'] = [float(tx), float(ty)]
            remaining -= travel_s
            corner = ai['corner'] = (corner+1)%4
            vid = 'jungle.skitterdown' if corner in (2, 3) else 'jungle.skitterup'
            if vid != row['vid']:
                self.batch.modify(row['objid'], 'VID', stringval=vid,
                                  offset=self._offset(at))
                row['vid'] = vid
                self.batch.animprops(row['objid'], 1, 1320, offset=self._offset(at))
        if at >= ai['next_pod']:
            ai['next_pod'] += self.fauna.SKITTER_POD_INTERVAL_S

            self._spawn_pod(at, row['pos'][0]+2560, row['pos'][1]+1024, parent=row['objid'])
            self._record('skitter_pod', at, objid=row['objid'], position=list(row['pos']))

    def _platform_index(self, platform):
        try:
            return tuple(platform) in self.platforms
        except TypeError:
            return False

    def _platform_of(self, x, y, pad=80000):
        best = None
        for plat in self.platforms:
            if plat in getattr(self, 'protected_platforms', ()):
                continue
            px, py, pw, ph = plat
            if px - pad <= x <= px + pw + pad and py - 200000 <= y <= py + 200000:
                score = abs(py - y) + abs(px + pw / 2 - x)
                if best is None or score < best[0]:
                    best = (score, plat)
        return best[1] if best else None

    def _nearest_corner(self, platform, pos):
        corners = self._corners(platform)
        return min(range(4), key=lambda i: math.hypot(corners[i][0] - pos[0],
                                                      corners[i][1] - pos[1]))

    def _pod_tick(self, row, at):
        if row['expire'] is None or at < row['expire']:
            return
        self._pod_fire(row, at)

    def _pod_fire(self, row, at):
        if row.get('fired'):
            return
        row['fired'] = True
        shots = self.fauna.SKITTER_POD_SHOTS[0]
        target = self._player_center(row['pos'])
        base = math.atan2(target[1] - row['pos'][1], target[0] - row['pos'][0])
        for _ in range(shots):
            angle = base + math.radians(self.rng.uniform(-self.fauna.SWIRL_AIM_JITTER_DEG,
                                                         self.fauna.SWIRL_AIM_JITTER_DEG))
            speed = self.fauna.SWIRL_SPEED_MP_S
            self._spawn_shot('swirlbb', row['objid'], at, row['pos'],
                             (math.cos(angle) * speed, math.sin(angle) * speed),
                             facing=math.degrees(angle))
        self.counts['lifetime'] += 1
        self._retire(row['objid'], DESTROY_LIFETIME, at)

    def _hive_tick(self, row, at):

        if not self.fauna.PREBOSS_HOPPER:
            return
        ai = row['ai']
        if ai.get('release_at') is not None and at >= ai['release_at']:
            ai['release_at'] = None
            x, y = row['pos']
            self._spawn_hopper(at, x+6144, y,
                               hp=self.fauna.HOPPER_BIG_HP if row['hp'] > self.fauna.HIVE_SMALL_HP
                               else self.fauna.HOPPER_HP)
        player = self._target_player(row['pos'])
        center = player.center() if player is not None else (0., 0.)
        if player is None or math.hypot(
                center[0]-row['pos'][0], center[1]-row['pos'][1]) > self.fauna.HIVE_RANGE_MP:
            return
        if at >= ai.get('next_toss', 0):
            cycle = self.fauna.HIVE_BIG_CYCLE_S if row['hp'] > self.fauna.HIVE_SMALL_HP else self.fauna.HIVE_SMALL_CYCLE_S
            ai['next_toss'] = at+cycle
            ai['release_at'] = at+self.fauna.HIVE_TOSS_ANIM_MS/1000.
            self.batch.animprops(row['objid'], 2, self.fauna.HIVE_TOSS_ANIM_MS,
                                 offset=self._offset(at))

    def _hopper_tick(self, row, dt, at):
        """Vertical birth, first armed hop, small bounces and occasional chase."""
        ai = row['ai']
        if ai.get('grounded'):
            if at < ai['takeoff_at']:
                return
            self._hopper_takeoff(row, at)
        vx, vy = ai['vel']
        x, y = row['pos']
        w, h = row['props'].get('wmp', 12288), row['props'].get('hmp', 12288)
        gravity = self.fauna.HOPPER_GRAVITY_MP_S2
        cap = self.fauna.HOPPER_FALL_CAP_MP_S
        pause_s = min(dt, ai.get('birth_delay', 0.))
        ai['birth_delay'] = max(0., ai.get('birth_delay', 0.)-pause_s)
        active_s = dt-pause_s
        accel_s = min(active_s, max(0., (cap-vy)/gravity))
        nx = x+vx*dt
        ny = y+vy*(pause_s+accel_s)+.5*gravity*accel_s*accel_s+cap*(active_s-accel_s)
        vy = min(cap, vy+gravity*active_s)

        ground = None
        if vy > 0:
            for px, py, pw, _ph in self.platforms:
                if px <= nx+w/2 <= px+pw and y+h <= py+1 and ny+h >= py:
                    ground = py if ground is None else min(ground, py)
        if ny+h >= self.pit_y:
            row['pos'] = [nx, self.pit_y-h]
            self._sync_actor(row, at)
            self._retire(row['objid'], DESTROY_INTERNAL, at)
            return
        if ground is not None:
            ny, vy = ground-h, 0.
            ai['grounded'] = True
            ai['takeoff_at'] = at+self.fauna.HOPPER_GROUND_S
            first = ai.get('birth_arc', False)
            self.batch.modify(row['objid'], 'POSE', intval=4 if first else 5,
                              offset=self._offset(at))
            if not first and self.rng.random() < self.fauna.HOPPER_HIVE_CHANCE:
                if self._maybe_found_hive(nx, ny, at):
                    row['pos'] = [nx, ny]
                    self._retire(row['objid'], DESTROY_INTERNAL, at)
                    return
        row['pos'] = [nx, ny]
        ai['vel'] = [vx, vy]

    def _hopper_takeoff(self, row, at):
        ai = row['ai']
        first = ai.pop('birth_arc', False)
        if first:
            ai['vel'][0] = ai['dir']*self.fauna.HOPPER_SPEED_MP_S
        vy = self.fauna.HOPPER_FIRST_VY_MP_S if first else self.fauna.HOPPER_BOUNCE_VY_MP_S
        large_chance=getattr(self.fauna,'HOPPER_LARGE_HOP_CHANCE',None)
        if not first and large_chance is not None and self.rng.random()<large_chance:

            vy=self.rng.uniform(*self.fauna.HOPPER_CHASE_VY_MP_S)
        if at >= ai.get('next_decision', 0):
            ai['next_decision'] = at+self.rng.uniform(*self.fauna.HOPPER_DECISION_S)
            center = (row['pos'][0]+row['props']['wmp']/2, row['pos'][1]+row['props']['hmp']/2)
            player = self._target_player(center)
            target = player.center() if player is not None else (0., 0.)
            if player is not None and math.hypot(
                    target[0]-center[0], target[1]-center[1]) <= self.fauna.HOPPER_RANGE_MP:
                ai['dir'] = 1 if target[0] >= center[0] else -1
                lo,hi = self.fauna.HOPPER_CHASE_SPEED_MP_S
                ai['vel'][0] = ai['dir']*self.rng.randint(lo//1000, hi//1000)*1000
                if large_chance is None and not first:
                    vy = self.rng.uniform(*self.fauna.HOPPER_CHASE_VY_MP_S)
            self.batch.sound(row['objid'], 'jungle.hopperjump', offset=self._offset(at))
        ai['grounded'] = False
        ai['vel'][1] = vy
        self.batch.modify(row['objid'], 'POSE', intval=3, offset=self._offset(at))
        if not ai.get('armed'):
            ai['armed'] = True
            row['props']['damage'] = self.fauna.HOPPER_DAMAGE
            self.batch.modify(row['objid'], 'DAMAGE', intval=self.fauna.HOPPER_DAMAGE,
                              offset=self._offset(at))
        facing = 0 if ai['vel'][0] >= 0 else 180
        if facing != row['facing']:
            row['facing'] = facing
            self.batch.modify(row['objid'], 'FACING', intval=facing, offset=self._offset(at))
        self._record('hopper_takeoff', at, objid=row['objid'], first=first,
                     velocity=list(ai['vel']))

    def _maybe_found_hive(self, x, y, at):
        """A hop that lands on a bare platform founds a hive under it."""
        w = 12288
        feet = y + w
        for px, py, pw, ph in self.platforms:
            if (px, py, pw, ph) in getattr(self, 'protected_platforms', ()):
                continue
            if not (px <= x + w / 2 <= px + pw):
                continue
            if abs(feet - py) > 14000:
                continue
            hive_w = 24576
            hx = min(max(x+w/2-hive_w/2, px), px+pw-hive_w)
            for hive in list(self.actors.values()):
                if hive['ai'] and hive['ai'].get('species') == 'hive':
                    if (hx < hive['pos'][0]+hive_w and hx+hive_w > hive['pos'][0]
                            and abs(hive['pos'][1]-(py+ph)) < 512):
                        return False
            return self._spawn_hive(at, hx, py+ph) is not None
        return False

    def _surface_at(self, x, y):
        best = None
        for px, py, pw, ph in self.platforms:
            if px <= x <= px + pw and py >= y - 4096 and (best is None or py < best):
                best = py
        return best

    def _blocked(self, x, y, props, shot):
        spec = self.fauna.SHOTS.get(shot) or {}
        if not spec.get('terrain'):
            return self._outside(x, y, props)
        w, h = props.get('wmp', 0), props.get('hmp', 0)
        for sx, sy, sw, sh in self.solids:
            if x < sx + sw and x + w > sx and y < sy + sh and y + h > sy:
                return True
        return self._outside(x, y, props)

    def _outside(self, x, y, props):
        x0, y0, x1, y1 = self.bounds
        return (x < x0 - 400000 or y < y0 - 400000 or x + props.get('wmp', 0) > x1 + 400000
                or y + props.get('hmp', 0) > y1 + 400000)

    def _player_over_platform(self, pair):
        """True while a live player is in the air column above the slab."""
        platform = pair.get('platform')
        if not platform:
            return False
        px, py, pw, ph = platform
        for player in self.combat_players():
            cx = player.xmp + player.wmp / 2.
            feet = player.ymp + player.hmp
            if (px-self.fauna.PINCER_PASS_X_PAD_MP <= cx <= px+pw+self.fauna.PINCER_PASS_X_PAD_MP
                    and py-self.fauna.PINCER_PASS_ABOVE_MP <= feet <= py+self.fauna.PINCER_PASS_TOUCH_MP):
                return True
        return False

    def _step_pincers(self, start, end):
        if end < self._pincer_at:
            return
        elapsed = max(0., end - self._pincer_last)
        self._pincer_last = end
        self._pincer_at = end + 0.05
        for pair in self.pincer_pairs:
            left, right = int(pair['left']), int(pair['right'])
            state = self.pincer_timers.get(left)
            if state is None:
                continue
            reset = getattr(self.fauna, 'PINCER_RESET_ANIM_ID', None)
            if reset is not None and state.get('restore_at') is not None and end >= state['restore_at']:
                for oid in (left, right):
                    self.batch.animprops(oid, reset, self.fauna.PINCER_RESET_ANIM_MS,
                                         offset=self._offset(end))
                state['restore_at'] = None
                self._record('pincer_rest', end, left=left, right=right)
            state['remain'] -= elapsed
            fire, reason = False, 'auto'
            if state['pending'] is not None:
                state['pending'] -= elapsed
                if state['pending'] <= 0:
                    fire, reason = True, 'pass'
            if not fire and state['remain'] <= 0:
                fire, reason = True, 'auto'
            if not fire and state['pending'] is None and self._player_over_platform(pair):

                if state['remain'] <= self.fauna.PINCER_CD_S - self.fauna.PINCER_PASS_MIN_S:
                    state['pending'] = self.fauna.PINCER_PASS_DELAY_S
                    state['armed'] = True
            if not fire:
                continue
            state['remain'] = self.fauna.PINCER_CD_S
            state['pending'] = None
            state['armed'] = False
            if reset is not None:
                state['restore_at'] = end+self.fauna.PINCER_ANIM_MS/1000
            offset = self._offset(end)
            for oid in (left, right):
                self.batch.sound(oid, self.fauna.PINCER_SOUND, offset=offset)
                self.batch.animprops(oid, self.fauna.PINCER_ANIM_ID, self.fauna.PINCER_ANIM_MS,
                                     offset=offset)
            self.counts['pincer_slashes'] += 1
            self._record('pincer', end, left=left, right=right, reason=reason)

    def _hopper_burst(self, row, at):
        props = self.spawn_props(self.fauna.HOPPER_EXPLODE)
        props = dict(props, xmp=int(row['pos'][0]), ymp=int(row['pos'][1]),
                     damage=self.fauna.HOPPER_EXPLODE_DAMAGE)
        oid = self._create(self.fauna.HOPPER_EXPLODE, props, at, kind='fx')
        self.actors[oid]['expire'] = at + 0.333
        self.batch.sound(oid, 'jungle.hopperxgen', offset=self._offset(at))
