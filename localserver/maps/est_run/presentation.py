"""Native EST animation slots/sounds; decisions live in the controllers.

Slots are catalogue *indices*, not the global Animation enum value. Durations
and the sound names below occur in the two supplied EST recordings. No generic
enemy-shot sound is substituted for an unobserved sound.
"""
from collections import deque

SOUNDS={
    'jungle.stinger':'jungle.stingergen','jungle.drops':'jungle.dropsgen',
    'jungle.pollen':'jungle.pollengen','jungle.spitegg':'jungle.egggen',
    'jungle.larva':'jungle.eggcrack','jungle.spitter':'jungle.chrysalispop',
    'jungle.swirlbb':'jungle.swirlbshoot',
}
ACTIONS={
    'wasp':(1,924,None),'fly':(1,528,None),
    'nest':(2,1188,None),'frog_tongue':(1,2574,'jungle.frogtongue'),
    'nest_right':(3,1188,None),'nest_center':(4,1188,None),
    'spider_drop':(1,1056,'jungle.spiderdrop'),
    'pinwheel':(1,2706,None),'turtle_drop':(1,1254,None),
    'turtle_drop_fast':(2,1254,None),
    'spitter':(1,1716,None),'pincer':(0,2500,'jungle.pincerattack'),
}

class Presentation:
    def __init__(self,host):
        self.host=host;self.seen=set();self.order=deque()

    def _once(self,key):
        if key in self.seen:return False
        self.seen.add(key);self.order.append(key)
        if len(self.order)>4096:self.seen.discard(self.order.popleft())
        return True

    def body_created(self,oid):
        row=self.host.actors.get(oid)
        if not row or not self._once(('create',oid)):return

        sound=SOUNDS.get(row.get('vid')) if row.get('vid') in (
            'jungle.stinger','jungle.drops','jungle.pollen') else None
        if sound:self.host.batch.sound(oid,sound,offset=self.host._offset())
        if row.get('vid')=='jungle.pollen':
            self.host.batch.animprops(oid,2,660,offset=self.host._offset())

            row['props']['wmp']=34*256
            self.host.batch.modify(oid,8,intval=34,offset=self.host._offset())

    def action(self,oid,name,token=None):
        if not self._once(('action',oid,name,token)):return False
        anim,duration,sound=ACTIONS[name]
        self.host.batch.animprops(oid,anim,duration,offset=self.host._offset())
        if sound:self.host.batch.sound(oid,sound,offset=self.host._offset())
        return True

    def sound(self,oid,name,token=None):
        if self._once(('sound',oid,name,token)):
            self.host.batch.sound(oid,name,offset=self.host._offset())
