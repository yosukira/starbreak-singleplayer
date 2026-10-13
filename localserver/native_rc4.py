"""Optional system-libcrypto acceleration for the existing retail RC4 stream.

No protocol/key changes or new runtime dependency. Unsupported platforms keep
the Python implementation. CDLL calls release the GIL while encrypting bytes.
"""
import ctypes as C
import sys

class RC4State(C.Structure):
    _fields_=[('x',C.c_uint),('y',C.c_uint),('data',C.c_uint*256)]

def load():
    if not sys.platform.startswith('linux'):return None
    try:
        library=C.CDLL('libcrypto.so.3')
        library.RC4_set_key.argtypes=[C.POINTER(RC4State),C.c_int,C.c_void_p]
        library.RC4_set_key.restype=None
        library.RC4.argtypes=[C.POINTER(RC4State),C.c_size_t,C.c_void_p,C.c_void_p]
        library.RC4.restype=None
        state=RC4State();library.RC4_set_key(C.byref(state),3,b'Key')
        output=C.create_string_buffer(9)
        library.RC4(C.byref(state),9,b'Plaintext',output)
        if output.raw!=bytes.fromhex('bbf316e8d940af0ad3'):return None
        return library
    except (OSError,AttributeError):return None

LIBRARY=load()

def state_from_python(permutation,i,j):
    if LIBRARY is None:return None
    state=RC4State();state.x=i;state.y=j;state.data[:]=permutation
    return state

def crypt(state,data):
    data=bytes(data)
    if not data:return data
    output=C.create_string_buffer(len(data))
    LIBRARY.RC4(C.byref(state),len(data),data,output)
    return output.raw
