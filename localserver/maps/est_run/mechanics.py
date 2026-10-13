"""Open EST environment, original native motion/attachments and paired gates.

No backing, thickened walls or added damage loops. Pincer damage remains the
native hitbox channel. Pincer cooldown/trigger, pusher patrol span and turtle
route are SIM; original terrain, invulnerability, attachment and ellipse fields
are D and remain on the wire. Each object has exactly one motion owner.
"""
import copy
import math
from collections import Counter,deque
from ..jungle_common.scene import motion_position
from .presentation import Presentation

class ExplorationMechanics:
    def __init__(self,host):
        self.host=host;self.owned=set();self.native={};self.movers={};self.pincers=[]
        self.blobs={};self.retired=set();self.counts=Counter();self._seen=set();self._order=deque()
        self._event_refs=deque(maxlen=4096)
        self.presentation=getattr(host,'presentation',None) or Presentation(host)
        if getattr(host,'presentation',None) is None:host.presentation=self.presentation

    def prepare(self):
        recipe=getattr(self.host,'recipe',None) or getattr(getattr(self.host,'instance',None),'recipe',{})
        for door,pair in recipe.get('gate_pairs',{}).items():
            blob=int(pair['blob']);self.blobs[blob]=int(door);self.owned.add(blob)
            if blob in self.host.actors:self.host.actors[blob]['_mechanic']=True
        for oid,row in self.host.actors.items():
            if row.get('dead'):continue
            vid=row.get('vid','');props=row['props'];move=props.get('move') or {}
            if move and not row.get('_est_driven'):
                self.native[oid]=dict(start=tuple(row['pos']),move=copy.deepcopy(move),epoch=self.host.room_time)
                row['_mechanic_motion']=True
            if vid in ('jungle.stonepusher','jungle.turtlebody'):
                self.owned.add(oid);row['_mechanic']=True
                x,y=row['pos'];w=props.get('wmp',0)
                surfaces=[s for s in self.host.surfaces() if s[4]==2 and s[2]>=w]
                platform=min(surfaces,key=lambda s:abs(s[1]-(y+props.get('hmp',0)))+
                    abs(s[0]+s[2]/2-(x+w/2))) if surfaces else None
                if vid=='jungle.stonepusher' and platform:
                    lo,hi=platform[0],platform[0]+platform[2]-w;speed=25600.
                else:lo,hi=x-192*256,x+192*256;speed=24000.
                self.movers[oid]=dict(home=(x,y),lo=min(lo,x),hi=max(hi,x),
                    direction=-1 if props.get('facing',0)==180 else 1,speed=speed,
                    turtle=vid=='jungle.turtlebody',last=self.host.room_time)
        self._prepare_pincers()
        self.host.record('est_environment_policy',evidence='SIM: pincer8s/pass1s, mover local spans; D native objects retained')

    def _prepare_pincers(self):
        available={oid:row for oid,row in self.host.actors.items() if row.get('vid')=='jungle.pincer' and not row.get('dead')}
        used=set()
        for oid,row in sorted(available.items(),key=lambda item:tuple(item[1]['pos'])):
            if oid in used:continue
            x,y=row['pos'];w=row['props'].get('wmp',34560)
            choices=[(abs(other['pos'][0]-x),key) for key,other in available.items()
                if key not in used and key!=oid and other['pos'][0]>x
                and abs(other['pos'][1]-y)<512 and other['pos'][0]-x<=150000]
            ids=(oid,min(choices)[1]) if choices else (oid,)
            used.update(ids);self.owned.update(ids)
            for key in ids:available[key]['_mechanic']=True
            right=max(available[key]['pos'][0]+available[key]['props'].get('wmp',w) for key in ids)
            self.pincers.append(dict(ids=ids,left=x,right=right,y=y,
                next=self.host.room_time+8.,pending=None,restore=None,last=self.host.room_time))

    def _players(self):
        for p in (list(getattr(self.host,'players',{}).values()) or [getattr(self.host,'player',None)]):
            if p is None:continue
            if isinstance(p,dict):
                if not p.get('alive',True):continue
                center=p.get('center')
                if callable(center):center=center()
                if center is None:center=(p.get('xmp',0)+p.get('wmp',10240)/2,p.get('ymp',0)+p.get('hmp',20480)/2)
                yield center[0],center[1]+p.get('hmp',20480)/2
            elif getattr(p,'alive',True):yield p.center()[0],p.ymp+p.hmp

    def _native_position(self,oid,visiting):
        row=self.host.actors.get(oid);state=self.native.get(oid)
        if not row or row.get('dead') or not state or oid in visiting:return
        visiting.add(oid);move=state['move'];at=self.host.room_time;elapsed=max(0.,at-state['epoch'])
        parent=(move.get('relativeto') or {}).get('objid')
        origin=(0.,0.)
        if parent is not None:
            self._native_position(parent,visiting)
            parent_row=self.host.actors.get(parent)
            if not parent_row or parent_row.get('dead'):

                if not row.get('vid'):
                    self.host._retire(oid,reason='INTERNAL');self.retired.add(oid)
                visiting.remove(oid);return
            origin=tuple(parent_row['pos'])
        ellipse=move.get('elliptical')
        if ellipse:
            period=max(1,ellipse.get('period',6000));phase=ellipse.get('pos',0)+elapsed*1000
            angle=phase/period*2*math.pi
            ra=ellipse.get('radiusamp',0)+ellipse.get('ravel',0)*1000*elapsed
            rb=ellipse.get('radiusbmp',abs(ra))+ellipse.get('rbvel',0)*1000*elapsed
            position=(origin[0]+ellipse.get('xmp',0)+ra*math.cos(angle),
                      origin[1]+ellipse.get('ymp',0)+rb*math.sin(angle))
        elif move.get('follow') and parent is not None:
            dx=row['pos'][0]-origin[0];dy=row['pos'][1]-origin[1];distance=math.hypot(dx,dy)
            limit=move['follow'].get('maxdist',48)*256
            position=(origin[0]+dx/distance*limit,origin[1]+dy/distance*limit) if distance>limit else tuple(row['pos'])
        else:
            start=(0.,0.) if parent is not None else state['start']
            point=motion_position(start,move,elapsed)
            position=(origin[0]+point[0],origin[1]+point[1])
        previous=tuple(row['pos']);row['pos']=list(position)
        dt=at-state.get('last',state['epoch']);row['vel']=[(position[j]-previous[j])/dt if dt>0 else 0. for j in (0,1)]
        state['last']=at;visiting.remove(oid)

    def step(self,dt):
        at=self.host.room_time
        for oid,state in list(self.movers.items()):
            row=self.host.actors.get(oid)
            if not row or row.get('dead'):continue
            old=tuple(row['pos']);x=old[0]+state['direction']*state['speed']*dt
            if x<state['lo']:x=2*state['lo']-x;state['direction']=1
            if x>state['hi']:x=2*state['hi']-x;state['direction']=-1
            y=state['home'][1]
            if state['turtle']:y+=math.sin(at*math.pi/4)*8192
            row['pos']=[x,y];row['vel']=[(x-old[0])/dt if dt else 0.,(y-old[1])/dt if dt else 0.]
            self.host.batch.move_to(oid,round(x),round(y),dt=max(1,round(dt*1000)),offset=self.host._offset())

        for oid,row in self.host.actors.items():
            if row.get('dead'):continue
            if row.get('_est_driven'):
                self.native.pop(oid,None);continue
            if oid in self.native and self.native[oid]['move']==row['props'].get('move'):continue
            if row['props'].get('move'):
                self.native[oid]=dict(start=tuple(row['pos']),move=copy.deepcopy(row['props']['move']),epoch=at)
                row['_mechanic_motion']=True
        for oid in list(self.native):self._native_position(oid,set())
        for pair in self.pincers:
            alive=[oid for oid in pair['ids'] if oid in self.host.actors and not self.host.actors[oid].get('dead')]
            if not alive:continue
            if pair['restore'] is not None and at>=pair['restore']:
                for oid in alive:self.host.batch.animprops(oid,1,660,offset=self.host._offset())
                pair['restore']=None
            near=any(pair['left']-8192<=x<=pair['right']+8192 and
                pair['y']-81920<=feet<=pair['y']+34560+4096 for x,feet in self._players())
            if near and pair['pending'] is None and at-pair['last']>=4.:pair['pending']=at+1.
            if at>=pair['next'] or pair['pending'] is not None and at>=pair['pending']:
                pair.update(next=at+8.,pending=None,restore=at+2.5,last=at)
                for oid in alive:self.presentation.action(oid,'pincer',at)
                self.counts['pincer_slashes']+=1

    def _unlock(self,blob):
        door=self.blobs[blob];row=self.host.actors.get(door)
        if row and not row.get('dead'):
            row['props']['interact']=1
            self.host.batch.modify(door,'INTERACT',intval=1,offset=self.host._offset())
        callback=getattr(self.host,'unlock',None)
        if callback:callback('doorblob:'+str(blob),door=door,evidence='D paired blob HP death enables this door')
        self.host.record('est_doorblob_unlock',blob=blob,door=door,evidence='D pairing; USER/local persistent flag')
        self.counts['doors_unlocked']+=1

    def feedback(self,event):
        if not isinstance(event,dict) or event.get('kind') not in ('damage','destroy'):return False
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.host.worldid:return False
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        if oid in self.retired:return True
        token=next(((k,event[k]) for k in ('event_id','seq','native_t','source_t_ms') if k in event),None)
        key=(str(token),event.get('kind'),oid,event.get('value'),event.get('reason')) if token else ('object',id(event))
        if key in self._seen:return True
        self._seen.add(key);self._order.append(key);self._event_refs.append(event)
        if len(self._order)>4096:self._seen.discard(self._order.popleft())
        row=self.host.actors.get(oid)
        if not row or row.get('dead'):self.retired.add(oid);return True
        if row['props'].get('invulnerable') or oid not in self.blobs:return True
        reason=event.get('reason',2)
        if event['kind']=='damage':
            value=int(event.get('value',event.get('val',0)) or 0)
            if value<=0:return True
            row['hp']=max(0,row['hp']-value);row['props']['hitpoints']=row['hp']
            self.host.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self.host._offset())
            if row['hp']>0:return True
            reason=2
        is_hp=reason==2 or str(reason).upper()=='HITPOINTS'
        if is_hp:self._unlock(oid)
        self.host._retire(oid,reason=reason,native=event['kind']=='destroy');self.retired.add(oid)
        return True
    def expire(self,oid):return False
    def interact(self,oid,player):return False
    def status(self):return dict(owned=len(self.owned),native_motions=len(self.native),
        moving_surfaces=len(self.movers),pincer_groups=len(self.pincers),counts=dict(self.counts),
        evidence='D native geometry/attachments; SIM routes and pincer decisions')
