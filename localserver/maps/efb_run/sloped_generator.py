"""Configurable-preview XY composition of complete native EFB hill/nest modules.

Only observed ordered64px polygon/material contacts are allowed. Native
module dy and seam dy accumulate; no crop, mirror, resize, invented slope or
new cnest spawning rule. Static compatibility is not a player-route proof.
"""
import copy
from fractions import Fraction as F
import hashlib
import json
from pathlib import Path
import random

from .generator import base_version,arrival_groups,digest,rect,refs,relocate,geometry_sha

ROLES=frozenset(('sector_2','final_approach'))
FOOTPRINTS=((10240,20480),(15360,21504))

def source_signature(room):
    wp=copy.deepcopy(room['world']['worldprops_']);wp.pop('seed_',None)
    return digest(dict(worldprops=wp,**{k:room[k] for k in ('role','variant','objects','creators','links','switches','entries','spawn')},
        entry_support=room['metadata']['entry_support'],spawn_marker=room['metadata']['spawn_marker_id']))

def polygon(p):
    x,y,w,h=map(F,rect(p));shape=p.get('shapetype',0)
    values={0:((x,y),(x+w,y),(x+w,y+h),(x,y+h)),1:((x,y+h),(x+w,y),(x+w,y+h)),
        2:((x,y),(x+w,y+h),(x,y+h)),3:((x,y),(x+w,y),(x,y+h)),4:((x,y),(x+w,y),(x+w,y+h))}
    if shape not in values:raise ValueError('Unknown EFB native shape')
    return list(values[shape])

def polygon_overlap(p,body):
    poly=polygon(p);x,y,w,h=body
    for axis,bound,greater in ((0,x,True),(0,x+w,False),(1,y,True),(1,y+h,False)):
        if not poly:return False
        out=[];previous=poly[-1]
        for point in poly:
            a=previous[axis]>=bound if greater else previous[axis]<=bound
            b=point[axis]>=bound if greater else point[axis]<=bound
            if a!=b:
                t=(F(bound)-previous[axis])/(point[axis]-previous[axis])
                out.append(tuple(previous[i]+t*(point[i]-previous[i]) for i in (0,1)))
            if b:out.append(point)
            previous=point
        poly=out
    return abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in zip(poly,poly[1:]+poly[:1])))>0

def support_measure(objects,body):
    x,y,w,h=body;feet=y+h;spans=[]
    for p in objects.values():
        if p.get('terraintype') not in (1,2) or p.get('shapetype',0) or p.get('move'):continue
        px,py,pw,ph=rect(p)
        if abs(py-feet)<=1 and px<x+w and px+pw>x:spans.append((max(x,px),min(x+w,px+pw)))
    end=x;total=0;center=False
    for a,b in sorted(spans):
        total+=max(0,b-max(a,end));end=max(end,b)
        center|=a<=x+w/2<=b
    return total,center

def flat_support(objects,body):return support_measure(objects,body)[0]>=body[2]

