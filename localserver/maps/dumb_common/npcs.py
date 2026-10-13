'Local battle simulator runtime.'
import math
from . import params

def face(host, row, dx, at):
    facing = 0 if dx >= 0 else 180
    if row['facing'] != facing:
        row['facing'] = facing
        host.batch.modify(row['objid'], 'FACING', intval=facing, offset=host._offset(at))

def surfaces(host):
    for row in host.actors.values():
        p = row['props']
        kind = p.get('terraintype')
        if kind in (1,2):
            x,y = host.position(row['objid'])
            yield (x,y,p.get('wmp',0),p.get('hmp',0),kind)

def ground_step(host, row, dt, vx):
    """Walls stop horizontal travel; ledges support only downward travel."""
    x,y = row['pos']; w,h = row['props']['wmp'],row['props']['hmp']
    nx = x+vx*dt
    geometry = list(surfaces(host))
    for sx,sy,sw,sh,kind in geometry:
        if kind != 1 or y+h <= sy or y >= sy+sh:
            continue
        if vx > 0 and x+w <= sx and nx+w > sx:
            nx = min(nx,sx-w)
        if vx < 0 and x >= sx+sw and nx < sx+sw:
            nx = max(nx,sx+sw)
    vy = row['vel'][1]
    ny = y+vy*dt+.5*params.NPC_GRAVITY*dt*dt
    vy += params.NPC_GRAVITY*dt
    supported = False
    for sx,sy,sw,sh,kind in geometry:
        if nx+w <= sx or nx >= sx+sw:
            continue
        if kind==1 and row['vel'][1]<0 and y>=sy+sh and ny<sy+sh:
            ny=sy+sh;vy=0.
        if y+h <= sy+1 and ny+h >= sy and vy >= 0:
            ny = min(ny,sy-h); vy = 0.; supported = True
    row['pos'] = [nx,ny]
    row['vel'][1] = vy
    return supported, abs(nx-(x+vx*dt)) > 1

def fly_step(host, row, dt, vx, vy):
    x,y = row['pos']; w,h = row['props']['wmp'],row['props']['hmp']
    nx,ny = x+vx*dt,y+vy*dt
    hit = False
    for sx,sy,sw,sh,kind in surfaces(host):
        if kind != 1:
            continue
        if ny+h <= sy or ny >= sy+sh:
            continue
        if vx > 0 and x+w <= sx and nx+w > sx:
            nx = min(nx,sx-w);hit = True
        elif vx < 0 and x >= sx+sw and nx < sx+sw:
            nx = max(nx,sx+sw);hit = True
    row['pos'] = [nx,max(getattr(host,'npc_ceiling',0.),min(ny,262144-h))]
    return hit

def muzzle(row, key, direction, pack=None):
    x,y = row['pos']; p=row['props']; w,h=p['wmp'],p['hmp']
    if row['ai']['species']=='heavy':
        point = params.HEAVY_ANIM_POINTS.get(key)
        if point:

            px_, py_ = point
            shot = params.PROJECTILES[key]
            cx = x + w/2 + direction*px_*256
            cy = y + h + py_*256
            return (cx - shot['wmp']/2, cy - shot['hmp']/2)
        front,top = params.HEAVY_MUZZLE_MP[key]
        shotw = params.PROJECTILES[key]['wmp']
        return (x-front if direction<0 else x+w+front-shotw,y+top)
    if row['ai']['species']=='sniper':
        return (x+(1280 if direction<0 else 3840),y-8960)
    if row['ai']['species']=='trooper':
        return (x+(-3072 if direction<0 else 14336),y+6400)
    slot=1 if key in ('bullet','boundingproj') else (5 if row['ai']['species']=='erocketeer' else 3)
    if row['ai']['species'] in ('light','rocketeer','erocketeer'):
        points=list(pack.emit_points(row['vid'],slot,(x,y),(w,h),
                    180 if direction<0 else 0)) if pack else []
        if points:
            shot=params.PROJECTILES[key]
            return points[0]['x']-shot['wmp']/2,points[0]['y']-shot['hmp']/2
    return (x-4096 if direction<0 else x+w-2560,y+h*.3)

