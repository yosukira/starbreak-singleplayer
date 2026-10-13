"""Local EFF ground support with native lower-triangle slopes and rock riding.

Triangle enums/rectangles are D;32px step allowance and center-foot route
decisions are SIM. Upper-triangle ceilings use conservative AABB rejection.
Projectile/accelerated-rock sweeps use the actual occupied native polygon.
"""
import math
from ..dumb_common.physics import sweep_entry

def terrain_contact(row,start,end,size):
    """First full-body sweep contact with the current native terrain shape.

    A slope's empty half cannot block a seed or make it spawn fire. Rectangle
    contact keeps the existing EFF semantics, including stationary tangency.
    This geometry does not introduce an enemy routing or sliding decision.
    """
    p=row['props'];x,y=row['pos'];w,h=p.get('wmp',0),p.get('hmp',0)
    if w<=0 or h<=0:return None
    shape=p.get('shapetype',0)
    if shape==0:return sweep_entry(start,end,size,(x,y,w,h))
    hit=terrain_hit(row,start,end,size)
    return None if hit is None else hit['fraction']

def terrain_hit(row,start,end,size):
    """Occupied-shape contact details, without assuming a bounce decision.

    Normals point out of the obstacle at the entering surface. Existing
    overlap/departing-only contact has no inferred entry normal. Gftseed can
    therefore distinguish a real side contact from landing without treating
    every contact as its fire-producing terminal event.
    """
    p=row['props'];x,y=row['pos'];w,h=p.get('wmp',0),p.get('hmp',0)
    if w<=0 or h<=0:return None
    shape=p.get('shapetype',0)
    if shape not in (0,1,2,3,4):raise ValueError('Unsupported EFF native terrain shape '+str(shape))
    if sweep_entry(start,end,size,(x,y,w,h)) is None:return None
    vertices={0:((x,y),(x+w,y),(x+w,y+h),(x,y+h)),
              1:((x,y+h),(x+w,y),(x+w,y+h)),
              2:((x,y),(x+w,y+h),(x,y+h)),
              3:((x,y),(x+w,y),(x,y+h)),
              4:((x,y),(x+w,y),(x+w,y+h))}

    axes=[('x',1,0),('y',0,1)]
    if shape:axes.append(('diagonal',h,w) if shape in (1,3) else ('diagonal',-h,w))
    sw,sh=size;lo,hi=0.,1.;entries=[]
    for label,ax,ay in axes:
        values=[vx*ax+vy*ay for vx,vy in vertices[shape]]
        offsets=(0,sw*ax,sh*ay,sw*ax+sh*ay)
        lower,upper=min(values)-max(offsets),max(values)-min(offsets)
        origin=start[0]*ax+start[1]*ay
        delta=(end[0]-start[0])*ax+(end[1]-start[1])*ay
        if abs(delta)<1e-12:
            if origin<=lower or origin>=upper:return None
        else:
            a,b=(lower-origin)/delta,(upper-origin)/delta
            near,far=min(a,b),max(a,b)
            magnitude=math.hypot(ax,ay);sign=-1 if delta>0 else 1
            normal=(sign*ax/magnitude,sign*ay/magnitude)
            if near>lo:
                lo=near;entries=[(label,normal)]
            elif near>=0 and abs(near-lo)<1e-12:
                entries.append((label,normal))
            hi=min(hi,far)
            if lo>hi:return None
    return dict(fraction=lo,axes=tuple(label for label,normal in entries),
                normals=tuple(normal for label,normal in entries))

def floor_height(row,left,right):
    p=row['props'];x,y=row['pos'];w=p.get('wmp',0);h=p.get('hmp',0)
    if not w or right<=x or left>=x+w:return None
    shape=p.get('shapetype',0)
    if shape==1:return y+h*(1-max(0,min(1,(min(right,x+w)-x)/w)))
    if shape==2:return y+h*max(0,min(1,(max(left,x)-x)/w))
    return y

def ground(host,row,state,dt,vx,avoid_edges=False):
    p=row['props'];w,h=p['wmp'],p['hmp'];x,y=row['pos'];vy=state.get('vy',0.)

    surfaces=[(oid,r) for oid,r in host.actors.items() if oid!=row['id'] and not r.get('dead')
              and not (r.get('creator')==row['id'] and r.get('vid')=='forest.minerrock')
              and r['props'].get('terraintype') in (1,2) and r['props'].get('wmp',0)>0 and r['props'].get('hmp',0)>0]
    support=host.actors.get(state.get('support'))
    if support and state.get('support_pos') and state.get('grounded'):
        x+=support['pos'][0]-state['support_pos'][0];y+=support['pos'][1]-state['support_pos'][1]
    oldx,oldy=x,y;nx=x+vx*dt;feet=y+h
    for oid,r in surfaces:
        q=r['props'];sx,sy=r['pos'];sw,sh=q['wmp'],q['hmp']
        if q.get('terraintype')!=1 or q.get('shapetype',0) in (1,2):continue
        if y>=sy+sh or y+h<=sy:continue
        if state.get('grounded') and 0<=feet-sy<=32*256:continue
        if vx>0 and x+w<=sx and nx+w>sx:nx=sx-w
        elif vx<0 and x>=sx+sw and nx<sx+sw:nx=sx+sw
    if avoid_edges and state.get('grounded') and support:
        sx=support['pos'][0];sr=sx+support['props'].get('wmp',0)
        center=nx+w/2
        if center<sx or center>sr:
            equivalent=[r for _,r in surfaces if (f:=floor_height(r,nx,nx+w)) is not None and abs(f-feet)<=32*256]
            if not equivalent:nx=x;state['edge']=True
            else:state['edge']=False
    gravity=state.get('gravity',340000.);cap=160000.
    accelerate=max(0,min(dt,(cap-vy)/gravity));ny=y+vy*accelerate+.5*gravity*accelerate*accelerate+cap*(dt-accelerate)
    vy=min(cap,vy+gravity*dt);candidates=[]
    if vy>=0:
        for oid,r in surfaces:
            top=floor_height(r,nx,nx+w)
            if top is None:continue
            previous_top=floor_height(r,oldx,oldx+w)
            step=32*256 if state.get('grounded') else 0
            crossed=(previous_top is not None and feet<=previous_top+1 and ny+h>=top-1)
            if crossed or feet-step-1<=top<=ny+h+1:candidates.append((top,oid,r))
    state['grounded']=bool(candidates)
    if candidates:
        top,oid,r=min(candidates,key=lambda item:item[0]);ny=top-h;vy=0.
        state['support']=oid;state['support_pos']=tuple(r['pos'])
    else:
        state['support']=None;state['support_pos']=None
        if vy<0:
            for oid,r in surfaces:
                q=r['props'];sx,sy=r['pos'];sw,sh=q['wmp'],q['hmp']
                if q.get('terraintype')==1 and nx+w>sx and nx<sx+sw and y>=sy+sh and ny<sy+sh:
                    ny=sy+sh;vy=0.
    row['pos']=[nx,ny];row['vel']=[(nx-oldx)/dt if dt else 0.,(ny-oldy)/dt if dt else 0.];state['vy']=vy
    return abs(nx-oldx-vx*dt)>1
