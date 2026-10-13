'EFF native catalogue slots and runtime sounds, with bounded phase dedup.'
from collections import deque

SOUNDS={'forest.gtproj':'forest.gthrow','forest.gftseed':'forest.gthrow',
        'forest.bcbomb':'forest.bomblaunch','forest.bdbomb':'forest.bomblaunch',
        'forest.bcbombx':'forest.bombx','forest.bdbombx':'forest.firex','forest.gftfire':'forest.firex'}
ACTIONS={'throw':(1,1782),'firethrow':(6,1782),'hop':(2,528),'drop':(1,1386),'hive':(1,2000),'miner':(5,600),'seed_open':(5,660)}

class Presentation:
    def __init__(self,host):self.host=host;self.seen=set();self.order=deque()
    def once(self,key):
        if key in self.seen:return False
        self.seen.add(key);self.order.append(key)
        if len(self.order)>4096:self.seen.discard(self.order.popleft())
        return True
    def body_created(self,oid):
        row=self.host.actors.get(oid)
        if row and self.once(('create',oid)) and row['vid'] in SOUNDS:
            self.host.batch.sound(oid,SOUNDS[row['vid']],offset=self.host._offset())
    def action(self,oid,name,token):
        if not self.once(('action',oid,name,token)):return
        slot,duration=ACTIONS[name];self.host.batch.animprops(oid,slot,duration,offset=self.host._offset())
    def sound(self,oid,name,token):
        if self.once(('sound',oid,name,token)):self.host.batch.sound(oid,name,offset=self.host._offset())
