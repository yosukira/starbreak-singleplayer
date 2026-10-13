"""Candidate final-egress coordinator, Win32 GCC/PyPy3.5; not wired/published.

Caller contract: ONLY a marked local simulator may arm it. The server prepares
one transition, waits for armed, and then sends the one expected single-pack
LoadPVid. LoadPVid has no nonce. R/editor/class intents MUST cancel-and-ACK
before that send, or be serialized until this transaction finishes. Multi-pack
preload routes and a shared observer dispatcher are deliberately unsupported.

The final snapshot is the unchanged shared full-run-ground-joint-v1 contract.
It is retained outside old World lifetime. Failed matched loads return ret4
before the original. A successful load calls the original once; CreateWorld
only binds ACTUAL compressed payload bytes. No file/network/C++ calls, writes
to native objects, regular player.inventory mutation, or restore logic here.
"""
import copy
import ctypes
import hashlib
import json
import math
import os
import struct
import time

try:
    from ._full_run_ground import GroundReader, SCHEMA as JOINT_SCHEMA, ABI as GROUND_ABI
    from ._full_run_inventory import REFRESH_SCOPE, ReadableMemory, BAD_STATS
    from ._hardlab_delta import serialize_message, new_stats, DEFAULT_LIMITS
except (ImportError, ValueError, SystemError):
    from _full_run_ground import GroundReader, SCHEMA as JOINT_SCHEMA, ABI as GROUND_ABI
    from _full_run_inventory import REFRESH_SCOPE, ReadableMemory, BAD_STATS
    from _hardlab_delta import serialize_message, new_stats, DEFAULT_LIMITS

SCHEMA = 'full-run-egress-v1'
READY_SCHEMA = 'full-run-egress-ready-v1'
MAX_WIRE_BYTES = 63000
MAX_JOINT_BYTES = 60000
MAX_CONTROL_TTL_S = 10.
RUN_MAPS = frozenset(('egy_run','eub_run','etl_run','est_run','efb_run','eff_run'))
TARGETS = {
    'load_pvid': (0x469b90, bytes.fromhex('5589e557565381ec8c000000')),
    'create_world': (0x46e5d0, bytes.fromhex('5589e557565381ecbc000000')),
}

_OWNERS = {}

def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')

def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def same_scope(a,b):
    return isinstance(a,dict) and isinstance(b,dict) and set(a)==set(REFRESH_SCOPE) and set(b)==set(REFRESH_SCOPE) and all(
        type(a[k]) is type(b[k]) and a[k]==b[k] for k in REFRESH_SCOPE)

def load_gate_code(callback,trampoline):
    'uint32 cdecl(this,caller,LoadPVid*) -> 1 tail-jump, 0 ret4.'
    code=bytearray(b'\x55\x89\xe5\x83\xec\x14\x89\x4d\xfc\x9c\x60\x83\xec\x04')
    code.extend(b'\xff\x75\x08\xff\x75\x04\xff\x75\xfc\xb8'+struct.pack('<I',callback)+b'\xff\xd0')
    code.extend(b'\x89\x45\xf8\x83\xc4\x10\x61\x9d\x83\x7d\xf8\x00\x74\x08')
    code.extend(b'\xc9\xb8'+struct.pack('<I',trampoline)+b'\xff\xe0\x31\xc0\xc9\xc2\x04\x00')
    return bytes(code)

def createworld_observer_code(callback,trampoline):
    """void cdecl(this,caller,data,length), then original thiscall tail-jump."""
    code=bytearray(b'\x55\x89\xe5\x83\xec\x10\x89\x4d\xfc\x9c\x60\x83\xec\x04')
    code.extend(b'\xff\x75\x0c\xff\x75\x08\xff\x75\x04\xff\x75\xfc')
    code.extend(b'\xb8'+struct.pack('<I',callback)+b'\xff\xd0\x83\xc4\x14\x61\x9d\xc9')
    code.extend(b'\xb8'+struct.pack('<I',trampoline)+b'\xff\xe0')
    return bytes(code)

