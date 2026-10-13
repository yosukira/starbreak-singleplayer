'ETL live environment and switch rooms; never runtime event playback.'
import copy
import math
from collections import Counter

from ..etl_motion import NativeMotion
from .abilities import (NATIVE,Ownership,Projectiles,rect,center,players,nearest,
    move,set_prop,follow,visible,geometry_changed)
from .presentation import projectile_top_left

TURRETS={'lab.turretv','lab.turretvp','lab.turreth','lab.turrethp','lab.turrethceiling','lab.turrethceilingp'}
GENERATORS={'lab.bouldergen','lab.bulkgen','lab.bulkgenh','lab.flowervine','lab.clingvinebase',
            'lab.clingvinebaseup','lab.snakegen','lab.leafemitter'}
STATIC={'lab.trap','lab.trapm','lab.pplate','lab.switch','lab.switchopen','lab.display1','lab.display2','lab.display3',
        'lab.blastdoor','lab.blastdoorup','lab.bulkhead','lab.bulkheadh','lab.bbulkhead','lab.zombiespawner','lab.vspines','lab.bledge'}

class ExplorationMechanics(Ownership):
    def __init__(self,host):
        super().__init__(host,'_etl_mechanic');self.states={};self.tracks={};self.projectiles=Projectiles(host,'_etl_mechanic_projectile')
        self.pending=[];self.challenge=None;self.now=host.room_time;self.counts=Counter();self.plate_traps={}

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        traps=[(oid,row) for oid,row in self.host.actors.items() if row['vid']=='lab.trap']
        for oid,row in self.host.actors.items():
            if row['vid']!='lab.pplate':continue
            x,y,w,h=rect(row)

            matches=[tid for tid,t in traps if t['pos'][0]==x and t['props'].get('wmp')==w
                     and abs(t['pos'][1]+t['props'].get('hmp',0)-y)<=8192]
            self.plate_traps[oid]=matches
        self.host.record('etl_mechanic_policy',evidence='D switch/doors/rows/actions; pressure/generator/respawn choices SIM',
            room=getattr(self.host,'recipe',{}).get('role'))

    def add_actor(self,oid):
        row=self.host.actors.get(oid)
        if not row or row.get('dead') or row.get('_etl_enemy') or oid in self.retired:return False
        if oid in self.owned:return True
        vid=row['vid']
        if vid not in TURRETS|GENERATORS|STATIC:return False
        self.own(oid)
        self.states[oid]=dict(vid=vid,home=tuple(row['pos']),next=self.now+.8,child=None,
            phase='idle',pressed=False,rearm=True,expire=None,pending_emit=None)
        movement=row['props'].get('move') or {}
        if movement and not movement.get('relativeto'):
            track=NativeMotion(row['pos']);track.set(movement,self.now);self.tracks[oid]=track
        return True

    def _set_move(self,oid,movement):
        row=self.host.actors[oid];track=self.tracks.setdefault(oid,NativeMotion(row['pos']))
        clock=getattr(self.host,'wire_end_ms',round(self.now*1000))/1000.
        track.set(movement,clock);row['props']['move']=copy.deepcopy(movement)

        offset=getattr(self.host,'_slice_ms',0) if getattr(self.host,'_in_step',False) else self.host._offset()
        self.host.batch.modify(oid,'MOVE',message=('Properties_Move',self.host.normalize('Properties_Move',movement)),offset=offset)

    def _attack(self,oid,anim,duration):
        vid=self.host.actors[oid]['vid']
        if getattr(self.host,'presentation',None):self.host.presentation.attack(oid,vid,anim,duration,token=self.now)
        else:self.host.batch.animprops(oid,anim,duration,offset=self.host._offset())
        self.host.record('etl_mechanic_attack',id=oid,vid=vid,anim=anim,duration_ms=duration,evidence='D native action; trigger/cadence SIM')

    def interact(self,oid,player=None):
        from ...interactions import can_interact
        if player is not None and not can_interact(player):return False
        oid=int(oid);state=self.states.get(oid);row=self.host.actors.get(oid)
        if not state or not row or row['vid']!='lab.switch' or self.challenge is not None:return False
        self.now=self.host.room_time
        set_prop(self.host,oid,'interact',0)
        self.challenge=dict(switch=oid,pressed=self.now,open_at=self.now+.2,phase='switch-delay',
            next_row=0,spawned=[],opened=False)

        if hasattr(self.host,'unlock'):self.host.unlock('bulkhead_switch',evidence='D switch accepted; physical door travel remains3000ms')
        self.host.record('etl_bulkhead_switch',id=oid,evidence='D switch/200ms opening delay; no kill-all timer')
        return True

    def _open_challenge(self):
        c=self.challenge;oid=c['switch'];set_prop(self.host,oid,'vid','lab.switchopen')
        for key,row in list(self.host.actors.items()):
            if row['vid'] in ('lab.blastdoor','lab.blastdoorup'):
                x,y=row['pos'];dy=-81920 if row['vid'].endswith('up') else 81920
                self._set_move(key,dict(to=dict(xmp=round(x),ymp=round(y+dy),dt=3000)))
                if getattr(self.host,'presentation',None):self.host.presentation.sound(key,'lab.bdooropen',self.now)
            elif row['vid'] in ('lab.display1','lab.display2'):
                set_prop(self.host,key,'vid','lab.display2' if row['vid']=='lab.display1' else 'lab.display3')
        c['phase']='deploying';c['next_deploy']=self.now+.650

    def _step_challenge(self):
        c=self.challenge
        if c is None:return
        if c['phase']=='switch-delay' and self.now+1e-9>=c['open_at']:self._open_challenge()
        if c['phase']=='deploying' and self.now+1e-9>=c['next_deploy']:
            recipe=getattr(self.host,'recipe',{})
            role=recipe.get('metadata',{}).get('source_role',recipe.get('role','bulkhead_1'))
            source=NATIVE['challenge_spawns'].get(role,NATIVE['challenge_spawns']['bulkhead_1'])

            ys=sorted({item['position'][1] for item in source})
            if c['next_row']<len(ys):
                y=ys[c['next_row']]
                for item in source:
                    if item['position'][1]!=y:continue
                    props=copy.deepcopy(item['props']);vid=props.pop('vid')
                    child=self.host._create(vid,item['position'],c['switch'],props)
                    self.add_actor(child);set_prop(self.host,child,'facing',item['facing'])
                    self._attack(child,3,594)
                    if item['vid_after']!=vid:set_prop(self.host,child,'vid',item['vid_after'])
                    if item['deploy_move']:self._set_move(child,item['deploy_move'])
                    self.states[child]['next']=self.now+1.070
                    c['spawned'].append(child)
                c['next_row']+=1;c['next_deploy']+=.175
            if c['next_row']>=len(ys):c['phase']='active'
        c['opened']=self.now+1e-9>=c['open_at']+3.

    def _plate(self,oid,state):
        row=self.host.actors[oid];x,y,w,h=rect(row)
        down=False
        for p in players(self.host):
            px,py=center(p);ph=p.get('hmp',21504) if isinstance(p,dict) else p.hmp
            pw=p.get('wmp',15360) if isinstance(p,dict) else p.wmp
            if px+pw/2>x and px-pw/2<x+w and abs(py+ph/2-y)<=4096:down=True
        if not down:state['rearm']=True
        if down and state['rearm'] and not state['pressed']:
            state.update(pressed=True,rearm=False,release=self.now+.576,trap_at=self.now+.051,
                         fired_traps=[],press_at=self.now)
            self._set_move(oid,dict(to=dict(xmp=state['home'][0],ymp=state['home'][1]+2048,dt=100)))
            if getattr(self.host,'presentation',None):self.host.presentation.sound(oid,'lab.pplatetrigger',self.now)
            self.host.record('etl_pressure_plate',id=oid,traps=self.plate_traps.get(oid,[]),evidence='D plate2048/100ms and trap delay; player-foot trigger SIM')
        if state.get('trap_at') is not None and self.now+1e-9>=state['trap_at']:
            for trap in self.plate_traps.get(oid,[]):
                if trap in self.host.actors:
                    self._attack(trap,1,1980);state['fired_traps'].append(trap)
            state['trap_at']=None
        if state['pressed'] and self.now+1e-9>=state['release']:
            self._set_move(oid,dict(to=dict(xmp=state['home'][0],ymp=state['home'][1],dt=100)))

            offset=getattr(self.host,'_slice_ms',0) if getattr(self.host,'_in_step',False) else self.host._offset()
            for trap in state['fired_traps']:
                current=self.host.actors.get(trap)
                if current is None or current.get('dead') or current['vid']!='lab.trap':continue
                if getattr(self.host,'presentation',None):
                    self.host.presentation.sound(trap,'lab.trap',('plate-return',oid,state['press_at']),offset=offset)
                else:self.host.batch.sound(trap,'lab.trap',offset=offset)
            state['fired_traps']=[]
            state['pressed']=False

    def _turret(self,oid,state):
        row=self.host.actors[oid];x,y,w,h=rect(row);target=nearest(self.host,(x+w/2,y+h/2))
        if target is None or self.now<state['next'] or math.dist(center(target),(x+w/2,y+h/2))>1000*256:return
        self._attack(oid,1,990)
        self.pending.append(dict(at=self.now+.792,owner=oid,kind='turret-shot'))
        state['next']=self.now+3.0

    def _new_enemy(self,vid,position,owner):
        child=self.host._create(vid,position,owner)
        self.host.enemies.add_actor(child)
        self.host.record('etl_generator_enemy',id=child,parent=owner,vid=vid,evidence='D species/native birth action; geometry-derived outlet/regeneration SIM')
        return child

    def _plant_outlet(self,row):
        vid=row['vid'];x,y,w,h=rect(row)
        species={'lab.flowervine':'lab.flowerdropper','lab.clingvinebase':'lab.clingvinedown',
            'lab.clingvinebaseup':'lab.clingvineup','lab.snakegen':'lab.plantsnake'}[vid]
        props=self.host.pack.props(species);cw,ch=props.get('wmp',0),props.get('hmp',0)

        if vid=='lab.flowervine':position=(x+w/2-cw/2,y-ch/2)
        elif vid=='lab.snakegen':position=(x,y)
        else:position=(x+w/2-cw/2,y+h if vid=='lab.clingvinebase' else y-ch)
        return species,position

    def _generator(self,oid,state):
        row=self.host.actors[oid];vid=state['vid'];x,y,w,h=rect(row);point=(x+w/2,y+h/2)
        target=nearest(self.host,point)
        if target is None or math.dist(center(target),point)>1000*256 or self.now<state['next']:return
        child=state.get('child')
        if child in self.host.actors:return
        if vid in ('lab.flowervine','lab.clingvinebase','lab.clingvinebaseup','lab.snakegen'):
            species,position=self._plant_outlet(row)
            occupied=state.get('occupied')
            if occupied in self.host.actors:return
            if occupied is not None:
                state.update(occupied=None,next=self.now+8.)
                return

            matches=[key for key,q in self.host.actors.items() if q['vid']==species
                and tuple(q['pos'])==tuple(position) and not q.get('dead')]
            if matches:
                state['occupied']=min(matches)
                self.host.record('etl_generator_outlet_occupied',id=oid,occupant=state['occupied'],
                    evidence='D exact outlet coordinates; duplicate suppression SIM, no creator inference')
                return
            state['child']=self._new_enemy(species,position,oid)
            if species.startswith('lab.clingvine'):
                self._attack(state['child'],2,1848)
            elif species=='lab.plantsnake':self._attack(state['child'],2,1056)
            else:self._attack(state['child'],1,660)
            state['next']=self.now+8.
        elif vid=='lab.leafemitter':
            px,py=center(target);angle=math.atan2(py-point[1],px-point[0])
            self.projectiles.spawn('lab.leaf',point,(math.cos(angle)*80000,math.sin(angle)*80000),oid,lifetime=13.5)
            state['next']=self.now+2.5
        elif vid=='lab.bouldergen':
            state['child']=self.projectiles.spawn('lab.boulders',(x+w/2-16384,y+h),(0.,0.),oid,
                gravity=340000.,lifetime=12.)
            if getattr(self.host,'presentation',None):self.host.presentation.sound(oid,'lab.bulkheaddown',self.now)
            state['next']=self.now+5.
        elif vid in ('lab.bulkgen','lab.bulkgenh'):
            species='lab.bulkheadh' if vid.endswith('h') else 'lab.bulkhead'
            props=self.host.pack.props(species);cw,ch=props['wmp'],props['hmp']
            position=(x+w,y+h/2-ch/2) if vid.endswith('h') else (x+w/2-cw/2,y+h)

            overlaps=any(q['props'].get('terraintype')==1 and q['pos'][0]<position[0]+cw
                and q['pos'][0]+q['props'].get('wmp',0)>position[0] and q['pos'][1]<position[1]+ch
                and q['pos'][1]+q['props'].get('hmp',0)>position[1] for key,q in self.host.actors.items() if key!=oid)
            if not overlaps:
                child=self.host._create(species,position,oid);self.add_actor(child)
                self.states[child]['expire']=self.now+8.;state['child']=child
                self._attack(child,1,462)
                if getattr(self.host,'presentation',None):self.host.presentation.sound(child,'lab.bulkhead',self.now)
                turret_vid='lab.turreth' if vid.endswith('h') else 'lab.turretv'
                turret_props=self.host.pack.props(turret_vid)

                turret_origin=self.host.actors[child]['pos'] if vid=='lab.bulkgenh' else position
                child_turret=self.host._create(turret_vid,turret_origin,child,turret_props)
                self.add_actor(child_turret);self.states[child_turret]['expire']=self.now+8.
                if (vid=='lab.bulkgenh' and (cw,ch)==(26624,32768) and
                        (turret_props.get('wmp'),turret_props.get('hmp'))==(24576,8192)):

                    set_prop(self.host,child_turret,'facing',270)
                    self._attack(child_turret,3,594)
                    tx,ty=self.host.actors[child_turret]['pos']
                    self._set_move(child_turret,dict(to=dict(xmp=round(tx),ymp=round(ty)-8192,dt=500)))
                    self.host.record('etl_bulkhead_turret_deployment',id=child_turret,wall=child,generator=oid,
                        local_offset_mp=[0,-8192],duration_ms=500,
                        evidence='D own bracket action/FACING/MOVE; local single-wall/gun association SIM, no native creator asserted')
            state['next']=self.now+10.
            self.host.record('etl_bulkhead_growth',id=oid,child=state.get('child'),evidence='D native segments/turrets; generator association/growth/cap8s SIM')

    def step(self,dt):
        self.now=self.host.room_time;self._step_challenge()
        for oid,state in list(self.states.items()):
            row=self.host.actors.get(oid)
            if row is None:self.states.pop(oid,None);continue
            if state.get('expire') is not None and self.now+1e-9>=state['expire']:
                self.retire(oid,'LIFETIME');continue
            if row['vid'] in TURRETS:self._turret(oid,state)
            elif state['vid']=='lab.pplate':self._plate(oid,state)
            elif state['vid'] in GENERATORS:self._generator(oid,state)
        for oid,track in list(self.tracks.items()):
            row=self.host.actors.get(oid)
            if row is not None:
                position=list(track.position(getattr(self.host,'wire_end_ms',round(self.now*1000))/1000.))
                if row['pos']!=position and row['props'].get('terraintype') in (1,2):geometry_changed(self.host)
                row['pos']=position
            else:self.tracks.pop(oid,None)
        for oid in self.owned:
            if oid in self.host.actors:follow(self.host,oid)
        for event in list(self.pending):
            if event['at']>self.now+1e-9:continue
            self.pending.remove(event);oid=event['owner'];row=self.host.actors.get(oid)
            if row is None:continue
            x,y,w,h=rect(row);vid=row['vid'];facing=row['props'].get('facing',0)
            if vid.startswith('lab.turretv'):angle=180 if facing==180 else 0
            else:angle=90 if 'ceiling' in vid or facing==180 else 270
            shot='lab.thumperp' if vid.endswith('p') else 'lab.thumper'
            speed=29500. if shot.endswith('p') else 39215.686
            origin=(x+w/2,y+h/2);r=math.radians(angle)
            position=projectile_top_left(row,self.host.pack.props(shot))

            if position is None:position=origin
            self.projectiles.spawn(shot,position,(math.cos(r)*speed,math.sin(r)*speed),oid,lifetime=8.)
        self.projectiles.step(dt)

    def retire(self,oid,reason='INTERNAL',native=False):
        state=self.states.pop(oid,None);self.tracks.pop(oid,None)
        self.pending=[e for e in self.pending if e['owner']!=oid]
        if state and state.get('child') in self.host.actors:
            child=state['child']
            if self.host.actors[child].get('_etl_enemy'):self.host.enemies.retire(child,'INTERNAL')
        for child,row in list(self.host.actors.items()):
            if row.get('creator')==oid and child in self.states:self.retire(child,'INTERNAL')
        super().retire(oid,reason,native)

    def feedback(self,event):
        if self.projectiles.feedback(event):return True
        oid=int(event.get('victim',event.get('objid',0)) or 0);row=self.host.actors.get(oid)
        return super().feedback(event,drop=bool(row and row['vid'] in TURRETS and row.get('hp',0)>1 and row['props'].get('removetype')==1))

    def expire(self,oid):
        if self.projectiles.expire(oid):return True
        if oid not in self.owned:return False
        self.retire(oid,'LIFETIME');return True

    def status(self):
        return dict(objects=len(self.states),projectiles=len(self.projectiles.shots),
            inventory=dict(Counter(s['vid'] for s in self.states.values())),
            challenge=copy.deepcopy(self.challenge),counts=dict(self.counts),
            policyEvidence='D plate/doors/deployment/animation/projectile fields; trigger/generator/cadence SIM',
            unresolved=['lab.zombiespawner: no child observed in this run; preserved static, no invented pogo wave'])
