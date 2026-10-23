"""SC topology schematic: authored internal geometry, local diagram coordinates."""
import heapq
import html
import math


_EPS = 1e-7
_PAD = 3.5
_LANE = 5.5


def _segments(points):
    return [(a, b) for a, b in zip(points, points[1:]) if a != b]


def _simplify(points):
    result = []
    for point in points:
        point = tuple(point)
        if result and point == result[-1]:
            continue
        if len(result) >= 2:
            a, b = result[-2:]
            if (abs(a[0]-b[0]) < _EPS and abs(b[0]-point[0]) < _EPS or
                    abs(a[1]-b[1]) < _EPS and abs(b[1]-point[1]) < _EPS):
                result[-1] = point
                continue
        result.append(point)
    return result


def _inside_segment(a, b, rect):
    """Strict interior intersection; a visibility lane may follow its edge."""
    x0, y0, x1, y1 = rect
    if abs(a[0]-b[0]) < _EPS:
        return (x0+_EPS < a[0] < x1-_EPS
                and min(max(a[1], b[1]), y1) > max(min(a[1], b[1]), y0)+_EPS)
    return (y0+_EPS < a[1] < y1-_EPS
            and min(max(a[0], b[0]), x1) > max(min(a[0], b[0]), x0)+_EPS)


def _intersection(a, b, c, d):
    """A proper crossing, excluding shared endpoints and parallel lanes."""
    ah, ch = abs(a[1]-b[1]) < _EPS, abs(c[1]-d[1]) < _EPS
    if ah == ch:
        return None
    if not ah:
        a, b, c, d = c, d, a, b
    x, y = c[0], a[1]
    if (min(a[0], b[0])+_EPS < x < max(a[0], b[0])-_EPS and
            min(c[1], d[1])+_EPS < y < max(c[1], d[1])-_EPS):
        return (x, y)
    return None


def _overlap(a, b, c, d):
    ah, ch = abs(a[1]-b[1]) < _EPS, abs(c[1]-d[1]) < _EPS
    if ah != ch:
        return 0.
    axis, fixed = (0, 1) if ah else (1, 0)
    if abs(a[fixed]-c[fixed]) > _EPS:
        return 0.
    return max(0., min(max(a[axis], b[axis]), max(c[axis], d[axis]))
               - max(min(a[axis], b[axis]), min(c[axis], d[axis])))


def _crossing_cost(a, b, c, d):
    # A visibility-grid step often ends exactly on an existing lane. Count
    # that contact too; otherwise splitting a crossing into two steps is free.
    horizontal = abs(a[1]-b[1]) < _EPS
    if horizontal == (abs(c[1]-d[1]) < _EPS):
        return False
    if horizontal:
        x,y = c[0],a[1]
        return (min(a[0],b[0])-_EPS <= x <= max(a[0],b[0])+_EPS
                and min(c[1],d[1])+_EPS < y < max(c[1],d[1])-_EPS)
    x,y = a[0],c[1]
    return (min(a[1],b[1])-_EPS <= y <= max(a[1],b[1])+_EPS
            and min(c[0],d[0])+_EPS < x < max(c[0],d[0])-_EPS)


