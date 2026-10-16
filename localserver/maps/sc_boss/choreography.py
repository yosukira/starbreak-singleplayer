'Coherent platform formations, using runtime layouts and bounded travel.'
import copy
import heapq
import math
import statistics

from .collision import sweep
from .kinematics import slot_kinematics,paced_points,terminal_lanes,points,lanes,curve
from .plinko import balanced_targets

MAX_PLATFORM_SPEED_PX_S=350.
PRIMARY={'normal':'scboss.dshot','triangle':'scboss.rshot','nova':'scboss.gshot',
         'pachinko':'scboss.plinkos','ladder':'scboss.vortex'}

def nova_for_side(pack,course,slot,payload):
    """Keep a synthesized Nova's whole burst on its platform side.

    L-Nova owns measured left-hand orbits but borrows a D-Nova attack.
    Rotate that donor around its emitter, including blooms and ring pieces;
    other slots in the same synthesized course still use their native side.
    """
    synth=course.get('synthesized') or {}
    index=synth.get('slot')
    if (synth.get('mode')!='nova' or index is None
            or abs(course['formations'][index]['t']-slot['start'])>1e-6):
        return payload
    donor=next(c for c in pack.programs['courses']
               if not c.get('synthesized') and c['pattern_id']==synth['donor'])
    side=donor['formations'][synth['donor_slot']]['side']
    turns=('URDL'.index(slot['side'])-'URDL'.index(side))%4
    if not turns:return payload
    launches=[a['props'] for a in payload if a['vid']=='scboss.gshot'
              and (a['props'].get('move') or {}).get('to')]
    if not launches:raise ValueError('Synthesized Nova has no emitter')
    cx=round(statistics.median(p['xmp'] for p in launches))
    cy=round(statistics.median(p['ymp'] for p in launches))

    def vector(x,y):
        return ((x,y),(-y,x),(-x,-y),(y,-x))[turns]

    def rotate(value):
        if isinstance(value,list):return [rotate(v) for v in value]
        if not isinstance(value,dict):return value
        result={k:rotate(v) for k,v in value.items()}
        for xkey,ykey in (('xmp','ymp'),('movetoxmp','movetoymp')):
            if xkey in value and ykey in value:
                x,y=vector(value[xkey]-cx,value[ykey]-cy)
                result[xkey],result[ykey]=cx+x,cy+y
        for suffix in ('vel','accel'):
            xkey,ykey='x'+suffix,'y'+suffix
            if xkey in value or ykey in value:
                result[xkey],result[ykey]=vector(value.get(xkey,0),value.get(ykey,0))
        return result

    return [rotate(a) if a['vid']=='scboss.gshot' else a for a in payload]

def mode_timing(course,slot,courses=None):
    """First emission and native end of this attack, excluding next travel."""
    lo,hi=slot['start'],slot['end'];mode=slot['mode']
    synth=course.get('synthesized') or {}
    if (courses and synth.get('mode')==mode and synth.get('slot') is not None
            and abs(course['formations'][synth['slot']]['t']-lo)<1e-6):

        donor=next(c for c in courses if not c.get('synthesized') and c['pattern_id']==synth['donor'])
        n=synth['donor_slot'];forms=donor['formations'];dlo=forms[n]['t']
        dhi=forms[n+1]['t'] if n+1<len(forms) else donor['duration_s']
        original=mode_timing(donor,dict(start=dlo,end=dhi,mode=mode));shift=lo-dlo
        expected={(a['source_id'],a['vid'],round(a['t']+shift,5)) for a in original['attacks']}
        result=dict(original)
        for k in ('first','last','finish','outgoing','assembled'):result[k]+=shift
        result['attacks']=[a for a in course['attacks']
                           if (a['source_id'],a['vid'],round(a['t'],5)) in expected]
        return result
    attacks=[a for a in course['attacks'] if lo<=a['t']<hi and a['vid']!='scboss.scream']
    main=[a for a in attacks if a['vid']==PRIMARY[mode]]
    if not main:raise ValueError('payload has no primary attacks: %r'%slot)
    first=min(a['t'] for a in main);last=max(a['t'] for a in main)
    outgoing=[];assembled=[]
    for p in course['platforms']:
        before=[]
        for c in p['commands']:
            mp=c.get('modifyprops',{})
            if mp.get('property')!=41:continue
            points=((mp.get('decoded') or {}).get('waypoint') or {}).get('points') or []

            if last<c['t']<hi+2 and len(points)>=12:outgoing.append(c['t'])
            if c['t']<=first:before.append((c['t'],c['t']+sum(pt['dt'] for pt in points)/1000.))
        if before:assembled.append(max(before)[1])
    out=statistics.median(outgoing) if outgoing else hi
    finish=(max(a['t']+max(0,a['props'].get('lifetime',0))/1000. for a in main)
            if mode=='nova' else out)
    assembled_at=min(first,max(assembled)) if assembled else first
    return dict(first=first,last=last,finish=finish,outgoing=out,assembled=assembled_at,
                lead_s=max(.32,min(1.7,first-assembled_at)),lead_s_measured=max(0.,first-assembled_at),
                attack_s=finish-first,attacks=attacks)

