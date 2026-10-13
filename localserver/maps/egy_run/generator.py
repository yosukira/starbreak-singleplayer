'Seeded composition of compiled native EGY modules.'
import collections
import copy
import hashlib
import json
import random
from pathlib import Path

ADAPTER_VERSION=hashlib.sha256(b''.join((Path(__file__).parent/name).read_bytes()
                            for name in ('generator.py','scene.py','arrivals.py'))).hexdigest()

def geometry_signature(objects):
    return sorted((p.get('vid',''),p.get('xmp',0),p.get('ymp',0),p.get('wmp',0),p.get('hmp',0),
                   p.get('shapetype',0),p.get('terraintype',0))
                  for p in objects.values() if p.get('terraintype') in (1,2))

def geometry_sha(objects):
    return hashlib.sha256(json.dumps(geometry_signature(objects),separators=(',',':')).encode()).hexdigest()

def refs(value):
    if isinstance(value,list):
        return [ref for item in value for ref in refs(item)]
    if not isinstance(value,dict):
        return []
    return ([int(value['objid'])] if 'objid' in value else []) + [ref for item in value.values() for ref in refs(item)]

def relocate(props, dx, dy, mapping):
    """Translate world coordinates; relative motion offsets stay relative."""
    result=copy.deepcopy(props)

    def walk(value, translate=True, field=None):
        if isinstance(value,list):
            for item in value:
                walk(item,translate,field)
        elif isinstance(value,dict):
            local=translate and 'relativeto' not in value
            if local and (field in ('properties','to','start','sin','elliptical','points') or 'xmp' in value or 'ymp' in value):
                if dx:
                    value.setdefault('xmp',0)
                if dy:
                    value.setdefault('ymp',0)
            for key,item in list(value.items()):
                if key == 'objid':
                    if int(item) not in mapping:
                        raise ValueError('module object reference is outside module: %s' % item)
                    value[key]=mapping[int(item)]
                elif key in ('xmp','ymp') and local:
                    value[key]=int(item)+(dx if key=='xmp' else dy)
                else:
                    walk(item,local,key)

    walk(result,field='properties')
    return result

