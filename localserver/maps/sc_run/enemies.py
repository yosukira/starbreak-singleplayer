"""Live SC creatures: observed actions, user lifecycle rules, configurable fits.

Distances, search periods, navigation accelerations and random choices below
are local FIT values. They are not reconstructed retail AI constants. Actions
respond to the current room/player state; no captured timeline is replayed.
"""
import copy
import math
from collections import Counter

from .abilities import ScProjectiles, center, move, players, prop, stop_motion
from .mechanic_helpers import place
from .physics import clear, edge_ahead, flight, geometry, ground, rect, sinusoid, sweep


GHOSTS = ('sc.scout', 'sc.bomber', 'sc.sniper', 'sc.assassin', 'sc.titan')
GROUND = {'sc.skeleton', 'sc.golem', 'sc.egolem', 'sc.tank', 'sc.titan'}
BODIES = GROUND | set(GHOSTS) | {'sc.flytank', 'sc.monk', 'sc.hunter', 'sc.eassassin'}
SHOTS = {'sc.shard', 'sc.sliver', 'sc.sliverx', 'sc.burst', 'sc.golemb', 'sc.golemw', 'sc.wraith'}
VITALS = {'sc.skeleton': 1000, 'sc.golem': 5000, 'sc.egolem': 10000, 'sc.tank': 3000,
          'sc.flytank': 2000, 'sc.monk': 3000, 'sc.titan': 2000,
          **{v: 750 for v in GHOSTS if v != 'sc.titan'}}

# FIT: waking is deliberately independent from acquisition and scan cadence.
# Species overrides may be supplied in recipe.metadata.sc_enemy_fit.species.
DEFAULT_SPECIES = {
    # OBS (ranges-notes.md, second recording): six clean single-player first
    # motions happen at 1151-1546px centre distance, so 1600 is a FIT upper
    # bound. Weapon ranges are bounded by the measured first-shot geometry:
    # tank ~843px, flytank ~302-367px (both with no prior damage).
    'sc.skeleton': dict(wake_px=1600., acquire_px=960., release_px=1500., scan_s=.45, speed=55000., attack_px=170.),
    'sc.golem': dict(wake_px=1600., acquire_px=1200., release_px=1700., scan_s=.55, speed=22000., attack_px=700.),
    'sc.tank': dict(wake_px=1600., acquire_px=1100., release_px=1700., scan_s=.5, speed=24000., attack_px=860.),
    'sc.flytank': dict(wake_px=1600., acquire_px=700., release_px=1300., scan_s=.6, speed=32000., attack_px=380.),
    'sc.hunter': dict(wake_px=1600., acquire_px=1600., release_px=2400., scan_s=.4, speed=46000., attack_px=150.),
    'sc.monk': dict(wake_px=1600., acquire_px=1000., release_px=1500., scan_s=.65, speed=22000., attack_px=900.),
}
DEFAULT_FIT = dict(respawn_s=120., gravity=200000., skeleton_jump_v=181000.,
    titan_jump_v=125000., golem_jump_v=90000., golem_gravity=200000.,
    # FIT: one same-body capture jumps at about 528px centre distance and
    # throws at 300px and 587px, so a 420px jump range cannot reproduce it.
    golem_close_px=560., golem_jump_weight=.5, golem_restitution=.8,  # weight is FIT
    hunter_manifest_px=160., urn_wake_px=1700., urn_step_s=.25,
    urn_emit_s=.105, manifest_s=.83, titan_manifest_s=1.62,
    skeleton_thrust_cooldown=.605, skeleton_cleave_cooldown=.8,
    skeleton_variant_policy='object_id_fit', patrol_px=240., patrol_leg_s=1.1, steer_leg_s=(.45, 1.0),
    assassin_visible_s=2.4, assassin_initial_wait_s=.1,
    assassin_ghost_wait_s=.325, assassin_pause_s=.78, assassin_move_s=.35,
    assassin_segments=(1, 2, 3, 4),
    # OBS: of 72 ghost->burst segments, 62 moved once, 8 twice, 1 three times
    # and 1 four times; the four lengths were never equally likely.
    assassin_segment_weights=(62., 8., 1., 1.),
    # OBS: titan 15/149 manifests; the remaining four forms share the rest.
    ghost_form_weights={'sc.titan': 15., 'sc.scout': 33.5, 'sc.bomber': 33.5,
                        'sc.sniper': 33.5, 'sc.assassin': 33.5}, burst_speed=36000., burst_jitter_rad=.20,
    monk_interval_s=16., monk_refill_s=1.92, monk_orb_interval_s=.25,
    monk_mode_choices=('sequential', 'radial', 'focused'),
    # OBS: a captured warning line is 13 segments of 64px (~832px).
    laser_segment_px=32., laser_range_px=1600., laser_max_segments=50,
    fly_acceleration=50000., scout_dive_acceleration=160000.,
    scout_dive_s=.8, scout_cooldown_s=(2.2, 3.4), skeleton_melee_vertical_px=110.,
    flytank_hover_span_px=180., golem_stand_px=520., golem_stand_scale=.25, golem_wave_gravity=180000., tank_patrol_px=112.,
    wraith_interval_s=(.5, .85), wraith_initial_delay_s=.1,
    # OBS: 3796 same-owner dynamic wraiths leave at 4994-7474ms even though the
    # authored lifetime field is 30000ms; the native cleanup trigger is unknown,
    # so the server instance is bounded by the observed visible life.
    wraith_life_s=7.5)


