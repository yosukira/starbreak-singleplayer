import collections
from ..dumb_wave.session import DumbWaveLiveSession
from ..dumb_common.doors import open_doors
from .scene import DumbNormalWavePack

class DumbNormalWaveLiveSession(DumbWaveLiveSession):
    MAP_ID='dumb_normal_wave'
    PACK_CLASS=DumbNormalWavePack

    def prepare(self):
        super().prepare();self.doors_open=False;self.roster=[]

    def _available_spawn_species(self,spec):
        return collections.Counter(self.roster)

    def _choose_spawn_species(self,spec,allowed=None):
        candidates=[n for n in self.roster if allowed is None or n in allowed]
        if allowed is None:
            exclusive=[n for n in candidates if n in ('heavy','sniper')]
            if exclusive:candidates=exclusive
        if not candidates:raise ValueError('Wave roster cannot fill this batch')
        name=self.rng.choice(candidates);self.roster.remove(name);return name

    def _controller_event(self,event):
        if event['kind']=='wave_start':
            counts=self.pack.waves['waves'][event['wave']-1]['species_counts']
            self.roster=[name for name,count in counts.items()for _ in range(count)]
            self.rng.shuffle(self.roster)
        super()._controller_event(event)
        if event['kind']=='complete':open_doors(self,event['at'])
