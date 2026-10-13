"""Native Betty actors, lift mechanics and live hit feedback."""
import copy
import math
from ..dumb_wave.session import DumbWaveLiveSession
from ..dumb_common.doors import open_doors
from ..dumb_common.elevators import step_elevators
from ..dumb_common.scene import normalize
from .scene import DumbMidPack
from .controller import DumbMidController

class DumbMidLiveSession(DumbWaveLiveSession):
    MAP_ID='dumb_mid'
    PACK_CLASS=DumbMidPack
    RIDE_MOUNT_MP=6144
    RIDE_BAND_MP=8192

    def prepare(self):
        super().prepare();self.lifts={};self.doors_open=False;self.shields={}
        self._next_id=10000

    def position(self,identity,at=None):
        row=self.actors.get(identity)
        if row and row['kind']=='betty':return tuple(row['pos'])
        return super().position(identity,at)

    def _controller_event(self,event):
        kind=event['kind'];at=event['at'];identity=event.get('identity')
        if kind=='create_piece':
            vid=['dumb.bouncingbetty','dumb.smallbetty','dumb.minibetty'][event['tier']]
            props=copy.deepcopy(self.pack.betty['templates'][vid]);props.update(xmp=round(event['pos'][0]),
                ymp=round(event['pos'][1]),invulnerable=event['invulnerable'],pose=1)
            row=self._add_actor(identity,props,at,kind='betty',hp=event['hp'])
            self.batch.create(identity,self.pack.wire_props(props),offset=self._offset(at))
            if event['tier']==0:
                shield_id=identity+4;self.shields[identity]=shield_id
                shield=copy.deepcopy(self.pack.betty['templates']['dumb.forcefieldbetty'])
                shield.update(xmp=props['xmp'],ymp=props['ymp'],draw=event['invulnerable'],
                    move=dict(relativeto=dict(objid=identity),to=dict(xmp=0,ymp=0)))
                self._add_actor(shield_id,shield,at,kind='static')
                self.batch.create(shield_id,self.pack.wire_props(shield),offset=self._offset(at))
                self._anim(shield_id,1,396,at)
        elif kind=='shield':
            row=self.actors.get(identity)
            if row:
                row['props']['invulnerable']=event['enabled']
                self.batch.modify(identity,'INVULNERABLE',boolval=event['enabled'],offset=self._offset(at))
                shield=self.shields.get(identity)
                if shield in self.actors:self.batch.modify(shield,'DRAW',boolval=event['enabled'],offset=self._offset(at))
        elif kind=='hp':
            if identity in self.actors:
                self.actors[identity]['hp']=event['value'];self.batch.modify(identity,'HITPOINTS',intval=event['value'],offset=self._offset(at))
        elif kind=='destroy_piece':
            shield=self.shields.pop(identity,None)
            if shield is not None:self._retire(shield,1,at)
            self._retire(identity,2,at)
        elif kind=='balls':
            for _ in range(event['count']):
                x=self.rng.uniform(event['x'],event['x']+event['width']-8192)
                props=self.pack.projectile_props('munitions')
                props.update(xmp=round(x),ymp=round(event['y']),wmp=8192,hmp=8192,
                    lifetime=self.pack.betty['ball_lifetime_ms'],damage=30,ownertype=2,blocktype=2,
                    removetype=3,vid='dumb.munitions',pose=3)
                self._next_id+=1
                row=self._add_actor(self._next_id,props,at,kind='projectile',vel=(0,0))
                row.update(bounce=True,expire=at+props['lifetime']/1000.)
                self.batch.create(row['objid'],self.pack.wire_props(props),offset=self._offset(at))
                self.batch.sound(row['objid'],'dumb.grenadegen',offset=self._offset(at));self.counts['shots']+=1
        elif kind in('triangle_path','shake_path','assembly_path'):
            members=event.get('members',[identity]);offsets=event.get('offsets',[(0,0)])
            for identity,shift in zip(members,offsets):
                row=self.actors.get(identity)
                if row is None:continue
                points=[dict(p,xmp=p['xmp']+shift[0],ymp=p['ymp']+shift[1])for p in event['points']]
                if kind=='assembly_path':

                    target=dict(points[0],pos=0,start=dict(xmp=round(event['start'][0]),ymp=round(event['start'][1])))
                    move=dict(to=target)
                else:move=dict(waypoint=dict(points=points))
                self.batch.modify(identity,'MOVE',message=('Properties_Move',normalize('Properties_Move',move,self.layouts)),offset=self._offset(at))
                row['native_path_until']=at+sum(p['dt']for p in points)/1000.

                row['sent']=[points[-1]['xmp'],points[-1]['ymp']]
                row['sent_at']=row['native_path_until'];row['send_at']=row['native_path_until']
        elif kind=='sound':
            source=next(iter(self.controller.parts),19);self.batch.sound(source,event['name'],offset=self._offset(at))
        elif kind=='piece_killed':

            self.counts['splits']+=event['tier']<2
            x,y=event['position'];size=event['size']
            self.supplies.killed(x+size/2,y+size/2)
        elif kind=='complete':open_doors(self,at)
        self._record(kind,at,**{k:v for k,v in event.items()if k not in ('kind','at')})

    def munitions_floor(self,row,nx,ny):
        x,y=row['pos'];w,h=row['props']['wmp'],row['props']['hmp']
        candidates=[sy for sx,sy,sw,sh,_ in self.walls
            if max(x,nx)+w>sx and min(x,nx)<sx+sw and sy>=y+h-256]
        return min(candidates)if candidates else 155648.

    def _feedback(self,event):
        if not isinstance(event,dict)or int(event.get('ownerid',self.worldid))!=self.worldid:return
        identity=int(event.get('victim')or 0)
        if self.controller and identity in self.controller.parts:
            p=self.controller.parts[identity]
            if event.get('kind')=='damage':
                self.counts['native_damage']+=1;self.controller.damage_piece(identity,int(event.get('value')or 0))
            elif event.get('kind')=='destroy'and not p['invulnerable']and identity not in self.controller.group:
                if int(event.get('reason')or 0)in (1,2):self.controller.damage_piece(identity,p['hp'])
            for action in self.controller.take_events():self._controller_event(action)
            return

        if self.actors.get(identity,{}).get('kind')in('projectile','fx'):super()._feedback(event)

    def step(self,dt):
        if not math.isfinite(dt)or dt<0:raise ValueError('dt must be finite and nonnegative')
        state,_=self.bridge.snapshot()
        if state is not None:self.player=state
        self._frame_start=self.room_time
        if self.controller is None and state and state.alive and state.age()<2:
            self.controller=DumbMidController(self.pack,self.seed)
            self.room_time=self._frame_start=0.
            for event in self.controller.begin():self._controller_event(event)
        for event in self.bridge.poll_events():self._feedback(event)
        if self.controller:
            end=self.room_time+dt
            step_elevators(self,self.room_time,end,self.pack.scene['elevator'],self.pack.scene['elevators'])
            geometry=[]
            for row in self.actors.values():
                p=row['props']
                if p.get('terraintype')in(1,2):
                    x,y=self.position(row['objid'],end);geometry.append((x,y,p['wmp'],p['hmp']))
            target=self.player.center()if self.player else None
            for event in self.controller.step(dt,target,geometry):self._controller_event(event)
            for identity,p in self.controller.parts.items():
                row=self.actors.get(identity)
                if row:
                    row['pos']=list(p['pos']);self._pose(identity,p['pose'],end)
                    if end>=row.get('native_path_until',0):self._sync_actor(row,end)
            self._step_projectiles(self.room_time,end);self.room_time=end;self.phase=self.controller.state
        self.supplies.flush();self._wire_ms+=dt*1000;target=round(self._wire_ms)
        self.batch.dt=target-self._sent_ms;self._sent_ms=target
        self.server._send_raw(self.channel,12,self.batch.encode(),'dumb-mid-live',record=False)
        self.frames+=1;self.batch.clear()
