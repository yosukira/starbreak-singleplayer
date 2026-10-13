"""Runtime content pack: parameter data only, no recording reads."""
import copy
import json
from pathlib import Path
from ..dumb_common.scene import normalize

ROOT=Path(__file__).resolve().parents[3]/'maps/dumb_wave'

class DumbWavePack:
    def __init__(self,root=None):
        self.root=Path(root or ROOT)
        for name in ('map','scene','templates','waves','species','archetypes','effects'):
            setattr(self,name,json.loads((self.root/(name+'.json')).read_text(encoding='utf8')))
        self.animations=json.loads((self.root/'animation-points.json').read_text())['vids']
        self.createworld_hex=str(self.root/'createworld.hex')

    def wire_props(self,props):return normalize('Properties',copy.deepcopy(props))
    def species_props(self,name):return copy.deepcopy(self.species.get(name))
    def projectile_props(self,name):return copy.deepcopy(self.archetypes.get('dumb.'+name,{}))

    def emit_points(self,vid,slot,position,size,facing=0):
        table=self.animations.get(vid,[])
        if not 0<=slot<len(table):raise ValueError('invalid animation %s/%s'%(vid,slot))
        x,y=position;w,h=size;flip=-1 if facing==180 else 1
        for point in table[slot]['points']:
            yield dict(x=x+w/2+flip*point['x']*256,y=y+h+point['y']*256,
                angle=point['angle']if flip==1 else (540-point['angle'])%360,time=point['time'])
