"""Win32 call adapters for the offline native replay boundary.

This module does not install anything on import. The caller must supply a
matching executable signature and retain every callback until its hook has
been removed. Compatible with SBPE's embedded Python 3.5.
"""
import struct
import os

try:
    _RETAINED_HOOKS
except NameError:
    _RETAINED_HOOKS=set()
try:
    _RETIRED_HOOKS
except NameError:
    _RETIRED_HOOKS=set()
MAX_RETIRED_HOOKS=128


def uint32(value):
    if type(value) is not int or not 0 < value <= 0xffffffff:
        raise ValueError('Invalid native code address')
    return struct.pack('<I', value)


def argument_count(value):
    if type(value) is not int or not 0 <= value <= 6:
        raise ValueError('Unsupported native argument count')
    return value


def shared_hook_target(ffi,lib,refs,name,target,expected):
    """Observe the verified original trampoline of known SBPE hooks.

    Their callbacks remain installed. Unknown patches still fail closed; a
    known owner's trampoline must contain the original prologue followed by
    a jump back to this exact executable entry.
    """
    actual=bytes(ffi.buffer(ffi.cast('const char *',target),len(expected)))
    if actual==expected:return target,expected,None
    active=getattr(getattr(refs,'manager',None),'_active',{})
    owner=None;handle=None
    if name=='WorldClient::updateInput':
        owner=getattr(active.get('plugin_combat_assist'),'native',None)
        handle=getattr(owner,'update_input_hook',None)
    elif name=='MessageHandlers::run':
        owner=getattr(active.get('plugin_combat_recorder'),'_net',None)
        handle=getattr(owner,'_hook',None)
    elif name=='ServerConnection::sendMessage':
        owner=getattr(active.get('plugin_combat_assist'),'research_wire',None)
        for candidate in getattr(owner,'hooks',[]):
            if candidate==ffi.NULL or not lib.subhook_is_installed(candidate):continue
            pointer=lib.subhook_get_trampoline(candidate)
            if pointer==ffi.NULL:continue
            address=int(ffi.cast('uintptr_t',pointer));code=bytes(ffi.buffer(ffi.cast('const char *',pointer),len(expected)+5))
            for size in range(5,len(expected)+1):
                if code[:size]==expected[:size] and code[size:size+1]==b'\xe9':
                    destination=(address+size+5+struct.unpack('<i',code[size+1:size+5])[0])&0xffffffff
                    if destination==(target+size)&0xffffffff:return address,code[:size+5],owner
    elif name=='WorldClient::handlePlayerCharacterKilled':
        owner=getattr(active.get('plugin_combat_assist'),'native',None)
        for candidate in getattr(owner,'lifecycle_hooks',[]):
            if candidate==ffi.NULL or not lib.subhook_is_installed(candidate):continue
            pointer=lib.subhook_get_trampoline(candidate)
            if pointer==ffi.NULL:continue
            address=int(ffi.cast('uintptr_t',pointer));code=bytes(ffi.buffer(ffi.cast('const char *',pointer),len(expected)+5))
            for size in range(5,len(expected)+1):
                if code[:size]==expected[:size] and code[size:size+1]==b'\xe9':
                    destination=(address+size+5+struct.unpack('<i',code[size+1:size+5])[0])&0xffffffff
                    if destination==(target+size)&0xffffffff:return address,code[:size+5],owner
    if (actual[:1]!=b'\xe9' or owner is None or handle is None or handle==ffi.NULL
            or not lib.subhook_is_installed(handle)):
        raise ValueError('Native replay conflicts with another code patch: '+name)
    pointer=lib.subhook_get_trampoline(handle)
    if pointer==ffi.NULL:raise ValueError('Existing native hook has no original trampoline: '+name)
    address=int(ffi.cast('uintptr_t',pointer))
    code=bytes(ffi.buffer(ffi.cast('const char *',pointer),len(expected)+5))
    for size in range(5,len(expected)+1):
        if code[:size]!=expected[:size] or code[size:size+1]!=b'\xe9':continue
        destination=(address+size+5+struct.unpack('<i',code[size+1:size+5])[0])&0xffffffff
        if destination==(target+size)&0xffffffff:return address,code[:size+5],owner
    raise ValueError('Existing native hook does not preserve verified code: '+name)


