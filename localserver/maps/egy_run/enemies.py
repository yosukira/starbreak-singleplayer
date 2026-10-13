'Live ordinary EGY enemies; native capabilities with exploration policy.'
import copy
import math
from collections import Counter, deque
from types import SimpleNamespace

from ...live import constants
from ...live.enemies import Enemy, BEHAVIOUR, GROUND_HORIZONTAL_SPEEDS, HEAVY_HORIZONTAL_SPEEDS, loader_flame_move
from ...live.projectiles import ProjectileSystem, Projectile, segment_hit, rects_overlap
from ...live.terrain import GroundMotion, segment_hits_rect
from ...live.aircraft import AircraftMotion, ACQUIRE_RANGE, PATROL_PHASE_SECONDS, RISE_SPEED
from ...live.rejoin import jump_speed
from ...live.wire import DESTROY_REASON
from .profiles import FAMILIES, LOADER_THROWS, WALLER_LOBS, TEMPLATES, OBSERVED_CROUCH_ATTACKS, profile

class _Rules:
    """Keep ordinary cannon balls separate from the boss dronebomb preset."""
    ENEMY_SHOTS = copy.deepcopy(constants.ENEMY_SHOTS)
    ENEMY_SHOTS['cannonshot'] = dict(vid='hulk.cannonshot', damage=55, speed=65000,
        lifetime=3.,size=8192,kind='falling',gravity=117187.5,burst_vid='hulk.cannonshotx')
    ENEMY_SHOTS['cannonshotenergy'] = dict(vid='hulk.cannonshotenergy',damage=35,speed=65000,
        lifetime=8.,size=8192,kind='falling',gravity=117187.5,burst_vid='hulk.cannonshotenergyx')

    ENEMY_SHOTS['dronebomb'] = copy.deepcopy(constants.ENEMY_SHOTS['cannonshot'])

    def __getattr__(self, name):
        return getattr(constants, name)

class _Surface:
    def __init__(self, oid, rect, kind):
        self.objid, self._rect, self.kind = oid, tuple(rect), int(kind)

    def rect(self):
        return self._rect

    def get(self, name, default=None):
        return self.kind if name == 'terraintype_' else default

class _Room:
    CELL=256*256
    def __init__(self, host):
        self.host, self.objects = host, {}
        self._fingerprint=None;self._surfaces=[];self.by_id={};self.previous_by_id={};self._geometry_time=None
        self._grid={};self._order={};self._span_cache={};self._query_boxes={}
        self.refresh()

    def refresh(self):
        rows = self.host.actors
        identities = {};bounds_flags={}
        for oid,row in rows.items():
            p=row['props']
            if not row.get('dead') and p.get('terraintype') in (1,2):
                signature=tuple(row['pos'])+(p.get('wmp',0),p.get('hmp',0),p['terraintype'])
                identities.setdefault(signature,deque()).append(oid)
                bounds_flags[oid]=p.get('boundsworld',True) is not False
        geometry=[]
        for values in self.host.surfaces():
            x,y,w,h,kind=values
            if kind not in (1,2) or w<=0 or h<=0: continue
            matches=identities.get(tuple(values))
            key=matches.popleft() if matches else ('surface',)+tuple(values)
            geometry.append((key,(x,y,w,h),kind))
        if self._geometry_time!=self.host.room_time:
            self.previous_by_id=self.by_id;self._geometry_time=self.host.room_time
        fingerprint=tuple((key,rect,kind,bounds_flags.get(key,True)) for key,rect,kind in geometry)
        if fingerprint==self._fingerprint:return
        self._fingerprint=fingerprint
        self._surfaces=geometry;self.by_id={key:(rect,kind) for key,rect,kind in geometry}
        self.objects={key:_Surface(key,rect,kind) for key,rect,kind in geometry}
        self._order={key:index for index,(key,rect,kind) in enumerate(geometry)}
        self._grid={};self._span_cache={};self._query_boxes={}
        for key,(x,y,w,h),kind in geometry:
            previous=self.previous_by_id.get(key,((x,y,w,h),kind))[0]
            qx,qy=min(x,previous[0]),min(y,previous[1])
            qw,qh=max(x+w,previous[0]+previous[2])-qx,max(y+h,previous[1]+previous[3])-qy
            self._query_boxes[key]=(qx,qy,qw,qh)
            for ix in range(math.floor(qx/self.CELL),math.floor((qx+qw)/self.CELL)+1):
                for iy in range(math.floor(qy/self.CELL),math.floor((qy+qh)/self.CELL)+1):
                    self._grid.setdefault((ix,iy),[]).append(key)
        rects=[rect for key,rect,kind in geometry if bounds_flags.get(key,True)]
        if not rects:rects=[rect for key,rect,kind in geometry]
        if rects:
            x=min(r[0] for r in rects);y=min(r[1] for r in rects)
            self._bounds=(x,y,max(r[0]+r[2] for r in rects)-x,max(r[1]+r[3] for r in rects)-y)
        else:self._bounds=(-10**8,-10**8,2*10**8,2*10**8)

    def surfaces(self):
        return self._surfaces

    def bounds(self):
        return self._bounds

    def query(self,rect):
        """Broad phase only. Exact retail-compatible sweeps run afterwards."""
        x,y,w,h=rect;keys=set()
        for ix in range(math.floor(x/self.CELL),math.floor((x+w)/self.CELL)+1):
            for iy in range(math.floor(y/self.CELL),math.floor((y+h)/self.CELL)+1):
                keys.update(self._grid.get((ix,iy),()))
        result=[]
        for key in sorted(keys,key=self._order.__getitem__):
            other,kind=self.by_id[key];sx,sy,sw,sh=self._query_boxes[key]
            if sx<=x+w and sx+sw>=x and sy<=y+h and sy+sh>=y:result.append((key,other,kind))
        return result

    def connected_span(self,rect):
        left,top,width,_=rect;right=left+width;cache_key=(left,top,right)
        if cache_key in self._span_cache:return self._span_cache[cache_key]
        intervals=sorted((r[0],r[0]+r[2]) for key,r,kind in self._surfaces if abs(r[1]-top)<=1)
        for lo,hi in intervals:
            if lo<=right and hi>=left:left,right=min(left,lo),max(right,hi)

        changed=True
        while changed:
            changed=False
            for lo,hi in intervals:
                if lo<=right and hi>=left:
                    a,b=min(left,lo),max(right,hi)
                    if (a,b)!=(left,right):left,right,changed=a,b,True
        self._span_cache[cache_key]=(left,right)
        return left,right

class _GroundMotion(GroundMotion):
    """Use the existing exact solver on a swept local candidate set."""
    def __init__(self,room,body):
        self.body=body;self._local=None;self._climb_kinematic=False
        super().__init__(room)

    def _fall_displacement(self,dt):

        if self._climb_kinematic:return self.vy*dt
        return super()._fall_displacement(dt)

    def surfaces(self):
        return self._local if self._local is not None else self.room.surfaces()

    def step(self,body,dt,vx,now,avoid_edges=False):
        dx=vx*dt;dy=self.vy*dt+(0. if self._climb_kinematic else .5*self.gravity*dt*dt)
        carry=(0.,0.)
        if self.support in self.room.by_id and self.support_rect:
            rect,kind=self.room.by_id[self.support]
            carry=(rect[0]-self.support_rect[0],rect[1]-self.support_rect[1])
        x0,y0=body.x,body.y;x1,y1=x0+dx+carry[0],y0+dy+carry[1]
        box=(min(x0,x1)-2,min(y0,y1)-2,abs(x1-x0)+body.w+4,abs(y1-y0)+body.h+4)
        self._local=self.room.query(box)
        if self.support in self.room.by_id and not any(key==self.support for key,rect,kind in self._local):
            rect,kind=self.room.by_id[self.support];self._local.append((self.support,rect,kind))

        self.previous_surfaces=self.room.previous_by_id
        return super().step(body,dt,vx,now,avoid_edges=avoid_edges)

class _Body:
    def __init__(self,row):
        self.x,self.y=map(float,row['pos'])
        self.w=int(row['props'].get('wmp',12800));self.h=int(row['props'].get('hmp',23040))
        self.facing=-1 if row['props'].get('facing',0)==180 else 1

    def center(self): return self.x+self.w/2,self.y+self.h/2
    def rect(self): return self.x,self.y,self.w,self.h

