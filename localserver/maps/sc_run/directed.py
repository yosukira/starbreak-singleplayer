"""Seeded SC graph growth using fixed authored left/right door ports."""
import itertools
import random
from collections import deque
from .layout import (FIXED, THREE_PORT, ORDINARY, MIDS, CHESTS, ENCOUNTERS,
                     stable_seed, ports, four_way_sides, _fit, _guardian, embed, validate)

OPPOSITE = dict(left='right', right='left')


class DirectionMismatch(ValueError):
    pass


def generate(content, seed, exploration_rooms=None, *, fit=None):
    config = _fit(content, exploration_rooms, fit)
    required = set(FIXED+THREE_PORT+MIDS+CHESTS+('scstart01', 'sccore', 'scboss'))
    if required-set(content.templates):
        raise ValueError('SC authored content missing: '+', '.join(sorted(required-set(content.templates))))
    # All six Chest entrances and four Mid entrances are balanced (5L/5R).
    # The mandatory two-left-door Loop needs a corresponding right-door turn.
    # Select only authored optional-room sets with balanced ordinary ports.
    available = [d for d in ORDINARY if d in content.templates]
    signatures = {}
    for descriptor in FIXED+THREE_PORT+tuple(available):
        signatures[descriptor] = sum(1 if p['side']=='left' else -1
            for p in ports(content.recipe(descriptor, 0)) if p['kind']!='core')
    extra = config['exploration_rooms']-len(FIXED)-len(THREE_PORT)
    fixed_delta = sum(signatures[d] for d in FIXED+THREE_PORT)
    choices = [row for row in itertools.combinations(available, extra)
               if fixed_delta+sum(signatures[d] for d in row)==0]
    if not choices:
        raise ValueError('SC exploration budget has no balanced authored left/right ports')
    for attempt in range(64):
        rng = random.Random(stable_seed(seed, 'directed-doors:'+str(attempt)))
        try:
            return _attempt(content, seed, config, rng.choice(choices), rng)
        except DirectionMismatch:
            continue
    raise ValueError('SC could not connect fixed door directions for this seed')


