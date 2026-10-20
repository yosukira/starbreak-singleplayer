import hashlib,json,tempfile,unittest,uuid
from unittest import mock
from tools.native_replay import NativeJournals
class NativePageTests(unittest.TestCase):
 def store(self):
  folder=self.enterContext(tempfile.TemporaryDirectory());store=NativeJournals(folder);identifier=str(uuid.uuid4())
  store.begin(identifier,'a'*64,'b'*64,dict(content=None),[1366,768])
  rows=[['loop',0],['frame'],['clock',1],['step',16],['return','frame'],['state',[0,0]],['loop',1]]
  store.append(identifier,dict(sequence=0,ops=rows[:3],end=False));store.append(identifier,dict(sequence=1,ops=rows[3:],end=True))
  return store,identifier,rows
 def test_header_does_not_decode_operations_and_pages_preserve_cross_page_stack(self):
  store,identifier,rows=self.store()
  with mock.patch.object(store,'unpack_chunk',side_effect=AssertionError('Header may not decode the trace')):
   header,count=store.page_header(identifier)
  self.assertEqual(count,2);self.assertEqual(header['clock_scope'],'game-v1')
  pages=list(store.read_pages(identifier));self.assertEqual(sum([page['ops'] for page in pages],[]),rows)
  self.assertEqual([p['end'] for p in pages],[False,True])
 def test_changed_checksum_or_invalid_cross_page_call_is_rejected(self):
  store,identifier,rows=self.store()
  with store.connect() as db:db.execute('UPDATE chunks SET digest=? WHERE id=? AND sequence=1',('0'*64,identifier))
  with self.assertRaisesRegex(ValueError,'checksum'):list(store.read_pages(identifier))
 def test_portable_runner_cursor_must_still_end_a_complete_frame(self):
  store,identifier,rows=self.store()
  with store.connect() as db:
   header=json.loads(db.execute('SELECT header FROM attempts WHERE id=?',(identifier,)).fetchone()[0]);header['runners']=[dict(cursor=4,engine='browser',client='c'*64,adapter='d'*64)]
   db.execute('UPDATE attempts SET header=? WHERE id=?',(json.dumps(header),identifier))
  with self.assertRaisesRegex(ValueError,'engine boundary'):list(store.read_pages(identifier))
if __name__=='__main__':unittest.main()