class _Climber:
    """Ordinary adapter for the existing Boss wall-climb/dismount capability.

    D ordinary bodies also climb short, wide barriers/fgen. Only candidate
    eligibility and the physical over-top exit differ from the Boss's tall
    wall route; its segment merge, 49k ascent and anim10 hold are reused.
    """
    _climb_wall = Enemy._climb_wall
    _step_wall_climb = Enemy._step_wall_climb
    _step_wall_climb_hold = Enemy._step_wall_climb_hold
    _begin_wall_dismount = Enemy._begin_wall_dismount
    _step_wall_dismount = Enemy._step_wall_dismount

    def __init__(self,oid,state):
        self.body=state['body'];self.motion=state['motion'];self.rules=constants
        self.objid=oid;self.vid=state['vid'];self.rule=dict(threshold=state['policy']['attack_px'])
        self.climb_side=0;self.climb_started=self.climb_route=self.climb_start_y=None
        self.climb_hold_until=self.climb_dismount_until=self.wall_jump_until=0.
        self.wall_jump_vx=0.;self.next_jump=0.;self.state='patrol';self.pose=4
        self.over_rect=None

    x=property(lambda self:self.body.x,lambda self,value:setattr(self.body,'x',value))
    y=property(lambda self:self.body.y,lambda self,value:setattr(self.body,'y',value))
    w=property(lambda self:self.body.w)
    h=property(lambda self:self.body.h)
    facing=property(lambda self:self.body.facing,lambda self,value:setattr(self.body,'facing',value))

    def center(self):return self.body.center()

    def _travel_speed(self,direction,running=True):
        return HEAVY_HORIZONTAL_SPEEDS['shredder']['run' if running else 'walk'][direction]

    def _climb_wall_candidates(self,side):
        edge=self.x+self.w if side>0 else self.x
        return [item for item in self.motion.room.objects.values()
            if item.kind==1 and item.rect()[2]>0 and item.rect()[3]>0
            and abs((item.rect()[0] if side>0 else item.rect()[0]+item.rect()[2])-edge)<512]

    def _touching_wall(self,side):
        return next((item for item in self._climb_wall_candidates(side)
            if self.y<item.rect()[1]+item.rect()[3] and self.y+self.h>item.rect()[1]),None)

    def _climb_wall_top(self,side):
        top=Enemy._climb_wall_top(self,side)

        return None if top is None else top+24*256

    def _detach_from_wall(self,now,player,results):
        Enemy._detach_from_wall(self,now,None,results)

        self.facing=-self.climb_side
        self.wall_jump_vx=self.facing*self._travel_speed(self.facing,True)

class _ExplorationFlight(AircraftMotion):
    """Full shared flight capability; ordinary timed-turn policy stays SIM.

    Ordinary2153 proves wall clipping and committed headings, while1859/2973
    also turn without a side wall. Their short ceiling legs do not establish
    the Boss5.1s timer, so reuse it provisionally instead of deleting it.
    """
    def __init__(self,body,policy):
        super().__init__(body)
        self.wall_hold_s=policy['drone_wall_hold_s']
        self.wall_height_policy=policy['drone_wall_height_policy']
        self.descent_speed=policy['drone_descent_speed_mp_s']
        self._height_speed_pending=False
        self.wall_contact=None
        self.wall_replans=0
        self.last_wall_decision=None

    def _speed(self,body,value):

        height=self._height_speed_pending
        self._height_speed_pending=False
        if (height and self.active and self.clear and value==RISE_SPEED
                and self.goal_y>body.y+.001):
            return super()._speed(body,self.descent_speed)
        return super()._speed(body,value)

    def _contact_present(self,body):
        contact=self.wall_contact
        if contact is None:return False

        for key,rect,kind in body.motion.surfaces():
            if key!=contact['key'] or kind!=1 or tuple(rect)!=contact['rect']:continue
            x,y,w,h=rect
            edge=body.x+body.w if contact['side']>0 else body.x
            wall_edge=x if contact['side']>0 else x+w
            return abs(edge-wall_edge)<.001 and body.y<y+h and body.y+body.h>y
        return False

    def _replan_wall(self,body,player,now):
        contact=self.wall_contact
        valid=(player is not None and player.alive and
            math.hypot(body.center()[0]-player.center()[0],body.center()[1]-player.center()[1])<=ACQUIRE_RANGE)
        previous_goal=self.goal_y;candidate=None;height_accepted=False
        height_reason='invalid-target'
        if contact.get('vertical_at_contact'):

            height_reason='committed-vertical'
        elif valid:
            candidate=self._goal(body,player)
            height_accepted=(candidate<min(previous_goal,body.y)-.001)
            height_reason='upward-goal' if height_accepted else 'lower-or-same-goal'
        if height_accepted:
            self.goal_y=candidate
            self.target_x=player.center()[0]

            if abs(self.goal_y-body.y)>.001:
                self.direction=-1 if self.target_x<body.center()[0] else 1
        self.next_patrol_at=None if abs(self.goal_y-body.y)>.001 else now+PATROL_PHASE_SECONDS
        contact['selected']=True
        self.wall_replans+=1
        self.last_wall_decision=dict(wall=contact['key'],side=contact['side'],
            contact_at=contact['at'],selected_at=now,target_valid=valid,
            direction=self.direction,goal_y=self.goal_y,previous_goal_y=previous_goal,
            candidate_goal_y=candidate,height_accepted=height_accepted,
            height_reason=height_reason,height_policy=self.wall_height_policy)

    def step(self,body,dt,player,now):

        if not self.clear:self.launch_top=self._safe_height(body,self.launch_top)
        self.goal_y=self._safe_height(body,self.goal_y)
        if self.wall_contact is not None:
            if not self._contact_present(body):self.wall_contact=None
            elif not self.wall_contact['selected']:
                if now+1e-9<self.wall_contact['until']:
                    body.state,body.pose='aircraft-wall-hold',2
                    body.facing=self.direction
                    return
                self._replan_wall(body,player,now)
        self._height_speed_pending=True
        try:super().step(body,dt,player,now)
        finally:self._height_speed_pending=False

    def _move_horizontal(self,body,distance,now):
        old_x=body.x
        incoming=1 if distance>0 else -1 if distance<0 else 0
        super()._move_horizontal(body,distance,now)
        if not incoming:return
        if self.direction==incoming and abs(body.x-old_x-distance)<.001:return
        hit=None
        for key,rect,kind in body.motion.surfaces():
            x,y,w,h=rect
            if kind!=1 or not (body.y<y+h and body.y+body.h>y):continue
            crossed=(old_x+body.w<=x and old_x+body.w+distance>=x) if incoming>0 else (old_x>=x+w and old_x+distance<=x+w)
            edge=body.x+body.w if incoming>0 else body.x
            if crossed and abs(edge-(x if incoming>0 else x+w))<.001:
                hit=(key,tuple(rect));break
        if hit is None:return
        if self.goal_y>body.y+.001:

            self.direction=incoming
        contact=self.wall_contact
        if contact is not None and contact['key']==hit[0] and contact['rect']==hit[1] and contact['side']==incoming:
            if contact['selected'] and abs(self.goal_y-body.y)>.001:

                self.direction=incoming
            return
        vertical=abs(self.goal_y-body.y)>.001
        self.wall_contact=dict(key=hit[0],rect=hit[1],side=incoming,at=now,
            until=now if vertical else now+self.wall_hold_s,selected=False,
            vertical_at_contact=vertical)
        self.next_patrol_at=None

def _center(player):
    value=player.get('center') if isinstance(player,dict) else getattr(player,'center',None)
    if callable(value):return tuple(value())
    if value is not None:return tuple(value)
    if isinstance(player,dict):
        p=player.get('pos',(player.get('xmp',0),player.get('ymp',0)))
        return p[0]+player.get('wmp',7680)/2,p[1]+player.get('hmp',17920)/2
    return player.xmp+player.wmp/2,player.ymp+player.hmp/2

def _alive(player):
    return bool(player.get('alive',True)) if isinstance(player,dict) else bool(getattr(player,'alive',True))

