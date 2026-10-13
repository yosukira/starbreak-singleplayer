"""Native Patriarch map assets, independent of recordings and other map AI."""
import base64
import copy
import json
from pathlib import Path
from ... import pb_layout

ROOT = Path(__file__).resolve().parents[3]/'maps/sc_patriarch'

def normalize(kind, value, layouts=None):
    layouts = layouts or pb_layout.default_layouts()
    if not isinstance(value, dict):
        return value
    fields = {f['name']:f for f in layouts.types[kind]['fields']}
    result = {}
    for key,item in value.items():
        name = key if key in fields else key+'_'
        if name not in fields:
            raise ValueError('unknown %s field %s' % (kind,key))
        child = fields[name].get('item')
        if child:
            item = ([normalize(child,row,layouts) for row in item] if isinstance(item,list)
                    else normalize(child,item,layouts))
        result[name] = item
    return result

def clean(value):
    if isinstance(value,list):return [clean(v) for v in value]
    if not isinstance(value,dict):return value
    if 'raw' in value:return base64.b64decode(value['raw']).decode('utf8')
    return {k.rstrip('_'):clean(v) for k,v in value.items()}

class PatriarchPack:
    def __init__(self,root=None):
        self.root=Path(root or ROOT)
        for name in ('map','scene','templates','dynamic-templates','entities'):
            setattr(self,name.replace('-','_'),json.loads((self.root/(name+'.json')).read_text(encoding='utf8')))
        self.createworld_hex=str(self.root/'createworld.hex')
        self.solids=[tuple(p.get(k,0)/256 for k in ('xmp','ymp','wmp','hmp'))
                     for p in self.templates.values() if p.get('terraintype')==1 and p.get('vid')=='sc.wall']

    def props(self,vid):return copy.deepcopy(self.dynamic_templates[vid])
    def wire_props(self,props):return normalize('Properties',copy.deepcopy(props))