def fire(host, row, key, at, direction=None):
    from ...participants import from_bridge, nearest
    p = nearest(from_bridge(host.bridge, host.player),
                (row['pos'][0]+row['props']['wmp']/2, row['pos'][1]+row['props']['hmp']/2))
    if p is None:
        return
    if direction is None:
        direction = (row['ai'].get('action_dir') if key in params.HEAVY_MAIN_ACTIONS
                     and row['ai']['species']=='heavy' else None)
        direction = direction or (-1 if row['facing']==180 else 1)
    origin = muzzle(row,key,direction,host.pack)
    target = (p.xmp+p.wmp/2,p.ymp+p.hmp/2)
    if key == 'bullet' and row['ai']['species'] == 'trooper':

        speed = params.PROJECTILES['bullet']['speed']
        host._spawn_shot(key, row['objid'], at, position=origin,
                         vel=(direction * speed, 0.),
                         facing=180 if direction < 0 else None)
        host._record('npc_shot', at, objid=row['objid'],
                     species=row['ai']['species'], shot=key, vid='dumb.' + key)
        return
    if key in params.HEAVY_MAIN_ACTIONS:
        action = params.HEAVY_MAIN_ACTIONS[key]
        shot = host._spawn_shot(key,row['objid'],at,position=origin,
            vel=(direction*action['initial_speed'],0.),
            accel=(direction*action['accel'],0.),facing=180 if direction<0 else 0)
        host.actors[shot]['max_speed'] = action['max_speed']

        host._native_velocity(shot,at)
    elif key == 'munitions':

        host._spawn_shot(key,row['objid'],at,position=origin,
            vel=(direction*59000.,28000.),accel=(0.,params.NPC_GRAVITY))
    elif key == 'missile':

        base = row['ai'].get('fire_angle')
        if base is None:
            dx,dy=target[0]-origin[0],target[1]-origin[1]
            base = math.atan2(dy,dx)
        ux, uy = math.cos(base), math.sin(base)
        flight=params.MISSILE_FLIGHT[row['ai']['species']]
        shot=host._spawn_shot(key,row['objid'],at,position=origin,
            vel=(ux*flight['initial_speed'],uy*flight['initial_speed']),
            accel=(ux*flight['acceleration'],uy*flight['acceleration']))
        host.actors[shot]['missile_motion']=dict(thrust=flight['acceleration'],
            turn_rate=math.radians(host.rng.triangular(-flight['turn_degrees'],flight['turn_degrees'],0.)))
    elif key == 'drone':
        if host._spawn_shot(key,row['objid'],at,position=origin,target=target) is None:
            return
    elif key == 'boundingproj':
        host._spawn_shot(key,row['objid'],at,position=origin,
                         vel=(direction*78000.,78000.))
    else:

        host._spawn_shot(key,row['objid'],at,position=origin,target=target)
    host._record('npc_shot',at,objid=row['objid'],species=row['ai']['species'],
                 shot=key,vid='dumb.'+key)

def announce(host,row,key,anim,emit,at,duration):
    if row['ai']['species']=='heavy':
        host._pose(row['objid'],4,at)
        row['ai']['action_dir']=-1 if row['facing']==180 else 1
    host._anim(row['objid'],anim,round(duration*1000),at)
    row['ai'].update(action_until=at+duration,pending_key=key,
                     pending_at=at+emit if emit is not None else None)
    host._record('npc_action',at,objid=row['objid'],species=row['ai']['species'],
                 anim=anim,duration=duration,emit_s=emit)

def acquire_target(row,player,spec,at):
    """Starting aggro and retaining an engaged target are separate decisions.

    Long-distance launches in recordings include already active enemies;
    they must not make newly spawned enemies acquire across the whole room.
    Explicit damage can alert an otherwise idle robot.
    """
    x,y=row['pos'];p=row['props'];ai=row['ai']
    dx=player.xmp+player.wmp/2-x-p['wmp']/2
    dy=player.ymp+player.hmp/2-y-p['hmp']/2
    radius=spec['aggro_mp']if ai.get('acquired')else spec.get('acquire_mp',spec['aggro_mp'])
    active=math.hypot(dx,dy)<=radius or at<ai.get('alert_until',-1)
    ai['acquired']=active
    return active

