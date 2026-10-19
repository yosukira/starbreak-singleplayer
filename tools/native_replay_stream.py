"""Match decoded native messages to the same verified encounter journal.

Only the local controller uses the synthetic cipher for packet indexing. The
native bridge receives plaintext, typed frames through its owned loopback
channel; no game socket, cipher state or executable memory is transplanted.
"""
import base64
from localserver.protocol import RC4,random_string
from tools.replay_stream import ReplayStream,framed


class Identity:
    def crypt(self,data):return bytes(data)


class NativeReplayStream(ReplayStream):
    def __init__(self,client,server):
        rows=client['ops']
        if not any(row[0]=='message' and row[1]==10 for row in rows):
            raise ValueError('Native recording has no original world bootstrap')
        cipher=RC4(random_string(0,64))
        virtual=[['open'],['net',base64.b64encode(framed(1,b'')).decode()]]
        for row in rows:
            if row[0]=='message':
                data=framed(row[1],base64.b64decode(row[2],validate=True))
                virtual.append(['net',base64.b64encode(cipher.crypt(data)).decode()])
            else:virtual.append(['timer'])
        super().__init__(dict(ops=virtual),server)

    def progress(self,cursor):return super().progress(cursor+2)
    def transmitter(self,received):return Identity()
    def receiver(self,sent):return Identity()
