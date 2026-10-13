'Bounded composition of lossless native EST open-platform modules.'
import copy
import hashlib
import json
import math
from pathlib import Path
import random

from ..egy_run.generator import relocate, refs

EPS = 1e-7

def geometry_signature(objects):
    return sorted((oid, p.get('vid', ''), *(p.get(k, 0) for k in
                   ('xmp', 'ymp', 'wmp', 'hmp', 'terraintype', 'shapetype')))
                  for oid, p in objects.items() if p.get('terraintype') in (1, 2))

def geometry_sha(objects):
    return hashlib.sha256(json.dumps(geometry_signature(objects), separators=(',', ':')).encode()).hexdigest()

def overlap(a, b):
    return a[0] < b[0]+b[2] and a[0]+a[2] > b[0] and a[1] < b[1]+b[3] and a[1]+a[3] > b[1]

def rectangle(p):
    return tuple(p.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))

def _free_vertical(t, initial_velocity, profile):
    g, cap = profile['gravity_mp_s2'], profile['terminal_mp_s']
    limit = (cap-initial_velocity)/g
    if t <= limit:
        return initial_velocity*t+.5*g*t*t
    return initial_velocity*limit+.5*g*limit*limit+cap*(t-limit)

def release_velocity(velocity, profile):
    """PlayerCharacter::endJump, once on JUMP unhit (raw -5000/+30000).

    Native raw velocity is mp*256/ms. Do not clamp to zero or multiply by
    a jump-height percentage. Action handlers may consume input; witnesses
    assume ordinary unhurt movement with no active movement item/action.
    """
    floor = profile.get('jump_release_floor_mp_s', 5000*1000/256)
    boost = profile.get('jump_release_boost_mp_s', 30000*1000/256)
    return min(velocity+boost, -floor) if velocity < -floor else velocity

def vertical(t, profile, jump_hold_s=None):
    velocity = -profile['jump_mp_s']
    if jump_hold_s is None or t <= jump_hold_s:
        return _free_vertical(t, velocity, profile)
    h = max(0., jump_hold_s)
    position = _free_vertical(h, velocity, profile)
    released = release_velocity(min(velocity+profile['gravity_mp_s2']*h,
                                    profile['terminal_mp_s']), profile)
    return position+_free_vertical(t-h, released, profile)

def vertical_velocity(t, profile, jump_hold_s=None):
    g, cap = profile['gravity_mp_s2'], profile['terminal_mp_s']
    velocity = -profile['jump_mp_s']
    if jump_hold_s is not None and t >= jump_hold_s:
        velocity = release_velocity(min(velocity+g*jump_hold_s, cap), profile)
        t -= jump_hold_s
    return min(velocity+g*t, cap)

def apex_time(profile, jump_hold_s=None):
    original = profile['jump_mp_s']/profile['gravity_mp_s2']
    if jump_hold_s is None or jump_hold_s >= original:
        return original
    released = release_velocity(-profile['jump_mp_s']+
                                profile['gravity_mp_s2']*jump_hold_s, profile)
    return jump_hold_s+max(0., -released)/profile['gravity_mp_s2']

def _descending_time(delta_y, initial_velocity, profile):
    g, cap = profile['gravity_mp_s2'], profile['terminal_mp_s']
    discriminant = initial_velocity*initial_velocity+2*g*delta_y
    if discriminant < 0:
        return None
    result = (-initial_velocity+math.sqrt(max(0., discriminant)))/g
    if result < -EPS:
        return None
    limit = (cap-initial_velocity)/g
    if result > limit:
        result = limit+(delta_y-_free_vertical(limit, initial_velocity, profile))/cap
    return result

def landing_time(delta_y, profile, jump_hold_s=None):
    """First descending crossing after one optional native jump release."""
    initial = -profile['jump_mp_s']
    original = _descending_time(delta_y, initial, profile)
    if jump_hold_s is None or (original is not None and original <= jump_hold_s):
        return original
    h = max(0., jump_hold_s)
    position = _free_vertical(h, initial, profile)
    velocity = release_velocity(min(initial+profile['gravity_mp_s2']*h,
                                    profile['terminal_mp_s']), profile)
    remaining = _descending_time(delta_y-position, velocity, profile)
    return None if remaining is None else h+remaining

def _x(t, start, speed, stop):
    return start+speed*min(t, stop)

