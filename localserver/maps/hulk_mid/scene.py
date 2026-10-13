"""Fixed original room geometry and seeded trap configurations."""
import copy
import json
import random
from pathlib import Path
from ... import pb_layout
from tools.make_create_world import unwrap_decompress, wrap_compress

ROOT=Path(__file__).resolve().parents[3]/'maps/hulk_mid'

def trap_visual(directions,side=None):
    'Native assets encode their ports; names are not firing directions.'
    ports=set(directions)
    if len(ports)==3:
        return ('hulk.vtconduitleft' if 0 in ports else 'hulk.vtconduitright',0,False)
    if len(ports)==1:
        if side in ('left','right'):
            return 'hulk.vconduit',0,False
        return ('hulk.terminalup' if 270 in ports else 'hulk.terminaldown',0,False)
    rotation={(0,270):0,(0,90):90,(180,270):270,(90,180):180}
    return 'hulk.lconduit',rotation[tuple(sorted(ports))],True

BODY_GEOMETRY={
    'hulk.vconduit':dict(size=(96,62),offset=(-49,5),rp=(0,-17),angle=270,flip_y=False),
    'hulk.lconduit':dict(size=(96,96),offset=(70,11),rp=(0,0),angle=270,flip_y=True),
    'hulk.vtconduitleft':dict(size=(96,96),offset=(-56,10),rp=(0,0),angle=270,flip_y=False),
    'hulk.vtconduitright':dict(size=(96,96),offset=(57,-85),rp=(0,0),angle=90,flip_y=False),
}

def quarter_turn(point,angle):
    x,y=point
    return ((x,y),(-y,x),(-x,-y),(y,-x))[(int(angle)%360)//90]

def body_bounds(props):
    """Visible pipe rectangle in px relative to Properties.xmp/ymp.

    Retail drawAnimatedImage anchors rotated objects at their center, and
    non-rotated objects at bottom center (0x4d0939 / 0x4d0c60).
    """
    g=BODY_GEOMETRY[props['vid']];w,h=g['size'];rx,ry=g['rp']
    if g['flip_y']:ry=h-ry
    pts=[]
    for x,y in ((-rx,-ry),(w-rx,-ry),(w-rx,h-ry),(-rx,h-ry)):
        x,y=quarter_turn((x,y),g['angle'])
        x,y=x+g['offset'][0],y+g['offset'][1]
        if props.get('rotate'):x,y=quarter_turn((x,y),props.get('facing',0))
        pts.append((x+props['wmp']/512,y+props['hmp']/(512 if props.get('rotate') else 256)))
    return min(p[0] for p in pts),min(p[1] for p in pts),max(p[0] for p in pts),max(p[1] for p in pts)

def seat_side(props,slot,overlap_px=2):
    bx,by,ex,ey=body_bounds(props);width,height=ex-bx,ey-by
    x,y=slot['x']/256,slot['y']/256
    px=x-overlap_px-bx if slot['side']=='left' else x+96+overlap_px-ex
    py=y+(96-height)/2-by
    props.update(xmp=round(px*256),ymp=round(py*256))
    return [round((px+bx)*256),round((py+by)*256),round(width*256),round(height*256)]

def normalize(typ,value,layouts=None):
    layouts=layouts or pb_layout.default_layouts()
    if isinstance(value,list):return [normalize(typ,v,layouts) for v in value]
    if not isinstance(value,dict):return value
    fields={f['name']:f for f in layouts.types[typ]['fields']}
    out={}
    for k,v in value.items():
        name=k if k in fields else k+'_'
        if name not in fields:raise ValueError('unknown %s.%s'%(typ,k))
        child=fields[name].get('item')
        out[name]=normalize(child,v,layouts) if child else v
    return out

class HulkMidPack:
    def __init__(self,root=None):
        self.root=Path(root or ROOT)
        for name in ('scene','templates','entities','dynamic-templates'):
            setattr(self,name.replace('-','_'),json.loads((self.root/(name+'.json')).read_text(encoding='utf8')))

    def rolled_objects(self,seed):
        rng=random.Random(seed)
        omit=set(self.scene['trap_ids'])
        objects={int(k):copy.deepcopy(v) for k,v in self.templates.items() if int(k) not in omit}
        traps={}
        oid=200
        for slot in self.scene['slots']:
            side=slot['side'];x,y=slot['x'],slot['y']
            inward=0 if side=='left' else 180
            if side in ('top','bottom'):
                directions=[90 if side=='top' else 270]
                terrain=2
            else:
                if rng.random()<self.entities['traps']['inward_weight']:
                    directions=rng.choice([[270,inward],[90,inward],[270,90,inward]])
                else:directions=rng.choice([[270],[90]])
                terrain=1
            vid,facing,rotate=trap_visual(directions,side)
            props=dict(xmp=x,ymp=y,wmp=24576,hmp=24576,hitpoints=800,terraintype=terrain,
                vid=vid,soundprefix='hulk.energyconduit',facing=facing,rotate=rotate)
            visible=[x,y,24576,24576]
            if side in ('left','right'):
                visible=seat_side(props,slot,self.entities['traps'].get('mount_overlap_px',2))
            objects[oid]=props
            traps[oid]=dict(directions=directions,side=side,mount=[x,y],body_bounds_mp=visible)
            oid+=1
        return objects,traps

    def rolled_world(self,blob,seed):
        objects,_=self.rolled_objects(seed)
        layouts=pb_layout.default_layouts()
        msg=layouts.decode('CreateWorld',unwrap_decompress(blob))
        msg['worldstate_']['serverworld_']['obj_']=[dict(objid_=i,props_=normalize('Properties',p,layouts)) for i,p in objects.items()]
        return wrap_compress(layouts.encode('CreateWorld',msg))

    def props(self,vid):
        return copy.deepcopy(self.dynamic_templates[vid])