def route(start,end,size,head):
    """Shortest visible corner route around the head, not through its bbox."""
    if not sweep(start,end,size,head):return [end]
    x,y,w,h=head;pw,ph=size;gap=32*256.
    corners=[(x-pw-gap,y-ph-gap),(x+w+gap,y-ph-gap),
             (x-pw-gap,y+h+gap),(x+w+gap,y+h+gap)]
    points=[start,end]+corners
    queue=[(0.,0,[])];seen=set()
    while queue:
        dist,i,path=heapq.heappop(queue)
        if i in seen:continue
        seen.add(i)
        if i==1:return [points[j] for j in path]
        for j,p in enumerate(points):
            if j==i or j in seen:continue
            if not sweep(points[i],p,size,head):
                heapq.heappush(queue,(dist+math.dist(points[i],p),j,path+[j]))

    return [end]

def clockwise_route(start,end,size,head,previous,following):
    """Direction handoff around the boss in screen-clockwise order."""
    if (previous,following) not in (('U','R'),('R','D'),('D','L'),('L','U')):
        return route(start,end,size,head)
    x,y,w,h=head;pw,ph=size;gap=32*256.;sx,sy=start;ex,ey=end
    if previous=='U':
        top=min(sy,ey,y-ph-gap);right=max(ex,x+w+gap)
        path=[(sx,top),(right,top),(right,ey),(ex,ey)]
    elif previous=='R':
        right=max(sx,ex,x+w+gap);bottom=max(ey,y+h+gap)
        path=[(right,sy),(right,bottom),(ex,bottom),(ex,ey)]
    elif previous=='D':
        bottom=max(sy,ey,y+h+gap);left=min(ex,x-pw-gap)
        path=[(sx,bottom),(left,bottom),(left,ey),(ex,ey)]
    else:
        left=min(sx,ex,x-pw-gap);top=min(ey,y-ph-gap)
        path=[(left,sy),(left,top),(ex,top),(ex,ey)]
    result=[];last=start
    for p in path:
        if math.dist(last,p)>1:result.append(p);last=p
    return result or [end]

def side_entry(point,size,head,side):
    x,y,w,h=head;pw,ph=size;px,py=point;gap=32*256.
    if side=='R':px=max(px,x+w+gap)
    elif side=='L':px=min(px,x-pw-gap)
    elif side=='D':py=max(py,y+h+gap)
    else:py=min(py,y-ph-gap)
    return px,py

def ladder_retraction(pack,slot,timing,side):
    """Recover the same-direction collection shape, not the next route."""
    candidates=[slot]+[dict(course=ci,start=lo,end=hi,mode='ladder')
        for windows in pack.programs['slot_payloads'].values()
        for ci,lo,hi in windows.get('ladder',[])
        if next((f['side'] for f in pack.programs['courses'][ci]['formations'] if f['t']==lo),None)==side]
    for candidate in candidates:
        course=pack.programs['courses'][candidate['course']]
        if course.get('synthesized'):continue
        t=mode_timing(course,candidate);refs=[];envelopes=[];times=[]
        for p in course['platforms']:
            qs=[q for q in lanes(p,course) if t['first']<q['t']<t['outgoing'] and len(points(q))==10]
            if not qs:continue
            q=qs[-1];target=points(q)[-1]
            refs.append(dict(p,props=dict(p['props'],xmp=target['xmp'],ymp=target['ymp'])))
            envelopes.append(curve(q));times.append(q['t']-t['first'])
        if len(refs)>len(course['platforms'])*.8:
            return refs,{p['source_id']:v for p,v in zip(refs,envelopes)},statistics.median(times)
    return None

def geometry(session,course,slot,mode):
    key=(id(course),slot['start'],slot['end'],mode)
    cache=getattr(session.pack,'_layout_cache',None)
    if cache is None:cache=session.pack._layout_cache={}
    if key not in cache:cache[key]=snapshot_geometry(course,slot,mode)
    return cache[key]

