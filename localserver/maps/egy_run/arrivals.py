'Pure EGY arrival geometry helpers; no captures, settings or host imports.'

DEFAULT_FOOTPRINTS=((10240,20480),(15360,21504))

def _overlaps(a,b):
    x,y,w,h=a;xx,yy,ww,hh=b
    return x<xx+ww and x+w>xx and y<yy+hh and y+h>yy

def arrival_safe(objects,point,nominal=(10240,20480),footprints=DEFAULT_FOOTPRINTS):
    """The nominal top-left defines one shared center/feet for every shell."""
    x,y=point;feet=y+nominal[1];center=x+nominal[0]/2
    for w,h in footprints:
        body=(center-w/2,feet-h,w,h)
        complete=any(p.get('terraintype') in (1,2) and p.get('ymp',0)==feet
            and p.get('xmp',0)<=body[0] and body[0]+w<=p.get('xmp',0)+p.get('wmp',0) for p in objects.values())
        if not complete or any(p.get('terraintype')==1 and _overlaps(body,
                tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp'))) for p in objects.values()):return False
    return True

def choose_safe_arrival(objects,point,nominal=(10240,20480),footprints=DEFAULT_FOOTPRINTS,max_shift_px=256):
    """Find the smallest tested 1px horizontal shift on the same floor, or None.

    Inputs are never changed. This is a SIM spawn adapter using native terrain;
    it never manufactures a floor or changes a room/body position.
    """
    candidates=[0]+[sign*pixels for pixels in range(1,int(max_shift_px)+1) for sign in (1,-1)]
    for pixels in candidates:
        found=[point[0]+pixels*256,point[1]]
        if not arrival_safe(objects,found,nominal,footprints):continue
        center=found[0]+nominal[0]/2;feet=found[1]+nominal[1];width=max(w for w,h in footprints)
        witness=next(oid for oid,p in objects.items() if p.get('terraintype') in (1,2)
            and p.get('ymp',0)==feet and p.get('xmp',0)<=center-width/2
            and center+width/2<=p.get('xmp',0)+p.get('wmp',0))
        return dict(point=found,support_objid=witness,feet_y=feet,shift_mp=[pixels*256,0],
                    checked_arrival_footprints_mp=[list(size) for size in footprints])
    return None
