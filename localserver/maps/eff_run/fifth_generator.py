"""Explicit candidate: whole EFF room5 path/hive modules; default unconnected.

The sole FIT裁分 is reference-free shape0 horizontal ground on32px boundaries.
Every other source property and the complete cap250 are retained. Unknown
module ordering is SIM; static/native loading is not normal traversal proof.
"""
import copy
import hashlib
import json
from pathlib import Path
import random

from .generator import digest,geometry_sha,rect,refs,relocate

TILE=8192
ROLE='exploration5'
CAP_ID=250
FOOTPRINTS=((7680,17920),(6656,15872),(15360,21504),(7680,20480))

def physical_geometry_sha(objects):
    """Partition-independent actual32px occupation/material + full triangles."""
    cells=set();other=[]
    for p in objects.values():
        if p.get('terraintype') not in (1,2):continue
        x,y,w,h=rect(p);material=digest({k:v for k,v in p.items() if k not in ('xmp','ymp','wmp','hmp')})
        if not p.get('shapetype',0) and all(v%TILE==0 for v in (x,y,w,h)):
            cells.update((a,b,material) for a in range(x//TILE,(x+w)//TILE) for b in range(y//TILE,(y+h)//TILE))
        else:other.append((x,y,w,h,material))
    return digest(dict(cells=sorted(cells),nonrect_or_nongrid=sorted(other)))

def signature(room):
    wp=copy.deepcopy(room['world']['worldprops_']);wp.pop('seed_',None)
    return digest(dict(worldprops=wp,**{k:room.get(k) for k in
        ('role','objects','creators','spawn','entries','anchors','links','switches','gate_pairs')},

        entry_support=room['metadata'].get('entry_support')))

def actual_rect(objects,oid,seen=()):
    if oid in seen:raise ValueError('Fifth relative geometry cycle')
    p=objects[oid];x,y,w,h=rect(p);m=p.get('move') or {};parent=m.get('relativeto',{}).get('objid')
    if parent:
        if set(m)-{'relativeto','to'} or m.get('to',{}).get('dt',0):raise ValueError('Fifth unknown moving relative geometry')
        px,py,_,_=actual_rect(objects,parent,seen+(oid,));to=m.get('to',{})
        x,y=px+to.get('xmp',0),py+to.get('ymp',0)
    return x,y,w,h

def body_overlap(p,body):
    x,y,w,h=rect(p);bx,by,bw,bh=body
    if bx+bw<=x or bx>=x+w or by+bh<=y or by>=y+h:return False
    if not p.get('shapetype',0):return bx<x+w and bx+bw>x and by<y+h and by+bh>y

    from fractions import Fraction as F
    vertices={1:((x,y+h),(x+w,y),(x+w,y+h)),2:((x,y),(x+w,y+h),(x,y+h)),
        3:((x,y),(x+w,y),(x,y+h)),4:((x,y),(x+w,y),(x+w,y+h))}
    shape=p['shapetype']
    if shape not in vertices:raise ValueError('Unknown EFF native shape')
    polygon=[(F(a),F(b)) for a,b in vertices[shape]]
    for axis,bound,greater in ((0,bx,True),(0,bx+bw,False),(1,by,True),(1,by+bh,False)):
        if not polygon:return False
        output=[];old=polygon[-1]
        for point in polygon:
            a=old[axis]>=bound if greater else old[axis]<=bound
            b=point[axis]>=bound if greater else point[axis]<=bound
            if a!=b:
                t=(F(bound)-old[axis])/(point[axis]-old[axis]);output.append(tuple(old[i]+t*(point[i]-old[i]) for i in (0,1)))
            if b:output.append(point)
            old=point
        polygon=output
    return bool(polygon) and abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(polygon,polygon[1:]+polygon[:1])))>0

def supported(objects,body):
    x,y,w,h=body;feet=y+h;spans=[]
    for p in objects.values():
        px,py,pw,ph=rect(p)
        if p.get('terraintype') in (1,2) and not p.get('shapetype',0) and py==feet and px<x+w and px+pw>x:
            spans.append((max(x,px),min(x+w,px+pw)))
    end=x
    for a,b in sorted(spans):
        if a>end:return False
        end=max(end,b)
    return end>=x+w

def validate(room,original=None,ports=()):
    objects=room['objects'];ids=set(objects)
    for oid,p in objects.items():
        if any(i and i not in ids for i in refs(p)):raise ValueError('Fifth lost object reference '+str(oid))
    if not set(room['creators'])<=ids or not set(room['creators'].values())<=ids:raise ValueError('Fifth lost creator')
    if not set(room['links'])<=ids:raise ValueError('Fifth lost actual door')
    for x,y in ports:
        for w,h in FOOTPRINTS:
            body=(x-w/2,y-h,w,h)
            if any(p.get('terraintype')==1 and body_overlap(p,body) for p in objects.values()):raise ValueError('Fifth actual port body blocked')
            if not supported(objects,body):raise ValueError('Fifth full-foot port unsupported')
    if original is not None:
        for field in ('spawn','entries','anchors','links','gate_pairs','switches'):
            if room.get(field)!=original.get(field):raise ValueError('Fifth changed protected navigation '+field)
        if room['metadata'].get('entry_support')!=original['metadata'].get('entry_support'):raise ValueError('Fifth changed arrival support metadata')
        if objects[CAP_ID]!=original['objects'][CAP_ID]:raise ValueError('Fifth global native cap changed')
    return True

