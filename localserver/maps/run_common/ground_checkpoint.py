'Pure joint-checkpoint validation and restoration plans.'
import copy
import hashlib
import json
import math

from ... import pb_layout
from .player_inventory import merge_player_inventory
from .inventory_handoff import SCOPE_FIELDS

SCHEMA='full-run-ground-joint-v1'
GROUND_SCHEMA='full-run-ground-v1'
ABI='win32-gcc48-ground-v1'
NATIVE_BOOLS=('isPrivate','hasGravity','xBlocked','yBlocked','crouching','running',
              'instantWalkDecel','createSoundGlobal')
NATIVE_INTS=('offsetDT','xVel','yVel','attXVel','moveXVel','moveYVel','walkAccel',
             'airAccel','gravityAccel','redamageTime','lastXmp','lastYmp','lastDamageT')

def integer(value,label,minimum=-2147483648,maximum=2147483647):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('invalid '+label)
    return value

def wire_size(value):
    return len(json.dumps(value,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii'))

def message(kind,value,layouts,depth=0):
    """Validate real PB fields without inventing absent/default values."""
    if not isinstance(value,dict) or depth>12:raise ValueError('invalid '+kind+' message')
    fields={f['name'].rstrip('_'):f for f in layouts.types[kind]['fields']};result={}
    for key,item in value.items():
        if not isinstance(key,str):raise ValueError('invalid '+kind+' field name')
        name=key.rstrip('_');f=fields.get(name)
        if f is None or name+'_' in result:raise ValueError('unknown/duplicate '+kind+' field '+key)
        tag=f['kind']
        if tag in ('int32','sint32','uint32'):
            item=integer(item,name,0 if tag=='uint32' else -2147483648,4294967295 if tag=='uint32' else 2147483647)
        elif tag=='bool':
            if type(item) is not bool:raise ValueError('invalid boolean '+name)
        elif tag=='stringPointer':
            if not isinstance(item,str) or len(item.encode('utf8'))>4096:raise ValueError('invalid text '+name)
        elif tag=='repeatedString':
            if not isinstance(item,list) or len(item)>128 or any(not isinstance(s,str) or len(s.encode('utf8'))>4096 for s in item):raise ValueError('invalid repeated text '+name)
        elif tag=='messagePointer':item=message(f['item'],item,layouts,depth+1)
        elif tag=='repeatedMessage':
            if not isinstance(item,list) or len(item)>128:raise ValueError('invalid repeated message '+name)
            item=[message(f['item'],row,layouts,depth+1) for row in item]
        else:raise ValueError('unsupported '+kind+' field kind '+tag)
        result[name+'_']=copy.deepcopy(item)
    return result

def item(value,layouts):
    encoded=message('ItemProperties',value,layouts)
    integer(encoded.get('type_'),'ground item type',1)
    valid={v for k,v in layouts.enums['Stat'].items() if k.startswith('STAT_') and v!=0}
    if any(type(b.get('stat_')) is not int or b['stat_'] not in valid for b in encoded.get('statboosts_',[])):
        raise ValueError('invalid ground item stat')
    return encoded

def objkey(value,camel=False,empty=False):
    names=('worldId','objId') if camel else ('worldid','objid')
    if not isinstance(value,dict) or set(value)!=set(names):raise ValueError('invalid ground object key')
    key=tuple(integer(value[k],k,0 if empty else 1,4294967295) for k in names)
    if empty and bool(key[0])!=bool(key[1]):raise ValueError('half-empty native object key')
    return key

def property_references(kind,value,layouts,path=()):
    """Walk actual PB message types, preserving key and scalar presence.

    MOVE.relativeto, MOVE.lerp.target and both reaction.teleport fields are
    covered by the layout rather than an incomplete hand-maintained list.
    """
    if kind=='ObjKeyMessage':
        key=(value.get('worldid_',0),value.get('objid_',0))
        if bool(key[0])!=bool(key[1]):raise ValueError('half-empty protobuf object reference')
        yield path,key
        return
    fields={f['name']:f for f in layouts.types[kind]['fields']}
    for name,child in value.items():
        field=fields[name]
        if field['kind']=='messagePointer':
            yield from property_references(field['item'],child,layouts,path+(name,))
        elif field['kind']=='repeatedMessage':
            for index,row in enumerate(child):
                yield from property_references(field['item'],row,layouts,path+(name,index))

def validate_ground(joint,layouts):
    ground=joint.get('ground');quality=joint.get('quality') or {}
    if quality.get('stable') is not True or quality.get('abi',{}).get('name')!=ABI:raise ValueError('unsupported/unstable joint ABI')
    if not isinstance(ground,dict) or ground.get('schema')!=GROUND_SCHEMA or ground.get('ok') is not True:raise ValueError('ground read not complete')
    q=ground.get('quality') or {}
    if (q.get('stable') is not True or q.get('passes')!=2 or q.get('native_clocks_equal') is not True or
            q.get('set_key_ptr_class_live_equal') is not True):raise ValueError('ground double read not stable')
    declared=integer(q.get('local_declared_count'),'local object count',0,4096)
    if type(q.get('local_traversed_count')) is not int or q['local_traversed_count']!=declared:raise ValueError('local map count mismatch')
    count=integer(ground.get('count'),'ground count',0,64);rows=ground.get('objects')
    if not isinstance(rows,list) or len(rows)!=count or count>declared:raise ValueError('incomplete ground object list')
    keys=set();pointers=set();boosts=0;result=[]
    for source in rows:
        if not isinstance(source,dict):raise ValueError('invalid ground record')
        row=copy.deepcopy(source);key=objkey(row.get('key'))
        if key[0]!=joint['scope']['worldid'] or key[1]==joint['scope']['objid']:raise ValueError('ground object is foreign/the player')
        pointer=integer(row.get('ptr'),'native pointer',65536,2147418111)
        if key in keys or pointer in pointers:raise ValueError('duplicate native ground identity')
        keys.add(key);pointers.add(pointer)
        integer(row.get('observed_generation'),'observed object generation',1,9223372036854775807)
        kind=row.get('rtti')
        if kind not in ('Loot','Chest'):raise ValueError('unsupported ground class')
        props=message('Properties',row.get('props'),layouts)
        present=row.get('lifetime_present')
        if type(present) is not bool or present!=('lifetime_' in props):raise ValueError('lifetime presence mismatch')
        lifetime=row.get('remaining_lifetime_ms')
        if present:
            if integer(lifetime,'remaining lifetime')!=props['lifetime_']:raise ValueError('remaining lifetime mismatch')
        elif lifetime is not None:raise ValueError('absent lifetime has a fabricated value')
        native=row.get('native')
        if not isinstance(native,dict):raise ValueError('missing ground native state')
        for field in NATIVE_INTS:integer(native.get(field),'native '+field)
        for field in NATIVE_BOOLS:
            if type(native.get(field)) is not bool:raise ValueError('invalid native '+field)
        for field in ('subWorld','action'):integer(native.get(field),'native '+field,0,4294967295)
        integer(native.get('damagedCount'),'native damaged count',0,4096)
        if not isinstance(native.get('createSound'),str) or len(native['createSound'].encode('utf8'))>4096:raise ValueError('invalid native createSound')
        objkey(native.get('attached'),camel=True,empty=True)
        if kind=='Loot':
            if 'chest' in row or not isinstance(row.get('loot'),dict):raise ValueError('invalid Loot material')
            material=row['loot'];encoded=item(material.get('itemprops'),layouts)
            integer(material.get('untilAutoApply'),'untilAutoApply')
            integer(native.get('itemDesc'),'item description pointer',65536,2147418111)
            boosts+=len(encoded.get('statboosts_',[]))
        else:
            if 'loot' in row or not isinstance(row.get('chest'),dict):raise ValueError('invalid Chest material')
            material=row['chest'];items=material.get('items');angles=material.get('angles')
            if not isinstance(items,list) or len(items)>128 or not isinstance(angles,list) or len(angles)>128:raise ValueError('invalid Chest contents/angles')
            for v in items:boosts+=len(item(v,layouts).get('statboosts_',[]))
            for v in angles:integer(v,'Chest angle')
            integer(material.get('pos'),'Chest position');integer(material.get('untilThrow'),'untilThrow')
        result.append(row)
    if boosts+sum(len(i.get('statboosts',[])) for i in joint['inventory']['items'])>4096:raise ValueError('joint affixes exceed bound')
    return result

class GroundCheckpointStore:
    """Whole-set replace only; identical material is never an identity key."""
    def __init__(self):self.checkpoint=None;self.revision=0

    def replace_joint(self,joint,issued,*,expected_scope,request_id,attempt,now,max_age_s=.75,layouts=None):
        report=dict(ready=False,errors=[])
        try:
            layouts=layouts or pb_layout.default_layouts()
            if not isinstance(joint,dict) or joint.get('schema')!=SCHEMA or joint.get('ok') is not True:raise ValueError('joint read not complete')
            if wire_size(joint)>60000:raise ValueError('joint ASCII wire size exceeds bound')
            if joint.get('scope')!=expected_scope or set(expected_scope)!=set(SCOPE_FIELDS):raise ValueError('joint scope mismatch')
            integer(request_id,'expected request',1,9223372036854775807);integer(attempt,'expected attempt',1)
            if type(joint.get('request_id')) is not int or joint['request_id']!=request_id or type(joint.get('request_attempt')) is not int or joint['request_attempt']!=attempt:raise ValueError('joint nonce/attempt mismatch')
            for field in ('native_server_t','native_local_t'):integer(joint.get(field),field,0)
            inventory=joint.get('inventory')
            if not isinstance(inventory,dict):raise ValueError('missing joint inventory')
            for field in ('scope','request_id','request_attempt','sampled_at','native_server_t'):
                if type(inventory.get(field)) is not type(joint.get(field)) or inventory.get(field)!=joint.get(field):raise ValueError('inventory and ground observation mismatch: '+field)
            candidate,merged=merge_player_inventory(issued,inventory,now=now,max_age_s=max_age_s,
                expected_scope=expected_scope,expected_request_id=request_id,layouts=layouts)
            if not merged['ready_for_handoff']:raise ValueError('joint inventory rejected: '+str(merged['errors']))
            objects=validate_ground(joint,layouts)
            revision=self.revision+1
            checkpoint=dict(schema='full-run-ground-checkpoint-v1',revision=revision,scope=copy.deepcopy(expected_scope),
                sampled_at=joint['sampled_at'],request_id=request_id,request_attempt=attempt,
                native_server_t=joint['native_server_t'],native_local_t=joint['native_local_t'],objects=objects,
                inventory=copy.deepcopy(inventory),create_player=candidate,
                lifetime_policy='SIM pause remaining native time while room unoccupied',
                boundary_scope='Validated observation; caller must prove final egress before committing a transition')

            self.checkpoint=checkpoint;self.revision=revision
            report.update(ready=True,revision=revision,count=len(objects))
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError) as error:report['errors'].append(str(error))
        return copy.deepcopy(self.checkpoint),report

    def prepare_restore(self,new_scope,*,foreign_keys,layouts=None):
        """Data plan only. A verified constructor-before-update entry is required."""
        if self.checkpoint is None:raise ValueError('no ground checkpoint')
        if not isinstance(new_scope,dict) or set(new_scope)!=set(SCOPE_FIELDS):raise ValueError('incomplete destination scope')
        for field in ('room_token','worldid','objid'):integer(new_scope[field],field,1,4294967295)
        integer(new_scope['room_seed'],'room seed',0,4294967295)
        integer(new_scope['charid'],'character ID',0,4294967295)
        integer(new_scope['player_type'],'character type',0)
        for field in ('map','room_seed','charid','player_type'):
            if new_scope[field]!=self.checkpoint['scope'][field]:raise ValueError('restore is for a different room/character')
        if new_scope['room_token']<=self.checkpoint['scope']['room_token']:raise ValueError('restore needs a new visit token')
        layouts=layouts or pb_layout.default_layouts()
        foreign_keys={objkey(dict(zip(('worldid','objid'),key))) for key in foreign_keys}
        rows=self.checkpoint['objects'];mapping={}
        for row in rows:
            identity=dict(checkpoint_revision=self.revision,scope=self.checkpoint['scope'],key=row['key'],
                ptr=row['ptr'],generation=row['observed_generation'])
            mapping[objkey(row['key'])]=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(',',':')).encode('utf8')).hexdigest()
        def reference(key):
            if key==(0,0):return None
            if key==(self.checkpoint['scope']['worldid'],self.checkpoint['scope']['objid']):return dict(kind='player')
            if key in mapping:return dict(kind='record',record_key=mapping[key])
            if key in foreign_keys:return dict(kind='foreign',worldid=key[0],objid=key[1])
            raise ValueError('unresolved restored attachment/object reference')

        records=[];dependencies={}
        for row in rows:
            native=row['native'];kind=row['rtti'];props=message('Properties',row['props'],layouts)
            if native['action'] or native['damagedCount']:raise ValueError('unsupported Action/contact ledger restoration')
            if native['lastDamageT']!=-1:raise ValueError('unsupported absolute damage clock restoration')
            if props.get('activateworldid_',0) not in (0,self.checkpoint['scope']['worldid']):
                raise ValueError('unsupported activation world reference')
            if props.get('activateworldid_',0) and new_scope['worldid']!=self.checkpoint['scope']['worldid']:
                raise ValueError('activation world reference requires audited world remap')
            if kind=='Chest' and (len(row['chest']['angles'])!=len(row['chest']['items']) or not -1<=row['chest']['pos']<=len(row['chest']['items'])):raise ValueError('unsupported Chest progress restoration')
            attached=objkey(native['attached'],camel=True,empty=True)
            attached_reference=reference(attached)
            items=[row['loot']['itemprops']] if kind=='Loot' else row['chest']['items']
            encoded_items=[item(v,layouts) for v in items]
            references=[dict(path=['source_properties_']+list(path),reference=reference(key))
                for path,key in property_references('Properties',props,layouts)]
            for index,material in enumerate(encoded_items):
                references.extend(dict(path=['itemprops_',index]+list(path),reference=reference(key))
                    for path,key in property_references('ItemProperties',material,layouts))
            record_key=mapping[objkey(row['key'])]
            dependencies[record_key]={ref['record_key'] for ref in
                [attached_reference]+[r['reference'] for r in references] if ref and ref['kind']=='record'}
            records.append(dict(record_key=record_key,rtti=kind,
                construction=dict(source_properties_=props,itemprops_=[item(v,layouts) for v in items],
                    anchor_policy='Requires audited per-kind constructor adapter; these are not CreateLoot wire props'),
                authoritative=copy.deepcopy(row),attached_reference=attached_reference,property_references=references))
        order=[];remaining={key:set(value) for key,value in dependencies.items()}
        while remaining:
            ready=sorted(key for key,value in remaining.items() if not value)
            if not ready:raise ValueError('cyclic restored object references require an audited deferred binder')
            order.extend(ready)
            for key in ready:del remaining[key]
            for value in remaining.values():value.difference_update(ready)
        return dict(schema='full-run-ground-restore-plan-v1',checkpoint_revision=self.revision,source_scope=copy.deepcopy(self.checkpoint['scope']),
            scope=copy.deepcopy(new_scope),records=records,construction_order=order,requires_before_first_update=True,
            constructor_calls_per_record=1,ready_for_native_execution=False,
            lifetime_semantics='NativeObject.update VA0x445c14: lifetime presence enables signed dt>=raw-value expiration; zero/negative is not infinite',
            limitation='Plan has no audited native materializer or egress seal; never replay it as ordinary CreateLoot')
