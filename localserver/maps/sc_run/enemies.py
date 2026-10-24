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
from .physics import clear, edge_ahead, flight, geometry, ground, rect, sinusoid, surface, sweep


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
    # FIT: activation, acquisition and weapon radii are separate local policy.
    # Attack reception geometry does not identify native activation/search
    # thresholds, target IDs or decision-time ranges.
    'sc.skeleton': dict(wake_px=1600., acquire_px=960., release_px=1500., scan_s=.45, speed=14000*1000/256, attack_px=170.),
    # USER range calibration against a 500px Soidal: distance to the large
    # body's near edge, not its centre (which adds its 80px half-width).
    'sc.golem': dict(wake_px=475., wake_metric='body', acquire_px=1200., release_px=1700., scan_s=.55,
                     speed=7000*1000/256, attack_px=475., attack_metric='body'),
    # Oct23 solo starts have 1059..1118px reception context. 1150/1200 are
    # local attack/search fits covering those cases, not native thresholds.
    'sc.tank': dict(wake_px=1600., acquire_px=1200., release_px=1700., scan_s=.5, speed=10000*1000/256, attack_px=1150.),
    # FIT: a later single-player attack has ~738px reception geometry. Allow
    # that context without presenting 800/900 as recovered native thresholds.
    'sc.flytank': dict(wake_px=1600., acquire_px=900., release_px=1300., scan_s=.6, speed=32000., attack_px=800.),
    # New fresh hunters start while the sole player is diagonally 1881px
    # away (dx1266/dy1391). A 1600px centre circle misses that context.
    # Separate axis-aligned wake/search windows are local geometry fits.
    'sc.hunter': dict(wake_px=1600., wake_shape='box', acquire_px=1600.,
                      acquire_shape='box', release_px=2400., scan_s=.4,
                      speed=118000., attack_px=150.),
    # USER: monks activate locally, rather than the whole room waking on entry.
    # Existing captured residents are already moving; 900px is a configurable
    # fresh-activation FIT, not a threshold recovered from those snapshots.
    'sc.monk': dict(wake_px=900., acquire_px=1000., release_px=1500., scan_s=.65, speed=70000., attack_px=900.),
    # Solo dive-start contexts reach 807px. A committed attempt and its
    # blocked retries continue independently of this new-attack range.
    'sc.scout': dict(wake_px=1500., acquire_px=900., release_px=1500., scan_s=.5, speed=30000., attack_px=900.),
}
DEFAULT_FIT = dict(respawn_s=120., gravity=200000., skeleton_jump_v=181000.,
    # OBS: one skeleton rises 640px. FIT: choose height from reachable landing
    # surfaces, with a small landing margin; ordinary jumps keep the low speed.
    # OBS: native ascent fits are around 336k MP/s². Keeping the old 200k
    # gravity made even a low jump hang for 1.81s. This separate FIT preserves
    # the observed high-platform reach without changing other creatures.
    skeleton_gravity=340000., skeleton_jump_max_v=350000.,
    skeleton_jump_clearance_px=32., skeleton_platform_wait_s=.15,
    titan_jump_v=125000., golem_jump_v=90000., golem_gravity=200000.,
    # FIT within the separately calibrated 475px total range. Keep an outer
    # throw-only band; this is not a recovered native jump cutoff.
    golem_close_px=450., golem_jump_weight=.5, golem_restitution=.8,
    # OBS: a fresh room starts surn production before any player subworld is
    # present. Local production therefore starts independently of creature
    # wake/acquisition. Native births use the moving urn's top-left plus
    # (1, -18)px, not body centres.
    # FIT: a .2s minimum plus frame scheduling covers the observed 200-255ms
    # between births; the native scheduler itself is not reconstructed.
    hunter_manifest_px=160., hunter_acceleration=80000., urn_step_s=.2, urn_birth_offset_px=(1., -18.),
    urn_emit_s=.105, manifest_s=.83, titan_manifest_s=1.62,
    skeleton_thrust_cooldown=.605, skeleton_cleave_cooldown=.8,
    skeleton_variant_policy='object_id_fit', patrol_px=240., patrol_leg_s=1.1, steer_leg_s=(.45, 1.0),
    # FIT minimum; observed visible intervals include 1.560 and 1.830 seconds.
    # Reappearance is governed by the live target and clear space, not a draw
    # from the number of movement legs seen in a recording.
    assassin_visible_s=1.5, assassin_initial_wait_s=.1,
    assassin_ghost_wait_s=.325, assassin_pause_s=.78,
    # FIT to 65 checked new-recording legs: distance ~= 400px + 1284px/s*t.
    # The instantaneous spatial component is not a constant-speed movement.
    assassin_ghost_speed=1284.4*256, assassin_ghost_instant_px=400.,
    assassin_ghost_min_step_s=.008, assassin_manifest_px=160.,
    # FIT selection policy. Capture frequencies do not establish spawn odds.
    ghost_form_weights={'sc.titan': 15., 'sc.scout': 33.5, 'sc.bomber': 33.5,
                        'sc.sniper': 33.5, 'sc.assassin': 33.5},
    # OBS: burst endpoint components are integer multiples of 1000 within
    # [-16000, 15000]mp/s. Independent uniform selection is a local FIT;
    # neither an eight-direction ring nor the native RNG was established.
    burst_axis_mp_s=(-16000, 15000), shard_gravity=156000.,
    # OBS: two same-generation full volleys repeat in 7.762/7.764s: ~1.91s
    # refill wait, staggered eight-orb refill, then ~4.1s full-orb preparation.
    # Preparation starts only with an eligible target; it commits that aim.
    # Optional interval remains a minimum override, not the old 16s timer.
    monk_interval_s=0., monk_prepare_s=4., monk_refill_s=1.92, monk_orb_interval_s=.25,
    # Three long tracks remain in a translated ~413x417px area. The authored
    # local birth point anchors this 420px box; it is not a recovered home
    # from an arbitrary already-active capture snapshot.
    monk_patrol_span_px=210., monk_acceleration=160000., monk_patrol_leg_s=2.5,
    monk_mode_choices=('sequential', 'radial', 'focused'),
    # OBS: a captured warning line is 13 segments of 64px (~832px).
    laser_segment_px=32., laser_range_px=1600., laser_max_segments=50, laser_draw_z=-128,
    fly_acceleration=50000., scout_dive_acceleration=350000., scout_dive_speed=175000.,
    # FIT safety limit, not a fixed action time: a native dive lands after 2.279s.
    scout_dive_s=3., scout_cooldown_s=(2.2, 3.4),
    scout_min_drop_px=32., scout_recover_s=1., scout_recover_speed=60000.,
    # FIT to post-landing velocity changes, independently of ordinary flight.
    scout_recover_acceleration=120000.,
    # Blocked cues can repeat within one attempt. They do not reset the
    # independent attack cooldown; successful floor contact starts recovery.
    scout_blocked_retry_s=.55, scout_max_retries=2, skeleton_melee_vertical_px=110.,
    flytank_hover_span_px=180., golem_stand_px=520., golem_stand_scale=.25, golem_wave_gravity=180000.,
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
            self.projectiles.adopt(oid, gravity=self.fit['shard_gravity'] if vid == 'sc.shard' else 200000. if vid == 'sc.golemb' else 0., bounce=vid == 'sc.golemb')
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
                fit_id = oid
                if self.fit['skeleton_variant_policy'] == 'object_id_fit':
                    sources = self.host.recipe.get('metadata', {}).get('sc_skeleton_source_ids', {})
                    fit_id = int(sources.get(str(oid), sources.get(oid, oid)))
                variant = 'cleave' if self.fit['skeleton_variant_policy'] == 'object_id_fit' and fit_id % 4 == 0 else 'thrust'
            u['variant'] = variant
        if family == 'sc.hunter':
            # Birth anim1/1320ms is presentation, not an action lock. Native
            # children can manifest 0..105ms after birth when already near.
            u['ready'] = self.now
        if family in GHOSTS and row.get('hp') is None and vid != 'sc.eassassin':
            self._manifest_begin(oid, u)
        elif vid == 'sc.eassassin':
            u.update(active=True, phase='ghost', saved_hp=VITALS['sc.assassin'], ghost_moves=0,
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
        if family in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'):
            support = self._supporting_surface(rect(row))
            if support is not None:
                # Bind before the first mechanism step. Otherwise a rising
                # one-way floating ledge moves above unbound feet and the
                # body falls through it on its first activation frame.
                u.update(grounded=True, support=support[0], vy=0.)
                platform = self.host.actors[support[0]]
                u.update(home_support=support[0], home_offset=tuple(
                    u['home'][axis]-platform['pos'][axis] for axis in (0, 1)))
                if family == 'sc.tank':
                    u['patrol_support'] = support[0]
                    u['patrol_surfaces'] = (support[0],)
        return True

    def _supporting_surface(self, bounds, *, terrain=None, tolerance=2.):
        x, y, w, h = bounds
        candidates = []
        surfaces = geometry(self.host) if terrain is None else terrain
        for oid, r, _kind, shape in surfaces:
            if self.host.actors.get(oid, {}).get('vid') in BODIES | SHOTS:
                continue
            top = surface(r, shape, x, w)
            if top is not None and abs(top-y-h) <= tolerance:
                overlap = min(x+w, r[0]+r[2])-max(x, r[0])
                candidates.append((-overlap, oid, top))
        if not candidates:
            return None
        _overlap, support, top = min(candidates)
        return support, top

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
        if u['vid'] == 'sc.titan':
            prop(self.host, oid, 'blocktype', 1)
        u.update(phase='patrol', next=self.now+self.fit['assassin_initial_wait_s'], busy=0.)

    @staticmethod
    def _body_distance(row, point):
        x, y, w, h = rect(row)
        return math.hypot(max(x-point[0], point[0]-x-w, 0.),
                          max(y-point[1], point[1]-y-h, 0.))

    @staticmethod
    def _inside_range(policy, kind, origin, point):
        radius = policy[kind+'_px']*256
        if policy.get(kind+'_shape') == 'box':
            return max(abs(point[0]-origin[0]), abs(point[1]-origin[1])) <= radius
        return math.dist(origin, point) <= radius

    def _scan(self, oid, u, live):
        row = self.host.actors[oid]
        policy = self.policy(u['vid'])
        c = center(row)
        if not u['active']:
            if policy.get('wake_metric') == 'body':
                awake = any(self._body_distance(row, center(p)) <= policy['wake_px']*256 for p in live.values())
            else:
                awake = any(self._inside_range(policy, 'wake', c, center(p)) for p in live.values())
            if not awake:
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
        locked_ghost = u['vid'] == 'sc.hunter' or (u['vid'] == 'sc.assassin' and u['phase'] == 'ghost')
        if old is not None and (locked_ghost or math.dist(center(old), c) <= policy['release_px']*256):
            target = old
        else:
            eligible = [(key, p) for key, p in live.items() if self._inside_range(policy, 'acquire', c, center(p))]
            selected = min(eligible, key=lambda kv: math.dist(center(kv[1]), c)) if eligible else (None, None)
            u['target'], target = selected
        if target is not None:
            u['target_point'] = center(target)
            dx = u['target_point'][0]-c[0]
            if (abs(dx) > 8*256 and u['vid'] not in ('sc.flytank', 'sc.tank', 'sc.monk')
                    and not (u['vid'] == 'sc.scout' and u.get('scout_phase'))):
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
        point = u.get('target_point') or center(target)
        c = center(row)
        delta = (point[0]-c[0], point[1]-c[1])
        distance = math.hypot(*delta)
        if distance <= self.fit['hunter_manifest_px']*256:
            # Local selection policy, not a recovered retail probability.
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
        desired = tuple(d/max(1., distance)*speed for d in delta)
        change = (desired[0]-u['vx'], desired[1]-u['vy'])
        scale = min(1., self.fit['hunter_acceleration']*dt/max(1., math.hypot(*change)))
        u['vx'] += change[0]*scale
        u['vy'] += change[1]*scale
        # The chosen target is sampled on the search clock. Inertia prevents
        # instant reversals; ethereal movement itself still passes walls.
        move(self.host, oid, (row['pos'][0]+u['vx']*dt, row['pos'][1]+u['vy']*dt), dt,
             2 if u['vy'] < 0 else 3, 0 if u['vx'] >= 0 else 180)

    def _urn_step(self, dt, live):
        for oid, urn in list(self.urns.items()):
            row = self.host.actors.get(oid)
            if row is None:
                continue
            if urn['native'].get('sin'):
                move(self.host, oid, sinusoid(urn['native'], urn['origin'], self.now), dt)
            if not urn['active']:
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

    def _skeleton_jump_plan(self, oid, u, point, *, allow_level=False):
        """Reach the higher platform on which the player actually stands."""
        if not u['active'] or not u.get('grounded') or point is None or allow_level:
            return None
        target_support = u.get('jump_target_support')
        if target_support is None:
            return None
        row = self.host.actors[oid]
        x, y, w, h = rect(row)
        gravity = self.fit['skeleton_gravity']
        if gravity <= 0:
            return None
        normal = self.fit['skeleton_jump_v']
        maximum = max(normal, self.fit['skeleton_jump_max_v'])
        speed = self.policy('sc.skeleton')['speed']
        margin = self.fit['skeleton_jump_clearance_px']*256
        candidates = []
        for platform, bounds, kind, shape in self.terrain:
            if platform != target_support or platform in (oid, u.get('support')):
                continue
            left, right = bounds[0], bounds[0]+bounds[2]-w
            landing_x = min(max(point[0]-w/2, left), right) if left <= right else (left+right)/2
            top = surface(bounds, shape, landing_x, w)
            if top is None or top < point[1]:
                continue
            rise = y+h-top
            if rise < 24*256:
                continue
            velocity = max(normal, math.sqrt(2*gravity*(max(0., rise)+margin)))
            if velocity > maximum:
                continue
            fall_at = (velocity+math.sqrt(max(0., velocity*velocity-2*gravity*rise)))/gravity
            vx = (landing_x-x)/max(.01, fall_at)
            if abs(vx) > speed or not clear(self.host, (landing_x, top-h, w, h), (oid,)):
                continue
            score = abs(top-point[1])+abs(landing_x+w/2-point[0])*.5
            candidates.append((score, platform, landing_x, top, velocity, vx, fall_at))
        for _, platform, landing_x, top, velocity, vx, fall_at in sorted(candidates)[:8]:
            # A ballistic height alone is insufficient: a ceiling or the side
            # of a solid platform can make the intended landing unreachable.
            high = y-velocity*velocity/(2*gravity)
            nearby = [t for t in self.terrain if t[1][0] < max(x, landing_x)+2*w
                      and t[1][0]+t[1][2] > min(x, landing_x)-w
                      and t[1][1] <= y+h+1 and t[1][1]+t[1][3] >= high-1]
            probe = dict(row, pos=list(row['pos']))
            state = dict(vy=-velocity, grounded=False, support=u.get('support'))
            elapsed = 0.
            while elapsed < fall_at+.15:
                delta = min(.05, fall_at+.15-elapsed)
                probe['pos'] = list(ground(nearby, probe, state, delta, vx, gravity))
                elapsed += delta
                if state.get('grounded'):
                    if abs(probe['pos'][1]+h-top) <= 8*256:
                        return dict(vy=-velocity, vx=vx, support=state['support'])
                    break
        return None

    def _skeleton_platform_target(self, oid, u, target):
        if target is None:
            u.pop('jump_candidate', None)
            u.pop('jump_target_support', None)
            return None
        value = target if isinstance(target, dict) else getattr(target, 'raw', {})
        # Position-only bridge frames still need a stable geometric contact;
        # when a native airborne pose/ground flag exists, honour it too.
        if value.get('grounded') is False or value.get('pose') in (1, 3):
            u.pop('jump_candidate', None)
            u.pop('jump_target_support', None)
            return None
        point = center(target)
        w = target.get('wmp', 0) if isinstance(target, dict) else getattr(target, 'wmp', 0)
        h = target.get('hmp', 0) if isinstance(target, dict) else getattr(target, 'hmp', 0)
        support = self._supporting_surface((point[0]-w/2, point[1]-h/2, w, h),
                                           terrain=self.terrain, tolerance=4*256)
        row = self.host.actors[oid]
        if support is None or support[1] >= row['pos'][1]+row['props'].get('hmp', 0)-24*256:
            u.pop('jump_candidate', None)
            u.pop('jump_target_support', None)
            return None
        candidate = (u.get('target'), support[0])
        if u.get('jump_candidate') != candidate:
            u.update(jump_candidate=candidate, jump_candidate_at=self.now)
        if self.now-u['jump_candidate_at'] < self.fit['skeleton_platform_wait_s']:
            return None
        u['jump_target_support'] = support[0]
        return point

    def _begin_skeleton_jump(self, oid, u, point, *, allow_level=False):
        if self.now < u.get('next_jump', 0.):
            return False
        plan = self._skeleton_jump_plan(oid, u, point, allow_level=allow_level)
        u['next_jump'] = self.now+(.8 if plan else .35)
        if plan is None:
            return False
        u.update(vy=plan['vy'], jump_vx=plan['vx'], jump_surface=plan['support'], grounded=False)
        if abs(plan['vx']) > 1.:
            u['direction'] = 1 if plan['vx'] > 0 else -1
        self.host.presentation.sound(oid, 'sc.skeletonjump', self.now)
        return True

    def _tank_velocity(self, oid, u, dt, busy):
        """Patrol the same connected surface, independently from gun aiming."""
        if not u.get('grounded'):
            return 0.
        row = self.host.actors[oid]
        terrain = [t for t in self.terrain if t[0] != oid
                   and self.host.actors.get(t[0], {}).get('vid') not in BODIES | SHOTS | {'sc.tshot'}]
        anchor = next((t for t in terrain if t[0] == u.get('patrol_support')), None)
        if anchor is None:
            anchor = next((t for t in terrain if t[0] == u.get('support')), None)
            if anchor is not None:
                u['patrol_support'] = anchor[0]
        if anchor is None or busy or dt <= 0:
            return 0.
        _sid, bounds, _kind, shape = anchor
        left, right, top = bounds[0], bounds[0]+bounds[2], bounds[1]
        members = [anchor[0]]
        tracks = getattr(getattr(self.host, 'mechanics', None), 'tracks', {})
        def moving_surface(item):
            return item[0] in tracks or bool(self.host.actors[item[0]]['props'].get('move'))
        if shape == 0 and not moving_surface(anchor):
            # Split tiles at the same height form one platform. A nearby lower
            # ledge, independent moving surface, or transient solid body never
            # extends it. Temporary alignment is not a shared patrol platform.
            remaining = [t for t in terrain if t[3] == 0 and abs(t[1][1]-top) <= 1
                         and not moving_surface(t)]
            changed = True
            while changed:
                changed = False
                for item in remaining[:]:
                    r = item[1]
                    if r[0] <= right+1 and r[0]+r[2] >= left-1:
                        new_left, new_right = min(left, r[0]), max(right, r[0]+r[2])
                        changed = changed or new_left != left or new_right != right
                        left, right = new_left, new_right
                        members.append(item[0])
                        remaining.remove(item)
        u['patrol_surfaces'] = tuple(set(members))
        width = row['props'].get('wmp', 0)
        if right-left <= width:
            return 0.
        x = row['pos'][0]
        # Preserve a captured initial overhang, but never increase it.
        low, high = min(left, x), max(right-width, x)
        vx = self._ground_speed('sc.tank', u['direction'])
        predicted = x+vx*dt
        if predicted < low or predicted > high:
            predicted = max(low, min(high, predicted))
            u['direction'] *= -1
        return (predicted-x)/dt

    def _ground_speed(self, vid, direction):
        # Native steady walking uses signed fixed-point truncation. In both
        # new captures +10000/-10000 wire units give +39/-40 MP per ms.
        wire = round(self.policy(vid)['speed']*256/1000)
        return (direction*wire >> 8)*1000.

    def _ground(self, oid, u, dt, target):
        row = self.host.actors[oid]
        vid = 'sc.golem' if u['vid'] == 'sc.egolem' else u['vid']
        point = u['target_point'] if target is not None else None
        c = center(row)
        # Observe actual target contact even during a swing/action cooldown.
        # Only executing the jump waits for this creature's movement gates.
        # Otherwise leaving and returning during busy reuses an old dwell time.
        jump_point = self._skeleton_platform_target(oid, u, target) if vid == 'sc.skeleton' else None
        busy = self.now < u['busy']
        vx = 0. if busy else self._ground_speed(vid, u['direction'])
        tank_edge_turned = False
        if vid == 'sc.tank':
            direction = u['direction']
            vx = self._tank_velocity(oid, u, dt, busy)
            tank_edge_turned = u['direction'] != direction
        if vid == 'sc.golem' and point is not None:
            # USER: the golem mostly holds its ground between attacks. A slow
            # perpetual chase is still a chase; close targets need no walking.
            # The boundary of this holding region remains a configurable FIT.
            if math.dist(point, c) <= self.fit['golem_stand_px']*256:
                vx = 0.
        drop = False
        if not busy and u.get('grounded') and point is not None and vid in ('sc.skeleton', 'sc.titan'):
            target_height = target.get('hmp', 0) if isinstance(target, dict) else getattr(target, 'hmp', 0)
            feet_delta = point[1]+target_height/2-row['pos'][1]-row['props'].get('hmp', 0)
            upward = feet_delta < -24*256 if vid == 'sc.skeleton' else point[1] < c[1]-96*256
            downward = feet_delta > 96*256 if vid == 'sc.skeleton' else point[1] > c[1]+96*256
            if upward and self.now >= u.get('next_jump', 0):
                if vid == 'sc.skeleton':
                    if jump_point is not None:
                        self._begin_skeleton_jump(oid, u, jump_point)
                else:
                    u['vy'] = -self.fit['titan_jump_v']
                    u['next_jump'] = self.now+.8
                    u['grounded'] = False
            elif downward:
                drop = True
        if not busy and not u.get('grounded') and 'jump_vx' in u:
            vx = u['jump_vx']
        if vid != 'sc.tank' and not busy and u.get('grounded') and edge_ahead(self.terrain, row, u, u['direction'], 8*256):
            # Patrol respects edges; pursuit can leave a platform for a lower
            # target, while upward pursuit uses its independent jump.
            if vid in ('sc.tank', 'sc.golem') or point is None or point[1] <= c[1]+96*256:
                u['direction'] *= -1
                vx *= -1
        previous = u.get('grounded', False)
        gravity = self.fit['golem_gravity'] if vid == 'sc.golem' else self.fit['skeleton_gravity'] if vid == 'sc.skeleton' else self.fit['gravity']
        pos = ground(self.terrain, row, u, dt, vx, gravity, drop=drop)
        if vid == 'sc.tank':
            # A tank's lane is a support constraint, not just an edge hint.
            # Carry, rounding or a large slice cannot send it through that
            # still-existing platform into an unrelated lower one.
            lane = [t for t in self.terrain if t[0] in u.get('patrol_surfaces', ())]
            tops = [(top, t[0]) for t in lane
                    if (top := surface(t[1], t[3], pos[0], row['props'].get('wmp', 0))) is not None]
            if tops:
                top, support = min(tops)
                pos = (pos[0], top-row['props'].get('hmp', 0))
                u.update(grounded=True, support=support, vy=0.)
        if u.get('blocked') and not busy:
            if target is None or vid == 'sc.tank':
                # The endpoint clamp may already have selected the way back.
                # Reversing again when a wall lies just inside that endpoint
                # would leave the tank pointing into the same wall forever.
                if vid != 'sc.tank' or not tank_edge_turned:
                    u['direction'] *= -1
            elif vid == 'sc.skeleton' and u.get('grounded'):
                # Wall contact alone never requests a jump. A later stable
                # higher-player-platform decision may choose a clear arc.
                u['direction'] *= -1
            elif vid == 'sc.titan' and u.get('grounded'):
                u['vy'] = -self.fit['titan_jump_v']
        if vid == 'sc.golem' and u.get('slam') and u.get('grounded') and not previous:
            u.pop('slam', None)
            self._waves(oid, pos)
            u['busy'] = self.now+.2
            u['lock'] = u['busy']
        if u.get('grounded'):
            u.pop('jump_vx', None)
            u.pop('jump_surface', None)
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
        if vid == 'sc.assassin' and point is not None:
            # The corporeal burst body waits at its manifestation point.
            # Pursuit through geometry belongs to the ethereal phase.
            u['vx'] = u['vy'] = 0.
            stop_motion(self.host, oid)
            return
        if vid == 'sc.scout' and self._scout_fly(oid, u, dt):
            return
        if vid == 'sc.monk':
            self._monk_fly(oid, u, dt)
            return
        if vid == 'sc.flytank':
            # USER: the flying tank hovers at its birth height and only drifts a
            # short way left and right; it never descends or climbs.
            hover_y = u.setdefault('hover_y', row['pos'][1])
            home_x = u.setdefault('hover_x', row['pos'][0])
            span = self.fit['flytank_hover_span_px']*256
            turn = False
            if point is not None and self.now >= u.get('steer_at', 0.):
                u['direction'] = -1 if point[0] < center(row)[0] else 1
                turn = True
            if row['pos'][0] <= home_x-span and u['direction'] < 0:
                u['direction'] = 1
                turn = True
            elif row['pos'][0] >= home_x+span and u['direction'] > 0:
                u['direction'] = -1
                turn = True
            if turn:
                u['steer_at'] = self.now+self.host.rng.uniform(*self.fit['steer_leg_s'])
            speed = self.policy(vid)['speed']
            end, hit = flight(self.terrain, row, (u['direction']*speed, 0.), dt)
            if hit:
                u['direction'] *= -1
                u['steer_at'] = self.now+self.host.rng.uniform(*self.fit['steer_leg_s'])
                turn = True
            end = (end[0], hover_y)
            facing = (0 if u['direction'] > 0 else 180) if turn and self.now >= u['lock'] else None
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

    def _scout_fly(self, oid, u, dt):
        phase = u.get('scout_phase')
        if phase is None:
            return False
        row = self.host.actors[oid]
        if phase == 'retry':
            target = players(self.host).get(u.get('dive_target_key'))
            if target is None:
                self._scout_recover(u)
                phase = 'recover'
            elif self.now < u['retry_at']:
                # The blocked descent stops, while tangential inertia remains.
                end, hit = flight(self.terrain, row, (u['vx'], 0.), dt)
                if hit:
                    u['vx'] = 0.
                move(self.host, oid, end, dt, 2,
                     0 if u['direction'] > 0 else 180)
                return True
            else:
                point = center(target)
                origin = center(row)
                dx, dy = point[0]-origin[0], point[1]-origin[1]
                if dy <= 0:
                    # FIT: do not turn a committed dive into an upward attack.
                    self._scout_recover(u)
                    phase = 'recover'
                else:
                    length = max(1., math.hypot(dx, dy))
                    # FIT: retain the chosen player, refresh only at this cue,
                    # and commit the new heading without replacing velocity.
                    u.update(scout_phase='dive', dive_cue=self.now,
                             dive_target=point, dive_heading=(dx/length, dy/length),
                             direction=1 if dx >= 0 else -1,
                             dive_until=self.now+self.fit['scout_dive_s'])
                    self.host.presentation.sound(oid, 'sc.scoutattack', self.now)
                    phase = 'dive'
        if phase == 'dive' and self.now >= u['dive_until']:
            self._scout_recover(u)
            phase = 'recover'
        if phase == 'recover':
            if self.now >= u['recover_until']:
                u.pop('scout_phase', None)
                u.pop('steer', None)
                return False
            desired = (0., -self.fit['scout_recover_speed'])
            acceleration = self.fit['scout_recover_acceleration']*dt
        else:
            desired = tuple(v*self.fit['scout_dive_speed'] for v in u['dive_heading'])
            acceleration = self.fit['scout_dive_acceleration']*dt
        delta = (desired[0]-u['vx'], desired[1]-u['vy'])
        length = math.hypot(*delta)
        scale = min(1., acceleration/max(1., length))
        u['vx'] += delta[0]*scale
        u['vy'] += delta[1]*scale
        end, hit = flight(self.terrain, row, (u['vx'], u['vy']), dt)
        if hit:
            terrain = next((t for t in self.terrain if t[0] == hit[1]), None)
            size = (row['props'].get('wmp', 0), row['props'].get('hmp', 0))
            top = surface(terrain[1], terrain[3], end[0], size[0]) if terrain else None
            floor = top is not None and u['vy'] > 0 and abs(end[1]+size[1]-top) <= 1
            blocked_early = end[1]+size[1]/2 < u['dive_target'][1]-64*256
            if phase == 'dive' and floor:
                # flight consumed only the contact fraction of dt. Finish
                # remaining tangential motion for both recovery and retry.
                fraction = max(0., min(1., hit[0]-1e-7))
                landing = dict(row, pos=[end[0], top-size[1]])
                end, side_hit = flight(self.terrain, landing,
                    (u['vx'], 0.), dt*(1.-fraction))
                if side_hit:
                    u['vx'] = 0.
            if phase == 'dive' and floor and blocked_early and u.get('dive_retries', 0) < self.fit['scout_max_retries']:
                u.update(scout_phase='retry', retry_at=max(self.now+.05,
                         u['dive_cue']+self.fit['scout_blocked_retry_s']),
                         dive_retries=u.get('dive_retries', 0)+1, vy=0.)
                end = (end[0], top-size[1])
            else:
                if phase == 'dive':
                    self._scout_recover(u)
                # Native floor contact keeps tangential inertia. A side-wall
                # collision still stops it instead of carrying through terrain.
                if not floor:
                    u['vx'] = 0.
                u['vy'] = 0.
        move(self.host, oid, end, dt, 3 if u['vy'] > 0 else 2,
             None if self.now < u['lock'] else 0 if u['direction'] > 0 else 180)
        return True

    def _scout_recover(self, u):
        u.update(scout_phase='recover', recover_until=self.now+self.fit['scout_recover_s'],
                 dive_until=0., vy=min(0., u['vy']))

    def _monk_fly(self, oid, u, dt):
        row = self.host.actors[oid]
        span = self.fit['monk_patrol_span_px']*256
        goal = u.get('patrol_goal')
        if goal is None or self.now >= u.get('patrol_goal_until', 0.) or math.dist(row['pos'], goal) < 20*256:
            goal = tuple(v+self.host.rng.uniform(-span, span) for v in u['home'])
            u.update(patrol_goal=goal, patrol_goal_until=self.now+self.fit['monk_patrol_leg_s'])
        delta = tuple(goal[i]-row['pos'][i] for i in (0, 1))
        distance = math.hypot(*delta)
        speed = min(self.policy('sc.monk')['speed'], distance*3.)
        desired = tuple(v/max(1., distance)*speed for v in delta)
        change = (desired[0]-u['vx'], desired[1]-u['vy'])
        scale = min(1., self.fit['monk_acceleration']*dt/max(1., math.hypot(*change)))
        u['vx'] += change[0]*scale
        u['vy'] += change[1]*scale
        end, hit = flight(self.terrain, row, (u['vx'], u['vy']), dt)
        end = list(end)
        for i, axis in enumerate(('vx', 'vy')):
            bound = max(u['home'][i]-span, min(u['home'][i]+span, end[i]))
            if bound != end[i] or hit:
                u[axis] *= -.3
                u.pop('patrol_goal', None)
            end[i] = bound
        if abs(u['vx']) > 1000.:
            u['direction'] = 1 if u['vx'] > 0 else -1
        move(self.host, oid, end, dt, 2 if u['vy'] < 0 else 3,
             0 if u['direction'] > 0 else 180)

    def _weapons(self, oid, u, target):
        if u['phase'] in ('manifest', 'ghost') or target is None or self.now < u['next'] or self.now < u['busy']:
            return
        row = self.host.actors[oid]
        c, point = center(row), u['target_point']
        if point is None:
            return
        dx, dy = point[0]-c[0], point[1]-c[1]
        aim = math.degrees(math.atan2(dy, dx)) % 360.
        distance = math.hypot(dx, dy)
        policy = self.policy(u['vid'])
        if policy.get('attack_metric') == 'body':
            distance = self._body_distance(row, point)
        if distance > policy['attack_px']*256:
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
            if distance < self.fit['golem_close_px']*256 and self.host.rng.random() < self.fit['golem_jump_weight']:
                self._attack(oid, u, 4, 1320, window=.92, cooldown=1.18)
                u.update(vy=-self.fit['golem_jump_v'], grounded=False, slam=True)
            else:
                anim = self.host.rng.choice((9, 11))
                self._attack(oid, u, anim, 2310, cooldown=2.39)
                self._queue(oid, 1.518, 'golemb', target=point, direction=u['direction'],
                            variant=anim)
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
            # OBS: every complete native triple alternates anim1/anim2.
            # FIT: choose the first phase per volley; its native policy is unknown.
            first = self.host.rng.choice((1, 2))
            for index, delay in enumerate((.305, .679, .9545)):
                self._queue(oid, delay, 'shard', variant=first if index % 2 == 0 else 3-first)
        elif vid == 'sc.scout':
            if u.get('scout_phase') or dy < self.fit['scout_min_drop_px']*256:
                return
            self.host.presentation.sound(oid, 'sc.scoutattack', self.now)
            cooldown = self.fit['scout_cooldown_s']
            if isinstance(cooldown, (tuple, list)):
                cooldown = self.host.rng.uniform(*cooldown)
            length = max(1., math.hypot(dx, dy))
            u.update(scout_phase='dive', dive_heading=(dx/length, dy/length),
                     dive_target=point, dive_target_key=u['target'],
                     dive_retries=0, dive_started=self.now,
                     direction=1 if dx >= 0 else -1,
                     dive_cue=self.now, dive_until=self.now+self.fit['scout_dive_s'],
                     next=self.now+cooldown)
            self.counts['attacks'] += 1
        elif vid == 'sc.assassin':
            self._assassin_ghost(oid, u)
        elif vid == 'sc.monk':
            self._monk_attack(oid, u, point, delay=self.fit['monk_prepare_s'])
        # Titan has contact damage and height navigation, no invented attack.

    def _laser(self, oid, point):
        row = self.host.actors[oid]
        c = center(row)
        angle = math.atan2(point[1]-c[1], point[0]-c[0])
        # Capture-fitted sockets: fixed head pivot plus a rotating 32px muzzle.
        # An AABB projection moves the beam far beyond the actual head when
        # aiming diagonally. The two species have different head pivots.
        side, vertical = (36., 3.) if row['vid'] == 'sc.flytank' else (44., -26.)
        direction = 1 if math.cos(angle) >= 0 else -1
        c = (c[0]+(direction*side+32*math.cos(angle))*256,
             c[1]+(vertical+32*math.sin(angle))*256)
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
        damage_points = []
        for index in range(count+1):
            vid = 'sc.aimend' if index == count else 'sc.aim'
            p = self.host.pack.props(vid)
            d = min(length, index*step)
            pos = (c[0]+math.cos(angle)*d-p['wmp']/2, c[1]+math.sin(angle)*d-p['hmp']/2)
            # WorldView draws z in descending order: lower values appear in
            # front. Keep the warning and beam above terrain and foreground art.
            child = self.host._create(vid, pos, 0, dict(facing=round(math.degrees(angle)),
                                                       lifetime=2000, z=self.fit['laser_draw_z']))
            # The birth entry point is idempotent with ActorSession._create;
            # the terminal sc.aimend has no extra animation.
            self.host.presentation.laser_segment_created(child)
            self.own(child)
            self.effects.add(child)
            damage_points.append((c[0]+math.cos(angle)*d, c[1]+math.sin(angle)*d))
        # OBS: the visible aim segments warn for about one second. Separate
        # unnamed 16px damage35 volumes then occupy those same centres until
        # the 2-second beam ends. Animation alone contains no beam hitbox.
        self._queue(oid, 1., 'laser_damage', points=damage_points, ends=self.now+2.)

    def _waves(self, oid, landed=None):
        row = self.host.actors[oid]
        x, y, w, h = rect(row)
        if landed is not None:
            x, y = landed
        for direction in (-1, 1):
            p = self.host.pack.props('sc.golemw')
            pos = (x+w if direction > 0 else x-p['wmp'], y+h-p['hmp'])
            # USER: the ground wave runs along the floor and cannot fly.
            self.projectiles.spawn('sc.golemw', pos, (direction*148500., 0.), oid, creator=0,
                                   gravity=self.fit['golem_wave_gravity'])

    def _assassin_ghost(self, oid, u):
        row = self.host.actors[oid]
        u['saved_hp'] = row['hp']
        u.update(active=True, phase='ghost', ghost_moves=0,
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
        point = u.get('target_point')
        if point is None:
            if clear(self.host, rect(row), (oid,)):
                self._assassin_burst(oid, u, attack=False)
            return
        c = center(row)
        near = math.dist(point, c) <= self.fit['assassin_manifest_px']*256
        if u.get('ghost_moves', 0) and near and clear(self.host, rect(row), (oid,)):
            self._assassin_burst(oid, u)
            return
        # USER: keep chasing the locked player through walls until near them
        # and outside terrain. Pick a clear nearby endpoint when the larger
        # ghost body cannot fit exactly on the player's centre.
        w, h = row['props'].get('wmp', 0), row['props'].get('hmp', 0)
        desired = point
        if not clear(self.host, (point[0]-w/2, point[1]-h/2, w, h), (oid,)):
            candidates = [(point[0]+math.cos(a)*r*256, point[1]+math.sin(a)*r*256)
                          for r in (32, 64, 96, 128) for a in (i*math.pi/4 for i in range(8))]
            candidates = [p for p in candidates if clear(self.host, (p[0]-w/2, p[1]-h/2, w, h), (oid,))]
            if candidates:
                desired = min(candidates, key=lambda p: math.dist(c, p))
        distance = math.dist(c, desired)
        pos = (desired[0]-(c[0]-row['pos'][0]), desired[1]-(c[1]-row['pos'][1]))
        duration = max(self.fit['assassin_ghost_min_step_s'],
                       (distance-self.fit['assassin_ghost_instant_px']*256)/self.fit['assassin_ghost_speed'])
        u['ghost_motion'] = dict(start=self.now, duration=duration,
                                  origin=tuple(row['pos']), end=pos)
        u['ghost_moves'] = u.get('ghost_moves', 0)+1

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
        low, high = (round(v/1000) for v in self.fit['burst_axis_mp_s'])
        for _ in range(8):
            # OBS: 119 simultaneous eight-shot groups; 949 computable paths
            # have independent-looking constant x/y components, not a ring.
            # FIT: sample each quantized component within the captured bounds.
            velocity = tuple(self.host.rng.randint(low, high)*1000. for _ in (0, 1))
            self.projectiles.spawn('sc.burst', self._origin(oid, 'sc.burst'),
                                   velocity, 0)
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
        # OBS: every refill starts at pos=1000 on the same 2000ms orbit.
        # Births 250ms apart produce the eight different phases naturally.
        # An additional index*45deg would cancel the staggering and stack them.
        model = dict(xmp=5120, ymp=9216, radiusamp=20480, period=2000, pos=1000)
        parent = self.host.actors[oid]
        child = self.host._create('sc.monkb', (parent['pos'][0]+5120-20480, parent['pos'][1]+9216), oid,
                                  dict(lifetime=0, invulnerable=True, blocktype=0, removetype=0,
                                       move=dict(relativeto=dict(objid=oid), elliptical=model)))
        self.projectiles.adopt(child, owner=oid, orbit=math.pi-self.now*math.pi)
        self.host.presentation.attack(child, 'sc.monkb', 1, 264, token='birth')
        u['orbs'].append(child)
        u['refill_at'] = self.now+self.fit['monk_orb_interval_s']

    def _monk_attack(self, oid, u, point, *, delay=0.):
        orbs = list(u['orbs'])
        if len(orbs) < 8 or u.get('monk_firing'):
            return
        mode = self.host.rng.choice(tuple(self.fit['monk_mode_choices']))
        c = center(self.host.actors[oid])
        aim = math.atan2(point[1]-c[1], point[0]-c[0])
        # OBS: the full-ring volley's absolute directions have no fixed relation
        # to the player's bearing, so its phase must not be anchored to aim.
        phase = self.host.rng.uniform(0., 2*math.pi) if mode == 'radial' else 0.
        # The weapon state owns this preparation/release sequence independently
        # of movement and later scans. Do not rebuild its aim every live tick.
        u.update(monk_firing=True, monk_prepare_until=self.now+delay,
                 monk_aim=tuple(point), next=self.now+max(delay, self.fit['monk_interval_s']))
        for index, child in enumerate(orbs):
            if mode == 'radial':
                angle = phase+index*math.pi/4
            elif mode == 'sequential':
                angle = aim-math.radians(40)+index*math.pi/16
            else:
                angle = aim+math.radians(-15+index*30/7)
            self._queue(oid, delay+(index*.25 if mode == 'sequential' else 0.), 'monk_release',
                        child=child, angle=angle, first=index == 0, last=index == len(orbs)-1)
        self.counts['monk_preparations'] += 1

    def _emit(self, event):
        oid, kind = event['owner'], event['kind']
        row = self.host.actors.get(oid)
        if row is None:
            return
        if kind == 'laser_damage':
            remaining = round((event['ends']-self.now)*1000)
            if remaining <= 0:
                return
            for x, y in event['points']:
                child = self.host._create('', (x-8*256, y-8*256), 0,
                    dict(wmp=4096, hmp=4096, damage=35, draw=False, lifetime=remaining))
                self.own(child)
                self.effects.add(child)
                end_birth = getattr(self.host, 'created_at_end', None)
                if end_birth:
                    end_birth(child)
        elif kind == 'hunter_birth':
            urn = self.urns.get(oid)
            if urn is None:
                return
            # The urn continues its authored bob while the birth is pending.
            # Use its current position rather than freezing the animation's
            # starting point or aligning two differently sized body centres.
            dx, dy = self.fit['urn_birth_offset_px']
            origin = (row['pos'][0]+dx*256, row['pos'][1]+dy*256)
            child = self.host._create('sc.hunter', origin, oid)
            self.host.actors[child]['_sc_new_hunter'] = True
            self.host.actors[child]['_sc_reserved_slot'] = event['slot']
            self.add_actor(child)
            self.units[child]['lineage'] = (oid, event['slot'])
            urn['slots'][event['slot']] = child
            self.host.presentation.attack(child, 'sc.hunter', 1, 1320, token='birth')
        elif kind == 'golemb':
            x, y, w, h = rect(row)
            ball = self.host.pack.props('sc.golemb')
            # OBS: high throw9 and low throw11 have separate birth sockets.
            # Retain the locked throw side while the release is pending.
            side, down = (180., -192.) if event.get('variant') == 9 else (96., 74.)
            origin = (x+w/2+event['direction']*side*256-ball['wmp']/2,
                      y+h/2+down*256-ball['hmp']/2)
            # OBS: dynamic birth endpoints keep a horizontal launch: 18 right
            # samples at +136000mp/s, 20 left samples at -137000mp/s, initial vy0.
            velocity = (136000. if event['direction'] > 0 else -137000., 0.)
            # Gravity is endpoint fitted; restitution remains local FIT.
            self.projectiles.spawn('sc.golemb', origin, velocity, oid,
                                   gravity=200000., bounce=True)
        elif kind == 'sliver':
            # OBS: all 541 native MOVE.vel vectors are integer truncations of
            # 25000*cos/sin(integer facing). 25000 is a wire amplitude, not
            # mp/second; native signed >>8 precedes millisecond integration.
            facing = round(math.degrees(event['angle']))
            angle = math.radians(facing)
            velocity = self.projectiles.wire_velocity((int(math.cos(angle)*25000),
                                                       int(math.sin(angle)*25000)))
            origin = list(self._origin(oid, 'sc.sliver'))
            side = -1 if row['props'].get('facing', 0) == 180 else 1
            target = (origin[0]+side*86*256, origin[1]-29*256)
            p = self.host.pack.props('sc.sliver')
            # OBS open-space socket; wall shortening is observed too. Axis
            # sweeps are local collision policy, not a recovered spawn solver.
            for axis in (0, 1):
                end = list(origin)
                end[axis] = target[axis]
                hit = sweep(self.terrain, origin, end, (p['wmp'], p['hmp']), (oid,))
                origin[axis] += (end[axis]-origin[axis])*(max(0., hit[0]-1e-7) if hit else 1.)
            # Keep the integer launch angle used for the wire components.
            # Quantized velocity can point a degree away from that facing.
            self.projectiles.spawn('sc.sliver', origin, velocity, oid,
                                   changes=dict(facing=facing))
        elif kind == 'shard':
            velocity = self.host.actors[oid].get('vel', (0., 0.))
            # OBS: horizontal launch follows the bomber's current movement;
            # captured values reach -59000/+58000mp/s. No 15000 clamp exists
            # in those paths. Preserve native per-millisecond quantization.
            vx = math.floor(velocity[0]/1000)*1000.
            origin = list(self._origin(oid, 'sc.shard'))
            side = -1 if row['props'].get('facing', 0) == 180 else 1
            origin[0] += side*256
            end = (origin[0], origin[1]+51*256)
            p = self.host.pack.props('sc.shard')
            # OBS: below-body socket, raised near terrain. The generic sweep
            # is a local fit for that birth collision correction.
            hit = sweep(self.terrain, origin, end, (p['wmp'], p['hmp']), (oid,))
            origin[1] += (end[1]-origin[1])*(max(0., hit[0]-1e-7) if hit else 1.)
            changes = dict(animprops=dict(animid=event['variant'], time=198)) if 'variant' in event else None
            self.projectiles.spawn('sc.shard', origin, (vx, 0.), oid,
                                   gravity=self.fit['shard_gravity'], changes=changes)
        elif kind == 'monk_release':
            child, u = event['child'], self.units.get(oid)
            shot = self.projectiles.shots.get(child)
            if shot:
                if event.get('first'):
                    self.counts['monk_volleys'] += 1
                vx, vy = self.projectiles.wire_velocity((round(math.cos(event['angle'])*20000),
                                                        round(math.sin(event['angle'])*20000)))
                shot.update(orbit=None, vx=vx, vy=vy, expiry=self.now+8., born=self.now)
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
            self._carry(getattr(mechanics, 'platform_deltas', {}), dt)
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
                if u['vid'] != 'sc.tank' and abs(row['pos'][0]-u['home'][0]) > self.fit['patrol_px']*256:
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

    def _carry(self, deltas, dt=0., *, families=None):
        for oid, u in self.units.items():
            if families is not None and u['vid'] not in families:
                continue
            row = self.host.actors.get(oid)
            if row is None or not u.get('grounded'):
                continue
            for platform, (old, new, size) in deltas.items():
                x, y, w, h = rect(row)
                if platform == u.get('support') or abs(y+h-old[1]) <= 2 and x < old[0]+size[0] and x+w > old[0]:
                    point = (row['pos'][0]+new[0]-old[0], row['pos'][1]+new[1]-old[1])
                    if u['vid'] in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'):
                        # Publish the carry even while dormant or holding an
                        # attack. The subsequent ground step can be motionless;
                        # changing only row.pos leaves the native snapshot behind.
                        if dt > 0:
                            move(self.host, oid, point, dt)
                        else:
                            place(self.host, oid, point)
                    else:
                        row['pos'] = list(point)
                    break

    def _ground_respawn_point(self, event):
        """Keep the birth point in its original support's moving frame."""
        home = event['home']
        support = event.get('home_support')
        if support is None:
            return home
        w, h = event['props'].get('wmp', 0), event['props'].get('hmp', 0)
        platform = self.host.actors.get(support)
        if (platform is not None and not platform.get('dead')
                and platform['props'].get('terraintype') in (1, 2)):
            point = tuple(platform['pos'][axis]+event['home_offset'][axis] for axis in (0, 1))
            top = surface(rect(platform), platform['props'].get('shapetype', 0), point[0], w)
            if top is not None and abs(top-point[1]-h) <= 2 and clear(self.host, (*point, w, h)):
                return point
        # A streamed/deleted platform has no remaining phase to recover. FIT:
        # prefer a legal floor below the old home column, then the nearest
        # legal support. Do not let an unrelated creature become the anchor.
        candidates = []
        for oid, bounds, _kind, shape in geometry(self.host):
            if self.host.actors[oid]['vid'] in BODIES | SHOTS:
                continue
            x, _y, width, _height = bounds
            px = max(x, min(home[0], x+width-w)) if width >= w else x+(width-w)/2
            top = surface(bounds, shape, px, w)
            if top is None:
                continue
            point = (px, top-h)
            if clear(self.host, (*point, w, h)):
                below_home = abs(px-home[0]) <= 1 and top >= home[1]+h-2
                candidates.append((0 if below_home else 1, math.dist(home, point), oid, point))
        return min(candidates)[-1] if candidates else home

    def _respawn(self):
        for event in list(self.respawns):
            if self.now < event['at']:
                continue
            self.respawns.remove(event)
            point = event['home']
            if event['vid'] in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'):
                point = self._ground_respawn_point(event)
            oid = self.host._create(event['vid'], point, 0, event['props'])
            self.add_actor(oid)
            self.units[oid]['guardian'] = event.get('guardian', False)
            if (event['vid'] == 'sc.skeleton'
                    and event.get('skeleton_variant') in ('cleave', 'thrust')):
                # The new object ID still represents this same determined unit.
                self.units[oid]['variant'] = event['skeleton_variant']
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
                event = dict(at=self.now+self.fit['respawn_s'],
                    vid=u['vid'], home=u['home'], props=p, guardian=u.get('guardian'))
                if u['vid'] == 'sc.skeleton':
                    event['skeleton_variant'] = u['variant']
                if (u['vid'] in ('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem')
                        and u.get('home_support') is not None):
                    event.update(home_support=u['home_support'], home_offset=u['home_offset'])
                self.respawns.append(event)
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
        # The reconnect adapter advances mechanisms first. Carry only the
        # existing grounded bodies before creating due respawns, whose birth
        # points already use the platform's current phase.
        mechanics = getattr(self.host, 'mechanics', None)
        self._carry(getattr(mechanics, 'platform_deltas', {}), 0.,
                    families=('sc.skeleton', 'sc.tank', 'sc.golem', 'sc.egolem'))
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
            u.pop('scout_phase', None)
            u.pop('steer', None)
            u.pop('jump_vx', None)
            u.pop('jump_surface', None)
            # Clear native animation hitboxes and the old attack stance in the
            # reconnect snapshot. No attack animation or sound is replayed.
            prop(self.host, oid, 'animprops', None)
            pose = (5 if u.get('grounded') else 1) if u['vid'] in GROUND else 3 if u['vid'] == 'sc.flytank' else 2
            prop(self.host, oid, 'pose', pose)
            stop_motion(self.host, oid)
        for oid, shot in list(self.projectiles.shots.items()):
            self.projectiles.expire_due(oid)
        self.projectiles.resume_orbits()
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
