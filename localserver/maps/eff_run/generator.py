'Lossless EFF native support-family composition. Opt-in; no capture reads.'
import copy
import hashlib
import json
from pathlib import Path
import random

FIXED=('rock_ascent','mid','boss')
EXPLORATION=('entry','exploration1','exploration3','exploration5','approach')
MP=256

def digest(value):
    def canonical(v):
        if isinstance(v,dict):return {str(k):canonical(q) for k,q in v.items()}
        if isinstance(v,(list,tuple)):return [canonical(q) for q in v]
        return v
    return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def rect(p):return tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp'))
def overlap(a,b):return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]
def touches(a,b):return a[0]<=b[0]+b[2] and a[0]+a[2]>=b[0] and a[1]<=b[1]+b[3] and a[1]+a[3]>=b[1]

def refs(value):
    if isinstance(value,list):return [r for q in value for r in refs(q)]
    if not isinstance(value,dict):return []
    return ([int(value['objid'])] if value.get('objid') else [])+[r for q in value.values() for r in refs(q)]

def closure(objects,creators,seeds):
    """Undirected dependency closure, including incoming refs and creators."""
    result=set(seeds);changed=True
    while changed:
        changed=False
        for oid,p in objects.items():
            links=set(refs(p))
            if creators.get(oid):links.add(creators[oid])
            if oid in result or links&result:
                added={oid}|links
                if not added<=set(objects):raise ValueError('EFF missing object in dependency closure')
                if not added<=result:result|=added;changed=True
    return result

def relocate(props,dx,dy,mapping):
    result=copy.deepcopy(props)
    def walk(value,absolute=True,field='properties'):
        if isinstance(value,list):
            for q in value:walk(q,absolute,field)
        elif isinstance(value,dict):
            absolute=absolute and 'relativeto' not in value
            if absolute and (field in ('properties','to','start','sin','elliptical','points') or 'xmp' in value or 'ymp' in value):
                if dx:value.setdefault('xmp',0)
                if dy:value.setdefault('ymp',0)
            for key,q in list(value.items()):
                if key=='objid' and q:
                    if value.get('worldid',1)!=1 or int(q) not in mapping:raise ValueError('EFF outside-world/outside-family reference')
                    value[key]=mapping[int(q)]
                elif key in ('xmp','ymp') and absolute:value[key]=int(q)+(dx if key=='xmp' else dy)
                else:walk(q,absolute,key)
    walk(result);return result

def source_signature(room):
    return digest({k:room.get(k) for k in ('role','objects','creators','spawn','entries','anchors','links','switches','gate_pairs')})

def geometry_sha(objects):
    return digest(sorted((p.get('vid',''),*rect(p),p.get('terraintype'),p.get('shapetype',0),p.get('z',0))
        for p in objects.values() if p.get('terraintype') in (1,2)))

def protected(room,padding=512*MP):
    objects=room['objects'];ids=set(room['links'])|set(room.get('switches',{}))
    ids|={room['metadata']['spawn_marker_id'],room['metadata']['original_spawn_objid']}
    areas=[]
    for oid in ids:
        p=objects[oid];x,y,w,h=rect(p)
        areas.append((x-padding,y-padding,w+2*padding,h+2*padding))
    for x,y in room['entries'].values():areas.append((x-padding,y-padding,60*MP+2*padding,84*MP+2*padding))
    return ids,areas

def placement_check(room,slot,module,dx,dy,objects=None):
    """Necessary local safety only. Directed jump proofs are compiled offline."""
    objects=room['objects'] if objects is None else objects
    remove=set(slot['remove_ids']);protected_ids,areas=protected(room)
    if remove&protected_ids:return 'protected-reference'
    if closure(objects,room.get('creators',{}),remove)!=remove:return 'reference-closure'
    original=objects[slot['support_id']];donor=module['objects'][str(module['support_id'])]
    ox,oy,ow,oh=rect(original);nx,ny,nw,nh=rect(donor);new=(nx+dx,ny+dy,nw,nh)
    if (donor.get('vid'),donor.get('terraintype'),donor.get('shapetype',0),nh)!=(original.get('vid'),original.get('terraintype'),original.get('shapetype',0),oh):return 'material-or-plane'
    if new[1]!=oy or new[0]>ox or new[0]+nw<ox+ow or nw<=ow:return 'old-standing-interval'
    terrain=[rect(p) for p in objects.values() if p.get('terraintype') in (1,2) and p.get('boundsworld',True)]
    bounds=(min(q[0] for q in terrain),min(q[1] for q in terrain),max(q[0]+q[2] for q in terrain),max(q[1]+q[3] for q in terrain))
    if new[0]<bounds[0] or new[1]<bounds[1] or new[0]+nw>bounds[2] or new[1]+nh>bounds[3]:return 'native-camera-boundary'
    if any(overlap(new,a) for a in areas):return 'protected-anchor-corridor'
    for oid,p in objects.items():
        if oid in remove or p.get('terraintype') not in (1,2):continue
        if touches(new,rect(p)) and not touches(rect(original),rect(p)):return 'new-native-terrain-contact'
        if overlap(new,rect(p)) and not overlap(rect(original),rect(p)):return 'new-terrain-overlap'

    for oid,p in module['objects'].items():
        if int(oid)==module['support_id'] or not p.get('vid') or not p.get('hitpoints',0):continue
        x,y,w,h=rect(p);body=(x+dx,y+dy,w,h)
        if any(overlap(body,rect(q)) for key,q in objects.items() if key not in remove and q.get('terraintype')==1):return 'native-family-body-overlap'
    return None

