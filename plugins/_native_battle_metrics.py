"""Read clamped local-player health changes in the checked offline client.

The original modifyHitpoints(int, bool, bool) is called exactly once. Its
Win32 thiscall ABI and complete 144-byte body were checked in both supplied
1.14.6 executables. No damage calculation or game state is replaced.
"""
import hashlib
import os

try:
    from ._native_replay_hooks import NativeHook
except (ImportError, ValueError, SystemError):
    from _native_replay_hooks import NativeHook

RVA=0x175c0
SIZE=144
BODY='f0ecac1d439140c766ea94e3953f109add428b059a670087c07846d7811858b9'
PREFIX=bytes.fromhex('5589e557565383ec1c89ce8b018b5d0c')


class HealthTotals:
    def __init__(self):
        self.scope=None
        self.damage_taken=self.healing=0
        self.stack=[]

    def reset(self,scope):
        if scope!=self.scope:
            self.scope=scope;self.damage_taken=self.healing=0;self.stack=[]

    def before(self,hp):
        entry=[max(0,int(hp)),0]
        self.stack.append(entry)
        return entry

    def after(self,entry,hp):
        if not self.stack or self.stack[-1] is not entry:
            raise ValueError('Health observation order changed')
        self.stack.pop()
        change=max(0,int(hp))-entry[0]
        if self.stack:self.stack[-1][1]+=change
        if entry[0]>0:
            own=change-entry[1]
            self.damage_taken+=max(0,-own);self.healing+=max(0,own)

    def document(self):
        return dict(protocol='player-metrics-v1',damage_taken=self.damage_taken,healing=self.healing)


class NativeHealthMetrics:
    def __init__(self,ffi,lib,refs,base,hook=NativeHook):
        if ffi.sizeof('void *')!=4:raise ValueError('Health metrics require the checked Win32 client')
        body=bytes(ffi.buffer(ffi.cast('const char *',base+RVA),SIZE))
        if hashlib.sha256(body).hexdigest()!=BODY:
            raise ValueError('Native health code differs from the checked client')
        self.ffi,self.refs=ffi,refs;self.totals=HealthTotals()
        self.hook=hook(ffi,lib,base+RVA,PREFIX,result='void',
            arguments=('int','unsigned int','unsigned int'),convention='thiscall',replace=self.observe)
        self.hook.install()

    def local(self,pointer=None):
        client=self.refs.WorldClient
        if client is None or client==self.ffi.NULL or client.clientWorld==self.ffi.NULL:return None
        world=client.clientWorld;player=world.player
        if player==self.ffi.NULL or pointer is not None and self.ffi.cast('void *',player)!=pointer:return None
        props=player.asClientObject.asNativeObject.asWorldObject.props
        scope=(int(self.ffi.cast('uintptr_t',world)),int(world.asWorld.props.seed))
        self.totals.reset(scope)
        return props

    def observe(self,original,pointer,*args):
        props=self.local(pointer)
        if props is None:return original(pointer,*args)
        entry=self.totals.before(props.hitpoints)
        result=original(pointer,*args)
        self.totals.after(entry,props.hitpoints)
        return result

    def document(self):
        if self.hook.error or self.local() is None:return {}
        return self.totals.document()


_ACTIVE=None


def create(ffi,lib,refs):
    global _ACTIVE
    if os.environ.get('HARDLAB_LIVE_SESSION')!='1':return None
    if _ACTIVE is None:
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p]
        kernel.GetModuleHandleW.restype=ctypes.c_void_p
        base=kernel.GetModuleHandleW(None)
        if not base:raise OSError('Native health module was not found')
        _ACTIVE=NativeHealthMetrics(ffi,lib,refs,int(base))
    else:_ACTIVE.refs=refs
    return _ACTIVE
