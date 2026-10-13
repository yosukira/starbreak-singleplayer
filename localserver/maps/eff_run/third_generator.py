'Local battle simulator runtime.'
import copy
import hashlib
import json
from pathlib import Path
import random

def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()

class EffThirdGenerator:
    def __init__(self,root):
        self.root=Path(root)
        self.data=json.loads((self.root/'fixed-third/modes.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.data.items() if k!='content_version'})!=self.data['content_version']:
            raise ValueError('EFF third-mode pool fingerprint mismatch')
        self.modes={m['key']:m for m in self.data['modes']}
        self.adapter_version=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

    def generate(self,base,seed,mode=None):
        if base['role']!='rock_ascent':raise ValueError('EFF third selector only accepts rock_ascent')
        explicit=mode is not None
        if mode is None:mode=random.Random('eff-fixed-third-v1:'+str(int(seed)&0x7fffffff)).choice(list(self.modes))
        if mode not in self.modes:raise ValueError('Unknown EFF third fixed mode: '+str(mode))
        selected=self.modes[mode]
        path=self.root/selected['recipe_file'];raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=selected['recipe_sha256']:
            raise ValueError('EFF third native recipe fingerprint mismatch')
        room=json.loads(raw.decode('utf8'))
        for field in ('objects','creators','links','gate_pairs'):
            room[field]={int(k):v for k,v in room.get(field,{}).items()}
        if room['role']!=base['role']:raise ValueError('EFF third prefab changed route role')
        previous=base['metadata'].get('generation',{})
        components=dict(ordinary_modules=previous.get('content_version'),fixed_modes=self.data['content_version'],
                        selector=self.adapter_version,native_recipe=selected['recipe_sha256'])
        room['metadata']['fixed_third_mode']=dict(key=mode,source_objects=selected['source_objects'],
            rockspawners=selected['rockspawners'],confirmed_original=True,
            selection='explicit fixed-mode diagnostic' if explicit else 'SIM deterministic equal choice; original probabilities unknown',
            initialization=selected['initialization'],source=copy.deepcopy(selected['source']))
        room['metadata']['generation']=dict(mode='confirmed-fixed-mode-selection',seed=int(seed)&0x7fffffff,
            fixed_mode=mode,mode_count=len(self.modes),geometry_changed=mode!='two-spawners',
            content_version=digest(components),component_versions=components,
            evidence='D complete native CW prefabs + USER original-server confirmation; selection SIM',
            limits='Fixed third-room choice, not exploration module assembly. Oct6 late Init absent; no later rocks baked into initial room.')
        return room
