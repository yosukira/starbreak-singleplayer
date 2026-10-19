import base64
import copy
import unittest

from localserver import game_settings, pb_layout
from localserver.replay import ServerCapture, ServerReplay
from localserver.protocol import RC4, random_string
from tools.replay_stream import ReplayStream, framed
from test_replay import encounter, input_at


class ReplayStreamTests(unittest.TestCase):
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
        index=ReplayStream(client,server)
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