class NativeAccess(object):
    def __init__(self,ffi,bridge,reader):
        if ffi.sizeof('void *')!=4:raise ValueError('egress requires32bit native ABI')
        self.ffi,self.bridge,self.reader=ffi,bridge,reader
        self.guard=ReadableMemory(.05)
        for kind,field,offset in (('GameClient','worldClient',72),('WorldClient','clientWorld',72)):
            if ffi.offsetof('struct '+kind,field)!=offset:raise ValueError('egress native world pointer ABI mismatch')
        layout=reader.adapter.table.type('LoadPVid')
        if layout is None or layout['size']!=ffi.sizeof('struct LoadPVid') or layout.get('hasBitsOffset')!=ffi.offsetof('struct LoadPVid','_has_bits'):
            raise ValueError('LoadPVid native protobuf ABI mismatch')
        for field in layout['fields']:
            if field['offset']!=ffi.offsetof('struct LoadPVid',field['name'].rstrip('_')):
                raise ValueError('LoadPVid native field ABI mismatch')

    def address(self,pointer):return int(self.ffi.cast('uintptr_t',pointer))
    def protect(self,pointer,kind):self.guard(self.address(pointer),self.ffi.sizeof('struct '+kind))
    def thread(self):return int(ctypes.windll.kernel32.GetCurrentThreadId())

    def current(self,this=None):
        cw=self.bridge._current_world()
        if cw is None or cw==self.ffi.NULL:return None
        self.guard.reset()
        self.protect(cw,'ClientWorld')
        if this is not None:
            gc=self.ffi.cast('struct GameClient *',this);self.protect(gc,'GameClient')
            wc=gc.worldClient
            if wc==self.ffi.NULL:return None
            self.protect(wc,'WorldClient')
            if self.address(wc.clientWorld)!=self.address(cw):return None
        return cw

    def identity(self,cw,context):
        self.guard.reset()
        if cw is None or cw==self.ffi.NULL:return dict(scope=None,alive=False)
        self.protect(cw,'ClientWorld')
        if cw.player==self.ffi.NULL or cw.mySubWorld==self.ffi.NULL:return dict(scope=None,alive=False)
        self.protect(cw.mySubWorld,'ClientSubWorld')
        character=self.ffi.cast('struct PlayerCharacter *',cw.player);self.protect(character,'PlayerCharacter')
        world=character.asPlayer.asClientObject.asNativeObject.asWorldObject
        desc=character.charDesc
        if desc==self.ffi.NULL:return dict(scope=None,alive=False)
        self.protect(desc,'CharacterDescription')
        scope=dict(map=context.get('map'),room_token=context.get('room_token'),room_seed=int(cw.asWorld.props.seed),
            worldid=int(cw.mySubWorld.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId),objid=int(world.objId),
            charid=int(character.charId),player_type=int(desc.type))
        return dict(scope=scope,alive=bool(world.isLive) and int(world.props.hitpoints)>0,
            vitals=dict(hp=int(world.props.hitpoints),ammo=int(character.ammo),
                xmp=int(world.props.xmp),ymp=int(world.props.ymp),wmp=int(world.props.wmp),hmp=int(world.props.hmp),
                facing=int(world.props.facing),pose=int(world.props.pose)),
            world_ptr=self.address(cw),player_ptr=self.address(character))

    def pvid(self,message):

        adapter=self.reader.adapter
        adapter.guard.begin()
        try:
            stats=new_stats()
            value=serialize_message(adapter.reader,adapter.table,'LoadPVid',int(message),
                dict(DEFAULT_LIMITS,maxDepth=2,maxRepeated=1,maxNodes=2),stats)
            if any(stats.get(k) for k in BAD_STATS) or not isinstance(value,dict) or set(value)!=set(('pvid',)):
                raise ValueError('incomplete LoadPVid decode')
            result=value['pvid']
            if not isinstance(result,str) or not result or '\x00' in result or len(result.encode('utf8'))>128:
                raise ValueError('invalid bounded LoadPVid.pvid')
            return result
        finally:adapter.guard.finish()

    def payload(self,data,length):
        length=int(length)
        if not 0<length<=8*1024*1024:raise ValueError('CreateWorld payload exceeds8MB bound')
        self.guard.reset()
        for offset in range(0,length,1048576):self.guard(int(data)+offset,min(1048576,length-offset))
        return bytes(self.ffi.buffer(self.ffi.cast('char *',data),length))