def snapshot_geometry(course,slot,mode):
    from .motion import profile_position
    lo,hi=slot['start'],slot['end'];span=hi-lo
    at=lo+span*.45
    if mode=='pachinko':

        candidates=[lo+span*f for f in (.25,.35,.45,.55)]
        def width(t):
            pts=[(profile_position(p,t),p['props']) for p in course['platforms']]
            return max(x+q['wmp'] for ((x,y),q) in pts)-min(x for ((x,y),q) in pts)
        at=max(candidates,key=width)
    refs=[]
    for p in course['platforms']:
        q=dict(p,props=dict(p['props']),commands=[]);x,y=profile_position(p,at)
        q['props'].update(xmp=int(x),ymp=int(y))
        refs.append(q)
    return refs,at

def append_layout(session,pieces,targets,at,head,minimum_s=1.5,envelopes=None,
                  previous=None,following=None,individual=False,limit_speed=False):
    """Follow native speed envelopes; settle when the last piece arrives."""
    paths=[];longest=0.
    starts=at if isinstance(at,list) else [at]*len(pieces)
    for n,(piece,target) in enumerate(zip(pieces,targets)):
        piece['commands'].sort(key=lambda c:c['t'])
        start=session._evaluate_profile_position(piece,starts[n])
        size=(piece['props']['wmp'],piece['props']['hmp'])
        path=clockwise_route(start,target,size,head,previous,following) if previous else route(start,target,size,head)
        length=sum(math.dist(a,b) for a,b in zip([start]+path,path))
        paths.append((start,path,length));longest=max(longest,length)
    duration=max(minimum_s,longest/(MAX_PLATFORM_SPEED_PX_S*256.))
    arrival=duration if envelopes is None else 0.
    arrivals=[]
    for n,(piece,(start,path,length)) in enumerate(zip(pieces,paths)):
        if envelopes is not None:
            native_points,own_duration=paced_points(start,path,envelopes[n],minimum_s=minimum_s)
            if limit_speed:
                # Plan the same durations that the runtime speed cap will use.
                x,y=start
                for point in native_points:
                    tx,ty=point['xmp'],point['ymp']
                    point['dt']=max(point['dt'],int(math.ceil(
                        math.hypot(tx-x,ty-y)/(MAX_PLATFORM_SPEED_PX_S*256.)*1000.)))
                    x,y=tx,ty
            piece['commands'].append(dict(t=round(starts[n],6),modifyprops=dict(property=41,
                decoded=dict(waypoint=dict(points=native_points)))))
            arrival=max(arrival,sum(p['dt'] for p in native_points)/1000.)
            arrivals.append(starts[n]+sum(p['dt'] for p in native_points)/1000.)
            continue
        points=[];last=start
        for p in path:
            dt=duration*math.dist(last,p)/max(1.,length)
            points.append(dict(xmp=int(p[0]),ymp=int(p[1]),dt=max(1,int(math.ceil(dt*1000.)))))
            last=p
        piece['commands'].append(dict(t=round(starts[n],6),modifyprops=dict(property=41,
            decoded=dict(waypoint=dict(points=points)))))
        arrivals.append(starts[n]+sum(p['dt'] for p in points)/1000.)
    return arrivals if individual else arrival

def append_transfer(session,pieces,entries,envelopes,at,head,previous=None,following=None,individual=False,limit_speed=False):
    """Reach the packet's pre-formation positions before applying its shape."""
    entries=[side_entry(p,(q['props']['wmp'],q['props']['hmp']),head,following) for q,p in zip(pieces,entries)] if following else entries
    return append_layout(session,pieces,entries,at,head,minimum_s=0.,envelopes=envelopes,
                         previous=previous,following=following,individual=individual,limit_speed=limit_speed)

