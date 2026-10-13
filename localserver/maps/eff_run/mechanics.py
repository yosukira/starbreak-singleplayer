"""EFF rising native rocks and relative helpers; no duplicated damage loop."""
import copy
import math
from collections import Counter
from ..sc_boss.motion import NativeMotion
from .physics import terrain_contact

ROCKS=('forest.rock8x8','forest.rock12x12','forest.rock48x48','forest.rock48x64','forest.rock64x48','forest.rock64x64')

class ExplorationMechanics:
    def __init__(self,host):
        self.host=host;self.native={};self.rocks={};self.spawners={};self.owned=set();self.counts=Counter();self.advanced_at=None
    def prepare(self):
        for oid,row in self.host.actors.items():
            if row.get('_eff_driven'):continue
            if row['vid']=='forest.rockspawner':self.spawners[oid]=self.host.room_time+self.host.rng.uniform(.1,.6)
            if row['vid'].startswith('forest.rock') and row['vid']!='forest.rockspawner':self.add_rock(oid)
            if row['props'].get('move') and oid not in self.rocks:self._adopt_motion(oid)
        self.host.record('eff_environment_policy',evidence='D captured rock MOVE -5 then -20/Z55/BLOCKTYPE4; SIM cadence/type/jitter/25s safety lifetime')
    def _adopt_motion(self,oid):
        row=self.host.actors[oid];move=copy.deepcopy(row['props']['move']);parent=(move.get('relativeto') or {}).get('objid')
        model=NativeMotion(*(row['pos'] if parent is None else (0,0)));model.move=move;model.at=row.get('born',self.host.room_time)
        self.native[oid]=dict(model=model,move=move,parent=parent);self.owned.add(oid);row['_eff_mechanic']=True
    def add_rock(self,oid):
        row=self.host.actors[oid];self._adopt_motion(oid)
        self.rocks[oid]=dict(born=row.get('born',self.host.room_time),accelerated=False);row['_eff_rock']=True
    def advance_movers(self,dt):
        logical_at=self.host.room_time
        if self.advanced_at==logical_at:return
        self.advanced_at=logical_at
        at=getattr(self.host,'wire_end_ms',round(logical_at*1000))/1000.
        for oid,row in list(self.host.actors.items()):
            if row.get('_eff_driven'):continue
            if row['props'].get('move') and (oid not in self.native or self.native[oid]['move']!=row['props']['move']):self._adopt_motion(oid)
        for oid,state in list(self.native.items()):
            row=self.host.actors.get(oid)
            if not row:self.native.pop(oid,None);self.rocks.pop(oid,None);continue
            if row.get('_eff_driven'):

                self.native.pop(oid,None);self.rocks.pop(oid,None);self.owned.discard(oid)
                row.pop('_eff_mechanic',None);row.pop('_eff_rock',None);continue
            parent=state['parent'];origin=(0,0)
            if parent is not None:
                owner=self.host.actors.get(parent)
                if not owner:
                    self.host._retire(oid,'INTERNAL');self.native.pop(oid,None);continue
                origin=owner['pos']
            rock=self.rocks.get(oid)
            if rock and not rock['accelerated'] and at-rock['born']>=2.:
                position=state['model'].position(at);vel=state['move'].get('vel',{})
                current=vel.get('yvel',0)+vel.get('yaccel',0)*(at-state['model'].at)*1000
                limit=vel.get('minyvel',-12500);current=max(limit,current)
                move=dict(vel=dict(yvel=round(current),yaccel=-20,minyvel=limit))
                state['model'].set(move,at);state['move']=copy.deepcopy(move);row['props']['move']=copy.deepcopy(move)
                row['props'].update(z=55,blocktype=4);rock['accelerated']=True
                offset=getattr(self.host,'_slice_ms',0) if getattr(self.host,'_in_step',False) else self.host._offset()
                self.host.batch.modify(oid,'MOVE',message=('Properties_Move',self.host.normalize('Properties_Move',move)),offset=offset)
                self.host.batch.props(oid,dict(Z=55,BLOCKTYPE=4),offset=offset);self.counts['rock_accelerated']+=1
            local=state['model'].position(at);old=row['pos'];position=[origin[j]+local[j] for j in (0,1)]
            if rock and rock['accelerated']:
                size=(row['props'].get('wmp',0),row['props'].get('hmp',0))
                solids=[r for key,r in self.host.actors.items() if key!=oid and r['props'].get('terraintype')==1]
                if any(terrain_contact(r,old,position,size) is not None for r in solids):
                    self.host._retire(oid,'BLOCKED');self.native.pop(oid,None);self.rocks.pop(oid,None);continue
            row['pos']=position;row['vel']=[(position[j]-old[j])/dt if dt else 0 for j in (0,1)]
    def step(self,dt):
        self.advance_movers(dt);at=self.host.room_time
        for parent,due in list(self.spawners.items()):
            row=self.host.actors.get(parent)
            if row is None:continue
            if at<due:continue
            self.spawners[parent]=at+self.host.rng.uniform(.45,.75)
            if len(self.rocks)>=96:continue
            vid=self.host.rng.choice(ROCKS);p=self.host.pack.props(vid);w=p['wmp'];x,y=row['pos'];sw=row['props']['wmp']
            if sw<w+16*256:continue
            limit=-self.host.rng.randint(10000,15000)
            oid=self.host._create(vid,(self.host.rng.uniform(x+8*256,x+sw-w-8*256),y+6*256),
                changes=dict(move=dict(vel=dict(yvel=0,yaccel=-5,minyvel=limit)),lifetime=25000))
            self.host.created_at_end(oid);self.add_rock(oid);self.counts['rocks_created']+=1
    def expire(self,oid):
        if oid not in self.owned:return False
        self.native.pop(oid,None);self.rocks.pop(oid,None);self.owned.discard(oid);self.host._retire(oid,'LIFETIME');return True
    def feedback(self,event):
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.host.worldid:return False
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        if event.get('kind')=='destroy' and event.get('reason') in (5,6,7,'BLOCKED','LIFETIME','CANCEL'):
            self.native.pop(oid,None);self.rocks.pop(oid,None);self.owned.discard(oid);self.host._retire(oid,event['reason'])
        return True
    def interact(self,oid,player):return False
    def status(self):return dict(rocks=len(self.rocks),spawners=len(self.spawners),native_motions=len(self.native),counts=dict(self.counts),
        evidence='D native rock motion/geometry; SIM type/cadence/cap25s lifetime; no server player damage loop')
