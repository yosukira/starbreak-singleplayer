"""Observe the original local-character death notification, never infer a death from loading."""
import hashlib
import os

try:
    from ._native_replay_hooks import NativeHook
except (ImportError, ValueError, SystemError):
    from _native_replay_hooks import NativeHook

RVA=0xdb4d0
BODY='8cd3247dff93720eb502990dbd6903199652b59bd7415af205219ac7c28b2fa5'
PREFIX=bytes.fromhex('5589e55383ec1489cb8b4154890424e8')


class NativeDeath:
    def __init__(self,ffi,lib,refs,base,hook=NativeHook):
        code=bytes(ffi.buffer(ffi.cast('const char *',base+RVA),48))
        target,signature=base+RVA,PREFIX;self.shared_owner=None
        if hashlib.sha256(code).hexdigest()!=BODY:
            try:from ._native_replay_hooks import shared_hook_target
            except (ImportError,ValueError,SystemError):from _native_replay_hooks import shared_hook_target
            target,signature,self.shared_owner=shared_hook_target(ffi,lib,refs,'WorldClient::handlePlayerCharacterKilled',target,PREFIX)
            size=len(signature)-5;code=PREFIX[:size]+code[size:]
        if ffi.sizeof('void *')!=4 or hashlib.sha256(code).hexdigest()!=BODY:raise ValueError('Native death notification differs from the checked client')
        self.ffi,self.refs=ffi,refs;self.scope=None;self.last=None;self.dead=False;self.missing=False
        self.hook=hook(ffi,lib,target,signature,result='void',arguments=(),convention='thiscall',replace=self.observe)
        self.hook.install()

    def identity(self,world):
        return (int(self.ffi.cast('uintptr_t',world)),int(world.asWorld.props.seed))

    def prepare_replay(self):
        client=self.refs.WorldClient
        # This verified original callback only destroys WorldClient.overlay
        # and clears its pointer. Bypass notification hooks during UI cleanup.
        if client is not None and client!=self.ffi.NULL and client.overlay!=self.ffi.NULL:self.hook.original(client)
        self.scope=None;self.last=None;self.dead=False;self.missing=False

    def observe(self,original,client):
        if client==self.refs.WorldClient:
            world=self.refs.WorldClient.clientWorld
            if world!=self.ffi.NULL and self.identity(world)==self.scope and self.last is not None:self.dead=True;self.missing=False
        return original(client)

    def document(self,world,value):
        scope=self.identity(world)
        if scope!=self.scope:self.scope=scope;self.last=None;self.dead=False;self.missing=False
        if self.dead and self.last is not None:
            if not value.get('objid') or value.get('hp',0)<=0:self.missing=True
            elif getattr(self,'missing',False):self.dead=False;self.missing=False
            if self.dead:return dict(self.last,hp=0,alive=False)
        if value.get('objid',0)>0:self.last=dict(value)
        return value


_ACTIVE=None
def create(ffi,lib,refs):
    global _ACTIVE
    if os.environ.get('HARDLAB_LIVE_SESSION')!='1':return None
    if _ACTIVE is None:
        import ctypes
        kernel=ctypes.windll.kernel32
        kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p];kernel.GetModuleHandleW.restype=ctypes.c_void_p
        base=kernel.GetModuleHandleW(None)
        if not base:raise OSError('Native module unavailable')
        _ACTIVE=NativeDeath(ffi,lib,refs,int(base))
    else:_ACTIVE.refs=refs
    return _ACTIVE