def compile_course(session,plan,live_pieces=None,initial=True,exits=True):
    courses=session.pack.programs['courses'];first=courses[plan[0]['course']]
    source=live_pieces if live_pieces is not None else first['platforms']
    pieces=[dict(p,props=copy.deepcopy(p['props']),commands=[copy.deepcopy(c) for c in p['commands']
            if initial and c['t']<1.5]) for p in source];attacks=[];head_commands=[];forms=[]
    base=session.pack.templates['6']
    head=(base['xmp'],base['ymp'],base['wmp'],base['hmp'])

    cursor=0.
    previous_side='U' if initial or session.flow_side not in ('U','R','D','L') else session.flow_side
    for i,slot in enumerate(plan):
        src=courses[slot['course']];mode=slot['mode']
        key=(slot['course'],slot['start'],slot['end'],mode)
        cache=session.pack._timing_cache
        if key not in cache:cache[key]=mode_timing(src,slot,courses)
        timing=cache[key]
        native=slot_kinematics(session.pack,slot)
        refs,snapshot=geometry(session,src,slot,mode)
        proxies=[]
        for n,p in enumerate(pieces):
            q=dict(p,props=dict(p['props']));x,y=session._evaluate_profile_position(p,cursor)
            q['props'].update(xmp=x,ymp=y);q['_index']=n;proxies.append(q)
        pairs=session._match_slot_pieces(proxies,refs,repeat=True)
        targets=[None]*len(pieces);lanes=[None]*len(pieces)
        entries=[None]*len(pieces);transfer_curves=[None]*len(pieces);shape_curves=[None]*len(pieces)
        donor_ids=[None]*len(pieces)
        if mode=='nova':
            session._assign_nova_rings(slot)
        for ours,theirs in pairs:
            n=ours['_index'];target=(theirs['props']['xmp'],theirs['props']['ymp'])
            k=native[theirs['source_id']];entries[n]=k['entry'];transfer_curves[n]=k['transfer']
            shape_curves[n]=k['shape'];donor_ids[n]=theirs['source_id']
            lane=(slot.get('orbit_map') or {}).get(theirs['source_id'])
            if lane:
                lane=copy.deepcopy(lane);lanes[n]=lane
                target=session._ellipse_entry_point(dict(xmp=lane['xmp'],ymp=lane['ymp'],
                    radiusamp=lane['radius'],radiusbmp=lane['radius'],period=lane['period'],pos=lane['pos']))
            targets[n]=target
        if mode=='pachinko':
            targets=balanced_targets(pieces,targets,head,session.pack.scene['bounds'])
        if mode=='nova':
            groups={}
            for n,lane in enumerate(lanes):
                if lane:groups.setdefault((lane['xmp'],lane['ymp'],lane['radius'],lane['period']),[]).append(n)
            for key,indices in groups.items():
                for j,n in enumerate(indices):
                    lanes[n]['pos']=int(key[3]*j/len(indices))
                    targets[n]=session._ellipse_entry_point(dict(xmp=key[0],ymp=key[1],radiusamp=key[2],
                        radiusbmp=key[2],period=key[3],pos=lanes[n]['pos']))
        if initial and i==0:
            direction_arrival=1.5

            for p,original in zip(pieces,first['platforms']):
                for c in original['commands']:
                    if 1.5<=c['t']<timing['outgoing'] and (
                            'movetoxmp' in c or c.get('modifyprops',{}).get('property')==41):
                        p['commands'].append(copy.deepcopy(c))
            travel=timing['assembled']-direction_arrival
        else:
            entry_times=append_transfer(session,pieces,entries,transfer_curves,cursor,head,
                previous=previous_side,following=slot['side'],individual=True,limit_speed=mode=='nova')
            shape_times=append_layout(session,pieces,targets,entry_times,head,
                                      minimum_s=0.,envelopes=shape_curves,individual=True,limit_speed=mode=='nova')
            direction_arrival=statistics.median(entry_times)
            arrived=max(shape_times) if mode in ('pachinko','nova') else sorted(shape_times)[int((len(shape_times)-1)*.9)]
            travel=arrived-direction_arrival
        arrived=direction_arrival+travel
        attack_at=arrived+timing['lead_s']
        end=attack_at+timing['attack_s']
        if initial and i==0:
            end=min(end,43.)
            for p in pieces:p['commands']=[c for c in p['commands'] if c['t']<end]
        role='rise1' if initial and i==0 else ('coarse_' if initial else 'fine_')+slot['side']+str(i)
        forms.append(dict(t=cursor,side=slot['side'],mode=mode,role=role,settle_t=arrived,
                          direction_arrival_t=direction_arrival,attack_t=attack_at,end_t=end,
                          source_attack_s=timing['attack_s'],layout_source=slot['course'],snapshot_t=snapshot))
        if mode=='ladder':
            retraction=ladder_retraction(session.pack,slot,timing,slot['side'])
            if retraction:
                cloud,curves,delay=retraction;proxies=[]
                for n,p in enumerate(pieces):
                    q=dict(p,props=dict(p['props']));x,y=session._evaluate_profile_position(p,attack_at+delay)
                    q['props'].update(xmp=x,ymp=y);q['_index']=n;proxies.append(q)
                collapse=[None]*len(pieces);speeds=[None]*len(pieces)
                for ours,theirs in session._match_slot_pieces(proxies,cloud,repeat=True):
                    n=ours['_index'];collapse[n]=(theirs['props']['xmp'],theirs['props']['ymp']);speeds[n]=curves[theirs['source_id']]
                append_layout(session,pieces,collapse,attack_at+delay,head,minimum_s=0.,envelopes=speeds)
                forms[-1]['retract_t']=attack_at+delay
        for n,lane in enumerate(lanes):
            if lane:
                e=dict(xmp=lane['xmp'],ymp=lane['ymp'],radiusamp=lane['radius'],radiusbmp=lane['radius'],
                       period=lane['period'],pos=lane['pos'])
                pieces[n]['commands'].append(dict(t=arrived,modifyprops=dict(property=41,decoded=dict(elliptical=e))))

        payload=timing['attacks']
        elite_nova=mode=='nova' and (session.pack.entities.get('difficulty') or {}).get('nova_rate',1.)!=1.
        if elite_nova:
            from .difficulty import nova_payload
            payload=nova_payload(session.pack,timing,attack_at,end)+[
                a for a in timing['attacks'] if a['vid']!='scboss.gshot']
        if mode=='nova':
            payload=nova_for_side(session.pack,src,slot,payload)
        for a in payload:
            if elite_nova and a['vid']=='scboss.gshot':
                attacks.append(a)
                if (a['props'].get('move') or {}).get('to'):
                    head_commands.append(dict(t=max(arrived,a['t']-1.32),
                        modifyprops=dict(property=49,decoded=dict(animid=1,time=2112))))
                continue
            if timing['first']<=a['t']<=timing['finish']:
                if mode=='nova' and a['vid'] in ('scboss.vortex','scboss.hopper'):continue
                q=copy.deepcopy(a);q['t']=attack_at+(a['t']-timing['first'])
                if q['t']<=end:attacks.append(q)
        for c in src['head']:
            if timing['first']-timing['lead_s']<=c['t']<timing['finish']:
                if elite_nova and c.get('modifyprops',{}).get('property')==49:
                    continue
                q=copy.deepcopy(c);q['t']=attack_at+c['t']-timing['first']
                if q['t']<end:head_commands.append(q)
        cursor=end;previous_side=slot['side']
    if not exits:
        for p in pieces:p['commands'].sort(key=lambda c:c['t'])
        return dict(source=dict(synthesized=True),duration_s=cursor,platforms=pieces,attacks=attacks,
                    head=head_commands,formations=forms,synthesized=dict(tag='fine-state-owned',plan=plan))

    last=plan[-1];src=courses[last['course']]
    native_tail,departure,split=terminal_lanes(src,courses)
    forms.append(dict(t=cursor,side='D',mode='exit',role='exit_down',
                      source_departure_s=departure))
    split_at=cursor+split-departure
    forms.append(dict(t=split_at,side='LR',mode='exit',role='exit_split',
                      source_departure_s=departure,source_split_s=split))

    end=cursor+src['duration_s']-departure
    for n,p in enumerate(pieces):
        tail=native_tail[donor_ids[n]]
        for role in ('departure','split'):
            lane=tail[role];at=cursor+lane['t']-departure
            move=copy.deepcopy(lane['motion'].move)
            start=session._evaluate_profile_position(p,at);wp=move['waypoint']['points']
            first=(wp[0]['xmp'],wp[0]['ymp'])

            path=route(start,first,(p['props']['wmp'],p['props']['hmp']),head)
            duration=max(wp[0]['dt']/1000.,sum(math.dist(a,b) for a,b in zip([start]+path,path))
                         /(MAX_PLATFORM_SPEED_PX_S*256.))
            patched=[];length=sum(math.dist(a,b) for a,b in zip([start]+path,path));last_point=start
            for target in path:
                dt=duration*math.dist(last_point,target)/max(1.,length)
                patched.append(dict(xmp=int(target[0]),ymp=int(target[1]),dt=max(1,int(math.ceil(dt*1000)))))
                last_point=target
            wp[:1]=patched
            p['commands'].append(dict(t=round(at,6),modifyprops=dict(property=41,decoded=move)))
    for p in pieces:p['commands'].sort(key=lambda c:c['t'])
    return dict(source=dict(synthesized=True),duration_s=end,platforms=pieces,attacks=attacks,
                head=head_commands,formations=forms,synthesized=dict(tag='state-owned',plan=plan),
                exit_down_s=cursor,exit_split_s=split_at,second_shatter_s=end)
