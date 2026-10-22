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

    @classmethod
    def initial_snapshot(cls,server,run,instance):
        super().initial_snapshot(server,run,instance)
        # Respawn/expiry clocks continue while a room is unoccupied. Resume
        # reconciles deadlines without replaying old attacks or simulating an
        # unobserved fight against stale players.
        runtime=instance.runtime
        last=run._room_seen.get(instance.role)
        if runtime is not None and last is not None and hasattr(runtime,'actors') and hasattr(runtime,'enemies'):
            elapsed=max(0.,run._clock()-last)
            if elapsed>.1:
                # Age only objects that already existed while absent. New
                # respawns/platforms created by resume are born at arrival.
                instance.snapshot.advance(elapsed)
                runtime.room_time+=elapsed
                runtime._wire_ms+=elapsed*1000
                runtime.wire_end_ms=round(runtime._wire_ms)
                runtime._sent_ms=runtime.wire_end_ms
                runtime._slice_ms=0
                for controller in (runtime.enemies,runtime.mechanics):
                    resume=getattr(controller,'resume',None)
                    if resume:resume(elapsed)
                runtime._expire_lifetimes()
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

    def _notice(self,text,red=False):
        payload=self.layouts.encode('Chat',dict(message_=text,filteredmessage_=text,
                                                chattype_=6 if red else 3))
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
        # Every task/Gen interaction is preserved by the existing shared
        # input handoff, including held movement and one consumed door press.
        if self.instance.flags.get('chest_cleared'):
            from .loot import ScChestLoot
            if not hasattr(self,'_chest_loot'):self._chest_loot=ScChestLoot(self)
            oid=self.instance.recipe['metadata'].get('chest_guardian')
            body=self.instance.recipe['objects'].get(oid,{})
            self._chest_loot.drop(self.instance.role,(body.get('xmp',0),body.get('ymp',0)))
        self.apply_progress(run,self.instance,emit=True)
        age=run.collapse_age()
        if age is not None and not run.boss_entered:
            if not getattr(run,'_announced',False):
                self._notice('All four generators have been destroyed.')
                self._notice('Star Cathedral is collapsing!',red=True)
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
