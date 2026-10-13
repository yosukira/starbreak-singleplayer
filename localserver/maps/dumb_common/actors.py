"""Shared native actors, shots and physics; independent map controllers."""
import copy
import heapq
import math
from . import params
from .scene import normalize, motion_position
from .physics import sweep_entry
from ...supply import is_hp_death
DESTROY_INTERNAL,DESTROY_HITPOINTS,DESTROY_LIFETIME=1,2,6

class DumbActorHost:
    MUNITIONS_GRAVITY=340000.
    MUNITIONS_TERMINAL=160000.
    MUNITIONS_BOUNCE=1.0
    MUNITIONS_SIDE_SPEED=59000.

    def native_retirement_capability(self,objid):
        """Issued role/deadline; NPC HP or shield deaths remain server-owned."""
        if type(objid)is not int or objid<=0 or objid in self.retired:return None
        row=self.actors.get(objid)
        if row is None or row['kind'] not in ('projectile','fx','npc'):return None
        expiry=row.get('expire')
        if type(expiry)not in (int,float) or not math.isfinite(expiry) or expiry<0:expiry=None
        return dict(ownerid=self.worldid,objid=objid,role=row['kind'],
            expires_at=expiry,room_time=self.room_time,
            lifetime_due=expiry is not None and self.room_time>=expiry)

    def _note_npc_damage(self,row,at):
        if row['kind']=='npc'and row.get('ai')is not None:
            row['ai']['alert_until']=max(row['ai'].get('alert_until',0),at+3.)

    def _solid_rects(self):
        'Collision terrain as (x, y, w, h, terraintype).'
        rows = []
        for _identity, props in self.pack.templates.items():
            terrain = props.get('terraintype')
            if terrain not in (1, 2):
                continue
            rows.append((props.get('xmp', 0), props.get('ymp', 0), props.get('wmp', 0),
                         props.get('hmp', 0), terrain))
        return rows

    def _record(self, kind, at, **values):
        self.events.append(dict(kind=kind, at=round(at, 6), **values))
        if len(self.events) > 5000:
            self.events = self.events[-4000:]

    def _enqueue(self, at, kind, **values):
        self.sequence += 1
        heapq.heappush(self.queue, (at, self.sequence, dict(kind=kind, **values)))

    def _offset(self, at):
        return max(0, int(round((at - self._frame_start) * 1000.)))

    def _add_actor(self, objid, props, at, kind='static', hp=None, ai=None, vel=None, accel=None):
        row = dict(objid=objid, props=copy.deepcopy(props), kind=kind,
                   start=(props.get('xmp', 0), props.get('ymp', 0)), epoch=at,
                   move=copy.deepcopy(props.get('move') or {}), hp=hp, ai=ai,
                   vid=props.get('vid', ''), npc=kind == 'npc', profile=None,
                   pos=[float(props.get('xmp', 0)), float(props.get('ymp', 0))],
                   vel=list(vel or (0., 0.)), accel=list(accel or (0., 0.)),
                   sent=[float(props.get('xmp', 0)), float(props.get('ymp', 0))],
                   sent_at=at, send_at=at, expire=None, facing=props.get('facing', 0),
                   pose=int(props.get('pose') or 0))
        self.actors[objid] = row
        return row

    def position(self, objid, at=None):
        row = self.actors.get(objid)
        if row is None:
            return (0., 0.)
        at = self.room_time if at is None else at
        if row['kind'] in ('npc', 'projectile', 'fx'):
            return tuple(row['pos'])
        move = row['move']
        if 'relativeto' in move:
            parent = move['relativeto'].get('objid')
            if parent in self.actors and parent != objid:
                x, y = self.position(parent, at)
                offset = move.get('to') or {}
                return x + offset.get('xmp', 0), y + offset.get('ymp', 0)
        return motion_position(row['start'], move, max(0., at - row['epoch']))

    def _move(self, objid, move, at):
        row = self.actors[objid]
        row['start'] = self.position(objid, at)
        row['move'], row['epoch'] = copy.deepcopy(move or {}), at

    def _command(self, objid, command, at, dt_ms=None):
        row = self.actors.get(objid)
        if row is None:
            return
        offset = self._offset(at)
        change = command.get('modifyprops')
        if change:
            prop = change['property']
            if prop in (19, 20):
                return
            values = {k: change[k] for k in ('intval', 'boolval', 'stringval') if k in change}
            if prop in (41, 49) and 'decoded' in change:
                message_type = 'Properties_Move' if prop == 41 else 'AnimationProperties'
                decoded = change['decoded']
                if prop == 41:
                    self._move(objid, decoded, at)
                values['message'] = (message_type, normalize(message_type, decoded, self.layouts))
            self.batch.modify(objid, prop, offset=offset, **values)
            if prop == 43:
                row['vid'] = change.get('stringval', '')
                self._record('vid', at, objid=objid, vid=row['vid'])
        if command.get('playsound'):
            self.batch.sound(objid, command['playsound']['soundname'], offset=offset)
        if 'movetoxmp' in command or 'movetoymp' in command:
            x, y = self.position(objid, at)
            target = (command.get('movetoxmp', x), command.get('movetoymp', y))
            duration = max(1, dt_ms or command.get('movetodt') or 50)
            self.batch.move_to(objid, int(target[0]), int(target[1]), dt=duration, offset=offset)
            self._move(objid, dict(to=dict(xmp=target[0], ymp=target[1], dt=duration)), at)

    def _sync_actor(self, row, at):
        """Send a native MOVE delta when a simulated actor has drifted."""
        if at < row['send_at']:
            return
        if abs(row['pos'][0] - row['sent'][0]) < 256 and abs(row['pos'][1] - row['sent'][1]) < 256:
            return
        dt_ms = max(1, int(round((at - row['sent_at']) * 1000.)))
        self.batch.move_to(row['objid'], int(round(row['pos'][0])), int(round(row['pos'][1])),
                           dt=dt_ms, offset=self._offset(at))
        row['sent'] = list(row['pos'])
        row['sent_at'] = at

        row['send_at'] = at + (0.05 if row['kind'] == 'projectile' else 0.08)

    def _retire(self, objid, reason, at, native=False):
        row = self.actors.get(objid)
        if row is None or objid in self.retired:
            return
        self.retired.add(objid)
        if row['props'].get('vid') == 'dumb.drone' and reason in (1, 2, 3, 4, 5):

            self._drone_burst(row, at)

        if row['props'].get('vid') in ('dumb.munitions', 'dumb.tankmunitions')\
                and reason in (1,2,3,4,5,6):
            self._munitions_burst(row, at)
        effect = {'dumb.missile':'missilex', 'dumb.rocketbullet':'rocketbulletx'}.get(row['vid'])
        if effect and reason in (2,4,5,6):
            self._projectile_explosion(effect, row, at)
        if row['kind'] == 'npc':
            self._npc_died(objid, row, at, reason)
        if not native or getattr(self.bridge, 'shared_room', False):
            self.batch.destroy(objid, ownerid=self.worldid, reason=reason, offset=self._offset(at))
        self.actors.pop(objid, None)
        parent=self.drone_parents.pop(objid,None)
        if parent is not None:
            self.drone_children.get(parent,set()).discard(objid)
        self._record('destroy', at, objid=objid, reason=reason, actor_kind=row['kind'], native=native)

    def _npc_died(self, objid, row, at, reason):
        ai = row.get('ai') or {}
        species = ai.get('species')
        if is_hp_death(reason):
            x, y = self.position(objid, at)
            self.supplies.killed(x + row['props'].get('wmp', 0) / 2.,
                                 y + row['props'].get('hmp', 0) / 2.)
        if self.controller is not None:
            self.controller.npc_died(objid)
        self._record('npc_died', at, objid=objid, species=species, hp=row.get('hp'))

    def _munitions_burst(self, row, at):
        'runtime profile 74: the lobbed shell splits into 8 shrapnel.'
        template = params.PROJECTILES['munitionshrapnel'] if 'munitionshrapnel' in params.PROJECTILES else None
        if template is None:
            return

        x,y=row.get('split_origin',row['sent'])
        x,y=round(x/256)*256,round(y/256)*256
        for index in range(8):
            angle = math.radians(index * 45)
            vel = (math.cos(angle) * template['speed'], math.sin(angle) * template['speed'])
            child=self._spawn_shot('munitionshrapnel',0,at,position=(x,y),vel=vel)
            if index==0:
                self.batch.sound(child,'dumb.shrapnelgen',offset=self._offset(at))
        self.counts['splits'] += 1
        self._record('split', at, objid=row['objid'], children=8)

    def _drone_burst(self, row, at):
        'runtime death: 10 shrapnel inside a 36 degree fan, not a ring.'
        template = params.PROJECTILES['droneshrapnel']
        x, y = row['pos']
        cx = x + row['props'].get('wmp', 0) / 2
        cy = y + row['props'].get('hmp', 0) / 2
        count = max(2, int(params.DRONE_FAN_COUNT))
        center = math.pi if int(row.get('facing') or 0) == 180 else 0.0
        spread = math.radians(float(params.DRONE_FAN_DEGREES))
        for index in range(count):
            angle = center + (index / (count - 1) - 0.5) * spread
            vel = (math.cos(angle) * template['speed'],
                   math.sin(angle) * template['speed'])
            self._spawn_shot('droneshrapnel', 0, at,
                             position=(cx - template['wmp'] / 2,
                                       cy - template['hmp'] / 2), vel=vel)
        self.counts['splits'] += 1
        self._record('split', at, objid=row['objid'], children=count)

    def _projectile_explosion(self, key, parent, at):
        props = self.pack.projectile_props(key)
        if not props:
            return
        x,y=parent['pos'];p=parent['props']
        props.update(xmp=round(x+(p['wmp']-props['wmp'])/2),
                     ymp=round(y+(p['hmp']-props['hmp'])/2))
        self._next_id += 1
        row=self._add_actor(self._next_id,props,at,kind='fx')
        row['expire']=at+props['lifetime']/1000.
        self.batch.create(row['objid'],self.pack.wire_props(props),offset=self._offset(at))

    def _native_velocity(self, objid, at):
        row=self.actors[objid];vx,vy=row['vel'];ax,ay=row['accel']
        cap=row.get('max_speed',195312.5)/3.90625
        velocity=dict(xvel=round(vx/3.90625),yvel=round(vy/3.90625),
            xaccel=round(ax/3906.25),yaccel=round(ay/3906.25),
            minxvel=-round(cap),maxxvel=round(cap))
        row['native_velocity']=True
        self.batch.modify(objid,'MOVE',message=('Properties_Move',
            normalize('Properties_Move',dict(vel=velocity),self.layouts)),offset=self._offset(at))

    def drone_child_count(self, parent):
        """Ownership survives AI/presentation updates; deaths release slots."""
        return len(self.drone_children.get(parent,()))

    def _spawn_shot(self, key, creator, at, position=None, vel=None, accel=None,
                    lifetime_ms=None, target=None, facing=None):
        producer=self.actors.get(creator)
        capped=(key=='drone' and producer is not None)
        if capped and self.drone_child_count(creator)>=params.SPECIES['sniper']['max_pets']:
            self.counts['cap_skips']+=1
            self._record('drone_cap',at,parent=creator,children=sorted(self.drone_children[creator]))
            return None
        template = copy.deepcopy(params.PROJECTILES[key])
        props = self.pack.projectile_props(key)
        props.update(dict(xmp=float(position[0]) if position else 0.,
                     ymp=float(position[1]) if position else 0.,
                     wmp=template['wmp'], hmp=template['hmp'], z=9,
                     blocktype=template['blocktype'], ownertype=2,
                     damage=template['damage'], removetype=props.get('removetype',3), vid=template['vid']))
        props['z'] = template.get('z',props.get('z',9))
        if key=='munitions' and creator in self.actors and self.actors[creator]['vid']=='dumb.launcher':
            props.update(wmp=8192,hmp=8192,z=10)
        if template.get('rotate'):
            props['rotate'] = True
        if template.get('trail'):
            props['movetrail'] = template['trail']
        if template.get('armor_pierce'):
            props['armorpierce'] = template['armor_pierce']
        life = template.get('lifetime_ms') if lifetime_ms is None else lifetime_ms
        if life:
            props['lifetime'] = life
        if target is not None and position is not None:
            dx = target[0] - position[0]
            dy = target[1] - position[1]
            length = math.hypot(dx, dy) or 1.
            vel = (dx / length * template['speed'], dy / length * template['speed'])
        self._next_id += 1
        objid = self._next_id

        row = self._add_actor(objid, props, at, kind='projectile', hp=template.get('hp'),
                              vel=vel or (0., template['speed']),
                              accel=accel or (0., 0.))
        if key == 'drone':
            row.update(kind='npc',npc=True,hp=props.get('hitpoints',300),
                       ai=dict(species='drone',parent=creator,next_shot_at=math.inf))
            if producer is not None:
                self.drone_children.setdefault(creator,set()).add(objid)
                self.drone_parents[objid]=creator
        if life:
            row['expire'] = at + life / 1000.
        self.batch.create(objid, self.pack.wire_props(props),
                          creatorid=creator if creator in self.actors else 0,
                          offset=self._offset(at))

        pose = params.PROJECTILE_POSE.get(key)
        if key=='munitionshrapnel' and vel:
            pose=2 if abs(vel[1])<1 else 3
        if pose:
            self._pose(objid, pose, at)

        if facing is not None:
            row['facing'] = int(facing)
            self.batch.modify(objid, 'FACING', intval=int(facing),
                              offset=self._offset(at))
        if vel and (template.get('rotate') or key in ('laserbullet', 'drone')):
            angle = int(round(math.degrees(math.atan2(vel[1], vel[0])) % 360.))
            self.batch.modify(objid, 'FACING', intval=angle, offset=self._offset(at))
        self.counts['shots'] += 1
        if key in params.PROJECTILE_SOUNDS:
            self.batch.sound(objid,params.PROJECTILE_SOUNDS[key],offset=self._offset(at))
        return objid

    def _species_props(self, species):
        return self.pack.species_props(species)

    def _spawn_npc(self, species, at, position=None):
        props = self._species_props(species)
        if props is None:
            return None
        if position is not None:
            props['xmp'], props['ymp'] = int(round(position[0])), int(round(position[1]))
        hp = props.get('hitpoints')
        spec = params.SPECIES.get(species, {})
        ai = dict(species=species, dir=1 if self.rng.random() < 0.5 else -1,
                  next_shot_at=at + self.rng.uniform(0.2, spec.get('cadence_s') or 1.0),
                  min_x=props.get('xmp', 0) - 24576, max_x=props.get('xmp', 0) + 24576)
        region = self._region_of(props.get('xmp', 0), props.get('ymp', 0) + props.get('hmp', 0))
        if region:
            rect = self.npc_regions[region]['rect']
            ai['min_x'] = max(rect[0], ai['min_x'])
            ai['max_x'] = min(rect[0] + rect[2] - 16384, ai['max_x'])
            ai['min_x'] = min(ai['min_x'], ai['max_x'])
        self._next_id += 1
        objid = self._next_id
        row = self._add_actor(objid, props, at, kind='npc', hp=hp, ai=ai)
        self.batch.create(objid, self.pack.wire_props(props), creatorid=0, offset=self._offset(at))
        self.counts['spawns'] += 1
        self.counts['npcs'] += 1
        self._record('npc_create', at, objid=objid, species=species, x=props.get('xmp'),
                     y=props.get('ymp'), hp=hp, region=region)
        return objid

    def _region_of(self, x, surface):
        for name, spec in self.npc_regions.items():
            rect = spec['rect']
            if (rect[0] - 8192 <= x <= rect[0] + rect[2] + 8192
                    and abs(surface - rect[1]) <= 4096):
                return name
        return None

    def _anim(self, objid, animid, time_ms, at=None, **kwargs):
        decoded = dict(animid=int(animid), time=int(time_ms))
        self._command(objid, dict(modifyprops=dict(property=49, decoded=decoded)),
                      at if at is not None else self.room_time)

    def _pose(self, objid, pose, at=None):
        'runtime POSE modify: the client resolves the animation from it.'
        objid, pose = int(objid), int(pose)
        row = self.actors.get(objid)
        if row is not None:
            if row.get('pose') == pose:
                return False
            row['pose'] = pose
        self.batch.modify(objid, 'POSE', intval=pose,
                          offset=self._offset(at if at is not None else self.room_time))
        return True

    def _step_dynamics(self, start, end):
        self._step_npcs(start, end)
        self._step_projectiles(start, end)

    def _step_npcs(self, start, end):
        dt = max(0., end - start)
        for objid, row in list(self.actors.items()):
            if row['kind'] != 'npc' or objid in self.retired:
                continue
            if row['epoch']>end:
                continue
            if row['expire'] is not None and end >= row['expire']:

                self._retire(objid, DESTROY_LIFETIME, end)
                continue
            self._npc_tick(row, max(0.,end-max(start,row['epoch'])), end)
            self._sync_actor(row, end)

    def _npc_tick(self, row, dt, at):
        from .npcs import tick
        tick(self, row, dt, at)

    def _step_projectiles(self, start, end):
        for objid, row in list(self.actors.items()):
            if row['epoch']>end:
                continue
            if row['kind'] == 'fx':
                if row['expire'] is not None and end >= row['expire']:
                    self._retire(objid, DESTROY_LIFETIME, end)
                continue
            if row['kind'] != 'projectile' or objid in self.retired:
                continue
            dt=max(0.,end-max(start,row['epoch']))
            if row['vid']=='dumb.boundingproj':
                self._step_bounding(row,dt,end)
                continue
            if row['props'].get('vid') == 'dumb.munitions' and row.get('bounce'):

                self._step_munitions(row, dt, end)
                continue
            vx, vy = row['vel']
            ax, ay = row['accel']
            missile=row.get('missile_motion')
            if missile:
                speed=math.hypot(vx,vy);angle=math.atan2(vy,vx)
                midpoint=angle+missile['turn_rate']*dt/2
                travel=(speed+missile['thrust']*dt/2)*dt
                nx=row['pos'][0]+math.cos(midpoint)*travel
                ny=row['pos'][1]+math.sin(midpoint)*travel
                angle+=missile['turn_rate']*dt;speed+=missile['thrust']*dt
                row['vel']=[math.cos(angle)*speed,math.sin(angle)*speed]
                facing=round(math.degrees(angle)%360)%360
                if facing!=row['facing']:
                    row['facing']=facing
                    self.batch.modify(objid,'FACING',intval=facing,offset=self._offset(end))
                self._pose(objid,2 if abs(row['vel'][0])>=abs(row['vel'][1]) else 3,end)
            else:
                cap=row.get('max_speed')
                accelerating=dt
                if cap:
                    speed=math.hypot(vx,vy);accel=math.hypot(ax,ay)
                    accelerating=min(dt,max(0.,(cap-speed)/accel)) if accel else 0.
                nx = row['pos'][0] + vx*dt + ax*accelerating*(dt-accelerating/2)
                ny = row['pos'][1] + vy*dt + ay*accelerating*(dt-accelerating/2)
                row['vel'] = [vx + ax*accelerating, vy + ay*accelerating]
            size=(row['props']['wmp'],row['props']['hmp'])
            hits=[t for wall in self.walls if (t:=sweep_entry(row['pos'],(nx,ny),size,wall[:4])) is not None]
            if hits:
                fraction=max(0.,min(hits)-1e-6)
                nx=row['pos'][0]+(nx-row['pos'][0])*fraction
                ny=row['pos'][1]+(ny-row['pos'][1])*fraction
            row['pos'] = [nx, ny]

            expire = row['expire'] is not None and end >= row['expire']
            blocked = bool(hits) or self._outside(nx, ny, row['props']) or self._blocked(nx, ny, row['props'])
            if expire or blocked:
                if blocked:
                    row['split_origin']=list(row['sent'])
                row['send_at'] = 0.
                self._sync_actor(row, end)
                if expire:
                    self.counts['lifetime'] += 1
                    self._retire(objid, DESTROY_LIFETIME, end)
                else:
                    self.counts['wall_hits'] += 1
                    self._retire(objid, 5, end)
                continue
            if not row.get('native_velocity'):
                self._sync_actor(row, end)

    def _step_bounding(self,row,dt,end):
        """Light's constant diagonal shot reflects on solid terrain."""
        from .physics import sweep_contact
        from .npcs import surfaces
        remaining=dt
        size=(row['props']['wmp'],row['props']['hmp'])
        geometry=[s[:4] for s in surfaces(self) if s[4]==1]
        for _ in range(8):
            start=row['pos'];vx,vy=row['vel']
            finish=[start[0]+vx*remaining,start[1]+vy*remaining]
            contacts=[c for wall in geometry if (c:=sweep_contact(start,finish,size,wall)) is not None]
            if not contacts:
                row['pos']=finish
                break
            fraction=min(c[0]for c in contacts)
            axes={axis for t,normal in contacts if abs(t-fraction)<1e-8 for axis in normal}
            row['pos']=[start[0]+(finish[0]-start[0])*fraction,
                        start[1]+(finish[1]-start[1])*fraction]
            for axis in axes:
                row['vel'][axis]*=-1
                row['pos'][axis]+=math.copysign(1e-4,row['vel'][axis])
            row['rebound_count']=row.get('rebound_count',0)+1
            remaining*=1-fraction
            if remaining<1e-9:
                break
        self._sync_actor(row,end)
        if row['expire'] is not None and end>=row['expire']:
            self.counts['lifetime']+=1
            self._retire(row['objid'],DESTROY_LIFETIME,end)

    def _step_munitions(self, row, dt, end):
        'Local battle simulator runtime.'
        x, y = row['pos']
        vx, vy = row['vel']
        w, h = row['props'].get('wmp', 8192), row['props'].get('hmp', 8192)
        gravity=self.MUNITIONS_GRAVITY
        accelerating=min(dt,max(0.,(self.MUNITIONS_TERMINAL-vy)/gravity))
        nx=x+vx*dt
        ny=y+vy*dt+gravity*accelerating*(dt-accelerating/2)
        next_vy=min(self.MUNITIONS_TERMINAL,vy+gravity*dt)
        floor_rule=getattr(self,'munitions_floor',262144.)
        floor=float(floor_rule(row,nx,ny) if callable(floor_rule) else floor_rule)
        if next_vy>0 and ny+h>=floor:
            gap=max(0.,floor-h-y)
            contact=(math.sqrt(vy*vy+2*gravity*gap)-vy)/gravity if gap else 0.

            if contact>accelerating:
                covered=vy*accelerating+.5*gravity*accelerating*accelerating
                contact=accelerating+max(0.,gap-covered)/self.MUNITIONS_TERMINAL
            remaining=max(0.,dt-min(dt,contact))
            row['bounce_vy_in']=min(self.MUNITIONS_TERMINAL,vy+gravity*contact)
            impulse=self.rng.uniform(*params.MUNITIONS_REBOUND_SPEED)
            row['rebound_speed']=impulse
            row['rebound_count']=row.get('rebound_count',0)+1
            ny=floor-h-impulse*remaining+.5*gravity*remaining*remaining
            next_vy=-impulse+gravity*remaining
            if not row.get('side_push'):
                row['side_push'] = True
                vx = math.copysign(self.MUNITIONS_SIDE_SPEED,
                                   self.rng.choice((-1., 1.)))
        for sx, sy, sw, sh, terrain in self.walls:
            if ny + h <= sy or ny >= sy + sh:
                continue
            if vx > 0 and x + w <= sx and nx + w > sx:
                nx = sx - w
                vx = 0.
            elif vx < 0 and x >= sx + sw and nx < sx + sw:
                nx = sx + sw
                vx = 0.
        row['vel'] = [vx, next_vy]
        row['pos'] = [nx, ny]
        self._sync_actor(row, end)
        if row['expire'] is not None and end >= row['expire']:
            self.counts['lifetime'] += 1
            self._retire(row['objid'], DESTROY_LIFETIME, end)

    def _drop_projectile(self, objid, reason, at):
        """Retire a projectile copy without a destroy packet (unused fallback)."""
        self._retire(objid, reason, at, native=True)

    def _outside(self, x, y, props):
        x0, y0, x1, y1 = self.bounds
        return (x < x0 or y < y0 or x + props.get('wmp', 0) > x1
                or y + props.get('hmp', 0) > y1)

    def _blocked(self, x, y, props):

        w, h = props.get('wmp', 0), props.get('hmp', 0)
        for sx, sy, sw, sh, terrain in self.walls:
            if x < sx + sw and x + w > sx and y < sy + sh and y + h > sy:
                return True
        return False