def _route(start, end, obstacles, previous):
    """Bend-aware A* on the room-edge visibility grid, with separate lanes."""
    xs = {start[0], end[0]}
    ys = {start[1], end[1]}
    for x0, y0, x1, y1 in obstacles:
        xs.update((x0, x1))
        ys.update((y0, y1))
    # Alternate lanes prevent long coincident lines in a shared corridor.
    for a, b in previous:
        if abs(a[1]-b[1]) < _EPS:
            ys.update((a[1]-_LANE, a[1]+_LANE))
        else:
            xs.update((a[0]-_LANE, a[0]+_LANE))
    xs.update((min(xs)-24, max(xs)+24))
    ys.update((min(ys)-24, max(ys)+24))
    xs, ys = sorted(xs), sorted(ys)
    first = (xs.index(start[0]), ys.index(start[1]), 0)
    target = (xs.index(end[0]), ys.index(end[1]))
    pending = [(abs(start[0]-end[0])+abs(start[1]-end[1]), 0., first)]
    costs, parents, clear_cache = {first: 0.}, {}, {}
    while pending:
        _, cost, current = heapq.heappop(pending)
        if cost != costs.get(current):
            continue
        xi, yi, direction = current
        if (xi, yi) == target:
            result = []
            while True:
                result.append((xs[current[0]], ys[current[1]]))
                if current == first:
                    return _simplify(reversed(result))
                current = parents[current]
        a = (xs[xi], ys[yi])
        for nx, ny, axis in ((xi-1, yi, 1), (xi+1, yi, 1), (xi, yi-1, 2), (xi, yi+1, 2)):
            if not (0 <= nx < len(xs) and 0 <= ny < len(ys)):
                continue
            b = (xs[nx], ys[ny])
            key = tuple(sorted((a, b)))
            available = clear_cache.get(key)
            if available is None:
                available = not any(_inside_segment(a, b, rect) for rect in obstacles)
                clear_cache[key] = available
            if not available:
                continue
            distance = abs(a[0]-b[0])+abs(a[1]-b[1])
            penalty = 7. if direction and direction != axis else 0.
            for c, d in previous:
                overlap = _overlap(a, b, c, d)
                if overlap > _EPS:
                    penalty += 1000+overlap*70
                elif _crossing_cost(a, b, c, d):
                    penalty += 1000
            next_cost = cost+distance+penalty
            state = (nx, ny, axis)
            if next_cost+_EPS >= costs.get(state, math.inf):
                continue
            costs[state], parents[state] = next_cost, current
            heuristic = abs(b[0]-end[0])+abs(b[1]-end[1])
            heapq.heappush(pending, (next_cost+heuristic, next_cost, state))
    raise ValueError('SC overview could not route between authored doors')


def _rect_overlap(a, b):
    return min(a[2], b[2]) > max(a[0], b[0])+_EPS and min(a[3], b[3]) > max(a[1], b[1])+_EPS


def _port(room, oid):
    for source in room['metadata']['ports']:
        if int(source.get('id', source.get('objid', 0))) == int(oid):
            props = room['objects'].get(int(oid), room['objects'].get(str(oid)))
            at = source.get('at', [props.get('xmp', 0)+props.get('wmp', 0)/2,
                                   props.get('ymp', 0)+props.get('hmp', 0)/2])
            return dict(source, at=at)
    raise ValueError('SC overview door is absent: '+str(oid))


def _drawing(plan):
    rooms = plan['rooms']
    original = {role: tuple(room['metadata']['world_map_bounds_mp']) for role, room in rooms.items()}
    x0 = min(r[0] for r in original.values())
    y0 = min(r[1] for r in original.values())
    x1 = max(r[2] for r in original.values())
    y1 = max(r[3] for r in original.values())
    scale = 2240/max(1, x1-x0)
    def point(x, y):
        return (round(54+(x-x0)*scale, 5), round(140+(y-y0)*scale, 5))
    rects = {role: (*point(box[0], box[1]), *point(box[2], box[3])) for role, box in original.items()}
    obstacles = [(a-_PAD, b-_PAD, c+_PAD, d+_PAD) for a, b, c, d in rects.values()]
    def terminal(role, oid):
        room = rooms[role]
        p = _port(room, oid)
        offset = room['metadata']['world_map_offset_mp']
        at = point(p['at'][0]+offset[0], p['at'][1]+offset[1])
        a, b, c, d = rects[role]
        side = p.get('side')
        if side not in ('left', 'right', 'top', 'bottom'):
            side = min(('left', 'right', 'top', 'bottom'), key=lambda s:
                       dict(left=abs(at[0]-a), right=abs(at[0]-c),
                            top=abs(at[1]-b), bottom=abs(at[1]-d))[s])
        outside = dict(left=(a-_PAD, at[1]), right=(c+_PAD, at[1]),
                       top=(at[0], b-_PAD), bottom=(at[0], d+_PAD))[side]
        return at, outside
    pending = []
    for edge in plan['edges']:
        a, ao = terminal(edge['a'], edge['a_port'])
        b, bo = terminal(edge['b'], edge['b_port'])
        distance = abs(ao[0]-bo[0])+abs(ao[1]-bo[1])
        pending.append((distance, edge['a'], edge['b'], edge, a, ao, b, bo))
    previous, routes = [], []
    for _, _, _, edge, a, ao, b, bo in sorted(pending, key=lambda row: row[:3]):
        middle = _route(ao, bo, obstacles, previous)
        points = _simplify([a]+middle+[b])
        # Every interior segment must remain outside every unrelated room.
        for start, end in _segments(points):
            for role, rect in rects.items():
                if role not in (edge['a'], edge['b']) and _inside_segment(start, end, rect):
                    raise ValueError('SC overview route crosses room '+role)
        crossings = []
        for start, end in _segments(points):
            for c, d in previous:
                hit = _intersection(start, end, c, d)
                if hit:
                    crossings.append(hit)
        routes.append(dict(edge=edge, points=points, crossings=sorted(set(crossings))))
        previous.extend(_segments(points))
    return dict(rects=rects, point=point, scale=scale, routes=routes,
                width=2348, height=math.ceil((y1-y0)*scale+216))


