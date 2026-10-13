"""Swept rectangles for SC Boss enemies and projectiles, in native mp."""
import math

def overlaps(rect,box):
    x,y,w,h=rect;bx,by,bw,bh=box
    return x<bx+bw and x+w>bx and y<by+bh and y+h>by

def sweep(start,end,size,box):
    """First contact (fraction, outward normal), including initial overlap.

    Uses the whole moving rectangle and a Minkowski-expanded obstacle.
    Tangential motion on a face is allowed; entering a face is blocked.
    """
    x,y=start;w,h=size;bx,by,bw,bh=box
    if overlaps((x,y,w,h),box):
        depth,normal=min(((x+w-bx,(-1,0)),(bx+bw-x,(1,0)),
                          (y+h-by,(0,-1)),(by+bh-y,(0,1))))
        return (0.,normal)
    enter=0.;leave=1.;normal=None
    for origin,delta,lo,hi,nlo,nhi in (
            (x,end[0]-x,bx-w,bx+bw,(-1,0),(1,0)),
            (y,end[1]-y,by-h,by+bh,(0,-1),(0,1))):
        if abs(delta)<1e-12:
            if origin<=lo or origin>=hi:return None
            continue
        near=(lo-origin)/delta;far=(hi-origin)/delta
        face=nlo if delta>0 else nhi
        if near>far:near,far=far,near
        if near>=enter:enter=near;normal=face
        leave=min(leave,far)
        if enter>leave:return None
    if normal is None or enter<0. or enter>1. or leave<=0.:return None
    return (enter,normal)

def stop_at_face(point,size,box,normal):
    x,y=point;w,h=size;bx,by,bw,bh=box
    if normal[0]<0:x=bx-w
    elif normal[0]>0:x=bx+bw
    elif normal[1]<0:y=by-h
    else:y=by+bh
    return x,y
