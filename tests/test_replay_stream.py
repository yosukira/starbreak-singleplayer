import base64
import copy
import struct
import unittest
from unittest import mock

from localserver import game_settings, pb_layout
from localserver.replay import ServerCapture, ServerReplay
from localserver.protocol import RC4, random_string
from tools.replay_stream import ReplayStream, framed
from test_replay import encounter, input_at


class ReplayStreamTests(unittest.TestCase):
    def test_reconciliation_does_not_scan_all_prior_frames(self):
        import struct
        from types import SimpleNamespace
        client,server,_=self.documents()
        wire=ReplayStream(client,server)
        boundary=wire.boundaries[-1]
        offset=boundary.start
        size=struct.unpack('>i',wire.plain[offset+1:offset+5])[0]
        payload=wire.plain[offset+5:offset+5+size]
        class NoScan(list):
            def __iter__(self):raise AssertionError('Repeated full history scan')
        wire.boundaries=NoScan(wire.boundaries)
        self.assertEqual(wire.reconcile(boundary.frame,0,12,payload,SimpleNamespace()),payload)

    def test_native_plaintext_index_matches_legacy_cipher_and_keeps_verification(self):
        from tools.native_replay_stream import NativeReplayStream
        browser,server,_=self.documents()
        plain=ReplayStream(browser,server).plain
        offset=5+struct.unpack('>i',plain[1:5])[0]
        operations=[['message',10,'']]
        while offset<len(plain):
            size=struct.unpack('>i',plain[offset+1:offset+5])[0]
            operations.append(['message',plain[offset],base64.b64encode(plain[offset+5:offset+5+size]).decode()])
            operations.append(['state',[0,0]]);offset+=5+size
        cipher=RC4(random_string(0,64));virtual=[['open'],['net',base64.b64encode(framed(1,b'')).decode()]]
        for row in operations:
            virtual.append(['net',base64.b64encode(cipher.crypt(framed(row[1],base64.b64decode(row[2])))).decode()] if row[0]=='message' else ['timer'])
        legacy=ReplayStream(dict(ops=virtual),server)
        with mock.patch('tools.replay_stream.RC4',side_effect=AssertionError('Native plaintext must not be encrypted again')):
            native=NativeReplayStream(dict(ops=operations),server)
            self.assertEqual(native.plain,legacy.plain)
            self.assertEqual(native.received,legacy.received);self.assertEqual(native.sent,legacy.sent)
            self.assertEqual(native.boundaries,legacy.boundaries)
            for boundary in native.boundaries:
                position=boundary.start+1
                self.assertEqual(native.catchup(position),legacy.catchup(position))
                self.assertEqual(native.transmitter(position).crypt(b'payload'),b'payload')
                self.assertEqual(native.receiver(0).crypt(b'input'),b'input')
            wrong=copy.deepcopy(server);wrong['frames'][5]['sha256']='0'*64
            with self.assertRaisesRegex(ValueError,'do not match'):NativeReplayStream(dict(ops=operations),wrong)

    def test_reconnects_reset_each_cipher_without_hiding_earlier_or_later_battles(self):
        first,server,raw1=self.documents();second,_,raw2=self.documents()
        operations=[['open']]+first['ops']+[['close'],['open']]+second['ops']
        client=dict(ops=operations)
        earlier=ReplayStream(client,dict(server,world_sequence=1))
        later=ReplayStream(client,dict(server,world_sequence=2))
        self.assertEqual(len(earlier.boundaries),len(later.boundaries))
        self.assertGreater(later.boundaries[0].start,earlier.boundaries[-1].end)
        raw=raw1+raw2
        boundary=later.boundaries[4];position=boundary.start+2
        covering,suffix=later.catchup(position)
        self.assertEqual(covering,boundary)
        self.assertEqual(later.transmitter(position).crypt(suffix),raw[position:boundary.end])
        received,sent=later.progress(len(operations))
        self.assertEqual(received,len(raw))
        expected=RC4(random_string(0,64))
        expected.crypt(bytes(sent-later.epoch['sent_start']-53))
        self.assertEqual(later.receiver(sent).crypt(b'new input'),expected.crypt(b'new input'))
        # A final unfinished Hello belongs to the next connection; it cannot
        # damage the complete, independently verified battle before it.
        partial=dict(ops=[['open']]+first['ops']+[['close'],['open'],['net',base64.b64encode(raw2[:2]).decode()]])
        recovered=ReplayStream(partial,dict(server,world_sequence=1))
        self.assertEqual(recovered.boundaries,earlier.boundaries)

    def test_cross_platform_integer_angle_only_allows_one_ulp_cosmetic_difference(self):
        from types import SimpleNamespace
        import math
        layouts=pb_layout.default_layouts()
        def payload(angle,damage=35):return layouts.encode('Delta',dict(worldupdate_=dict(worldid_=1,dt_=17,generalupdates_=[dict(offset_=17,create_=dict(objid_=40077,props_=dict(vid_='hulk.sgmbullet',facing_=angle,damage_=damage)))])))
        expected=payload(44)
        wire=ReplayStream.__new__(ReplayStream);wire._packet_frame=10;wire._packet_rows=[(12,expected)]
        projectile=SimpleNamespace(objid=40077,rotate=True,homing=False,vx=1.,vy=1.)
        session=SimpleNamespace(projectiles=SimpleNamespace(projectiles=[projectile]))
        self.assertEqual(wire.reconcile(10,0,12,payload(45),session),expected)
        # Damage/position changes and real aim differences remain fatal to the
        # subsequent packet hash; recording data cannot override game rules.
        changed=payload(45,damage=99)
        self.assertEqual(wire.reconcile(10,0,12,changed,session),changed)
        projectile.vy=math.tan(math.radians(45.1));changed=payload(45)
        self.assertEqual(wire.reconcile(10,0,12,changed,session),changed)
        projectile.vy=1.;changed=payload(46)
        self.assertEqual(wire.reconcile(10,0,12,changed,session),changed)

    def documents(self, repeated=False):
        packets=[]
        with game_settings.event_snapshot(game_settings.normalize({'map':'egy_hulk'})):
            session, bridge, server=encounter('egy_hulk')
            server._send_raw=lambda channel,kind,payload,*a,**kw:packets.append(framed(kind,payload))
            capture=ServerCapture(session)
            capture.run('prepare')
            for tick in range(40):
                bridge._absorb(input_at(tick));capture.run('step',1/60)
        hello=framed(1,b'original anonymous Hello')
        create=framed(12,pb_layout.default_layouts().encode('Delta',{'createplayer_':{}}))
        prefix=framed(8,b'joined')+framed(12,b'')+create
        engine=b''.join(packets)
        encrypted=RC4(random_string(0,64)).crypt(prefix+engine+(create+engine if repeated else b''))
        raw=hello+encrypted
        # Split headers and payloads across different received chunks.
        operations=[['send',53,12345]];offset=0;lengths=[1,2,4,37,256,13]
        while offset<len(raw):
            size=lengths[len(operations)%len(lengths)]
            operations.append(['net',base64.b64encode(raw[offset:offset+size]).decode()])
            offset+=size
            operations.append(['send',7,12345])
        return {'ops':operations},capture.document(),raw

    def test_exact_controller_boundary_and_missing_suffix_across_chunk_splits(self):
        client,server,raw=self.documents()
        layouts=pb_layout.default_layouts();decode=layouts.decode;delta_calls=[]
        def counted(kind,*args,**kwargs):
            if kind=='Delta':delta_calls.append(True)
            return decode(kind,*args,**kwargs)
        with mock.patch.object(layouts,'decode',side_effect=counted):index=ReplayStream(client,server)
        self.assertEqual(len(delta_calls),1,'Only the world bootstrap needs a full Delta decode when indexing')
        self.assertEqual(len(index.boundaries),sum(row['packets']>0 for row in server['frames']))
        for boundary in index.boundaries:
            for position in (boundary.start+1,boundary.end-1,boundary.end):
                with self.subTest(frame=boundary.frame,byte=position):
                    covering,suffix=index.catchup(position)
                    self.assertEqual(covering,boundary)
                    self.assertEqual(index.transmitter(position).crypt(suffix),raw[position:boundary.end])
        received,sent=index.progress(len(client['ops']))
        self.assertEqual(received,len(raw))
        expected=RC4(random_string(0,64));expected.crypt(bytes(sent-53))
        self.assertEqual(index.receiver(sent).crypt(b'fresh input'),expected.crypt(b'fresh input'))
        # These are real controller frames, independently rebuilt and checked.
        replay=ServerReplay(server)
        while replay.advance():pass

    def test_wrong_or_ambiguous_recordings_cannot_be_taken_over(self):
        client,server,_=self.documents()
        wrong=copy.deepcopy(server);wrong['frames'][5]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'do not match'):ReplayStream(client,wrong)
        index=ReplayStream(client,server)
        for value in (-1,0,index.boundaries[-1].end+1):
            with self.assertRaises(ValueError):index.boundary(value)
        with self.assertRaises(ValueError):index.receiver(20)
        client,server,_=self.documents(repeated=True)
        second=ReplayStream(client,dict(server,world_sequence=2))
        first=ReplayStream(client,dict(server,world_sequence=1))
        self.assertGreater(second.boundaries[0].start,first.boundaries[-1].end)
        server.pop('world_sequence')
        with self.assertRaisesRegex(ValueError,'Ambiguous'):ReplayStream(client,server)


if __name__=='__main__':unittest.main()
