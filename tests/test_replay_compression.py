import base64
import copy
import gzip
import json
import struct
import unittest
import uuid
from unittest import mock

from localserver.protocol import RC4,random_string
from tools.client_replay import SCHEMA
from tools.replay_bundle import MAGIC,MAGIC_COMPACT,transform_wire,pack_battle,read_battle
from tools.replay_sharing import clean_replay
from test_battle_records import sample


def wire(plain,hello=b'offline Hello'):
    return bytes([1])+struct.pack('>i',len(hello))+hello+RC4(random_string(0,64)).crypt(plain)


def document(ops):
    identifier=str(uuid.uuid4());record=sample();record.update(platform='web',replay=dict(id=identifier))
    return dict(record=record,client=dict(schema=SCHEMA,id=identifier,client='a'*64,adapter='b'*64,
        preferences=None,viewport=[1366,768],ops=ops))


class ReplayCompressionTests(unittest.TestCase):
    def test_wire_transform_is_byte_exact_for_split_hellos_chunks_and_reconnections(self):
        plain=b'\x0c\0\0\0\x08protobuf'*100
        first,second=wire(plain),wire(plain[::-1],hello=b'other Hello')
        for cut in range(1,23):
            ops=[['open']]+[['net',base64.b64encode(part).decode()] for part in (first[:cut],first[cut:cut+1],first[cut+1:])]
            ops += [['loop',0],['tick',1234.567890123],['loop',1],['close'],['open']]
            ops += [['net',base64.b64encode(part).decode()] for part in (second[:2],second[2:])]
            client=document(ops)['client'];original=copy.deepcopy(client)
            transformed=transform_wire(client)
            self.assertEqual(transform_wire(transformed),original)
            self.assertEqual(client,original,'Compression cannot mutate the live recording')
            self.assertNotEqual(transformed['ops'],original['ops'])

    def test_compact_and_legacy_files_import_the_same_exact_recording(self):
        data=wire(b'common world update and repeated geometry fields\0'*6000)
        value=document([['open']]+[['net',base64.b64encode(data[i:i+64000]).decode()] for i in range(0,len(data),64000)]+[['loop',0],['frame',17],['state',[1,0]],['loop',1]])
        compact,expanded=pack_battle(value)
        legacy=MAGIC+gzip.compress(json.dumps(value).encode(),mtime=0)
        self.assertTrue(compact.startswith(MAGIC_COMPACT))
        self.assertLess(len(compact),len(legacy)/3)
        a,b=read_battle(legacy),read_battle(compact)
        self.assertEqual(a[:3],b[:3]);self.assertGreater(expanded,len(compact))
        # Sharing validation must keep the compact representation, while still
        # removing extra fields and preserving every original client byte.
        shared=clean_replay(compact,'Player_34')
        self.assertTrue(shared.startswith(MAGIC_COMPACT))
        self.assertEqual(read_battle(shared)[1],b[1])
        self.assertEqual(read_battle(shared)[0]['shared_by'],'Player_34')

    def test_small_or_unrecognized_streams_keep_the_legacy_format(self):
        value=document([['loop',0],['loop',1]])
        self.assertTrue(pack_battle(value)[0].startswith(MAGIC))
        value['client']['ops']=[['open'],['net',base64.b64encode(b'not a Hello').decode()]]
        self.assertTrue(pack_battle(value)[0].startswith(MAGIC))

    def test_unknown_codec_bad_hello_and_expansion_limits_are_rejected(self):
        value=document([['open'],['net',base64.b64encode(wire(b'updates'*1000)).decode()]])
        value['encoding']='unknown'
        blob=MAGIC_COMPACT+gzip.compress(json.dumps(value).encode())
        with self.assertRaisesRegex(ValueError,'compression'):read_battle(blob)
        value['encoding']='wire-clear-v1'
        value['client']['ops'][1][1]=base64.b64encode(b'\x01\xff\xff\xff\xffx').decode()
        with self.assertRaisesRegex(ValueError,'Hello'):read_battle(MAGIC_COMPACT+gzip.compress(json.dumps(value).encode()))
        with mock.patch('tools.replay_bundle.MAX_EXPANDED',10):
            with self.assertRaisesRegex(ValueError,'too large'):read_battle(blob)


if __name__=='__main__':unittest.main()
