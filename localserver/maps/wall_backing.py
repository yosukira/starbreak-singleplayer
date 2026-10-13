"""Pure compilation helper for a family's sealed structural-wall exterior.

Original geometry and input dictionaries are unchanged. Static structure
defines the flood boundary; moving/destructible blockers never seal it.
The caller selects native tile assets, layer, thickness and protected areas.
"""
import collections
import copy
import math

def _rectangles(cells, tile):
    rows=collections.defaultdict(list)
    for x,y in cells:rows[y].append(x)
    active,done,last={},[],None
    for y,xs in sorted(rows.items()):
        if last is not None and y!=last+1:done.extend(active.values());active={}
        runs=[]
        for x in sorted(xs):
            if runs and x==runs[-1][1]+1:runs[-1][1]=x
            else:runs.append([x,x])
        following={}
        for left,right in runs:
            key=(left,right);r=active.pop(key,[left,y,right-left+1,0]);r[3]+=1;following[key]=r
        done.extend(active.values());active=following;last=y
    done.extend(active.values())
    return [[x*tile,y*tile,w*tile,h*tile] for x,y,w,h in done]

def with_wall_backing(objects, *, wall_vids, backing_vid, transition_vid=None,
                      trim_vid=None, tile_size=8192, padding=262144, layer=60,
                      transition_tiles=2, structural_ids=None, protected_rects=(),
                      strict_enclosure=True, start_id=None):
    'Local battle simulator runtime.'
    tile=int(tile_size)
    if tile<=0 or padding<=0:raise ValueError('Wall backing tile_size/padding must be positive')
    result={int(k):copy.deepcopy(v) for k,v in objects.items()}
    selected=set(map(int,structural_ids)) if structural_ids is not None else None
    wall_vids=set(wall_vids)
    structures={oid:p for oid,p in result.items() if p.get('vid') in wall_vids and
        p.get('terraintype')==1 and not p.get('move') and p.get('hitpoints',0)<=0 and
        (selected is None or oid in selected)}
    if not structures:raise ValueError('No static structural walls for backing')
    solid=set()
    source_rects={}
    for oid,p in structures.items():
        x,y,w,h=[int(p.get(k,0)) for k in ('xmp','ymp','wmp','hmp')]
        if w<=0 or h<=0 or any(v%tile for v in (x,y,w,h)):
            raise ValueError('Structural wall %s is not aligned to tile_size %s'%(oid,tile))
        source_rects[oid]=[x,y,w,h]
        solid.update((i,j) for i in range(x//tile,(x+w)//tile) for j in range(y//tile,(y+h)//tile))
    margin=math.ceil(padding/tile)
    xmin=min(x for x,y in solid)-margin;xmax=max(x for x,y in solid)+margin
    ymin=min(y for x,y in solid)-margin;ymax=max(y for x,y in solid)+margin
    outside={(xmin,ymin)};queue=collections.deque(outside)
    while queue:
        x,y=queue.popleft()
        for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            a,b=point
            if xmin<=a<=xmax and ymin<=b<=ymax and point not in solid and point not in outside:
                outside.add(point);queue.append(point)
    protected=set()
    for rectangle in protected_rects:
        x,y,w,h=rectangle
        center=(math.floor((x+w/2)/tile),math.floor((y+h/2)/tile))
        if strict_enclosure and center in outside:
            raise ValueError('Structural exterior reaches protected playable center: %r'%list(rectangle))
        protected.update((i,j) for i in range(math.floor(x/tile),math.ceil((x+w)/tile))
                         for j in range(math.floor(y/tile),math.ceil((y+h)/tile)))
    skipped=len(outside & protected)
    outside-=protected
    def neighbors(p):
        x,y=p;return ((x-1,y),(x+1,y),(x,y-1),(x,y+1))
    transition={p for p in outside if any(q in solid for q in neighbors(p))} if transition_vid else set()
    for _ in range(max(0,int(transition_tiles)-1)):
        previous=frozenset(transition)
        transition.update({p for p in outside-previous if any(q in previous for q in neighbors(p))})
    deep=outside-transition
    oid=max(max(result,default=0)+1,int(start_id or 1));added=[]
    groups=[]
    if transition_vid:groups.append((transition_vid,transition))
    groups.append((backing_vid,deep))
    for vid,cells in groups:
        for x,y,w,h in _rectangles(cells,tile):
            result[oid]=dict(vid=vid,xmp=x,ymp=y,wmp=w,hmp=h,terraintype=1,
                             isinternal=True,boundsworld=False,draw=True,z=int(layer))
            added.append(oid);oid+=1
    if trim_vid:

        rim={p for p in outside if p[0] in (xmin,xmax) or p[1] in (ymin,ymax)}
        for x,y,w,h in _rectangles(rim,tile):
            result[oid]=dict(vid=trim_vid,xmp=x,ymp=y,wmp=w,hmp=h,terraintype=1,
                             isinternal=True,boundsworld=False,draw=True,z=int(layer))
            added.append(oid);oid+=1
    audit=dict(policy='USER wall depth; SIM sealed-exterior fill on captured static geometry',
        tile_size_mp=tile,padding_mp=margin*tile,wall_vids=sorted(wall_vids),
        backing_vid=backing_vid,transition_vid=transition_vid,trim_vid=trim_vid,layer=layer,
        source_wall_rects=source_rects,structural_ids=sorted(structures),
        excluded_dynamic_wall_ids=sorted(oid for oid,p in result.items() if p.get('vid') in wall_vids and
            (p.get('move') or p.get('hitpoints',0)>0)),
        transition_cells=len(transition),deep_cells=len(deep),protected_cells_skipped=skipped,
        added_ids=added,innerFacesPreserved=True,boundsworld=False)
    return result,audit