def sync_world(room):
    from ..jungle_common.scene import normalize
    room['world']['worldstate_']['serverworld_']['obj_']=[dict(objid_=oid,props_=normalize('Properties',p),
        **({'creatorid_':room['creators'][oid]} if oid in room['creators'] else {})) for oid,p in sorted(room['objects'].items())]
    room['metadata'].update(max_objid=max(room['objects']),dynamic_id_start=max(room['objects'])+1000)

class EffModuleGenerator:
    def __init__(self,root):
        self.root=Path(root);self.pool=json.loads((self.root/'modules/pool.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.pool.items() if k!='content_version'})!=self.pool['content_version']:raise ValueError('EFF module pool fingerprint mismatch')
        self.component_versions=dict(pool=self.pool['content_version'],
            generator=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            scene_adapter=hashlib.sha256(Path(__file__).with_name('scene.py').read_bytes()).hexdigest())
    def generate(self,room,seed):
        room=copy.deepcopy(room);role=room['role']
        components=dict(self.component_versions,source_recipe=source_signature(room))
        content_version=digest(components)
        if role in FIXED:
            room['metadata']['generation']=dict(mode='protected-fixed-mode' if role=='rock_ascent' else 'protected-fixed-encounter',
                geometry_changed=False,content_version=content_version,component_versions=components,pool_version=self.pool['content_version']);return room
        if source_signature(room)!=self.pool['source_signatures'][role]:raise ValueError('EFF native source recipe changed: '+role)
        before=geometry_sha(room['objects']);rng=random.Random('eff-native-support-v1:'+role+':'+str(int(seed)&0xffffffff))
        slots=list(self.pool['slots'].get(role,[]));rng.shuffle(slots);used=set();changes=[];rejections=[];envelopes=[];extensions=[]

        for slot in slots:
            if len(changes)>=4:break
            if used&set(slot['remove_ids']):continue
            if changes and rng.random()<.35:continue
            choices=list(slot['choices']);rng.shuffle(choices)
            for choice in choices:
                module=self.pool['modules'][choice['module']];dx,dy=choice['translation_mp']
                error=placement_check(room,slot,module,dx,dy)
                ox,oy,ow,oh=rect(room['objects'][slot['support_id']]);dp=module['objects'][str(module['support_id'])]
                nx,ny,nw,nh=rect(dp);nx+=dx;ny+=dy
                extra=([(nx,ny,ox-nx,nh)] if nx<ox else [])+([(ox+ow,ny,nx+nw-ox-ow,nh)] if nx+nw>ox+ow else [])
                planned_envelopes=choice['proof']['witness_envelopes_mp']
                if not error and (any(overlap(e,b) for e in extra for b in envelopes) or
                        any(overlap(e,b) for e in extensions for b in planned_envelopes)):
                    error='combined-directed-witness-overlap'
                if error:rejections.append(dict(support=slot['support_id'],module=choice['module'],reason=error));continue
                start=max(room['objects'])+1000;mapping={int(oid):start+i+1 for i,oid in enumerate(sorted(module['objects'],key=int))}
                for oid in slot['remove_ids']:room['objects'].pop(oid);room['creators'].pop(oid,None)
                for oid,p in module['objects'].items():room['objects'][mapping[int(oid)]]=relocate(p,dx,dy,mapping)
                for oid,parent in module.get('creators',{}).items():room['creators'][mapping[int(oid)]]=mapping[int(parent)]
                used|=set(slot['remove_ids'])
                extensions.extend(extra);envelopes.extend(planned_envelopes)
                changes.append(dict(support=slot['support_id'],module=choice['module'],removed_ids=slot['remove_ids'],
                    added_ids=sorted(mapping.values()),id_mapping=mapping,translation_mp=[dx,dy],proof=choice['proof']))
                break
        after=geometry_sha(room['objects']);sync_world(room)
        room['metadata']['generation']=dict(mode='native-support-family-combination',content_version=content_version,component_versions=components,
            pool_version=self.pool['content_version'],seed=int(seed)&0xffffffff,
            before_geometry_sha=before,geometry_sha=after,geometry_changed=before!=after,replacements=changes,rejections=rejections,
            unchanged_reason=None if changes else 'No compiled compatible complete native family for this source; original retained explicitly',
            evidence='D full native platform/Props and closed parent/reference family; FIT support assignment; SIM grammar/weights',
            limits='Local retained support and directed fitted jump tests; no whole-route, dynamic combat or native four-class posterior')
        return room
