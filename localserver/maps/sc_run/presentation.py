"""SC observed animation/sound pairs, shared by enemies and mechanisms."""

# Original server birth updates. Shard anim1/anim2 alternate within each
# native three-shot volley; the weapon queue supplies the selected phase.
PROJECTILE_BIRTH = {
    'sc.shard': ((1, 2), 198),
    'sc.sliver': ((1,), 198),
    'sc.sliverx': ((1,), 264),
    'sc.monkb': ((1,), 264),
}


class ScPresentation:
    def __init__(self, host):
        self.host = host

    def once(self, oid, key, token):
        row = self.host.actors.get(oid)
        if row is None:
            return False
        state = row.setdefault('_sc_presentation', {})
        if key in state and state[key] == token:
            return False
        state[key] = token
        return True

    def sound(self, oid, name, token=None):
        if self.once(oid, 'sound:'+name, token):
            self.host.batch.sound(oid, name, offset=self.host._offset())

    def body_created(self, oid):
        # A CreateWorld body is not necessarily a fresh birth. Its original
        # animprops are already in the snapshot; controllers request new cues.
        row = self.host.actors.get(oid)
        if (row is not None and row.get('vid') == 'sc.aim'
                and oid in getattr(self.host, '_pending_creates', {})):
            # Laser segments are created directly by the weapon controller,
            # outside projectile_created. Their observed create flash is
            # anim1/264; aimend has no such animation in its native asset.
            self.laser_segment_created(oid)

    def laser_segment_created(self, oid):
        """One create flash across host and weapon-controller callbacks."""
        row = self.host.actors.get(oid)
        if row is not None and row.get('vid') == 'sc.aim':
            self.attack(oid, 'sc.aim', 1, 264, token='birth')

    def attack(self, oid, vid, anim, time_ms, token=None):
        if not self.once(oid, 'attack', (anim, token)):
            return
        self.host.batch.animprops(oid, anim, time_ms, offset=self.host._offset())
        row = self.host.actors.get(oid)
        if row is not None:
            row['props']['animprops'] = dict(animid=anim, time=time_ms)
        sound_vid = 'sc.golem' if vid == 'sc.egolem' else vid
        name = {('sc.skeleton', 6): 'sc.skeletonswing',
                ('sc.golem', 4): 'sc.golemjump',
                ('sc.golem', 9): 'sc.golemthrow',
                ('sc.golem', 11): 'sc.golemthrow',
                ('sc.tank', 1): 'sc.laser',
                ('sc.flytank', 1): 'sc.laser',
                ('sc.assassin', 2): 'sc.burst',
                ('sc.eassassin', 1): 'sc.ghost'}.get((sound_vid, anim))
        if name:
            self.sound(oid, name, (anim, token))

    def projectile_created(self, oid, vid):
        name = {'sc.shard': 'sc.bombdrop', 'sc.sliver': 'sc.sliver',
                'sc.sliverx': 'sc.sliverx'}.get(vid)
        if name:
            self.sound(oid, name, 'birth')
        if vid in PROJECTILE_BIRTH:
            variants, duration = PROJECTILE_BIRTH[vid]
            row = self.host.actors.get(oid) or {}
            current = row.get('props', {}).get('animprops') or {}
            anim = current.get('animid', variants[0])
            if anim not in variants:
                anim = variants[0]
            self.attack(oid, vid, anim, duration, token='birth')
        # Monk orb birth is its 264ms create animation. The observed
        # sc.monkshoot sound belongs to the later orbit-to-projectile release.

    def effect_created(self, oid, vid):
        if vid == 'sc.sliverx':
            self.projectile_created(oid, vid)


Presentation = ScPresentation
