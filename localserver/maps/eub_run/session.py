"""Full EUB room graph, persistent exploration and native encounters."""
from ..run_common.session import RunLiveSession
from ..run_common.actors import ActorSession
from .scene import EubRunPack
from .route import EubLevelRun

class EubRunLiveSession(RunLiveSession):
    MAP_ID = 'eub_run'
    PACK_CLASS = EubRunPack
    RUN_CLASS = EubLevelRun

    @staticmethod
    def encounter_kind(instance):
        return instance.recipe['metadata'].get('encounter_kind') or (instance.role if instance.role in ('mid','boss','wave_chamber') else None)

    @classmethod
    def prepare_document(cls, run, instance, document):
        managed = set(instance.recipe['metadata'].get('encounter_managed_initial_ids', []))
        if managed:
            world = document['worldstate_']['serverworld_']
            world['obj_'] = [row for row in world['obj_'] if row['objid_'] not in managed]
        return document

    def create_runtime(self, options, room):
        kind=self.encounter_kind(self.instance)
        if kind in ('mid','boss'):
            from .encounters import create
            return create(self, options)
        from .enemies import ExplorationEnemies
        from .mechanics import ExplorationMechanics
        from .presentation import Presentation
        def enemies(host):
            result=ExplorationEnemies(host)
            if kind=='wave_chamber' and result.controller is None:
                from types import SimpleNamespace
                from ..dumb_wave.controller import DumbWaveController
                result.controller=DumbWaveController(SimpleNamespace(waves=host.pack.wave_rules,map=dict(id='eub_run')),host.instance.seed)
            return result
        return ActorSession(self.proxy,self.channel,options,self.instance,self.bridge,self.run.pack,
            map_id=self.MAP_ID,enemies_factory=enemies,
            mechanics_factory=ExplorationMechanics,presentation_factory=Presentation)

    def encounter_finished(self):
        kind=self.encounter_kind(self.instance)
        if kind in ('mid','boss'):
            from .encounters import finished
            return finished(self.current,kind)
        return False

initial_world = EubRunLiveSession.initial_world
world_blob = EubRunLiveSession.world_blob
