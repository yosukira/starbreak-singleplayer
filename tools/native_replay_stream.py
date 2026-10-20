"""Match decoded native messages to the same verified encounter journal.

Packet indexing uses the original plaintext frames directly. The native
bridge receives plaintext, typed frames through its owned loopback
channel; no game socket, cipher state or executable memory is transplanted.
"""
import base64
from tools.replay_stream import ReplayStream,framed


class Identity:
    def crypt(self,data):return bytes(data)


class NativeReplayStream(ReplayStream):
    def __init__(self,client,server):
        rows=client['ops']
        if not any(row[0]=='message' and row[1]==10 for row in rows):
            raise ValueError('Native recording has no original world bootstrap')
        virtual=[['open'],['net',framed(1,b'')]]
        for row in rows:
            if row[0]=='message':
                data=framed(row[1],base64.b64decode(row[2],validate=True))
                virtual.append(['net',data])
            else:virtual.append(['timer'])
        # This is an internal typed-byte path, never a flag read from a file.
        # Keep the shared packet bounds, world anchor and controller digests.
        super().__init__(dict(ops=virtual),server,plaintext=True)

    def progress(self,cursor):return super().progress(cursor+2)
    def transmitter(self,received):return Identity()
    def receiver(self,sent):return Identity()