def validate(room,original=None):
    objects=room['objects'];ids=set(objects)
    for oid,p in objects.items():
        if not {i for i in refs(p) if i}.issubset(ids):raise ValueError('XY module lost native object reference '+str(oid))
    if not set(room['creators']).issubset(ids) or not set(room['creators'].values()).issubset(ids):raise ValueError('XY module creator closure lost')
    if not set(room['links']).issubset(ids):raise ValueError('XY module lost native navigation body')
    for name,point in room['entries'].items():
        for w,h in FOOTPRINTS:
            body=(point[0]+(FOOTPRINTS[0][0]-w)//2,point[1]+FOOTPRINTS[0][1]-h,w,h)
            if any(p.get('terraintype')==1 and polygon_overlap(p,body) for p in objects.values()):raise ValueError('XY arrival blocked by exact native polygon: '+name)
            measure,center=support_measure(objects,body)
            if not center:raise ValueError('XY arrival lost native feet-center support: '+name)
            if original is not None:
                p=original['entries'][name]
                before=(p[0]+(FOOTPRINTS[0][0]-w)//2,p[1]+FOOTPRINTS[0][1]-h,w,h)
                if measure<support_measure(original['objects'],before)[0]:raise ValueError('XY arrival lost original support extent: '+name)
    marker=objects[room['metadata']['spawn_marker_id']]
    if (marker['xmp'],marker['ymp'])!=(room['spawn'][0]+FOOTPRINTS[0][0]/2,room['spawn'][1]+FOOTPRINTS[0][1]):raise ValueError('XY marker/spawn disagrees')
    return True

class EfbSlopedGenerator:
    """Call on unchanged source recipes; scene controls preview activation.

    Forced API: generate(room,seed,rule_id=...,module_ids=[...],contact_ids=[...]).
    contact_ids has N+1 observed joints: prefix->first, between, last->suffix.
    Omit forced values for deterministic bounded local SIM count/selection.
    """
    def __init__(self,root,pool_file='sloped-pool.json'):
        self.root=Path(root);self.data=json.loads((self.root/'modules'/pool_file).read_text(encoding='utf8'))
        if digest({k:v for k,v in self.data.items() if k!='content_version'})!=self.data['content_version']:raise ValueError('XY native pool fingerprint mismatch')
        if base_version(self.root)!=self.data['base_content_version']:raise ValueError('XY source native content version changed')
        for module in self.data['templates'].values():
            for field in ('objects','creators'):module[field]={int(k):v for k,v in module[field].items()}
        directory=Path(__file__).parent
        self.versions=dict(pool=self.data['content_version'],base=self.data['base_content_version'],
            adapter=digest({str(p.relative_to(directory.parent)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                (Path(__file__),directory/'scene.py',directory/'generator.py',directory.parent/'eub_run/generator.py')}))

    def contacts(self,left,right,role):
        return [key for key,c in sorted(self.data['contacts'].items()) if c['role']==role and
            c['left_signature']==left and c['right_signature']==right]

    def plans(self,rule,count,forced=None):
        role=rule['role'];templates=self.data['templates'];result=[]
        def visit(signature,modules,joints):
            if len(modules)==count:
                for contact in self.contacts(signature,rule['right_signature'],role):result.append((modules,joints+[contact]))
                return
            choices=[forced[len(modules)]] if forced is not None else rule['templates']
            for key in choices:
                if key not in rule['templates']:continue
                module=templates[key]
                for contact in self.contacts(signature,module['left']['signature'],role):
                    visit(module['right']['signature'],modules+[key],joints+[contact])
        visit(rule['left_signature'],[],[])
        return result

    def generate(self,room,seed,*,rule_id=None,module_ids=None,contact_ids=None,count=None):
        original=copy.deepcopy(room);room=copy.deepcopy(room);seed=int(seed)&0x7fffffff
        components=dict(self.versions,source_recipe=source_signature(room));version=digest(components)
        rules=self.data['recipes'].get(room['variant'],[])
        if room['role'] in ROLES and (room['variant'] not in self.data['source_signatures'] or
                source_signature(room)!=self.data['source_signatures'][room['variant']]):
            raise ValueError('XY requires an unchanged confirmed original source recipe')
        if room['role'] not in ROLES or not rules:
            if rule_id is not None or module_ids is not None or contact_ids is not None or count is not None:raise ValueError('Forced XY composition requested for protected/unproved source')
            room['metadata']['generation']=dict(mode='retained-native-xy-skeleton',geometry_changed=False,
                reason='Fixed encounter/tower or no whole native clean port; no fake crop',content_version=version,component_versions=components)
            return room
        if source_signature(room)!=self.data['source_signatures'][room['variant']]:raise ValueError('XY source recipe changed')
        rng=random.Random('efb-native-xy-v1:'+room['variant']+':'+str(seed))
        if rule_id is not None:
            found=[r for r in rules if r['id']==rule_id]
            if len(found)!=1:raise ValueError('Unknown/ambiguous forced XY rule')
            rule=found[0]
        else:rule=rng.choice(rules)
        if module_ids is not None:
            module_ids=list(module_ids);count=len(module_ids)
        if count is None:count=rng.choice(rule['allowed_counts'])
        if type(count) is not int or count not in rule['allowed_counts']:raise ValueError('XY count not compiled/proved')
        plans=self.plans(rule,count,module_ids)
        if contact_ids is not None:plans=[p for p in plans if p[1]==list(contact_ids)]
        if not plans:raise ValueError('No observed complete native joint sequence; refuse unknown XY interface')
        module_ids,contact_ids=rng.choice(plans);modules=[self.data['templates'][k] for k in module_ids]
        contacts=[self.data['contacts'][k] for k in contact_ids]
        moving=set(rule['moving_ids']);objects=room['objects'];identity={oid:oid for oid in objects}
        if room['metadata']['spawn_marker_id'] in moving or not moving.issubset(objects):raise ValueError('XY would move source initial spawn or lose suffix')
        if any((i in moving)!=(r in moving) for i,p in objects.items() for r in refs(p) if r) or any((i in moving)!=(p in moving) for i,p in room['creators'].items()):raise ValueError('XY suffix splits native reference/creator group')
        if any(group&moving and not group.issubset(moving) for group in arrival_groups(room)):raise ValueError('XY suffix splits native arrival/support group')
        total_width=sum(m['width_mp'] for m in modules)
        exit_y=rule['left_foot_y_mp']
        for index,module in enumerate(modules):exit_y+=contacts[index]['seam_dy_mp']+module['dy_mp']
        shift_y=exit_y+contacts[-1]['seam_dy_mp']-rule['right_foot_y_mp']
        for oid in moving:objects[oid]=relocate(objects[oid],total_width,shift_y,identity)
        for name,proof in room['metadata']['entry_support'].items():
            if int(proof['anchor_objid']) in moving:
                room['entries'][name][0]+=total_width;room['entries'][name][1]+=shift_y
                proof['nativeFeet'][0]+=total_width;proof['nativeFeet'][1]+=shift_y
        next_id=max(objects)+1000;inserted=[];cursor=rule['plane_mp'];foot=rule['left_foot_y_mp']
        for index,(key,module) in enumerate(zip(module_ids,modules)):
            foot+=contacts[index]['seam_dy_mp'];dx=cursor-module['left_plane_mp'];dy=foot-module['left_port_y_mp']
            mapping={oid:next_id+n for n,oid in enumerate(sorted(module['objects']))};next_id+=len(mapping)
            for oid,p in module['objects'].items():objects[mapping[oid]]=relocate(p,dx,dy,mapping)
            for oid,parent in module['creators'].items():room['creators'][mapping[oid]]=mapping[parent]
            inserted.append(dict(template=key,source=module['source'],object_id_map=mapping,translation_mp=[dx,dy],native_dy_mp=module['dy_mp']))
            cursor+=module['width_mp'];foot+=module['dy_mp']
        room['metadata']['generation']=dict(mode='native-ice-xy-sequence',seed=seed,base_variant=original['variant'],rule=rule['id'],
            modules=inserted,contact_ids=contact_ids,moving_source_ids=sorted(moving),translation_mp=[total_width,shift_y],
            geometry_changed=geometry_sha(objects)!=geometry_sha(original['objects']),geometry_sha=geometry_sha(objects),
            content_version=version,component_versions=components,pool_version=self.data['content_version'],
            static_interfaces='D exact shape0..4/material64px ordered contact pairs; native module/seam dy preserved',
            internal_route_proven=False,cnest_spawning='UNRESOLVED; original terrain only, no invented creatures',
            limits='Whole native slope/stair/nest nodes; full-pool normal routes await user playtest. Tower clean cuts unresolved; original complete tower prefabs retained. Count/weights SIM')
        room['metadata'].update(max_objid=max(objects),dynamic_id_start=max(objects)+1000,
            structural_wall_ids=[i for i,p in objects.items() if p.get('vid')=='ice.wall'])
        room['variant']+='-xy-'+room['metadata']['generation']['geometry_sha'][:12]
        room['world']['worldprops_']['seed_']=seed
        validate(room,original)
        from .scene import EfbRunPack
        EfbRunPack._set_objects(room)
        return room