def _path(points, crossings):
    def fmt(p):
        return '%.3f %.3f' % p
    result = ['M'+fmt(points[0])]
    for a, b in _segments(points):
        horizontal = abs(a[1]-b[1]) < _EPS
        axis = 0 if horizontal else 1
        direction = 1 if b[axis] >= a[axis] else -1
        hits = [p for p in crossings if (abs(p[1-axis]-a[1-axis]) < _EPS
                and min(a[axis], b[axis])+_EPS < p[axis] < max(a[axis], b[axis])-_EPS)]
        hits.sort(key=lambda p: p[axis]*direction)
        last = a[axis]
        for hit in hits:
            radius = min(2.7, abs(hit[axis]-last)/2, abs(b[axis]-hit[axis])/2)
            if radius < .2:
                continue
            before, after = list(hit), list(hit)
            before[axis] -= direction*radius
            after[axis] += direction*radius
            result.append('L'+fmt(tuple(before)))
            result.append('A%.3f %.3f 0 0 1 %s' % (radius, radius, fmt(tuple(after))))
            last = after[axis]
        result.append('L'+fmt(b))
    return ' '.join(result)


def _base_label(role, room):
    kind = room['metadata'].get('encounter_kind', '')
    if role == 'entry':
        return '入口', '#5fe1ef'
    if role == 'boss' or kind == 'boss':
        return 'Boss', '#e49cfa'
    if kind == 'gen':
        return 'Gen '+str(int(room['metadata'].get('gen_index', 0))+1)+' · 独立', '#ffd573'
    if kind in ('patriarch', 'bonesaw', 'hive', 'jump'):
        return dict(patriarch='Patriarch', bonesaw='Bonesaw', hive='Hive', jump='Jump')[kind], '#7be2b0'
    if role.startswith('chest'):
        return 'Chest '+str(int(role.rsplit('_', 1)[1])+1), '#e9c980'
    name = room['metadata']['template']
    return ({'scmid01': '搬运平台', 'scmid02': '图书馆', 'scmid03': '迷宫', 'scmid04': '僧侣庭', 'scmid05': 'Loop',
             'scmid06': '固定平台', 'scmid11': '蜂巢', 'scmid17': '引擎 · 60000',
             'scmid18': '雕像'}.get(name, 'SC '+name.replace('scmid', '')), '#9ab9d9')


def _label(role, room):
    text, color = _base_label(role, room)
    index = room['metadata'].get('main_index')
    if index is not None:
        text = '%02d · %s' % (int(index), text)
    elif role == 'boss':
        text = 'Boss · 独立'
    return text, color


