"""Native EUB connection assembly; only compiled module resources at runtime.

The original skeleton and fixed fragments remain intact. Complete native
corridor nodes are inserted at observed compatible faces; no wall is resized,
cropped, mirrored or invented to seal an uncertain interface.
"""
import copy
import hashlib
import json
import random
from pathlib import Path

TILE=16384
PROTECTED_ROLES={'wave_chamber','laser_2','laser_8','mid','boss'}
FOOTPRINTS=((10240,20480),(15360,21504))
BODY_VIDS={'dumb.'+v for v in ('trooper','sniper','rocketeer','erocketeer','heavy','light','elight','towersegment','robosweeper','tank','shieldturret')}

def digest(value):
    def canonical(item):
        if isinstance(item,dict):return {str(k):canonical(v) for k,v in item.items()}
        if isinstance(item,(list,tuple)):return [canonical(v) for v in item]
        return item
    return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def rect(p):return [p.get(k,0) for k in ('xmp','ymp','wmp','hmp')]

def overlaps(a,b):return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]

def refs(value):
    if isinstance(value,list):return [r for item in value for r in refs(item)]
    if not isinstance(value,dict):return []
    return ([int(value['objid'])] if 'objid' in value else [])+[r for item in value.values() for r in refs(item)]

def relocate(props,dx,dy,mapping):
    """Typed absolute coordinates translate; relative offsets stay relative.

    A missing protobuf coordinate is zero. Materialize it only if its axis
    actually moves, preserving the source presence bits on unchanged axes.
    """
    result=copy.deepcopy(props)
    def walk(value,absolute=True,field='properties'):
        if isinstance(value,list):
            for item in value:walk(item,absolute,field)
        elif isinstance(value,dict):
            absolute=absolute and 'relativeto' not in value
            if absolute and (field in ('properties','to','start','sin','elliptical','points') or 'xmp' in value or 'ymp' in value):
                if dx:value.setdefault('xmp',0)
                if dy:value.setdefault('ymp',0)
            for key,item in list(value.items()):
                if key=='objid':
                    if int(item) not in mapping:raise ValueError('EUB module reference outside closure: '+str(item))
                    value[key]=mapping[int(item)]
                elif key in ('xmp','ymp') and absolute:value[key]=int(item)+(dx if key=='xmp' else dy)
                else:walk(item,absolute,key)
    walk(result)
    return result

def geometry_sha(objects):
    rows=sorted((p.get('vid',''),*rect(p),p.get('shapetype',0),p.get('terraintype',0))
                for p in objects.values() if p.get('terraintype') in (1,2) and p.get('ownertype')!=2)
    return digest(rows)

def face(objects,plane,side,y0,y1):
    """Full native64px collision/material collar, with merged exact bands."""
    left,right=(plane-TILE,plane) if side=='left' else (plane,plane+TILE)
    rows=[]
    for p in objects.values():
        if p.get('terraintype') not in (1,2):continue
        x,y,w,h=rect(p)
        if x<right and x+w>left and y<y1 and y+h>y0:
            tag=(p.get('vid',''),p['terraintype'],p.get('shapetype',0),p.get('z',0),bool(p.get('isinternal',False)))
            rows.append((max(x,left)-left,max(y,y0)-y0,min(x+w,right)-left,min(y+h,y1)-y0,*tag))
    out=[]
    for tag in sorted({row[4:] for row in rows}):
        group=[row for row in rows if row[4:]==tag];ys=sorted({v for row in group for v in (row[1],row[3])});bands=[]
        for a,b in zip(ys,ys[1:]):
            spans=[]
            for row in sorted(group):
                if row[1]<=a and row[3]>=b:
                    if spans and row[0]<=spans[-1][1]:spans[-1][1]=max(spans[-1][1],row[2])
                    else:spans.append([row[0],row[2]])
            if bands and bands[-1][2]==spans and bands[-1][1]==a:bands[-1][1]=b
            else:bands.append([a,b,spans])
        for a,b,spans in bands:out.extend((x,a,xx,b,*tag) for x,xx in spans)
    return sorted(out)

def supports(objects,p,oid=None):
    x,y,w,h=rect(p);feet=y+h
    return [i for i,q in objects.items() if i!=oid and q.get('terraintype') in (1,2)
            and abs(q.get('ymp',0)-feet)<=1 and x<q.get('xmp',0)+q.get('wmp',0) and x+w>q.get('xmp',0)]

