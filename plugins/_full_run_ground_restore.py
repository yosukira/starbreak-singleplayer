"""Pure, bounded production-candidate restore protocol (PyPy3.5 compatible).

No IO, clocks, native calls, hooks, source pointers or gameplay effects are
performed here. Callers supply time/layouts and real native observations.
This ledger does not install the verified v3 experimental materializer.
"""
import base64
import copy
import hashlib
import json
import math

SCHEMA='full-run-ground-restore-control-v1'
SCOPE=('map','room_token','room_seed','worldid','objid','charid','player_type')
MAX_MESSAGE=6144
MAX_MANIFEST=60000
CHUNK_BYTES=3072
MAX_RECORDS=64
CREATE_LOOT_CALLER=0x40dede
GUARDS=('native_updates_paused','target_ai_paused','input_paused',
    'other_createloot_paused','hook_signatures_valid','no_hook_conflict')
BOOL_NATIVE=('isPrivate','hasGravity','xBlocked','yBlocked','crouching','running',
    'instantWalkDecel','createSoundGlobal')
INT_NATIVE=('offsetDT','xVel','yVel','attXVel','moveXVel','moveYVel','walkAccel',
    'airAccel','gravityAccel','redamageTime','lastXmp','lastYmp','lastDamageT')
REQUIRED_NATIVE=set(BOOL_NATIVE+INT_NATIVE+('attached','createSound','subWorld','action','damagedCount'))
COMMON_FIELDS={'schema','kind','restore_nonce','checkpoint_sha256','manifest_sha256','destination_scope'}
WIRE_FIELDS=dict(arm={'source_scope','revision','record_count','manifest_bytes','issued_at','expires_at'},
    record_chunk={'index','record_key','record_sha256','record_bytes','chunk_index','chunk_count','data'},
    chunk_ack={'index','chunk_index','record_sha256'},record_receipt={'index','receipt'},
    receipt_ack={'index','receipt_sha256'},final_ready={'receipts_sha256'},final_ack={'receipts_sha256'},
    committed_ack={'receipts_sha256'},batch_claimed={'constructor_count'},failed={'reason','discard_destination'},arm_ack=set(),prepare=set(),prepared_ack=set())