def _horizontal_times(start, speed, stop, end, left, right):
    intervals = []
    if stop > 0 and speed:
        first, last = sorted(((left-start)/speed, (right-start)/speed))
        lo, hi = max(0., first), min(stop, end, last)
        if hi-lo > EPS:
            intervals.append((lo, hi))
    stationary = _x(stop, start, speed, stop)
    if left+EPS < stationary < right-EPS and end-max(0., stop) > EPS:
        intervals.append((max(0., stop), end))
    return intervals

def _clear_path(objects, source, target, start_x, speed, stop, duration, profile, jump_hold_s=None):
    width, height = profile['width_mp'], profile['height_mp']
    source_y = source.get('ymp', 0)
    apex = apex_time(profile, jump_hold_s)
    for oid, p in objects.items():
        terrain = p.get('terraintype')
        if terrain not in (1, 2):
            continue

        if p.get('vid') in ('jungle.stonepusher', 'jungle.turtlebody'):
            continue
        px, py, pw, ph = rectangle(p)
        if not pw or not ph:
            continue
        if terrain == 1:
            for lo, hi in _horizontal_times(start_x, speed, stop, duration, px-width, px+pw):
                times = [lo, hi, max(lo, min(hi, apex))]
                if jump_hold_s is not None and lo < jump_hold_s < hi:
                    times.append(jump_hold_s)
                values = [source_y+vertical(t, profile, jump_hold_s) for t in times]

                if max(values) > py+EPS and min(values) < py+ph+height-EPS:
                    return False
        else:
            crossed = landing_time(py-source_y, profile, jump_hold_s)
            if crossed is None or crossed <= EPS or crossed >= duration-EPS:
                continue
            x = _x(crossed, start_x, speed, stop)
            if x < px+pw-EPS and x+width > px+EPS:
                return False
    return True

def jump_witness(objects, source_id, target_id, profile):
    """One jump with bounded optional JUMP release, plus directional hold.

    Horizontal hold uses idealized catalog top speed; first-frame input and
    acceleration timing still need native posterior checks. The result tests
    continuous body/solid intersection plus earlier one-way landings, and
    does not claim optimal native path planning.
    """
    if any(profile.get(key, 0) <= 0 for key in ('width_mp', 'height_mp', 'walk_mp_s',
           'jump_mp_s', 'gravity_mp_s2', 'terminal_mp_s')):
        raise ValueError('EST mobility profile needs positive explicit collider/physics parameters')
    if source_id == target_id:
        return None
    source, target = objects[source_id], objects[target_id]
    if source.get('vid') != 'jungle.ledge' or target.get('vid') != 'jungle.ledge':
        return None
    width = profile['width_mp']
    margin = profile.get('landing_inset_mp', 256)
    a = (source.get('xmp', 0)+margin, source.get('xmp', 0)+source.get('wmp', 0)-width-margin)
    b = (target.get('xmp', 0)+margin, target.get('xmp', 0)+target.get('wmp', 0)-width-margin)
    if a[1] < a[0] or b[1] < b[0]:
        return None
    delta = target.get('ymp', 0)-source.get('ymp', 0)
    if delta > profile.get('max_drop_mp', 768*256):
        return None
    maximum_duration = landing_time(delta, profile)
    if maximum_duration is None or maximum_duration <= EPS:
        return None
    capacity = profile['walk_mp_s']*maximum_duration
    if max(0., b[0]-a[1], a[0]-b[1]) > capacity:
        return None
    starts = sorted({a[0], a[1], (a[0]+a[1])/2, max(a[0], min(a[1], (b[0]+b[1])/2))})
    ends = sorted({b[0], b[1], (b[0]+b[1])/2})
    obstacles = objects
    if profile.get('jump_mode') == 'variable-release':

        left, right = min(a[0], b[0]), max(a[1], b[1])+width
        bottom = max(source.get('ymp', 0), target.get('ymp', 0))
        top = source.get('ymp', 0)+vertical(apex_time(profile), profile)-profile['height_mp']
        obstacles = {oid: p for oid, p in objects.items() if p.get('terraintype') in (1, 2) and
            p.get('xmp', 0) < right and p.get('xmp', 0)+p.get('wmp', 0) > left and
            p.get('ymp', 0) < bottom+EPS and p.get('ymp', 0)+p.get('hmp', 0) > top}
    for hold in jump_hold_candidates(profile):
        duration = landing_time(delta, profile, hold)
        if duration is None or duration <= EPS:
            continue
        for start in starts:
            for end in ends:
                dx = end-start
                stop = abs(dx)/profile['walk_mp_s']
                if stop > duration+EPS:
                    continue
                speed = math.copysign(profile['walk_mp_s'], dx) if abs(dx) > EPS else 0.
                if not _clear_path(obstacles, source, target, start, speed, stop, duration, profile, hold):
                    continue
                if profile.get('jump_mode', 'full-held') == 'full-held':
                    return dict(source=source_id, target=target_id, takeoff_left_mp=round(start),
                        landing_left_mp=round(end), flight_s=duration, horizontal_speed_mp_s=speed,
                        hold_horizontal_s=stop, hold_jump_s=duration,
                        action='jump + directional hold, then release direction',
                        evidence='D catalog velocities/collider; FIT gravity conversion; SIM bounded witness search')
                return dict(source=source_id, target=target_id, takeoff_left_mp=start,
                    landing_left_mp=end, flight_s=duration, horizontal_speed_mp_s=speed,
                    hold_horizontal_s=stop, hold_jump_s=duration if hold is None else min(hold, duration),
                    jump_hold_s=hold, jump_release=hold is not None and hold < duration,
                    action='jump + directional hold, optional native JUMP release, then release direction',
                    evidence='D C++ jump-release constants; D/FIT explicit profile physics; SIM finite action search')
    return None

