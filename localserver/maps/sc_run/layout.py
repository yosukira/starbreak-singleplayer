"""Seeded SC topology of whole authored rooms and their real door ports.

The default 14 exploration rooms and 25 main rooms are a configurable local
FIT budget. They do not establish a retail room count or generator policy.
"""
import hashlib
from collections import Counter

MIDS = ('sctask01', 'sctask02', 'sctask03', 'sctask04')
ENCOUNTERS = dict(sctask01='patriarch', sctask02='jump', sctask03='bonesaw', sctask04='hive')
FIXED = ('scmid01', 'scmid02', 'scmid03', 'scmid04', 'scmid05', 'scmid11', 'scmid17', 'scmid18')
THREE_PORT = ('scmid13', 'scmid14', 'scmid15', 'scmid16')
ORDINARY = ('scmid06', 'scmid07', 'scmid08', 'scmid09', 'scmid10', 'scmid12')
CHESTS = tuple('scend%02d' % i for i in range(1, 7))
DEFAULT_LAYOUT_FIT = dict(exploration_rooms=14, world_map_gap_px=1024)


def stable_seed(seed, name):
    return int.from_bytes(hashlib.sha256(('%s:%s' % (seed, name)).encode()).digest()[:4], 'big') & 0x7fffffff


def bounds(recipe):
    value = recipe['metadata'].get('bounds')
    authored = None
    if isinstance(value, dict):
        authored = value['x'], value['y'], value['x']+value['w'], value['y']+value['h']
    elif value and len(value) == 4:
        authored = tuple(value)
    rows = [p for p in recipe['objects'].values() if p.get('terraintype') in (1, 2)] or list(recipe['objects'].values())
    actual = (min(p.get('xmp', 0) for p in rows), min(p.get('ymp', 0) for p in rows),
              max(p.get('xmp', 0)+p.get('wmp', 0) for p in rows), max(p.get('ymp', 0)+p.get('hmp', 0) for p in rows))
    # Playable-area metadata can omit outer walls. The world overview must
    # reserve the complete terrain, including the canonical Patriarch border.
    if authored:
        return min(authored[0],actual[0]), min(authored[1],actual[1]), max(authored[2],actual[2]), max(authored[3],actual[3])
    return actual


def ports(recipe):
    result = []
    for source in recipe['metadata'].get('ports', []):
        p = dict(source)
        p['id'] = int(p.get('id', p.get('objid', 0)))
        props = recipe['objects'][p['id']]
        p.setdefault('at', [props.get('xmp', 0)+props.get('wmp', 0)/2, props.get('ymp', 0)+props.get('hmp', 0)/2])
        p.setdefault('entry', 'door:'+str(p['id']))
        result.append(p)
    return result


def _is_core(recipe, oid):
    return 'coredoor' in recipe['objects'][oid].get('vid', '')


def four_way_sides(recipe):
    ordinary = [p for p in ports(recipe) if p['kind'] != 'core']
    sides = {side: sorted((p for p in ordinary if p['side'] == side),
                          key=lambda p: p['at'][1]) for side in ('left', 'right')}
    return sides if len(ordinary) == 4 and all(len(ps) == 2 for ps in sides.values()) else {}


def _choose(recipe, available, direction, core=None):
    candidates = [p for p in ports(recipe) if p['id'] in available
                  and (core is None or _is_core(recipe, p['id']) == core)]
    if not candidates:
        raise ValueError('No unused SC port of requested kind in '+recipe['role'])
    x0, y0, x1, y1 = bounds(recipe)
    cx, cy = (x0+x1)/2, (y0+y1)/2
    dx, dy = direction
    return max(candidates, key=lambda p: ((p['at'][0]-cx)/max(1, x1-x0)*dx +
                                         (p['at'][1]-cy)/max(1, y1-y0)*dy, -p['id']))


def _fit(content, exploration_rooms, requested):
    config = dict(DEFAULT_LAYOUT_FIT)
    config.update(getattr(content, 'manifest', {}).get('local_layout_fit', {}))
    config.update(requested or {})
    if exploration_rooms is not None:
        config['exploration_rooms'] = exploration_rooms
    n = config['exploration_rooms']
    if type(n) is not int or not 13 <= n <= len(FIXED)+len(THREE_PORT)+len(ORDINARY):
        raise ValueError('SC budget must cover fixed rooms and a balanced opposite-side Loop')
    if config['world_map_gap_px'] <= 0:
        raise ValueError('SC room spacing must be positive')
    return config


