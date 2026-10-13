"""EUB native presentation; shared NPC actions retain Dumb's own send path."""
class Presentation:
    def __init__(self,host):self.host=host
    def body_created(self,oid):
        row=self.host.actors.get(oid)
        if row is not None:row.setdefault('_eub_presentation',{})
    def modify(self,oid,key,value):
        row=self.host.actors.get(oid)
        if row is None:return
        row['props'][key]=value
        if key=='vid':row['vid']=value
        if key=='hitpoints':row['hp']=value
        self.host.batch.modify(oid,key.upper(),**({'boolval':value} if isinstance(value,bool) else {'stringval':value} if isinstance(value,str) else {'intval':int(value)}),offset=self.host._offset())
    def pose(self,oid,value):self.modify(oid,'pose',value)
    def _action_offset(self,at_end):
        return self.host._slice_ms if at_end and self.host._in_step and self.host.room_time>self.host._frame_start else self.host._offset()
    def anim(self,oid,slot,duration,at_end=False):
        if oid in self.host.actors:self.host.batch.modify(oid,'ANIMPROPS',message=('AnimationProperties',dict(animid_=int(slot),time_=int(duration))),offset=self._action_offset(at_end))
    def sound(self,oid,name,at_end=False,offset=None):
        if oid in self.host.actors:self.host.batch.sound(oid,name,offset=self._action_offset(at_end) if offset is None else int(offset))

    def shield_shot(self,oid):

        self.anim(oid,3,77,at_end=True)

    def sweeper_prepare(self,oid):

        self.anim(oid,1,1386,at_end=True)
        return 1.122

    def wall_turret_prepare(self,oid,slot):

        if slot not in (2,3,4,5):raise ValueError('invalid EUB wall-cannon shooting slot')
        self.anim(oid,slot,2640,at_end=True)
        return 2.310
