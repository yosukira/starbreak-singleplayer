'Bounded, read-only Win32 native inventory/EC snapshots (PyPy3.5).'
import copy
import ctypes
import json
import math
from pathlib import Path
import time
try:
    from ._hardlab_delta import CffiReader,load_layouts,serialize_message,new_stats,DEFAULT_LIMITS
except (ImportError,ValueError,SystemError):
    from _hardlab_delta import CffiReader,load_layouts,serialize_message,new_stats,DEFAULT_LIMITS

SCHEMA='full-run-inventory-v1'
ABI='win32-gcc48-items-v1'
BAD_STATS=('readErrors','unknownTypes','unknownKinds','depthTruncated','nodeBudgetExceeded','repeatedTruncated','stringErrors','presenceMismatch')

def plain(value):
    if isinstance(value,list):return [plain(v) for v in value]
    if not isinstance(value,dict):return value
    if 'value' in value and 'name' in value:return value['value']
    return {k.rstrip('_'):plain(v) for k,v in value.items()}

class ReadFailure(Exception):pass

class ReadableMemory(object):
    """Win32 VirtualQuery guard, cached only for one bounded snapshot."""
    class Info(ctypes.Structure):
        _fields_=[('base',ctypes.c_void_p),('allocation',ctypes.c_void_p),('allocation_protect',ctypes.c_ulong),
            ('size',ctypes.c_size_t),('state',ctypes.c_ulong),('protect',ctypes.c_ulong),('kind',ctypes.c_ulong)]

    def __init__(self,seconds=.05):
        self.seconds=seconds;self.query=ctypes.windll.kernel32.VirtualQuery
        self.query.argtypes=[ctypes.c_void_p,ctypes.POINTER(self.Info),ctypes.c_size_t];self.query.restype=ctypes.c_size_t
        self.reset()

    def reset(self):self.regions=[];self.deadline=time.perf_counter()+self.seconds

    def __call__(self,address,size):
        if time.perf_counter()>self.deadline:raise ReadFailure('inventory read time budget exhausted')
        address=int(address);size=int(size)
        if size<0 or size>1048576 or not 0x10000<=address<0x7fff0000 or address+size>0x7fff0000:
            raise ReadFailure('invalid native read range')
        end=address+size
        while address<end:
            match=next((r for r in self.regions if r[0]<=address<r[1]),None)
            if match is None:
                info=self.Info()
                if not self.query(address,ctypes.byref(info),ctypes.sizeof(info)):raise ReadFailure('VirtualQuery failed')
                if info.state!=0x1000 or info.protect&0x101 or not info.protect&0xee:
                    raise ReadFailure('native page is not committed/readable')
                match=(int(info.base),int(info.base)+int(info.size));self.regions.append(match)
            if match[1]<=address:raise ReadFailure('invalid native page size')
            address=min(end,match[1])

class GuardedReader(CffiReader):
    def __init__(self,ffi,util,guard):super(GuardedReader,self).__init__(ffi,util);self.guard=guard
    def u8(self,address):self.guard(address,1);return super(GuardedReader,self).u8(address)
    def u32(self,address):self.guard(address,4);return super(GuardedReader,self).u32(address)
    def pointer(self,address):self.guard(address,4);return super(GuardedReader,self).pointer(address)
    def scalar(self,kind,address):
        self.guard(address,1 if kind in ('bool','int8','uint8') else 2 if kind in ('int16','uint16') else 8 if kind in ('int64','uint64','double') else 4)
        return super(GuardedReader,self).scalar(kind,address)
    def strings(self,address):
        data=self.pointer(address)
        if not data:return b''
        self.guard(data-12,12);length=self.u32(data-12);capacity=self.u32(data-8)
        if length>4096 or length>capacity:raise ReadFailure('invalid bounded native string')
        self.guard(data,length)
        return bytes(self.ffi.buffer(self.ffi.cast('char *',data),length))