class ScEnemies:
    def __init__(self, host):
        self.host = host
        metadata = host.recipe.get('metadata', {})
        overrides = copy.deepcopy(metadata.get('sc_enemy_fit') or {})
        species = overrides.pop('species', {})
        self.fit = dict(DEFAULT_FIT, **overrides)
        self.policies = copy.deepcopy(DEFAULT_SPECIES)
        for vid, values in species.items():
            self.policies.setdefault(vid, {}).update(values)
        self.units, self.urns = {}, {}
        self.pending, self.respawns = [], []
        self.owned, self.retired, self.effects = set(), set(), set()
        self.seen, self.recent = set(), []
        self._damage_ignored = set()
        self.now = host.room_time
        self.projectiles = ScProjectiles(self)
        self.counts = Counter()
        self.terrain = []
        self.urn_counts = metadata.get('sc_surn_counts') or {}
        self.guardian_id = metadata.get('chest_guardian')
        self.guardian_rewarded = False
        # USER: fixed wall hazards, unrelated to creatures or player targeting.
        # Origins/angles/offsets are native birth samples for this geometry.
        # Independent interval and sample selection are explicitly local FIT.
        self.emitters = []
        for profile in metadata.get('sc_wraith_emitters', []):
            profile = copy.deepcopy(profile)
            interval = profile.get('interval_s', self.fit['wraith_interval_s'])
            if not profile.get('births') or len(interval) != 2 or not 0 < interval[0] <= interval[1]:
                raise ValueError('SC Wraith emitter needs native birth samples and a positive FIT interval')
            self.emitters.append(dict(profile=profile, interval=interval,
                next=self.now+self.fit['wraith_initial_delay_s']))
        self.manifest_choices = tuple(metadata.get('sc_ghost_forms') or GHOSTS)
        if not self.manifest_choices or any(v not in GHOSTS for v in self.manifest_choices):
            raise ValueError('SC ghost forms must be observed scout/bomber/sniper/assassin/titan VIDs')

    def policy(self, vid):
        if vid == 'sc.egolem':
            vid = 'sc.golem'
        return dict(dict(wake_px=1500., acquire_px=900., release_px=1500., scan_s=.5,
                         speed=30000., attack_px=800.), **self.policies.get(vid, {}))

    def own(self, oid):
        self.owned.add(oid)
        self.host.actors[oid]['_sc_enemy'] = True

    def prepare(self):
        # Surn identities must exist before explicit creator relationships.
        ids = list(self.host.actors)
        for oid in ids:
            if self.host.actors[oid]['vid'] == 'sc.surn':
                self.add_actor(oid)
        for oid in ids:
            self.add_actor(oid)
        for oid in list(self.units):
            if self.units[oid]['vid'] == 'sc.monk':
                self._find_orbs(oid)
        self.host.record('sc_enemy_policy', evidence='OBS native actions/counts/speeds; USER independent wake/search/scan, through-wall acquisition, 120s respawn; FIT decision/navigation/choice parameters',
                         fit=copy.deepcopy(self.fit), species=copy.deepcopy(self.policies))

    def add_actor(self, oid):
        row = self.host.actors.get(oid)
        if row is None or row.get('dead') or oid in self.retired:
            return False
        if oid in self.owned or oid in self.projectiles.shots:
            return True
        vid, p = row['vid'], row['props']
        if vid in SHOTS:
            self.projectiles.adopt(oid, gravity=150000. if vid == 'sc.shard' else 200000. if vid == 'sc.golemb' else 0., bounce=vid == 'sc.golemb')
            return True
        if vid in ('sc.aim', 'sc.aimend'):
            self.own(oid)
            self.effects.add(oid)
            return True
        if vid == 'sc.monkb':
            owner = (p.get('move') or {}).get('relativeto', {}).get('objid', row.get('creator', 0))
            elliptical = (p.get('move') or {}).get('elliptical')
            phase = 2*math.pi*elliptical.get('pos', 0)/max(1, elliptical.get('period', 2000))-self.now*math.pi if elliptical else None
            self.projectiles.adopt(oid, owner=owner, orbit=phase)
            return True
        if vid == 'sc.surn':
            self.own(oid)
            count = self.urn_counts.get(str(oid), self.urn_counts.get(oid, 3))
            if count not in (3, 7):
                raise ValueError('SC surn count must be user-observed 3 or 7')
            self.urns[oid] = dict(count=count, slots={}, due={}, next=self.now, active=False,
                                 native=copy.deepcopy(p.get('move') or {}), origin=tuple(row['pos']))
            stop_motion(self.host, oid)
            return True
        if vid not in BODIES:
            return False
        self.own(oid)
        direction = -1 if p.get('facing', 0) == 180 else 1
        family = 'sc.assassin' if vid == 'sc.eassassin' else vid
        u = dict(vid=family, active=False, phase='dormant', home=tuple(row['pos']),
                 direction=direction, target=None, target_point=None,
                 scan=self.now, next=self.now+.1, busy=0., lock=0.,
                 patrol_until=self.now+self.fit['patrol_leg_s'],
                 vx=0., vy=0., grounded=False, support=None,
                 initial=copy.deepcopy(p), saved_hp=row.get('hp'), orbs=[], guardian=oid == self.guardian_id)
        self.units[oid] = u
        if family == 'sc.skeleton':
            # Both attack families are OBS. The object-ID association is FIT,
            # configurable independently of the two proven durations.
            variant = p.get('_sc_skeleton_variant')
            configured = self.host.recipe.get('metadata', {}).get('sc_skeleton_variants', {})
            variant = configured.get(str(oid), configured.get(oid, variant))
            if variant not in ('cleave', 'thrust'):
                variant = 'cleave' if self.fit['skeleton_variant_policy'] == 'object_id_fit' and oid % 4 == 0 else 'thrust'
            u['variant'] = variant
        if family == 'sc.hunter':
            u['ready'] = self.now+1.32 if row.get('_sc_new_hunter') else self.now
        if family in GHOSTS and row.get('hp') is None and vid != 'sc.eassassin':
            self._manifest_begin(oid, u)
        elif vid == 'sc.eassassin':
            u.update(active=True, phase='ghost', saved_hp=VITALS['sc.assassin'], ghost_steps=1,
                     ghost_at=self.now+self.fit['assassin_ghost_wait_s'])
        self._attach_explicit_lineage(oid, row.get('creator', 0))
        # USER: no creature is born inside a wall; nudge it to the nearest
        # opening before it ever acts.
        w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
        if family != 'sc.hunter' and not clear(self.host, (row['pos'][0], row['pos'][1], w, h), (oid,)):
            for radius in (16, 32, 48, 64, 96, 128, 192):
                spot = None
                for step in range(0, 360, 45):
                    px = row['pos'][0]+math.cos(math.radians(step))*radius*256
                    py = row['pos'][1]+math.sin(math.radians(step))*radius*256
                    if clear(self.host, (px, py, w, h), (oid,)):
                        spot = (px, py)
                        break
                if spot:
                    place(self.host, oid, spot)
                    u['home'] = spot
                    break
        stop_motion(self.host, oid)
        return True

    def _attach_explicit_lineage(self, oid, creator):
        if creator not in self.urns:
            return
        urn = self.urns[creator]
        reserved = self.host.actors[oid].get('_sc_reserved_slot')
        slot = reserved if reserved is not None else next((s for s in range(urn['count']) if s not in urn['slots']), None)
        if slot is not None:
            urn['slots'][slot] = oid
            urn['active'] = True  # An explicit existing child proves prior activation.
            self.units[oid]['lineage'] = (creator, slot)

    def _manifest_begin(self, oid, u):
        titan = u['vid'] == 'sc.titan'
        u.update(active=True, phase='manifest', busy=self.now+(self.fit['titan_manifest_s'] if titan else self.fit['manifest_s']))
        anim = 4 if titan else 3 if u['vid'] == 'sc.assassin' else 1
        self.host.presentation.attack(oid, u['vid'], anim, 1650 if titan else 858, token=self.now)
        self.host.presentation.sound(oid, 'sc.manifest', self.now)
        prop(self.host, oid, 'damage', 0)
        prop(self.host, oid, 'hitboxdamage', 0)
        prop(self.host, oid, 'blocktype', 2)
        prop(self.host, oid, 'hitpoints', None)
        self.host.actors[oid]['hp'] = None

    def _manifest_end(self, oid, u):
        row = self.host.actors[oid]
        hp = u['saved_hp'] if u['saved_hp'] is not None else VITALS[u['vid']]
        row['hp'] = hp
        prop(self.host, oid, 'hitpoints', hp)
        prop(self.host, oid, 'hitboxdamage' if u['vid'] == 'sc.titan' else 'damage', 35)
        prop(self.host, oid, 'ownertype', 2)
        if u['vid'] in ('sc.assassin', 'sc.titan'):
            prop(self.host, oid, 'blocktype', 1)
        u.update(phase='patrol', next=self.now+self.fit['assassin_initial_wait_s'], busy=0.)

    def _scan(self, oid, u, live):
        row = self.host.actors[oid]
        policy = self.policy(u['vid'])
        c = center(row)
        if not u['active']:
            if not any(math.dist(center(p), c) <= policy['wake_px']*256 for p in live.values()):
                return None
            u.update(active=True, phase='patrol')
            self.counts['activations'] += 1
        if u['target'] is not None and u['target'] not in live:
            # A player that left/died is no longer a room target. Forget its
            # point even during an action lock; acquiring a replacement still
            # waits for the independent search deadline and facing lock.
            u.update(target=None, target_point=None)
            if u['phase'] == 'chase':
                u['phase'] = 'patrol'
        # All scans and target changes obey the independent search timer.
        if self.now < u['scan'] or self.now < u['lock']:
            return live.get(u['target'])
        u['scan'] = self.now+max(.01, policy['scan_s'])
        old = live.get(u['target'])
        if old is not None and (u['vid'] == 'sc.hunter' or math.dist(center(old), c) <= policy['release_px']*256):
            target = old
        else:
            eligible = [(key, p) for key, p in live.items() if math.dist(center(p), c) <= policy['acquire_px']*256]
            selected = min(eligible, key=lambda kv: math.dist(center(kv[1]), c)) if eligible else (None, None)
            u['target'], target = selected
        if target is not None:
            u['target_point'] = center(target)
            dx = u['target_point'][0]-c[0]
            if abs(dx) > 8*256:
                u['direction'] = 1 if dx >= 0 else -1
            if u['phase'] in ('patrol', 'chase'):
                u['phase'] = 'chase'
        else:
            u['target_point'] = None
            if u['phase'] == 'chase':
                u['phase'] = 'patrol'
        return target

    def _attack(self, oid, u, anim, duration, *, window=None, cooldown=None, lock=True):
        row = self.host.actors[oid]
        if lock:
            prop(self.host, oid, 'facing', 0 if u['direction'] > 0 else 180)
        u.update(busy=self.now+(duration/1000 if window is None else window),
                 next=self.now+(duration/1000 if cooldown is None else cooldown))
        if lock:
            u['lock'] = max(u['busy'], self.now+duration/1000)
        self.host.presentation.attack(oid, row['vid'], anim, duration, token=self.now)
        self.counts['attacks'] += 1

    def _queue(self, oid, delay, kind, **values):
        self.pending.append(dict(at=self.now+delay, owner=oid, kind=kind, **values))

    def _origin(self, oid, vid):
        c = center(self.host.actors[oid])
        p = self.host.pack.props(vid)
        return (c[0]-p.get('wmp', 0)/2, c[1]-p.get('hmp', 0)/2)

    def _hunter(self, oid, u, dt, target):
        row = self.host.actors[oid]
        if target is None or self.now < u['ready']:
            return
        # Hunter keeps its chosen player until that player leaves the room.
        point = center(target)
        c = center(row)
        delta = (point[0]-c[0], point[1]-c[1])
        distance = math.hypot(*delta)
        if distance <= self.fit['hunter_manifest_px']*256:
            # OBS: titan formed 15 of 149 manifests (~10%), not one in five.
            vid = self.host.rng.choices(self.manifest_choices,
                                        [self.fit['ghost_form_weights'].get(v, 1.) for v in self.manifest_choices])[0]
            p = self.host.pack.props(vid)
            pos = (c[0]-p.get('wmp', 0)/2, c[1]-p.get('hmp', 0)/2)
            # USER: no form appears inside a wall, including the larger Titan.
            if clear(self.host, (*row['pos'], row['props'].get('wmp', 0), row['props'].get('hmp', 0)), (oid,)) and clear(self.host, (*pos, p.get('wmp', 0), p.get('hmp', 0)), (oid,)):
                lineage = u.get('lineage')
                chosen = u['target']
                self.retire(oid, 'INTERNAL')
                new = self.host._create(vid, pos, 0,
                    dict(hitpoints=None, damage=None, hitboxdamage=None, blocktype=2))
                self.add_actor(new)
                nu = self.units[new]
                if nu['phase'] != 'manifest':
                    nu['saved_hp'] = None
                    self._manifest_begin(new, nu)
                nu.update(target=chosen, target_point=point, lineage=lineage) if lineage else nu.update(target=chosen, target_point=point)
                if lineage and lineage[0] in self.urns:
                    self.urns[lineage[0]]['slots'][lineage[1]] = new
                self.counts['manifests'] += 1
                return
        speed = min(self.policy(u['vid'])['speed'], distance/max(dt, 1e-9))
        move(self.host, oid, (row['pos'][0]+delta[0]/max(1., distance)*speed*dt,
                             row['pos'][1]+delta[1]/max(1., distance)*speed*dt), dt,
             2 if delta[1] < 0 else 3, 0 if delta[0] >= 0 else 180)

    def _urn_step(self, dt, live):
        for oid, urn in list(self.urns.items()):
            row = self.host.actors.get(oid)
            if row is None:
                continue
            if urn['native'].get('sin'):
                move(self.host, oid, sinusoid(urn['native'], urn['origin'], self.now), dt)
            if not urn['active']:
                if not any(math.dist(center(p), center(row)) <= self.fit['urn_wake_px']*256 for p in live.values()):
                    continue
                urn['active'] = True
            if self.now+1e-9 < urn['next']:
                continue
            for slot in range(urn['count']):
                if slot in urn['slots'] or self.now+1e-9 < urn['due'].get(slot, 0.):
                    continue
                urn['slots'][slot] = None  # reserve before the delayed birth
                urn['due'].pop(slot, None)
                urn['next'] = self.now+self.fit['urn_step_s']
                self.host.presentation.attack(oid, row['vid'], 1, 264, token=(self.now, slot))
                self._queue(oid, self.fit['urn_emit_s'], 'hunter_birth', slot=slot)
                break

    def _ground(self, oid, u, dt, target):
        row = self.host.actors[oid]
        vid = 'sc.golem' if u['vid'] == 'sc.egolem' else u['vid']
        point = u['target_point'] if target is not None else None
        c = center(row)
        busy = self.now < u['busy']
        vx = 0. if busy else u['direction']*self.policy(vid)['speed']
        if vid == 'sc.tank' and abs(row['pos'][0]-u['home'][0]) > self.fit['tank_patrol_px']*256:
            # OBS: captured tanks never leave their platform and patrol ~112px.
            u['direction'] = 1 if row['pos'][0] < u['home'][0] else -1
            vx = u['direction']*self.policy(vid)['speed']
        if vid == 'sc.golem' and point is not None:
            # USER: a golem mostly stands its ground; OBS tracks only shuffle
            # about 100px, so inside the stand range it creeps instead of
            # striding at full speed.
            if math.dist(point, c) <= self.fit['golem_stand_px']*256:
                vx *= self.fit['golem_stand_scale']
        drop = False
        if not busy and u.get('grounded') and point is not None and vid in ('sc.skeleton', 'sc.titan'):
            # OBS: a captured skeleton climbs +320px onto the player's platform.
            # One jump height serves every upward case; the old 96px request,
            # 112px drop and 80px edge reversal left an 80-112px dead band where
            # the body neither jumped nor turned.
            if point[1] < c[1]-96*256 and self.now >= u.get('next_jump', 0):
                u['vy'] = -self.fit['skeleton_jump_v' if vid == 'sc.skeleton' else 'titan_jump_v']
                u['next_jump'] = self.now+.8
                if vid == 'sc.skeleton':
                    self.host.presentation.sound(oid, 'sc.skeletonjump', self.now)
                u['grounded'] = False
            elif point[1] > c[1]+96*256:
                drop = True
        if not busy and u.get('grounded') and edge_ahead(self.terrain, row, u, u['direction'], 8*256):
            # Patrol respects edges; pursuit can leave a platform for a lower
            # target, while upward pursuit uses its independent jump.
            if vid in ('sc.tank', 'sc.golem') or point is None or point[1] <= c[1]+96*256:
                u['direction'] *= -1
                vx *= -1
        previous = u.get('grounded', False)
        pos = ground(self.terrain, row, u, dt, vx, self.fit['golem_gravity'] if vid == 'sc.golem' else self.fit['gravity'], drop=drop)
        if u.get('blocked') and not busy:
            if target is None:
                u['direction'] *= -1
            elif vid in ('sc.skeleton', 'sc.titan') and u.get('grounded'):
                u['vy'] = -self.fit['skeleton_jump_v' if vid == 'sc.skeleton' else 'titan_jump_v']
        if vid == 'sc.golem' and u.get('slam') and u.get('grounded') and not previous:
            u.pop('slam', None)
            self._waves(oid)
            u['busy'] = self.now+.2
            u['lock'] = u['busy']
        if u.get('grounded'):
            pose = 4 if busy else 5
        else:
            pose = 3 if u['vy'] < 0 else 1
        move(self.host, oid, pos, dt, pose, None if self.now < u['lock'] else 0 if u['direction'] > 0 else 180)

    def _fly(self, oid, u, dt, target):
        row = self.host.actors[oid]
        point = u['target_point'] if target is not None else None
        vid = u['vid']
        if vid == 'sc.flytank' and self.now < u['busy']:
            # OBS: the flying tank holds its position for the whole 4.51-4.55s
            # attack window; a zeroed integrator still let the client keep
            # interpolating the previous MOVE.
            u['vx'] = u['vy'] = 0.
            stop_motion(self.host, oid)
            return
        if vid == 'sc.assassin' and u['phase'] == 'ghost':
            self._assassin_ghost_step(oid, u, dt)
            return
        if vid == 'sc.flytank':
            # USER: the flying tank hovers at its birth height and only drifts a
            # short way left and right; it never descends or climbs.
            hover_y = u.setdefault('hover_y', row['pos'][1])
            home_x = u.setdefault('hover_x', row['pos'][0])
            span = self.fit['flytank_hover_span_px']*256
            if row['pos'][0] < home_x-span:
                u['direction'] = 1
            elif row['pos'][0] > home_x+span:
                u['direction'] = -1
            if point is not None and self.now >= u.get('steer_at', 0.):
                u['direction'] = -1 if point[0] < row['pos'][0] else 1
                u['steer_at'] = self.now+self.host.rng.uniform(*self.fit['steer_leg_s'])
            speed = self.policy(vid)['speed']
            end, hit = flight(self.terrain, row, (u['direction']*speed, 0.), dt)
            end = (end[0], hover_y)
            facing = None if self.now < u['lock'] else 0 if u['direction'] > 0 else 180
            u['vx'], u['vy'] = u['direction']*speed, 0.
            move(self.host, oid, end, dt, 2, facing)
            return
        # USER: a creature commits to a heading for a while and only re-decides
        # at intervals; it does not steer at the player's live position.
        if point is None:
            desired = (u['direction']*self.policy(vid)['speed'], math.sin(self.now*2)*5000.)
        elif self.now >= u.get('steer_at', 0.) or 'steer' not in u:
            c = center(row)
            dx, dy = point[0]-c[0], point[1]-c[1]
            if vid == 'sc.bomber':
                dy -= 180*256
            elif vid in ('sc.sniper', 'sc.monk'):
                scale = max(0., math.hypot(dx, dy)-300*256)/max(1., math.hypot(dx, dy))
                dx, dy = dx*scale, dy*scale
            length = max(1., math.hypot(dx, dy))
            speed = min(self.policy(vid)['speed'], length*2.)
            u['steer'] = (dx/length*speed, dy/length*speed)
            u['steer_at'] = self.now+self.host.rng.uniform(*self.fit['steer_leg_s'])
            desired = u['steer']
        else:
            desired = u['steer']
        acceleration = self.fit['fly_acceleration']*dt
        for axis, wanted in zip(('vx', 'vy'), desired):
            u[axis] += max(-acceleration, min(acceleration, wanted-u[axis]))
        if vid == 'sc.scout' and self.now < u.get('dive_until', 0.):
            u['vy'] += self.fit['scout_dive_acceleration']*dt
        end, hit = flight(self.terrain, row, (u['vx'], u['vy']), dt)
        size = (row['props'].get('wmp', 0), row['props'].get('hmp', 0))
        if sweep(self.terrain, end, end, size, (oid,)):
            # A carried platform can push a body into geometry; never accept a
            # position that is already inside a wall or floor.
            end = (row['pos'][0], row['pos'][1])
            u['vx'] = -u['vx']*.3
            u['vy'] = -abs(u['vy'])*.3
        if hit:
            u['vx'] = -u['vx']*.3
            u['vy'] = -abs(u['vy'])*.3
            if target is None:
                u['direction'] *= -1
            u['dive_until'] = 0.
        facing = None if self.now < u['lock'] else 0 if u['direction'] > 0 else 180
        move(self.host, oid, end, dt, 3 if u['vy'] > 0 else 2, facing)

    def _weapons(self, oid, u, target):
        if u['phase'] in ('manifest', 'ghost') or target is None or self.now < u['next'] or self.now < u['busy']:
            return
        row = self.host.actors[oid]
        c, point = center(row), u['target_point']
        if point is None:
            return
        dx, dy = point[0]-c[0], point[1]-c[1]
        aim = math.degrees(math.atan2(dy, dx)) % 360.
        if math.hypot(dx, dy) > self.policy(u['vid'])['attack_px']*256:
            return
        vid = 'sc.golem' if u['vid'] == 'sc.egolem' else u['vid']
        if vid == 'sc.skeleton':
            if not u.get('grounded') or abs(dy) > self.fit['skeleton_melee_vertical_px']*256:
                return
            cleave = u['variant'] == 'cleave'
            self._attack(oid, u, 6, 726 if cleave else 594, window=.405,
                         cooldown=self.fit['skeleton_cleave_cooldown' if cleave else 'skeleton_thrust_cooldown'])
            prop(self.host, oid, 'pose', 4)
        elif vid == 'sc.golem':
            if not u.get('grounded'):
                return
            if math.hypot(dx, dy) < self.fit['golem_close_px']*256 and self.host.rng.random() < self.fit['golem_jump_weight']:
                self._attack(oid, u, 4, 1320, window=.92, cooldown=1.18)
                u.update(vy=-self.fit['golem_jump_v'], grounded=False, slam=True)
            else:
                anim = self.host.rng.choice((9, 11))
                self._attack(oid, u, anim, 2310, cooldown=2.39)
                self._queue(oid, 1.518, 'golemb', target=point, direction=u['direction'],
                            angle=math.atan2(dy, dx), variant=anim)
        elif vid in ('sc.tank', 'sc.flytank'):
            flying = vid == 'sc.flytank'
            self._attack(oid, u, 1, 396 if flying else 1254,
                         window=4.525 if flying else 2.525, cooldown=5.55 if flying else 3.125)
            prop(self.host, oid, 'pose', 1 if flying else 4)
            # OBS: captured tank/flytank facings are arbitrary angles (-33/-47/8),
            # and the beam damage follows the body's facing, so the aim has to be
            # published instead of the horizontal 0/180 movement direction.
            prop(self.host, oid, 'facing', round(aim) % 360)
            self._laser(oid, point)
        elif vid == 'sc.sniper':
            self._attack(oid, u, 4, 1320, cooldown=3.1)
            angle = math.atan2(dy, dx)
            # OBS general-create medians, not the earlier sound-clock offsets.
            for delay, offset in zip((.409, .505, .561, .614, .675), (0, 5, -5, 10, -10)):
                self._queue(oid, delay, 'sliver', angle=angle+math.radians(offset))
        elif vid == 'sc.bomber':
            self._attack(oid, u, 4, 1320, cooldown=3.64, lock=False)
            # OBS general-create medians 305/679/954.5ms.
            for delay in (.305, .679, .9545):
                self._queue(oid, delay, 'shard')
        elif vid == 'sc.scout':
            self.host.presentation.sound(oid, 'sc.scoutattack', self.now)
            u.update(dive_until=self.now+self.fit['scout_dive_s'], next=self.now+self.fit['scout_cooldown_s'])
            self.counts['attacks'] += 1
        elif vid == 'sc.assassin':
            self._assassin_ghost(oid, u)
        elif vid == 'sc.monk':
            self._monk_attack(oid, u, point)
        # Titan has contact damage and height navigation, no invented attack.

    def _laser(self, oid, point):
        row = self.host.actors[oid]
        c = center(row)
        angle = math.atan2(point[1]-c[1], point[0]-c[0])
        # USER: the beam left the middle of the body. Start it on the body edge
        # along the aim instead of at the centre.
        w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
        reach = (w*abs(math.cos(angle))+h*abs(math.sin(angle)))/2
        c = (c[0]+math.cos(angle)*reach, c[1]+math.sin(angle)*reach)
        # USER: the beam is one fixed length and only a solid wall stops it;
        # platforms never shorten it and it does not stop at the player.
        length = self.fit['laser_range_px']*256
        hit = sweep(geometry(self.host), c,
                    (c[0]+math.cos(angle)*length, c[1]+math.sin(angle)*length), (0, 0), (oid,))
        if hit:
            length = max(0., hit[0]*length-8*256)
        step = max(1., self.fit['laser_segment_px']*256)
        # OBS: captured beams place a segment every 32px and stop at 50 of them.
        count = max(1, min(self.fit['laser_max_segments']-1, math.ceil(length/step)))
        for index in range(count+1):
            vid = 'sc.aimend' if index == count else 'sc.aim'
            p = self.host.pack.props(vid)
            d = min(length, index*step)
            pos = (c[0]+math.cos(angle)*d-p['wmp']/2, c[1]+math.sin(angle)*d-p['hmp']/2)
            # OBS: the captured terminal segment carries z=61 while the plain
            # segments omit z, which made the beam render behind platforms.
            child = self.host._create(vid, pos, 0, dict(facing=round(math.degrees(angle)),
                                                       lifetime=1254, z=61, damage=15))
            self.own(child)
            self.effects.add(child)

    def _waves(self, oid):
        row = self.host.actors[oid]
        x, y, w, h = rect(row)
        for direction in (-1, 1):
            p = self.host.pack.props('sc.golemw')
            pos = (x+w if direction > 0 else x-p['wmp'], y+h-p['hmp'])
            # USER: the ground wave runs along the floor and cannot fly.
            self.projectiles.spawn('sc.golemw', pos, (direction*148500., 0.), oid, creator=0,
                                   gravity=self.fit['golem_wave_gravity'])

    def _assassin_ghost(self, oid, u):
        row = self.host.actors[oid]
        u['saved_hp'] = row['hp']
        u.update(phase='ghost', ghost_steps=self.host.rng.choices(tuple(self.fit['assassin_segments']),
                                                                self.fit['assassin_segment_weights'])[0],
                 ghost_at=self.now+self.fit['assassin_ghost_wait_s'], ghost_motion=None,
                 next=math.inf, busy=0., lock=0., vx=0., vy=0.)
        prop(self.host, oid, 'vid', 'sc.eassassin')
        prop(self.host, oid, 'damage', 0)
        prop(self.host, oid, 'hitboxdamage', 0)
        prop(self.host, oid, 'ownertype', 0)
        prop(self.host, oid, 'blocktype', 0)
        prop(self.host, oid, 'hitpoints', None)  # wire clear, never HP=0
        row['hp'] = None
        self.host.presentation.attack(oid, 'sc.eassassin', 1, 264, token=self.now)

    def _assassin_ghost_step(self, oid, u, dt):
        row = self.host.actors[oid]
        if u.get('ghost_cancelled'):
            # A room return cancels the old burst rather than replaying it.
            # Restore the injured body silently, once it is outside terrain.
            if clear(self.host, rect(row), (oid,)):
                self._assassin_burst(oid, u, attack=False)
                return
            if u['target_point'] is None:
                return
        motion = u.get('ghost_motion')
        if motion:
            f = min(1., (self.now-motion['start'])/motion['duration'])
            point = tuple(motion['origin'][i]+(motion['end'][i]-motion['origin'][i])*f for i in (0, 1))
            move(self.host, oid, point, dt, facing=0 if motion['end'][0] >= motion['origin'][0] else 180)
            if f >= 1:
                u.update(ghost_motion=None, ghost_at=self.now+self.fit['assassin_pause_s'])
            return
        if self.now < u['ghost_at']:
            return
        if u['ghost_steps'] <= 0 and clear(self.host, rect(row), (oid,)):
            self._assassin_burst(oid, u)
            return
        point = u.get('target_point') or center(row)
        radius = self.host.rng.uniform(64., 200.)*256  # FIT flank placement
        angle = self.host.rng.uniform(-math.pi, math.pi)
        c = center(row)
        desired = (point[0]+math.cos(angle)*radius, point[1]+math.sin(angle)*radius)
        pos = (desired[0]-(c[0]-row['pos'][0]), desired[1]-(c[1]-row['pos'][1]))
        u['ghost_motion'] = dict(start=self.now, duration=self.fit['assassin_move_s'],
                                  origin=tuple(row['pos']), end=pos)
        u['ghost_steps'] -= 1

    def _assassin_burst(self, oid, u, *, attack=True):
        row = self.host.actors[oid]
        prop(self.host, oid, 'vid', 'sc.assassin')
        row['hp'] = u['saved_hp']
        for name, value in (('damage', 35), ('hitpoints', row['hp']), ('ownertype', 2), ('blocktype', 1)):
            prop(self.host, oid, name, value)
        u.pop('ghost_cancelled', None)
        u.update(phase='chase' if u['target'] is not None else 'patrol', busy=self.now+.396 if attack else 0.,
                 next=self.now+self.fit['assassin_visible_s'])
        if not attack:
            prop(self.host, oid, 'animprops', None)
            return
        self.host.presentation.attack(oid, 'sc.assassin', 2, 396, token=self.now)
        base = self.host.rng.uniform(-math.pi, math.pi)
        for index in range(8):
            angle = base+index*math.pi/4+self.host.rng.uniform(-self.fit['burst_jitter_rad'], self.fit['burst_jitter_rad'])
            speed = self.fit['burst_speed']*self.host.rng.uniform(.7, 1.3)
            self.projectiles.spawn('sc.burst', self._origin(oid, 'sc.burst'),
                                   (math.cos(angle)*speed, math.sin(angle)*speed), 0)
        self.counts['bursts'] += 1

    def _find_orbs(self, oid):
        u = self.units[oid]
        for child, row in list(self.host.actors.items()):
            if row['vid'] != 'sc.monkb':
                continue
            owner = (row['props'].get('move') or {}).get('relativeto', {}).get('objid', row.get('creator', 0))
            s = self.projectiles.shots.get(child)
            if owner == oid or s and s['owner'] == oid and s['orbit'] is not None:
                self.add_actor(child)
                if child not in u['orbs']:
                    u['orbs'].append(child)
        u.setdefault('refill_at', self.now)

    def _monk_refill(self, oid, u):
        u['orbs'] = [x for x in u['orbs'] if x in self.projectiles.shots and self.projectiles.shots[x]['orbit'] is not None]
        if len(u['orbs']) >= 8 or self.now+1e-9 < u.get('refill_at', self.now) or u.get('monk_firing'):
            return
        index = len(u['orbs'])
        phase = index*math.pi/4
        child = self.host._create('sc.monkb', self._origin(oid, 'sc.monkb'), oid,
                                  dict(lifetime=0, invulnerable=True, blocktype=0, removetype=0))
        self.projectiles.adopt(child, owner=oid, orbit=phase-self.now*math.pi)
        self.host.presentation.attack(child, 'sc.monkb', 1, 264, token='birth')
        u['orbs'].append(child)
        u['refill_at'] = self.now+self.fit['monk_orb_interval_s']

    def _monk_attack(self, oid, u, point):
        orbs = list(u['orbs'])
        if len(orbs) < 8 or u.get('monk_firing'):
            return
        mode = self.host.rng.choice(tuple(self.fit['monk_mode_choices']))
        c = center(self.host.actors[oid])
        aim = math.atan2(point[1]-c[1], point[0]-c[0])
        # OBS: the full-ring volley's absolute directions have no fixed relation
        # to the player's bearing, so its phase must not be anchored to aim.
        phase = self.host.rng.uniform(0., 2*math.pi) if mode == 'radial' else 0.
        u.update(monk_firing=True, next=self.now+self.fit['monk_interval_s'])
        for index, child in enumerate(orbs):
            if mode == 'radial':
                angle = phase+index*math.pi/4
            elif mode == 'sequential':
                angle = aim-math.radians(40)+index*math.pi/16
            else:
                angle = aim+math.radians(-15+index*30/7)
            self._queue(oid, index*.25 if mode == 'sequential' else 0., 'monk_release',
                        child=child, angle=angle, last=index == len(orbs)-1)
        self.counts['monk_volleys'] += 1

    def _emit(self, event):
        oid, kind = event['owner'], event['kind']
        row = self.host.actors.get(oid)
        if row is None:
            return
        if kind == 'hunter_birth':
            urn = self.urns.get(oid)
            if urn is None:
                return
            child = self.host._create('sc.hunter', self._origin(oid, 'sc.hunter'), oid)
            self.host.actors[child]['_sc_new_hunter'] = True
            self.host.actors[child]['_sc_reserved_slot'] = event['slot']
            self.add_actor(child)
            self.units[child]['lineage'] = (oid, event['slot'])
            urn['slots'][event['slot']] = child
            self.host.presentation.attack(child, 'sc.hunter', 1, 1320, token='birth')
        elif kind == 'golemb':
            x, y, w, h = rect(row)
            ball = self.host.pack.props('sc.golemb')
            angle = event.get('angle')
            if angle is None:
                origin = (x+w if event['direction'] > 0 else x-ball['wmp'], y+h*.7-ball['hmp']/2)
                velocity = (event['direction']*136000., 0.)
            else:
                # OBS: every captured ball is born at body centre + (side 96px,
                # down 74px); the sideways sign follows the throw direction.
                origin = (x+w/2+event['direction']*96*256-ball['wmp']/2,
                          y+h/2+74*256-ball['hmp']/2)
                velocity = (math.cos(angle)*136000., math.sin(angle)*136000.)
            # FIT: the captures contain no sc.golemb movement packet at all, so
            # the 136k speed and the aimed launch are a local policy; only the
            # 1519-1570ms release delay and the solid body are observed.
            self.projectiles.spawn('sc.golemb', origin, velocity, oid,
                                   gravity=200000., bounce=True)
        elif kind == 'sliver':
            angle = event['angle']
            self.projectiles.spawn('sc.sliver', self._origin(oid, 'sc.sliver'),
                                   (math.cos(angle)*25000., math.sin(angle)*25000.), oid)
        elif kind == 'shard':
            velocity = self.host.actors[oid].get('vel', (0., 0.))
            self.projectiles.spawn('sc.shard', self._origin(oid, 'sc.shard'),
                                   (max(-15000., min(15000., velocity[0])), 0.), oid, gravity=150000.)
        elif kind == 'monk_release':
            child, u = event['child'], self.units.get(oid)
            shot = self.projectiles.shots.get(child)
            if shot:
                shot.update(orbit=None, vx=math.cos(event['angle'])*20000.,
                            vy=math.sin(event['angle'])*20000., expiry=self.now+8., born=self.now)
                prop(self.host, child, 'blocktype', 4)
                prop(self.host, child, 'removetype', 3)
                # MOVE was stopped on adoption; these endpoints are now
                # server integrated, so a stale relative orbit cannot resume.
                self.host.presentation.sound(child, 'sc.monkshoot', self.now)
                if u and child in u['orbs']:
                    u['orbs'].remove(child)
            if u and event['last']:
                u.update(monk_firing=False, refill_at=self.now+self.fit['monk_refill_s'])

    def step(self, dt):
        self.now = self.host.room_time
        # The host runs mechanics afterwards. A pre-physics hook supplies the
        # current moving-platform geometry without stepping it a second time.
        mechanics = getattr(self.host, 'mechanics', None)
        if mechanics and hasattr(mechanics, 'pre_physics'):
            mechanics.pre_physics(dt)
            self._carry(getattr(mechanics, 'platform_deltas', {}))
        self.terrain = geometry(self.host)
        live = players(self.host)
        self._respawn()
        self._emitters_step()
        self._urn_step(dt, live)
        for oid, u in list(self.units.items()):
            if oid not in self.host.actors:
                self.units.pop(oid, None)
                continue
            if u['phase'] == 'manifest':
                if self.now < u['busy']:
                    continue
                self._manifest_end(oid, u)
            target = self._scan(oid, u, live)
            if not u['active']:
                continue
            if target is None and self.now >= u['patrol_until'] and self.now >= u['lock']:
                u['patrol_until'] = self.now+self.fit['patrol_leg_s']
                row = self.host.actors[oid]
                if abs(row['pos'][0]-u['home'][0]) > self.fit['patrol_px']*256:
                    u['direction'] = 1 if row['pos'][0] < u['home'][0] else -1
            if u['vid'] == 'sc.hunter':
                self._hunter(oid, u, dt, target)
            elif u['vid'] in GROUND:
                self._weapons(oid, u, target)
                self._ground(oid, u, dt, target)
            else:
                if u['vid'] == 'sc.monk':
                    self._monk_refill(oid, u)
                self._weapons(oid, u, target)
                self._fly(oid, u, dt, target)
        ready, self.pending = self.pending, []
        for event in ready:
            if self.now+1e-9 >= event['at']:
                self._emit(event)
            else:
                self.pending.append(event)
        self.projectiles.step(dt, self.terrain)

    def _emitters_step(self):
        for emitter in self.emitters:
            if self.now+1e-9 < emitter['next']:
                continue
            profile = emitter['profile']
            birth = self.host.rng.choice(profile['births'])
            parameters = dict(profile['sin'], angle=profile['angle'], offset=birth['offset'])
            self.projectiles.spawn_sinusoid('sc.wraith', birth['pos_mp'], parameters,
                                            lifetime_s=self.fit['wraith_life_s'])
            self.counts['wraith_emissions'] += 1
            # Never drain a backlog after a delayed frame or an empty room.
            emitter['next'] = self.now+self.host.rng.uniform(*emitter['interval'])

    def _carry(self, deltas):
        for oid, u in self.units.items():
            row = self.host.actors.get(oid)
            if row is None or not u.get('grounded'):
                continue
            for platform, (old, new, size) in deltas.items():
                x, y, w, h = rect(row)
                if platform == u.get('support') or abs(y+h-old[1]) <= 2 and x < old[0]+size[0] and x+w > old[0]:
                    row['pos'][0] += new[0]-old[0]
                    row['pos'][1] += new[1]-old[1]
                    break

    def _respawn(self):
        for event in list(self.respawns):
            if self.now < event['at']:
                continue
            self.respawns.remove(event)
            oid = self.host._create(event['vid'], event['home'], 0, event['props'])
            self.add_actor(oid)
            self.units[oid]['guardian'] = event.get('guardian', False)
            if event['vid'] in GHOSTS:
                self._manifest_begin(oid, self.units[oid])
            self.counts['respawns'] += 1
            if event['vid'] == 'sc.skeleton':
                self.host.presentation.attack(oid, 'sc.skeleton', 0, 660, token='respawn')

    def retire(self, oid, reason='INTERNAL', native=False):
        if oid in self.retired:
            return
        row = self.host.actors.get(oid)
        u = self.units.pop(oid, None)
        self.retired.add(oid)
        self.owned.discard(oid)
        self.effects.discard(oid)
        self.pending = [e for e in self.pending if e['owner'] != oid]
        dead = reason in ('HITPOINTS', 'hitpoints', 2)
        if dead and u and row:
            row['_sc_killed'] = True
            self.counts['deaths'] += 1
            guardian_repeat = u.get('guardian') and self.guardian_rewarded
            if u.get('guardian'):
                mechanics = getattr(self.host, 'mechanics', None)
                callback = getattr(mechanics, 'guardian_defeated', None)
                if callback:
                    callback(self.guardian_id)
                self.guardian_rewarded = True
            lineage = u.get('lineage')
            if lineage and lineage[0] in self.urns:
                urn = self.urns[lineage[0]]
                urn['slots'].pop(lineage[1], None)
                urn['due'][lineage[1]] = self.now+self.fit['respawn_s']
            elif u['vid'] != 'sc.hunter':
                p = copy.deepcopy(u['initial'])
                p.update(vid=u['vid'], hitpoints=VITALS.get(u['vid'], p.get('hitpoints', 1)))
                p.pop('animprops', None)
                self.respawns.append(dict(at=self.now+self.fit['respawn_s'],
                    vid=u['vid'], home=u['home'], props=p, guardian=u.get('guardian')))
            if getattr(self.host, 'supplies', None) and not guardian_repeat:
                c = center(row)
                self.host.supplies.killed(*c)
        if u and u['vid'] == 'sc.monk':
            for child in list(u['orbs']):
                self.projectiles.retire(child)
        self.host._retire(oid, reason, native=native)

    def feedback(self, event):
        oid = int(event.get('victim', event.get('objid', 0)) or 0)
        if self.projectiles.feedback(event):
            return True
        if oid in self.retired:
            return True
        if oid not in self.owned:
            return False
        identity = event.get('event_id', event.get('seq'))
        key = (event.get('kind'), oid, str(identity))
        if identity is not None:
            if key in self.seen:
                return True
            self.seen.add(key)
        elif any(event is value for value in self.recent):
            return True
        self.recent = (self.recent+[event])[-256:]
        row = self.host.actors.get(oid)
        if row is None:
            return True
        self.now = self.host.room_time
        if event.get('kind') == 'damage':
            if row.get('hp') is None or row['props'].get('invulnerable'):
                # A damage report the server cannot apply is otherwise silent.
                # Name it once per actor so a real "unkillable" report is
                # diagnosable from the room log instead of looking like AI.
                if oid not in self._damage_ignored:
                    self._damage_ignored.add(oid)
                    try:
                        from ...server import log as _log
                    except Exception:
                        _log = None
                    if _log is not None:
                        _log('sc damage ignored: vid=%s oid=%s hp=%s invulnerable=%s value=%s' % (
                            row['vid'], oid, row.get('hp'),
                            bool(row['props'].get('invulnerable')), event.get('value')))
                return True
            row['hp'] = max(0, row['hp']-max(0, int(event.get('value', 0))))
            prop(self.host, oid, 'hitpoints', row['hp'])
            # USER: taking a hit never wakes a dormant creature.
            if row['hp'] == 0:
                self.retire(oid, 'HITPOINTS')
        elif event.get('kind') == 'destroy':
            self.retire(oid, event.get('reason', 'INTERNAL'), native=True)
        return True

    def expire(self, oid):
        if self.projectiles.expire(oid):
            return True
        if oid not in self.owned:
            return False
        self.retire(oid, 'LIFETIME')
        return True

    def resume(self, elapsed_s):
        """Host advanced room/wire clocks while absent; never replay attacks.

        elapsed_s is explicit room-clock input, not a wall-clock read. Return
        reconciles deaths/expiry once, leaving new decisions for normal step.
        """
        self.now = self.host.room_time
        for emitter in self.emitters:
            if emitter['next'] <= self.now:
                emitter['next'] = self.now+self.host.rng.uniform(*emitter['interval'])
        self._respawn()
        for event in self.pending:
            if event['kind'] == 'hunter_birth' and event['owner'] in self.urns:
                self.urns[event['owner']]['slots'].pop(event['slot'], None)
        self.pending.clear()
        for oid, u in self.units.items():
            u.update(target=None, target_point=None, scan=self.now,
                     monk_firing=False, vx=0., vy=0.)
            if u['phase'] == 'manifest':
                # This includes ghosts respawned by _respawn() at arrival.
                # Their new creation stage has not consumed the absence.
                if self.now < u['busy']:
                    continue
                self._manifest_end(oid, u)
            u.update(busy=0., lock=0., next=max(u['next'], self.now+.1))
            if u['phase'] == 'chase':
                u['phase'] = 'patrol'
            if u['phase'] == 'ghost':
                u.update(ghost_motion=None, ghost_cancelled=True,
                         ghost_at=self.now+self.fit['assassin_ghost_wait_s'])
                if clear(self.host, rect(self.host.actors[oid]), (oid,)):
                    self._assassin_burst(oid, u, attack=False)
            u.pop('slam', None)
            u.pop('dive_until', None)
            # Clear native animation hitboxes and the old attack stance in the
            # reconnect snapshot. No attack animation or sound is replayed.
            prop(self.host, oid, 'animprops', None)
            pose = (5 if u.get('grounded') else 1) if u['vid'] in GROUND else 3 if u['vid'] == 'sc.flytank' else 2
            prop(self.host, oid, 'pose', pose)
            stop_motion(self.host, oid)
        for oid, shot in list(self.projectiles.shots.items()):
            due = self.host.wire_lifetime_due(oid) if hasattr(self.host, 'wire_lifetime_due') else None
            if due is True or shot['orbit'] is None and self.now >= shot['expiry']:
                self.projectiles.retire(oid, 'LIFETIME')
        for oid in list(self.effects):
            due = hasattr(self.host, 'wire_lifetime_due') and self.host.wire_lifetime_due(oid) is True
            self.retire(oid, 'LIFETIME' if due else 'INTERNAL')

    def status(self):
        return dict(alive=len(self.units), families=dict(Counter(u['vid'] for u in self.units.values())),
                    urns=len(self.urns), respawning=len(self.respawns)+sum(len(v['due']) for v in self.urns.values()),
                    projectiles=len(self.projectiles.shots), effects=len(self.effects),
                    wraith_emitters=len(self.emitters),
                    states=dict(Counter(u['phase'] for u in self.units.values())), counts=dict(self.counts),
                    evidence='OBS actions/lifecycles/emit counts; USER independent distance and scan state, through-wall search, no damage wake, 120s respawn; FIT radii/periods/navigation/random choices')