def _guardian(recipe):
    meta, objects = recipe['metadata'], recipe['objects']
    template = meta['template']
    meta.pop('chest_guardian', None)
    if template == 'scend02':
        tanks = [(i, p) for i, p in objects.items() if p.get('vid') == 'sc.tank']
        if not tanks:
            raise ValueError('SC tank tower has no authored tanks')
        highest = min(p.get('ymp', 0) for _, p in tanks)
        guards = [i for i, p in tanks if p.get('ymp', 0) == highest]
        if len(guards) != 1:
            raise ValueError('SC tank tower must have one highest tank')
        meta.pop('chest_reward_unresolved', None)
        meta.pop('guardian_candidates', None)
        meta['chest_guardian'] = guards[0]
        meta['chest_guardian_evidence'] = 'USER highest authored tank triggers Chest'
        return
    guards = [i for i, p in objects.items() if p.get('vid') == 'sc.egolem']
    if template == 'scend06':
        guards = [i for i, p in objects.items() if p.get('vid') == 'sc.tank']
        meta['chest_guardian_evidence'] = 'FIT unique captured tank; dedicated guardian rule not established'
    else:
        meta['chest_guardian_evidence'] = 'Observed unique authored sc.egolem'
    if len(guards) != 1:
        raise ValueError('SC Chest needs a unique authored guard or explicit unresolved metadata')
    meta['chest_guardian'] = guards[0]


def generate(content, seed, exploration_rooms=None, *, fit=None):
    from .directed import generate as connect_authored_doors
    return connect_authored_doors(content, seed, exploration_rooms, fit=fit)


def embed(plan):
    """Reserve true room bounds in separate columns/rows, preserving aspect."""
    widths, heights = {}, {}
    for role, r in plan['rooms'].items():
        col, row = plan['cells'][role]; x0, y0, x1, y1 = bounds(r)
        widths[col] = max(widths.get(col, 0), x1-x0)
        heights[row] = max(heights.get(row, 0), y1-y0)
    gap = plan.get('local_layout_fit', DEFAULT_LAYOUT_FIT)['world_map_gap_px']*256
    def centers(sizes):
        result, at = {}, 0
        for index in sorted(sizes):
            result[index] = at+sizes[index]/2; at += sizes[index]+gap
        return result
    xs, ys = centers(widths), centers(heights)
    for role, r in plan['rooms'].items():
        col, row = plan['cells'][role]; x0, y0, x1, y1 = bounds(r)
        offset = (xs[col]-(x0+x1)/2, ys[row]-(y0+y1)/2)
        r['metadata']['world_map_offset_mp'] = list(offset)
        r['metadata']['world_map_bounds_mp'] = [x0+offset[0], y0+offset[1], x1+offset[0], y1+offset[1]]


def _port_rotation(recipe):
    x0, y0, x1, y1 = bounds(recipe)
    def key(p):
        x, y = p['at']; side = p.get('side')
        if side not in ('top', 'right', 'bottom', 'left'):
            side = min(('top', 'right', 'bottom', 'left'), key=lambda s:
                       dict(top=abs(y-y0), right=abs(x-x1), bottom=abs(y-y1), left=abs(x-x0))[s])
        return dict(top=(0, x), right=(1, y), bottom=(2, -x), left=(3, -y))[side]
    return [p['id'] for p in sorted(ports(recipe), key=key)]


