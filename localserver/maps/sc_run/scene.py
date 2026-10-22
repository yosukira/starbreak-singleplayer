"""Full SC pack: authored rooms, seeded connections and original art rules."""
import copy
from .catalogue import ScContent
from .layout import generate, stable_seed

class ScRunPack:
    def __init__(self, root=None, *, exploration_rooms=None, layout_fit=None):
        self.content=ScContent(root)
        self.root=self.content.root
        self.dynamic_templates=self.content.dynamic_templates
        self.dynamic_template_variants=self.content.dynamic_template_variants
        self.plan=None
        self.exploration_rooms=exploration_rooms
        self.layout_fit=copy.deepcopy(layout_fit or {})

    def configure(self,seed):
        self.plan=generate(self.content,seed,self.exploration_rooms,fit=self.layout_fit)
        return self

    @property
    def room_names(self):
        if self.plan is None:raise RuntimeError('SC run seed not configured')
        return tuple(self.plan['rooms'])

    def room(self,role,seed=0):
        if self.plan is None:self.configure(seed)
        recipe=copy.deepcopy(self.plan['rooms'][role])
        props=recipe['world']['worldprops_']
        props.update(seed_=int(seed)&0x7fffffff, zoneid_=int(self.plan['seed'])&0xffffffff,
                     gameid_=stable_seed(self.plan['seed'],role))
        # One room still owns its original decoration, scattering, fill and
        # external tile rules. SC is not given a generic wall-thickening pass.
        if role!='boss':props.update(zone_='sc',music_='sc')
        # The room descriptor is scboss; the original audio file is sc_boss.
        if role=='boss':
            props['music_']='sc_boss'
            for oid,p in recipe['objects'].items():
                if p.get('vid')=='lobbyportal':
                    recipe['links'][oid]=dict(destination='home',external=True,
                                             trigger='interact',requires='encounter:boss')
        return recipe

    def props(self,vid):return self.content.props(vid)

