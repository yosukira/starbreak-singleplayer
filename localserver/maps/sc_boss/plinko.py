"""Shared plinko board distribution and low-energy rolling rules."""
import math

def balanced_targets(pieces, targets, head, bounds):
    """Centre the footprint and redistribute surplus pegs without stacking."""
    cx=head[0]+head[2]/2.
    left=min(x for x,y in targets)
    right=max(x+p['props']['wmp'] for p,(x,y) in zip(pieces,targets))
    shift=cx-(left+right)/2.
    room_left=(bounds['x']+32)*256.
    room_right=(bounds['x']+bounds['w']-32)*256.
    shift=max(room_left-left,min(room_right-right,shift))
    result=[(x+shift,y) for x,y in targets]
    board_left=left+shift;board_right=right+shift

    def side(index):
        return -1 if result[index][0]+pieces[index]['props']['wmp']/2.<cx else 1

    def free(index, x):
        p=pieces[index]['props'];y=result[index][1];w,h=p['wmp'],p['hmp'];gap=8*256.
        return not any(j!=index and y<oy+pieces[j]['props']['hmp']+gap and y+h+gap>oy
            and x<ox+pieces[j]['props']['wmp']+gap and x+w+gap>ox
            for j,(ox,oy) in enumerate(result))

    for _ in range(len(pieces)):
        left_count=sum(side(i)<0 for i in range(len(pieces)))
        difference=len(pieces)-2*left_count
        if abs(difference)<=1:break
        dense=1 if difference>0 else -1
        candidates=[i for i in range(len(pieces)) if side(i)==dense]

        def density(index):
            x,y=result[index]
            return sum(abs(x-ox)<192*256. and abs(y-oy)<160*256.
                       for j,(ox,oy) in enumerate(result) if j!=index)
        candidates.sort(key=lambda i:(-density(i),abs(result[i][0]-cx),i))
        changed=False
        for index in candidates:
            w=pieces[index]['props']['wmp'];mirror=2*cx-result[index][0]-w
            lo=max(room_left,board_left);hi=min(room_right-w,board_right-w)
            if dense>0:hi=min(hi,cx-w-8*256.)
            else:lo=max(lo,cx+8*256.)
            if hi<lo:continue
            mirror=max(lo,min(hi,mirror))
            positions=[mirror]
            for offset in range(1,50):
                positions.extend((mirror-offset*24*256.,mirror+offset*24*256.))
            location=next((x for x in positions if lo<=x<=hi and free(index,x)),None)
            if location is not None:
                result[index]=(location,result[index][1]);changed=True;break
        if not changed:break
    return [(int(round(x)),int(round(y))) for x,y in result]

def roll_velocity(x, width, vx, surfaces, top, speed):
    """Find an open edge across touching pegs and leave it in a short glide."""
    intervals=sorted((box[0],box[0]+box[2]) for ident,box,old,terrain in surfaces
                     if abs(box[1]-top)<=512)
    merged=[]
    for lo,hi in intervals:

        if merged and lo-merged[-1][1]<width:
            merged[-1]=(merged[-1][0],max(hi,merged[-1][1]))
        else:merged.append((lo,hi))
    band=next(((lo,hi) for lo,hi in merged if x+width>lo and x<hi),None)
    if band is None:return vx
    lo,hi=band;left=x+width-lo+256.;right=hi-x+256.
    direction=(-1. if left<right else 1.) if abs(vx)<=6000.*speed else math.copysign(1.,vx)
    distance=left if direction<0 else right

    magnitude=max(abs(vx),23000.*speed,min(76000.*speed,distance/(.35/speed)))
    return direction*magnitude