def _label_positions(plan, drawing):
    rects, labels = drawing['rects'], {}
    occupied = []
    priority = lambda role: (0 if role.startswith(('mid_', 'gen_')) or role in ('entry', 'boss') else 1, role)
    for role in sorted(plan['rooms'], key=priority):
        room = plan['rooms'][role]
        text, color = _label(role, room)
        width = 12+sum(12 if ord(char) > 255 else 6.8 for char in text)
        a, b, c, d = rects[role]
        cx, cy = (a+c)/2, (b+d)/2
        above, below = (cx-width/2, b-24, cx+width/2, b-5), (cx-width/2, d+5, cx+width/2, d+24)
        candidates = [below, above] if room['metadata'].get('encounter_kind') == 'gen' else [above, below]
        candidates.extend(((a-width-7, cy-10, a-7, cy+9), (c+7, cy-10, c+width+7, cy+9)))
        candidates.extend((cx-width/2, b-24-offset, cx+width/2, b-5-offset) for offset in (23, 46))
        def score(box):
            overlaps = sum(_rect_overlap(box, other) for other in occupied)
            overlaps += sum(_rect_overlap(box, other) for name, other in rects.items() if name != role)
            return overlaps
        chosen = min(enumerate(candidates), key=lambda row: (score(row[1]), row[0]))[1]
        occupied.append(chosen)
        labels[role] = (chosen, text, color)
    return labels


