"""Persistent EUB switches, timed lasers, spatial spawners and native lifts."""
import copy
import math

from ..dumb_common.elevators import step_elevators
from ...supply import is_hp_death
from .profiles import SIM,USER,LASER

def overlap(a,b):return a[0]<b[0]+b[2] and a[0]+a[2]>b[0] and a[1]<b[1]+b[3] and a[1]+a[3]>b[1]

class ExplorationMechanics:
    def __init__(self,host):
        self.host=host;self.owned=set();self.states={};self.effects={};self.moves={};self.lifts={};self._seen=set()
        self._teleport_plans={};self._teleport_results=set();self._teleport_presses={}

    def prepare(self):
        for oid,row in self.host.actors.items():
            vid=row['vid']
            if vid in ('dumb.teleportspawner','dumb.flameturret','dumb.eturret','dumb.rturret'):
                self.states[oid]=dict(vid=vid,next=self.host.room_time+.1,children=set(),pending=[])
                self.owned.add(oid);row['_eub_mechanic']=True

    def enemy_retired(self,oid,reason):
        for state in self.states.values():state['children'].discard(oid)

    def _retire(self,oid,reason):
        self.host._retire(oid,reason);self.owned.discard(oid);self.effects.pop(oid,None)
        state=self.states.pop(oid,None)
        if state:
            for pending in state['pending']:
                if pending['fx'] in self.host.actors:self.host._retire(pending['fx'],'CANCEL')

    def expire(self,oid):
        if oid not in self.owned:return False
        self._retire(oid,'LIFETIME');return True

    def feedback(self,event):
        if event.get('kind')=='player_teleport_result':return self.teleport_feedback(event)
        if event.get('kind')=='interact':return self.interact(int(event.get('objid',event.get('victim',0)) or 0),self.host.player)
        oid=int(event.get('victim',event.get('objid',0)) or 0)
        if oid not in self.owned:return False
        row=self.host.actors.get(oid)
        if row is None:return True
        if row['props'].get('invulnerable'):return True
        token=event.get('event_id')
        if token is not None:
            if token in self._seen:return True
            self._seen.add(token)
        if event.get('kind')=='damage' and row.get('hp') is not None:
            row['hp']=max(0,row['hp']-max(0,int(event.get('value',0))))
            self.host.presentation.modify(oid,'hitpoints',row['hp'])
            if not row['hp']:

                self._retire(oid,'HITPOINTS')
        elif event.get('kind')=='destroy':self._retire(oid,'HITPOINTS' if is_hp_death(event.get('reason')) else 'CANCEL')
        return True

    def teleport_plan(self,oid,player):
        for pair in self.host.recipe['mechanics']['telepad_pairs']:
            if oid not in (pair['source'],pair['source']-1):continue
            target=self.host.actors.get(pair['destination'])
            if target is None:return None
            x,y=target['pos'];w=target['props'].get('wmp',0)
            point=[round(x+(w-player.wmp)/2),round(y-player.hmp)]
            return dict(source=pair['source'],destination=pair['destination'],to=point,
                source_fx=[player.xmp+player.wmp/2-8960,player.ymp+player.hmp-38400],
                destination_fx=[point[0]+player.wmp/2-8960,point[1]+player.hmp-38400],
                evidence='FIT native paired-pad module, destination anonymous anchor; client-owned player placement required')
        return None

    @staticmethod
    def _teleport_player_key(player):
        return (player.worldid,player.objid,player.raw.get('life_id'))

    def _teleport_press(self,player=None):
        """Both wrapper and native-feedback entrances share one held press.

        Only an explicit physical release rearms it. An ACK-only datagram,
        missing keys, arrival at the other pad or command completion cannot.
        A new room/player first observed held waits for release, like the
        existing full-level wrapper and Boss physical-key latch.
        """
        current,keys=self.host.bridge.snapshot();player=player or current
        if current is None or player is None or current.age()>.5 or not current.alive:return None
        key=self._teleport_player_key(player)
        if key!=self._teleport_player_key(current):return None
        scope=self._cue_scope();state=self._teleport_presses.get(key)
        if state is None or state['scope']!=scope:
            state=dict(scope=scope,down=None,armed=False,consumed=False);self._teleport_presses[key]=state
        if keys.get('interact') is False:
            state.update(down=False,armed=True,consumed=False)
        elif keys.get('interact') is True:
            state['down']=True
        return state

    def interact(self,oid,player=None,check_range=True):
        oid=int(oid);row=self.host.actors.get(oid);player=player or self.host.player
        from ...interactions import can_interact
        if player is not None and not can_interact(player):return False
        if row is None:return False
        if check_range:
            if player is None or not player.alive:return False
            px,py=player.center();x,y=row['pos'];w,h=row['props'].get('wmp',0),row['props'].get('hmp',0)
            if math.hypot(px-x-w/2,py-y-h/2)>120*256:return False
        state=self.host.recipe.get('switches',{}).get(oid)
        if state is None:
            plan=self.teleport_plan(oid,player) if player is not None else None
            callback=getattr(self.host,'request_player_teleport',None)
            if plan and callback:
                press=self._teleport_press(player)
                if press is None or not press['armed'] or not press['down'] or press['consumed']:return False
                if self.host.bridge.snapshot()[1].get('interaction_blocked'):return False
                applied=callback(oid,player,plan)
                if applied:
                    press['consumed']=True
                    self._teleport_plans[oid]=plan
                return bool(applied)
            return False
        key=state.get('flag','switch:'+str(oid))
        if self.host.instance.flags.get(key):return False
        targets=state['targets'];proofs=state.get('proofs',[])
        direct_moves=proofs[0].get('door_moves',{}) if proofs else {}
        for target in targets:
            door=self.host.actors.get(target)
            if door is None:continue
            move=direct_moves.get(str(target),{})
            point=move.get('to',{})
            x,y=door['pos'];duration=max(1,int(point.get('dt',396)))

            if point:
                end=[point.get('xmp',x),point.get('ymp',y)]
            else:
                other=[self.host.actors[i]['pos'][1] for i in targets if i in self.host.actors]
                end=[x,y-door['props']['hmp'] if y==min(other) else y+door['props']['hmp']]
            self.moves[target]=dict(start=[x,y],end=end,born=self.host.room_time,duration=duration/1000,
                                    born_ms=self.host._sent_ms+self.host._offset(),duration_ms=duration)
            self.host.batch.move_to(target,round(end[0]),round(end[1]),dt=duration,offset=self.host._offset())
        for switch,other in self.host.recipe.get('switches',{}).items():
            if sorted(other['targets'])!=sorted(targets):continue
            if switch in self.host.actors:
                self.host.presentation.modify(switch,'vid','dumb.switchclosed')
                self.host.presentation.modify(switch,'interact',0)
                self.host.unlock(other.get('flag','switch:'+str(switch)),evidence=state['evidence'])
        self.host.record('switch_open',id=oid,targets=targets,evidence=state['evidence'],fixture_bypass_range=not check_range)
        return True

    def teleport_feedback(self,event):
        command=event.get('command_id')
        if command in self._teleport_results:return True
        self._teleport_results.add(command)
        oid=int(event.get('source_oid',0) or 0);plan=self._teleport_plans.pop(oid,None)
        if event.get('applied') is not True or plan is None:return True
        for position in (plan['source_fx'],plan['destination_fx']):
            child=self.host._create('dumb.teleportfx',position,0,dict(lifetime=594))
            self.host.created_at_end(child)

        self.host.presentation.sound(oid,'dumb.teleportenemy')
        self.host.record('telepad_applied',source_oid=oid,command_id=command,evidence=plan['evidence'])
        return True

    def unlock_exits(self,key):
        for oid,row in self.host.actors.items():
            if row['vid']!='dumb.closedDoor':continue
            vid='dumb.prevDoor' if row['props'].get('facing')==180 else 'dumb.nextDoor'
            self.host.presentation.modify(oid,'vid',vid);self.host.presentation.anim(oid,0,660)
        self.host.unlock(key,evidence='SIM finite controller completion; D closedDoor->prev/next presentation')

    def _spawn_candidates(self,oid,state):
        row=self.host.actors[oid];x,y=row['pos'];w=row['props']['wmp'];specs=self.host.recipe['mechanics'].get('spawner_specs',{})
        spec=specs.get(str(oid),specs.get(oid,{}));planes=[]
        if spec.get('spawn_feet_mp') is not None:planes.append(float(spec['spawn_feet_mp']))
        planes.extend(sy for sx,sy,sw,sh,kind in self.host.surfaces() if sx<=x+w/2<sx+sw and sy>=y+row['props']['hmp'])
        if not planes:return []
        feet=min(planes,key=lambda value:abs(value-(y+row['props']['hmp']))) if spec.get('spawn_feet_mp') is None else planes[0]
        result=[]
        for species in SIM['spawner_species']:
            props=self.host.enemies.pack.species_props(species)
            if not props:continue
            width,height=props['wmp'],props['hmp'];left=x+w/2-width/2
            choices=[left]
            if spec.get('spawn_left_range_mp'):choices.extend(spec['spawn_left_range_mp'])
            for left in sorted(set(choices)):
                body=[left,feet-height,width,height]
                blocked=any(overlap(body,[sx,sy,sw,sh]) for sx,sy,sw,sh,kind in self.host.surfaces() if kind==1)
                occupied=any(r.get('_eub_enemy') and overlap(body,[*r['pos'],r['props']['wmp'],r['props']['hmp']]) for r in self.host.actors.values())
                for pending in state['pending']:
                    reserved=self.host.enemies.pack.species_props(pending['species'])
                    occupied|=overlap(body,[*pending['position'],reserved['wmp'],reserved['hmp']])
                if not blocked and not occupied:result.append((species,[left,feet-height]))
        return result

    def _spawner(self,oid,state):
        now=self.host.room_time
        state['children'].intersection_update(self.host.actors)
        for pending in list(state['pending']):
            if now<pending['due']:continue
            child=self.host.enemies.spawn(pending['species'],pending['position'],oid)
            if child is not None:state['children'].add(child)
            state['pending'].remove(pending)
        if now<state['next'] or len(state['children'])+len(state['pending'])>=USER['spawner_capacity']:return
        candidates=self._spawn_candidates(oid,state);state['next']=now+SIM['spawner_retry_s']
        if not candidates:return
        species,pos=self.host.rng.choice(candidates)
        fx=self.host._create('dumb.largeteleportfxenemy' if species=='heavy' else 'dumb.teleportfxenemy',pos,oid)
        self.host.created_at_end(fx)

        self.host.presentation.sound(oid,'dumb.teleportenemy',at_end=True)
        state['pending'].append(dict(species=species,position=pos,due=now+.775,fx=fx))
        self.host.record('spawner_reserved',id=oid,species=species,capacity=2,
            evidence='USER two real live children; D775ms flash->mob; SIM spatial/choice policy; native same-creator three-child counterexample retained')

    def _laser(self,oid,state):
        now=self.host.room_time
        if now<state['next']:return
        row=self.host.actors[oid];specs=self.host.recipe['mechanics'].get('laser_specs',{})
        spec=specs.get(str(oid),specs.get(oid,{}));offset=spec.get('offset_mp',[0,row['props'].get('hmp',16384)])
        w,h=spec.get('dimensions_mp',[16384,57344]);x,y=row['pos']
        child=self.host._create('dumb.flame',(x+offset[0],y+offset[1]),oid,dict(wmp=w,hmp=h,damage=0,lifetime=LASER['lifetime_ms']))
        self.host.created_at_end(child);self.owned.add(child)
        born=self.host.actors[child]['_wire_born_ms'];self.effects[child]=dict(parent=oid,on=born+round(LASER['windup_s']*1000),off=born+round((LASER['windup_s']+LASER['active_s'])*1000),active=False,
            telegraph_at=born+LASER['telegraph_delay_ms'],telegraphed=False,cue_scope=self._cue_scope())
        state['next']=now+SIM['laser_cycle_s']

    def _cue_scope(self):

        context=getattr(self.host.bridge,'native_context',None)
        return tuple(context.get(k) for k in ('map','room_token','room_seed')) if context else None

    def _turret(self,oid,state):
        if self.host.player is None or not self.host.player.alive:return
        row=self.host.actors[oid];player=self.host.player;x,y=row['pos'];w,h=row['props']['wmp'],row['props']['hmp']
        tx,ty=player.center();angle=math.atan2(ty-y-h/2,tx-x-w/2)
        vid='dumb.explodingproj' if state['vid']=='dumb.eturret' else 'dumb.ricochetproj'
        pending=state.get('shot')
        if pending is None:
            if self.host.room_time<state['next'] or math.hypot(tx-x-w/2,ty-y-h/2)>SIM['activation_px']*256:return
            dx,dy=tx-x-w/2,ty-y-h/2

            slot=(3 if dx>=0 else 4) if abs(dx)>=abs(dy) else (2 if dy>=0 else 5)
            delay=self.host.presentation.wall_turret_prepare(oid,slot)
            state['shot']=dict(at=self.host.room_time+delay,slot=slot,angle=angle)
            state['next']=self.host.room_time+SIM['turret_cooldown_s']
            return
        if self.host.room_time<pending['at']:return
        state.pop('shot');slot=pending['slot'];angle=pending['angle']
        specs=self.host.recipe['mechanics'].get('turret_specs',{});spec=specs.get(str(oid),specs.get(oid,{}))
        shot=self.host.pack.props(vid)
        if slot in (3,4):

            offset=[(14080 if slot==3 else 3328)-shot['wmp']/2,8192-shot['hmp']/2]
        else:

            offset=spec.get('offset_mp',[(w-shot['wmp'])/2,h if slot==2 else -shot['hmp']])
        speed=60000.;child=self.host.enemies.shot(vid,oid,(x+offset[0],y+offset[1]),(speed*math.cos(angle),speed*math.sin(angle)))
        if vid=='dumb.ricochetproj':self.host.actors[child]['kind']='custom-projectile';self.host.enemies.custom[child]=dict(ricochet=True)
        self.host.record('wall_turret_fire',id=oid,slot=slot,child=child,evidence='D2640ms shooting animation/2310ms emit; SIM target direction/aim lock/cooldown')

    def step(self,dt):
        self._teleport_press()
        now=self.host.room_time
        for oid,move in list(self.moves.items()):
            row=self.host.actors.get(oid)
            if row is None:self.moves.pop(oid);continue
            progress=min(1.,max(0.,(self.host.wire_end_ms-move['born_ms'])/move['duration_ms']))
            row['pos']=[a+(b-a)*progress for a,b in zip(move['start'],move['end'])]
            if progress>=1:self.moves.pop(oid)
        for oid,state in list(self.states.items()):
            if oid not in self.host.actors:continue
            if state['vid']=='dumb.teleportspawner':self._spawner(oid,state)
            elif state['vid']=='dumb.flameturret':self._laser(oid,state)
            else:self._turret(oid,state)
        for oid,effect in list(self.effects.items()):
            if oid not in self.host.actors:self.effects.pop(oid);continue
            if not effect['telegraphed']:
                if effect['cue_scope']!=self._cue_scope() or self.host.actors[oid].get('dead'):
                    effect['telegraphed']=True
                elif self.host.wire_end_ms>=effect['telegraph_at']:
                    offset=max(0,min(self.host._slice_ms,effect['telegraph_at']-self.host._sent_ms))
                    self.host.presentation.sound(oid,'dumb.flametelegraph',offset=offset)
                    effect['telegraphed']=True
            active=effect['on']<=self.host.wire_end_ms<effect['off']
            if active!=effect['active']:
                self.host.presentation.modify(oid,'damage',LASER['damage'] if active else 0);effect['active']=active
                if active:self.host.presentation.sound(oid,'dumb.flamegen')
        elevators=self.host.recipe['mechanics']['elevators']
        if elevators:
            adapter=self.host.enemies;adapter.player=self.host.player;adapter.lifts=self.lifts;adapter.RIDE_MOUNT_MP=6144;adapter.RIDE_BAND_MP=8192
            rule=dict(bottom_ymp=max(self.host.actors[i]['props'].get('ymp',0) for i in elevators),ceiling_ymp=self.host.recipe['metadata']['bounds_mp'][1]+16384,speed_mp_s=30000.)
            step_elevators(adapter,now-dt,now,rule,elevators)

    def status(self):return dict(devices=len(self.states),effects=len(self.effects),moving_doors=len(self.moves),
        spawners={str(oid):dict(alive=len(s['children']),pending=len(s['pending']),capacity=USER['spawner_capacity'],
            evidence='USER two; D native same-creator three-child counterexample unresolved') for oid,s in self.states.items() if s['vid']=='dumb.teleportspawner'})

EubMechanics=ExplorationMechanics