def _attempt(content, seed, config, optional, rng):
    rooms, free, edges, arm_of, parents = {}, {}, [], {}, {}

    def put(role, descriptor, category):
        room = content.recipe(descriptor, stable_seed(seed, role))
        room.update(role=role, links={})
        room['metadata'].update(template=descriptor, layout_category=category)
        room['entries'] = {('door_'+str(k).split(':',1)[1] if str(k).startswith('door:') else k): v
                           for k,v in room.get('entries',{}).items()}
        room['entries'].setdefault('default', list(room['spawn']))
        for p in room['metadata']['ports']:
            oid = int(p.get('id',p.get('objid',0)))
            p['entry'] = 'door_'+str(oid)
            if p['entry'] not in room['entries']:
                raise ValueError('SC authored port lacks an arrival: '+role+'/'+str(oid))
            room['objects'][oid].update(interact=0, trigger=0)
        rooms[role] = room
        free[role] = {p['id'] for p in ports(room)}
        return room

    def normal(role):
        return [p for p in ports(rooms[role]) if p['id'] in free[role] and p['kind']!='core']

    def four_way(role):
        return bool(four_way_sides(rooms[role]))

    def connection(a,pa,b,pb):
        if OPPOSITE.get(pa['side'])!=pb['side']:return []
        if not (four_way(a) and four_way(b)):return [(pa,pb)]
        # Adjacent four-way rooms share an entire side: upper to upper,
        # lower to lower. Never spend one of those ports on another room.
        sides=[sorted((p for p in normal(n) if p['side']==side),key=lambda p:p['at'][1])
               for n,side in ((a,pa['side']),(b,pb['side']))]
        if any(len(ps)!=2 for ps in sides):return []
        pairs=list(zip(*sides))
        return pairs if any(p['id']==pa['id'] and q['id']==pb['id'] for p,q in pairs) else []

    def wire(a, pa, b, pb, gate=None):
        core = pa['kind']=='core' and pb['kind']=='core'
        if not core and OPPOSITE.get(pa['side'])!=pb['side']:
            raise DirectionMismatch('SC ordinary doors must connect left to right')
        free[a].remove(pa['id']);free[b].remove(pb['id'])
        required_flags = []
        for src,dst,p,q,required in ((a,b,pa,pb,gate),(b,a,pb,pa,None)):
            # Native combat Mid entrance doors also reopen only on victory.
            if rooms[src]['objects'][p['id']].get('vid')=='sc.cdoor':
                required = 'mid_cleared'
            link = dict(destination=dst,entry=q['entry'],trigger='interact')
            if required:link['requires']=required
            rooms[src]['links'][p['id']]=link
            rooms[src]['objects'][p['id']]['interact']=0 if required else 1
            required_flags.append(required)
        edges.append(dict(a=a,b=b,a_port=pa['id'],b_port=pb['id'],
                          requires=gate,a_requires=required_flags[0],b_requires=required_flags[1]))

    def directed():
        result = {n:[] for n,r in rooms.items() if r['metadata']['layout_category'] not in ('gen','boss')}
        for e in edges:
            a,b=e['a'],e['b']
            if a not in result or b not in result:continue
            p=next(p for p in ports(rooms[a]) if p['id']==e['a_port'])
            src,dst=(a,b) if p['side']=='right' else (b,a)
            result[src].append(dst)
        return result

    put('entry','scstart01','hall')
    put('engine','scmid17','exploration')
    put('statue','scmid18','exploration')
    descriptors = [d for d in FIXED+THREE_PORT+tuple(optional) if d not in ('scmid17','scmid18')]
    rng.shuffle(descriptors)
    for index,descriptor in enumerate(descriptors):
        put('path_%d_%d'%(index//4,index%4+1),descriptor,'exploration')
    remaining = [n for n in rooms if n!='entry']
    connected = ['entry']
    hall_order = sorted(ports(rooms['entry']),key=lambda p:(p['side'],p['at'][1]))
    hall_arm = {p['id']:i for i,p in enumerate(hall_order)}
    arm_roots = {}
    while remaining:
        frontier = [(n,p) for n in connected for p in normal(n)]
        hall = [(n,p) for n,p in frontier if n=='entry']
        if hall:frontier=hall  # All hall ports lead out; paired routes may share a room.
        candidates = [(a,pa,b,pb) for a,pa in frontier for b in remaining for pb in normal(b)
                      if (pairs:=connection(a,pa,b,pb)) and
                      len(edges)+len(pairs)-len(connected)<=1]
        if not candidates:raise DirectionMismatch('SC port growth stranded an authored room')
        a,pa,b,pb = rng.choice(candidates)
        pairs=connection(a,pa,b,pb)
        for source,destination in pairs:
            wire(a,source,b,destination)
            if a=='entry':arm_roots[hall_arm[source['id']]]=b
        arm_of[b] = hall_arm[pa['id']] if a=='entry' else arm_of[a]
        parents[b]=a;connected.append(b);remaining.remove(b)

    # Reserve one opposite-port connection to form the local single cycle.
    # Reject a directed cycle: every right-door destination must be spatially
    # to its right, which is impossible if those inequalities form a cycle.
    adjacency = directed()
    def reaches(start,target):
        seen={start};pending=[start]
        while pending:
            for n in adjacency[pending.pop()]:
                if n==target:return True
                if n not in seen:seen.add(n);pending.append(n)
        return False
    existing = {frozenset((e['a'],e['b'])) for e in edges}
    right = [(n,p) for n in connected for p in normal(n) if p['side']=='right']
    left = [(n,p) for n in connected for p in normal(n) if p['side']=='left']
    if len(edges)==len(connected)-1:
        pairs = [(a,pa,b,pb) for a,pa in right for b,pb in left
                 if a!=b and frozenset((a,b)) not in existing and not reaches(b,a)
                 and len(connection(a,pa,b,pb))==1]
        if not pairs:raise DirectionMismatch('SC remaining door ports cannot close a spatially consistent cycle')
        wire(*rng.choice(pairs))

    leaves = [('mid_'+ENCOUNTERS[d],d,'mid') for d in MIDS]
    leaves += [('chest_'+str(i),d,'chest') for i,d in enumerate(CHESTS)]
    rng.shuffle(leaves)
    for role,descriptor,category in leaves:
        room=put(role,descriptor,category)
        entrance=normal(role)
        if len(entrance)!=1:raise ValueError('SC Mid/Chest must have one ordinary entrance')
        entrance=entrance[0]
        slots=[(n,p) for n in connected for p in normal(n) if p['side']==OPPOSITE[entrance['side']]]
        if not slots:raise DirectionMismatch('SC leaves cannot match their fixed entrance sides')
        parent,door=rng.choice(slots)
        wire(parent,door,role,entrance)
        arm_of[role]=arm_of.get(parent,hall_arm.get(door['id'],0))
        parents[role]=parent
        if category=='mid':
            index=MIDS.index(descriptor)
            room['metadata'].update(encounter_kind=ENCOUNTERS[descriptor],mid_index=index)
            gen=put('gen_'+str(index),'sccore','gen')
            gen['metadata'].update(gen_index=index,encounter_kind='gen')
            a=next(p for p in ports(room) if p['kind']=='core')
            b=next(p for p in ports(gen) if p['kind']=='core')
            wire(role,a,gen['role'],b,gate='mid_cleared')
            arm_of[gen['role']]=arm_of[role];parents[gen['role']]=role
        else:_guardian(room)
    if any(free.values()):raise DirectionMismatch('SC authored door ports remain unmatched')
    put('boss','scboss','boss')['metadata']['encounter_kind']='boss'

    numbered=[];seen={'entry'};queue=deque(['entry'])
    while queue:
        n=queue.popleft();numbered.append(rooms[n])
        for oid,link in sorted(rooms[n]['links'].items()):
            dst=link['destination']
            if dst not in seen and rooms[dst]['metadata']['layout_category'] not in ('gen','boss'):
                seen.add(dst);queue.append(dst)
    for index,room in enumerate(numbered):
        room['metadata']['main_index']=index
        room['world']['worldprops_']['floor_']=index
    for room in rooms.values():
        if room['metadata']['layout_category'] in ('gen','boss'):
            room['world']['worldprops_']['floor_']=0

    # Longest-path columns preserve every authored right->left inequality.
    adjacency=directed();indegree={n:0 for n in adjacency};levels={n:0 for n in adjacency}
    for targets in adjacency.values():
        for n in targets:indegree[n]+=1
    queue=deque(sorted(n for n,d in indegree.items() if d==0));visited=0
    while queue:
        n=queue.popleft();visited+=1
        for dst in adjacency[n]:
            levels[dst]=max(levels[dst],levels[n]+1);indegree[dst]-=1
            if indegree[dst]==0:queue.append(dst)
    if visited!=len(adjacency):raise DirectionMismatch('SC ordinary door directions form a cycle')
    shift=levels['entry'];cells={};occupied=set()
    def allocate(n,column,wanted):
        row=wanted
        while (column,row) in occupied:row+=1
        cells[n]=(column,row);occupied.add((column,row))
        rooms[n]['metadata']['cell']=[column,row]
    for room in numbered:
        n=room['role'];arm=arm_of.get(n)
        allocate(n,levels[n]-shift,0 if arm is None else (-6,6,-3,3)[arm])
    for n,r in rooms.items():
        if r['metadata']['layout_category']=='gen':
            parent=parents[n];x,y=cells[parent];allocate(n,x,y+1)
    allocate('boss',0,max(y for x,y in cells.values())+3)
    plan=dict(schema='sc-level-layout-v4',seed=int(seed),rooms=rooms,cells=cells,edges=edges,
              local_layout_fit=config,
              generation_policy='USER fixed room coverage and authored door directions; FIT room budget, growth, numbering and single-cycle choice',
              topology=dict(main_room_count=len(numbered),independent_gen_count=4,independent_boss_count=1,
                            direction_rule='All ordinary doors connect right to left without mirroring rooms',
                            four_way_rule='Adjacent four-way rooms connect both upper and lower doors in matching order',
                            arms=[dict(index=i,entry_port=hall_order[i]['id'],root=arm_roots[i],
                                       rooms=[n for n in connected if n!='entry' and
                                              arm_of[n]==arm_of[arm_roots[i]]])
                                  for i in range(4)]))
    embed(plan);validate(plan)
    return plan