class HookPair(object):
    """Two exclusive native hook owners; refuse any existing detour."""
    def __init__(self,ffi,lib,coordinator):
        self.ffi,self.lib,self.coordinator=ffi,lib,coordinator
        self.records=[]
        self.guard=ReadableMemory(.05)
        self.kernel32=ctypes.windll.kernel32
        self.kernel32.VirtualAlloc.restype=ctypes.c_void_p
        self.kernel32.VirtualAlloc.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_uint32]
        self.kernel32.VirtualFree.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32]

    def bytes(self,address,size):
        self.guard.reset();self.guard(address,size)
        return bytes(self.ffi.buffer(self.ffi.cast('char *',address),size))

    def preflight(self):
        for name,(address,signature) in TARGETS.items():
            if address in _OWNERS or self.bytes(address,len(signature))!=signature:
                raise ValueError('egress hook conflict/unknown signature: '+name+'; no observer dispatcher available')

    def install(self):
        self.preflight()
        try:
            for name,(address,signature) in TARGETS.items():

                if address in _OWNERS or self.bytes(address,len(signature))!=signature:
                    raise ValueError('egress hook owner changed during installation: '+name)
                stub=int(self.kernel32.VirtualAlloc(None,128,0x3000,0x40) or 0)
                if not stub:raise MemoryError('egress native thunk allocation failed')
                row=dict(name=name,address=address,stub=stub,hook=None,callback=None)
                self.records.append(row)
                if name=='load_pvid':
                    def load(this,caller,message):
                        try:return int(bool(self.coordinator._on_load_pvid(int(this),int(caller),int(message))))
                        except Exception as error:
                            self.coordinator._unexpected_callback_error('load_pvid',error)
                            return 1
                    callback=self.ffi.callback('uint32_t(uint32_t,uint32_t,uint32_t)',load)
                else:
                    def world(this,caller,data,length):
                        try:self.coordinator._on_create_world(int(this),int(caller),int(data),int(length))
                        except Exception as error:self.coordinator._unexpected_callback_error('create_world',error)
                    callback=self.ffi.callback('void(uint32_t,uint32_t,uint32_t,uint32_t)',world)
                row['callback']=callback
                hook=self.lib.subhook_new(self.ffi.cast('void *',address),self.ffi.cast('void *',stub),0)
                if hook==self.ffi.NULL:raise RuntimeError('egress subhook_new failed')
                row['hook']=hook
                trampoline=self.lib.subhook_get_trampoline(hook)
                if trampoline==self.ffi.NULL:raise RuntimeError('egress native trampoline missing')
                callback_address=int(self.ffi.cast('uintptr_t',callback))
                original=int(self.ffi.cast('uintptr_t',trampoline))
                code=(load_gate_code if name=='load_pvid' else createworld_observer_code)(callback_address,original)
                ctypes.memmove(stub,code,len(code))
                self.kernel32.FlushInstructionCache(self.kernel32.GetCurrentProcess(),ctypes.c_void_p(stub),len(code))
                row['detour']=b'\xe9'+struct.pack('<i',stub-address-5)
                _OWNERS[address]=self
                self.lib.subhook_install(hook)
                if not self.lib.subhook_is_installed(hook):raise RuntimeError('egress native hook install failed')
        except Exception:
            self.close()
            raise

    def close(self):
        errors=[]
        retained=[]
        for row in reversed(self.records):
            hook=row['hook'];address=row['address']
            try:
                if address in _OWNERS:
                    installed=hook is not None and self.lib.subhook_is_installed(hook)
                    original=TARGETS[row['name']][1]
                    clean_uninstalled=(not installed and self.bytes(address,len(original))==original)
                    if _OWNERS[address] is not self or (not clean_uninstalled and self.bytes(address,5)!=row.get('detour')):
                        raise RuntimeError('foreign native patch after egress install: '+row['name'])
                    if installed:
                        self.lib.subhook_remove(hook)
                        if self.lib.subhook_is_installed(hook):raise RuntimeError('egress subhook_remove failed: '+row['name'])
                    del _OWNERS[address]
                if hook is not None and hook!=self.ffi.NULL:
                    self.lib.subhook_free(hook);row['hook']=None
                if row['stub']:
                    if not self.kernel32.VirtualFree(row['stub'],0,0x8000):raise RuntimeError('egress thunk free failed')
                    row['stub']=0
            except Exception as error:
                errors.append(str(error));retained.append(row)
        self.records=list(reversed(retained))
        if errors:raise RuntimeError('; '.join(errors))

