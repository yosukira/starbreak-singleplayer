"""Native EGY presentation shared by exploration AI and mechanisms.

Tables are direct EGY sound events or existing Hulk Boss send mappings.
Unknown actions keep their supplied animation and receive no invented sound.
Native soundprefix/reaction and Destroy animations remain native behavior.
All methods emit Delta and synchronize actor props; per-phase tokens prevent
duplicate sound/animation dispatch when multiple components report one event.
"""
import math

ATTACK_SOUNDS = {
    ('hulk.eloader',4):'hulk.loaderdrill',
    ('hulk.scavengerloader',5):'hulk.loaderdrill',
    ('hulk.scavengershredder',2):'hulk.punch',
    ('hulk.scavengershredder',4):'hulk.tail',
    ('hulk.scavengershredderenergy',2):'hulk.epunch',
    ('hulk.scavengershredderenergy',4):'hulk.etail',
    ('hulk.scavengerengineer',3):'hulk.tail',
    ('hulk.scavengercommando',0):'hulk.throw',

    ('hulk.scavengerloader',7):'hulk.loaderthrow',
    ('hulk.scavengerloader',8):'hulk.loaderthrow',
}
PROJECTILE_SOUNDS = {
    'hulk.sgmbullet':'hulk.bulletgen', 'hulk.sshot':'hulk.shotgungen',
    'hulk.smissile':'hulk.missilegen', 'hulk.cannonshot':'hulk.mortargen',

    'hulk.dronebomb':'hulk.mortargen',
    'hulk.cannonshotenergy':'hulk.mortaregen',
}
EFFECT_SOUNDS = {
    'hulk.sshotx':'hulk.shotx', 'hulk.smissilex':'hulk.missilex',
    'hulk.grenadex':'hulk.grenadex', 'hulk.barrelx':'hulk.barrelx',
    'hulk.cannonshotx':'hulk.mortarx', 'hulk.cannonshotenergyx':'hulk.mortarex',
    'hulk.grenadepulse':'hulk.grenadebounce', 'hulk.minispawn':'hulk.miniseekergen',
}
BODY_SOUND_PREFIXES = {
    'hulk.scavengergunman':'hulk.lizard', 'hulk.scavengershotgunner':'hulk.lizard',
    'hulk.scavengermissiler':'hulk.lizard', 'hulk.scavengercommando':'hulk.lizard',
    'hulk.rpgscavenger':'hulk.lizard', 'hulk.scavengerengineer':'hulk.lizard',
    'hulk.scavengershredder':'hulk.lizard', 'hulk.scavengerwaller':'hulk.lizard',
    'hulk.scavengershredderenergy':'hulk.elizard', 'hulk.scavengerwallerenergy':'hulk.elizard',
    'hulk.cranehooksmall':'hulk.cranehook', 'hulk.cranehooklarge':'hulk.cranehook',
}
DOOR_USED = {vid:(1,396) for vid in ('hulk.door1','hulk.door2','hulk.door3','hulk.prevdoor')}
DANGER_WARNING = {vid:(0 if vid=='hulk.danger64h' else 1,990) for vid in
                  ('hulk.danger','hulk.danger64h','hulk.danger64w','hulk.danger96h','hulk.danger96w')}

