"""Ordinary EFB mites reuse the measured IceBoss creature/weapon capability.

Only exploration acquisition/retention and the new48kmp/s charger fit differ.
No Boss phase, summon timetable, victory rule or user-shortened hatch is run.
"""
import copy
import math
from pathlib import Path
import json
from types import SimpleNamespace
from ..ice_boss.controller import IceBossController
from .abilities import rect,players,center,prop,move,stop_motion
from .physics import polygon,surface,swept_shape,resolve_triangles

MITES={'ice.maxethrower','ice.mrangthrower','ice.msworder','ice.mcharger'}

class MiteAbility:
    target=IceBossController.target
    facing=IceBossController.facing
    pose=IceBossController.pose
    anim=IceBossController.anim
    _npc_tick=IceBossController._npc_tick
    _projectile=IceBossController._projectile

    def __init__(self,controller):
        self.controller=controller;self.host=controller.host
        self.time=0.;self.remainder=0.;self.actors={};self.retired=set();self.players={};self.pending=[];self.shots=0;self.npc_kills=0
        path=Path(__file__).resolve().parents[3]/'maps/ice_boss/entities.json'
        entities=json.loads(path.read_text(encoding='utf8'))
        entities['npc'].update(acquire_px=900,release_px=1200,charge_px_s=48000/256)
        self.pack=SimpleNamespace(entities=entities,props=self.host.pack.props,dynamic_templates=self.host.pack.dynamic_templates,
            rect=lambda p:tuple(p.get(k,0)/256 for k in ('xmp','ymp','wmp','hmp')),
            scene=dict(left=-1e9,right=1e9),solids=[],ledges=[],surfaces=[],triangles=[])

    def geometry(self):
        surfaces=[(oid,rect(row),row['props'].get('terraintype'),row['props'].get('shapetype',0))
            for oid,row in self.host.actors.items() if not row.get('dead') and row['props'].get('terraintype') in (1,2)]
        self.pack.solids=[tuple(v/256 for v in r) for oid,r,kind,shape in surfaces if kind==1 and not shape]
        self.pack.ledges=[tuple(v/256 for v in r) for oid,r,kind,shape in surfaces if kind==2 and not shape]
        self.pack.triangles=[(oid,tuple(v/256 for v in r),shape,kind) for oid,r,kind,shape in surfaces if shape]
        for oid,r,shape,kind in self.pack.triangles:polygon(r,shape)
        self.pack.surfaces=self.pack.solids+self.pack.ledges
        bounds=[r for oid,r,kind,shape in surfaces if kind==1]
        if bounds:
            self.pack.scene.update(left=min(r[0]/256 for r in bounds),right=max((r[0]+r[2])/256 for r in bounds))

    def ground(self,a,dt,gravity=1400.,platforms=True):
        if not self.pack.triangles:return IceBossController.ground(self,a,dt,gravity,platforms)
        x,y=a['pos'];w,h=a['size']
        previous=dict(x=x,y=y,w=w,h=h,vy=a['vel'][1],grounded=a.get('grounded',False))
        IceBossController.ground(self,a,dt,gravity,platforms)
        body=dict(x=a['pos'][0],y=a['pos'][1],w=w,h=h,vy=a['vel'][1],grounded=a['grounded'])
        resolve_triangles(body,previous,self.pack.triangles,platforms=platforms,tolerance=1/256)
        a['pos']=[body['x'],body['y']];a['vel'][1]=body['vy'];a['grounded']=body['grounded']

    def _flying_tick(self,a,dt):
        if not self.pack.triangles:return IceBossController._flying_tick(self,a,dt)
        start=tuple(a['pos']);IceBossController._flying_tick(self,a,dt)
        if a['id'] not in self.actors or a['kind'] not in ('fire','disc','rang','debris'):return
        if any(kind==1 and swept_shape(r,shape,start,a['pos'],a['size']) is not None
                for oid,r,shape,kind in self.pack.triangles):self.remove(a,'BLOCKED')

    def add(self,oid):
        row=self.host.actors[oid];x,y,w,h=rect(row)
        self.actors[oid]=dict(id=oid,kind='npc',vid=row['vid'],pos=[x/256,y/256],size=[w/256,h/256],
            vel=[0.,0.],hp=row['hp'],facing=row['props'].get('facing',0),pose=row['props'].get('pose',4),
            grounded=False,action='idle',until=0.,next_attack=self.time+.8,aggro=False)
        stop_motion(self.host,oid)

    def emit(self,kind,**values):
        oid=values.get('id')
        if kind=='props':
            for name,value in values['values'].items():prop(self.host,oid,name,value)
        elif kind=='anim':
            row=self.host.actors.get(oid)
            if row:self.host.presentation.attack(oid,row['vid'],values['anim'],round(values['duration']*1000),token=self.time)
            self.host.record('efb_mite_attack',id=oid,anim=values['anim'],evidence='existing IceBoss native mite ability; ordinary target gates SIM')
        elif kind=='sound':
            row=self.host.actors.get(oid,{})
            anim=row.get('props',{}).get('animprops',{}).get('animid')
            token=(anim,self.time) if values['name']=='ice.msswing' else self.time
            self.host.presentation.sound(oid,values['name'],token)
        elif kind not in ('move','create','remove'):self.host.record('efb_mite_'+kind,evidence='existing measured Ice capability',**values)

    def _new(self,kind,vid,pos,**fields):
        props=self.host.pack.props(vid);props['move']=dict(vel=dict(xvel=0,yvel=0))
        oid=self.host._create(vid,[v*256 for v in pos],fields.get('owner',0),props)
        row=self.host.actors[oid];self.controller.own(oid);row['_efb_mite_projectile']=True
        a=dict(id=oid,kind=kind,vid=vid,pos=list(pos),size=list(self.pack.rect(props)[2:]),vel=[0.,0.],
            hp=props.get('hitpoints'),facing=0,pose=1,grounded=False,born=self.time,expiry=math.inf)
        a.update(fields);self.actors[oid]=a;return a

    def remove(self,a,reason='INTERNAL'):
        if a['id'] in self.retired:return
        self.retired.add(a['id']);self.actors.pop(a['id'],None)
        self.controller.retire(a['id'],reason)

    def step(self,dt,active):
        self.geometry();self.players={i:[v/256 for v in center(p)] for i,p in enumerate(players(self.host))}
        self.remainder+=dt
        while self.remainder+1e-10>=1/120:
            self.remainder-=1/120;self.time+=1/120
            ready,self.pending=self.pending,[]
            for when,kind,oid in ready:
                if when>self.time+1e-9:self.pending.append((when,kind,oid));continue
                owner=self.actors.get(oid)
                if owner and self.target(owner,self.pack.entities['npc']['release_px']):self._projectile(owner,kind)
            for oid,a in list(self.actors.items()):
                if oid not in self.host.actors:self.actors.pop(oid,None);continue
                if a['kind']=='npc':
                    if oid in active:self._npc_tick(a,1/120)
                    else:a['vel'][0]=0.;self.ground(a,1/120)
                else:
                    if self.host.wire_lifetime_due(oid) is True:self.remove(a,'LIFETIME')
                    else:self._flying_tick(a,1/120)
        for oid,a in list(self.actors.items()):
            if oid in self.host.actors:
                move(self.host,oid,[v*256 for v in a['pos']],dt)
                if a['kind'] in ('rang','axe'):
                    prop(self.host,oid,'facing',round(math.degrees(math.atan2(a['vel'][1],a['vel'][0])))%360 if not a.get('stuck') else a['facing'])

    def carry(self,deltas):
        for a in self.actors.values():
            if a['kind']!='npc' or not a.get('grounded'):continue
            x,y=a['pos'];w,h=a['size']
            for oid,(old,new,size) in deltas.items():
                ox,oy=old;sw,sh=size
                if abs((y+h)*256-oy)<=2 and x*256<ox+sw and (x+w)*256>ox:
                    a['pos'][0]+=(new[0]-old[0])/256;a['pos'][1]+=(new[1]-old[1])/256;break

    def forget(self,oid):
        self.actors.pop(oid,None);self.retired.add(oid);self.pending=[p for p in self.pending if p[2]!=oid]