def validate(plan):
    rooms = plan['rooms']; counts = Counter(r['metadata']['template'] for r in rooms.values())
    if any(counts[d] != 1 for d in FIXED+MIDS+CHESTS):
        raise ValueError('SC needs each required fixed, Mid and Chest type exactly once')
    used, darts, rotation, four_way_edges = set(), {}, {}, {}
    for role, r in rooms.items():
        order = _port_rotation(r)
        for index, oid in enumerate(order):
            rotation[(role, oid)] = (role, order[(index+1) % len(order)])
    for e in plan['edges']:
        a, b = e['a'], e['b']
        if four_way_sides(rooms[a]) and four_way_sides(rooms[b]):
            key = tuple(sorted((a, b)))
            four_way_edges.setdefault(key, []).append(e)
        for src, dst, oid, other in ((e['a'], e['b'], e['a_port'], e['b_port']), (e['b'], e['a'], e['b_port'], e['a_port'])):
            if (src, oid) in used:
                raise ValueError('SC authored port is connected twice')
            used.add((src, oid)); darts[(src, oid)] = (dst, other)
            link = rooms[src]['links'][oid]
            arrival = next(p for p in ports(rooms[dst]) if p['id'] == other)
            if link['destination'] != dst or link['entry'] != arrival['entry'] or link['entry'] not in rooms[dst]['entries']:
                raise ValueError('SC door has no reciprocal authored arrival')
            departure = next(p for p in ports(rooms[src]) if p['id'] == oid)
            if departure['kind']=='core' or arrival['kind']=='core':
                if departure['kind']!=arrival['kind']:
                    raise ValueError('SC ordinary door cannot lead to a Gen portal')
            elif {departure['side'],arrival['side']}!={'left','right'}:
                raise ValueError('SC ordinary doors must have opposite authored sides')
            elif departure['side']=='right':
                if rooms[src]['metadata']['world_map_bounds_mp'][2] >= rooms[dst]['metadata']['world_map_bounds_mp'][0]:
                    raise ValueError('SC right door destination must be spatially to its right')
    for (a, b), edges in four_way_edges.items():
        pairs = [(e['a_port'], e['b_port']) if e['a'] == a else (e['b_port'], e['a_port'])
                 for e in edges]
        side = next(p['side'] for p in ports(rooms[a]) if p['id'] == pairs[0][0])
        opposite = 'left' if side == 'right' else 'right'
        expected = {(p['id'], q['id']) for p, q in
                    zip(four_way_sides(rooms[a])[side], four_way_sides(rooms[b])[opposite])}
        if len(pairs) != 2 or set(pairs) != expected:
            raise ValueError('SC adjacent four-way rooms must connect both upper/lower doors in order')
    if used != {(role, p['id']) for role, r in rooms.items() for p in ports(r)}:
        raise ValueError('SC authored ports remain unconnected')
    reached, stack = {'entry'}, ['entry']
    while stack:
        role = stack.pop()
        for link in rooms[role]['links'].values():
            if link['destination'] not in reached:
                reached.add(link['destination']); stack.append(link['destination'])
    if set(rooms)-reached != {'boss'} or len(plan['edges'])-len(reached)+1 != 1:
        raise ValueError('SC requires a connected ordinary network with one cycle')
    for role, r in rooms.items():
        if r['metadata'].get('encounter_kind') != 'gen':
            continue
        if len(r['links']) != 1:
            raise ValueError('SC Gen must have one dedicated Mid door')
        mid = rooms[next(iter(r['links'].values()))['destination']]
        forward = [(oid, link) for oid, link in mid['links'].items() if link['destination'] == role]
        if not mid['role'].startswith('mid_') or len(forward) != 1 or forward[0][1].get('requires') != 'mid_cleared' or not _is_core(mid, forward[0][0]):
            raise ValueError('SC Gen must use its Mid core door after completion')
    faces, visited = 0, set()
    for start in darts:
        if start in visited:
            continue
        faces += 1; dart = start
        while dart not in visited:
            visited.add(dart); dart = rotation[darts[dart]]
    if len(reached)-len(plan['edges'])+faces != 2:
        raise ValueError('SC authored port rotation is not planar')
    plan['topology'].update(cycle_rank=1, port_rotation_faces=faces, port_rotation_genus=0)
    rows = list(rooms.items())
    for index, (name, a) in enumerate(rows):
        ax, ay, bx, by = a['metadata']['world_map_bounds_mp']
        for other, b in rows[index+1:]:
            cx, cy, dx, dy = b['metadata']['world_map_bounds_mp']
            if min(bx, dx) > max(ax, cx) and min(by, dy) > max(ay, cy):
                raise ValueError('SC room bounds overlap: '+name+'/'+other)
    return True


def svg(plan):
    from .overview import svg as render
    return render(plan)