def thiscall_callback(callback, count):
    """thiscall -> cdecl callback(this, arguments...), including callee pop."""
    count = argument_count(count)
    code = bytearray(b'\x55\x89\xe5')
    for index in reversed(range(count)):
        code.extend(b'\xff\x75' + bytes([8 + index*4]))
    code.extend(b'\x51\xb8' + uint32(callback) + b'\xff\xd0')
    code.extend(b'\x83\xc4' + bytes([(count+1)*4]) + b'\xc9')
    code.extend(b'\xc2' + struct.pack('<H', count*4) if count else b'\xc3')
    return bytes(code)


def thiscall_original(trampoline, count):
    """cdecl adapter(this, arguments...) -> original thiscall trampoline."""
    count = argument_count(count)
    code = bytearray(b'\x55\x89\xe5')
    for index in reversed(range(count)):
        code.extend(b'\xff\x75' + bytes([12 + index*4]))
    code.extend(b'\x8b\x4d\x08\xb8' + uint32(trampoline) + b'\xff\xd0\xc9\xc3')
    return bytes(code)


class NativeHook:
    """A checked hook with a separately callable, permanently retained original.

    `replace(original, *args)` owns the decision to call or suppress the
    original during playback. Without a replacement, calls pass through once.
    The caller must check that the target belongs to its owned offline client.
    """
    def __init__(self, ffi, lib, target, expected, result='void', arguments=(),
                 convention='thiscall', replace=None, kernel=None):
        if ffi.sizeof('void *') != 4:
            raise ValueError('Native replay hooks require the 32-bit client ABI')
        uint32(target)
        if not isinstance(expected, bytes) or not 8 <= len(expected) <= 32:
            raise ValueError('A native executable signature is required')
        if convention not in ('thiscall', 'cdecl'):
            raise ValueError('Unknown native call convention')
        if result not in ('void', 'int', '_Bool', 'unsigned int', 'unsigned long long'):
            raise ValueError('Unsupported native result type')
        arguments = tuple(arguments)
        argument_count(len(arguments))
        if any(arg not in ('void *', 'const char *', 'int', 'unsigned int') for arg in arguments):
            raise ValueError('Unsupported native argument type')
        self.ffi,self.lib,self.target,self.expected = ffi,lib,target,expected
        self.result,self.arguments,self.convention = result,arguments,convention
        self.replace = replace
        self.kernel = kernel
        self.hook = self.callback = self.original = None
        self.allocations = []
        self.error = None
        self.active_calls = 0
        self.exposed = self.retired = False

    def _kernel(self):
        if self.kernel is not None:return self.kernel
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.VirtualAlloc.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32,ctypes.c_uint32]
        kernel.VirtualAlloc.restype = ctypes.c_void_p
        kernel.VirtualFree.argtypes = [ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint32]
        kernel.VirtualFree.restype = ctypes.c_int
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.FlushInstructionCache.argtypes = [ctypes.c_void_p,ctypes.c_void_p,ctypes.c_size_t]
        kernel.FlushInstructionCache.restype = ctypes.c_int
        self.kernel = kernel
        return kernel

    def allocate(self, code):
        import ctypes
        kernel = self._kernel()
        address = kernel.VirtualAlloc(None,len(code),0x3000,0x40)
        if not address:raise MemoryError('Could not allocate native replay adapter')
        address = int(address);self.allocations.append(address)
        ctypes.memmove(address,code,len(code))
        if not kernel.FlushInstructionCache(kernel.GetCurrentProcess(),address,len(code)):
            raise OSError('Could not flush native replay adapter')
        return address

    def check(self):
        if os.environ.get('HARDLAB_LIVE_SESSION')!='1':
            raise ValueError('Native replay hooks are restricted to the offline simulator')
        ffi = self.ffi
        if bytes(ffi.buffer(ffi.cast('const char *',self.target),len(self.expected))) != self.expected:
            raise ValueError('Native client code differs from the verified executable')

    def install(self):
        if self.retired:raise RuntimeError('Create a new native hook after removal')
        if self.hook is not None:return self
        if len(_RETIRED_HOOKS)>=MAX_RETIRED_HOOKS:
            raise RuntimeError('Native replay hook reload limit reached')
        self.check()
        ffi = self.ffi
        args = (('void *',)+self.arguments if self.convention=='thiscall' else self.arguments)
        signature = self.result+'('+(', '.join(args) or 'void')+')'
        def invoke(values):
            if self.replace is None:return self.original(*values)
            called = [False, None]
            def original(*forwarded):
                if called[0]:raise RuntimeError('Native original was called twice')
                called[0] = True
                called[1] = self.original(*forwarded)
                return called[1]
            try:return self.replace(original,*values)
            except Exception as error:
                # Recorder failure cannot execute a game update twice or drop
                # an otherwise live native call. Keep only the error type.
                self.error = type(error).__name__
                return called[1] if called[0] else original(*values)
        def dispatch(*values):
            self.active_calls += 1
            try:return invoke(values)
            finally:self.active_calls -= 1
        self.callback = ffi.callback(signature,dispatch)
        address = int(ffi.cast('uintptr_t',self.callback))
        try:
            if self.convention=='thiscall':address = self.allocate(thiscall_callback(address,len(self.arguments)))
            hook = self.lib.subhook_new(ffi.cast('void *',self.target),ffi.cast('void *',address),0)
            if hook==ffi.NULL:raise RuntimeError('Could not create native replay hook')
            self.hook = hook
            _RETAINED_HOOKS.add(self)
            trampoline = self.lib.subhook_get_trampoline(hook)
            if trampoline==ffi.NULL:raise RuntimeError('Native replay hook has no original trampoline')
            original = int(ffi.cast('uintptr_t',trampoline))
            if self.convention=='thiscall':original = self.allocate(thiscall_original(original,len(self.arguments)))
            self.original = ffi.cast(self.result+'(*)('+(', '.join(args) or 'void')+')',original)
            self.exposed=True
            self.lib.subhook_install(hook)
            if not self.lib.subhook_is_installed(hook):raise RuntimeError('Could not install native replay hook')
            return self
        except Exception:
            self.remove();raise

    def remove(self):
        if self.retired:return
        if self.active_calls:
            raise RuntimeError('A native replay call is still running')
        if self.hook is not None:
            self.lib.subhook_remove(self.hook)
            # Do not free an adapter while the branch might still be live.
            if self.lib.subhook_is_installed(self.hook):
                raise RuntimeError('Native replay hook is still installed')
            if self.exposed:
                # A native thread may have just returned from the Python
                # callback but still be executing its return stub/trampoline.
                # Retain these tiny, bounded allocations until process exit;
                # removing the branch does not prove such threads quiescent.
                self.retired=True
                _RETIRED_HOOKS.add(self);_RETAINED_HOOKS.discard(self)
                return
            self.lib.subhook_free(self.hook);self.hook=None
        kernel = self.kernel
        while self.allocations:
            address = self.allocations[-1]
            if not kernel.VirtualFree(address,0,0x8000):raise OSError('Could not free native replay adapter')
            self.allocations.pop()
        self.original=self.callback=None
        _RETAINED_HOOKS.discard(self)


class NativeHookGroup:
    """Check the whole boundary before changing code; roll back partial setup."""
    def __init__(self,hooks):
        self.hooks=tuple(hooks)
        self.installed=False
        if not self.hooks or len({hook.target for hook in self.hooks})!=len(self.hooks):
            raise ValueError('Native replay hooks must have distinct targets')

    def install(self):
        if self.installed:return self
        for hook in self.hooks:hook.check()
        try:
            for hook in self.hooks:hook.install()
        except Exception:
            self.remove()
            raise
        self.installed=True
        return self

    def remove(self):
        error=None
        for hook in reversed(self.hooks):
            try:hook.remove()
            except Exception as current:error=error or current
        self.installed=False
        if error is not None:raise error