def jump_hold_candidates(profile):

    if profile.get('jump_mode', 'full-held') == 'full-held':
        return [None]
    if profile['jump_mode'] != 'variable-release':
        raise ValueError('unsupported EST jump_mode')
    step = profile.get('jump_hold_step_s', .016)
    minimum = profile.get('min_jump_hold_s', .016)
    if step <= 0 or minimum <= 0:
        raise ValueError('EST jump release search needs positive hold bounds')
    apex = profile['jump_mp_s']/profile['gravity_mp_s2']
    count = math.ceil(apex/step)
    if count > 256:
        raise ValueError('EST jump release search exceeds256 hold candidates')

    return [None]+[minimum+i*step for i in range(count) if minimum+i*step < apex]

def support_graph(objects, profile, ids=None):
    nodes = sorted(ids if ids is not None else (oid for oid, p in objects.items()
                   if p.get('vid') == 'jungle.ledge'))
    adjacency = {oid: [] for oid in nodes}
    witnesses = []
    for source in nodes:
        for target in nodes:
            witness = jump_witness(objects, source, target, profile)
            if witness:
                adjacency[source].append(target)
                witnesses.append(witness)
    return dict(nodes=nodes, edges=adjacency, witnesses=witnesses,
        method=('directed full-jump/landing graph; continuous solid sweep; no occupancy flood'
                if profile.get('jump_mode', 'full-held') == 'full-held' else
                'directed variable-release jump/landing graph; continuous solid sweep; no occupancy flood'),
        limits=['no double jump/dash/jetpack/platform ability', 'dynamic hazard/mover phases excluded',
                'finite takeoff/landing samples; omitted edge is not proof of native impossibility'])

def reachable(graph, start):
    result = {start}
    pending = [start]
    while pending:
        for nxt in graph['edges'].get(pending.pop(), []):
            if nxt not in result:
                result.add(nxt)
                pending.append(nxt)
    return result

def preservation_check(before, after, support, profiles, radius_mp=640*256):
    origin = before[support]
    x, y, w, _ = rectangle(origin)
    ids = sorted(oid for oid, p in before.items() if p.get('vid') == 'jungle.ledge' and
                 abs(p.get('xmp', 0)+p.get('wmp', 0)/2-(x+w/2)) <= radius_mp and
                 abs(p.get('ymp', 0)-y) <= radius_mp)
    results = []
    for profile in profiles:
        old = support_graph(before, profile, ids)
        new = support_graph(after, profile, ids)
        lost = [(a, b) for a in ids for b in reachable(old, a) if b not in reachable(new, a)]
        old_interval = [x, x+w-profile['width_mp']]
        p = after[support]
        new_interval = [p.get('xmp', 0), p.get('xmp', 0)+p.get('wmp', 0)-profile['width_mp']]
        contained = new_interval[0] <= old_interval[0] <= old_interval[1] <= new_interval[1]
        results.append(dict(profile=profile['id'], nodes=len(ids),
            before_edges=len(old['witnesses']), after_edges=len(new['witnesses']),
            lost_reachable_pairs=lost, original_standing_interval_mp=old_interval,
            new_standing_interval_mp=new_interval, standing_interval_contained=contained,
            support_outgoing_before=[e for e in old['witnesses'] if e['source'] == support],
            support_incoming_before=[e for e in old['witnesses'] if e['target'] == support]))
    return dict(ok=all(not r['lost_reachable_pairs'] and r['standing_interval_contained'] for r in results),
                profiles=results, radius_mp=radius_mp)

