"""EFB native observed cues; no generic fabricated attack/birth sound."""

class Presentation:
    def __init__(self,host):self.host=host

    def once(self,oid,key,token):
        row=self.host.actors.get(oid)
        if row is None:return False
        state=row.setdefault('_efb_presentation',{})
        if key in state and state[key]==token:return False
        state[key]=token;return True

    def sound(self,oid,name,token=None):
        if self.once(oid,'sound:'+name,token):self.host.batch.sound(oid,name,offset=self.host._offset())

    def body_created(self,oid):pass

    def attack(self,oid,vid,anim,time_ms,token=None):
        if not self.once(oid,'attack',(anim,token)):return
        self.host.batch.animprops(oid,anim,time_ms,offset=self.host._offset())
        row=self.host.actors.get(oid)
        if row:row['props']['animprops']=dict(animid=anim,time=time_ms)
        if vid=='ice.msworder':self.sound(oid,'ice.msswing',(anim,token))
        elif vid=='ice.rdino' and anim==4:self.sound(oid,'ice.dinojump',(anim,token))

    def projectile_created(self,oid,vid):
        name={'ice.shard':'ice.genshard','ice.spine':'ice.genspine','ice.throwball':'ice.genball',
            'ice.maxe':'ice.genaxe','ice.mrang':'ice.genrang','ice.fhead':'ice.genflame'}.get(vid)
        if name:self.sound(oid,name,'birth')

    def effect_created(self,oid,vid):
        name={'ice.sambush':'ice.smalla','ice.lambush':'ice.largea'}.get(vid)
        if name:self.sound(oid,name,'release')