def _atlas_svg(plan):
    """Render one self-contained atlas; no capture, UI or filesystem access."""
    drawing = _drawing(plan)
    rects, point, scale = drawing['rects'], drawing['point'], drawing['scale']
    labels = _label_positions(plan, drawing)
    crossing_count = sum(len(route['crossings']) for route in drawing['routes'])
    esc = html.escape
    body = ['<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s">' %
            (drawing['width'], drawing['height'], drawing['width'], drawing['height']),
            '<title>SC 关卡拓扑示意 / 本地生成方案 — seed %s</title>' % int(plan['seed']),
            '<desc>Diagram coordinates belong to the local generation proposal and are not original-server continuous map coordinates. Internal authored room geometry and door order are retained. Crossing marks remain unfinished routing, never gameplay overpasses. Four Gen defeats automatically enter the separate Boss arena.</desc>',
            '<defs><marker id="door-arrow" viewBox="0 0 6 6" refX="4.8" refY="3" markerWidth="4" markerHeight="4" orient="auto-start-reverse"><path d="M0 0 L6 3 L0 6" fill="none" stroke="#9bcaff" stroke-width="1.1"/></marker>',
            '<style>text{font-family:"Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif} .terrain{fill:#8bafc7;fill-opacity:.62} .ledge{fill:#a8c9d7;fill-opacity:.72}</style>',
            '</defs>', '<rect width="100%" height="100%" fill="#0b1421"/>',
            '<text x="35" y="34" fill="#f3f7ff" font-size="24" font-weight="650">SC 关卡拓扑示意 / 本地生成方案</text>',
            '<text x="35" y="57" fill="#b3c4d8" font-size="12">示意坐标，非原服连续地图坐标；保留房间内部几何与真实门序，不代表原图复现。</text>',
            '<text x="35" y="79" fill="#91a9c2" font-size="12">主关编号 00–%s · 4 Gen 独立 · Boss 独立 · %s rooms · seed %s</text>' %
            (max((r['metadata'].get('main_index',-1) for r in plan['rooms'].values()),default=-1),len(plan['rooms']), int(plan['seed'])),
            '<path d="M1080 36 H1130" stroke="#87bcf7" stroke-width="2" marker-start="url(#door-arrow)" marker-end="url(#door-arrow)"/><text x="1140" y="40" fill="#c2d4e6" font-size="12">双向门连接</text>',
            '<path d="M1260 36 H1310" stroke="#e5b85f" stroke-width="2" stroke-dasharray="6 4"/><text x="1320" y="40" fill="#c2d4e6" font-size="12">Mid 完成后进入独立 Gen</text>']
    body.append('<text x="1080" y="65" fill="%s" font-size="12">%s</text>' %
                ('#f4ad80' if crossing_count else '#7be2b0',
                 '连线排布未完成：仍有 %s 处交线；不是游戏中的高架通道。' % crossing_count if crossing_count
                 else '本图连线无交线；仅为本地拓扑示意。'))
    linked = {(e['a'], int(e['a_port'])) for e in plan['edges']} | {(e['b'], int(e['b_port'])) for e in plan['edges']}
    for role, room in plan['rooms'].items():
        a, b, c, d = rects[role]
        _, color = _label(role, room)
        clip = 'room-'+role
        title = '%s / %s' % (role, room['metadata']['template'])
        body.append('<g data-role="%s" data-template="%s"><title>%s</title>' % (esc(role), esc(room['metadata']['template']), esc(title)))
        body.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s" fill-opacity=".09" stroke="%s" stroke-opacity=".65" stroke-width=".85"/>' % (a,b,c-a,d-b,color,color))
        body.append('<clipPath id="%s"><rect x="%.3f" y="%.3f" width="%.3f" height="%.3f"/></clipPath><g clip-path="url(#%s)">' % (clip,a,b,c-a,d-b,clip))
        ox, oy = room['metadata']['world_map_offset_mp']
        for props in room['objects'].values():
            terrain = props.get('terraintype', 0)
            special = props.get('vid', '') in ('sc.enginecore', 'sc.fancy', 'sc.core')
            if terrain not in (1, 2) and not special:
                continue
            x, y = point(props.get('xmp', 0)+ox, props.get('ymp', 0)+oy)
            width, height = props.get('wmp', 0)*scale, props.get('hmp', 0)*scale
            if width <= 0 or height <= 0:
                continue
            if special:
                body.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="%s" fill-opacity=".85"/>' % (x,y,max(1.2,width),max(1.2,height),color))
                continue
            shape = props.get('shapetype', 0)
            corners = [(x,y),(x+width,y),(x+width,y+height),(x,y+height)]
            triangles = {1: (1,2,3), 2: (0,2,3), 3: (0,1,3), 4: (0,1,2)}
            if shape in triangles:
                coords = ' '.join('%.3f,%.3f' % corners[i] for i in triangles[shape])
                body.append('<polygon points="%s" class="terrain"/>' % coords)
            else:
                body.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" class="%s"/>' % (x,y,max(.45,width),max(.55,height),'ledge' if terrain == 2 else 'terrain'))
        body.append('</g></g>')
    for route in drawing['routes']:
        edge = route['edge']
        path = _path(route['points'], [])
        gate = bool(edge.get('requires'))
        color = '#e5b85f' if gate else '#87bcf7'
        attrs = ' stroke-dasharray="6 4"' if gate else ''
        body.append('<g data-edge="%s:%s--%s:%s" data-gate="%s"><title>%s ↔ %s%s</title>' %
                    (esc(edge['a']),edge['a_port'],esc(edge['b']),edge['b_port'],esc(str(edge.get('requires') or '')),
                     esc(edge['a']),esc(edge['b']),' · Mid cleared' if gate else ''))
        body.append('<path d="%s" fill="none" stroke="#0b1421" stroke-width="4.8" stroke-linejoin="round"/>' % path)
        body.append('<path d="%s" fill="none" stroke="%s" stroke-width="1.65" stroke-linejoin="round" marker-start="url(#door-arrow)" marker-end="url(#door-arrow)"%s/>' % (path,color,attrs))
        body.append('</g>')
    for crossing in sorted(set(p for route in drawing['routes'] for p in route['crossings'])):
        x,y = crossing
        body.append('<circle cx="%.3f" cy="%.3f" r="4" fill="#0b1421" stroke="#f4ad80" stroke-width="1.2"/><path d="M%.3f %.3f l4 4 m-4 0 l4 -4" stroke="#f4ad80" stroke-width=".8"><title>交线：图示排布待完成，不是游戏高架。</title></path>' % (x,y,x-2,y-2))
    for role, room in plan['rooms'].items():
        ox, oy = room['metadata']['world_map_offset_mp']
        for source in room['metadata']['ports']:
            oid = int(source.get('id', source.get('objid', 0)))
            p = _port(room, oid)
            x, y = point(p['at'][0]+ox,p['at'][1]+oy)
            connected = (role,oid) in linked
            body.append('<circle data-door="%s:%s" cx="%.3f" cy="%.3f" r="%s" fill="%s" stroke="#0b1421" stroke-width=".7"><title>%s · door %s</title></circle>' %
                        (esc(role),oid,x,y,2.7 if connected else 1.5,'#d4eaff' if connected else '#5a6c80',esc(role),oid))
    for role, (box, text, color) in labels.items():
        a,b,c,d = box
        room = rects[role]
        cx,cy = (a+c)/2,(b+d)/2
        near = (max(room[0],min(cx,room[2])), max(room[1],min(cy,room[3])))
        body.append('<path d="M%.3f %.3f L%.3f %.3f" stroke="%s" stroke-width=".65" opacity=".7"/>' % (cx,cy,*near,color))
        body.append('<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" rx="3" fill="#111e2e" stroke="%s" stroke-width=".6"/>' % (a,b,c-a,d-b,color))
        body.append('<text x="%.3f" y="%.3f" text-anchor="middle" fill="%s" font-size="12" font-weight="600">%s</text>' % (cx,b+13.6,color,esc(text)))
    if 'boss' in rects:
        a,b,c,d = rects['boss']
        text = '4 个 Gen 全部击破 → 自动转入 Boss'
        width = 265
        x = max(28,min((a+c-width)/2,drawing['width']-width-28))
        y = min(d+37,drawing['height']-38)
        body.append('<rect x="%.3f" y="%.3f" width="%s" height="25" rx="5" fill="#302044" stroke="#bb87de" stroke-width=".8"/>' % (x,y,width))
        body.append('<text x="%.3f" y="%.3f" text-anchor="middle" fill="#e8c5fc" font-size="12">%s</text>' % (x+width/2,y+17,esc(text)))
        body.append('<path d="M%.3f %.3f V%.3f" stroke="#bb87de" stroke-width="1" stroke-dasharray="3 3"/>' % ((a+c)/2,d+4,y))
    body.append('</svg>')
    return '\n'.join(body)


