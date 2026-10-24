"""Monk motion and seeded occupancy of authored, clear native hall slots."""
import copy
import math
import random

from ..dumb_common.scene import normalize
from ..eub_run.generator import refs
from .physics import swept_shape


def position(home, elapsed, heading, *, bias=10., radius=200., radial_rate=1., angular_rate=10./7.):
    """Capture-fitted rose; the local activation phase starts at its anchor.

    Native moving snapshots do not identify a home. The caller supplies its
    authored local anchor, and preserves the epoch across room-clock absence.
    """
    if elapsed <= 0:
        return tuple(home)
    phase = math.asin(-bias/radius) if radius else 0.
    r = bias+radius*math.sin(elapsed*radial_rate+phase)
    angle = heading+elapsed*angular_rate
    # Native endpoint coordinates have whole-pixel truncation, including on
    # negative axes. Round only after translating into world coordinates.
    return tuple(math.trunc(home[i]/256+r*f)*256
                 for i, f in enumerate((math.cos(angle), math.sin(angle))))


class MonkPopulation:
    """FIT: 5/6/7/8 bodies, uniformly sampled from observed native columns.

    Source counts are examples, not recovered spawn probabilities. Keep a
    column empty and reject any slot whose complete 210px motion envelope
    intersects solid terrain or leaves the selected hall's bounds.
    """
    counts = (5, 6, 7, 8)

    def __init__(self, templates):
        self.slots = {}
        for recipe in templates:
            for props in recipe['objects'].values():
                if props.get('vid') == 'sc.monk':
                    self.slots.setdefault((props.get('xmp', 0), props.get('ymp', 0)),
                                          copy.deepcopy(props))
        self._legal = {}

    def legal_slots(self, recipe):
        objects = recipe['objects']
        terrain = tuple((p.get('xmp', 0), p.get('ymp', 0), p.get('wmp', 0), p.get('hmp', 0),
                         p.get('shapetype', 0)) for p in objects.values()
                        if p.get('terraintype') == 1)
        bounds = tuple(recipe['metadata']['bounds'])
        key = bounds, terrain
        if key not in self._legal:
            columns = {}
            margin = 210*256
            for (x, y), props in sorted(self.slots.items()):
                start = x-margin, y-margin
                size = props.get('wmp', 0)+2*margin, props.get('hmp', 0)+2*margin
                if (start[0] < bounds[0] or start[1] < bounds[1]
                        or start[0]+size[0] > bounds[2] or start[1]+size[1] > bounds[3]):
                    continue
                if any(swept_shape(row[:4], row[4], start, start, size,
                                   penetrating=True, tolerance=1.) is not None for row in terrain):
                    continue
                columns.setdefault(x, []).append((x, y))
            self._legal[key] = columns
        return self._legal[key]

    def populate(self, recipe, seed):
        if recipe['metadata'].get('template') != 'scmid04':
            return recipe
        columns = self.legal_slots(recipe)
        choices = [count for count in self.counts if count < len(columns)]
        if not choices:
            return recipe
        rng = random.Random('sc-monk-population-v1:'+str(int(seed)))
        count = rng.choice(choices)
        points = [rng.choice(columns[x]) for x in sorted(rng.sample(sorted(columns), count))]
        objects, creators = recipe['objects'], recipe['creators']
        old = {oid for oid, props in objects.items() if props.get('vid') == 'sc.monk'}
        # Fresh native hall templates contain bodies, not prefilled orb rings.
        # A future linked template must compile an assembly before it can use
        # this body-only pool; do not leave dangling references behind.
        if any(old.intersection(refs(props)) for oid, props in objects.items() if oid not in old):
            raise ValueError('SC monk population requires unlinked fresh hall bodies')
        if any(int(parent) in old for oid, parent in creators.items() if oid not in old):
            raise ValueError('SC monk population has an unsupported child assembly')
        next_id = max(objects, default=0)+1
        for oid in old:
            objects.pop(oid)
            creators.pop(oid, None)
        selected = []
        for offset, point in enumerate(points):
            oid = next_id+offset
            objects[oid] = copy.deepcopy(self.slots[point])
            selected.append(dict(id=oid, at=list(point)))
        recipe['metadata']['sc_monk_population'] = dict(
            policy='fit-native-slots-v1', count=count, count_pool=choices,
            legal_columns=len(columns), selected=selected)
        server = recipe['world']['worldstate_']['serverworld_']
        server['obj_'] = [row for row in server.get('obj_', []) if row['objid_'] not in old]
        server['obj_'].extend(dict(objid_=item['id'], props_=normalize('Properties', objects[item['id']]))
                              for item in selected)
        return recipe
