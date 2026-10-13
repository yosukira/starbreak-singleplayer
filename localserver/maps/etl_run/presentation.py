"""ETL observed animation/sound channels; no invented generic combat sounds."""
from .abilities import NATIVE

def projectile_top_left(emitter, projectile):
    'Observed emitter geometry -> projectile top-left, without a second half.'
    p=emitter['props'];vid=emitter['vid'];x,y=emitter['pos']
    w,h=p.get('wmp',0),p.get('hmp',0)
    child=projectile.get('vid');cw,ch=projectile.get('wmp',0),projectile.get('hmp',0)
    if child in ('lab.thumper','lab.thumperp') and (cw,ch)==(8192,8192):
        if ((vid in ('lab.turretv','lab.turretvp') and (w,h)==(8192,24576)) or
                (vid in ('lab.turreth','lab.turrethp','lab.turrethceiling','lab.turrethceilingp') and (w,h)==(24576,8192))):
            return x+(w-cw)/2,y+(h-ch)/2
    if child=='lab.electricity' and (cw,ch)==(16384,16384) and (w,h)==(32768,32768):
        if vid=='lab.epturretup':offset=(8192,-6144)
        elif vid=='lab.epturretdown':offset=(8192,24832)
        elif vid=='lab.epturret' and p.get('facing',0) in (0,180):
            offset=(23552,8704) if p.get('facing',0)==0 else (-7168,8704)
        else:return None
        return x+offset[0],y+offset[1]
    return None

class Presentation:
    def __init__(self,host):self.host=host

    def _once(self,oid,key,token):
        row=self.host.actors.get(oid)
        if row is None:return False
        seen=row.setdefault('_etl_presentation',{})
        if key in seen and seen[key]==token:return False
        seen[key]=token;return True

    def sound(self,oid,name,token=None,*,offset=None):
        if self._once(oid,'sound:'+name,token):
            self.host.batch.sound(oid,name,offset=self.host._offset() if offset is None else offset)

    def body_created(self,oid):
        row=self.host.actors.get(oid)
        if row is None:return

        if row['vid']=='lab.flowerdropper':self.sound(oid,'lab.flowerregen','create')
        elif row['vid'] in ('lab.clingvineup','lab.clingvinedown'):self.sound(oid,'lab.cvineregen','create')
        elif row['vid']=='lab.plantsnake':self.sound(oid,'lab.snakeregen','create')

    def attack(self,oid,vid,anim,time_ms,token=None):
        if not self._once(oid,'attack',(token,anim)):return
        self.host.batch.animprops(oid,anim,time_ms,offset=self.host._offset())
        row=self.host.actors.get(oid)
        if row:row['props']['animprops']=dict(animid=anim,time=time_ms)
        sound=('lab.pulsecharge' if vid.startswith('lab.epturret') else
               'lab.shamblerattack' if vid=='lab.shambler' and anim==4 else
               'lab.rapidattacktell' if vid=='lab.rovingturret' and anim in (3,4) else
               'lab.snakeattack' if vid=='lab.plantsnake' and anim==1 else None)

        if sound:self.sound(oid,sound,(token,anim))

    def projectile_created(self,oid,vid,*_args):
        name={'lab.sbullet':'lab.bullet','lab.bullet':'lab.bullet','lab.seed':'lab.seed',

              'lab.electricity':'lab.epulse','lab.thumper':'lab.thumper',
              'lab.explodingflower':'lab.flower'}.get(vid)
        if name:self.sound(oid,name,'create')

    def effect_created(self,oid,vid):
        if vid=='lab.explodingflowerx':self.sound(oid,'lab.flowerx','create')