def _thumbnail_drawing(plan):
    """Dependency-free cards on the generator's columns and row order.

    Route every authored door separately. In particular, an adjacent pair of
    four-way rooms has two distinct edges, never a simple-graph neighbor entry.
    """
    from .layout import bounds, four_way_sides
    columns = {value: index for index, value in enumerate(sorted({c[0] for c in plan['cells'].values()}))}
    rows = {value: index for index, value in enumerate(sorted({c[1] for c in plan['cells'].values()}))}
    cards, terminals = {}, {}
    width, height, xstep, ystep = 292, 200, 448, 288
    for role, room in plan['rooms'].items():
        column, row = plan['cells'][role]
        x, y = 64+columns[column]*xstep, 168+rows[row]*ystep
        source = bounds(room)
        a,b,c,d = source
        scale = min((width-80)/max(1,c-a), 116/max(1,d-b))
        origin = (x+(width-(c-a)*scale)/2-a*scale, y+57-b*scale)
        card = dict(center=(x+width/2,y+height/2),box=(x,y,x+width,y+height),
                    source=source,scale=scale,origin=origin)
        cards[role] = card
        for port in room['metadata']['ports']:
            px,py = origin[0]+port['at'][0]*scale,origin[1]+port['at'][1]*scale
            side = port['side']
            anchor = dict(left=(x,py),right=(x+width,py),top=(px,y),bottom=(px,y+height))[side]
            outside = dict(left=(x-8,py),right=(x+width+8,py),
                           top=(px,y-8),bottom=(px,y+height+8))[side]
            terminals[(role,int(port['id']))] = dict(at=(px,py),anchor=anchor,outside=outside,side=side)
    obstacles = [(a-8,b-8,c+8,d+8) for a,b,c,d in (card['box'] for card in cards.values())]
    paired, pairs = {}, {}
    for index, edge in enumerate(plan['edges']):
        if four_way_sides(plan['rooms'][edge['a']]) and four_way_sides(plan['rooms'][edge['b']]):
            key = tuple(sorted((edge['a'],edge['b'])))
            pair = pairs.setdefault(key,'P'+str(len(pairs)+1))
            paired[index] = pair
    routes = []
    for index, edge in enumerate(plan['edges']):
        a = terminals[(edge['a'],int(edge['a_port']))]
        b = terminals[(edge['b'],int(edge['b_port']))]
        # Visibility routing avoids every card. Lines may cross or share a
        # schematic lane; their edge IDs retain the independent door relation.
        middle = _route(a['outside'],b['outside'],obstacles,[])
        points = _simplify([a['anchor']]+middle+[b['anchor']])
        routes.append(dict(id='E%02d'%(index+1),edge=edge,points=points,pair=paired.get(index)))
    return dict(cards=cards,terminals=terminals,routes=routes,
                width=columns[max(columns)]*xstep+width+128,
                height=rows[max(rows)]*ystep+height+244)


