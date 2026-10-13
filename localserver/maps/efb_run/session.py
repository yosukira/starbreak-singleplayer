"""Persistent seven-node EFB run; exploration variants do not change its route."""
from ..run_common.session import RunLiveSession
from ..run_common.actors import ActorSession
from .scene import EfbRunPack

class EfbRunLiveSession(RunLiveSession):
    MAP_ID='efb_run'
    PACK_CLASS=EfbRunPack

    def create_runtime(self,options,room):
        if self.instance.role in ('mid','boss'):
            from .encounters import create
            return create(self,options)
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

initial_world=EfbRunLiveSession.initial_world
world_blob=EfbRunLiveSession.world_blob
