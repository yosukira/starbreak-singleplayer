# -*- coding: utf-8 -*-
'runtime ETL boss-room terrain variants and selection helpers.'
import glob
import os
import random

VARIANTS_DIR = 'variants'

BOUNDARY_WALLS = {
    223: ('w', 'left', 24576),
    224: ('w', 'right', 24576),
    225: ('h', 'top', 16384),
    226: ('h', 'bottom', 16384),
}
BOUNDARY_OUTWARD_MP = 1024 * 256

def _thicken_props(objid, props):
    'Grow one fixed wall outward in-place; supports wire and pack keys.'
    entry = BOUNDARY_WALLS.get(int(objid))
    if entry is None:
        return False
    axis, side, base = entry
    vid = props.get('vid_', props.get('vid'))
    if vid != 'lab.wall':
        return False
    if axis == 'w':
        size_key = 'wmp_' if 'wmp_' in props else 'wmp'
        pos_key = 'xmp_' if 'xmp_' in props else 'xmp'
        current = int(props.get(size_key) or 0)
        target = max(current, base + BOUNDARY_OUTWARD_MP)
        if current >= target:
            return False
        if side == 'left':
            props[pos_key] = int(props.get(pos_key) or 0) - (target - current)
        props[size_key] = target
    else:
        size_key = 'hmp_' if 'hmp_' in props else 'hmp'
        pos_key = 'ymp_' if 'ymp_' in props else 'ymp'
        current = int(props.get(size_key) or 0)
        target = max(current, base + BOUNDARY_OUTWARD_MP)
        if current >= target:
            return False
        if side == 'top':
            props[pos_key] = int(props.get(pos_key) or 0) - (target - current)
        props[size_key] = target
    return True

def _join_boundary_corners(walls):
    """Extend outer spans to close corners without moving room-facing faces."""
    if not all(objid in walls for objid in BOUNDARY_WALLS):
        return set()
    def key(props,name):
        return name+'_' if name+'_' in props else name
    def value(props,name):
        return int(props.get(key(props,name)) or 0)
    left,right,top,bottom=(walls[objid] for objid in (223,224,225,226))
    if any(p.get('vid_',p.get('vid'))!='lab.wall' for p in (left,right,top,bottom)):
        return set()
    xmin=value(left,'xmp');xmax=value(right,'xmp')+value(right,'wmp')
    ymin=value(top,'ymp');ymax=value(bottom,'ymp')+value(bottom,'hmp')
    changed=set()
    for objid,pos,size,lo,hi in ((223,'ymp','hmp',ymin,ymax),(224,'ymp','hmp',ymin,ymax),
                               (225,'xmp','wmp',xmin,xmax),(226,'xmp','wmp',xmin,xmax)):
        props=walls[objid]
        start=min(value(props,pos),lo)
        end=max(value(props,pos)+value(props,size),hi)
        if start!=value(props,pos) or end-start!=value(props,size):
            props[key(props,pos)]=start;props[key(props,size)]=end-start
            changed.add(objid)
    return changed

def thicken_boundary_message(message):
    """Thicken the four fixed CreateWorld floor slabs before wire encode."""
    world = (message.get('worldstate_') or {}).get('serverworld_') or {}
    patched = set()
    walls = {}
    for item in world.get('obj_') or []:
        objid = int(item.get('objid_') or item.get('objid') or 0)
        props = item.get('props_') or item.get('props') or {}
        if objid in BOUNDARY_WALLS:walls[objid]=props
        if _thicken_props(objid, props):
            patched.add(objid)
    patched.update(_join_boundary_corners(walls))
    if (all(objid in walls for objid in BOUNDARY_WALLS)
            and all(p.get('vid_',p.get('vid'))=='lab.wall' for p in walls.values())):
        left,right,top,bottom=(walls[objid] for objid in (223,224,225,226))
        def value(props,name):return int(props.get(name+'_',props.get(name,0)))
        xmin=value(left,'xmp');xmax=value(right,'xmp')+value(right,'wmp')
        ymin=value(top,'ymp');ymax=value(bottom,'ymp')+value(bottom,'hmp')

        rectangles=[(xmin,ymin,value(left,'wmp')-24576,ymax-ymin),
                    (value(right,'xmp')+24576,ymin,value(right,'wmp')-24576,ymax-ymin),
                    (xmin,ymin,xmax-xmin,value(top,'hmp')-16384),
                    (xmin,value(bottom,'ymp')+16384,xmax-xmin,value(bottom,'hmp')-16384)]
        objects=world['obj_'];existing={o.get('objid_'):o for o in objects}
        border=32*256
        layers=[('lab.transition',rectangle) for rectangle in rectangles]
        layers.extend(('lab.external',(x+2*border,y+2*border,w-4*border,h-4*border))
                      for x,y,w,h in rectangles)
        layers.extend(('lab.wall',rectangle) for rectangle in
                      ((xmin,ymin,border,ymax-ymin),(xmax-border,ymin,border,ymax-ymin),
                       (xmin,ymin,xmax-xmin,border),(xmin,ymax-border,xmax-xmin,border)))
        for index,(vid,(x,y,w,h)) in enumerate(layers):
            objid=5000+index
            props=dict(xmp_=x,ymp_=y,wmp_=w,hmp_=h,vid_=vid,terraintype_=1,
                       isinternal_=True,draw_=True,boundsworld_=True,z_=60)
            if objid in existing:
                if existing[objid]['props_'].get('vid_') not in ('lab.external','lab.transition','lab.wall'):
                    raise ValueError('Fixed-wall backing ID conflicts with scene')
                existing[objid]['props_']=props
            else:objects.append(dict(objid_=objid,props_=props))
    return len(patched)

def thicken_boundary_templates(templates):
    """Apply the same floor thickness to the runtime template table."""
    patched = set()
    walls = {}
    for objid in BOUNDARY_WALLS:
        props = templates.get(str(objid)) or templates.get(objid)
        if isinstance(props, dict):
            walls[objid]=props
            if _thicken_props(objid, props):patched.add(objid)
    patched.update(_join_boundary_corners(walls))
    return len(patched)

def variant_paths(root):
    return sorted(glob.glob(os.path.join(root, VARIANTS_DIR, '*.hex')))

def variant_names(root):
    return [os.path.splitext(os.path.basename(path))[0]
            for path in variant_paths(root)]

def choose_variant(root, name=None, rng=None):
    'Return one CreateWorld hex for ``root``.'
    paths = variant_paths(root)
    if name:
        wanted = str(name)
        for path in paths:
            if os.path.splitext(os.path.basename(path))[0] == wanted:
                return path
        raise ValueError('unknown lab_hard terrain variant %r (have %s)'
                         % (wanted, ', '.join(variant_names(root)) or 'none'))
    if not paths:
        return os.path.join(root, 'createworld.hex')
    return (rng or random).choice(paths)