class CffiInventoryAdapter(object):
    def __init__(self,ffi,util,layouts_path=None,seconds=.05):
        self.ffi=ffi
        if ffi.sizeof('void *')!=4:raise ReadFailure('unsupported inventory ABI: requires32bit pointers')
        if ffi.offsetof('struct STDHashtable','before_begin')!=8 or ffi.offsetof('struct STDHashtable','element_count')!=12:
            raise ReadFailure('unsupported native hash-table layout')
        self.guard=ReadableMemory(seconds);self.reader=GuardedReader(ffi,util,self.guard)
        self.table=load_layouts(str(layouts_path or Path(__file__).with_name('runtime_pb_layouts.json')))
        for name in ('ItemProperties','StatBoost'):
            layout=self.table.type(name)
            if layout is None or layout['size']!=ffi.sizeof('struct '+name) or layout['hasBitsOffset']!=ffi.offsetof('struct '+name,'_has_bits'):
                raise ReadFailure(name+' DWARF/CFFI size/presence mismatch')
            for field in layout['fields']:
                if field['offset']!=ffi.offsetof('struct '+name,field['name'].rstrip('_')):
                    raise ReadFailure(name+' DWARF/CFFI field offset mismatch')
        self.abi=dict(name=ABI,pointer_size=4,hash_before_begin=8,hash_count=12,node_next=0,node_key=4,node_value=8,
            item_props=ffi.offsetof('struct Item','itemProps'),item_slot=ffi.offsetof('struct Item','slotPos'),
            character_items=ffi.offsetof('struct PlayerCharacter','items'),character_ec=ffi.offsetof('struct PlayerCharacter','ec'))

    def address(self,value):return int(self.ffi.cast('uintptr_t',value))
    def begin(self,character):
        self.guard.reset();self.guard(self.address(character),self.ffi.sizeof('struct PlayerCharacter'))
    def identity(self,character):
        desc=character.charDesc;player_type=None
        if desc!=self.ffi.NULL:
            self.guard(self.address(desc),8);player_type=int(desc.type)
        return dict(charid=int(character.charId),player_type=player_type)
    def header(self,character):
        t=character.items.t
        return (int(t.element_count),self.address(t.before_begin),int(t.bucket_count),self.address(t.buckets))
    def node(self,address):
        self.guard(address,12)
        return (self.reader.pointer(address),self.reader.scalar('int32',address+4),self.reader.pointer(address+8))
    def ec(self,character):return int(character.ec)
    def item(self,address,character,max_boosts):
        self.guard(address,self.ffi.sizeof('struct Item'));item=self.ffi.cast('struct Item *',address)
        owner=self.address(item.user)
        if owner!=self.address(character):raise ReadFailure('item is owned by a different character')
        slot=int(item.slotPos);physical_type=int(item.itemProps.type)
        count=int(item.itemProps.statboosts.current_size)
        if count<0 or count>max_boosts:raise ReadFailure('item affix count exceeds bound')
        stats=new_stats();limits=dict(DEFAULT_LIMITS,maxDepth=6,maxRepeated=max_boosts,maxNodes=max_boosts+8)
        props=plain(serialize_message(self.reader,self.table,'ItemProperties',self.address(self.ffi.addressof(item,'itemProps')),limits,stats))
        if any(stats.get(k,0) for k in BAD_STATS):raise ReadFailure('incomplete ItemProperties read: '+str({k:stats[k] for k in BAD_STATS if stats.get(k)}))
        if not isinstance(props,dict) or props.get('type')!=physical_type:raise ReadFailure('native item type/presence mismatch')
        if len(props.get('statboosts',[]))!=count:raise ReadFailure('native affix count/presence mismatch')
        valid_stats={v for k,v in self.table.enums['Stat'].items() if k.startswith('STAT_') and v!=0}
        if any(type(v.get('stat')) is not int or v['stat'] not in valid_stats for v in props.get('statboosts',[])):
            raise ReadFailure('native item contains invalid/unknown StatBoost')
        return dict(props=props,slot=slot,owner_ok=True,boost_count=count)

    def world(self,cw,context):
        if cw==self.ffi.NULL or cw.player==self.ffi.NULL:raise ReadFailure('no native player')
        character=self.ffi.cast('struct PlayerCharacter *',cw.player)
        world=character.asPlayer.asClientObject.asNativeObject.asWorldObject
        owner=None
        if cw.mySubWorld!=self.ffi.NULL:owner=int(cw.mySubWorld.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId)
        seed=int(cw.asWorld.props.seed);context=context or {}
        matching=context.get('room_seed')==seed and context.get('room_token') is not None and bool(context.get('room_ready'))
        scope=dict(worldid=owner,objid=int(world.objId),room_seed=seed,map=context.get('map'),
            room_token=context.get('room_token') if matching else None)
        return character,scope

