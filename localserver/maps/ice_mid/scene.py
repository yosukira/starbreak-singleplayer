"""Seeded original platform-module grammar, shared by wire and simulation."""
import copy
import json
import random
from pathlib import Path
from ... import pb_layout
from ..ice_boss.scene import normalize
from tools.make_create_world import unwrap_decompress, wrap_compress

ROOT = Path(__file__).resolve().parents[3] / 'maps/ice_mid'

def generate(templates, seed):
    rng = random.Random(seed)
    out = {k: copy.deepcopy(templates[k]) for k in ('9', '10', '37', '38', '39')}

    widths = rng.choice(([288, 288, 128], [288, 288, 288], [288]*4))
    x = 832
    oid = 100

    def terrain(vid, x, y, w, h):
        nonlocal oid
        oid += 1
        wall = vid == 'ice.wall'
        out[str(oid)] = dict(vid=vid, xmp=x*256, ymp=y*256, wmp=w*256, hmp=h*256,
                            terraintype=1 if wall else 2, draw=True, z=60,
                            isinternal=False if wall else True, boundsworld=wall)

    terrain('ice.wall', 384, 0, 64, 1344)
    terrain('ice.ledge', 800, 1120, 32, 32)
    for y in (480, 640, 800, 960):
        terrain('ice.ledge', 448, y, 384, 32)
    omitted = []
    for w in widths:
        gap = rng.choice((480, 640, 800, 960, 1120))

        if omitted and omitted[-1] == gap:
            gap = rng.choice([y for y in (480, 640, 800, 960, 1120) if y != gap])
        omitted.append(gap)
        for y in (480, 640, 800, 960, 1120):
            if y != gap:
                terrain('ice.ledge', x, y, w, 32)
        x += w
    right = x + 320
    for y in (480, 640, 800, 960, 1120):
        terrain('ice.ledge', x, y, 320, 32)
    terrain('ice.wall', 448, 1280, right-448, 64)
    terrain('ice.wall', 448, 0, right+384-448, 64)
    terrain('ice.wall', right, 448, 64, 896)
    terrain('ice.wall', right+64, 448, 320, 64)
    terrain('ice.wall', right+320, 64, 64, 384)
    out['37'].update(xmp=(right+160)*256, ymp=224*256, interact=0)
    out['38'].update(xmp=(right+172)*256, ymp=448*256)
    out['39'].update(xmp=(right-256)*256, ymp=816*256, pose=4,
                     hitpoints=1000, removetype=0, soundprefix='ice.crossbeast')
    return out, dict(left=448, right=right, floor=1280, top=64, boss_id=39,
                     spawn_id=10, exit_id=37, rows=[480, 640, 800, 960, 1120],
                     middle_widths=widths, missing_rows=omitted, seed=seed)

class IceMidPack:
    def __init__(self, root=None, seed=0):
        self.root = Path(root or ROOT)
        for name in ('map', 'templates', 'dynamic-templates', 'entities'):
            setattr(self, name.replace('-', '_'), json.loads((self.root/(name+'.json')).read_text(encoding='utf8')))
        self.templates, self.scene = generate(self.templates, 0 if seed is None else int(seed))
        self.solids = [self.rect(p) for p in self.templates.values() if p.get('terraintype') == 1]
        self.ledges = [self.rect(p) for p in self.templates.values() if p.get('terraintype') == 2]
        self.surfaces = self.solids + self.ledges

        self.runs = {}
        for x, y, w, h in self.surfaces:
            if y in self.scene['rows'] or y == 1280:
                self.runs.setdefault(y, []).append((x, x+w))
        for y, spans in self.runs.items():
            merged = []
            for a, b in sorted(spans):
                if merged and a <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
                else:
                    merged.append((a, b))
            self.runs[y] = merged

    @staticmethod
    def rect(p):
        return tuple(p.get(k, 0)/256 for k in ('xmp', 'ymp', 'wmp', 'hmp'))

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))

def roll_create_world(blob, seed):
    pack = IceMidPack(seed=seed)
    layouts = pb_layout.default_layouts()
    msg = layouts.decode('CreateWorld', unwrap_decompress(blob))
    msg['worldprops_']['seed_'] = int(seed)
    msg['worldstate_']['serverworld_']['obj_'] = [dict(objid_=int(i), props_=pack.wire_props(p))
                                               for i, p in pack.templates.items()]
    return wrap_compress(layouts.encode('CreateWorld', msg))