def arrival_safe(objects,point,support_id):
    support=objects.get(support_id)
    if support is None:return False
    cx=point[0]+FOOTPRINTS[0][0]/2;feet=point[1]+FOOTPRINTS[0][1];sx,sy,sw,_=rect(support)
    for w,h in FOOTPRINTS:
        if abs(feet-sy)>1 or not sx<=cx-w/2 or not cx+w/2<=sx+sw:return False
        if any(overlaps([cx-w/2,feet-h,w,h],rect(p)) for p in objects.values() if p.get('terraintype')==1):return False
    return True

def navigation_groups(room):
    """Do not split a native switch-door group or either pad assembly pair."""
    groups=[];objects=room['objects']
    for oid,switch in room.get('switches',{}).items():groups.append({int(oid),*map(int,switch['targets'])})
    for pair in room['mechanics'].get('telepad_pairs',[]):
        pads={pair['source'],pair.get('destination_pad',pair['destination']+1)}
        groups.append({i for pad in pads for i in (pad-1,pad,pad+1) if i in objects})
    for info in room['metadata']['entry_support'].values():groups.append({info['anchor_objid'],info['support_objid']})
    return groups

def validate(room,original=None):
    objects=room['objects'];creators=room.get('creators',{})
    for oid,p in objects.items():
        if not set(refs(p)).issubset(objects):raise ValueError('Dangling EUB reference: '+str(oid))
    if not set(creators).issubset(objects) or not set(creators.values()).issubset(objects):raise ValueError('Dangling EUB creator')
    for key,info in room['metadata']['entry_support'].items():
        if not arrival_safe(objects,room['entries'][key],info['support_objid']):raise ValueError('Unsafe EUB entry '+key)
    marker=objects[room['metadata']['spawn_marker_id']]
    if [marker.get('xmp',0)-FOOTPRINTS[0][0]/2,marker.get('ymp',0)-FOOTPRINTS[0][1]]!=room['spawn']:raise ValueError('EUB marker/spawn disagree')
    for oid in room['links']:
        if oid not in objects:raise ValueError('EUB link lost native body')
    for group in navigation_groups(room):
        if not group.issubset(objects):raise ValueError('EUB mechanism graph not closed')
    if original:
        old=original['objects'];new_ids=set(objects)-set(old)
        for oid,p in objects.items():
            if p.get('vid') not in BODY_VIDS:continue
            if oid in old and supports(old,old[oid],oid) and not supports(objects,p,oid):raise ValueError('Retained NPC lost feet support '+str(oid))
            before=old.get(oid)
            for sid,q in objects.items():
                if q.get('terraintype')!=1 or not overlaps(rect(p),rect(q)):continue
                if oid in new_ids or before is None or sid not in old or not overlaps(rect(before),rect(old[sid])):
                    raise ValueError('New permanent wall intersects body '+str((oid,sid)))
    return True