def tick(host,row,dt,at):
    from ...participants import from_bridge, nearest
    p=nearest(from_bridge(host.bridge, host.player),
              (row['pos'][0]+row['props']['wmp']/2, row['pos'][1]+row['props']['hmp']/2))
    if p is None or not p.alive:
        return
    ai=row['ai'];name=ai['species'];spec=params.SPECIES[name]
    x,y=row['pos'];w,h=row['props']['wmp'],row['props']['hmp']
    dx=p.xmp+p.wmp/2-x-w/2
    dy=p.ymp+p.hmp-y-h
    acquired=acquire_target(row,p,spec,at)
    if ai.get('pending_at') is not None and at>=ai['pending_at']:
        key=ai.pop('pending_key',None);ai['pending_at']=None
        if key:
            fire(host,row,key,at)
    if name=='drone':
        ty=p.ymp+p.hmp/2-y-h/2;length=math.hypot(dx,ty) or 1.
        fly_step(host,row,dt,dx/length*params.SPECIES['drone']['walk_speed'],
                 ty/length*params.SPECIES['drone']['walk_speed'])
        face(host,row,dx,at)
        host._pose(row['objid'],2 if abs(dx)>=abs(ty) else 3,at)
        return
    if name=='sniper':
        face(host,row,dx,at);host._pose(row['objid'],2,at)
        pets=host.drone_child_count(row['objid'])
        if acquired and pets<spec['max_pets'] and at>=ai['next_shot_at'] and ai.get('pending_at') is None:
            announce(host,row,'drone',2,.61,at,1.056)
            ai['next_shot_at']=at+spec['cadence_s']
        return
    if name=='heavy':

        can_fire = acquired

        active=at<ai.get('action_until',0.)
        if can_fire and at>=ai.get('next_shell_at',row['epoch']+2.07):
            if not active:
                face(host,row,dx,at)
            fire(host,row,'munitions',at,direction=1 if dx>=0 else -1)
            ai['next_shell_at']=at+params.HEAVY_SHELL_INTERVAL_S
        walk_phase=ai.setdefault('walk_at',at)

        moving=not active and (at-walk_phase)%1.80<.90
        if acquired:ai['dir']=1 if dx>=0 else -1
        direction=ai.setdefault('dir',1)
        vx=direction*19500 if moving else 0.
        _,wall=ground_step(host,row,dt,vx)
        if wall:ai['dir']=-direction
        if not active:

            face(host,row,dx if acquired else direction,at)
        host._pose(row['objid'],5 if moving else 4,at)
        if moving and at>=ai.get('next_step_anim',0.):
            slot=0 if ai.get('step_flip',False) else 1
            ai['step_flip']=not ai.get('step_flip',False)
            ai['next_step_anim']=at+1.8
            host._anim(row['objid'],slot,1848,at)
        if can_fire and not active and at>=ai['next_shot_at']:

            choices=['rocketbullet','rocketbulletap']
            if abs(dx)<98304 and abs(dy)<row['props']['hmp']+p.hmp:
                choices.append('beam')
            key=host.rng.choice(choices);action=params.HEAVY_MAIN_ACTIONS[key]
            announce(host,row,None if key=='beam' else key,action['anim'],
                     action['emit_s'],at,action['time_ms']/1000.)
            ai['next_shot_at']=ai['action_until']+.1
            if key=='beam':
                host.batch.sound(row['objid'],'dumb.heavybeam',offset=host._offset(at))
                host._record('npc_shot',at,objid=row['objid'],species=name,shot='beam')
        return
    if name in ('rocketeer','erocketeer'):
        ceiling=getattr(host,'npc_ceiling',0.)
        elite=name=='erocketeer';mode=ai.setdefault('mode','wait')
        if mode=='wait':
            host._pose(row['objid'],4,at)
        if mode=='wait' and acquired and at>=ai['next_shot_at']:
            face(host,row,dx,at)
            ai['dir']=1 if dx>=0 else -1
            target_gap=math.hypot(max(0.,abs(dx)-(w+p.wmp)/2),
                                  max(0.,p.ymp-y-h,y-p.ymp-p.hmp))
            close=target_gap<=98304
            if not elite and close:
                announce(host,row,'bullet',1,.462,at,1.056)
                ai['hop_at']=at+1.156;ai['mode']='shot_wait'
                ai['hop_dir']=-1 if dx>=0 else 1
            elif not elite:
                host._anim(row['objid'],2,1056,at)
            if elite or not close:
                ai['takeoff_at']=at+.57;ai['hover_y']=max(ceiling,y-params.ROCKETEER_FLIGHT_HEIGHT)
                ai['lift_speed']=20000.;ai['mode']='launch'
        elif mode=='shot_wait':
            host._pose(row['objid'],4,at)
            if at>=ai['hop_at']:
                ai['mode']='backhop';row['vel'][1]=params.ROCKETEER_BACKHOP_VY
        elif mode=='backhop':
            landed,_=ground_step(host,row,dt,ai['hop_dir']*params.ROCKETEER_BACKHOP_VX)
            host._pose(row['objid'],3 if row['vel'][1]<0 else 1,at)
            if landed:
                host._pose(row['objid'],4,at)
                host._anim(row['objid'],2,1056,at)
                ai.update(mode='launch',takeoff_at=at+.25,hover_y=max(ceiling,row['pos'][1]-params.ROCKETEER_FLIGHT_HEIGHT),lift_speed=20000.)
        elif mode=='launch':
            if at>=ai['takeoff_at']:
                ai['mode']='takeoff'
                ai['lift_elapsed']=0.;ai['lift_origin_y']=row['pos'][1]
                host.batch.sound(row['objid'],'dumb.rocketeerjump',offset=host._offset(at))
        elif mode=='takeoff':
            host._pose(row['objid'],3,at)
            ai['lift_elapsed']=min(params.ROCKETEER_TAKEOFF_S,ai['lift_elapsed']+dt)
            elapsed=ai['lift_elapsed']
            ai['lift_speed']=params.ROCKETEER_LIFT_SPEED+params.ROCKETEER_LIFT_ACCEL*elapsed
            rise=params.ROCKETEER_LIFT_SPEED*elapsed+.5*params.ROCKETEER_LIFT_ACCEL*elapsed*elapsed
            row['pos'][1]=max(ceiling,ai['lift_origin_y']-rise)
            if elapsed>=params.ROCKETEER_TAKEOFF_S:
                ai['mode']='fly';ai['next_shot_at']=at
        elif mode=='fly':
            if at>=ai.get('landing_at',math.inf):
                ai['mode']='fall';row['vel'][1]=0.;ai.pop('landing_at',None)
                ai.pop('firing_until',None)
                return
            firing=ai.get('firing_until',0.)>at
            turning='wall_turn_at'in ai
            waiting_turn=turning and at<ai['wall_turn_at']
            if turning and not waiting_turn and not firing:
                ai['dir']=ai.pop('wall_turn_dir')
                ai.pop('wall_turn_at',None)
            if not firing and 'turn_after_fire' in ai and not waiting_turn:
                ai['dir']=ai.pop('turn_after_fire')
            direction=ai.get('fire_dir',ai.get('dir',1)) if firing else ai.setdefault('dir',1 if dx>=0 else -1)
            speed=78000. if elite else (7140. if abs(dx)>65536 else 0.)
            if waiting_turn or (firing and 'turn_after_fire' in ai):
                speed=0.
            if fly_step(host,row,dt,direction*speed,0.):
                if firing:
                    ai['turn_after_fire']=-direction
                else:

                    row['send_at']=0.
                    host._sync_actor(row,at)
                    ai['wall_turn_at']=at+.085
                    ai['wall_turn_dir']=-direction

            if ai.get('firing_until',0.) > at:
                face(host,row,ai['fire_dir'],at)
            else:
                ai['fire_dir']=1 if dx>=0 else -1
                face(host,row,direction,at)
            host._pose(row['objid'],2,at)
            if (acquired or ai.get('volley_left',0)>0) and at>=ai['next_shot_at'] and ai.get('pending_at') is None:
                left = int(ai.get('volley_left') or 0)
                if left <= 0:

                    left = int(params.MISSILE_VOLLEY_SHOTS)
                    ai['volley_gap'] = params.MISSILE_VOLLEY_FIRST_GAP_S
                ai['fire_dir']=direction
                target=(p.xmp+p.wmp/2,p.ymp+p.hmp/2)
                ai['fire_angle']=math.atan2(target[1]-y-h/2,target[0]-x-w/2)
                ai['firing_until']=at+(1.056 if elite else .660)
                announce(host,row,'missile',5 if elite else 3,.198,at,1.056 if elite else .660)
                left -= 1
                ai['volley_left']=left
                if left > 0:
                    gap=max(params.MISSILE_VOLLEY_MIN_GAP_S, float(ai['volley_gap']))
                    ai['next_shot_at']=at+gap
                    ai['volley_gap']=gap-params.MISSILE_VOLLEY_GAP_STEP_S
                else:
                    ai['landing_at']=at+params.ROCKETEER_LAND_AFTER_LAST_ANIM_S
                    ai['next_shot_at']=math.inf
        elif mode=='fall':
            landed,_=ground_step(host,row,dt,0.)
            host._pose(row['objid'],4 if landed else 1,at)
            if landed:
                ai['mode']='rest';ai['next_shot_at']=at+host.rng.uniform(*params.ROCKETEER_GROUND_REST_S)
        elif mode=='rest':
            host._pose(row['objid'],4,at)
            if acquired and at>=ai['next_shot_at']:
                ai['dir']=1 if dx>=0 else -1;face(host,row,dx,at)
                ai.update(mode='launch',takeoff_at=at,hover_y=max(ceiling,y-params.ROCKETEER_FLIGHT_HEIGHT),lift_speed=20000.)
                if not elite:
                    host._anim(row['objid'],2,1056,at)
        return
    if name in ('light','elight'):
        if name=='light' and ai.get('hop_started')is not None:
            elapsed=at-ai['hop_started']

            index=ai.get('hop_shot',0)
            if index<3 and elapsed>=.05+params.LIGHT_SHOT_GAPS_S[index]:
                announce(host,row,'boundingproj',1,.066,at,.264)
                ai['hop_shot']=index+1
            if not ai.get('hop_landed'):
                landed,_=ground_step(host,row,dt,ai['hop_dir']*params.LIGHT_BACKHOP_VX)
                host._pose(row['objid'],3,at)
                ai['hop_landed']=landed
            else:
                ground_step(host,row,dt,0.);host._pose(row['objid'],5,at)
            if ai.get('hop_landed') and ai.get('hop_shot',0)==3 and ai.get('pending_at')is None:
                ai['hop_started']=None;ai['dir']=-ai['hop_dir']
            return
        direction=ai.setdefault('dir',1 if dx>=0 else -1)
        grounded,hit=ground_step(host,row,dt,direction*spec['walk_speed'])
        if hit:
            ai['dir']=-direction
        face(host,row,direction,at);host._pose(row['objid'],5 if grounded else 1,at)
        if name=='light' and acquired and abs(dx)<65536 and abs(dy)<12288 and grounded and at>=ai['next_shot_at']:
            face(host,row,dx,at)
            ai.update(hop_started=at,hop_dir=-1 if dx>=0 else 1,hop_shot=0,hop_landed=False)
            row['vel'][1]=params.LIGHT_BACKHOP_VY
            ai['next_shot_at']=at+3.
        return

    active=at<ai.get('action_until',0.)
    level=abs(dy)<=12288
    if active:
        vx=0.
    elif acquired and level:
        vx=0.
        face(host,row,dx,at)
        if not active and at>=ai['next_shot_at']:
            announce(host,row,'bullet',2,.264,at,.528)
            ai['next_shot_at']=at+1.295
    else:
        direction=ai.setdefault('dir',1 if dx>=0 else -1)
        vx=direction*39000
        support=next((s for s in surfaces(host) if abs(y+h-s[1])<2
                      and x+w>s[0] and x<s[0]+s[2]),None)
        if support:
            lo,hi=support[0],support[0]+support[2]-w
            if x+vx*dt<lo or x+vx*dt>hi:
                direction=-direction;ai['dir']=direction;vx=direction*39000
                row['pos'][0]=max(lo,min(x,hi))
        face(host,row,vx,at)
    grounded,hit=ground_step(host,row,dt,vx)
    if hit:
        ai['dir']=-ai.get('dir',1)
    host._pose(row['objid'],(5 if vx else 4) if grounded else 1,at)
