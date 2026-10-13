"""SC Boss pack loader and protobuf normalization; no encounter dependencies."""
import copy
import json
from pathlib import Path
from ... import pb_layout

DEFAULT_ROOT=Path(__file__).resolve().parents[3]/'maps/sc_boss'

def normalize_props(type_name, value, layouts=None):
    layouts=layouts or pb_layout.default_layouts()
    if isinstance(value,dict) and 'value' in value and 'name' in value:
        return value['value']
    if not isinstance(value,dict):
        return value
    fields={f['name']:f for f in layouts.types.get(type_name,{}).get('fields',[])}
    result={}
    for key,item in value.items():
        name=key if key in fields else key+'_'
        if name not in fields:
            raise ValueError('unknown %s field: %s'%(type_name,key))
        child=fields[name].get('item')
        if child:
            item=([normalize_props(child,r,layouts) for r in item] if isinstance(item,list)
                  else normalize_props(child,item,layouts))
        result[name]=item
    return result

class ScBossPack:
    def __init__(self, root=None):
        self.root=Path(root or DEFAULT_ROOT)
        for name in ('scene','entities','templates','dynamic-templates','programs'):
            setattr(self,name.replace('-','_'),json.loads((self.root/(name+'.json')).read_text(encoding='utf8')))
        self.squid_calibration=json.loads((self.root/'squid-calibration.json').read_text(encoding='utf8'))

        extra=self.root/'fine-directions.json'
        self.fine_directions=(json.loads(extra.read_text(encoding='utf8'))
                              if extra.exists() else {})
        extra=self.root/'spawn-patterns.json'
        self.spawn_patterns=(json.loads(extra.read_text(encoding='utf8')) if extra.exists() else {})
        extra=self.root/'final-portals.json'
        self.final_portals=(json.loads(extra.read_text(encoding='utf8')) if extra.exists() else {})

        from .choreography import snapshot_geometry,mode_timing
        self._layout_cache={};self._timing_cache={}
        for modes in self.programs.get('slot_payloads',{}).values():
            for mode,windows in modes.items():
                for ci,lo,hi in windows:
                    course=self.programs['courses'][ci];slot=dict(start=lo,end=hi,mode=mode)
                    self._layout_cache[(id(course),lo,hi,mode)]=snapshot_geometry(course,slot,mode)
                    self._timing_cache[(ci,lo,hi,mode)]=mode_timing(course,slot,self.programs['courses'])
        from .kinematics import slot_kinematics
        for index,modes in self.programs.get('slot_payloads',{}).items():
            for mode,windows in modes.items():
                for ci,lo,hi in windows:
                    slot_kinematics(self,dict(slot=int(index),course=ci,start=lo,end=hi,mode=mode))
        self.createworld_hex=str(self.root/'createworld.hex')

    def wire_props(self, identity):
        source=(self.templates[str(identity)] if isinstance(identity,int) else self.dynamic_templates[identity])
        return normalize_props('Properties',copy.deepcopy(source))
