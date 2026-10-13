"""Wire protocol used by the retail StarBreak client.

Frame layout (from ServerConnection::sendMessage @0x472b30 and
ServerConnection::doRead @0x474700):

    type    : 1 byte
    length  : uint32 network byte order (big endian), payload byte count
    payload : protobuf message bytes

Both sides write the length through ``htonl`` (IAT 0x6b07a0) and read it
through ``ntohl`` (IAT 0x6b07b0), so the frame header is big endian even
though the client runs on a little-endian host.

The first server frame (MT_HELLO) is plaintext.  The client answers with
MT_CONNECT, which the retail client RSA encrypts with the server's public
key (ServerConnection::sendMessage special cases MessageType 3);
``tools/make_local_client.py`` patches that branch away so the local
server can read ``enckey_`` directly.  Both sides then derive a 64-byte
key with the client's ``randUtil::randomString`` algorithm (seed =
``enckey_``) and use two independent RC4 streams - one per direction,
each freshly keyed with the same 64 bytes - for every later frame
(ServerConnection::setRC4Keys @0x473300 calls RC4_setup twice).
"""

import socket
import struct
from . import native_rc4

class ProtocolError(IOError):
    pass

class PRNG(object):
    """Exact port of the client's PRNG::next() (shared/util/PRNG.cc)."""

    def __init__(self, seed):
        self.s0 = 0x24bcce84
        self.s1 = int(seed) & 0xffffffff
        self.s2 = 0x159a55e5
        self.s3 = 0x1f123bb5

    def next(self):
        s0, s1, s2, s3 = self.s0, self.s1, self.s2, self.s3
        mixed = ((s1 << 11) & 0xffffffff) ^ s1
        self.s1 = s2
        self.s3 = s0
        self.s2 = s3
        value = ((s0 >> 19) ^ s0 ^ mixed ^ (mixed >> 8)) & 0xffffffff
        self.s0 = value
        return value

def random_string(seed, length):
    """Port of randUtil::randomString(uint32 seed, int length)."""
    generator = PRNG(seed)
    return bytes(generator.next() % 0xff for _ in range(int(length)))

class RC4(object):
    def __init__(self, key):
        key = bytearray(key)
        self.s = list(range(256))
        j = 0
        for i in range(256):
            j = (j + self.s[i] + key[i % len(key)]) & 0xff
            self.s[i], self.s[j] = self.s[j], self.s[i]
        self.i = 0
        self.j = 0
        self._native=native_rc4.state_from_python(self.s,self.i,self.j)
        if self._native is not None:self.s=self._native.data

    def crypt(self, data):
        if self._native is not None:
            output=native_rc4.crypt(self._native,data)
            self.i=self._native.x;self.j=self._native.y
            return output
        output = bytearray(data)
        s = self.s
        i = self.i
        j = self.j
        for index in range(len(output)):
            i = (i + 1) & 0xff
            j = (j + s[i]) & 0xff
            s[i], s[j] = s[j], s[i]
            output[index] ^= s[(s[i] + s[j]) & 0xff]
        self.i = i
        self.j = j
        return bytes(output)

MAX_FRAME_SIZE = 16 * 1024 * 1024

class FrameChannel(object):
    """Read/write framed messages over one socket."""

    def __init__(self, sock):
        self.sock = sock
        self.rx = None
        self.tx = None

    def _read_exact(self, length):
        chunks = []
        remaining = length
        while remaining:
            chunk = self.sock.recv(remaining)
            if not chunk:
                raise ProtocolError('connection closed')
            chunks.append(chunk)
            remaining -= len(chunk)
        return b''.join(chunks)

    def read_frame(self):
        header = self._read_exact(5)
        if self.rx is not None:
            header = self.rx.crypt(header)
        message_type = header[0]
        length = struct.unpack('>i', header[1:5])[0]
        if length < 0 or length > MAX_FRAME_SIZE:
            raise ProtocolError('bad frame length: {0}'.format(length))
        payload = self._read_exact(length) if length else b''
        if self.rx is not None and payload:
            payload = self.rx.crypt(payload)
        return message_type, payload

    def send_frame(self, message_type, payload):
        self.send_frames(((message_type,payload),))

    def send_frames(self, frames):
        data=b''.join(bytes([int(kind)&0xff])+struct.pack('>i',len(payload))+bytes(payload)
            for kind,payload in frames)
        if self.tx is not None:
            data = self.tx.crypt(data)
        if data:self.sock.sendall(data)

    def enable_rc4(self, key):
        self.rx = RC4(key)
        self.tx = RC4(key)

def connect(host, port, timeout=10.0):
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.settimeout(None)
    return FrameChannel(sock)
