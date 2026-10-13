"""Fixed EST mid room, with fresh fauna and obstacle placement each run."""
import copy
import json
import random
from pathlib import Path

from ... import pb_layout
from ..jungle_common.scene import normalize, platform_slabs
from tools.make_create_world import unwrap_decompress, wrap_compress

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / 'maps/jungle_mid'
ROLLED_VIDS = {'jungle.stonepusher', 'jungle.pincer', 'jungle.hopperhive',
               'jungle.skitterup', 'jungle.skitterdown'}

class JungleMidPack:
    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT)
        for name in ('map', 'scene', 'templates', 'entities'):
            setattr(self, name, json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.dynamic = json.loads((self.root / 'dynamic-templates.json').read_text(encoding='utf8'))
        self.createworld_hex = str(self.root / 'createworld.hex')

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))

    def species_props(self, vid):
        props = self.dynamic.get(vid)
        if props is None:
            props = next((p for p in self.templates.values() if p.get('vid') == vid), None)
        return copy.deepcopy(props)

    def entrance_platforms(self):
        """Keep the spawn-door slab and its first adjacent step unoccupied."""
        door = self.templates[str(self.scene['spawn_id'])]
        center = door['xmp']+door['wmp']/2
        feet = door['ymp']+door['hmp']
        slabs = platform_slabs(self.templates)
        below = [p for p in slabs if p[0] <= center <= p[0]+p[2] and p[1] >= feet-256]
        if not below:
            return set()
        entry = min(below, key=lambda p: p[1])
        result = {entry}
        for slab in slabs:
            if (entry[0]+entry[2] <= slab[0] <= entry[0]+entry[2]+128*256
                    and 0 <= slab[1]-entry[1] <= 64*256):
                result.add(slab)
        return result

    def rolled_world(self, seed):
        rng = random.Random(int(seed))
        objects = {int(k): copy.deepcopy(v) for k, v in self.templates.items()
                   if v.get('vid') not in ROLLED_VIDS}

        layers = self.entities['layers']
        for props in objects.values():
            if props.get('vid') == 'jungle.ledge':
                props['z'] = layers['platform_z']
        self.protected_platforms = self.entrance_platforms()
        platforms = [dict(zip(('xmp', 'ymp', 'wmp', 'hmp'), slab))
                     for slab in platform_slabs(objects) if slab not in self.protected_platforms]

        wide = [p for p in platforms if p['wmp'] >= 32768]
        nid = max(map(int, self.templates)) + 1

        def add(vid, **changes):
            nonlocal nid
            oid = nid
            nid += 1
            props = self.species_props(vid) or {}
            props.update(vid=vid, **changes)
            objects[oid] = props
            return oid

        self.pincers, self.pushers, self.hives, self.skitters = [], [], [], []
        for plat in rng.sample(wide, rng.randint(1, 12)):
            x, y, w, h = (plat[k] for k in ('xmp', 'ymp', 'wmp', 'hmp'))
            left = add('jungle.pincer', xmp=x, ymp=y-34560, facing=0,
                       z=layers['mechanism_z'], pose=1, animprops=dict(animid=1, time=660))
            right = add('jungle.pincer', xmp=x+w-34560, ymp=y-34560, facing=180,
                        z=layers['mechanism_z'], pose=1, animprops=dict(animid=1, time=660))
            self.pincers.append(dict(left=left, right=right, platform=[x, y, w, h]))
        pusher_platforms = [p for p in platforms if p['wmp'] >= 40960]
        for plat in rng.sample(pusher_platforms, rng.randint(8, 27)):
            span = min(rng.randint(107, 140)*256, plat['wmp']-8192)
            lo = plat['xmp'] + rng.randint(0, max(0, plat['wmp']-8192-span))
            phase = rng.random()*2
            x = lo + span*(phase if phase <= 1 else 2-phase)
            oid = add('jungle.stonepusher', xmp=round(x), ymp=plat['ymp']-12288,
                      z=layers['mechanism_z'])
            self.pushers.append(dict(objid=oid, lo=lo, hi=lo+span, phase=phase))
        for plat in rng.sample(wide, 5):
            x = plat['xmp'] + rng.randint(0, plat['wmp']-24576)
            self.hives.append(add('jungle.hopperhive', xmp=x, ymp=plat['ymp']+plat['hmp'], hitpoints=1125))
        for plat in rng.sample(wide, rng.randint(6, 16)):
            self.skitters.append(add('jungle.skitterup', xmp=plat['xmp']+plat['wmp']//2-7680,
                                     ymp=plat['ymp']-12288, hitpoints=75))
        self.run_objects = objects
        message = pb_layout.default_layouts().decode('CreateWorld', unwrap_decompress(
            bytes.fromhex(Path(self.createworld_hex).read_text(encoding='ascii'))))
        message['worldstate_']['serverworld_']['obj_'] = [dict(objid_=oid, props_=self.wire_props(p))
                                                         for oid, p in objects.items()]
        return wrap_compress(pb_layout.default_layouts().encode('CreateWorld', message)).hex()
