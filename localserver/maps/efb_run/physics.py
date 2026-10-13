'EFB-local native shape0..4 collision, independent of AI/encounter policy.'
import math
from ..etl_motion import Terrain as RectangleTerrain

def polygon(bounds,shape):
    x,y,w,h=bounds
    vertices={0:((x,y),(x+w,y),(x+w,y+h),(x,y+h)),
        1:((x,y+h),(x+w,y),(x+w,y+h)),2:((x,y),(x+w,y+h),(x,y+h)),
        3:((x,y),(x+w,y),(x,y+h)),4:((x,y),(x+w,y),(x+w,y+h))}
    if shape not in vertices:raise ValueError('Unsupported EFB native terrain shape '+str(shape))
    return vertices[shape]

def swept_shape(bounds,shape,start,end,size,*,penetrating=False,tolerance=0.):
    """Continuous SAT against a full moving rectangle and convex source shape.

    Projectile contact is closed. Ground correction asks for actual interior
    entry so standing/tangent/departing contact does not become a ghost wall.
    tolerance is only the caller's native integer-coordinate contact quantum.
    """
    vertices=polygon(bounds,shape);w,h=size
    axes=[(1.,0.),(0.,1.)]+[(-(b[1]-a[1]),b[0]-a[0]) for a,b in zip(vertices,vertices[1:]+vertices[:1])]
    lo,hi=0.,1.
    for nx,ny in axes:
        if nx==ny==0:continue
        values=[x*nx+y*ny for x,y in vertices]
        offsets=(0.,w*nx,h*ny,w*nx+h*ny)
        lower,upper=min(values)-max(offsets),max(values)-min(offsets)
        margin=tolerance*math.hypot(nx,ny) if penetrating else 0.
        lower+=margin;upper-=margin
        if lower>=upper:return None
        origin=start[0]*nx+start[1]*ny;delta=(end[0]-start[0])*nx+(end[1]-start[1])*ny
        if abs(delta)<1e-12:
            if origin<lower or origin>upper or penetrating and (origin<=lower or origin>=upper):return None
        else:
            a,b=(lower-origin)/delta,(upper-origin)/delta
            lo=max(lo,min(a,b));hi=min(hi,max(a,b))
            if lo>hi or penetrating and lo>=hi:return None
    if penetrating and (hi<=0. or lo>=1.):return None
    return lo

def surface(bounds,shape,x,width,upper=True):
    """Exact native top/bottom envelope over the entire body footprint."""
    px,py,pw,ph=bounds;left=max(x,px);right=min(x+width,px+pw)
    if left>=right or pw<=0 or ph<=0:return None
    polygon(bounds,shape)
    if upper:
        if shape==1:return py+ph*(1-(right-px)/pw)
        if shape==2:return py+ph*(left-px)/pw
        return py
    if shape==3:return py+ph*(1-(left-px)/pw)
    if shape==4:return py+ph*(right-px)/pw
    return py+ph

def resolve_triangles(body,previous,triangles,*,platforms=True,tolerance=1.):
    'Correct a legacy rectangle-only tentative step, keeping its integrator.'
    x,y,w,h=previous['x'],previous['y'],body['w'],body['h'];nx,ny=body['x'],body['y']
    descending=previous.get('vy',0)>=0 and body.get('vy',0)>=0
    floors=[];ceilings=[]
    for oid,bounds,shape,kind in triangles:
        if kind==2 and not platforms:continue
        top=surface(bounds,shape,nx,w)
        if top is None:continue
        oldtop=surface(bounds,shape,x,w)
        contact=oldtop is not None and abs(y+h-oldtop)<=tolerance
        crossing=y+h<=(oldtop if oldtop is not None else top)+tolerance and ny+h>=top-tolerance
        if descending and (contact or crossing):floors.append((top,oid))
        if kind==1 and previous.get('vy',0)<0:
            bottom=surface(bounds,shape,nx,w,False);oldbottom=surface(bounds,shape,x,w,False)
            if y>=(oldbottom if oldbottom is not None else bottom)-tolerance and ny<=bottom:
                ceilings.append(bottom)
    if floors:
        top,oid=min(floors)
        if not body.get('grounded') or top<=ny+h+tolerance:
            ny=top-h;body.update(vy=0.,grounded=True,support=oid)
    elif ceilings:
        ny=max(ceilings);body.update(vy=0.,grounded=False,support=None)
    hits=[]
    for oid,bounds,shape,kind in triangles:
        if kind!=1:continue
        t=swept_shape(bounds,shape,(x,y),(nx,ny),(w,h),penetrating=True,tolerance=tolerance)
        if t is not None:hits.append((t,oid))
    if hits:
        t,oid=min(hits);t=max(0.,t-1e-10)
        tx,ty=x+(nx-x)*t,y+(ny-y)*t
        if abs(ty-ny)>tolerance:body.update(grounded=False,support=None)
        body['blocked']=bool(body.get('blocked')) or abs(tx-nx)>tolerance
        nx,ny=tx,ty
    body.update(x=nx,y=ny)

class TriangleTerrain(RectangleTerrain):
    """EFB eggs: unchanged ETL ballistic/flat path plus local triangle contact."""
    def __init__(self,templates):
        flat={};self.triangles=[]
        for key,p in templates.items():
            shape=p.get('shapetype',0);kind=int(p.get('terraintype') or 0)
            if shape and kind in (1,2):
                bounds=tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp'));polygon(bounds,shape)
                self.triangles.append((int(key),bounds,shape,kind))
            else:flat[key]=p
        super().__init__(flat)

    def support(self,x,y,w,h):
        original=super().support(x,y,w,h)
        if original is not None:return original
        for oid,bounds,shape,kind in self.triangles:
            top=surface(bounds,shape,x,w)
            if top is not None and abs(y+h-top)<=1:return oid
        return None

    def step(self,body,dt,vx,gravity,terminal=160000.):
        if not self.triangles:return super().step(body,dt,vx,gravity,terminal)
        previous=dict(body);super().step(body,dt,vx,gravity,terminal)
        resolve_triangles(body,previous,self.triangles,tolerance=1.)