class EgressCoordinator(object):
    'Main native-thread arm/close; dedicated outgoing/ACK cache, no I/O.'
    def __init__(self,ffi,lib,util,bridge):
        self.ffi,self.lib,self.util,self.bridge=ffi,lib,util,bridge
        self.reader,self.native,self.hooks=None,None,None
        self.control=None
        self.receipt=None
        self._small=None
        self._serial=0
        self._generation=None
        self._control_key=None
        self._attempt_control=None
        self._acknowledged=[]
        self._pending_ack=None
        self._thread=None
        self._callback_depth=0
        self._closed=False
        self.error=None
        self.stats=dict(final_reads=0,warm_reads=0,original_load_allowed=0,loads_skipped=0,
            unrelated_loads=0,duplicate_loads=0,createworlds=0,callback_errors=0)

    def _now(self):return time.time()
    def _make_reader(self):return GroundReader(self.ffi,self.util)
    def _make_native(self):return NativeAccess(self.ffi,self.bridge,self.reader)
    def _make_hooks(self):return HookPair(self.ffi,self.lib,self)

    def _live(self):
        return (str(os.environ.get('HARDLAB_LIVE_SESSION','')).strip().lower() in ('1','yes','true','on')
            and getattr(self.bridge,'enabled',False) is True and getattr(self.bridge,'host',None) in ('127.0.0.1','localhost','::1'))

    @property
    def protocol(self):
        return SCHEMA if self.hooks is not None and not self.error and not self._closed and self._live() else None

    @property
    def cancel_identity(self):
        'Exact validated attempt, even when warm read never installed hooks.'
        value=self.control or self._attempt_control
        return copy.deepcopy(value) if value is not None else None

    def _validate_control(self,value):
        if not isinstance(value,dict) or set(value)!=set(('request_id','request_attempt','scope','expected_pvid',
                'expected_cw_sha256','issued_at','expires_at')):raise ValueError('invalid egress control fields')
        for name in ('request_id','request_attempt'):
            if type(value[name]) is not int or not 0<value[name]<=9223372036854775807:raise ValueError('invalid '+name)
        scope=value['scope']
        if not isinstance(scope,dict) or set(scope)!=set(REFRESH_SCOPE):raise ValueError('egress needs exact7-field source scope')
        if scope['map'] not in RUN_MAPS or type(scope['map']) is not str:raise ValueError('unsupported egress source map')
        for key in REFRESH_SCOPE:
            if key!='map' and type(scope[key]) is not int:raise ValueError('invalid egress scope type: '+key)
        if any(scope[k]<=0 for k in ('room_token','worldid','objid')) or any(not 0<=scope[k]<=4294967295 for k in ('room_seed','charid')) or not 0<=scope['player_type']<=2147483647:
            raise ValueError('invalid egress scope range')
        pvid=value['expected_pvid']
        if not isinstance(pvid,str) or not pvid or '\x00' in pvid or len(pvid.encode('utf8'))>128:raise ValueError('invalid single expected_pvid')
        sha=value['expected_cw_sha256']
        if not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('invalid expected CW SHA256')
        issued,expires=value['issued_at'],value['expires_at']
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (issued,expires)) or not issued<expires or expires-issued>MAX_CONTROL_TTL_S:
            raise ValueError('invalid bounded egress issue/expiry')
        now=self._now()
        if issued>now+.025 or now>=expires:raise ValueError('egress control expired or from future')
        return copy.deepcopy(value)

    def _context(self):return copy.deepcopy(getattr(self.bridge,'input_context',{}) or {})

    def _identity(self,this=None):
        context=self._context()
        cw=self.native.current(this)
        identity=self.native.identity(cw,context)
        return cw,context,identity

    def _source_ok(self,identity):
        return bool(self.control and identity.get('alive') is True and same_scope(identity.get('scope'),self.control['scope']))

    def _joint(self,cw,context,control):
        value=self.reader.snapshot_world(cw,context,now=self._now(),
            request_id=control['request_id'],request_attempt=control['request_attempt'])
        if (not isinstance(value,dict) or value.get('schema')!=JOINT_SCHEMA or value.get('ok') is not True
                or value.get('ready') is not True or not same_scope(value.get('scope'),control['scope'])
                or value.get('quality',{}).get('stable') is not True or value.get('quality',{}).get('abi',{}).get('name')!=GROUND_ABI):
            raise ValueError('shared joint reader not complete/stable or source scope mismatched: '+str(value.get('quality',{}).get('errors',[]) if isinstance(value,dict) else 'invalid result'))
        inventory=value.get('inventory') or {};ground=value.get('ground') or {}
        if inventory.get('ok') is not True or inventory.get('items_ok') is not True or inventory.get('ec_ok') is not True or ground.get('ok') is not True or ground.get('schema')!='full-run-ground-v1':
            raise ValueError('shared joint inventory/ground incomplete')
        for name in ('scope','request_id','request_attempt','sampled_at','native_server_t'):
            if type(inventory.get(name)) is not type(value.get(name)) or inventory.get(name)!=value.get(name):raise ValueError('shared joint inventory identity mismatch: '+name)
        if any(type(value.get(k)) is not int or value[k]<0 for k in ('native_local_t','native_server_t')):
            raise ValueError('invalid shared native clocks')
        if value.get('request_id')!=control['request_id'] or value.get('request_attempt')!=control['request_attempt']:
            raise ValueError('shared joint nonce mismatch')
        if type(ground.get('count')) is not int or not isinstance(ground.get('objects'),list) or ground['count']!=len(ground['objects']):
            raise ValueError('shared joint ground count incomplete')
        if len(canonical(value))>MAX_JOINT_BYTES:raise ValueError('joint exceeds60000byte ASCII budget')
        return copy.deepcopy(value)

    def _small_state(self,status,ready=False,reason=None,control=None):
        c=control or self.control
        row=dict(schema=READY_SCHEMA,kind='egress_ready',status=status,ready=ready,protocol=self.protocol)
        if c:row.update({k:copy.deepcopy(c[k]) for k in ('request_id','request_attempt','issued_at','expires_at','scope','expected_pvid','expected_cw_sha256')})
        if reason:row['reason']=str(reason)[:512]
        row['max_wire_bytes']=MAX_WIRE_BYTES
        return row

    def arm(self,control):
        if self._closed:return self._small_state('closed',reason='coordinator closed')
        if self._pending_ack is not None:
            return self._small_state('busy',reason='exact ACK native-hook cleanup is pending')
        if control is None:
            if self.receipt is not None:return self._small_state('busy',reason='unacknowledged captured receipt cannot be cancelled')
            if self._callback_depth:return self._small_state('busy',reason='native callback is running')
            if self.native is not None and self._thread is not None and self.native.thread()!=self._thread:
                return self._small_state('busy',reason='cancel must run on native update thread')
            previous=self.control or self._attempt_control
            self.control=None
            self._remove_hooks()
            if self.hooks is not None:
                self._small=self._small_state('error',reason=self.error or 'hook cleanup incomplete',control=previous)
                return copy.deepcopy(self._small)
            self._control_key=None;self._attempt_control=None;self.error=None
            self._small=self._small_state('cancelled',control=previous)
            return copy.deepcopy(self._small)
        if self.receipt is not None:return self._small_state('busy',reason='unacknowledged egress receipt')
        if self.control is not None:
            try:
                if fingerprint(control)==self._control_key:return self.ready_document()
            except Exception:pass
            return self._small_state('busy',reason='caller must cancel/ACK previous armed transition first')
        if not self._live():return self._small_state('unavailable',reason='explicit local live marker/bridge required',control=None)
        validated=None
        try:
            value=self._validate_control(control);validated=value;key=fingerprint(value)
            if key==self._control_key:return self.ready_document()
            generation=(value['issued_at'],value['request_id'],value['request_attempt'])
            if self._generation is not None and generation<=self._generation:raise ValueError('superseded/replayed/conflicting egress control')
            if self.control is not None:raise ValueError('caller must cancel/ACK previous armed transition first')
            self._attempt_control=copy.deepcopy(value)
            self._generation=generation;self._control_key=key
            if self.reader is None:self.reader=self._make_reader()
            if self.native is None:self.native=self._make_native()
            thread=self.native.thread()
            if self._thread is not None and thread!=self._thread:raise ValueError('egress arm must run on native update thread')
            self._thread=thread
            cw,context,before=self._identity()
            if context.get('room_ready') is not True or not before.get('alive') or not same_scope(before.get('scope'),value['scope']):
                raise ValueError('warm native player/scope not live or mismatched')
            self.stats['warm_reads']+=1
            joint=self._joint(cw,context,value)
            _,_,after=self._identity()
            if before!=after:raise ValueError('native identity/vitals changed during warm read')
            if self._now()>=value['expires_at']:raise ValueError('control expired during warm read')
            hooks=self._make_hooks()
            self.hooks=hooks
            hooks.install()
            self.error=None;self.control=value
            self._small=self._small_state('armed',ready=True)
            self._small.update(warm_sampled_at=joint['sampled_at'],warm_snapshot_serial=joint['quality'].get('snapshot_serial'),
                single_pack=True,requires_serialized_server=True)
            return copy.deepcopy(self._small)
        except Exception as error:
            self.error=type(error).__name__+': '+str(error)
            self._remove_hooks()
            self._small=self._small_state('rejected',reason=self.error,control=validated)
            return copy.deepcopy(self._small)

    def ready_document(self):
        if self._closed:return self._small_state('closed')
        if self.error:return self._small_state('error',reason=self.error,control=self._attempt_control)
        if self.receipt is not None:return self._small_state(self.receipt['status'])
        if self.control and self._now()>=self.control['expires_at']:
            return self._small_state('expired',reason='armed identity retained; exact matching Load will abort')
        return copy.deepcopy(self._small) if self._small is not None else self._small_state('idle')

    def _base_receipt(self,status):
        self._serial+=1;c=self.control
        return dict(schema=SCHEMA,kind='ground_egress',status=status,serial=self._serial,
            request_id=c['request_id'],request_attempt=c['request_attempt'],issued_at=c['issued_at'],
            scope=copy.deepcopy(c['scope']),expected_pvid=c['expected_pvid'],expected_cw_sha256=c['expected_cw_sha256'],
            actual_cw_sha256=None)

    def _abort(self,reason,actual=None,untouched=True,status='aborted'):
        actual=actual or dict(scope=None,alive=False)
        row=self._base_receipt(status)
        report=dict(reason=str(reason)[:512],actual_scope=copy.deepcopy(actual.get('scope')),
            source_still_matches=self._source_ok(actual),source_untouched=untouched,
            original_load_allowed=not untouched,native_thread=self.native.thread())
        row.update(checkpoint_kind='abort-report',checkpoint_sha256=fingerprint(report),abort=report)
        if len(canonical(row))>MAX_WIRE_BYTES:raise ValueError('bounded abort receipt exceeds wire budget')
        self.receipt=row

    def _on_load_pvid(self,this,caller,message):
        if self.control is None or self._closed:return True
        self._callback_depth+=1
        pvid=None;actual=None
        try:
            pvid=self.native.pvid(message)
            if pvid!=self.control['expected_pvid']:
                self.stats['unrelated_loads']+=1
                if self.receipt is None:
                    _,_,actual=self._identity(this)
                    self._abort('active transaction interrupted by other LoadPVid: '+pvid,actual,untouched=False,status='interrupted')
                else:self.receipt['status']='recovery-required';self.receipt['interference']='another LoadPVid was allowed'
                return True
            if self.receipt is not None:
                self.stats['duplicate_loads']+=1;self.stats['loads_skipped']+=1
                return False
            cw,context,actual=self._identity(this)
            if (not self._live() or self.native.thread()!=self._thread or context.get('room_ready') is not True
                    or not self._source_ok(actual) or self._now()>=self.control['expires_at']):
                self._abort('matched Load expired, native scope/death/thread/context changed, or live marker lost',actual)
                self.stats['loads_skipped']+=1
                return False
            before=copy.deepcopy(actual)
            self.stats['final_reads']+=1
            joint=self._joint(cw,context,self.control)
            _,_,actual=self._identity(this)
            if before!=actual:raise ValueError('native identity/vitals changed during final joint read')
            if self._now()>=self.control['expires_at']:raise ValueError('control expired during final joint read')
            row=self._base_receipt('sealed')
            row.update(checkpoint_kind=JOINT_SCHEMA,checkpoint_sha256=fingerprint(joint),joint=joint,
                native_vitals=copy.deepcopy(actual.get('vitals')),native_thread=self._thread,
                source_still_matches=True,source_untouched=False,original_load_allowed=True,
                boundary='pre-GameClient::handleLoadPVid; successful original will destroy source')
            if len(canonical(row))>MAX_WIRE_BYTES-1024:raise ValueError('dedicated egress packet lacks1024byte final-envelope reserve')
            self.receipt=row;self.stats['original_load_allowed']+=1
            return True
        except Exception as error:
            if self.receipt is None:
                self._abort(type(error).__name__+': '+str(error),actual,untouched=pvid==self.control['expected_pvid'],
                    status='aborted' if pvid==self.control['expected_pvid'] else 'interrupted')
            if pvid==self.control['expected_pvid']:
                self.stats['loads_skipped']+=1
                return False
            return True
        finally:self._callback_depth-=1

    def _on_create_world(self,this,caller,data,length):
        if self.control is None or self._closed:return
        self._callback_depth+=1
        try:
            self.stats['createworlds']+=1
            if self.receipt is None:
                self._abort('CreateWorld arrived without final matched LoadPVid snapshot',untouched=False,status='interrupted')
            payload=self.native.payload(data,length)
            actual=hashlib.sha256(payload).hexdigest()
            row=self.receipt
            if row.get('checkpoint_kind')=='abort-report' and row['abort'].get('source_untouched'):
                row['abort']['source_untouched']=False
                row['abort']['original_load_allowed']=True
                row['abort']['reason']='CreateWorld followed pre-original abort; source cannot be presumed intact'
                row['checkpoint_sha256']=fingerprint(row['abort'])
            if row.get('actual_cw_sha256') is not None:
                row['status']='recovery-required';row['interference']='multiple native CreateWorld calls before exact ACK'
                return
            row['actual_cw_sha256']=actual;row['actual_cw_length']=len(payload)
            row['createworld_thread']=self.native.thread()
            if row['status']=='sealed' and actual==row['expected_cw_sha256'] and row['createworld_thread']==self._thread:
                row['status']='final'
            else:
                row['status']='recovery-required'
                row['interference']='actual CreateWorld payload/thread/status mismatches prepared transaction'
            if len(canonical(row))>MAX_WIRE_BYTES:

                self.error='egress final envelope exceeds wire bound; preserved captured cache'
        except Exception as error:
            self._unexpected_callback_error('create_world',error)
        finally:self._callback_depth-=1

    def _unexpected_callback_error(self,where,error):
        self.stats['callback_errors']+=1
        self.error=where+': '+type(error).__name__+': '+str(error)
        if self.receipt is None and self.control is not None:

            report=dict(reason=self.error[:512],actual_scope=None,source_still_matches=False,
                source_untouched=False,original_load_allowed=True,native_thread=None)
            self.receipt=self._base_receipt('recovery-required')
            self.receipt.update(checkpoint_kind='abort-report',checkpoint_sha256=fingerprint(report),abort=report)
        if self.receipt is not None:
            self.receipt['status']='recovery-required'
            self.receipt['interference']=self.error[:512]

    def outgoing_document(self):
        if self.receipt is None:return None
        if len(canonical(self.receipt))>MAX_WIRE_BYTES:return None
        return copy.deepcopy(self.receipt)

    @staticmethod
    def _ack_key(value):
        fields=('request_id','request_attempt','issued_at','scope','serial','checkpoint_sha256','actual_cw_sha256')
        if not isinstance(value,dict) or set(value)!=set(fields):return None
        return canonical(value)

    def ack(self,value):
        key=self._ack_key(value)
        if key is None:return False
        if key in self._acknowledged:return True
        if self._callback_depth:return False
        if self.native is not None and self.native.thread()!=self._thread:return False
        if self._pending_ack is not None:
            return self._finish_ack() if key==self._pending_ack['key'] else False
        if self.receipt is None:return False
        row=self.receipt
        expected={k:copy.deepcopy(row[k]) for k in ('request_id','request_attempt','issued_at','scope','serial','checkpoint_sha256','actual_cw_sha256')}
        if row['status']=='sealed' or key!=self._ack_key(expected):return False
        previous=self.control
        self._pending_ack=dict(key=key,value=copy.deepcopy(value),control=copy.deepcopy(previous))
        self.receipt=None;self.control=None;self._control_key=None
        return self._finish_ack()

    def _finish_ack(self):
        pending=self._pending_ack
        self._remove_hooks()
        if self.hooks is not None:
            self._small=self._small_state('error',reason=self.error or 'ACK hook cleanup incomplete',control=pending['control'])
            return False
        self._acknowledged.append(pending['key']);self._acknowledged=self._acknowledged[-32:]
        self._attempt_control=None;self.error=None
        self._small=self._small_state('acknowledged',control=pending['control'])
        self._small['acknowledged_receipt']=copy.deepcopy(pending['value'])
        self._pending_ack=None
        return True

    acknowledge=ack

    def _remove_hooks(self):
        if self.hooks is not None:
            hooks=self.hooks
            try:hooks.close();self.hooks=None
            except Exception as error:self.error='hook close refused: '+str(error)

    def close(self):
        if self._callback_depth:return False
        if self.native is not None and self._thread is not None and self.native.thread()!=self._thread:return False
        self._closed=True;self._remove_hooks()

        return self.hooks is None
