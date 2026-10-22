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
    return ({'scmid01': '搬运平台', 'scmid02': '图书馆', 'scmid03': '迷宫', 'scmid04': '僧侣庭',
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
    """Straight-line planar embedding with a protected disk for each card."""
    import networkx as nx
    rotation = {role: [] for role in plan['rooms'] if role != 'boss'}
    for edge in plan['edges']:
        for role, other, oid in ((edge['a'],edge['b'],edge['a_port']), (edge['b'],edge['a'],edge['b_port'])):
            port = _port(plan['rooms'][role], oid)
            x,y = port['at']; side = port['side']
            key = (dict(top=0,right=1,bottom=2,left=3)[side],
                   x if side == 'top' else y if side == 'right' else -x if side == 'bottom' else -y)
            rotation[role].append((key,other))
    embedding = nx.PlanarEmbedding()
    embedding.set_data({role:[other for _,other in sorted(rows)] for role,rows in rotation.items()})
    embedding.check_structure()
    raw = nx.combinatorial_embedding_to_pos(embedding,fully_triangulate=False)
    xmin,xmax = min(p[0] for p in raw.values()),max(p[0] for p in raw.values())
    ymin,ymax = min(p[1] for p in raw.values()),max(p[1] for p in raw.values())
    factor = min(2020/max(1,xmax-xmin),650/max(1,ymax-ymin))
    centers = {role:(95+(x-xmin)*factor,175+(ymax-y)*factor) for role,(x,y) in raw.items()}
    def distance(point,a,b):
        dx,dy = b[0]-a[0],b[1]-a[1]
        t = max(0,min(1,((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy)))
        return math.dist(point,(a[0]+t*dx,a[1]+t*dy))
    cards = {}
    for role,center in centers.items():
        limits = [math.dist(center,p)*.27 for other,p in centers.items() if other != role]
        limits += [distance(center,centers[e['a']],centers[e['b']])*.36
                   for e in plan['edges'] if role not in (e['a'],e['b'])]
        radius = min(48,*limits)
        room = plan['rooms'][role]
        source = room['metadata'].get('bounds')
        if not source:
            source = room['metadata']['world_map_bounds_mp']
        a,b,c,d = source
        scale = radius*2/max(1,math.hypot(c-a,d-b))
        width,height = (c-a)*scale,(d-b)*scale
        box = (center[0]-width/2,center[1]-height/2,center[0]+width/2,center[1]+height/2)
        cards[role] = dict(center=center,radius=radius,box=box,source=source,scale=scale)
    boss = plan['rooms']['boss']; source = boss['metadata']['bounds']
    a,b,c,d = source; scale = 92/max(c-a,d-b)
    cards['boss'] = dict(center=(1800,900),radius=68,box=(1754,854,1846,946),source=source,scale=scale)
    return dict(cards=cards,centers=centers,embedding=embedding,width=2630,height=1040)


def svg(plan):
    """Zero-crossing relationship diagram; rooms are independently scaled."""
    drawing = _thumbnail_drawing(plan)
    cards = drawing['cards']; esc = html.escape
    body = ['<svg xmlns="http://www.w3.org/2000/svg" width="2630" height="1040" viewBox="0 0 2630 1040">',
            '<title>SC 关卡拓扑示意 / 本地生成方案</title>',
            '<desc>Each authored room thumbnail has an independent scale. Coordinates are schematic, not original-server continuous map coordinates. Center lines terminate at cards and identify linked real doors; physical door-seam alignment is not asserted.</desc>',
            '<rect width="100%" height="100%" fill="#0b1421"/>',
            '<style>text{font-family:"Segoe UI","Microsoft YaHei","Noto Sans CJK SC",sans-serif}</style>',
            '<text x="35" y="39" fill="#f1f6ff" font-size="27" font-weight="650">SC 关卡拓扑示意 / 本地生成方案</text>',
            '<text x="35" y="65" fill="#b7c9dc" font-size="14">各房独立缩放；保留框内几何与真实门对应。示意坐标不是原服连续地图坐标，不代表原图复现。</text>',
            '<text x="35" y="90" fill="#87dab2" font-size="14">关系边零交线 · 主关 00–24 · 4 个 Gen 独立 · Boss 独立 · seed %s</text>' % int(plan['seed']),
            '<text x="35" y="113" fill="#91a9c2" font-size="12">中心关系线只接缩略框边；不宣称房间门缝在物理坐标中对齐。悬停线段可看双方门 ID。</text>']
    def on_box(center,other,box):
        dx,dy = other[0]-center[0],other[1]-center[1]
        tx = (box[2]-box[0])/2/abs(dx) if dx else math.inf
        ty = (box[3]-box[1])/2/abs(dy) if dy else math.inf
        t = min(tx,ty)
        return (center[0]+dx*t,center[1]+dy*t)
    for edge in plan['edges']:
        a,b = cards[edge['a']],cards[edge['b']]
        p = on_box(a['center'],b['center'],a['box']); q = on_box(b['center'],a['center'],b['box'])
        gate = edge.get('requires')
        color = '#e8bd69' if gate else '#86b8ed'
        dash = ' stroke-dasharray="6 4"' if gate else ''
        body.append('<path data-edge="%s--%s" d="M%.4f %.4f L%.4f %.4f" fill="none" stroke="%s" stroke-width="1.6"%s><title>%s door %s ↔ %s door %s%s</title></path>' %
                    (esc(edge['a']),esc(edge['b']),*p,*q,color,dash,esc(edge['a']),edge['a_port'],esc(edge['b']),edge['b_port'],' · Mid cleared' if gate else ''))
    labels = []
    for role,room in plan['rooms'].items():
        card = cards[role]; x0,y0,x1,y1 = card['box']; a,b,c,d = card['source']; factor = card['scale']
        _,color = _label(role,room)
        index = room['metadata'].get('main_index')
        short = '%02d'%index if index is not None else 'G'+str(room['metadata'].get('gen_index',0)+1) if role != 'boss' else 'B'
        def point(x,y):
            return (x0+(x-a)*factor,y0+(y-b)*factor)
        body.append('<g data-role="%s"><title>%s / %s · 独立缩放</title><clipPath id="thumb-%s"><rect x="%.4f" y="%.4f" width="%.4f" height="%.4f"/></clipPath><rect x="%.4f" y="%.4f" width="%.4f" height="%.4f" fill="#17283b" stroke="%s" stroke-width="1"/><g clip-path="url(#thumb-%s)">' %
                    (esc(role),esc(role),esc(room['metadata']['template']),role,x0,y0,x1-x0,y1-y0,x0,y0,x1-x0,y1-y0,color,role))
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
        body.append('</g>')
        for port in room['metadata']['ports']:
            x,y = point(*port['at'])
            body.append('<circle cx="%.4f" cy="%.4f" r="1.3" fill="#fff3c5"><title>真实门 %s</title></circle>'%(x,y,port['id']))
        cx,cy = card['center']
        body.append('<rect x="%.3f" y="%.3f" width="25" height="17" rx="3" fill="#0d1928" stroke="%s" stroke-width=".7"/><text x="%.3f" y="%.3f" fill="%s" text-anchor="middle" font-size="11" font-weight="650">%s</text></g>' % (cx-12.5,cy-8.5,color,cx,cy+3.8,color,short))
        text,_ = _base_label(role,room)
        labels.append((999 if role=='boss' else 100+int(room['metadata'].get('gen_index',0)) if index is None else index,short,text,room['metadata']['template'],color))
    body += ['<rect x="2170" y="133" width="425" height="856" rx="8" fill="#111e2d" stroke="#314559"/>',
             '<text x="2190" y="162" fill="#eff5ff" font-size="17">房间编号 / 原始房型</text>']
    for i,(_,short,text,template,color) in enumerate(sorted(labels)):
        y = 190+i*25
        body.append('<text x="2190" y="%s" fill="%s" font-size="13">%s</text>' % (y,color,esc(short+'  '+text+'  /  '+template)))
    body += ['<path d="M1635 985 H1940" stroke="#bb87de" stroke-dasharray="4 4"/>',
             '<text x="1790" y="1010" text-anchor="middle" fill="#e4bff8" font-size="14">4 个 Gen 全部击破后自动转入独立 Boss</text>',
             '<text x="35" y="1006" fill="#91a9c2" font-size="12">蓝线：双向门关系；金色虚线：Mid 完成后进入对应 Gen。真实房间几何保留于各独立场景。</text>', '</svg>']
    return '\n'.join(body)
