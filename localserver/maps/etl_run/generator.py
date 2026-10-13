"""Conservative native ETL module replacement; disabled until native proof.

Every compiled donor is a complete native object/creator/reference closure.
The same-sized boundary collar and human free-space/foot interfaces must
match. No mirror, crop, synthetic platform or arbitrary corridor is created.
"""
import collections
import copy
import hashlib
import json
import random
from pathlib import Path

def digest(value):
    def canonical(v):
        if isinstance(v,dict):return {str(k):canonical(q) for k,q in v.items()}
        if isinstance(v,(list,tuple)):return [canonical(q) for q in v]
        return v
    return hashlib.sha256(json.dumps(canonical(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()
def rect(p):return tuple(p.get(k,0) for k in ('xmp','ymp','wmp','hmp'))
def overlap(a,b):return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]
def refs(v):
    if isinstance(v,list):return [r for x in v for r in refs(x)]
    if not isinstance(v,dict):return []
    return ([int(v['objid'])] if v.get('objid') else [])+[r for x in v.values() for r in refs(x)]

def relocate(props,dx,dy,mapping):
    """Preserve typed presence; absolute omitted coordinates default to zero."""
    result=copy.deepcopy(props)
    def walk(v,absolute=True,field=None):
        if isinstance(v,list):
            for q in v:walk(q,absolute,field)
        elif isinstance(v,dict):
            absolute=absolute and 'relativeto' not in v
            if absolute and (field in ('properties','to','start','sin','elliptical','points') or 'xmp' in v or 'ymp' in v):
                if dx:v.setdefault('xmp',0)
                if dy:v.setdefault('ymp',0)
            for k,q in list(v.items()):
                if k=='objid' and q:
                    if int(q) not in mapping:raise ValueError('ETL module outside reference '+str(q))
                    v[k]=mapping[int(q)]
                elif k in ('xmp','ymp') and absolute:v[k]=int(q)+(dx if k=='xmp' else dy)
                else:walk(q,absolute,k)
    walk(result,field='properties');return result

def support(objects,p):
    x,y,w,h=rect(p)
    return [i for i,q in objects.items() if q.get('terraintype') in (1,2) and abs(q.get('ymp',0)-y-h)<=1
        and x<q.get('xmp',0)+q.get('wmp',0) and x+w>q.get('xmp',0)]

def interface(objects,size,footprint=(15360,21504),step=4096):
    """Native boundary free-space relation plus exact side foot planes.

    Connectivity is swept occupancy on16px cells, not a jump proof. Its
    equality is a rejection guard; private native traversal remains pending.
    """
    ww,hh=size;w,h=footprint;nx=(ww-w)//step+1;ny=(hh-h)//step+1
    walls=[rect(p) for p in objects.values() if p.get('terraintype')==1 and not p.get('hitpoints',0)]
    blocked=set()
    for x,y,rw,rh in walls:
        for i in range(max(0,(x-w)//step+1),min(nx-1,(x+rw-1)//step)+1):
            for j in range(max(0,(y-h)//step+1),min(ny-1,(y+rh-1)//step)+1):blocked.add((i,j))
    allowed={(i,j) for i in range(max(0,nx)) for j in range(max(0,ny)) if (i,j) not in blocked}
    border=sorted(c for c in allowed if c[0] in (0,nx-1) or c[1] in (0,ny-1));regions={};number=0
    for c in sorted(allowed):
        if c in regions:continue
        number+=1;pending=[c];regions[c]=number
        while pending:
            i,j=pending.pop()
            for n in ((i-1,j),(i+1,j),(i,j-1),(i,j+1)):
                if n in allowed and n not in regions:regions[n]=number;pending.append(n)
    labels={};connected=[]
    for c in border:
        labels.setdefault(regions[c],len(labels));connected.append(labels[regions[c]])
    feet=[]
    for side,x in (('left',0),('right',ww-w)):
        for y in sorted({p.get('ymp',0) for p in objects.values() if p.get('terraintype') in (1,2)}):
            body=(x,y-h,w,h)
            if y>=h and y<=hh and not any(overlap(body,q) for q in walls) and support(objects,dict(xmp=x,ymp=y-h,wmp=w,hmp=h)):
                feet.append((side,y))
    return dict(footprint=list(footprint),border=border,connected=connected,feet=feet)

def collar(objects,size,cell=8192):
    ww,hh=size;result=[]
    for side,strip in (('left',(0,0,cell,hh)),('right',(ww-cell,0,cell,hh)),
        ('top',(0,0,ww,cell)),('bottom',(0,hh-cell,ww,cell))):
        rows=[]
        for p in objects.values():
            if p.get('vid') not in ('lab.wall','lab.ledge'):continue
            x,y,w,h=rect(p);sx,sy,sw,sh=strip
            if overlap((x,y,w,h),strip):rows.append((p['vid'],max(x,sx),max(y,sy),min(x+w,sx+sw)-max(x,sx),
                min(y+h,sy+sh)-max(y,sy),p.get('shapetype',0),p.get('terraintype',0)))
        result.append((side,sorted(rows)))
    return result

def rebuild_backing(room):
    """ETL-local5->4->1 family layer, same validated native rule as compiler."""
    from ..wall_backing import with_wall_backing
    objects=room['objects'];old=room['metadata']['wallBacking']
    for i in old['added_ids']:objects.pop(int(i),None);room['creators'].pop(int(i),None)
    protected=[];fw,fh=room['metadata']['spawn_footprint_mp']
    for p in objects.values():
        if p.get('spawn') or 'door' in p.get('vid','') or 'gate' in p.get('vid','') or p.get('vid')=='lobbyportal':
            x,y,w,h=rect(p);protected.append([x,y,w,h] if w and h else [x-fw/2,y-fh,fw,fh])
    filled,audit=with_wall_backing(objects,wall_vids={'lab.wall'},backing_vid='lab.external',transition_vid='lab.transition',
        trim_vid=None,tile_size=8192,padding=262144,layer=60,protected_rects=protected,start_id=max(objects)+1)
    for i in audit['added_ids']:filled[i]['isinternal']=False
    for i in audit['structural_ids']:filled[i].update(isinternal=False,draw=True,z=60)
    audit.update(trim_vid=None,externalFloodSeed=False,role=room['role'],
        materialRule='D native lab5-to4-to1; exterior geometry SIM; no outertrim/internal exterior seed')
    room['objects']=filled;room['metadata']['wallBacking']=audit

class EtlModuleGenerator:
    def __init__(self,root):
        self.root=Path(root);self.data=json.loads((self.root/'modules/pool.json').read_text(encoding='utf8'))
        if digest({k:v for k,v in self.data.items() if k!='content_version'})!=self.data['content_version']:
            raise ValueError('ETL native module pool fingerprint mismatch')

    def generate(self,room,seed):
        role=room.get('source_role',room['role'])
        if role not in self.data['recipes']:
            if role in ('mid','boss','bulkhead_1','bulkhead_2'):
                room['metadata']['generation']=dict(mode='protected-native-fixed-room',geometry_changed=False,
                    content_version=self.data['content_version']);return room
            raise ValueError('ETL exploration source has no verified native module recipe: '+role)
        expected=self.data['source_recipe_sha'][role]
        if digest(room)!=expected:raise ValueError('ETL native module base source fingerprint mismatch: '+role)
        rng=random.Random('etl-native-modules-v1:'+str(seed)+':'+role);slots=self.data['recipes'][role]
        if not slots:raise ValueError('ETL native module recipe is empty: '+role)
        slot=rng.choice(slots);module=self.data['modules'][rng.choice(slot['choices'])]
        removed=set(slot['remove_ids']);objects=room['objects'];creators=room['creators']

        protected=set(room['links'])|set(room['switches'])|{room['metadata']['spawn_marker_id'],room['metadata']['source_spawn_id']}
        protected.update(v['anchor_objid'] for v in room['metadata']['entry_details'].values())
        if removed&protected:raise ValueError('ETL module would remove a protected native anchor')
        for i,p in objects.items():
            if i not in removed and any(ref in removed for ref in refs(p)):raise ValueError('ETL module has an external referring object')
        if any((i in removed)!=(parent in removed) for i,parent in creators.items()):raise ValueError('ETL module creator closure broken')
        before_objects=copy.deepcopy(objects);start=max(objects)+1000;mapping={int(i):start+j+1 for j,i in enumerate(module['objects'])}
        for i in removed:objects.pop(i,None);creators.pop(i,None)
        dx,dy=slot['bbox'][:2]
        for i,p in module['objects'].items():objects[mapping[int(i)]]=relocate(p,dx,dy,mapping)
        for i,parent in module['creators'].items():creators[mapping[int(i)]]=mapping[int(parent)]
        for i,p in before_objects.items():
            if i in removed or p.get('ownertype')!=2 or not p.get('hitpoints',0):continue
            if support(before_objects,p) and not support(objects,p):raise ValueError('ETL native replacement removes NPC foot support: '+str(i))
            old=any(overlap(rect(p),rect(q)) for q in before_objects.values() if q.get('terraintype')==1)
            new=any(overlap(rect(p),rect(q)) for q in objects.values() if q.get('terraintype')==1)
            if new and not old:raise ValueError('ETL native replacement embeds retained NPC: '+str(i))
        rebuild_backing(room)
        for local,p in module['objects'].items():
            if p.get('ownertype')!=2 or not p.get('hitpoints',0) or int(local) in module.get('native_embedded_body_local_ids',[]):continue
            new=room['objects'][mapping[int(local)]]
            if any(overlap(rect(new),rect(q)) for q in room['objects'].values() if q.get('terraintype')==1):
                raise ValueError('ETL native replacement embeds donor NPC after deep backing: '+str(mapping[int(local)]))
        for p in room['objects'].values():
            if any(i not in room['objects'] for i in refs(p)):raise ValueError('ETL assembled module has dangling native reference')
        room['metadata'].update(max_objid=max(room['objects']),dynamic_id_start=max(room['objects'])+1000)
        room['metadata']['generation']=dict(mode='native-closed-module-replacement',seed=int(seed),
            content_version=self.data['content_version'],geometry_changed=slot['geometry_sha']!=module['geometry_sha'],
            replacements=[dict(slot=slot['id'],donor=module['id'],source=module['source'],mapping=mapping,
                removed_ids=sorted(removed),bbox=slot['bbox'])],
            evidence='D complete native Props/creator/MOVE closure; FIT exact collar/IC+WF occupancy and foot interfaces; SIM slot/donor choice',
            limits='One internal native module per room; no recovered Lua probabilities/branching policy; native traversal/TileMap acceptance pending')
        return room
