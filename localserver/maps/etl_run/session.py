"""Persistent native full-level adapter; public registration follows acceptance."""
from ..run_common.session import RunLiveSession
from ..run_common.actors import ActorSession
from .scene import EtlRunPack
from .route import EtlLevelRun

class EtlActorSession(ActorSession):
    def unlock(self,key,**evidence):
        metadata=self.recipe.get('metadata',{})
        if key=='bulkhead_switch' and metadata.get('encounter_key'):
            key='encounter:'+metadata['encounter_kind']
        return super().unlock(key,**evidence)

class EtlRunLiveSession(RunLiveSession):
    MAP_ID = 'etl_run'
    PACK_CLASS = EtlRunPack
    RUN_CLASS = EtlLevelRun

    def create_runtime(self, options, room):
        if self.instance.role in ('mid','boss'):
            from .encounters import create
            return create(self, options)
        from .enemies import ExplorationEnemies
        from .mechanics import ExplorationMechanics
        from .presentation import Presentation
        return EtlActorSession(self.proxy,self.channel,options,self.instance,self.bridge,self.run.pack,
            map_id=self.MAP_ID,enemies_factory=ExplorationEnemies,
            mechanics_factory=ExplorationMechanics,presentation_factory=Presentation)

    def encounter_finished(self):
        if self.instance.role in ('mid','boss'):
            from .encounters import finished
            return finished(self.current,self.instance.role)
        return False

initial_world = EtlRunLiveSession.initial_world
world_blob = EtlRunLiveSession.world_blob
