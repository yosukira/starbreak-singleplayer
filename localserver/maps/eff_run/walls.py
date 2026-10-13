'EFF-only native Forest seam collar; original play-facing tiles are intact.'
from collections import deque
import copy
import math
from ..wall_backing import _rectangles

TILE=8192
STRUCTURE={'forest.ground','forest.underground'}
TILE_VIDS=STRUCTURE|{'forest.rockspawner','forest.ledge','forest.sslopeu','forest.sslopeg','forest.slope',
 'forest.midgroundtop','forest.midgroundmid','forest.midgroundbot','forest.imid','forest.transition'}

def cells(p):
    x,y,w,h=[p.get(k,0) for k in ('xmp','ymp','wmp','hmp')]
    return {(a,b) for a in range(math.floor(x/TILE),math.ceil((x+w)/TILE))
            for b in range(math.floor(y/TILE),math.ceil((y+h)/TILE))} if w>0 and h>0 else set()

def collar(objects,anchors,depth=2,focus=None):
    'Local battle simulator runtime.'
    result=copy.deepcopy(objects);solid=set();paint=set();structure={};beds=set()
    for p in objects.values():
        c=cells(p)
        if p.get('terraintype')==1 and not p.get('move'):solid.update(c)
        if p.get('vid') in TILE_VIDS:
            paint.update(c)
            if p.get('vid') in STRUCTURE:
                for point in c:structure[point]=p['vid']
            if p.get('vid')=='forest.rockspawner':beds.update(c)
    if not structure:return result,dict(added_ids=[],protected_air_cells=0)
    occupied=solid|paint
    xmin=min(x for x,y in occupied)-depth-2;xmax=max(x for x,y in occupied)+depth+2
    ymin=min(y for x,y in occupied)-depth-2;ymax=max(y for x,y in occupied)+depth+2
    protected=set();queue=deque()
    for x,y in anchors:
        point=(math.floor(x/TILE),math.floor(y/TILE))
        if point not in solid and point not in protected:protected.add(point);queue.append(point)
    if not queue:raise ValueError('EFF collar requires an anchor in native free air')
    while queue:
        x,y=queue.popleft()
        for point in ((x-1,y),(x+1,y),(x,y-1),(x,y+1)):
            a,b=point
            if xmin<=a<=xmax and ymin<=b<=ymax and point not in solid and point not in protected:
                protected.add(point);queue.append(point)
    assigned=dict(structure)

    assigned.update({point:'forest.ground' for point in beds})
    frontier=dict(assigned) if focus is None else {point:assigned[point] for point in focus if point in assigned}
    added={}
    for _ in range(depth):
        following={}
        for (x,y),vid in frontier.items():
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    point=(x+dx,y+dy)
                    if (point in assigned or point in paint or point in protected or
                        not (xmin<=point[0]<=xmax and ymin<=point[1]<=ymax)):continue
                    following[point]=vid
        assigned.update(following);added.update(following);frontier=following
    overlay=set()
    for p in objects.values():
        if p.get('vid')!='forest.rockspawner':continue
        c=cells(p);left=min(x for x,y in c);right=max(x for x,y in c)
        for x,y in c:

            if x in (left,right):overlay.add((x,y))
    oid=max(result,default=0)+1;ids=[];deep_ids=[];overlay_ids=[]
    for vid in sorted(set(added.values())):
        for x,y,w,h in _rectangles({point for point,value in added.items() if value==vid},TILE):
            result[oid]=dict(vid=vid,xmp=x,ymp=y,wmp=w,hmp=h,
                terraintype=1,draw=True,decorate=True,z=60,isinternal=False,boundsworld=False,
                invulnerable=True,removetype=0,knockback=False,noleech=True,lifetime=-1)
            ids.append(oid);deep_ids.append(oid);oid+=1
    for x,y,w,h in _rectangles(overlay,TILE):
        result[oid]=dict(vid='forest.ground',xmp=x,ymp=y,wmp=w,hmp=h,
            terraintype=1,draw=True,decorate=True,z=60,isinternal=False,boundsworld=False,
            invulnerable=True,removetype=0,knockback=False,noleech=True,lifetime=-1)
        ids.append(oid);overlay_ids.append(oid);oid+=1
    return result,dict(policy='SIM native Forest two-cell closed-deep-side collar; no play-facing source change',
        tile_size_mp=TILE,depth_tiles=depth,added_ids=ids,added_cells=len(added),
        protected_air_cells=len(protected),source_visible_cells=len(paint),
        focus_cells=sorted(focus) if focus is not None else None,
        original_properties_preserved=True,original_tile_cells_overlaid=len(overlay),
        deep_collar_ids=deep_ids,bed_overlay_ids=overlay_ids,bed_overlay_cells=len(overlay),
        bed_overlay_physical_union_unchanged=overlay<=beds,
        boundsworld=False,free_space_flood_is_not_jump_proof=True)
