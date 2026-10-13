"""Jungle boss content pack; no other map's AI is imported."""
import copy
import json
import random
import struct
from pathlib import Path

from ... import lz4block, pb_layout
from ..jungle_common.scene import normalize, strip_underscores, motion_position

try:
    from tools.make_create_world import wrap_compress
except Exception:
    wrap_compress = None

ROOM_VIDS = ('jungle.pincer', 'jungle.hopperhive',
             'jungle.skitterup', 'jungle.skitterdown',
             'jungle.snakehead', 'jungle.snakebody', 'jungle.snaketail')

DEFAULT_ROOT = Path(__file__).resolve().parents[3] / 'maps/jungle_boss'
VELOCITY_TO_MP_S = 1000. / 256.

class JungleBossPack:
    def __init__(self, root=None):
        self.root = Path(root or DEFAULT_ROOT)
        for name in ('map', 'scene', 'templates', 'entities', 'programs'):
            setattr(self, name, json.loads((self.root / (name + '.json')).read_text(encoding='utf8')))
        self.createworld_hex = str(self.root / 'createworld.hex')

    def wire_props(self, props):
        return normalize('Properties', copy.deepcopy(props))

    def boss(self):
        return self.scene['boss']

    def chain_offsets(self):
        return {int(k): tuple(v) for k, v in self.scene['chain_offsets_mp'].items()}

    def species_props(self, vid):
        'runtime template props for one minion species (hp, sizes, damage).'
        for props in self.templates.values():
            if props.get('vid') == vid:
                return copy.deepcopy(props)
        return None

    @staticmethod
    def hive_positions(platform,rng):
        from . import params
        px,py,pw,ph=platform
        width=24576
        maximum=min(params.HIVE_COUNT_PER_PLATFORM[1],int(pw//width))
        count=rng.randint(params.HIVE_COUNT_PER_PLATFORM[0],max(1,maximum))
        spare=max(0,pw-count*width)
        offsets=sorted(rng.uniform(0,spare)for _ in range(count))
        return [(int(px+i*width+offset),int(py+ph))for i,offset in enumerate(offsets)]

    def rolled_world(self, seed):
        """Roll this run's room *inside* the CreateWorld payload.

        The retail server picks the hives/skitters/eye worms/pincers while it
        builds the room, so the client never sees a placeholder being
        destroyed - and the pincers keep the creation order that draws them
        under the boss.  Falls back to (None, None) when the blob cannot be
        rebuilt, and the session then uses the old runtime path.
        """
        if wrap_compress is None:
            return None
        try:
            blob = bytes.fromhex(Path(self.createworld_hex).read_text(encoding='ascii').strip())
            size = struct.unpack('>I', blob[:4])[0]
            raw = lz4block.decompress(blob[4:], size)
            message = pb_layout.default_layouts().decode('CreateWorld', raw)
            world = (message.get('worldstate_') or {}).get('serverworld_') or {}
            items = list(world.get('obj_') or [])
        except Exception:
            return None
        templates, keep, chains = {}, [], []
        recorded_worms = {int(w['head']): w for w in (self.scene.get('initial_worms') or [])}
        worm_links = {}
        for worm in recorded_worms.values():
            chain = [int(worm['head'])] + [int(v) for v in worm['body']] + [int(worm['tail'])]
            for oid in chain:
                worm_links[oid] = chain
        for item in items:
            props = dict(item.get('props_') or {})
            vid = props.get('vid_')
            if vid not in ROOM_VIDS:
                keep.append(item)
                continue
            templates.setdefault(vid, props)
        if not templates:
            return None
        rng = random.Random(int(seed))
        message.setdefault('worldprops_', {})['seed_'] = int(seed)
        nid = max((int(i.get('objid_') or 0) for i in items), default=0) + 1
        extra, pairs, hives, skitters = [], [], [], []

        def add(vid, props):
            nonlocal nid
            oid = nid
            nid += 1
            body = dict(templates.get(vid) or {})
            body.update(props)
            body['vid_'] = vid
            extra.append({'objid_': oid, 'props_': body})
            return oid

        platforms = []
        for props in self.templates.values():
            if props.get('vid') == 'jungle.ledge':
                platforms.append((props.get('xmp', 0), props.get('ymp', 0),
                                  props.get('wmp', 0), props.get('hmp', 0)))
        platforms = sorted(set(platforms), key=lambda p: (p[1], p[0]))
        if not platforms:
            return None

        from . import params
        lo, hi = (int(v) for v in params.PINCER_COUNT_RANGE)
        for platform in rng.sample(platforms, rng.randint(lo, min(hi, len(platforms)))):
            px, py, pw, _ph = platform
            width = int(templates['jungle.pincer'].get('wmp_') or params.PINCER_SPRITE_W_MP)
            height = int(templates['jungle.pincer'].get('hmp_') or params.PINCER_SPRITE_H_MP)
            left = add('jungle.pincer', dict(xmp_=int(px), ymp_=int(py - height),
                                             z_=params.PINCER_Z,
                                             facing_=params.PINCER_LEFT_FACING))
            right = add('jungle.pincer', dict(xmp_=int(px + pw - width),
                                              ymp_=int(py - height),
                                              z_=params.PINCER_Z,
                                              facing_=params.PINCER_RIGHT_FACING))
            pairs.append(dict(left=left, right=right, platform=[int(v) for v in platform],
                              center_x=(px + px + pw - width) / 2.,
                              y=py - height, gap=pw - width))

        hive_h = int(templates['jungle.hopperhive'].get('hmp_') or 16384)
        for platform in rng.sample(platforms, rng.randint(
                int(params.HIVE_PLATFORM_RANGE[0]),
                min(int(params.HIVE_PLATFORM_RANGE[1]), len(platforms)))):
            for hx,hy in self.hive_positions(platform,rng):
                oid = add('jungle.hopperhive', dict(xmp_=hx, ymp_=hy,
                                                    hitpoints_=params.HIVE_HP))
                hives.append(oid)

        count = hives and rng.randint(int(params.SKITTER_INIT_RANGE[0]),
                                      min(int(params.SKITTER_INIT_RANGE[1]), len(platforms)))
        for platform in rng.sample(platforms, count or 0):
            px, py, pw, _ph = platform
            oid = add('jungle.skitterup', dict(xmp_=int(px + pw/2 - 7680),
                                               ymp_=int(py - 12288),
                                               hitpoints_=params.SKITTER_HP))
            skitters.append(oid)

        slots = list(params.SNAKE_SLOT_MP)
        rng.shuffle(slots)
        worms = []
        for x, y in slots[:rng.randint(int(params.SNAKE_INIT_RANGE[0]),
                                       min(int(params.SNAKE_INIT_RANGE[1]), len(slots)))]:
            head = add('jungle.snakehead', dict(xmp_=int(x), ymp_=int(y)))
            body = []
            parent = head
            for _ in range(int(params.SNAKE_BODY_COUNT)):
                oid = add('jungle.snakebody', dict(
                    xmp_=int(x), ymp_=int(y),
                    move_={'relativeto': {'objid': parent},
                           'follow': {'maxdist': int(params.SNAKE_LINK_MP / 256)}}))
                body.append(oid)
                parent = oid
            tail = add('jungle.snaketail', dict(
                xmp_=int(x), ymp_=int(y),
                move_={'relativeto': {'objid': parent},
                       'follow': {'maxdist': int(params.SNAKE_LINK_MP / 256)}}))
            worms.append(dict(head=head, body=body, tail=tail))
        world['obj_'] = keep + extra
        raw = pb_layout.default_layouts().encode('CreateWorld', message)
        blob = wrap_compress(raw)
        self.run_objects = {}
        for item in world['obj_']:
            props = strip_underscores(dict(item.get('props_') or {}))
            self.run_objects[int(item['objid_'])] = props
        self.run_pincers = pairs
        self.run_hives = hives
        self.run_skitters = skitters
        self.run_worms = worms
        return blob.hex()