def expansion_valid(objects, old_id, new_props, protected):
    old = objects[old_id]
    x, y, w, h = rectangle(old)
    nx, ny, nw, nh = rectangle(new_props)
    if old.get('vid') != 'jungle.ledge' or new_props.get('vid') != 'jungle.ledge':
        return False, 'not native ledge family'
    if old.get('terraintype') != 2 or new_props.get('terraintype') != 2 or y != ny or h != nh:
        return False, 'support height/type changed'
    if not nx <= x < x+w <= nx+nw or nw <= w:
        return False, 'old support is not strictly contained'
    for oid, p in objects.items():
        if oid == old_id:
            continue
        if p.get('terraintype') in (1, 2) and overlap(rectangle(new_props), rectangle(p)):
            return False, 'expanded ledge overlaps native terrain: '+str(oid)
        if p.get('terraintype') in (1, 2):
            px, py, pw, ph = rectangle(p)
            new_contact = nx <= px+pw and nx+nw >= px and ny <= py+ph and ny+nh >= py
            old_contact = x <= px+pw and x+w >= px and y <= py+ph and y+h >= py

            if new_contact and not old_contact:
                return False, 'new native tile contact: '+str(oid)
        if oid in protected:
            px, py, pw, ph = rectangle(p)
            if pw and ph and overlap(rectangle(new_props), (px-128*256, py-128*256, pw+256*256, ph+256*256)):
                return False, 'expanded ledge enters protected navigation collar: '+str(oid)
    return True, None

def interface_envelope(objects, support, candidate, profiles, radius):
    """Bound every static arc checked by a compiled local interface proof.

    Other selected supports must be outside this volume; a separate successful
    single-replacement proof alone cannot authorize an interacting combination.
    Dynamic enemies/movers and full-room routes remain outside this claim.
    """
    origin=objects[support];x,y,w,_=rectangle(origin)
    ids={oid for oid,p in objects.items() if p.get('vid')=='jungle.ledge' and
         abs(p.get('xmp',0)+p.get('wmp',0)/2-(x+w/2))<=radius and
         abs(p.get('ymp',0)-y)<=radius}
    terrain=[objects[oid] for oid in ids]+[candidate]
    left=min(p.get('xmp',0) for p in terrain)-1
    right=max(p.get('xmp',0)+p.get('wmp',0) for p in terrain)+1
    rise=max(p['jump_mp_s']**2/(2*p['gravity_mp_s2']) for p in profiles)
    top=min(p.get('ymp',0) for p in terrain)-rise-max(p['height_mp'] for p in profiles)-1
    bottom=max(p.get('ymp',0) for p in terrain)+1
    return dict(nodes=sorted(ids),rect=[left,top,right-left,bottom-top])

