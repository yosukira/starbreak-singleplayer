"""EFF local complete-run adapter; public registry is owned by the main host."""
from ..run_common.session import RunLiveSession
from ..run_common.actors import ActorSession
from .scene import EffRunPack

class EffRunLiveSession(RunLiveSession):
    MAP_ID='eff_run'
    PACK_CLASS=EffRunPack
    def create_runtime(self,options,room):
        if self.instance.role in ('mid','boss'):
            from .encounters import create
            return create(self,options)
        from .enemies import ExplorationEnemies
        from .mechanics import ExplorationMechanics
        from .presentation import Presentation
        return ActorSession(self.proxy,self.channel,options,self.instance,self.bridge,self.run.pack,
            map_id=self.MAP_ID,enemies_factory=ExplorationEnemies,mechanics_factory=ExplorationMechanics,presentation_factory=Presentation)
    def encounter_finished(self):
        if self.instance.role not in ('mid','boss'):return False
        from .encounters import finished
        return finished(self.current,self.instance.role)

initial_world=EffRunLiveSession.initial_world
world_blob=EffRunLiveSession.world_blob
