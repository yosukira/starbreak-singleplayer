"""Persistent native full-level adapter; public registration follows acceptance."""
from ..run_common.session import RunLiveSession
from ..run_common.actors import ActorSession
from .scene import EstRunPack

class EstRunLiveSession(RunLiveSession):
    MAP_ID = 'est_run'
    PACK_CLASS = EstRunPack

    def create_runtime(self, options, room):
        if self.instance.role in ('mid','boss'):
            from .encounters import create
            return create(self, options)
        from .enemies import ExplorationEnemies
        from .mechanics import ExplorationMechanics
        from .presentation import Presentation
        return ActorSession(self.proxy,self.channel,options,self.instance,self.bridge,self.run.pack,
            map_id=self.MAP_ID,enemies_factory=ExplorationEnemies,
            mechanics_factory=ExplorationMechanics,presentation_factory=Presentation)

    def encounter_finished(self):
        if self.instance.role in ('mid','boss'):
            from .encounters import finished
            return finished(self.current,self.instance.role)
        return False

initial_world = EstRunLiveSession.initial_world
world_blob = EstRunLiveSession.world_blob