def svg(plan):
    """Render every real door relation using only the Python standard library."""
    drawing = _thumbnail_drawing(plan)
    cards = drawing['cards']; esc = html.escape
    body = ['<svg xmlns="http://www.w3.org/2000/svg" width="%s" height="%s" viewBox="0 0 %s %s">' %
            (drawing['width'],drawing['height'],drawing['width'],drawing['height']),
            '<title>SC 关卡拓扑示意 / 本地生成方案</title>',
            '<desc>Each authored room thumbnail has an independent scale. Cards preserve the generated column and row order. Lines identify independent bidirectional door transitions, not physical roads; line crossings and shared lanes are not junctions. Retail exact room count remains unknown.</desc>',
            '<rect width="100%" height="100%" fill="#0b1421"/>',
            '<style>text{font-family:"Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif}.port{paint-order:stroke;stroke:#132539;stroke-width:3;stroke-linejoin:round}</style>',
            '<text x="35" y="39" fill="#f1f6ff" font-size="27" font-weight="650">SC 关卡拓扑示意 / 本地生成方案</text>',
            '<text x="35" y="65" fill="#b7c9dc" font-size="14">各房独立缩放，未镜像；门侧与上下次序保留。示意坐标不是原服连续地图坐标。</text>',
            '<text x="35" y="90" fill="#87dab2" font-size="14">传送关系边 · 本地主关 00–%02d · 4 个 Gen 独立 · Boss 独立 · seed %s</text>' %
            (max(r['metadata'].get('main_index',-1) for r in plan['rooms'].values()),int(plan['seed'])),
            '<text x="35" y="113" fill="#91a9c2" font-size="12">线段仅表示双向传送，交叉或共线不连通；L/R/T/B 为真实门侧。E 为门连接，粉色 P 为上下成对连接。</text>',
            '<text x="35" y="136" fill="#91a9c2" font-size="12">编号与房数为本地拟合；原服已见编号 24，精确总数仍未知。金色虚线进入 Mid 对应 Gen；四 Gen 击破后自动转入独立 Boss。</text>']
    edge_ids = {}
    for route in drawing['routes']:
        edge = route['edge']
        for role,oid in ((edge['a'],edge['a_port']),(edge['b'],edge['b_port'])):
            edge_ids[(role,int(oid))] = (route['id'],route['pair'])
        gate = edge.get('requires')
        color = '#efa5df' if route['pair'] else '#e8bd69' if gate else '#86b8ed'
        dash = ' stroke-dasharray="6 4"' if gate else ''
        a_port,b_port = _port(plan['rooms'][edge['a']],edge['a_port']),_port(plan['rooms'][edge['b']],edge['b_port'])
        path = _path(route['points'],[])
        body.append('<g data-edge="%s:%s--%s:%s" data-edge-id="%s" data-pair="%s"><title>%s: %s door %s (%s) ↔ %s door %s (%s)%s</title>' %
                    (esc(edge['a']),edge['a_port'],esc(edge['b']),edge['b_port'],route['id'],route['pair'] or '',route['id'],
                     esc(edge['a']),edge['a_port'],a_port['side'],esc(edge['b']),edge['b_port'],b_port['side'],' · Mid cleared' if gate else ''))
        body.append('<path d="%s" fill="none" stroke="#0b1421" stroke-width="6"/><path d="%s" fill="none" stroke="%s" stroke-width="1.8"%s/></g>' %
                    (path,path,color,dash))
    for role,room in plan['rooms'].items():
        card = cards[role]; x0,y0,x1,y1 = card['box']; factor = card['scale']
        label,color = _label(role,room)
        index = room['metadata'].get('main_index')
        def point(x,y):
            return (card['origin'][0]+x*factor,card['origin'][1]+y*factor)
        body.append('<g data-role="%s" data-template="%s"><title>%s / %s · 独立缩放</title><rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" rx="6" fill="#132539" stroke="%s" stroke-width="1.2"/>' %
                    (esc(role),esc(room['metadata']['template']),esc(role),esc(room['metadata']['template']),x0,y0,x1-x0,y1-y0,color))
        body.append('<text x="%.3f" y="%.3f" fill="%s" text-anchor="middle" font-size="17" font-weight="650">%s</text><text x="%.3f" y="%.3f" fill="#91a9c2" text-anchor="middle" font-size="11">%s</text>' %
                    ((x0+x1)/2,y0+25,color,esc(label),(x0+x1)/2,y0+43,esc(room['metadata']['template'])))
        for p in room['objects'].values():
            if p.get('terraintype') not in (1,2) or not p.get('wmp') or not p.get('hmp'):
                continue
            x,y = point(p.get('xmp',0),p.get('ymp',0)); w,h = p['wmp']*factor,p['hmp']*factor
            shape = p.get('shapetype',0)
            corners = [(x,y),(x+w,y),(x+w,y+h),(x,y+h)]
            triangles = {1:(1,2,3),2:(0,2,3),3:(0,1,3),4:(0,1,2)}
            if shape in triangles:
                body.append('<polygon points="%s" fill="#a2bfd0"/>' % ' '.join('%.4f,%.4f'%corners[i] for i in triangles[shape]))
            else:
                body.append('<rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="#a2bfd0"/>' % (x,y,max(.3,w),max(.35,h)))
        for port in room['metadata']['ports']:
            oid = int(port['id']); terminal = drawing['terminals'][(role,oid)]
            edge_id,pair = edge_ids[(role,oid)]
            side = port['side']; letter = dict(left='L',right='R',top='T',bottom='B')[side]
            x,y = terminal['at']; ax,ay = terminal['anchor']
            tint = '#efa5df' if pair else '#ffd573' if port['kind']=='core' else '#c3def8'
            body.append('<circle data-door="%s:%s" cx="%.4f" cy="%.4f" r="2" fill="%s"><title>%s #%s · %s%s</title></circle>' %
                        (esc(role),oid,x,y,tint,side,oid,edge_id,' / '+pair if pair else ''))
            if side in ('left','right'):
                tx,ty,align = (x0+5,ay-5,'start') if side=='left' else (x1-5,ay-5,'end')
            else:
                tx,ty,align = ax,y0+54 if side=='top' else y1-8,'middle'
            text = '%s #%s · %s%s' % (letter,oid,edge_id,' '+pair if pair else '')
            body.append('<text class="port" x="%.3f" y="%.3f" fill="%s" text-anchor="%s" font-size="10">%s</text>' % (tx,ty,tint,align,esc(text)))
        if role=='boss':
            body.append('<text x="%.3f" y="%.3f" fill="#d8b3ee" text-anchor="middle" font-size="11">四个 Gen 全部击破后自动转入</text>' % ((x0+x1)/2,y1-8))
        body.append('</g>')
    body.append('</svg>')
    return '\n'.join(body)