class ExplorationEnemies:
    def __init__(self,host):
        self.host=host;self.room=_Room(host)
        self.projectiles=ProjectileSystem(self.room,seed=host.rng.randrange(2**31),rules=_Rules())
        self.projectiles.random=host.rng
        self.units={};self.fx={};self.walls={};self.attachments={};self.drone_owners={};self.owned=set();self.retired=set()
        self._seen=set();self._seen_queue=deque();self._event_refs=deque()
        self.now=host.room_time;self.counts=Counter()

    def prepare(self):
        for oid in list(self.host.actors):self.add_actor(oid)
        for oid,state in self.units.items():
            if state['family']=='drone':self._bind_drone(oid,self._creator(oid))
        for oid,row in self.host.actors.items():
            if row['vid'] not in ('hulk.canisterstack1','hulk.canisterstack2','hulk.canisterstack3'):continue
            parent=(row['props'].get('move') or {}).get('relativeto',{}).get('objid')
            if parent in self.units:
                self.attachments.setdefault(parent,set()).add(oid)
                row['_enemy_attachment']=True;self.owned.add(oid)
        policy=profile('hulk.scavengergunman')
        self.host.record('exploration_enemy_policy',evidence='SIM',activation_px=policy['activation_px'],acquisition_px=policy['acquisition_px'])

    def _present(self,method,*args,**kwargs):
        presentation=getattr(self.host,'presentation',None)
        callback=getattr(presentation,method,None)
        if callback is not None:callback(*args,**kwargs)
        return callback is not None

    def _created_at_end(self,oid):
        callback=getattr(self.host,'created_at_end',None)
        if callback is not None:callback(oid)

    def _lifetime_due(self,oid,fallback):
        callback=getattr(self.host,'wire_lifetime_due',None)
        due=callback(oid) if callback is not None else None
        return self.now+1e-9>=fallback if due is None else due

    def _creator(self,oid):
        row=self.host.actors.get(oid,{})
        for key in ('creator','creatorid','parent','parent_id','_engineer_owner','_enemy_creator'):
            if isinstance(row.get(key),int):return row[key]
        instance=getattr(self.host,'instance',None)
        creators=getattr(instance,'recipe',{}).get('creators',{})
        return creators.get(oid,creators.get(str(oid)))

    def _bind_drone(self,child,parent):
        owner=self.units.get(parent);drone=self.units.get(child)
        if not owner or owner['family']!='engineer' or not drone or drone['family']!='drone':return False
        if child in self.drone_owners and self.drone_owners[child]!=parent:return False
        self.drone_owners[child]=parent;owner['drone']=child
        self.host.actors[child]['_engineer_owner']=parent
        return True

    def add_actor(self,oid):
        """Adopt ordinary bodies, drones or a mechanics-created mini once."""
        row=self.host.actors.get(oid)
        if not row or row.get('dead') or oid in self.retired:return False
        vid=row['vid']
        if vid not in FAMILIES:return False
        if oid in self.units:return True
        if row.get('hp') is not None and row['hp']<=0:return False
        row.pop('_mechanic',None);row['_enemy_ai']=True
        relative=(row['props'].get('move') or {}).get('relativeto',{}).get('objid')
        if relative is not None:row['_enemy_creator']=relative
        body=_Body(row);policy=profile(vid);family=policy['family']
        initial_pose=row['props'].get('pose',4)
        direction=-1 if row['props'].get('facing',0)==180 else 1
        if 'facing' not in row['props'] and initial_pose==4 and not row['props'].get('move'):

            direction=self.host.rng.choice((-1,1))
        body.facing=direction
        state=dict(body=body,motion=_GroundMotion(self.room,body),policy=policy,vid=vid,
            family=family,active=initial_pose==6,direction=direction,
            action=None,attack_started=0.,next_attack=self.now+.25,recover_until=0.,
            jump_after=0.,air_vx=0.,last_sync=-1.,last_mtc_position=None,pose=None,facing=None,
            summon_after=self.now+.8,drone=None,retreat=False,build_after=self.now+.4,
            build_until=0.,wall_attempted=False,mini_origin=self.now,next_trail=self.now+.8)
        state.update(charge_at=None,charge_until=0.,charge_direction=state['direction'])
        state.update(look_direction=state['direction'],launch_until=0.,launch_start=0.,initial_walk=initial_pose==5,jump_route=None)
        state.update(acquired=False,target_key=None,initial_run=initial_pose==6,climb_target=None,climb_after=0.)
        state.update(edge_target=None,edge_approach=None,edge_hop_route=None,edge_hop_after=0.,post_hop_walk=None)

        state['melee_heading_ready']=(family=='melee' and initial_pose==5
                                      and not row['props'].get('animprops'))
        body.motion=state['motion']
        if family not in ('drone','mini') and initial_pose in (4,5,6):

            state['motion'].step(body,0.,0.,self.now)
        if family=='melee':state['climber']=_Climber(oid,state)
        if family=='drone':state['flight']=_ExplorationFlight(body,policy)
        if family=='mini':
            if row.get('hp') is None:row['hp']=350
            row['props'].update(hitpoints=row['hp'],damage=40)
            self.host.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self.host._offset())
            self.host.batch.modify(oid,'DAMAGE',intval=40,offset=self.host._offset())
        self.units[oid]=state;self.owned.add(oid)
        if family=='drone':self._bind_drone(oid,self._creator(oid))

        row['props']['move']={'vel':{'xvel':0,'yvel':0}}
        self.host.batch.modify(oid,'MOVE',message=('Properties_Move',{'vel_':{'xvel_':0,'yvel_':0}}),offset=self.host._offset())
        self._present('body_created',oid)
        return True

    def _players(self):
        candidates=list(getattr(self.host,'players',{}).values())
        current=getattr(self.host,'player',None)
        if current is not None and not candidates:candidates=[current]
        return [p for p in candidates if p is not None and _alive(p)]

    def _target(self,point):
        players=self._players()
        return min(players,key=lambda p:math.hypot(_center(p)[0]-point[0],_center(p)[1]-point[1])) if players else None

    def _ground_target(self,state):
        """Nearest living candidate, retaining a valid acquired player.

        Native packets do not expose a target id or an angular FOV. The
        current distance/retention policy is SIM, and walls do not block
        perception (ordinary waller98 RUN across permanent walls10/86).
        A separate sight check gates starting a shot below.
        """
        candidates=list(getattr(self.host,'players',{}).items())
        if not candidates and getattr(self.host,'player',None) is not None:
            candidates=[('current',self.host.player)]
        point=state['body'].center()
        ranked=sorted((math.dist(_center(p),point),index,key,p)
            for index,(key,p) in enumerate(candidates) if p is not None and _alive(p))
        if not ranked:return None,math.inf,None
        for distance,index,key,player in ranked:
            if key==state['target_key'] and distance<=state['policy']['retention_px']*256:
                return player,distance,key
        distance,index,key,player=ranked[0]
        return player,distance,key

    def _visible(self,start,end,owner=None):
        ignored={oid for oid,w in self.walls.items() if w['owner']==owner}
        box=(min(start[0],end[0]),min(start[1],end[1]),abs(end[0]-start[0]),abs(end[1]-start[1]))
        return not any(kind==1 and key not in ignored and segment_hits_rect(start,end,rect)
                       for key,rect,kind in self.room.query(box))

    def _melee_heading_commit(self,state,target=None):
        motion=state['motion']
        if not motion.grounded or motion.jumping:return 'airborne'
        support=self.room.by_id.get(motion.support)
        if not support or support[0]!=motion.support_rect:return 'support-changed'
        if state['action'] is not None or self.now<state['recover_until']:return 'attack-recovery'
        if self.now<state['launch_until']:return 'pilot-launch'
        if motion.blocked or motion.edge_blocked:return 'contact-turn'
        climber=state.get('climber')
        if climber and (climber.climb_started is not None or climber.climb_hold_until>0.
                or climber.climb_dismount_until>0. or self.now<climber.wall_jump_until
                or climber.over_rect is not None):return 'wall-route'
        if state['jump_route'] is not None or state['post_hop_walk'] is not None:return 'jump-route'
        route=(motion.support,state['direction'])
        if state['edge_hop_route']==route:return 'same-ledge-hop'
        rule=state['policy']['edge_hop']
        if support and support[1]==2 and rule and state['edge_approach']==route:
            left,right=self.room.connected_span(motion.support_rect)
            foot=state['body'].center()[0]
            gap=right-foot if state['direction']>0 else foot-left
            if gap<=rule['approach_px']*256:return 'ledge-approach'
        if target is not None:
            b=state['body'];tx,ty=_center(target)
            height=target.get('hmp',17920) if isinstance(target,dict) else getattr(target,'hmp',17920)
            higher=b.y+b.h-ty-height/2
            jump=state['policy']['pursuit_jump']
            apex=jump_speed(state['vid'])**2/(2*motion.gravity)-jump['apex_clearance_px']*256

            if (jump['min_height_px']*256<higher<=apex
                    and abs(tx-b.center()[0])<jump['max_dx_px']*256):return 'higher-target-navigation'
        return None

    def _melee_ground_heading(self,oid,state,target,acquired,select=False):
        rule=state['policy'].get('ground_pursuit_heading')

        if rule is None or 'melee_heading_ready' not in state:return
        if acquired and not state['melee_heading_ready']:return
        if not acquired and not select and not state['melee_heading_ready']:return
        commit=self._melee_heading_commit(state,target if acquired else None)
        if commit is not None:
            if state['melee_heading_ready']:
                state['melee_heading_ready']=False
                self.host.record('enemy_ground_heading_preserved',id=oid,reason=commit,
                    acquired=acquired,evidence=rule['evidence'])
            return
        if not acquired:

            if select and not state['initial_run'] and not state['melee_heading_ready']:
                state['melee_heading_ready']=True
                self.host.record('enemy_ground_heading_rearmed',id=oid,
                    reason=rule['rearm'],evidence='SIM new leg after actual unacquired ground patrol')
            return
        if not select or target is None:return
        state['melee_heading_ready']=False
        dx=_center(target)[0]-state['body'].center()[0]
        if not dx:return
        previous=state['direction'];state['direction']=-1 if dx<0 else 1
        self.host.record('enemy_ground_heading',id=oid,vid=state['vid'],target=state['target_key'],
            previous=previous,direction=state['direction'],evidence=rule['evidence'])

    def _state(self,oid,name):
        row=self.host.actors.get(oid)
        if row is not None and row.get('_enemy_state')!=name:
            row['_enemy_state']=name
            self.host.record('enemy_state',id=oid,vid=row['vid'],state=name)

    def _sync(self,oid,state,dt,pose):
        row=self.host.actors.get(oid)
        if not row or row.get('dead'):return
        b=state['body'];motion=state['motion']
        row['pos']=[b.x,b.y];row['vel']=[motion.vx,motion.vy]
        action=state['action']
        direction=state['direction'] if state['family']=='drone' else action['direction'] if action else state['look_direction'] if self.now<state['recover_until'] or state['family']=='engineer' or state['retreat'] else state['direction']
        facing=0 if direction>0 else 180
        for key,value in (('POSE',pose),('FACING',facing)):
            lower=key.lower()
            if state[lower]!=value:
                self.host.batch.modify(oid,key,intval=value,offset=self.host._offset())
                state[lower]=value;row['props'][lower]=value
        if self.now-state['last_sync']>=.049:
            interval=dt if state['last_sync']<0 else self.now-state['last_sync']
            point=(round(b.x),round(b.y))
            if point!=state['last_mtc_position']:
                self.host.batch.move_to(oid,*point,dt=max(1,round(interval*1000)),offset=self.host._offset())
                state['last_mtc_position']=point
            state['last_sync']=self.now

    def _speed(self,state,running=False):
        family=state['family'];direction=state['direction']
        if family in ('loader','eloader'):return HEAVY_HORIZONTAL_SPEEDS['loader']['walk'][direction]
        if family=='melee':return HEAVY_HORIZONTAL_SPEEDS['shredder']['run' if running else 'walk'][direction]
        return GROUND_HORIZONTAL_SPEEDS['run' if running and family!='engineer' else 'walk'][direction]

    def _edge(self,state,direction,dt,speed):
        motion=state['motion'];b=state['body']
        if not motion.grounded or motion.support_rect is None:return False
        left,right=self.room.connected_span(motion.support_rect)

        foot=b.x+b.w/2
        gap=right-foot if direction>0 else foot-left
        return gap<=speed*dt+state['policy']['edge_margin_px']*256

    def _move_ground(self,oid,state,dt,vx,allow_edge=True):
        b=state['body'];motion=state['motion'];policy=state['policy']
        post_hop_vx=self._post_hop_walk_velocity(oid,state)
        if post_hop_vx is not None:vx=post_hop_vx
        if post_hop_vx is None and allow_edge and self._step_edge_hop(oid,state,dt,vx):return
        if post_hop_vx is None and motion.grounded and vx and allow_edge and self._edge(state,1 if vx>0 else -1,dt,abs(vx)):
            if policy['edge_mode']=='turn':
                vx=-vx;state['direction']=-state['direction']
                if state['family']=='engineer':state['look_direction']=-state['direction']
            else:vx=0.
        if not motion.grounded and motion.jumping:vx=state['air_vx']
        motion.step(b,dt,vx,self.now,avoid_edges=post_hop_vx is None and allow_edge and policy['edge_mode']=='turn')
        if post_hop_vx is not None:
            stage=state['post_hop_walk']
            if motion.blocked:self._end_post_hop_walk(oid,state,'side-blocked')
            elif motion.grounded and stage['phase']=='fall':self._end_post_hop_walk(oid,state,'landed')
            elif motion.grounded and motion.support!=stage['support']:
                self._end_post_hop_walk(oid,state,'support-changed')
            elif not motion.grounded and motion.passive and motion.walked_off:
                stage['phase']='fall'
        if motion.blocked:
            if self._begin_wall_climb(oid,state):
                self._sync(oid,state,dt,2);return
            state['direction']*=-1;state['retreat']=False
            if state['family']=='engineer':state['look_direction']=-state['direction']
        pose=4 if motion.grounded and not vx else (6 if abs(vx)>40000 else 5) if motion.grounded else 3 if motion.jumping or motion.vy<0 else 1
        if (motion.grounded and state.get('attack_ground_pose') is not None
                and (state['action'] is not None or self.now<state['recover_until'])):

            pose=state['attack_ground_pose']
        if state['family']=='waller' and state['action'] and state['action']['anim']==9 and motion.grounded:pose=7
        self._sync(oid,state,dt,pose)

    def _end_post_hop_walk(self,oid,state,reason):
        stage=state['post_hop_walk']
        if stage is None:return
        state['post_hop_walk']=None
        self.host.record('enemy_post_hop_walkoff_end',id=oid,support=stage['support'],reason=reason)

    def _post_hop_walk_velocity(self,oid,state):
        """One same-ledge landing continues WALK through a body-edge fall.

        This does not acquire a target or request a hop. D32/34 keep their
        ~20k left motion/POSE5 then1, including34's airborne grenade action.
        Attack/recovery timers still advance; their usual vx0 only yields to
        this already committed route. Other patrols/actions are unchanged.
        """
        stage=state['post_hop_walk']
        if stage is None:return None
        rule=state['policy']['edge_hop'];motion=state['motion']
        support=self.room.by_id.get(stage['support'])
        if (not rule or not rule.get('post_hop_walk_off') or
                state['direction']!=stage['direction'] or not support or
                support[1]!=2 or tuple(support[0])!=tuple(stage['rect'])):
            self._end_post_hop_walk(oid,state,'route-changed');return None
        if stage['phase']=='hop':
            if not motion.grounded:
                if not motion.jumping:self._end_post_hop_walk(oid,state,'hop-interrupted')
                return None
            if motion.support!=stage['support']:
                self._end_post_hop_walk(oid,state,'different-landing');return None
            stage['phase']='walk'
            self.host.record('enemy_post_hop_walkoff',id=oid,vid=state['vid'],support=stage['support'],
                direction=stage['direction'],speed_mp_s=self._speed(state),evidence=rule['post_hop_evidence'])
        elif motion.grounded and (stage['phase']=='fall' or motion.support!=stage['support']):
            self._end_post_hop_walk(oid,state,'landed' if stage['phase']=='fall' else 'support-changed');return None
        if not motion.grounded:
            if not (motion.passive and motion.walked_off):
                self._end_post_hop_walk(oid,state,'motion-interrupted');return None
            stage['phase']='fall'
        return stage['direction']*self._speed(state)

    def _step_edge_hop(self,oid,state,dt,vx):
        rule=state['policy']['edge_hop'];motion=state['motion'];b=state['body'];target=state['edge_target']
        if not rule or not state['active'] or not motion.grounded or not vx or target is None:return False
        support=self.room.by_id.get(motion.support)
        if not support or support[1]!=2:return False
        direction=1 if vx>0 else -1;route=(motion.support,direction)
        left,right=self.room.connected_span(motion.support_rect)
        gap=right-b.center()[0] if direction>0 else b.center()[0]-left
        if state['edge_approach'] is not None and state['edge_approach']!=route:
            state['edge_approach']=state['edge_hop_route']=None
        if gap>rule['approach_px']*256:state['edge_approach']=route
        height=target.get('hmp',17920) if isinstance(target,dict) else getattr(target,'hmp',17920)
        below=_center(target)[1]+height/2-(b.y+b.h)
        if (state['edge_approach']!=route or state['edge_hop_route']==route
                or self.now<state['edge_hop_after'] or below<rule['min_target_below_px']*256
                or math.dist(_center(target),b.center())>state['policy']['activation_px']*256):return False
        if gap>abs(vx)*dt:return False
        crossing=max(0.,(gap+rule['overhang_px']*256)/abs(vx))

        if crossing>=dt:
            motion.step(b,dt,vx,self.now,avoid_edges=False)
            self._sync(oid,state,dt,5 if abs(vx)<40000 else 6);return True
        hop_vx=0. if rule['mode']=='vertical' else direction*HEAVY_HORIZONTAL_SPEEDS['shredder']['run'][direction]
        velocity=rule['velocity_mp_s'];gravity=motion.gravity
        probe=SimpleNamespace(x=b.x+vx*crossing,y=b.y,w=b.w,h=b.h)
        landing=None
        for key,(x,y,w,h),kind in self.room.surfaces():
            discriminant=velocity*velocity-2*gravity*(b.y+b.h-y)
            if discriminant<0:continue
            seconds=(velocity+math.sqrt(discriminant))/gravity
            if seconds<=0:continue
            landing_x=probe.x+hop_vx*seconds
            if landing_x>=x+w or landing_x+b.w<=x:continue
            if rule['mode']=='forward' and not x<=landing_x+b.w/2<=x+w:continue
            if not self._jump_path_clear(probe,hop_vx,velocity,seconds,gravity):continue
            if landing is None or seconds<landing[0]:landing=(seconds,key)
        if landing is None:return False
        if crossing>0:motion.step(b,crossing,vx,self.now,avoid_edges=False)
        launch_position=[b.x,b.y]
        motion.jump(velocity,b);state['air_vx']=hop_vx
        motion.step(b,dt-crossing,hop_vx,self.now)
        state['edge_hop_route']=route;state['edge_hop_after']=self.now+rule['cooldown_s']
        if rule.get('post_hop_walk_off') and landing[1]==route[0]:
            state['post_hop_walk']=dict(support=route[0],rect=tuple(support[0]),direction=direction,phase='hop')
        self._state(oid,'edge-hop');self.counts['edge_hops']+=1
        self.host.record('enemy_edge_hop',id=oid,vid=state['vid'],support=motion.support or route[0],
            landing=landing[1],mode=rule['mode'],velocity_mp_s=velocity,air_vx_mp_s=hop_vx,
            launch_position_mp=launch_position,evidence=rule['evidence'])
        self._sync(oid,state,dt,3);return True

    def _jump_path_clear(self,b,vx,velocity,seconds,gravity):
        """Exact ballistic swept AABB against solids and earlier ledge landings."""
        apex=min(seconds,velocity/gravity)
        top=b.y-velocity*apex+.5*gravity*apex*apex
        end_x=b.x+vx*seconds
        box=(min(b.x,end_x),top,abs(end_x-b.x)+b.w,b.y+b.h-top)
        for key,rect,kind in self.room.query(box):
            x,y,w,h=rect
            if kind==2:

                discriminant=velocity*velocity-2*gravity*(b.y+b.h-y)
                if discriminant<0:continue
                when=(velocity+math.sqrt(discriminant))/gravity
                if not 0.<when<seconds-1e-6:continue
                left=b.x+vx*when
                if left<x+w and left+b.w>x:return False
                continue
            if vx:
                first=(x-b.x-b.w)/vx;last=(x+w-b.x)/vx
                low=max(0.,min(first,last));high=min(seconds,max(first,last))
            else:
                if b.x>=x+w or b.x+b.w<=x:continue
                low,high=0.,seconds
            if high<=low:continue
            def height(t):return b.y-velocity*t+.5*gravity*t*t
            minimum=height(max(low,min(high,velocity/gravity)))
            maximum=max(height(low),height(high))

            if maximum>y-b.h+1e-5 and minimum<y+h-1e-5:return False
        return True

    def _wall_action(self,oid,state,event):
        anim,duration=event['anim'],event['time_ms']
        token=(self.now,anim)
        if not self._present('attack',oid,state['vid'],anim,duration,token=token):
            self.host.batch.animprops(oid,anim,duration,offset=self.host._offset())
        self.host.record('enemy_wall_action',id=oid,vid=state['vid'],anim=anim,
            duration_ms=duration,evidence=state['policy']['wall_climb']['evidence'])

    def _begin_wall_climb(self,oid,state):
        climber=state.get('climber');target=state['climb_target']
        if not climber or target is None or not state['motion'].grounded or self.now<state['climb_after']:return False
        direction=state['direction']
        if climber._touching_wall(direction) is None:return False
        wall_top=Enemy._climb_wall_top(climber,direction)
        obstacle_height=climber.y+climber.h-wall_top if wall_top is not None else math.inf
        height=target.get('hmp',17920) if isinstance(target,dict) else getattr(target,'hmp',17920)
        higher=climber.y+climber.h-(_center(target)[1]+height/2)
        jump_height=jump_speed(state['vid'])**2/(2*state['motion'].gravity)

        if not (0<obstacle_height<=state['policy']['wall_climb']['short_obstacle_px']*256+512
                or higher>jump_height-24*256):return False
        climber.climb_side=direction;climber.climb_started=self.now
        climber.climb_start_y=climber.y;climber.facing=direction
        climber.climb_hold_until=climber.climb_dismount_until=climber.wall_jump_until=0.
        motion=state['motion'];motion.support=motion.support_rect=None
        motion.grounded=motion.jumping=False;motion.vx=motion.vy=0.
        self._state(oid,'wall-climb')
        self._wall_action(oid,state,dict(anim=8,time_ms=5346))
        self.counts['climbs']+=1
        self.host.record('enemy_wall_climb',id=oid,vid=state['vid'],side=direction,
            speed_mp_s=constants.SHREDDER_CLIMB_SPEED,
            evidence=state['policy']['wall_climb']['evidence'])
        return True

    def _step_wall_capability(self,oid,state,dt,target):
        climber=state.get('climber')
        if climber is None:return False
        attached=(climber.climb_started is not None or climber.climb_hold_until>0.
                  or climber.climb_dismount_until>0.)
        if not attached and self.now>=climber.wall_jump_until:return False
        player=SimpleNamespace(alive=True,center=lambda:_center(target)) if target is not None else None
        old_x,old_y=climber.x,climber.y;events=[];motion=state['motion']
        if climber.climb_dismount_until>0.:
            climber._step_wall_dismount(dt,player,self.now,events)
            if climber.climb_dismount_until==0.:state['climb_after']=self.now+1.5
        elif climber.climb_hold_until>0.:
            climber._step_wall_climb_hold(dt,player,self.now,events)
        elif climber.climb_started is not None:
            start_y=climber.climb_start_y
            wall=climber._climb_wall(climber.climb_side)
            wall_top=Enemy._climb_wall_top(climber,climber.climb_side)
            speed=constants.SHREDDER_CLIMB_SPEED
            top_dt=max(0.,(old_y+climber.h-wall_top)/speed) if wall_top is not None else math.inf
            ceiling_before_top=False
            for key,(x,y,w,h),kind in self.room.query((old_x,old_y-speed*dt,climber.w,speed*dt)):
                if (kind==1 and old_x<x+w and old_x+climber.w>x and old_y>=y+h
                        and (old_y-y-h)/speed<top_dt-1e-9):ceiling_before_top=True
            over=(wall is not None and top_dt<dt and not ceiling_before_top
                  and wall_top-climber.h>=start_y-constants.SHREDDER_CLIMB_MAX_HEIGHT)

            climber._step_wall_climb(top_dt if over else dt,player,self.now,events)
            if over and not climber.climb_dismount_until:
                self._start_wall_over(oid,state,climber,wall)
                motion._climb_kinematic=True
                try:motion.step(climber,dt-top_dt,climber.wall_jump_vx,self.now)
                finally:motion._climb_kinematic=False

            ceiling=None
            if not over and climber.y<old_y:
                query=(min(old_x,climber.x),climber.y,abs(climber.x-old_x)+climber.w,
                       old_y-climber.y)
                for key,(x,y,w,h),kind in self.room.query(query):
                    if (kind==1 and climber.x<x+w and climber.x+climber.w>x
                            and climber.y<y+h<=old_y):
                        ceiling=max(ceiling or -math.inf,y+h)
                if ceiling is not None:
                    climber.y=ceiling;climber._begin_wall_dismount(self.now,events)
            crossed_top=(wall_top is not None and climber.y+climber.h<=wall_top)
            if (not over and ceiling is None and (climber.climb_hold_until>0. or
                    (climber.climb_started is not None and crossed_top)) and start_y is not None
                    and start_y-climber.y<constants.SHREDDER_CLIMB_MAX_HEIGHT-1e-6):

                self._start_wall_over(oid,state,climber,wall)
        else:
            motion.step(climber,dt,climber.wall_jump_vx,self.now)
            climber.state='wall-jump';climber.pose=3 if motion.jumping or motion.vy<0 else 1
            if motion.grounded:climber.wall_jump_until=0.;climber.pose=4
        if climber.over_rect is not None:
            x,y,w,h=climber.over_rect;cx=climber.x+climber.w/2
            if x<=cx<=x+w and climber.y+climber.h<=y:

                vy=motion.vy;Enemy._detach_from_wall(climber,self.now,player,events)
                motion.vy=vy;climber.over_rect=None
                self.host.record('enemy_wall_over_complete',id=oid,vid=state['vid'],
                    direction=climber.facing,evidence='D over-top phase; center-support/release target rule FIT/SIM')
        for event in events:self._wall_action(oid,state,event)
        if attached and (climber.climb_started is not None or climber.climb_hold_until>0.
                         or climber.climb_dismount_until>0.):
            motion.vx=(climber.x-old_x)/dt if dt else 0.
            motion.vy=(climber.y-old_y)/dt if dt else 0.
        state['direction']=state['look_direction']=climber.facing
        state['air_vx']=climber.wall_jump_vx
        state['jump_after']=max(state['jump_after'],climber.next_jump)
        self._state(oid,climber.state);self._sync(oid,state,dt,climber.pose)
        return True

    def _start_wall_over(self,oid,state,climber,wall):
        climber.climb_started=climber.climb_start_y=climber.climb_route=None
        climber.climb_hold_until=0.;climber.motion.jump(constants.SHREDDER_CLIMB_SPEED)
        climber.wall_jump_vx=climber.climb_side*climber._travel_speed(climber.climb_side)
        climber.wall_jump_until=self.now+1.2
        climber.next_jump=self.now+1.5;climber.state='wall-over';climber.pose=3
        climber.over_rect=wall.rect() if wall is not None else None
        self.host.batch.modify(oid,'ANIMPROPS',message=('AnimationProperties',{}),offset=self.host._offset())
        self.host.actors[oid]['props'].pop('animprops',None)
        self.host.record('enemy_wall_over',id=oid,vid=state['vid'],
            vertical_speed_mp_s=-constants.SHREDDER_CLIMB_SPEED,evidence='D short-obstacle exit; phase timing FIT')

    def _pursuit_jump(self,oid,state,target,vx,acquired):
        rule=state['policy']['pursuit_jump'];motion=state['motion'];b=state['body']
        if not rule or not acquired or target is None or not motion.grounded or not vx:return False
        tx,ty=_center(target)
        player_h=target.get('hmp',17920) if isinstance(target,dict) else getattr(target,'hmp',17920)
        feet=ty+player_h/2;higher=b.y+b.h-feet;dx=tx-b.center()[0]
        if higher<=rule['min_height_px']*256 or abs(dx)>=rule['max_dx_px']*256:
            state['jump_route']=None;return False

        if vx*dx<=0 or self.now<state['jump_after']:return False
        velocity=jump_speed(state['vid']);gravity=motion.gravity
        if higher>velocity*velocity/(2*gravity)-rule['apex_clearance_px']*256:return False
        tolerance=rule['target_support_tolerance_px']*256
        candidates=self.room.query((tx-1,feet-tolerance,2,2*tolerance))
        for target_key,rect,kind in candidates:
            x,y,w,h=rect
            if not x<=tx<=x+w or abs(y-feet)>tolerance:continue
            climb=b.y+b.h-y
            if climb<=rule['min_height_px']*256:continue
            discriminant=velocity*velocity-2*gravity*climb
            if discriminant<0:continue
            seconds=(velocity+math.sqrt(discriminant))/gravity
            landing_x=b.x+vx*seconds
            if landing_x>=x+w or landing_x+b.w<=x:continue
            route=(motion.support,target_key)

            if state['jump_route']==route:continue
            if not self._jump_path_clear(b,vx,velocity,seconds,gravity):continue
            state['jump_route']=route;state['jump_after']=self.now+constants.SHREDDER_PURSUIT_JUMP_INTERVAL
            motion.jump(velocity,b);state['air_vx']=vx
            self._state(oid,'pursuit-jump')
            self.host.record('enemy_pursuit_jump',id=oid,vid=state['vid'],target_surface=target_key,
                velocity_mp_s=velocity,cooldown_s=constants.SHREDDER_PURSUIT_JUMP_INTERVAL,evidence=rule['evidence'])
            return True
        return False

    def _actions(self,state):
        family=state['family']
        if family=='loader':return copy.deepcopy(LOADER_THROWS)
        if family=='waller':
            actions=copy.deepcopy(WALLER_LOBS)
            energy=state['vid'].endswith('energy')
            for action in actions:
                action['emit']='hulk.cannonshotenergy' if energy else 'hulk.cannonshot'
                if energy:
                    for point in action['points']:point['x']=34
            return actions
        actions=copy.deepcopy(BEHAVIOUR.get(state['vid'],{}).get('attacks',[]))
        crouch=OBSERVED_CROUCH_ATTACKS.get(state['vid'])
        if crouch is not None:actions.append(copy.deepcopy(crouch))
        return actions

    def _begin_attack(self,oid,state,target):
        actions=[a for a in self._actions(state)
                 if a.get('ground_pose') is None or state['motion'].grounded]
        if not actions:return False
        b=state['body'];tx,ty=_center(target);cx,cy=b.center()
        attack_direction=state['direction'] if state['family']=='drone' else 1 if tx>=cx else -1
        state['look_direction']=attack_direction
        if state['family']=='loader' and math.hypot(tx-cx,ty-cy)<=120*256:
            actions=copy.deepcopy(BEHAVIOUR.get(state['vid'],{}).get('attacks',actions))

        if state['family'] not in ('loader','waller','eloader','melee','engineer','drone'):
            desired=math.degrees(math.atan2(ty-cy,abs(tx-cx)))%360
            def error(a):
                point=(a.get('points') or [{}])[0]
                angle=point.get('angle',0)
                return abs((angle-desired+180)%360-180)
            best=min(error(a) for a in actions)
            actions=[a for a in actions if error(a)<=best+12]
        weights=[max(1,a.get('weight',1)) for a in actions]
        pick=self.host.rng.uniform(0,sum(weights));action=actions[-1]
        for candidate,weight in zip(actions,weights):
            pick-=weight
            if pick<=0:action=candidate;break
        action['pending']=list(enumerate(action.get('points',[])))
        action['start']=self.now;action['direction']=attack_direction
        state['action']=action;state['attack_started']=self.now
        duration=action['time_ms']/1000.;recovery=action.get('recovery',.4)
        cooldown=BEHAVIOUR.get(state['vid'],{}).get('rule',{}).get('seconds',duration+recovery)
        if state['family']=='loader':cooldown=state['policy']['loader_throw_cooldown_s']
        state['next_attack']=self.now+max(duration+recovery,cooldown)
        state['recover_until']=self.now+duration+recovery
        state['attack_ground_pose']=action.get('ground_pose')
        if state['attack_ground_pose'] is not None:

            pose=state['attack_ground_pose']
            if state['pose']!=pose:
                self.host.batch.modify(oid,'POSE',intval=pose,offset=self.host._offset())
                state['pose']=pose;self.host.actors[oid]['props']['pose']=pose
        if action.get('charge'):
            state['charge_at']=self.now+duration+state['policy']['charge_settle_s']
            state['charge_until']=state['charge_at']+state['policy']['charge_max_s']
            state['charge_direction']=attack_direction
        if not self._present('attack',oid,state['vid'],action['anim'],action['time_ms'],token=action['start']):
            self.host.batch.animprops(oid,action['anim'],action['time_ms'],offset=self.host._offset())
        self.host.record('enemy_attack',id=oid,vid=state['vid'],anim=action['anim'],duration_ms=action['time_ms'],
                         **({'presentation_evidence':action['evidence']} if action.get('evidence') else {}))
        self.counts['attacks']+=1
        return True

    def _advance_attack(self,oid,state,target):
        action=state['action']
        if action is None:return False
        pending=[]
        for index,point in action['pending']:
            if self.now+1e-9>=action['start']+point.get('timeMs',point.get('time',0))/1000.:
                if action.get('emit'):self._emit(oid,state,action,point,target)
            else:pending.append((index,point))
        action['pending']=pending
        if self.now+1e-9>=action['start']+action['time_ms']/1000.:
            state['action']=None;return False
        return True

    def _emit(self,oid,state,action,point,target):
        b=state['body'];direction=state['direction'] if state['family']=='drone' else action['direction']
        origin=(b.x+b.w/2+direction*point.get('x',0)*128,
                b.y+b.h-128-point.get('y',0)*128)
        angle=point.get('angle',0)
        if direction<0:angle=(180-angle)%360
        vid=action['emit']
        if vid=='hulk.seekercanister':
            diagonal=action['anim']==8
            velocity=(direction*(21213 if diagonal else 30000)*1000/256.,
                      -21213*1000/256. if diagonal else 0.)
            self.throw_canister(oid,origin,velocity,state['policy']['canister_lifetime_s'])
            return
        name=('dronebomb' if state['vid']=='hulk.bdrone' and vid=='hulk.cannonshot'
              else vid.split('.')[-1])
        spec=self.projectiles.rules.ENEMY_SHOTS.get(name,{})
        if not spec:return
        if name=='sshot':angle+=self.host.rng.uniform(-4.,4.)
        p=self.projectiles.fire_enemy_shot(oid,name,origin,angle,spec.get('damage',35),spec.get('speed',78000),
            homing=spec.get('homing',False),turn_rate=spec.get('turn_rate',90),kind=spec.get('kind','straight'),
            gravity=spec.get('gravity',0),accel=spec.get('accel',0),max_speed=spec.get('max_speed',0))
        if name.startswith('cannonshot'):

            p.vy=-40000*1000/256.
            dx=(_center(target)[0]-origin[0]) if target is not None else direction*300*256
            p.vx=max(-60000.,min(60000.,dx/1.8))
        elif name=='flame' and state['vid']=='hulk.eloader':
            p.set_velocity_command(loader_flame_move(action['anim'],direction))
        self._attach_projectile(p)

    def _template(self,vid):
        props=copy.deepcopy(TEMPLATES.get(vid,{}))
        try:props.update(self.host.pack.props(vid))
        except KeyError:pass
        props['vid']=vid
        return props

    def _attach_projectile(self,p):
        props=self._template(p.vid)
        props.update(wmp=p.w,hmp=p.h,damage=p.damage,ownertype=p.owner_type,boundsworld=False,
                     move={'vel':{'xvel':0,'yvel':0}})
        if p.lifetime>0:props['lifetime']=round(p.lifetime*1000)
        p.remove_type=props.get('removetype',0)
        oid=self.host._create(p.vid,[p.x,p.y],p.owner,props);p.objid=oid
        self.host.actors[oid]['_enemy_projectile']=True;self.owned.add(oid)
        self._present('projectile_created',oid,p.vid,projectile=p)
        self.counts['projectiles']+=1
        return oid

    def throw_canister(self,owner,origin,velocity=None,lifetime=None):
        'Shared live capability, also useful to focused physics fixtures.'
        velocity=velocity or (30000*1000/256.,0.)
        lifetime=profile('hulk.scavengerloader')['canister_lifetime_s'] if lifetime is None else lifetime
        p=self.projectiles.spawn(vid='hulk.seekercanister',owner=owner,
            x=origin[0]-8192,y=origin[1]-12288,vx=velocity[0],vy=velocity[1],width=16384,height=24576,
            damage=35,kind='falling',gravity=30*1000000/256.,lifetime=lifetime,rotate=False)
        oid=self._attach_projectile(p)
        self.host.record('loader_canister',id=oid,parent=owner,velocity=list(velocity),fuse_s=lifetime,fuse_evidence='SIM/USER-timeout')
        return oid

    def _create_fx(self,vid,pos,creator,changes=None):
        props=self._template(vid);props.update(changes or {})
        oid=self.host._create(vid,pos,creator,props)
        self.host.actors[oid]['_enemy_fx']=True;self.owned.add(oid)
        self.fx[oid]=self.now+props.get('lifetime',500)/1000.
        self._present('effect_created',oid,vid)
        self._created_at_end(oid)
        deadline=getattr(self.host,'wire_lifetime_deadline',lambda _oid:None)(oid)
        if deadline is not None:self.fx[oid]=deadline/1000.
        return oid

    def _finish_projectile(self,p,reason=None,native=False):
        if p.objid in self.retired:return
        if reason is not None:p.death_reason=reason.upper() if isinstance(reason,str) else reason
        p.alive=False;reason=p.death_reason
        code=reason if isinstance(reason,int) else DESTROY_REASON.get(reason,1)
        if p.vid=='hulk.seekercanister' and code in (5,6):
            props=self._template('hulk.miniseeker');props.update(hitpoints=350,damage=40)
            oid=self.host._create('hulk.miniseeker',[p.x,p.y+p.h-16384],p.objid,props)
            self.add_actor(oid);self.counts['canister_splits']+=1
            self._present('canister_opened',oid)
            self._created_at_end(oid)
            self.host.record('canister_open',id=p.objid,child=oid,reason=reason)
        else:
            burst=p.burst_effect()
            if burst=='hulk.napalm':
                drone=p.vid=='hulk.dronebomb'
                policy=profile('hulk.bdrone') if drone else None
                count=(self.host.rng.choice(policy['drone_fire_counts']) if drone else self.host.rng.randint(10,15))
                for index in range(count):
                    if drone:
                        vx=-policy['drone_fire_spread_mp_s']+2*policy['drone_fire_spread_mp_s']*index/(count-1)
                    q=self.projectiles.spawn(vid='hulk.napalm',owner=p.objid,x=p.x,y=p.y,
                        vx=vx if drone else self.host.rng.uniform(-58000,58000),
                        vy=0. if drone else self.host.rng.uniform(-50000,10000),
                        width=6144,height=6144,damage=25,kind='napalm',gravity=340000,lifetime=3.)
                    if drone:
                        q.x=p.x+p.w/2-q.w/2;q.y=p.y+p.h/2-q.h/2
                        q.rotate=False
                    self._created_at_end(self._attach_projectile(q))
            elif burst:
                props=self._template(burst)

                origin_center=p.vid=='hulk.dronebomb' and burst=='hulk.cannonshotx'
                self._create_fx(burst,[p.x+(0 if origin_center else p.w/2)-props.get('wmp',p.w)/2,
                                       p.y+(0 if origin_center else p.h/2)-props.get('hmp',p.h)/2],p.objid)
        self._retire(p.objid,reason,native)

    def _retire(self,oid,reason='INTERNAL',native=False):
        if oid in self.retired:return
        state=self.units.get(oid)
        if state and state['family']=='engineer':
            for child,parent in list(self.drone_owners.items()):
                if parent==oid:self._retire(child,'INTERNAL')
        parent=self.drone_owners.pop(oid,None)
        if parent in self.units:
            owner=self.units[parent]
            if owner['drone']==oid:owner['drone']=None
            owner['summon_after']=self.now+owner['policy']['engineer_resummon_s']
        for child in self.attachments.pop(oid,set()):self._retire(child,'INTERNAL')
        self.retired.add(oid);self.units.pop(oid,None);self.fx.pop(oid,None);self.walls.pop(oid,None)
        self.host._retire(oid,reason,native=native)

    def _build_wall(self,oid,state):
        policy=state['policy'];b=state['body']
        existing=[key for key,w in self.walls.items() if w['owner']==oid]
        if len(existing)>=policy['wall_per_owner'] or len(self.walls)>=policy['wall_total_cap']:return False
        props=self._template('hulk.wallsegment')
        props.update(hitpoints=policy['wall_hp'],lifetime=round(policy['wall_lifetime_s']*1000))
        cx=b.x+b.w/2+state['look_direction']*policy['wall_offset_px']*256
        pos=[cx-props['wmp']/2,b.y+b.h-props['hmp']]
        rect=tuple(pos)+(props['wmp'],props['hmp'])
        if any(kind==1 and rects_overlap(rect,other) for _,other,kind in self.room.surfaces()):return False
        wid=self.host._create('hulk.wallsegment',pos,oid,props)
        self.host.actors[wid]['_enemy_wall']=True;self.owned.add(wid)
        self.walls[wid]=dict(owner=oid,expire=self.now+policy['wall_lifetime_s'])
        self.host.batch.animprops(wid,2,528,offset=self.host._offset())
        state['build_after']=self.now+policy['wall_build_cooldown_s'];state['build_until']=self.now+policy['wall_build_s']
        self.room.refresh();self.counts['walls']+=1
        self.host.record('waller_wall',id=wid,parent=oid,policy='USER/SIM',vid='hulk.wallsegment')
        return True

    def _summon(self,oid,state):
        bound=[child for child,parent in self.drone_owners.items() if parent==oid
               and child in self.host.actors and not self.host.actors[child].get('dead')]
        if bound:
            state['drone']=bound[0];return
        child=state['drone']
        if child is not None:
            row=self.host.actors.get(child)
            if row and not row.get('dead'):return
            state['drone']=None;state['summon_after']=self.now+state['policy']['engineer_resummon_s']
        if self.now<state['summon_after']:return
        vid=self.host.rng.choice(('hulk.sdrone','hulk.bdrone'));b=state['body']

        props=self._template(vid)

        props.update(facing=180 if state['look_direction']<0 else 0,pose=1)
        child=self.host._create(vid,[b.x,b.y],oid,props)
        self.add_actor(child);self._bind_drone(child,oid)
        self.host.record('engineer_drone',id=child,parent=oid,vid=vid)

    def _step_flying(self,oid,state,dt,target):
        b=state['body'];policy=state['policy'];row=self.host.actors[oid]
        if state['family']=='drone':

            player=None
            if target is not None:
                point=_center(target)
                height=target.get('hmp',17920) if isinstance(target,dict) else getattr(target,'hmp',17920)
                player=SimpleNamespace(alive=True,ymp=point[1]-height/2,center=lambda:point)
            flight=state['flight'];old=(b.x,b.y);replans=flight.wall_replans
            flight.step(b,dt,player,self.now)
            if flight.wall_replans!=replans:
                self.host.record('aircraft_wall_replan',id=oid,**flight.last_wall_decision,
                    evidence='D side-contact/upward windows; height admission/target/hold FIT/SIM')
            state['active']=flight.active;state['direction']=flight.direction
            state['motion'].vx=(b.x-old[0])/dt if dt else 0.
            state['motion'].vy=(b.y-old[1])/dt if dt else 0.
            if target is not None:
                if (flight.active and self.now>=flight.ready_at
                        and self.now>=state['next_attack'] and state['action'] is None):
                    self._begin_attack(oid,state,target)
            self._advance_attack(oid,state,target)
            self._state(oid,'drone-attack' if state['action'] else b.state)
            self._sync(oid,state,dt,b.pose)
            return
        if target is None:
            state['motion'].vx=state['motion'].vy=0.
            self._state(oid,'active-patrol' if state['active'] else 'dormant');self._sync(oid,state,dt,2);return
        tx,ty=_center(target);cx,cy=b.center();dx,dy=tx-cx,ty-cy;distance=math.hypot(dx,dy)
        if state['family']=='mini':
            if not state['active'] and distance<=policy['mini_activation_px']*256:
                state['active']=True;state['mini_origin']=self.now
            speed=0.
            if state['active']:
                cycle=(self.now-state['mini_origin'])%policy['mini_period_s']
                speed=0 if cycle<policy['mini_pause_s'] else policy['mini_fast_px_s']*256 if cycle<policy['mini_pause_s']+policy['mini_dash_s'] else policy['mini_slow_px_s']*256
            vx,vy=(speed*dx/distance,speed*dy/distance) if distance else (0.,0.)
            self._state(oid,'mini-tracking' if state['active'] else 'dormant')
            if state['active'] and self.now>=state['next_trail']:
                state['next_trail']=self.now+policy['mini_trail_s']
                self._create_fx('hulk.minispawn',[b.x,b.y],oid)
        start=b.rect();end=(b.x+vx*dt,b.y+vy*dt,b.w,b.h)
        hits=[]
        for _,rect,kind in self.room.surfaces():
            if kind!=1:continue
            expanded=(rect[0]-b.w/2,rect[1]-b.h/2,rect[2]+b.w,rect[3]+b.h)
            hit=segment_hit(b.center(),(end[0]+b.w/2,end[1]+b.h/2),expanded)
            if hit is not None:hits.append(hit[0])
        fraction=max(0.,min(hits)-1e-5) if hits else 1.
        b.x+=(end[0]-b.x)*fraction;b.y+=(end[1]-b.y)*fraction
        if hits:state['direction']*=-1
        state['motion'].vx=vx if not hits else 0.;state['motion'].vy=vy if not hits else 0.
        if dx:state['direction']=1 if dx>0 else -1
        self._sync(oid,state,dt,2)

    def _step_unit(self,oid,state,dt):
        b=state['body'];policy=state['policy']
        if state['family'] in ('mini','drone'):
            self._step_flying(oid,state,dt,self._target(b.center()));return
        target,distance,target_key=self._ground_target(state)
        motion=state['motion'];visible=False;state['climb_target']=None;state['edge_target']=target
        if target is not None:
            tx,ty=_center(target);cx,cy=b.center();dx,dy=tx-cx,ty-cy

            if (distance<=policy['attack_px']*256 or
                    (policy['activation_requires_los'] and distance<=policy['activation_px']*256)):
                visible=self._visible((cx,cy),(tx,ty),oid)
            if not state['active'] and distance<=policy['activation_px']*256 and (visible or not policy['activation_requires_los']):
                state['active']=True;self.counts['activations']+=1
                if state['family']=='engineer':
                    state['direction']=-1 if dx>=0 else 1
                    state['look_direction']=-state['direction']
                self.host.record('enemy_activated',id=oid,vid=state['vid'],range_px=policy['activation_px'],evidence='SIM')
        else:dx=dy=0.
        keeping=(state['acquired'] and target_key==state['target_key'])
        acquired=target is not None and distance<=policy['retention_px' if keeping else 'acquisition_px']*256
        if acquired!=state['acquired']:
            state['acquired']=acquired
            self.host.record('enemy_acquired' if acquired else 'enemy_target_lost',id=oid,
                vid=state['vid'],target=target_key,range_px=policy['acquisition_px'],
                retention_px=policy['retention_px'],evidence='SIM distance/retention gate; FOV unknown')
        state['target_key']=target_key if acquired else None
        if acquired:state['initial_run']=False
        self._melee_ground_heading(oid,state,target,acquired)
        if self.now<state['launch_until']:

            age=self.now-state['launch_start'];boost=age<=.22
            if boost:motion.vy=-59000.-.5*motion.gravity*dt
            motion.step(b,dt,0. if boost else state['launch_vx'],self.now)
            self._state(oid,'launch');self._sync(oid,state,dt,3);return
        if self._step_wall_capability(oid,state,dt,target):return
        if not state['active']:

            if state['initial_walk']:
                self._state(oid,'pre-acquisition-patrol')
                self._move_ground(oid,state,dt,state['direction']*self._speed(state));return
            self._state(oid,'dormant')
            support=self.room.by_id.get(motion.support)
            if motion.grounded and support and support[0]==motion.support_rect:

                self._sync(oid,state,dt,4)
            else:self._move_ground(oid,state,dt,0.)
            return
        if state['family']=='engineer':self._summon(oid,state)
        busy=self._advance_attack(oid,state,target)
        if busy or self.now<state['recover_until']:
            self._state(oid,'attack' if busy else 'recover');self._move_ground(oid,state,dt,0.,False);return
        if state['charge_at'] is not None:
            direction=state['charge_direction'];speed=HEAVY_HORIZONTAL_SPEEDS['loader']['run'][direction]
            if self.now<state['charge_at']:
                self._state(oid,'charge-prepare');self._move_ground(oid,state,dt,0.,False);return
            if (self.now>=state['charge_until'] or motion.blocked or motion.edge_blocked or
                    self._edge(state,direction,dt,speed) or (target is not None and direction*dx<0)):
                state['charge_at']=None
            else:
                state['direction']=direction
                self._state(oid,'charge');self._move_ground(oid,state,dt,direction*speed,False);return
        if not acquired:state['jump_route']=None
        if state['family']=='waller' and acquired and visible and distance<=policy['attack_px']*256:
            if distance<policy['wall_retreat_px']*256 and not state['retreat']:
                state['retreat']=True;state['direction']=-1 if dx>=0 else 1
                state['look_direction']=-state['direction']
            if distance>=policy['wall_clear_px']*256:state['retreat']=False
            if state['retreat'] and not motion.edge_blocked and not motion.blocked:
                self._state(oid,'retreat');self._move_ground(oid,state,dt,state['direction']*self._speed(state));return
            if not state['wall_attempted'] and self.now<state['build_after']:
                self._state(oid,'build-prepare');self._move_ground(oid,state,dt,0.,False);return
            if self.now>=state['build_after'] and motion.grounded:
                state['wall_attempted']=True
                if not self._build_wall(oid,state):state['build_after']=self.now+1.
            if self.now<state['build_until']:
                self._state(oid,'building');self._move_ground(oid,state,dt,0.,False);return
        attack_range=policy['attack_px']*256
        in_reach=acquired and distance<=attack_range and (visible or not policy['attack_requires_los'])
        if state['family'] in ('melee','engineer'):in_reach=in_reach and abs(dy)<b.h
        if in_reach and self.now>=state['next_attack'] and (motion.grounded or state['family']=='ranged'):
            if self._begin_attack(oid,state,target):
                self._state(oid,'attack');self._move_ground(oid,state,dt,0.,False);return
        if state['family']=='engineer' and acquired:
            state['look_direction']=-state['direction']
            vx=state['direction']*self._speed(state)
            self._state(oid,'retreat')
        else:

            self._melee_ground_heading(oid,state,target,acquired,select=True)
            vx=state['direction']*self._speed(state,running=acquired or state['initial_run'])
            self._state(oid,'pursuit' if acquired else 'active-patrol')
        if state.get('climber') and acquired and not motion.jumping:
            state['climb_target']=target
        self._pursuit_jump(oid,state,target,vx,acquired)
        self._move_ground(oid,state,dt,vx)

    def step(self,dt):
        self.now=self.host.room_time
        self.room.refresh()
        for oid,state in list(self.units.items()):
            if oid not in self.units:continue
            row=self.host.actors.get(oid)
            if not row or row.get('dead'):
                self._retire(oid);continue
            self._step_unit(oid,state,dt)
        for parent,children in self.attachments.items():
            source=self.host.actors.get(parent)
            if source is None:continue
            for child in children:
                row=self.host.actors.get(child)
                if row is not None:
                    relative=row['props']['move'].get('to',{})
                    row['pos']=[source['pos'][0]+relative.get('xmp',0),source['pos'][1]+relative.get('ymp',0)]
        events=[]
        def steering(p,default):
            player=self._target((p.x+p.w/2,p.y+p.h/2))
            return _center(player) if player is not None else None
        clock=getattr(self.host,'wire_lifetime_due',None)
        for p in self.projectiles.projectiles:

            if (p.alive and not p.prepared and p.lifetime>0 and clock is not None and
                    clock(p.objid) is False and p.age+dt>=p.lifetime):
                p.age=min(p.age,p.lifetime-dt-1e-9)
        self.projectiles.step(dt,None,events,resolve_player=False,target_for=steering)
        for p in self.projectiles.projectiles:
            due=clock(p.objid) if clock is not None else None
            if (p.alive and not p.prepared and p.lifetime>0 and
                    (due is True or (due is None and p.age+1e-9>=p.lifetime))):
                p.age=max(p.age,p.lifetime);p.alive=False;p.death_reason='LIFETIME'
        for event in events:
            if event['kind']=='projectile-pulse':
                p=event['projectile'];pulse=self._create_fx('hulk.grenadepulse',[p.x,p.y],p.objid)
                self._present('grenade_pulse',pulse)
            elif event['kind']=='napalm-landed':
                p=event['projectile'];self._present('napalm_landed',p.objid,position=(p.x,p.y))
        for p in list(self.projectiles.projectiles):
            if p.objid in self.retired:continue
            if not p.alive:self._finish_projectile(p);continue
            row=self.host.actors.get(p.objid)
            if row and not row.get('dead'):
                row['pos']=[p.x,p.y];row['vel']=[p.vx,p.vy]
                self.host.batch.move_to(p.objid,round(p.x),round(p.y),dt=max(1,round(dt*1000)),offset=self.host._offset())
                if p.homing:
                    pose=p.flight_pose();facing=180 if p.vx<0 else 0
                    self.host.batch.modify(p.objid,'POSE',intval=pose,offset=self.host._offset())
                    self.host.batch.modify(p.objid,'FACING',intval=facing,offset=self.host._offset())
                    row['props'].update(pose=pose,facing=facing)
        for oid,end in list(self.fx.items()):
            if self._lifetime_due(oid,end):self._retire(oid,'LIFETIME')
        for oid,wall in list(self.walls.items()):
            if self._lifetime_due(oid,wall['expire']) or wall['owner'] in self.retired:self._retire(oid,'LIFETIME')
        self.projectiles.projectiles[:]=[p for p in self.projectiles.projectiles if p.objid not in self.retired]

    def _duplicate(self,event):
        token=next(((key,event[key]) for key in ('event_id','eventId','seq','native_t','source_t_ms') if key in event),None)
        if token is not None:
            key=('native',str(token),event.get('kind'),event.get('victim',event.get('objid')),event.get('value'),event.get('reason'))
        else:

            key=('object',id(event));self._event_refs.append(event)
            if len(self._event_refs)>4096:self._event_refs.popleft()
        if key in self._seen:return True
        self._seen.add(key);self._seen_queue.append(key)
        if len(self._seen_queue)>4096:self._seen.discard(self._seen_queue.popleft())
        return False

    def feedback(self,event):
        if not isinstance(event,dict) or event.get('kind') not in ('damage','destroy'):return False
        if 'ownerid' in event and int(event['ownerid'] or 0)!=self.host.worldid:return False
        oid=int(event.get('victim',event.get('objid',event.get('id',0))) or 0)
        if oid not in self.owned:return False
        if oid in self.retired or self._duplicate(event):return True
        row=self.host.actors.get(oid)
        if not row or row.get('dead'):self.retired.add(oid);return True
        kind=event['kind']
        p=next((p for p in self.projectiles.projectiles if p.objid==oid),None)
        reason=event.get('reason','HITPOINTS')
        if kind=='damage':
            value=int(event.get('value',event.get('val',0)) or 0)
            if value<=0:return True
            if row.get('hp') is None:return True
            row['hp']=max(0,row['hp']-value);row['props']['hitpoints']=row['hp']
            self.host.batch.modify(oid,'HITPOINTS',intval=row['hp'],offset=self.host._offset())
            if row['hp']>0:return True
            reason='HITPOINTS'
        if p is not None:
            self._finish_projectile(p,reason,native=kind=='destroy');return True
        code=reason if isinstance(reason,int) else DESTROY_REASON.get(str(reason).upper(),1)
        if code==2 and oid in self.units:
            state=self.units[oid]
            if state['family'] in ('loader','eloader'):self._spawn_survivor(oid,state)
            supplies=getattr(self.host,'supplies',None)
            if supplies is not None:
                pos=row['pos'];props=row['props'];supplies.killed(pos[0]+props.get('wmp',0)/2,pos[1]+props.get('hmp',0)/2)
        self._retire(oid,reason,native=kind=='destroy')
        return True

    def _spawn_survivor(self,oid,state):
        """D ordinary lines35002/92418: one 1000HP pilot on HP death.

        Loader's normal pilot is directly observed; eloader's normal default
        and both eject timings reuse the existing Boss capability as SIM.
        removal/expiration and damage+destroy duplicate feedback never eject.
        """
        vid=state['policy']['survivor_vid']
        b=state['body'];direction=state['look_direction']
        props=self._template(vid);props.update(hitpoints=1000,damage=40,pose=3,facing=180 if direction<0 else 0)
        child=self.host._create(vid,[b.x+b.w/2-6400,b.y],oid,props)
        self.add_actor(child);pilot=self.units[child]
        pilot.update(active=True,direction=direction,look_direction=direction,
            launch_start=self.now,launch_until=self.now+.8,
            launch_vx=direction*HEAVY_HORIZONTAL_SPEEDS['shredder']['run'][direction])
        pilot['motion'].jump(59000,pilot['body'])
        self.counts['loader_survivors']+=1
        self.host.record('loader_survivor',id=child,parent=oid,vid=vid,hp=1000,
                         evidence=state['policy']['survivor_evidence']+'; eject timing SIM reused from Boss')

    def status(self):
        return dict(alive=len(self.units),projectiles=len(self.projectiles.live()),walls=len(self.walls),
            attachments=sum(len(v) for v in self.attachments.values()),
            states=dict(Counter(self.host.actors[oid].get('_enemy_state','initial') for oid in self.units if oid in self.host.actors)),
            counts=dict(self.counts),policyEvidence='SIM; native fields/actions retained separately',
            stationaryCanisterOwner='mechanics; spawned minis adopted through add_actor')