def validate_parts(objects,records,modules):
    """No source primitive/body may cross another column or the fixed shell."""
    for record in records:
        module=modules[record['module']];dx,dy=record['translation_mp']
        left=module['left_x']+dx;right=left+module['width'];ids=set(record['object_id_map'].values())
        for oid in ids:
            p=objects[oid];x,y,w,h=actual_rect(objects,oid)
            if x<left or x+w>right:raise ValueError('Fifth module object leaves its full column')
            if p.get('terraintype') not in (1,2) and p.get('ownertype')!=2:continue
            for other,q in objects.items():
                if other not in ids and q.get('terraintype')==1 and body_overlap(q,(x,y,w,h)):
                    raise ValueError('Fifth terrain/NPC overlaps foreign module or fixed shell')

class EffFifthGenerator:
    'Source-backed fifth-room composition, also usable on a raw room pack.'
    def __init__(self,root=None):
        self.root=Path(root or Path(__file__).resolve().parents[3]/'maps/eff_run')
        self.data=json.loads((self.root/'fifth-modules/pool.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.data.items() if k!='content_version'})!=self.data['content_version']:raise ValueError('Fifth pool fingerprint differs')
        config_path=self.root/'fifth-modules/enabled.json'
        config=json.loads(config_path.read_text(encoding='utf8'))
        if config.get('content_version')!=self.data['content_version']:raise ValueError('Fifth enabled/pool version differs')
        self.default_enabled=config.get('enabled') is True
        self.versions=dict(pool=self.data['content_version'],adapter=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            activation_config=hashlib.sha256(config_path.read_bytes()).hexdigest(),
            scene=hashlib.sha256(Path(__file__).with_name('scene.py').read_bytes()).hexdigest(),
            geometry_helper=hashlib.sha256(Path(__file__).with_name('generator.py').read_bytes()).hexdigest())
    def generate(self,room,seed,order=None):
        original=copy.deepcopy(room)
        if room['role']!=ROLE or signature(room)!=self.data['source_signature']:raise ValueError('Fifth requires exact original room5 source')
        allowed=self.data['allowed_orders'];seed=int(seed)&0x7fffffff
        if order is None:order=random.Random('eff-fifth-path-v1:'+str(seed)).choice(allowed)
        order=list(order)
        if order not in allowed:raise ValueError('Fifth ordering lacks compiled static interface')
        room=copy.deepcopy(room);objects={int(i):copy.deepcopy(p) for i,p in self.data['fixed_objects'].items()}
        creators={int(i):int(p) for i,p in self.data['fixed_creators'].items()};next_id=max(original['objects'])+1000
        mappings={};records=[];cursor=self.data['region'][0];foot=self.data['entry_foot_y'];ports=[(cursor,foot)]
        for key in order:
            module=self.data['modules'][key];mapping={int(i):next_id+n for n,i in enumerate(sorted(module['objects'],key=int))};next_id+=len(mapping)
            dx,dy=cursor-module['left_x'],foot-module['left_foot_y']
            for oid,p in module['objects'].items():objects[mapping[int(oid)]]=relocate(p,dx,dy,mapping)
            for oid,parent in module['creators'].items():creators[mapping[int(oid)]]=mapping[int(parent)]
            mappings.update({str(i):mapping[int(i)] for i in module['objects']})
            records.append(dict(module=key,translation_mp=[dx,dy],object_id_map=mapping,
                source_ids=module['source_ids'],compiled_fragment_ids=module['compiled_source_ids'],
                object_origin=module['object_origin'],hive_ids=module['hive_ids'],npc_ids=module['npc_ids']))
            cursor+=module['width'];foot+=module['delta_y'];ports.append((cursor,foot))
        if cursor!=self.data['region'][1] or foot!=self.data['exit_foot_y']:raise ValueError('Fifth sum width/dy breaks fixed exit')
        room['objects']=objects;room['creators']=creators
        validate(room,original,ports)
        validate_parts(objects,records,self.data['modules'])
        from .scene import EffRunPack
        EffRunPack._set_objects(room)
        room['world']['worldprops_']['seed_']=seed;room['metadata'].update(runtime_seed=seed,
            max_objid=max(objects),dynamic_id_start=max(objects)+1000,object_count=len(objects))
        versions=dict(self.versions,source_recipe=signature(original));geometry=physical_geometry_sha(objects)
        room['metadata']['generation']=dict(mode='candidate-native-fifth-path-composition',seed=seed,order=order,
            modules=records,ports_mp=ports,geometry_sha=geometry,geometry_changed=geometry!=physical_geometry_sha(original['objects']),
            primitive_geometry_sha=geometry_sha(objects),geometry_hash_scope='Actual tile occupation/material union + full triangles; partition-only source order stays unchanged',
            content_version=digest(versions),component_versions=versions,pool_version=self.data['content_version'],
            source_object_count=len(original['objects']),tile_partition=self.data['tile_partition'],
            default_enabled=self.default_enabled,native_verified=False,manual_verification_pending=True,
            evidence='D native path/Props/doors and observed internal joints; FIT32px exact-union cut and undersidehive; SIM ordering',
            limits='Source-backed full local paths with necessary four-class body/mobility checks; dynamic combat/native tiles/normal compulsory route not yet proved')
        room['variant']=original['variant']+'-fifth-'+geometry[:12]
        return room
