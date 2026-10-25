"""SC transport adapter, shared door handoff and persistent room runtimes."""
import math
from ..run_common.session import RunLiveSession,change
from ..run_common.actors import ActorSession
from ..run_common.commit import tracked_tick
from .scene import ScRunPack
from .run import ScLevelRun

class ScRunLiveSession(RunLiveSession):
    MAP_ID='sc_run'
    PACK_CLASS=ScRunPack
    RUN_CLASS=ScLevelRun
    # The Cathedral run arms a room as soon as its world exists and accepts
    # sparse player telemetry at a door. Other full runs keep the old timing.
    ARM_ON_CREATE=True
    DOOR_TELEMETRY_S=.5

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.recipe['metadata'].get('encounter_kind') == 'patriarch':
            # The run keeps ticking while the next native world loads. Its
            # reused bridge can still hold the previous room's character.
            self.current.duel_entry = (self._ready_after, self.instance.seed)
            self.current.duel_entry_seen = False
            self.current.viewer_platform.clear()

    @classmethod
    def entry_position(cls, run, role, entry, wmp, hmp):
        from .doors import arrival
        point = arrival(run.room(role).recipe, entry, wmp, hmp)
        return point if point is not None else super().entry_position(run, role, entry, wmp, hmp)

    def boundary_properties(self, oid, link):
        # Purple invisible trigger, not the yellow sprite bounding box.
        return self.instance.snapshot.properties_at((1, int(link['trigger_id'])))

    # Projectiles and beam effects are transient: leaving a room must not leave
    # its shots hanging in mid air for the next visit.
    TRANSIENT = ('sc.aim', 'sc.aimend', 'sc.sliver', 'sc.sliverx', 'sc.burst',
                 'sc.shard', 'sc.golemb', 'sc.golemw', 'sc.wraith', 'sc.enginefire',
                 'sc.enginefireseed', 'sc.enginefireub')

    @classmethod
    def _clear_transients(cls, instance):
        runtime = getattr(instance, 'runtime', None)
        snapshot = instance.snapshot
        stale = {oid for oid, row in instance.recipe['objects'].items()
                 if row.get('vid') in cls.TRANSIENT}
        if snapshot is not None:
            stale.update(oid for (owner, oid), row in snapshot.objects.items()
                         if owner == 1 and row['props'].get('vid_') in cls.TRANSIENT)
        actors = getattr(runtime, 'actors', {})
        stale.update(oid for oid, row in actors.items() if row['vid'] in cls.TRANSIENT)
        enemies = getattr(runtime, 'enemies', None)
        # Laser damage volumes intentionally have no VID. Their controller
        # membership, rather than a sprite-name filter, owns their lifetime.
        stale.update(getattr(enemies, 'effects', ()))
        mechanics = getattr(runtime, 'mechanics', None)
        projectiles = getattr(enemies, 'projectiles', None)
        shots = getattr(projectiles, 'shots', {})
        # A monk's orbiting inventory persists with the creature. Once a ball
        # has been released it is a transient shot like the other projectiles.
        stale.update(oid for oid, shot in shots.items()
                     if shot['vid'] == 'sc.monkb' and shot.get('orbit') is None)
        for oid in sorted(stale):
            instance.recipe['objects'].pop(oid, None)
            if oid in actors:
                if oid in shots:
                    projectiles.retire(oid, 'INTERNAL', native=True)
                elif oid in getattr(mechanics, 'owned', ()):
                    mechanics.retire(oid, 'INTERNAL', native=True)
                elif oid in getattr(enemies, 'owned', ()):
                    enemies.retire(oid, 'INTERNAL', native=True)
                else:
                    runtime._retire(oid, 'INTERNAL', native=True)
            if snapshot is not None:
                snapshot.retire((1, oid))

    @classmethod
    def initial_snapshot(cls,server,run,instance):
        cls._clear_transients(instance)
        super().initial_snapshot(server,run,instance)
        # Respawn/expiry clocks continue while a room is unoccupied. Resume
        # reconciles deadlines without replaying old attacks or simulating an
        # unobserved fight against stale players.
        runtime=instance.runtime
        last=run._room_seen.get(instance.role)
        if runtime is not None and last is not None and hasattr(runtime,'actors') and hasattr(runtime,'enemies'):
            elapsed=max(0.,run._clock()-last)
            # Age only objects that already existed while absent. New
            # respawns/platforms created by resume are born at arrival.
            instance.snapshot.advance(elapsed)
            runtime.room_time+=elapsed
            runtime._wire_ms+=elapsed*1000
            runtime.wire_end_ms=round(runtime._wire_ms)
            runtime._sent_ms=runtime.wire_end_ms
            runtime._slice_ms=0
            # Reconcile even a zero/short revisit: its warning effects were
            # cleared above, so their queued damage must also be cancelled.
            # Resolve platform phases before carrying survivors/respawning.
            for controller in (runtime.mechanics,runtime.enemies):
                resume=getattr(controller,'resume',None)
                if resume:resume(elapsed)
            runtime._expire_lifetimes()
            # Cleanup must reach CreateWorld even on an immediate revisit;
            # natural lifetime expiry is not the room-exit projectile policy.
            if runtime.batch.general or runtime.batch.objects or runtime.batch.extra:
                runtime.batch.dt=0
                instance.snapshot.apply(runtime.batch.message())
                runtime._sent_ms=runtime.wire_end_ms
                runtime.batch.clear();runtime._pending_creates.clear()
        run._room_seen[instance.role]=run._clock()
        cls.apply_progress(run,instance)

    @classmethod
    def apply_progress(cls,run,instance,emit=False):
        super().apply_progress(run,instance,emit)
        recipe=instance.recipe
        for oid,p in recipe['objects'].items():
            vid=p.get('vid','')
            if vid.startswith('sc.task'):
                # Original status screens have four task positions.
                slots=sorted(i for i,q in recipe['objects'].items() if q.get('vid','').startswith('sc.task'))
                filled=slots.index(oid)<len(run.gens)
                change(instance,oid,'vid','sc.taskfull' if filled else 'sc.taskempty',emit)
        minute_ids=[oid for oid,p in recipe['objects'].items() if p.get('vid','').startswith('sc.min')]
        if minute_ids:
            # These sprites have 0..4 individual lit minute units. Preserve the
            # authored direction (rightmost fills first), including Gen bonus.
            minute_ids.sort(key=lambda oid:recipe['objects'][oid].get('xmp',0))
            minutes=min(4*len(minute_ids),math.ceil(run.remaining()/60))
            for oid in reversed(minute_ids):
                value=min(4,minutes);minutes-=value
                change(instance,oid,'vid','sc.min'+str(value),emit)

    def create_runtime(self,options,room):
        kind=self.instance.recipe['metadata'].get('encounter_kind')
        if kind in ('patriarch','bonesaw','hive','boss'):
            from .encounters import create
            return create(self,options)
        from .enemies import ScEnemies
        from .mechanics import ScMechanics
        from .presentation import ScPresentation
        return ActorSession(self.proxy,self.channel,options,self.instance,self.bridge,self.run.pack,
                            map_id=self.MAP_ID,enemies_factory=ScEnemies,
                            mechanics_factory=ScMechanics,presentation_factory=ScPresentation)

    def encounter_finished(self):
        kind=self.instance.recipe['metadata'].get('encounter_kind')
        if kind in ('patriarch','bonesaw','hive','boss'):
            from .encounters import finished
            done=finished(self.current,kind)
            if done and kind!='boss':self.instance.flags['mid_cleared']=True
            return done
        return bool(self.instance.flags.get('mid_cleared'))

    def _notice(self,text,red=False,chattype=None):
        payload=self.layouts.encode('Chat',dict(message_=text,filteredmessage_=text,
                                                chattype_=(6 if red else 3) if chattype is None else chattype))
        self.server._send_raw(self.channel,20,payload,'sc-announcement',record=False)

    def _flush_progress(self):
        batch=self.current.batch
        if batch.general or batch.objects or batch.extra:
            # The child already advanced the room clock. Progress changes
            # made afterwards belong to the same commit, with no extra dt.
            batch.dt=0
            self.proxy._send_raw(self.channel,12,batch.encode(),'sc-progress',record=False)
            batch.clear()

    def _cancel_superseded_handoff(self):
        if not self.run.boss_due():return
        pending=getattr(self,'_inventory_handoff',None)
        if pending is None or pending.link.get('destination')=='boss':return
        pending.cancel('SC global transition superseded the room intent')
        publish=getattr(self.server,'_publish_inventory_control',None)
        if publish:publish(self,None)
        else:
            context=dict(self.bridge.native_context or {})
            context.pop('inventory_request',None)
            self.bridge.native_context=context
        self._inventory_handoff=None
        self._inventory_status=dict(status='cancelled',reason=pending.reason)

    def after_room_step(self):
        # USER: singleplayer/activity runs retain Chest rooms and guardian
        # progress, but do not issue equipment chests, including after Mid.
        # Ordinary creature supplies belong to their existing controllers.
        return

    @tracked_tick
    def step(self,dt):
        # One commit boundary includes the SC-wide timer, rewards and collapse
        # work as well as the shared room tick. Do not nest tracked_tick.
        # Collapse also advances during loading. Commit it before polling any
        # older door intent, which may now be superseded by the Boss warp.
        self.run.observe(self.instance)
        self._cancel_superseded_handoff()
        RunLiveSession.step.__wrapped__(self,dt)
        if self.suppress_send:
            self.run.observe(self.instance)
            return
        if not self._ready:return
        run=self.run
        run.observe(self.instance)
        announced=getattr(run,'_announced_gen_count',0)
        for count in range(announced+1,len(run.gens)+1):
            self._notice('Gen%d Offline' % count,chattype=5)
        run._announced_gen_count=len(run.gens)
        minutes=run.timer_notice()
        if minutes is not None:
            self._notice('Firing Sequence Completes in %d Minutes' % minutes,chattype=5)
        # Every task/Gen interaction is preserved by the existing shared
        # input handoff, including held movement and one consumed door press.
        self.apply_progress(run,self.instance,emit=True)
        age=run.collapse_age()
        if age is not None and not run.boss_entered:
            if not getattr(run,'_announced',False):
                self._notice('Firing Sequence Cancelled',chattype=5)
                run._announced=True
            level=min(20,int(age))
            previous=getattr(run,'_shake_level',0)
            if level>previous:
                from ...live.wire import DeltaBatch
                batch=DeltaBatch(self.layouts,1)
                batch.shake(2*level)
                self.proxy._send_raw(self.channel,12,batch.encode(),'sc-collapse',record=False)
                run._shake_level=level
        player,_keys=self.bridge.snapshot()
        # The shared tick commits encounter:boss and opens the required
        # external link, including native DRAW and INTERACT, on victory.
        self._flush_progress()
        if player is not None and run.boss_due():
            self._transition(dict(destination='boss',entry='default',trigger='automatic'),player)

initial_world=ScRunLiveSession.initial_world
world_blob=ScRunLiveSession.world_blob