class InventoryReader(object):
    def __init__(self,ffi=None,util=None,layouts_path=None,max_items=256,max_boosts=128,max_total_boosts=2048,
                 max_read_seconds=.05,max_json_bytes=48000,adapter=None):
        self.adapter=adapter or CffiInventoryAdapter(ffi,util,layouts_path,max_read_seconds)
        self.max_items=int(max_items);self.max_boosts=int(max_boosts);self.max_total_boosts=int(max_total_boosts)
        self.max_json_bytes=int(max_json_bytes)

    def snapshot_world(self,cw,context=None,now=None,request_id=None):
        """Read now; request_id is an optional server refresh nonce, not a cache key.

        The caller supplies the actual current ClientWorld and acknowledged room
        context. It must call this after receiving the refresh request, on the
        native update thread. This function never returns a previous snapshot.
        """
        try:character,scope=self.adapter.world(cw,context)
        except Exception as error:
            return dict(schema=SCHEMA,sampled_at=time.time() if now is None else now,ok=False,items_ok=False,ec_ok=False,
                request_id=request_id,scope={},quality=dict(errors=[type(error).__name__+': '+str(error)]))
        return self.snapshot(character,scope,now,request_id)

    def snapshot(self,character,scope,now=None,request_id=None):
        started=time.perf_counter()
        result=dict(schema=SCHEMA,sampled_at=time.time() if now is None else now,items_ok=False,ec_ok=False,
            ok=False,request_id=request_id,scope=copy.deepcopy(scope),quality=dict(errors=[],abi=copy.deepcopy(self.adapter.abi),
                slot_source='Item.slotPos',read_clock='perf_counter',stable=False,declared_count=None,traversed_count=0))
        q=result['quality'];before_ec=None
        try:
            if not isinstance(scope,dict):raise ReadFailure('invalid native room scope')
            if request_id is not None and (type(request_id) is not int or request_id<=0):raise ReadFailure('invalid inventory request ID')
            self.adapter.begin(character);result['scope'].update(self.adapter.identity(character))
        except Exception as error:
            q['errors'].append(type(error).__name__+': '+str(error));q['read_elapsed_s']=time.perf_counter()-started;return result
        try:
            before_ec=self.adapter.ec(character)
            if type(before_ec) is not int or not 0<=before_ec<=2147483647:
                before_ec=None;raise ReadFailure('invalid native EC')
        except Exception as error:q['errors'].append(type(error).__name__+': '+str(error))
        try:
            header=self.adapter.header(character);count,node,buckets,bucket_ptr=header;q['declared_count']=count
            if count<0 or count>self.max_items or buckets<0 or buckets>1048576:raise ReadFailure('native inventory header exceeds bounds')
            if (count==0)!=(node==0):raise ReadFailure('empty map count/head disagreement')
            if count and (buckets==0 or bucket_ptr==0):raise ReadFailure('nonempty map lacks buckets')
            items=[];audit=[];visited={};slots=set();keys=set();records=[];boosts=0
            while node:
                if node in visited:raise ReadFailure('native inventory node cycle')
                if len(items)>=count:raise ReadFailure('native map chain exceeds declared count')
                link,key,item_ptr=self.adapter.node(node)
                if key in keys:raise ReadFailure('duplicate native map key')
                if not item_ptr:raise ReadFailure('null Item pointer in native map')
                row=self.adapter.item(item_ptr,character,self.max_boosts);slot=row['slot'];props=row['props']
                if row.get('owner_ok') is not True:raise ReadFailure('item owner mismatch')
                if type(slot) is not int or not 0<slot<=65535 or slot in slots:raise ReadFailure('invalid/duplicate native item slot')
                if type(props.get('type')) is not int or not 0<props['type']<=2147483647:raise ReadFailure('invalid native item type')
                if type(row['boost_count']) is not int or not 0<=row['boost_count']<=self.max_boosts:raise ReadFailure('item affix count exceeds bound')
                boosts+=row['boost_count']
                if boosts>self.max_total_boosts:raise ReadFailure('total native affix count exceeds bound')
                material=copy.deepcopy(props);material['slotpos']=slot;items.append(material)
                audit.append(dict(map_key=key,native_slotpos=slot,serialized_slotpos=props.get('slotpos'),type=props['type'],owner_ok=row['owner_ok']))
                visited[node]=(link,key,item_ptr);records.append((item_ptr,row));slots.add(slot);keys.add(key);node=link
            q['traversed_count']=len(items)
            if len(items)!=count:raise ReadFailure('native map chain shorter than declared count')
            for pointer,old in visited.items():
                if self.adapter.node(pointer)!=old:raise ReadFailure('native map changed during read')
            for pointer,old in records:
                if self.adapter.item(pointer,character,self.max_boosts)!=old:raise ReadFailure('item/slot/affixes changed during read')
            if self.adapter.header(character)!=header:raise ReadFailure('native map header changed during read')
            order=sorted(range(len(items)),key=lambda i:items[i]['slotpos'])
            result.update(items_ok=True,count=count,items=[items[i] for i in order],slots=[audit[i] for i in order])
            q.update(stable=True,traversed_count=count,boost_count=boosts,
                map_keys_match_native_slots=all(a['map_key']==a['native_slotpos'] for a in audit),
                serialized_slot_mismatches=sum(a['serialized_slotpos']!=a['native_slotpos'] for a in audit))
        except Exception as error:q['errors'].append(type(error).__name__+': '+str(error))
        try:
            after_ec=self.adapter.ec(character)
            if before_ec is None or type(after_ec) is not int or after_ec!=before_ec:raise ReadFailure('native EC changed/failed during read')
            result.update(ec_ok=True,ec=after_ec)
        except Exception as error:q['errors'].append(type(error).__name__+': '+str(error))
        if result['items_ok']:
            try:
                size=len(json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf8'))
                q['snapshot_json_bytes']=size
                if size>self.max_json_bytes:raise ReadFailure('native inventory payload exceeds bounded transport budget')
            except Exception as error:
                result['items_ok']=False
                for key in ('items','slots','count'):result.pop(key,None)
                q['errors'].append(type(error).__name__+': '+str(error))
        result['ok']=result['items_ok'] and result['ec_ok']
        q['read_elapsed_s']=time.perf_counter()-started
        return result

REFRESH_SCOPE=('map','room_token','room_seed','worldid','objid','charid','player_type')

class InventoryRefresh(object):
    """One on-demand read per scoped request/attempt, safe for ACK retries.

    No pending control means no ABI initialization or native memory reads.
    Cache retransmission never changes sampled_at. A new attempt is needed
    after a failed/aged read; room/character changes never reuse old success.
    reader/reader_factory are injectable for offline tests only.
    """
    def __init__(self,ffi=None,util=None,reader=None,reader_factory=None):
        self.ffi=ffi;self.util=util;self.reader=reader
        self.reader_factory=reader_factory or (lambda:InventoryReader(ffi,util))
        self.cache_key=None;self.cache=None;self.scope_key=None;self.highwater=None

    @staticmethod
    def _failed(control,now,reason,scope=None):
        return dict(schema=SCHEMA,sampled_at=now,request_id=control.get('id'),request_attempt=control.get('attempt'),
            ok=False,items_ok=False,ec_ok=False,scope=copy.deepcopy(scope or {}),quality=dict(errors=[str(reason)]))

    def _server_t(self,cw):
        adapter=getattr(self.reader,'adapter',None)
        if adapter is not None and hasattr(adapter,'server_t'):return adapter.server_t(cw)
        if self.ffi is None:raise ReadFailure('native server clock unavailable')
        if cw==self.ffi.NULL or cw.serverSubWorld==self.ffi.NULL:raise ReadFailure('native server subworld unavailable')
        return int(cw.serverSubWorld.asSubWorldImpl.asSubWorld.t)

    def document(self,cw,context,control,now=None):
        if not control:return None
        now=time.time() if now is None else now
        if not isinstance(control,dict):return self._failed({},now,'invalid inventory refresh control')
        if type(now) not in (int,float) or not math.isfinite(now):return self._failed(control,0.,'invalid inventory refresh time')
        identifier=control.get('id');attempt=control.get('attempt');expires=control.get('expires_at');issued=control.get('issued_at');target=control.get('scope')
        if type(identifier) is not int or identifier<=0 or type(attempt) is not int or attempt<=0 or not isinstance(target,dict):
            return self._failed(control,now,'invalid inventory refresh identity')
        if type(expires) not in (int,float) or not math.isfinite(expires) or now>=expires:
            return self._failed(control,now,'inventory refresh expired or invalid')
        if type(issued) not in (int,float) or not math.isfinite(issued) or issued>=expires or issued>now+.025:
            return self._failed(control,now,'inventory refresh issue time invalid')
        if not isinstance(target.get('map'),str) or not target['map'] or any(type(target.get(k)) is not int for k in REFRESH_SCOPE if k!='map'):
            return self._failed(control,now,'invalid inventory refresh scope')
        if any(target[k]<=0 for k in ('room_token','worldid','objid')) or not 0<=target['room_seed']<=4294967295 or not 0<=target['charid']<=4294967295 or not 0<=target['player_type']<=2147483647:
            return self._failed(control,now,'invalid inventory refresh scope range')
        context=context if isinstance(context,dict) else {}
        if context.get('room_ready') is not True or any(type(context.get(k)) is not type(target[k]) or context.get(k)!=target[k] for k in ('map','room_token','room_seed')):
            return self._failed(control,now,'native room context not ready or mismatched',{k:context.get(k) for k in ('map','room_token','room_seed')})
        minimum=control.get('native_t_min');native_t=None
        if minimum is not None:
            if type(minimum) is not int or not 0<=minimum<=2147483647:
                return self._failed(control,now,'invalid native TCP time barrier')
            try:native_t=self._server_t(cw)
            except Exception:return None
            if type(native_t) is not int:return self._failed(control,now,'invalid native server clock')
            if native_t<minimum:return None
        scope_key=tuple(target[k] for k in REFRESH_SCOPE);key=(identifier,attempt,scope_key,issued,expires,minimum)
        if self.scope_key!=scope_key:
            self.scope_key=scope_key;self.highwater=None;self.cache_key=None;self.cache=None

        generation=(issued,identifier,attempt)
        if self.highwater is not None and generation<self.highwater:
            return self._failed(control,now,'superseded inventory refresh request')
        if self.cache_key==key and self.cache is not None and not self.cache.get('ok'):
            return copy.deepcopy(self.cache)
        actual={}
        try:
            if self.reader is None:self.reader=self.reader_factory()
            character,actual=self.reader.adapter.world(cw,context)
            self.reader.adapter.begin(character);actual.update(self.reader.adapter.identity(character))
            if any(type(actual.get(k)) is not type(target[k]) or actual.get(k)!=target[k] for k in REFRESH_SCOPE):
                return self._failed(control,now,'native player/room identity mismatched',actual)
            if self.cache_key==key and self.cache is not None:return copy.deepcopy(self.cache)
            snapshot=self.reader.snapshot(character,actual,now=now,request_id=identifier)
            snapshot['request_attempt']=attempt
        except Exception as error:snapshot=self._failed(control,now,type(error).__name__+': '+str(error),actual)
        if native_t is None:
            try:native_t=self._server_t(cw)
            except Exception:pass
        snapshot['native_server_t']=native_t
        self.cache_key=key;self.highwater=generation;self.cache=copy.deepcopy(snapshot)
        return copy.deepcopy(snapshot)
