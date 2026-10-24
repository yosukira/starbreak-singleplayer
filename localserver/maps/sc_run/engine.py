"""Damage-driven Cathedral engine rounds and native projectile formations."""
import math

class ScEngine:
    def __init__(self, mechanics):
        self.mechanics = mechanics
        self.host = mechanics.host

    @property
    def config(self):
        return self.mechanics.config

    @property
    def now(self):
        return self.mechanics.now

    def prepare(self, state):
        state['engine'] = dict(phase='idle', next=self.now, requested=False, summons=0)

    def damage(self, state, before, after):
        e = state['engine']
        if after <= 0 or before <= after:
            return
        if e['phase'] == 'recovery' and self.now >= e['next']:
            e['phase'] = 'idle'
        if e['phase'] == 'idle':
            e['requested'] = True
        # Hits during a committed round/cooldown do not queue an unlimited
        # fight after the player stops shooting. A new eligible hit re-arms it.

    def step(self, oid, state):
        e = state['engine']
        if e['phase'] == 'recovery' and self.now >= e['next']:
            e['phase'] = 'idle'
        if e['phase'] == 'idle':
            if not e['requested']:
                return
            e['requested'] = False
            modes = tuple(self.config['engine_attack_modes'])
            if not modes or any(mode not in ('summon', 'spiral', 'wave', 'chains', 'straight') for mode in modes):
                raise ValueError('Unknown SC engine action mode')
            mode = self.host.rng.choice(modes)
            if mode == 'summon':
                e['summons'] += 1
                self._summon(oid)
                self._recover(e)
                self.host.record('sc_engine_round', id=oid, mode='summon', summon=e['summons'])
                return
            e.update(phase='burst', mode=mode, burst=0,
                     rotation=self.host.rng.randrange(360), next=self.now)
            self.host.record('sc_engine_round', id=oid, mode=e['mode'])
        if e['phase'] != 'burst' or self.now+1e-9 < e['next']:
            return
        if e['mode'] == 'chains':
            self._chains(oid)
        elif e['mode'] == 'straight':
            self._straight(oid, e)
        else:
            self._ring(oid, e)
        e['burst'] += 1
        count = (1 if e['mode'] == 'chains' else self.config['engine_straight_bursts']
                 if e['mode'] == 'straight' else self.config['engine_ring_bursts'])
        if e['burst'] >= count:
            self._recover(e)
        else:
            interval = 'engine_straight_interval_s' if e['mode'] == 'straight' else 'engine_burst_interval_s'
            e['next'] = self.now+self.mechanics._range(interval)

    def _recover(self, state):
        state.update(phase='recovery', next=self.now+self.mechanics._range('engine_round_pause_s'))

    def resume(self, state):
        e = state['engine']
        e.update(phase='idle', requested=False, next=self.now)
        # Progress and already-existing fire devices persist. No old burst or
        # sound is replayed after an unoccupied interval.

    def _summon(self, oid):
        m = self.mechanics
        cx, cy = m._core_center(oid)
        self._cue(oid, 2, 'sc.enginesummon')
        for _ in range(self.config['engine_seed_count']):
            angle = self.host.rng.uniform(0, 2*math.pi)
            speed = self.config['engine_seed_wire_speed']
            movement = dict(vel=dict(xvel=round(math.cos(angle)*speed), yvel=round(math.sin(angle)*speed)))
            child = m._spawn('sc.enginefireseed', (cx-8*256, cy-8*256), oid)
            self._birth_at_end(child)
            m._movement(child, movement)
            m.states[child]['core'] = oid

    def _shot(self, oid, center, movement, vid='sc.enginefire', changes=None):
        child = self.mechanics._spawn(vid, (center[0]-16*256, center[1]-16*256), oid, changes)
        self._birth_at_end(child)
        self.mechanics._movement(child, movement)
        track = self.mechanics.tracks.get(child)
        if track:
            self.host.actors[child]['pos'] = list(track.position(round(self.now*1000)))
        return child

    def _birth_at_end(self, oid):
        callback = getattr(self.host, 'created_at_end', None)
        if callback:
            callback(oid)

    def _cue(self, oid, animation, sound):
        # These rounds are emitted after integration. Keep the actor cue,
        # child MOVE and native birth/lifetime on that same wire boundary.
        offset = self.host._slice_ms if getattr(self.host, '_in_step', False) else self.host._offset()
        self.host.actors[oid]['props']['animprops'] = dict(animid=animation, time=990)
        self.host.batch.animprops(oid, animation, 990, offset=offset)
        self.host.batch.sound(oid, sound, offset=offset)

    def _ring(self, oid, state):
        center = self.mechanics._core_center(oid)
        cx, cy = map(round, center)
        self._cue(oid, 1, 'sc.engineshoot')
        count = self.config['engine_ring_count']
        if state['mode'] == 'spiral':
            # Native radial velocity is MP/ms; pos is angular phase only.
            # Alternating rbvel changes spiral handedness across three bursts.
            phase = self.host.rng.randrange(12000)
            sign = 1 if state['burst'] % 2 == 0 else -1
            for index in range(count):
                self._shot(oid, center, dict(elliptical=dict(xmp=cx, ymp=cy,
                    radiusamp=0, radiusbmp=0, ravel=70, rbvel=sign*70,
                    period=12000, pos=phase, angle=round(index*360/count))))
        else:
            for index in range(count):
                self._shot(oid, center, dict(sin=dict(xmp=cx, ymp=cy,
                    vel=self.config['engine_ring_wire_speed'],
                    period=self.config['engine_wave_period_px'], amp=self.config['engine_ring_amplitude'],
                    angle=(state['rotation']+round(index*360/count)) % 360)))

    def _chains(self, oid):
        center = self.mechanics._core_center(oid)
        cx, cy = map(round, center)
        # Observed coupled profiles: high-HP three positive-handed chains,
        # low-HP six negative-handed chains. Half HP is a local phase fit.
        low = self.host.actors[oid]['hp'] <= self.config['engine_hp']*self.config['engine_chain_phase_hp_fraction']
        count, sign = (6, -1) if low else (3, 1)
        for index in range(count):
            self._cue(oid, 1, 'sc.engineshoot')
            parent = self._shot(oid, center, dict(elliptical=dict(xmp=cx, ymp=cy,
                radiusamp=0, radiusbmp=0, ravel=70, rbvel=sign*70,
                period=7000, angle=360-round(index*360/count), pos=0)))
            for _ in range(9):
                parent = self._shot(oid, center, dict(relativeto=dict(objid=parent), follow=dict(maxdist=50)))

    def _straight(self, oid, state):
        center = self.mechanics._core_center(oid)
        count = self.config['engine_ring_count']
        offset = 180/count if state['burst'] % 2 else 0
        self._cue(oid, 1, 'sc.engineshoot')
        for index in range(count):
            angle = round(state['rotation']+offset+index*360/count) % 360
            radians = math.radians(angle)
            speed = self.config['engine_ring_wire_speed']
            # Native creation truncates the wire components toward zero;
            # the native motion evaluator then arithmetic-shifts each axis.
            movement = dict(vel=dict(xvel=int(math.cos(radians)*speed),
                                    yvel=int(math.sin(radians)*speed)))
            self._shot(oid, center, movement, 'sc.enginefireub', dict(facing=angle))
