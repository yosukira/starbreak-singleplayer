"""Locate a verified controller boundary in the original client's wire stream.

TCP/WebSocket chunks can stop in the middle of a packet, and one controller
step can emit several packets. A handoff therefore retains the installed
client and its unread queue, rebuilds through the covering server step, then
delivers only the missing suffix before fresh live updates.
"""
import base64
import bisect
import hashlib
import math
import struct
from dataclasses import dataclass

from localserver import pb_layout
from localserver.protocol import MAX_FRAME_SIZE, RC4, random_string
from localserver.replay import MAX_JOURNAL_BYTES, MAX_FRAMES, packet_digest


def framed(kind, payload):
    return bytes([kind])+struct.pack('>i', len(payload))+payload


@dataclass(frozen=True)
class Boundary:
    frame: int
    start: int
    end: int


class ReplayStream:
    def __init__(self, client, server, *, plaintext=False):
        operations = client.get('ops')
        frames = server.get('frames')
        if not isinstance(operations, list) or not isinstance(frames, list) or not 1 <= len(frames) <= MAX_FRAMES:
            raise ValueError('Invalid handoff journals')
        # Prefix counters let the backend verify a reported playback cursor,
        # rather than trusting arbitrary byte offsets from a browser request.
        self.received, self.sent = [0], [0]
        epochs=[]
        def new_epoch(received,sent):
            value=dict(start=received,sent_start=sent,chunks=[],connect_length=None)
            epochs.append(value);return value
        epoch=new_epoch(0,0)
        for op in operations:
            received,sent=self.received[-1],self.sent[-1]
            if op[0]=='open' and (epoch['chunks'] or epoch['connect_length'] is not None):
                epoch=new_epoch(received,sent)
            if op[0]=='net':
                data=bytes(op[1]) if plaintext else base64.b64decode(op[1],validate=True)
                received+=len(data)
                if received>MAX_JOURNAL_BYTES:raise ValueError('Handoff wire stream too large')
                epoch['chunks'].append(data)
            elif op[0]=='send':
                length=op[1]
                if type(length) is not int or not 0<=length<=MAX_FRAME_SIZE:raise ValueError('Invalid client send length')
                if epoch['connect_length'] is None:epoch['connect_length']=length
                sent+=length
            epoch['end'],epoch['sent_end']=received,sent
            self.received.append(received);self.sent.append(sent)
        # Each WebSocket connection has a plaintext Hello and fresh cipher
        # states. An exit/reconnect later in a tape must not invalidate an
        # earlier battle or carry its cipher offset into the next connection.
        self.key=random_string(0,64)
        raw_parts=[];plain_parts=[];packets=[];packet_epochs=[]
        for epoch in epochs:
            raw=b''.join(epoch.pop('chunks'));raw_parts.append(raw)
            if not raw:plain_parts.append(raw);continue
            if raw[0]!=1:raise ValueError('Missing original Hello frame')
            if len(raw)<5:plain_parts.append(raw);continue
            size=struct.unpack('>i',raw[1:5])[0]
            if not 0<=size<=65536:raise ValueError('Invalid original Hello frame')
            hello=size+5;epoch['hello_end']=epoch['start']+hello
            if hello>len(raw):plain_parts.append(raw);continue
            plain=raw if plaintext else raw[:hello]+RC4(self.key).crypt(raw[hello:]);plain_parts.append(plain)
            offset=hello
            while offset+5<=len(plain):
                kind=plain[offset];size=struct.unpack('>i',plain[offset+1:offset+5])[0]
                if not 0<=size<=MAX_FRAME_SIZE:raise ValueError('Invalid original replay frame size')
                end=offset+5+size
                if end>len(plain):break
                packets.append((kind,plain[offset+5:end],epoch['start']+offset,epoch['start']+end))
                packet_epochs.append(epoch);offset=end
        self.raw,self.plain=b''.join(raw_parts),b''.join(plain_parts)
        first = next((row for row in frames if row.get('packets')), None)
        if first is None:
            raise ValueError('Replay contains no controller output')
        for row in frames:
            if type(row.get('packets')) is not int or not 0 <= row['packets'] <= 4096:
                raise ValueError('Invalid controller packet count')
        # _read_loop sends CreatePlayer immediately before _start_live. Anchor
        # there instead of guessing from repeated, identical idle tick packets.
        layouts=pb_layout.default_layouts()
        # Locate the top-level bootstrap field without recursively decoding
        # every projectile and object in every subsequent world update. Only
        # candidate CreatePlayer packets need their full protobuf validation.
        players = [i for i, packet in enumerate(packets) if packet[0] == 12
                   and 'createplayer_' in layouts.observe_wire_types('Delta',packet[1])
                   and 'createplayer_' in layouts.decode('Delta', packet[1])]
        sequence = server.get('world_sequence')
        if sequence is None:
            if len(players) != 1:
                raise ValueError('Ambiguous controller world in replay')
            sequence = 1
        if type(sequence) is not int or not 1 <= sequence <= len(players):
            raise ValueError('Missing controller world in replay')
        selected=players[sequence-1]
        epoch=packet_epochs[selected]
        self.select_epoch(epoch)
        self.boundary_epochs={}
        cursor, boundaries = selected+1, []
        for index, row in enumerate(frames):
            if row.get('action')=='enter':
                sequence=row.get('world_sequence')
                if type(sequence)is not int or not 1<=sequence<=len(players):break
                selected=players[sequence-1]
                epoch=packet_epochs[selected];cursor=selected+1
            count = row['packets']
            if not count:
                if row['sha256'] != hashlib.sha256().hexdigest():
                    raise ValueError('Client and server replay streams do not match')
                continue
            if cursor+count > len(packets) or any(item is not epoch for item in packet_epochs[cursor:cursor+count]):
                break  # A recording may end before the next wire frame.
            group = packets[cursor:cursor+count]
            if self.digest(group) != row['sha256']:
                raise ValueError('Client and server replay streams do not match')
            boundaries.append(Boundary(index, group[0][2], group[-1][3]))
            self.boundary_epochs[index]=epoch
            cursor += count
        if not boundaries:
            raise ValueError('Replay contains no matching controller output')
        self.boundaries = boundaries
        self.ends = [row.end for row in self.boundaries]

    def reconcile(self, frame, ordinal, kind, payload, session):
        if kind!=12:return payload
        if getattr(self,'_packet_frame',None)!=frame:
            boundary=next((b for b in self.boundaries if b.frame==frame),None)
            self._packet_frame=frame;self._packet_rows=[]
            if boundary:
                pos=boundary.start
                while pos<boundary.end:
                    size=struct.unpack('>i',self.plain[pos+1:pos+5])[0]
                    self._packet_rows.append((self.plain[pos],self.plain[pos+5:pos+5+size]));pos+=size+5
        if ordinal>=len(self._packet_rows):return payload
        expected_kind,expected=self._packet_rows[ordinal]
        if expected_kind!=kind or expected==payload:return payload
        layouts=pb_layout.default_layouts()
        actual=layouts.decode('Delta',payload);wanted=layouts.decode('Delta',expected)
        a=actual.get('worldupdate_',{}).get('generalupdates_',[])
        b=wanted.get('worldupdate_',{}).get('generalupdates_',[])
        if len(a)!=len(b):return payload
        projectiles=getattr(getattr(session,'projectiles',None),'projectiles',[])
        changed=False
        for current,recorded in zip(a,b):
            if current==recorded:continue
            create=current.get('create_',{});other=recorded.get('create_',{})
            props=create.get('props_',{});old=other.get('props_',{})
            facing=props.get('facing_');original=old.get('facing_')
            if type(facing)is not int or type(original)is not int or abs(facing-original)!=1:return payload
            projectile=next((p for p in projectiles if p.objid==create.get('objid_')),None)
            if projectile is None or not projectile.rotate or projectile.homing:return payload
            angle=math.degrees(math.atan2(projectile.vy,projectile.vx));integer=round(angle)
            # libm/WASM atan2 can straddle an exact integer by one ULP. Only
            # this cosmetic creation angle may differ: positions, velocity,
            # damage, RNG and every other protocol field must remain identical.
            if abs(angle-integer)>1e-10 or integer not in (facing,original):return payload
            props['facing_']=original;changed=True
        return expected if changed and actual==wanted else payload

    def select_epoch(self,epoch):
        self.epoch=epoch
        self.hello_end=epoch['hello_end']
        self.connect_length=epoch['connect_length']

    @staticmethod
    def digest(packets):
        result = hashlib.sha256()
        for kind, payload, *_ in packets:
            packet_digest(result, kind, payload)
        return result.hexdigest()

    def progress(self, cursor):
        if type(cursor) is not int or not 0 <= cursor < len(self.received):
            raise ValueError('Invalid playback cursor')
        return self.received[cursor], self.sent[cursor]

    def boundary(self, byte_count):
        if type(byte_count) is not int or byte_count <= self.boundaries[0].start:
            raise ValueError('No controller step has reached the client yet')
        index = bisect.bisect_left(self.ends, byte_count)
        if index >= len(self.boundaries):
            raise ValueError('Playback has passed the verified controller journal')
        boundary=self.boundaries[index]
        if byte_count<=boundary.start:
            raise ValueError('Wait until room loading has completed before taking over')
        self.select_epoch(self.boundary_epochs[boundary.frame])
        return boundary

    def catchup(self, byte_count):
        boundary = self.boundary(byte_count)
        return boundary, self.plain[byte_count:boundary.end]

    def cipher_at(self, encrypted_bytes, direction=None):
        if type(encrypted_bytes) is not int or not 0<=encrypted_bytes<=MAX_JOURNAL_BYTES:raise ValueError('Invalid replay cipher offset')
        key=(self.epoch['start'],direction)
        cache=getattr(self,'_ciphers',{});previous=cache.get(key)
        cipher=RC4(self.key);remaining=encrypted_bytes
        if previous and previous[0]<=encrypted_bytes:
            from localserver.protocol import native_rc4
            offset,state,i,j=previous
            cipher.s=state.copy();cipher.i=i;cipher.j=j
            cipher._native=native_rc4.state_from_python(cipher.s,i,j)
            if cipher._native is not None:cipher.s=cipher._native.data
            remaining-=offset
        while remaining:
            count=min(remaining,65536);cipher.crypt(bytes(count));remaining-=count
        cache[key]=(encrypted_bytes,[int(cipher.s[i]) for i in range(256)],cipher.i,cipher.j)
        self._ciphers=cache
        return cipher

    def transmitter(self, received_bytes):
        self.boundary(received_bytes)
        return self.cipher_at(received_bytes-self.hello_end,'tx')

    def receiver(self, sent_bytes):
        if self.connect_length is None or not self.epoch['sent_start']+self.connect_length <= sent_bytes <= self.epoch['sent_end']:
            raise ValueError('Missing original CONNECT')
        return self.cipher_at(sent_bytes-self.epoch['sent_start']-self.connect_length,'rx')