class EstOpenModuleGenerator:
    def __init__(self, root, pool_file='pool.json'):
        self.root = Path(root)
        self.data = json.loads((self.root/'modules'/pool_file).read_text(encoding='utf8'))
        if self.data.get('schema') != 'est-native-open-modules-v1':
            raise ValueError('unsupported EST open-module schema')
        canonical = {k: v for k, v in self.data.items() if k != 'content_version'}
        version = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        if version != self.data.get('content_version'):
            raise ValueError('EST open-module content hash mismatch')
        self.modules = self.data['modules']
        config_path=self.root/'modules/enabled.json'
        config=json.loads(config_path.read_text(encoding='utf8')) if config_path.exists() else {}
        self.max_replacements=config.get('max_replacements',1)
        if not isinstance(self.max_replacements,int) or not 1<=self.max_replacements<=2:
            raise ValueError('EST preview permits one or two independent replacements')
        self.versions=dict(pool=self.data['content_version'],
            adapter=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            activation_config=hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.exists() else None)

    def generate(self, room, seed):
        room = copy.deepcopy(room)
        if room['role'] in ('mid', 'boss'):
            return room
        seed = int(seed) & 0x7fffffff
        recipes = self.data['recipes'].get(room['role'])
        if not recipes:
            raise ValueError('no verified EST open-module recipe for '+room['role'])
        objects = room['objects']
        original_objects=copy.deepcopy(objects)
        creators = room.get('creators', {})
        protected = set(self.data['protected_ids'][room['role']])
        rng = random.Random('est-open-modules-v1:%s:%s' % (room['role'], int(seed)))
        choices = [(recipe, choice) for recipe in recipes for choice in recipe['choices']]
        rng.shuffle(choices)
        attempts = [];replacements=[];interfaces=[];used=set()
        original_geometry = geometry_sha(objects)
        for recipe, choice in choices:
            removed = set(recipe['remove_ids'])
            if removed&used:continue
            if removed & protected:
                raise ValueError('compiled EST module modifies protected progress objects')
            if any(original_objects.get(int(k)) != p for k, p in recipe['expected_objects'].items()):
                raise ValueError('EST base differs from compiled native module source')
            if any(ref in removed for oid, p in objects.items() if oid not in removed for ref in refs(p)):
                raise ValueError('EST removal has an external reference')
            if any((int(oid) in removed) != (int(parent) in removed) for oid, parent in creators.items()):
                raise ValueError('EST removal crosses creator ownership')
            module = self.modules[choice['module']]
            native = {int(k): p for k, p in module['objects'].items()}
            support = int(recipe['support_id'])
            mapping = {int(module['support_id']): support}
            next_id = max(objects)+1000
            for oid in sorted(native):
                if oid not in mapping:
                    mapping[oid] = next_id
                    next_id += 1
            dx, dy = choice['translation_mp']
            added = {mapping[oid]: relocate(p, dx, dy, mapping) for oid, p in native.items()}
            valid, reason = expansion_valid(objects, support, added[support], protected)
            if not valid:
                attempts.append(dict(recipe=recipe['id'], module=choice['module'], rejected=reason))
                continue
            radius=choice['interface_proof']['local_directed_jump_check']['radius_mp']
            envelope=interface_envelope(original_objects,support,added[support],self.data['profiles'],radius)
            if any(support in old['envelope']['nodes'] or old['support'] in envelope['nodes'] or
                   overlap(rectangle(added[support]),old['envelope']['rect']) or
                   overlap(old['new_rect'],envelope['rect']) for old in interfaces):
                attempts.append(dict(recipe=recipe['id'],module=choice['module'],rejected='interacts with an already selected static interface'))
                continue
            for oid in removed:
                objects.pop(oid)
                creators.pop(oid, None)
            objects.update(added)
            for oid, parent in module.get('creators', {}).items():
                creators[mapping[int(oid)]] = mapping[int(parent)]
            if any(ref not in objects for p in objects.values() for ref in refs(p)):
                raise ValueError('EST generated dangling native object reference')
            used.update(removed)
            interfaces.append(dict(support=support,envelope=envelope,new_rect=rectangle(added[support])))
            replacements.append(dict(recipe=recipe['id'],module=choice['module'],
                source=module['source'],object_id_map=mapping,translation_mp=[dx,dy],
                removed_ids=sorted(removed),added_ids=sorted(added),support_id=support,
                interface_proof=choice['interface_proof']))
            if len(replacements)<self.max_replacements:continue
            break
        if replacements:
            final_geometry = geometry_sha(objects)
            if final_geometry == original_geometry:
                raise ValueError('EST module grammar promised geometry change but changed none')
            room['metadata'].update(max_objid=max(objects), dynamic_id_start=max(objects)+1000)
            original_native_seed = room['world'].get('worldprops_', {}).get('seed_')
            room['world'].setdefault('worldprops_', {})['seed_'] = seed
            room['metadata']['generation'] = dict(mode='native-open-support-module', seed=int(seed),
                original_native_seed=original_native_seed,
                content_version=hashlib.sha256(json.dumps(self.versions,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
                component_versions=self.versions,pool_version=self.data['content_version'],base_variant=room['variant'],
                geometry_changed=True, base_geometry_sha=original_geometry, geometry_sha=final_geometry,
                replacements=replacements,rejected_attempts=attempts,
                joint_interface_proof=dict(method='Each changed support lies outside every other selected local static arc envelope',
                    interfaces=interfaces,limits='D/FIT compiled collider and jump profiles; not live combat, dynamic movers, or full-room reachability'),
                evidence='D full native module Properties; FIT support closure; SIM uniform bounded grammar',
                limits=self.data['limits'])
            room['variant'] += '-open-generated-'+final_geometry[:12]
            return room
        raise ValueError('EST open-module composition rejected every choice: '+str(attempts))