def ascii_bytes(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')

def sha(value):return hashlib.sha256(ascii_bytes(value)).hexdigest()

def integer(value,label,low=0,high=4294967295):
    if type(value) is not int or not low<=value<=high:raise ValueError('invalid '+label)
    return value

def scope(value):
    if not isinstance(value,dict) or set(value)!=set(SCOPE):raise ValueError('scope needs exact seven fields')
    if not isinstance(value['map'],str) or not value['map'] or len(value['map'])>64:raise ValueError('invalid map')
    for field in ('room_token','worldid','objid'):integer(value[field],field,1)
    for field in ('room_seed','charid','player_type'):integer(value[field],field)
    return copy.deepcopy(value)

def key(value,camel=False):
    names=('worldId','objId') if camel else ('worldid','objid')
    if not isinstance(value,dict) or set(value)-set(names):raise ValueError('invalid ObjKey fields')
    out=tuple(integer(value.get(n,0),n) for n in names)
    if bool(out[0])!=bool(out[1]):raise ValueError('half-empty ObjKey')
    return out

def pb(table,kind,value,wire=False,depth=0):
    if depth>12 or not isinstance(value,dict):raise ValueError('invalid PB message '+kind)
    layout=table.type(kind)
    if layout is None:raise ValueError('unknown PB type '+kind)
    fields={f['name'].rstrip('_'):f for f in layout['fields']};out={};seen=set()
    for name,child in value.items():
        if not isinstance(name,str):raise ValueError('invalid PB field name')
        clean=name.rstrip('_')
        if clean not in fields or clean in seen:raise ValueError('unknown/duplicate PB field '+kind+'.'+name)
        seen.add(clean)
        f=fields[clean];mode=f['kind'];target=f['name'] if wire else clean
        if mode in ('messagePointer','messageInline'):child=pb(table,f['item'],child,wire,depth+1)
        elif mode=='repeatedMessage':
            if not isinstance(child,list) or len(child)>128:raise ValueError('PB repeated budget')
            child=[pb(table,f['item'],v,wire,depth+1) for v in child]
        elif mode=='repeatedString':
            if not isinstance(child,list) or len(child)>128 or any(not isinstance(v,str) for v in child):raise ValueError('invalid PB repeated string')
            for text in child:text.encode('utf8')
        elif mode in ('stringPointer','string'):
            if not isinstance(child,str) or len(child)>4096:raise ValueError('invalid PB string')
            child.encode('utf8')
        elif mode=='bool':
            if type(child) is not bool:raise ValueError('invalid PB bool')
        elif mode in ('int32','sint32'):integer(child,clean,-2147483648,2147483647)
        elif mode=='uint32':integer(child,clean)
        else:raise ValueError('unsupported PB field kind '+mode)
        out[target]=copy.deepcopy(child)
    if kind=='StatBoost':
        stat=out.get('stat_' if wire else 'stat')
        if type(stat) is not int or stat==0 or stat not in set(table.enums.get('Stat',{}).values()):raise ValueError('unknown/missing native StatBoost stat')
    if kind=='ItemProperties':integer(out.get('type_' if wire else 'type'),'native item type',1)
    return out

def references(table,kind,value,path=()):
    if kind=='ObjKeyMessage':return [(path,key(value))]
    fields={f['name'].rstrip('_'):f for f in table.type(kind)['fields']};out=[]
    for name,child in value.items():
        f=fields[name]
        if f['kind'] in ('messagePointer','messageInline'):out.extend(references(table,f['item'],child,path+(name,)))
        elif f['kind']=='repeatedMessage':
            for i,row in enumerate(child):out.extend(references(table,f['item'],row,path+(name,i)))
    return out

def validate_row(row,table,source):
    row=copy.deepcopy(row);kind=row.get('rtti');material='loot' if kind=='Loot' else 'chest'
    required={'key','ptr','rtti','props','native','lifetime_present','remaining_lifetime_ms','observed_generation',material}
    if kind not in ('Loot','Chest') or set(row)!=required:raise ValueError('unknown ground object fields/container')
    old=key(row['key'])
    if old[0]!=source['worldid'] or old[1] in (0,source['objid']):raise ValueError('invalid source ground owner')
    integer(row['ptr'],'observed pointer',1);integer(row['observed_generation'],'observed generation',1)
    props=pb(table,'Properties',row['props']);row['props']=props
    if 'move' in props:raise ValueError('present MOVE native continuation not validated')
    if props.get('shapetype',0)!=0 or props.get('activateworldid',0)!=0:raise ValueError('unsupported shape/activation owner')
    if not props.get('vid'):raise ValueError('missing VID')
    native=row['native']
    if not isinstance(native,dict) or not REQUIRED_NATIVE.issubset(native) or set(native)-REQUIRED_NATIVE-{'itemDesc'}:raise ValueError('unknown/incomplete native state')
    for f in BOOL_NATIVE:
        if type(native[f]) is not bool:raise ValueError('invalid native flag '+f)
    for f in INT_NATIVE:integer(native[f],f,-2147483648,2147483647)
    for f in ('subWorld','action','damagedCount'):integer(native[f],f)
    if 'itemDesc' in native:integer(native['itemDesc'],'itemDesc')
    key(native['attached'],camel=True)
    if native['isPrivate'] is not True or native['action'] or native['damagedCount']:raise ValueError('unsupported private/action/contact state')
    if native['lastDamageT']!=-1 or native['createSound']!='':raise ValueError('unsupported clock/createSound state')
    if type(row['lifetime_present']) is not bool or row['lifetime_present']!=('lifetime' in props):raise ValueError('lifetime presence mismatch')
    if row['lifetime_present']:integer(row['remaining_lifetime_ms'],'remaining lifetime',-2147483648,2147483647)
    if row['remaining_lifetime_ms']!=(props['lifetime'] if 'lifetime' in props else None):raise ValueError('remaining lifetime mismatch')
    if material=='loot':
        if set(row['loot'])!={'itemprops','untilAutoApply'}:raise ValueError('unknown Loot state')
        row['loot']['itemprops']=pb(table,'ItemProperties',row['loot']['itemprops'])
        integer(row['loot']['untilAutoApply'],'untilAutoApply',-2147483648,2147483647)
        items=[row['loot']['itemprops']]
    else:
        c=row['chest']
        if set(c)!={'items','angles','pos','untilThrow'} or not isinstance(c['items'],list) or not isinstance(c['angles'],list):raise ValueError('unknown Chest state')
        if not 2<=len(c['items'])<=128 or len(c['angles'])!=len(c['items']):raise ValueError('invalid/unproven Chest allocation')
        c['items']=[pb(table,'ItemProperties',v) for v in c['items']]
        integer(c['pos'],'Chest pos',-1,len(c['items']));integer(c['untilThrow'],'untilThrow',-2147483648,2147483647)
        for angle in c['angles']:integer(angle,'Chest angle',-2147483648,2147483647)
        if props['vid']=='chestopen' and (c['pos']<0 or props.get('interact')!=0):raise ValueError('inconsistent opened Chest')
        items=c['items']
    if any(len(v.get('statboosts',[]))>128 for v in items):raise ValueError('item boost budget')
    return row

def record_key(revision,source,row):
    return sha(dict(checkpoint_revision=revision,scope=source,key=row['key'],ptr=row['ptr'],generation=row['observed_generation']))

def foreign_set(values):
    result=set()
    for value in values:
        if not isinstance(value,(tuple,list)) or len(value)!=2:raise ValueError('invalid actual foreign key')
        actual=key(dict(zip(('worldid','objid'),value)))
        if actual==(0,0):raise ValueError('empty foreign key')
        result.add(actual)
    return result

def manifest(rows,source,revision,table,foreign_keys):
    if not isinstance(rows,list) or len(rows)>MAX_RECORDS:raise ValueError('ground record budget')
    records=[];by_old={};boosts=0
    for raw in rows:
        row=validate_row(raw,table,source);old=key(row['key'])
        if old in by_old:raise ValueError('duplicate source object identity')
        r=dict(record_key=record_key(revision,source,row),authoritative=row)
        by_old[old]=r['record_key'];records.append(r)
        items=[row['loot']['itemprops']] if row['rtti']=='Loot' else row['chest']['items']
        boosts+=sum(len(v.get('statboosts',[])) for v in items)
    if boosts>4096:raise ValueError('manifest boost budget')
    dependencies={r['record_key']:set() for r in records}
    player=(source['worldid'],source['objid'])
    for r in records:
        row=r['authoritative'];refs=references(table,'Properties',row['props'])
        refs.append((('native','attached'),key(row['native']['attached'],True)))
        items=[row['loot']['itemprops']] if row['rtti']=='Loot' else row['chest']['items']
        for i,item in enumerate(items):refs.extend(references(table,'ItemProperties',item,('item',i)))
        for path,ref in refs:
            if ref in ((0,0),player):continue
            if ref in by_old:dependencies[r['record_key']].add(by_old[ref])
            elif ref[0]==source['worldid'] or ref not in foreign_keys:raise ValueError('unknown ObjKey reference '+str(path))
    order=[];pending=copy.deepcopy(dependencies)
    while pending:

        ready=[r['record_key'] for r in records if r['record_key'] in pending and not pending[r['record_key']]]
        if not ready:raise ValueError('cyclic record references')
        order.extend(ready)
        for k in ready:del pending[k]
        for deps in pending.values():deps.difference_update(ready)
    by_record={r['record_key']:r for r in records};records=[by_record[k] for k in order]
    if len(ascii_bytes(records))>MAX_MANIFEST:raise ValueError('manifest ASCII budget')
    return records

def descriptors(records):return [dict(record_key=r['record_key'],record_sha256=sha(r),record_bytes=len(ascii_bytes(r))) for r in records]

def normalized_state(record,source,destination,bindings,table):
    row=copy.deepcopy(record['authoritative'])
    old_to_record={key(v['authoritative']['key']):k for k,v in bindings['records'].items()}
    def mapped(old):
        if old==(source['worldid'],source['objid']):return destination['worldid'],destination['objid']
        if old in old_to_record:
            actual=bindings['keys'].get(old_to_record[old])
            if actual is None:raise ValueError('dependency not bound to actual new object')
            return key(actual)
        return old
    def walk(kind,value):
        if kind=='ObjKeyMessage':
            actual=mapped(key(value));out=copy.deepcopy(value)
            for field,number in zip(('worldid','objid'),actual):
                if field in out:out[field]=number
            return out
        fields={f['name'].rstrip('_'):f for f in table.type(kind)['fields']};out={}
        for name,child in value.items():
            f=fields[name]
            if f['kind'] in ('messagePointer','messageInline'):child=walk(f['item'],child)
            elif f['kind']=='repeatedMessage':child=[walk(f['item'],v) for v in child]
            out[name]=child
        return out
    row['props']=walk('Properties',row['props'])
    a=mapped(key(row['native']['attached'],True));row['native']['attached']=dict(worldId=a[0],objId=a[1])
    for f in ('key','ptr','observed_generation'):row.pop(f,None)
    for f in ('subWorld','itemDesc'):row['native'].pop(f,None)
    return row

class Ledger(object):
    def __init__(self):self.phase='idle';self.error=None;self.identity=None;self.deadline=None
    def fail(self,reason):
        if self.phase=='ready':return self.status()
        self.error=self.error or str(reason)[:512];self.phase='failed'
        return self.status()
    def tick(self,now):
        if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('invalid time')
        if self.deadline is not None and now>=self.deadline and self.phase not in ('ready','failed'):self.fail('restore deadline exhausted; discard destination World')
    def message(self,kind,**values):
        out=dict(self.identity,schema=SCHEMA,kind=kind);out.update(values)
        if len(ascii_bytes(out))>MAX_MESSAGE:raise ValueError('control ASCII message budget')
        return out
    def matches(self,message):
        try:
            return isinstance(message,dict) and message.get('schema')==SCHEMA and self.identity is not None and scope(message.get('destination_scope'))==self.identity['destination_scope'] and all(
                type(message.get(k)) is type(v) and message.get(k)==v for k,v in self.identity.items()) and len(ascii_bytes(message))<=MAX_MESSAGE
        except (ValueError,TypeError,KeyError):return False
    def fields(self,message):
        kind=message.get('kind')
        if kind not in WIRE_FIELDS or set(message)!=COMMON_FIELDS|WIRE_FIELDS[kind]:raise ValueError('unknown/incomplete control fields')
    def status(self):return dict(phase=self.phase,error=self.error,discard_destination=self.phase=='failed',handoff_allowed=self.phase=='ready',
        discard_scope=copy.deepcopy(self.identity['destination_scope']) if self.identity and self.phase=='failed' else None,
        native_io_performed=False,gate_release_performed=False)

class RestoreServer(Ledger):
    'Host outbox. claim_tcp_batch is an irrevocable, at-most-once claim.'
    def __init__(self,checkpoint,destination_scope,restore_nonce,now,layouts,foreign_keys=(),timeout_s=5.,source_sealed=False):
        super(RestoreServer,self).__init__()
        if source_sealed is not True:raise ValueError('host must provide committed final-egress checkpoint')
        if checkpoint.get('schema')!='full-run-ground-checkpoint-v1':raise ValueError('invalid checkpoint')
        self.source=scope(checkpoint['scope']);self.destination=scope(destination_scope)
        for f in ('map','room_seed','charid','player_type'):
            if self.source[f]!=self.destination[f]:raise ValueError('different room/character restore')
        if self.destination['room_token']<=self.source['room_token']:raise ValueError('new visit token required')
        if not isinstance(restore_nonce,str) or not 16<=len(restore_nonce)<=96 or any(not(ord(c)<128 and (c.isalnum() or c in '_-')) for c in restore_nonce):raise ValueError('invalid restore nonce')
        if type(timeout_s) not in (int,float) or not .1<=timeout_s<=30:raise ValueError('invalid bounded timeout')
        self.table=layouts;self.revision=integer(checkpoint['revision'],'revision',1)
        self.records=manifest(checkpoint['objects'],self.source,self.revision,layouts,foreign_set(foreign_keys))
        self.descriptors=descriptors(self.records)
        self.identity=dict(restore_nonce=restore_nonce,checkpoint_sha256=sha(checkpoint),manifest_sha256=sha(self.descriptors),destination_scope=self.destination)
        self.deadline=now+timeout_s;self.issued_at=now;self.phase='arm';self.upload_index=0;self.chunk_index=0
        self.claimed=False;self.receipts=[];self.last_receipt_ack=None
        self.bindings=dict(records={r['record_key']:r for r in self.records},keys={})
        self.tick(now)
    def control(self,now):
        self.tick(now)
        if self.phase=='failed':return self.message('failed',reason=self.error,discard_destination=True)
        if self.phase=='arm':return self.message('arm',source_scope=self.source,revision=self.revision,record_count=len(self.records),
            manifest_bytes=len(ascii_bytes(self.records)),issued_at=self.issued_at,expires_at=self.deadline)
        if self.phase=='upload':
            r=self.records[self.upload_index];raw=ascii_bytes(r);chunks=(len(raw)+CHUNK_BYTES-1)//CHUNK_BYTES
            return self.message('record_chunk',index=self.upload_index,record_key=r['record_key'],record_sha256=sha(r),record_bytes=len(raw),
                chunk_index=self.chunk_index,chunk_count=chunks,data=base64.b64encode(raw[self.chunk_index*CHUNK_BYTES:(self.chunk_index+1)*CHUNK_BYTES]).decode('ascii'))
        if self.phase=='prepare':return self.message('prepare')
        if self.phase in ('finalize','ready'):return self.message('final_ack',receipts_sha256=sha(self.receipts))
        if self.phase=='receipts' and self.last_receipt_ack is None:return self.message('batch_claimed',constructor_count=len(self.records))
        return copy.deepcopy(self.last_receipt_ack)
    def accept(self,ack,now):
        self.tick(now)
        if self.phase=='failed' or not self.matches(ack):return False
        if self.phase=='ready':
            try:self.fields(ack)
            except ValueError:return False
            return ack.get('kind')=='committed_ack' and ack.get('receipts_sha256')==sha(self.receipts)
        try:self.fields(ack)
        except ValueError as error:self.fail(error);return False
        kind=ack.get('kind')
        if kind=='failed':self.fail('client rejected restore: '+str(ack.get('reason')));return False
        if kind=='arm_ack' and self.phase=='arm':self.phase='upload' if self.records else 'prepare';return True
        if kind=='chunk_ack' and self.phase=='upload':
            if type(ack.get('index')) is not int or type(ack.get('chunk_index')) is not int or ack['index']!=self.upload_index or ack['chunk_index']!=self.chunk_index:return False
            r=self.records[self.upload_index]
            if ack.get('record_sha256')!=sha(r):return False
            self.chunk_index+=1
            if self.chunk_index*CHUNK_BYTES>=len(ascii_bytes(r)):
                self.upload_index+=1;self.chunk_index=0
                if self.upload_index==len(self.records):self.phase='prepare'
            return True
        if kind=='prepared_ack' and self.phase=='prepare':self.phase='constructors';return True
        if kind=='record_receipt' and self.phase=='receipts':
            i=ack.get('index')
            if type(i) is not int or not 0<=i<len(self.records):return False
            if i<len(self.receipts):
                if ack.get('receipt')!=self.receipts[i]:self.fail('conflicting duplicate receipt')
                return False
            if i!=len(self.receipts):return False
            r=self.records[i];receipt=ack.get('receipt') or {};actual=receipt.get('key')
            try:
                new=key(actual)
                if new[0]!=self.destination['worldid'] or new[1] in (0,self.destination['objid']) or actual in self.bindings['keys'].values():raise ValueError('duplicate/invalid new identity')
                self.bindings['keys'][r['record_key']]=copy.deepcopy(actual)
                expected=sha(normalized_state(r,self.source,self.destination,self.bindings,self.table))
                if receipt.get('record_key')!=r['record_key'] or receipt.get('state_sha256')!=expected or receipt.get('before_first_update') is not True:raise ValueError('receipt state/provenance mismatch')
            except (ValueError,TypeError,KeyError) as error:self.fail(error);return False
            self.receipts.append(copy.deepcopy(receipt));self.last_receipt_ack=self.message('receipt_ack',index=i,receipt_sha256=sha(receipt));return True
        if kind=='final_ready' and self.phase=='receipts':
            if len(self.receipts)!=len(self.records) or ack.get('receipts_sha256')!=sha(self.receipts):self.fail('incomplete final restore receipt');return False
            self.phase='finalize';return True
        if kind=='committed_ack' and self.phase in ('finalize','ready') and ack.get('receipts_sha256')==sha(self.receipts):self.phase='ready';return True
        return False
    def claim_tcp_batch(self,now):
        self.tick(now)
        if self.phase!='constructors' or self.claimed:raise ValueError('constructor batch unavailable/already claimed')
        self.claimed=True;self.phase='receipts'
        return [dict(createloot_=dict(props_=pb(self.table,'Properties',r['authoritative']['props'],True),
            itemprops_=[pb(self.table,'ItemProperties',i,True) for i in ([r['authoritative']['loot']['itemprops']]
                if r['authoritative']['rtti']=='Loot' else r['authoritative']['chest']['items'])])) for r in self.records]

class RestoreRegistry(object):
    """Connection-lifetime visit journal. Never evicts a claimed visit.

    Register before control publication or TCP claim. Identical retries use
    the EXISTING ledger. A visit is (map,room_token) within this connection;
    player/owner/seed/character changes cannot create another visit. Full
    seven-field identity still must match to reuse the existing ledger.
    Capacity exhaustion rejects instead of silently forgetting history.
    """
    def __init__(self,max_visits=256):self.max_visits=integer(max_visits,'visit budget',1,4096);self.visits={}
    def claim(self,server):
        if not isinstance(server,RestoreServer):raise ValueError('registry requires RestoreServer')
        visit=(server.destination['map'],server.destination['room_token'])
        old=self.visits.get(visit)
        if old is not None:
            if old.identity!=server.identity:raise ValueError('destination visit already used; discard that World before a new restore')
            return old
        if server.phase!='arm' or server.claimed:raise ValueError('register before control publication/constructor claim')
        if len(self.visits)>=self.max_visits:raise ValueError('restore visit journal exhausted; no eviction/replay')
        self.visits[visit]=server
        return server

class RestoreClient(Ledger):
    """Client inbox/receipt journal; never constructs or restores an object."""
    def __init__(self,layouts):
        super(RestoreClient,self).__init__();self.table=layouts;self.records=[];self.upload=[];self.chunks=[];self.receipts=[]
        self.bindings=dict(records={},keys={});self.pointers={};self.next_constructor=0;self.acked_receipts=0;self.last_ack=None;self.batch_claimed=False
    def guards(self,actual_scope,guard):
        if scope(actual_scope)!=self.identity['destination_scope']:raise ValueError('actual destination identity changed')
        if not isinstance(guard,dict) or any(guard.get(k) is not True for k in GUARDS):raise ValueError('exclusive native/AI/input/CreateLoot gate or hook signature/conflict check failed')
    def consume(self,message,actual_scope,empty_observation,guard,now,foreign_keys=()):
        self.tick(now)
        if self.phase=='failed':return False
        try:
            if not isinstance(message,dict) or message.get('schema')!=SCHEMA or len(ascii_bytes(message))>MAX_MESSAGE:return False

            if scope(message.get('destination_scope'))!=scope(actual_scope):return False
            if self.identity is not None and not self.matches(message):return False
            self.fields(message)
            if self.identity is None:
                if message.get('kind')!='arm':return False
                destination=scope(message['destination_scope']);source=scope(message['source_scope'])
                if source['room_token']>=destination['room_token'] or any(source[f]!=destination[f] for f in ('map','room_seed','charid','player_type')):raise ValueError('invalid source/destination relationship')
                for name in ('checkpoint_sha256','manifest_sha256'):
                    if not isinstance(message.get(name),str) or len(message[name])!=64 or any(c not in '0123456789abcdef' for c in message[name]):raise ValueError('invalid hash')
                if not isinstance(message.get('restore_nonce'),str) or not 16<=len(message['restore_nonce'])<=96 or any(not(ord(c)<128 and (c.isalnum() or c in '_-')) for c in message['restore_nonce']):raise ValueError('invalid nonce')
                self.identity={k:copy.deepcopy(message[k]) for k in ('restore_nonce','checkpoint_sha256','manifest_sha256','destination_scope')}
                self.guards(actual_scope,guard)
                if not isinstance(empty_observation,dict) or empty_observation.get('complete') is not True or empty_observation.get('scope')!=destination or empty_observation.get('keys')!=[] or empty_observation.get('count')!=0:raise ValueError('destination preexisting ground is nonempty/unreadable')
                self.deadline=message['expires_at']
                if type(self.deadline) not in (int,float) or not math.isfinite(self.deadline) or not now<self.deadline<=now+30:raise ValueError('invalid restore deadline')
                self.source=source;self.revision=integer(message['revision'],'revision',1)
                self.count=integer(message['record_count'],'record count',0,MAX_RECORDS);self.total_bytes=integer(message['manifest_bytes'],'manifest bytes',2,MAX_MANIFEST)
                self.arm=copy.deepcopy(message);self.phase='upload';self.last_ack=self.message('arm_ack');return True
            if not self.matches(message):return False
            if self.phase=='ready':
                return message.get('kind')=='final_ack' and message.get('receipts_sha256')==sha(self.receipts)
            self.guards(actual_scope,guard);kind=message.get('kind')
            if kind=='failed':self.fail('host rejected restore: '+str(message.get('reason')));return False
            if kind=='arm':
                if message!=self.arm:raise ValueError('same restore identity with altered arm')
                return True
            if kind=='record_chunk' and self.phase=='upload':
                i=integer(message['index'],'record index',0,self.count-1);c=integer(message['chunk_index'],'chunk index',0,19)
                if i<len(self.upload):
                    old=self.upload[i]
                    if message['record_sha256']!=sha(old):raise ValueError('conflicting uploaded record')
                    return True
                if i!=len(self.upload):return False
                if c<len(self.chunks):
                    if message!=self.chunks[c]:raise ValueError('conflicting duplicate chunk')
                    return True
                if c!=len(self.chunks):return False
                size=integer(message['record_bytes'],'record bytes',1,MAX_MANIFEST);count=integer(message['chunk_count'],'chunk count',1,20)
                if count!=(size+CHUNK_BYTES-1)//CHUNK_BYTES:raise ValueError('invalid chunk shape')
                raw=base64.b64decode(message['data'].encode('ascii'),validate=True)
                if len(raw)!=(CHUNK_BYTES if c<count-1 else size-c*CHUNK_BYTES):raise ValueError('chunk length mismatch')
                if self.chunks and any(message[f]!=self.chunks[0][f] for f in ('record_key','record_sha256','record_bytes','chunk_count')):raise ValueError('chunk header changed')
                self.chunks.append(copy.deepcopy(message));self.last_ack=self.message('chunk_ack',index=i,chunk_index=c,record_sha256=message['record_sha256'])
                if len(self.chunks)==count:
                    combined=b''.join(base64.b64decode(x['data'].encode('ascii')) for x in self.chunks)
                    record=json.loads(combined.decode('ascii'))
                    if ascii_bytes(record)!=combined or sha(record)!=message['record_sha256'] or record.get('record_key')!=message['record_key'] or set(record)!={'record_key','authoritative'}:raise ValueError('record SHA/canonical shape mismatch')
                    self.upload.append(record);self.chunks=[]
                    if len(ascii_bytes(self.upload))>MAX_MANIFEST:raise ValueError('upload manifest budget')
                return True
            if kind=='prepare' and self.phase in ('upload','constructing'):
                if self.phase=='upload':
                    if len(self.upload)!=self.count or self.chunks:raise ValueError('incomplete record upload')
                    expected=manifest([r['authoritative'] for r in self.upload],self.source,self.revision,self.table,foreign_set(foreign_keys))
                    if expected!=self.upload or len(ascii_bytes(expected))!=self.total_bytes or sha(descriptors(expected))!=self.identity['manifest_sha256']:raise ValueError('manifest/topology/hash mismatch')
                    self.records=expected;self.bindings['records']={r['record_key']:r for r in expected};self.phase='constructing'
                self.last_ack=self.message('prepared_ack');return True
            if kind=='receipt_ack' and self.phase=='constructing':
                i=message.get('index')
                if type(i) is not int or i!=self.acked_receipts or i>=len(self.receipts):return False
                if message.get('receipt_sha256')!=sha(self.receipts[i]):raise ValueError('receipt ACK hash mismatch')
                self.acked_receipts+=1;return True
            if kind=='batch_claimed' and self.phase=='constructing':
                if type(message.get('constructor_count')) is not int or message['constructor_count']!=self.count:raise ValueError('constructor batch count mismatch')
                self.batch_claimed=True;return True
            if kind=='final_ack' and self.phase in ('constructing','ready'):
                if len(self.receipts)!=self.count or self.acked_receipts!=self.count or message.get('receipts_sha256')!=sha(self.receipts):raise ValueError('premature/mismatched final ACK')
                self.phase='ready';self.last_ack=self.message('committed_ack',receipts_sha256=sha(self.receipts));return True
            return False
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError) as error:self.fail(error);return False
    def before_constructor(self,caller,incoming,actual_key,pointer,actual_scope,guard,now,actual_rtti=None,actual_live=None):
        self.tick(now)
        try:
            self.guards(actual_scope,guard)
            if self.phase!='constructing' or caller!=CREATE_LOOT_CALLER or self.next_constructor>=len(self.records):raise ValueError('unexpected/extra native ground constructor')
            r=self.records[self.next_constructor];row=r['authoritative'];new=key(actual_key)
            if actual_rtti!=row['rtti'] or actual_live is not False:raise ValueError('actual constructor RTTI/liveness mismatch')
            if new[0]!=actual_scope['worldid'] or new[1] in (0,actual_scope['objid']) or actual_key in self.bindings['keys'].values() or pointer in self.pointers.values():raise ValueError('actual new ground identity reused')
            integer(pointer,'actual new pointer',1)
            materials=[row['loot']['itemprops']] if row['rtti']=='Loot' else row['chest']['items']
            got_props=pb(self.table,'Properties',incoming['props_']);got_items=[pb(self.table,'ItemProperties',v) for v in incoming['itemprops_']]
            if got_props!=row['props'] or got_items!=materials:raise ValueError('native TCP material/order mismatch')
            self.bindings['keys'][r['record_key']]=copy.deepcopy(actual_key);self.pointers[r['record_key']]=pointer
            expected=normalized_state(r,self.source,self.identity['destination_scope'],self.bindings,self.table)
            self.next_constructor+=1;self.batch_claimed=True
            return dict(index=self.next_constructor-1,record_key=r['record_key'],authoritative=copy.deepcopy(row),expected_state=expected,
                expected_state_sha256=sha(expected),construction=dict(source_properties_=copy.deepcopy(incoming['props_']),itemprops_=copy.deepcopy(incoming['itemprops_'])))
        except (ValueError,TypeError,KeyError) as error:self.fail(error);return None
    def restored(self,record_key,actual_key,verification,actual_scope,guard,now,before_first_update=False):
        self.tick(now)
        try:
            self.guards(actual_scope,guard)
            if self.phase!='constructing' or record_key not in self.bindings['keys'] or self.bindings['keys'][record_key]!=actual_key:raise ValueError('unbound receipt identity')
            i=next(i for i,r in enumerate(self.records) if r['record_key']==record_key)
            if i<len(self.receipts):return self.receipts[i]['key']==actual_key and verification.get('state_sha256')==self.receipts[i]['state_sha256']
            if i!=len(self.receipts):raise ValueError('native add receipts out of order')
            r=self.records[i];expected=normalized_state(r,self.source,self.identity['destination_scope'],self.bindings,self.table);digest=sha(expected)
            v=verification;cache=v.get('mapinfo_cache') or {};rect=[expected['props'].get(k,0) for k in ('xmp','ymp','wmp','hmp')]
            if before_first_update is not True or v.get('complete') is not True or v.get('state_sha256')!=digest or v.get('expected_sha256')!=digest:raise ValueError('full native state/before-first-update proof failed')
            if v.get('shape_owner_new') is not True or v.get('shape_owner_pointer')!=self.pointers[record_key] or not v.get('shape_pointer') or cache.get('rect')!=rect or cache.get('type_desc_matches') is not True:raise ValueError('native Shape/MapInfo proof failed')
            if expected['rtti']=='Chest' and expected['props']['vid']=='chestopen':
                vid=v.get('vid_cache') or {}
                if vid.get('complete') is not True or vid.get('actual_vid')!='chestopen' or not vid.get('cached_vad') or vid.get('cached_vad')!=vid.get('expected_vad') or not vid.get('visual_pointer'):raise ValueError('native opened Chest VID proof failed')
            self.receipts.append(dict(record_key=record_key,key=copy.deepcopy(actual_key),state_sha256=digest,before_first_update=True));return True
        except (ValueError,TypeError,KeyError,StopIteration) as error:self.fail(error);return False
    def observe_first_update(self,now):
        self.tick(now)
        if self.phase!='ready':self.fail('native update/use before all-record final handshake')
        return self.phase=='ready'
    def control(self,now):
        self.tick(now)
        if self.identity is None:return None
        if self.phase=='failed':return self.message('failed',reason=self.error,discard_destination=True)
        if self.phase=='ready':return copy.deepcopy(self.last_ack)
        if self.phase=='constructing':
            if self.acked_receipts<len(self.receipts):return self.message('record_receipt',index=self.acked_receipts,receipt=self.receipts[self.acked_receipts])
            if self.batch_claimed and len(self.receipts)==self.count:return self.message('final_ready',receipts_sha256=sha(self.receipts))
        return copy.deepcopy(self.last_ack)
    def status(self):
        out=super(RestoreClient,self).status()
        out['restore_ready']=self.phase=='ready'
        out['handoff_allowed']=False
        return out
