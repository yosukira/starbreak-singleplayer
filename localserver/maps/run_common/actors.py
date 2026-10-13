"""Shared actor clock, wire lifetime, feedback and presentation host.

Controllers own species decisions, mechanism rules and native MOVE simulation.
A reused RoomInstance retains this exact controller/RNG state while unoccupied.
"""
import copy
import random
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ...world_snapshot import plain
from ..hulk_mid.scene import normalize

class ActorSession:
    """Common actor host for native enemies and environment components."""
    def __init__(self, server, channel, options, instance, bridge, pack, *,
                 map_id, enemies_factory, mechanics_factory, presentation_factory, templates=None, players=None):
        self.server, self.channel, self.options = server, channel, options
        self.instance, self.bridge, self.pack = instance, bridge, pack
        self.recipe = instance.recipe
        self.map_id = map_id
        self.templates = templates or {}
        self.layouts, self.worldid = server.layouts, 1
        self.batch = DeltaBatch(self.layouts, 1)
        self.rng = random.Random(instance.seed)
        self.room_time = 0.
        self._frame_start = 0.
        self._wire_ms = self._sent_ms = 0
        self.wire_end_ms = self._slice_ms = 0
        self._in_step = False
        self._pending_creates, self._lifetimes = {}, {}
        self.frames = 0
        self.phase, self.player, self.players = 'exploring', None, {}
        self.players_provider=players
        self.retired, self.events, self.actors = set(), [], {}
        self.mechanics_config = {}
        creators = self.recipe.get('creators', {})
        for (owner, oid), obj in instance.snapshot.objects.items():
            if owner != 1:
                continue
            props = plain(copy.deepcopy(obj['props']))
            self.actors[oid] = self._row(oid, props)

            creator = creators.get(oid, creators.get(str(oid), 0))
            if creator:
                self.actors[oid]['creator'] = int(creator)
            self._set_wire_birth(oid, 0)
        self._next_id = max(self.actors, default=0) + 1000
        self.supplies = SupplyDrops(self, map_id, rng=random.Random(instance.seed ^ 0x10a7))
        self.presentation = presentation_factory(self)
        for oid in self.actors:
            self.presentation.body_created(oid)
        self.enemies, self.mechanics = enemies_factory(self), mechanics_factory(self)
        self.enemies.prepare()
        self.mechanics.prepare()

    @staticmethod
    def _row(oid, props):
        return dict(id=oid, objid=oid, vid=props.get('vid', ''), props=props,
                    pos=[props.get('xmp', 0), props.get('ymp', 0)], vel=[0., 0.],
                    hp=props.get('hitpoints'), dead=False)

    def normalize(self, typ, props):
        return normalize(typ, props, self.layouts)

    def _create(self, vid, pos, creator=0, changes=None):
        try:
            props = self.pack.props(vid)
        except KeyError:
            if vid not in self.templates:
                raise KeyError(self.map_id + ' dynamic template missing: ' + vid)
            props = copy.deepcopy(self.templates[vid])
        props.update(changes or {})
        props.update(vid=vid, xmp=round(pos[0]), ymp=round(pos[1]))
        self._next_id += 1
        oid = self._next_id
        row = self._row(oid, props)
        row['creator'] = creator
        self.actors[oid] = row
        self._set_wire_birth(oid, self._sent_ms)
        self._pending_creates[oid] = self.batch.create(oid,
            self.normalize('Properties', props), creatorid=creator, offset=self._offset())
        self.presentation.body_created(oid)
        return oid

    def _set_wire_birth(self, oid, born_ms):
        row = self.actors[oid]
        row['_wire_born_ms'] = int(born_ms)
        row['born'] = born_ms / 1000.
        lifetime = row['props'].get('lifetime', 0)
        if lifetime and lifetime > 0:
            self._lifetimes[oid] = int(born_ms) + int(lifetime)
        else:
            self._lifetimes.pop(oid, None)

    def created_at_end(self, oid):
        """A post-integration birth has not consumed this simulation slice.

        Default births stay at offset zero: ordinary shots are integrated
        during their birth tick. FX and burst children explicitly use this
        hook instead. Feedback received before time advances remains at the
        slice's start, and calls outside step are already at the wire clock.
        """
        update = self._pending_creates.get(oid)
        if update is None or not self._in_step or self.room_time <= self._frame_start:
            return
        update['offset_'] = self._slice_ms
        self._set_wire_birth(oid, self.wire_end_ms)

    def wire_lifetime_deadline(self, oid):
        return self._lifetimes.get(oid)

    def wire_lifetime_due(self, oid):
        deadline = self.wire_lifetime_deadline(oid)
        return None if deadline is None else self.wire_end_ms >= deadline

    def _finalize_birth_offsets(self):

        offsets = {oid: update['offset_'] for oid, update in self._pending_creates.items()
                   if update['offset_'] > 0}
        creators = {}
        for oid, offset in offsets.items():
            creator = self._pending_creates[oid]['create_'].get('creatorid_', 0)
            if creator:
                creators[creator] = max(creators.get(creator, 0), offset)
        for update in self.batch.objects:
            born = offsets.get(update['objid_'])
            if born is not None:
                update['offset_'] = max(update.get('offset_', 0), born)
        for update in self.batch.general:
            destroy = update.get('destroy_')
            if destroy and destroy['objid_'] in offsets:
                update['offset_'] = max(update.get('offset_', 0), offsets[destroy['objid_']])
            if destroy and destroy['objid_'] in creators:

                update['offset_'] = max(update.get('offset_', 0), creators[destroy['objid_']])

    def _expire_lifetimes(self):
        """Keep every finite native lifetime authoritative, including scenery."""
        for oid, deadline in list(self._lifetimes.items()):
            if self.wire_end_ms < deadline or oid not in self.actors:
                continue
            self._expire_actor(oid)

    def _expire_actor(self, oid):
        for controller in (self.enemies, self.mechanics):
            expire = getattr(controller, 'expire', None)
            if expire is not None and expire(oid):
                return
        self._retire(oid, 'LIFETIME')

    def unlock(self, key, **evidence):
        """Persistent room-local progress; controllers decide the real trigger."""
        metadata = self.recipe.get('metadata', {})
        if key == 'encounter:'+str(metadata.get('encounter_kind')) and metadata.get('encounter_key'):
            key = metadata['encounter_key']
        if key in self.instance.flags:
            return False
        self.instance.flags[key] = True
        self.record('room_unlock', key=key, **evidence)
        return True

    def _retire(self, oid, reason='INTERNAL', native=False):
        if oid in self.retired:
            return
        self.retired.add(oid)
        self._lifetimes.pop(oid, None)
        row = self.actors.pop(oid, None)
        if row is not None:
            row['dead'] = True

        offset = self._slice_ms if (self._in_step and reason in ('LIFETIME', 6) and
                                    self.room_time > self._frame_start) else self._offset()
        self.batch.destroy(oid, ownerid=1, reason=reason, offset=offset)

    def _offset(self):

        return 0

    def position(self, oid):
        row = self.actors.get(oid)
        return tuple(row['pos']) if row is not None else (0., 0.)

    def surfaces(self):
        return [(*row['pos'], row['props'].get('wmp', 0), row['props'].get('hmp', 0),
                 row['props']['terraintype']) for row in self.actors.values()
                if not row.get('dead') and row['props'].get('terraintype') in (1, 2)]

    solids = surfaces

    def record(self, kind, **values):
        self.events.append(dict(kind=kind, at=self.room_time, **values))
        if len(self.events) > 3000:
            self.events = self.events[-2000:]

    def request_player_teleport(self, oid, player, plan):
        return self.bridge.request_player_teleport(oid, player, plan['to'])

    def native_retirement_capability(self, oid):
        """Classify current native actors and their actual wire-clock deadline."""
        if type(oid)is not int or oid<=0 or oid in self.retired:return None
        row=self.actors.get(oid)
        if row is None or row.get('dead'):return None
        p=row['props']
        def number(value):
            value=value.get('value')if type(value)is dict else value
            return value if type(value)is int else 0
        if number(p.get('terraintype')) or number(p.get('interact')):return None
        hp=row.get('hp')
        projectiles=getattr(getattr(self.enemies,'projectiles',None),'shots',{})
        mite=getattr(getattr(self.enemies,'mites',None),'actors',{}).get(oid,{})
        if oid in projectiles or mite.get('kind')in ('disc','fire','axe','rang','debris'):role='projectile'
        elif type(hp)is int and hp>=0:role='npc'
        elif number(p.get('ownertype'))==2 and (number(p.get('damage'))>0 or number(p.get('hitboxdamage'))>0):role='projectile'
        else:role='fx'
        deadline=self.wire_lifetime_deadline(oid)
        return dict(ownerid=self.worldid,objid=oid,role=role,
                    expires_at=deadline/1000. if deadline is not None else None,
                    room_time=self.wire_end_ms/1000.,lifetime_due=self.wire_lifetime_due(oid)is True,
                    destructible_projectile=role=='projectile' and row['vid']=='ice.maxe' and number(p.get('removetype'))==3)

    def feedback(self, event):
        if event.get('kind') == 'player_teleport_result':
            callback = getattr(self.mechanics, 'teleport_feedback', None)
            if callback is not None:
                callback(event)
            return
        if event.get('ownerid') is not None and int(event['ownerid']) != 1:
            return
        if self.enemies.feedback(event) or self.mechanics.feedback(event):
            return
        oid = int(event.get('victim', event.get('objid', 0)) or 0)
        row = self.actors.get(oid)
        if row is None or row['props'].get('invulnerable'):
            return
        if event.get('kind') == 'damage' and row.get('hp') is not None:
            value = max(0, int(event.get('value', 0)))
            row['hp'] -= value
            row['props']['hitpoints'] = max(0, row['hp'])
            self.batch.modify(oid, 'HITPOINTS', intval=max(0, row['hp']))
            if row['hp'] <= 0:
                self._retire(oid, 'HITPOINTS')
        elif event.get('kind') == 'destroy' and row.get('hp') is not None:
            self._retire(oid, event.get('reason', 'INTERNAL'), native=True)

    def step(self, dt):
        self._frame_start = self.room_time
        self._in_step = True
        self._wire_ms += dt * 1000
        self.wire_end_ms = round(self._wire_ms)
        self._slice_ms = self.wire_end_ms - self._sent_ms
        self.player, _keys = self.bridge.snapshot()
        if self.players_provider is not None:
            self.players=self.players_provider() if callable(self.players_provider) else self.players_provider
        else:self.players = {(self.player.worldid, self.player.objid): self.player} if self.player else {}
        for event in self.bridge.poll_events():
            self.feedback(event)
        self.room_time += dt
        self.enemies.step(dt)
        self.mechanics.step(dt)
        self._expire_lifetimes()
        self._finalize_birth_offsets()
        self.batch.dt = self._slice_ms
        self._sent_ms = self.wire_end_ms
        self.server._send_raw(self.channel, 12, self.batch.encode(), self.map_id + '-exploration', record=False)
        self.batch.clear()
        self._pending_creates.clear()
        self._in_step = False
        self.supplies.flush()
        self.frames += 1

    def status(self):
        return dict(phase=self.phase, time=self.room_time, actors=len(self.actors), frames=self.frames,
                    enemies=self.enemies.status(), mechanics=self.mechanics.status())