class Presentation:
    def __init__(self,host):
        self.host=host
        self._states={}

    def _row(self,oid):
        return self.host.actors.get(int(oid))

    def _once(self,oid,phase,token=None):
        row=self._row(oid)
        state=(row.setdefault('_presentation_state',{}) if row is not None
               else self._states.setdefault(int(oid),{}))
        seen=state.setdefault('seen',[])
        key=repr((phase,token))
        if key in seen:return False
        seen.append(key)

        if len(seen)>64:del seen[:-64]
        return True

    def _offset(self):
        return self.host._offset()

    def _modify(self,oid,key,value):
        row=self._row(oid)
        if row is not None:
            row['props'][key.lower()]=value
        self.host.batch.modify(oid,key,offset=self._offset(),
            **({'boolval':value} if isinstance(value,bool) else
               {'stringval':value} if isinstance(value,str) else {'intval':int(value)}))

    def _animation(self,oid,anim,time_ms):
        self.host.batch.animprops(oid,int(anim),int(time_ms),offset=self._offset())
        row=self._row(oid)
        if row is not None:row['props']['animprops']=dict(animid=int(anim),time=int(time_ms))

    def _sound(self,oid,name):
        self.host.batch.sound(oid,name,offset=self._offset())

    def body_created(self,oid,vid=None):
        row=self._row(oid)
        if row is None:return False
        vid=vid or row['vid']
        if not self._once(oid,'body-created',vid):return False
        prefix=BODY_SOUND_PREFIXES.get(vid)
        if prefix and not row['props'].get('soundprefix'):
            self._modify(oid,'SOUNDPREFIX',prefix)
            return True
        return False

    def attack(self,oid,vid,anim,time_ms,token=None,emit_animation=True):
        token=self.host.room_time if token is None else token
        if not self._once(oid,'attack',(token,int(anim),int(time_ms))):return False
        if emit_animation:self._animation(oid,anim,time_ms)
        sound=ATTACK_SOUNDS.get((vid,int(anim)))
        if sound:self._sound(oid,sound)
        return True

    def projectile_created(self,oid,vid,projectile=None):
        if not self._once(oid,'projectile-created',vid):return False
        row=self._row(oid)
        vx,vy=(row.get('vel',[0.,0.]) if row is not None else (0.,0.))
        if projectile is not None:vx,vy=projectile.vx,projectile.vy
        if vid=='hulk.napalm':
            self._modify(oid,'POSE',5)
            self._modify(oid,'FACING',180 if vx<0 else 0)
        elif vid=='hulk.smissile':
            pose=projectile.flight_pose() if projectile is not None else (2 if abs(vx)>=abs(vy) else 3)
            self._modify(oid,'POSE',pose)
            self._modify(oid,'FACING',180 if vx<0 else 0)
            self._modify(oid,'ROTATE',False)
        elif vid in ('hulk.rpg','hulk.ngrenade'):
            self._modify(oid,'FACING',180 if vx<0 else 0)
        if projectile is not None and getattr(projectile,'rotate',False) and vid!='hulk.smissile':
            self._modify(oid,'ROTATE',True)
            self._modify(oid,'FACING',round(math.degrees(math.atan2(vy,vx))))
        sound=PROJECTILE_SOUNDS.get(vid)
        if sound:self._sound(oid,sound)
        return True

    def effect_created(self,oid,vid):
        if not self._once(oid,'effect-created',vid):return False
        sound=EFFECT_SOUNDS.get(vid)
        if sound:self._sound(oid,sound)
        return bool(sound)

    def canister_opened(self,child_oid):
        if not self._once(child_oid,'canister-opened','birth'):return False

        self._sound(child_oid,'hulk.canisterdeath')
        return True

    def grenade_pulse(self,pulse_oid):

        return self.effect_created(pulse_oid,'hulk.grenadepulse')

    def napalm_landed(self,oid,position=None):
        if not self._once(oid,'napalm-landed','settled'):return False
        row=self._row(oid)
        if position is None:
            if row is None:raise ValueError('Napalm landing needs actor or explicit position')
            position=row['pos']
        x,y=map(lambda v:int(round(v)),position)
        target=dict(xmp_=x,ymp_=y,dt_=1,pos_=0,start_=dict(xmp_=x,ymp_=y))
        self.host.batch.modify(oid,'MOVE',message=('Properties_Move',dict(to_=target)),offset=self._offset())
        if row is not None:
            row['pos']=[x,y];row['vel']=[0.,0.]
            row['props']['move']=dict(to=dict(xmp=x,ymp=y,dt=1,pos=0,start=dict(xmp=x,ymp=y)))
        self._modify(oid,'POSE',4)
        return True

    def danger_warning(self,oid,vid=None,token=None):
        row=self._row(oid)
        vid=vid or (row['vid'] if row is not None else '')
        action=DANGER_WARNING.get(vid)
        if action is None:return False
        token=self.host.room_time if token is None else token
        if not self._once(oid,'danger-warning',token):return False
        self._animation(oid,*action)
        return True

    def mechanism_interacted(self,oid,vid=None,token=None,emit_reaction=False):
        row=self._row(oid)
        vid=vid or (row['vid'] if row is not None else '')
        token=self.host.room_time if token is None else token
        if not self._once(oid,'mechanism-interacted',token):return False
        emitted=False
        action=DOOR_USED.get(vid)
        if action:
            self._animation(oid,*action);emitted=True
        if emit_reaction and row is not None:

            sound=(row['props'].get('interactreaction') or {}).get('sound')
            if sound:self._sound(oid,sound);emitted=True
        return emitted

NativePresentation=Presentation