class EubModuleGenerator:
    def __init__(self,root,pool_file='pool.json'):
        self.root=Path(root);self.data=json.loads((self.root/'modules'/pool_file).read_text(encoding='utf8'))
        semantic={k:v for k,v in self.data.items() if k!='content_version'}
        if digest(semantic)!=self.data['content_version']:raise ValueError('EUB native module pool semantic fingerprint mismatch')
        base=json.loads((self.root/'scene.json').read_text(encoding='utf8'))
        if base['content_version']!=self.data['base_content_version']:raise ValueError('EUB native module pool/base version mismatch')
        for template in self.data['templates'].values():
            for key in ('objects','creators'):template[key]={int(k):v for k,v in template.get(key,{}).items()}

    def generate(self,room,seed,*,rule_index=None,count=None,template_id=None):
        original=copy.deepcopy(room);room=copy.deepcopy(room)
        if room['role'] in PROTECTED_ROLES:
            room['metadata']['generation']=dict(mode='protected-native',reason='Fixed encounter/laser/elevator role',content_version=self.data['content_version'])
            return room
        rules=self.data['recipes'].get(room['variant'],[])
        if not rules:raise ValueError('EUB room has no verified native connection recipe: '+room['variant'])
        seed=int(seed)&0xffffffff
        rng=random.Random('eub-native-connections-v1:%s:%s'%(room['variant'],seed))
        rule=rules[rng.randrange(len(rules)) if rule_index is None else rule_index]
        repeats=rng.choice(rule['allowed_counts']) if count is None else int(count)
        if repeats not in rule['allowed_counts']:raise ValueError('EUB module repetition not verified')

        lengths=sorted({self.data['templates'][t]['width_mp'] for t in rule['templates']})
        length=rng.choice(lengths)
        chosen=template_id or rng.choice([t for t in rule['templates'] if self.data['templates'][t]['width_mp']==length])
        if chosen not in rule['templates']:raise ValueError('EUB native interface/template pair not verified')
        template=self.data['templates'][chosen];width=template['width_mp'];dx=rule['direction']*width*repeats
        objects=room['objects'];creators=room['creators'];moving=set(rule['moving_ids']);identity={oid:oid for oid in objects}
        if not moving.issubset(objects):raise ValueError('EUB moving source object missing')
        for group in navigation_groups(room)+[set(g) for g in rule['protected_fragments']]:
            if group&moving and not group.issubset(moving):raise ValueError('EUB cut splits native protected group')
        if any((oid in moving)!=(r in moving) for oid,p in objects.items() for r in refs(p)):raise ValueError('EUB cut crosses reference')
        if any((oid in moving)!=(parent in moving) for oid,parent in creators.items()):raise ValueError('EUB cut crosses creator')
        for oid in moving:objects[oid]=relocate(objects[oid],dx,0,identity)
        for name,info in room['metadata']['entry_support'].items():
            if info['anchor_objid'] in moving:
                for field in ('entries','anchors'):room[field][name][0]+=dx
                info['native_anchor_point_mp'][0]+=dx
        if room['metadata']['spawn_marker_id'] in moving:raise ValueError('EUB default arrival must remain stationary')
        for oid,switch in room.get('switches',{}).items():
            if oid not in moving:continue
            for proof in switch.get('proofs',[]):
                proof.setdefault('source_door_moves',copy.deepcopy(proof.get('door_moves',{})))
                proof['door_moves']={key:relocate(dict(move=value),dx,0,identity)['move'] for key,value in proof.get('door_moves',{}).items()}
                proof['assembly_translation_mp']=[dx,0]
        for key,spec in room['mechanics'].get('spawner_specs',{}).items():
            if int(key) in moving and spec.get('spawn_left_range_mp'):
                spec.setdefault('source_spawn_left_range_mp',list(spec['spawn_left_range_mp']))
                spec['spawn_left_range_mp']=[x+dx for x in spec['spawn_left_range_mp']]
        next_id=max(objects)+1000;bridges=[]
        for index in range(repeats):
            x=rule['plane_mp']+index*width if rule['direction']>0 else rule['plane_mp']-(index+1)*width
            y=rule['port_top_mp']-template['port_top_mp'];mapping={oid:next_id+i for i,oid in enumerate(sorted(template['objects']))};next_id+=len(mapping)
            for oid,p in template['objects'].items():objects[mapping[oid]]=relocate(p,x-template['origin'][0],y-template['origin'][1],mapping)
            for oid,parent in template['creators'].items():creators[mapping[oid]]=mapping[parent]
            for name,specs in template.get('device_specs',{}).items():
                for oid,spec in specs.items():
                    spec=copy.deepcopy(spec)
                    if spec.get('spawn_left_range_mp'):spec['spawn_left_range_mp']=[v+x-template['origin'][0] for v in spec['spawn_left_range_mp']]
                    if spec.get('spawn_feet_mp') is not None:spec['spawn_feet_mp']+=y-template['origin'][1]
                    room['mechanics'].setdefault(name,{})[str(mapping[int(oid)])]=spec
            bridges.append(dict(template=chosen,source=template['source'],object_id_map=mapping,translation_mp=[x-template['origin'][0],y-template['origin'][1]]))
        terrain=[rect(p) for p in objects.values() if p.get('terraintype') in (1,2)]
        metadata=room['metadata'];metadata.update(object_count=len(objects),max_objid=max(objects),dynamic_id_start=max(objects)+1000,
            bounds_mp=[min(p[0] for p in terrain),min(p[1] for p in terrain),max(p[0]+p[2] for p in terrain),max(p[1]+p[3] for p in terrain)])
        geometry=geometry_sha(objects)
        adapter=digest({name:hashlib.sha256((Path(__file__).parent/name).read_bytes()).hexdigest() for name in ('generator.py','scene.py')})
        versions=dict(pool=self.data['content_version'],base=self.data['base_content_version'],adapter=adapter)
        metadata['generation']=dict(mode='native-interface-assembly',seed=int(seed),base_variant=original['variant'],
            content_version=digest(versions),component_versions=versions,geometry_sha=geometry,rule=rule['id'],
            moving_source_ids=sorted(moving),translation_mp=[dx,0],repeats=repeats,bridges=bridges,
            evidence='D full native objects and observed collars; USER-confirmed full-run source; FIT interface/cut; SIM equal-length-class selection/repetition',
            limits='Native room skeleton retained with new native connector nodes and lengths; original module grammar, branching distribution and RNG weights unknown.')
        room['variant']+='-assembled-'+geometry[:12]
        room['world']['worldprops_']['seed_']=seed
        validate(room,original)
        from .scene import EubRunPack
        EubRunPack._set_objects(room['world'],objects,room)
        return room