def free_port_connectivity(objects, box, footprint=(10240,20480), step=4096):
    """Human-sized free-space check; this is not a claim of jump simulation."""
    x,y,xx,yy=box;w,h=footprint

    solids=[p for p in objects.values() if p.get('terraintype')==1
            and p.get('vid') not in ('hulk.barrier','hulk.ffield') and not p.get('hitpoints',0)]
    xs=list(range(x,xx-w+1,step));ys=list(range(y,yy-h+1,step))
    if not xs or not ys:
        return dict(ports=[],connected=[],method='40x80px swept occupancy, 16px grid; no jump physics')
    blocked=set()
    for p in solids:
        px,py=p.get('xmp',0),p.get('ymp',0)
        ia=max(0,(px-w-x)//step+1);ib=min(len(xs)-1,(px+p.get('wmp',0)-1-x)//step)
        ja=max(0,(py-h-y)//step+1);jb=min(len(ys)-1,(py+p.get('hmp',0)-1-y)//step)
        blocked.update((i,j) for i in range(ia,ib+1) for j in range(ja,jb+1))
    allowed={(i,j) for i in range(len(xs)) for j in range(len(ys)) if (i,j) not in blocked}

    ports=sorted((i,j) for i,j in allowed if i in (0,len(xs)-1) or j in (0,len(ys)-1))
    regions={};number=0
    for cell in sorted(allowed):
        if cell in regions:
            continue
        number+=1;pending=[cell];regions[cell]=number
        while pending:
            i,j=pending.pop()
            for neighbour in ((i-1,j),(i+1,j),(i,j-1),(i,j+1)):
                if neighbour in allowed and neighbour not in regions:
                    regions[neighbour]=number;pending.append(neighbour)

    labels={};connected=[]
    for cell in ports:
        region=regions[cell]
        labels.setdefault(region,len(labels))
        connected.append(labels[region])
    return dict(ports=ports,connected=connected,method='40x80px swept occupancy, 16px grid; no jump physics')

class EgyModuleGenerator:
    def __init__(self,root,pool_file=None,connection_file=None,native_library=None):
        path=root/'modules'/(pool_file or 'pool.json')
        self.data=json.loads(path.read_text(encoding='utf8'))
        self.modules=self.data['modules']
        enabled=root/'modules/connections-enabled.json'
        connection_config=json.loads(enabled.read_text(encoding='utf8')) if enabled.exists() else {}
        if connection_file is False:

            connection_config=dict(enabled=False,use_native_library=False)
        elif connection_file is not None:
            candidate=EgyConnectionComposer(root,connection_file)
            connection_config=dict(enabled=True,connections_file=connection_file,content_version=candidate.data['content_version'],
                                   use_native_library=bool(native_library))
        elif native_library is not None:
            connection_config['use_native_library']=bool(native_library)
        self.connections=(EgyConnectionComposer(root,connection_config.get('connections_file'))
                          if connection_config.get('enabled') else None)
        if self.connections and connection_config.get('content_version') != self.connections.data['content_version']:
            raise ValueError('EGY published connection manifest and data versions differ')
        self.connection_config=connection_config

    def generate(self,room,seed):
        if room['role'] in ('mid','boss'):
            return room
        if room['role'] in self.data.get('disabled_roles',{}) and not self.connections:
            room['metadata']['generation']=dict(mode='capture-native',seed=int(seed),
                geometry_sha=geometry_sha(room['objects']),geometry_changed=False,
                replacements=[],base_variant=room['variant'],
                reason=self.data['disabled_roles'][room['role']],content_version=self.data['content_version'])
            return room
        slots=self.data['recipes'].get(room['variant']) or []
        if room['role'] in self.data.get('disabled_roles',{}) and self.connections:
            slots=[]
        protection=self.connections.data if self.connections else self.data
        fixed=set(protection.get('fixed_terminal_ids',{}).get(room['variant'],[]))
        slots=[slot for slot in slots if not set(slot['remove_ids']+slot.get('preserve_support_ids',[]))&fixed]
        if self.connections and not self.connections.data['recipes'].get(room['variant']):
            raise ValueError('EGY selected native skeleton has no verified connection recipe')
        if not slots and not self.connections:
            raise ValueError('no verified module recipe for EGY room '+room['variant'])
        rng=random.Random('egy-modules-v1:%s:%s' % (room['variant'],int(seed)))
        base_variant=room['variant']
        original_sha=geometry_sha(room['objects'])
        offsets={}
        if self.connections:
            room,offsets=self.connections.compose(room,int(seed))
        objects=room['objects'];creators=room.get('creators',{})
        protected=set(room.get('links',{}))|set(room.get('switches',{}))
        protected.add(int(room['metadata']['spawn_marker_id']))
        for detail in room['metadata']['entry_support'].values():
            protected.update((int(detail['anchor_objid']),int(detail['support_objid'])))
        next_id=max(objects)+1000
        replacements=[]
        for slot in slots:
            module_id=rng.choice(slot['choices'])
            module=self.modules[module_id]
            removed=set(slot['remove_ids'])
            if removed & protected:
                raise ValueError('EGY module recipe removes a protected navigation/spawn object')
            if not removed.issubset(objects):
                raise ValueError('EGY module recipe overlaps or references missing base objects')
            if any(ref in removed for oid,p in objects.items() if oid not in removed for ref in refs(p)):
                raise ValueError('EGY module removal has an external object dependency')
            if any(int(creator) in removed for oid,creator in creators.items() if int(oid) not in removed):
                raise ValueError('EGY module removal has an external creator dependency')
            shift=next((offsets[oid] for oid in slot['remove_ids'] if oid in offsets),(0,0))
            dx=slot['origin'][0]+shift[0]-module['origin'][0];dy=slot['origin'][1]+shift[1]-module['origin'][1]
            kept=set(slot.get('preserve_support_ids',[]))
            mapping={};reused={}
            for oid,props in sorted(module['objects'].items(),key=lambda row:int(row[0])):
                coordinate=copy.deepcopy(props)
                coordinate['xmp']=coordinate.get('xmp',0)+dx;coordinate['ymp']=coordinate.get('ymp',0)+dy
                equivalent=next((prior for prior in sorted(kept) if
                    geometry_signature({0:objects[prior]})==geometry_signature({0:coordinate})),None)
                if equivalent is not None:
                    mapping[int(oid)]=equivalent;reused[int(oid)]=equivalent
                else:
                    mapping[int(oid)]=next_id;next_id+=1
            added={mapping[int(oid)]:relocate(p,dx,dy,mapping) for oid,p in module['objects'].items() if int(oid) not in reused}
            for props in added.values():
                if props.get('vid') == 'hulk.wall' and props.get('terraintype') == 1:
                    props.update(isinternal=True,boundsworld=True)
                    props.setdefault('draw',True)
                    props.setdefault('z',60)
            for oid in removed:
                objects.pop(oid)
                creators.pop(oid,None)
            objects.update(added)
            for oid,creator in module.get('creators',{}).items():
                creators[mapping[int(oid)]]=mapping[int(creator)]
            replacements.append(dict(slot=slot['id'],module=module_id,source=module['source'],
                source_object_ids=sorted(mapping),object_id_map=mapping,translation_mp=[dx,dy],
                geometry_sha=geometry_sha(added),evidence=slot['evidence'],
                baseline_npc_count=slot.get('baseline_npc_count',0),native_variant_npc_count=module.get('npc_count',0),
                checked_footprints_mp=slot['checked_footprints_mp'],
                retained_support_ids=sorted(kept),reused_native_support_map=reused,
                normalization=module['normalization']))
        final_sha=geometry_sha(objects)
        if final_sha == original_sha:

            changed=False
        else:
            changed=True
        room['metadata'].update(max_objid=max(objects),dynamic_id_start=max(objects)+1000,object_count=len(objects))
        component_versions=dict(pool=self.data['content_version'],adapter=ADAPTER_VERSION,
            connections=self.connections.data['content_version'] if self.connections else None,
            library=room['metadata'].get('library_content_version'))
        composite=hashlib.sha256(json.dumps(component_versions,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        mode=('native-interface-assembly+internal' if slots else 'native-interface-assembly') if self.connections else 'native-module-replacement'
        room['metadata']['generation']=dict(mode=mode,seed=int(seed),
            content_version=composite,component_versions=component_versions,base_variant=base_variant,
            replacements=replacements,geometry_sha=final_sha,base_geometry_sha=original_sha,
            geometry_changed=changed,evidence='D native module objects; FIT boundaries; SIM seeded composition',
            limits='Native room skeleton retained; whole connected components can move and complete native connectors vary length. Internal replacement applies only where compiled slots exist. Original server RNG and full expanding grammar unknown.')
        room['variant'] += '-generated-'+final_sha[:12]
        return room

def native_face_profile(objects,plane,side,y0,y1,width=8192):
    """Exact tile collision collar at an observed native x interface."""
    left,right=(plane-width,plane) if side=='left' else (plane,plane+width)
    rows=[]
    for p in objects.values():
        if p.get('vid','') not in ('hulk.wall','hulk.ledge','nil','') or p.get('terraintype') not in (1,2):
            continue
        x,y=p.get('xmp',0),p.get('ymp',0);xx,yy=x+p.get('wmp',0),y+p.get('hmp',0)
        if x<right and xx>left and y<y1 and yy>y0:
            rows.append((max(x,left)-left,max(y,y0)-y0,min(xx,right)-left,min(yy,y1)-y0,
                         p.get('vid',''),p.get('terraintype'),p.get('shapetype',0)))
    canonical=[]
    tags={tuple(row[4:]) for row in rows}
    for tag in sorted(tags):
        group=[row for row in rows if tuple(row[4:])==tag]
        ys=sorted({value for row in group for value in (row[1],row[3])})
        bands=[]
        for a,b in zip(ys,ys[1:]):
            spans=[]
            for row in sorted(group):
                if row[1]<=a and row[3]>=b:
                    if spans and row[0]<=spans[-1][1]:
                        spans[-1][1]=max(spans[-1][1],row[2])
                    else:
                        spans.append([row[0],row[2]])
            if bands and bands[-1][2]==spans and bands[-1][1]==a:
                bands[-1][1]=b
            else:
                bands.append([a,b,spans])
        for a,b,spans in bands:
            canonical.extend((x,a,xx,b,*tag) for x,xx in spans)
    return sorted(canonical)

class EgyConnectionComposer:
    """Insert native connector modules along verified graph-cut interfaces."""
    def __init__(self,root,connections_file=None):
        self.data=json.loads((root/'modules'/(connections_file or 'connections.json')).read_text(encoding='utf8'))

    def compose(self,room,seed,rule_index=None,count=None):
        rng=random.Random('egy-connections-v1:%s:%s' % (room['variant'],seed))
        rules=self.data['recipes'].get(room['variant'])
        if not rules:
            raise ValueError('No verified native connection recipe for '+room['variant'])
        objects=room['objects'];creators=room.get('creators',{})
        original_count=len(objects);offsets={};records=[]
        next_id=max(objects)+1000

        def translate(ids,dx):
            identity={oid:oid for oid in objects}
            for oid in ids:
                objects[oid]=relocate(objects[oid],dx,0,identity)
                old=offsets.get(oid,(0,0));offsets[oid]=(old[0]+dx,old[1])
            for name,detail in room['metadata']['entry_support'].items():
                if detail['anchor_objid'] in ids:
                    room['entries'][name][0]+=dx
                    room['anchors'][name][0]+=dx
            if room['metadata']['spawn_marker_id'] in ids:
                room['spawn'][0]+=dx

        def add_template(template,origin):
            nonlocal next_id
            mapping={int(oid):next_id+i for i,oid in enumerate(sorted(template['objects'],key=int))}
            next_id+=len(mapping)
            dx=origin[0]-template['origin'][0];dy=origin[1]-template['origin'][1]
            added={mapping[int(oid)]:relocate(props,dx,dy,mapping) for oid,props in template['objects'].items()}
            for p in added.values():
                if p.get('vid')=='hulk.wall':
                    p.update(isinternal=True,boundsworld=True);p.setdefault('draw',True);p.setdefault('z',60)
            objects.update(added)
            for oid,parent in template.get('creators',{}).items():
                creators[mapping[int(oid)]]=mapping[int(parent)]
            return mapping

        chosen=(rules if rules[0]['kind']=='terminal-cap' and rules[0].get('apply_together',True) else
                [rules[rng.randrange(len(rules)) if rule_index is None else rule_index]])
        for rule in chosen:
            allowed=rule.get('allowed_counts',[1,2,3])
            repeats=rng.choice(allowed) if count is None else int(count)
            if repeats not in allowed:
                raise ValueError('Native connector length lacks a verified recipe')
            if repeats<1:
                raise ValueError('Native connection repeat count must be positive')
            width=rule['stride_mp'];dx=rule['direction']*width*repeats
            moving=set(rule['moving_ids'])
            fixed=set(self.data.get('fixed_terminal_ids',{}).get(room['variant'],[]))
            if room['role']=='hall' and fixed&moving:
                raise ValueError('Native connection would move the fixed hall core')
            if fixed&moving and not fixed.issubset(moving):
                raise ValueError('Native connection would split a fixed terminal fragment')
            if not moving.issubset(objects):
                raise ValueError('Native graph-cut source objects missing')
            if any((oid in moving)!=(int(parent) in moving) for oid,parent in creators.items()):
                raise ValueError('Native graph cut crosses a creator edge')
            if any((oid in moving)!=(ref in moving) for oid,p in objects.items() for ref in refs(p)):
                raise ValueError('Native graph cut crosses an object-reference edge')
            translate(moving,dx)
            bridges=[]
            for port in rule['ports']:
                template=self.data['templates'][port['template']]
                step=template['width_mp']
                total=width*repeats//step
                for index in range(total):
                    x=rule['plane_mp']+index*step if rule['direction']>0 else rule['plane_mp']-(index+1)*step
                    origin=[x,port['top_mp']-template['port_top_mp']]
                    mapping=add_template(template,origin)
                    bridges.append(dict(template=port['template'],origin_mp=origin,source=template['source'],object_id_map=mapping))
            records.append(dict(rule=rule['id'],kind=rule['kind'],plane_mp=rule['plane_mp'],
                direction=rule['direction'],distance_mp=abs(dx),repeats=repeats,moving_source_ids=sorted(moving),
                ports=rule['ports'],bridges=bridges,
                evidence='D native modules and interface collars; FIT graph cut; SIM repeat count and composition'))
        room['metadata']['connections']=dict(mode='native-interface-assembly',content_version=self.data['content_version'],
            assemblies=records,objects_before=original_count,objects_after=len(objects),
            limits='Original room connectivity is retained while native connector nodes and lengths change; original server expanding RNG remains unknown.')
        return room,offsets
