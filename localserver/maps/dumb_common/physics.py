"""Swept boxes for authoritative projectile/wall contact."""

def sweep_entry(start, end, size, rect):
    x,y,w,h=rect
    lo,hi=0.,1.
    for a,b,lower,upper in ((start[0],end[0],x-size[0],x+w),
                             (start[1],end[1],y-size[1],y+h)):
        delta=b-a
        if abs(delta)<1e-12:
            if a<=lower or a>=upper:
                return None
            continue
        near,far=(lower-a)/delta,(upper-a)/delta
        lo,hi=max(lo,min(near,far)),min(hi,max(near,far))
        if lo>hi:
            return None
    return lo if 0<=lo<=1 and hi>=0 else None

def sweep_contact(start, end, size, rect):
    """First incoming contact and the reflecting axes, including corners."""
    x,y,w,h=rect
    near,far=-float('inf'),float('inf')
    axes=[]
    for axis,(a,b,lower,upper) in enumerate(((start[0],end[0],x-size[0],x+w),
                                           (start[1],end[1],y-size[1],y+h))):
        delta=b-a
        if abs(delta)<1e-12:
            if a<=lower or a>=upper:
                return None
            continue
        entry,exit=sorted(((lower-a)/delta,(upper-a)/delta))
        if entry>near+1e-9:
            near,axes=entry,[axis]
        elif abs(entry-near)<=1e-9:
            axes.append(axis)
        far=min(far,exit)
    if near>far or near< -1e-9 or near>1 or far<=0:
        return None
    return max(0.,near),axes
