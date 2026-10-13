"""Packet waypoint velocity envelopes and overlapping terminal lanes."""
import math
import statistics

from .motion import NativeMotion

def lanes(profile,course):
    offset=profile.get('source_born',course.get('start_s',0))-course.get('start_s',0)
    m=NativeMotion(profile['props']['xmp'],profile['props']['ymp']);result=[]
    for c in profile['commands']:
        t=c['t']+offset;mp=c.get('modifyprops') or {}
        if c.get('movetoxmp') is not None:
            if c.get('movetodt'):
                x,y=m.position(t)
                m.set(dict(to=dict(xmp=c['movetoxmp'],ymp=c['movetoymp'],dt=c['movetodt'],
                                   start=dict(xmp=x,ymp=y))),t,copy_move=False)
            else:m.correct(c['movetoxmp'],c.get('movetoymp'),t)
        elif mp.get('property')==41:m.set(mp.get('decoded') or {},t,copy_move=False)
        else:continue
        state=NativeMotion(*m.origin);state.move=m.move;state.at=m.at
        result.append(dict(t=t,motion=state,command=c))
    return result

def points(lane):
    move=lane['motion'].move
    return (move.get('waypoint') or {}).get('points') or ([move['to']] if move.get('to') else [])

def curve(lane,until=math.inf):
    x,y=lane['motion'].origin;at=lane['t'];result=[]
    for p in points(lane):
        dt=max(1,p.get('dt',1))/1000.;nx,ny=p.get('xmp',x),p.get('ymp',y)
        used=max(0.,min(at+dt,until)-at);distance=math.hypot(nx-x,ny-y)

        if dt>=.02 and used>0 and distance>1:result.append((distance/256./dt,used))
        at+=dt;x,y=nx,ny
        if at>=until:break
    return result

def _raw(course,lo,hi,first):
    result={}
    for p in course['platforms']:
        ls=lanes(p,course)
        candidates=[i for i,q in enumerate(ls) if q['t']<=first and
                    (points(q) or q['motion'].move.get('elliptical'))]
        if not candidates:continue
        i=candidates[-1]
        if ls[i]['motion'].move.get('elliptical'):
            before=[n for n in candidates[:-1] if points(ls[n])]
            if before:i=before[-1]
        incoming=ls[i];until=ls[i+1]['t'] if i+1<len(ls) else math.inf
        predecessors=[q for q in ls[:i] if len(points(q))>=12]
        transfer=curve(predecessors[-1],incoming['t']) if predecessors else curve(incoming,until)
        result[p['source_id']]=dict(entry=incoming['motion'].origin,shape=curve(incoming,until),
                                   transfer=transfer,shape_t=incoming['t'])
    return result

def slot_kinematics(pack,slot):
    key=(slot['course'],slot['start'],slot['end'],slot['mode'])
    cache=getattr(pack,'_kinematics_cache',None)
    if cache is None:cache=pack._kinematics_cache={}
    if key in cache:return cache[key]
    course=pack.programs['courses'][slot['course']];timing=pack._timing_cache[key]
    synth=course.get('synthesized') or {}
    if synth.get('mode')==slot['mode'] and synth.get('slot')==slot.get('slot'):
        base=next(c for c in pack.programs['courses'] if not c.get('synthesized') and c['pattern_id']==synth['base'])
        donor=next(c for c in pack.programs['courses'] if not c.get('synthesized') and c['pattern_id']==synth['donor'])
        first=min(a['t'] for a in base['attacks'] if slot['start']<=a['t']<slot['end']
                  and a['vid'] in ('scboss.dshot','scboss.rshot','scboss.gshot','scboss.vortex','scboss.plinkos'))
        result=_raw(base,slot['start'],slot['end'],first)
        n=synth['donor_slot'];lo=donor['formations'][n]['t']
        hi=donor['formations'][n+1]['t'];first=min(a['t'] for a in donor['attacks'] if lo<=a['t']<hi
            and a['vid'] in ('scboss.dshot','scboss.rshot','scboss.gshot','scboss.vortex','scboss.plinkos'))
        borrowed=list(_raw(donor,lo,hi,first).values())
        for i,q in enumerate(result.values()):q['shape']=borrowed[i%len(borrowed)]['shape']
    else:result=_raw(course,slot['start'],slot['end'],timing['first'])
    cache[key]=result
    return result

def paced_points(start,path,envelope,minimum_s=0.):
    """Map native scalar speeds onto the reconstructed route's arc length."""
    vertices=[start]+path;lengths=[math.dist(a,b) for a,b in zip(vertices,vertices[1:])]
    total=sum(lengths)
    if total<1:return [dict(xmp=int(path[-1][0]),ymp=int(path[-1][1]),dt=max(1,int(minimum_s*1000)))],minimum_s
    envelope=envelope or [(146.6,total/256./146.6)]
    native_distance=sum(v*dt*256. for v,dt in envelope)
    duration=sum(dt for v,dt in envelope)*total/native_distance
    scale=max(1.,minimum_s/max(.001,duration));duration*=scale
    distance=0.;time=0.;breaks=[]
    for v,dt in envelope:
        d=v*dt*256.*total/native_distance
        breaks.append((distance,d,time,dt*total/native_distance*scale));distance+=d;time+=breaks[-1][3]
    arcs=[];distance=0.
    for length in lengths:distance+=length;arcs.append(distance)
    cuts=sorted(set(arcs+[a+d for a,d,t,dt in breaks]));out=[];previous=0.;edge=0
    for distance in cuts:
        while edge<len(arcs)-1 and distance>arcs[edge]+1e-7:edge+=1
        origin=0. if edge==0 else arcs[edge-1]
        f=(distance-origin)/max(1e-9,lengths[edge]);a,b=vertices[edge],vertices[edge+1]
        x,y=a[0]+(b[0]-a[0])*f,a[1]+(b[1]-a[1])*f
        entry=next((q for q in breaks if distance<=q[0]+q[1]+1e-7),breaks[-1])
        arc,span,t,dt=entry;now=t+dt*min(1.,max(0.,(distance-arc)/span))
        out.append(dict(xmp=int(round(x)),ymp=int(round(y)),dt=max(1,int(math.ceil((now-previous)*1000)))))
        previous=now
    return out,duration

def terminal_lanes(course,courses=None):

    if course.get('synthesized') and courses:
        base=course['synthesized']['base']
        original=next(c for c in courses if not c.get('synthesized') and c['pattern_id']==base)
        return terminal_lanes(original)
    result={};departures=[];splits=[]
    for p in course['platforms']:
        ls=lanes(p,course)
        finals=[(i,q) for i,q in enumerate(ls) if len(points(q))==11 and
                course['duration_s']-13<q['t']<course['duration_s']]
        if not finals:continue
        i,split=finals[-1];previous=[q for q in ls[:i] if len(points(q))>=12]
        if not previous:continue
        departure=previous[-1];departures.append(departure['t']);splits.append(split['t'])
        result[p['source_id']]=dict(departure=departure,split=split)
    if len(result)<len(course['platforms'])/2:raise ValueError('incomplete terminal native lanes')
    return result,statistics.median(departures),statistics.median(splits)
