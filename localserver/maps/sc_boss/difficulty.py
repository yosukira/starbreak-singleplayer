"""Optional SC difficulty modifiers; the normal encounter is the identity."""
import copy
import math
import statistics

PROJECTILES = frozenset(('scboss.sshot', 'scboss.dshot', 'scboss.rshot',
                        'scboss.gshot', 'scboss.plinkos', 'scboss.bomb', 'scboss.bits'))

def scaled_int(value, factor):

    return int(math.floor(value * factor + .5))

def scale_stats(props, hp=1., damage=1.):
    result = copy.deepcopy(props)
    for key in ('damage', 'hitboxdamage'):
        if result.get(key, 0) > 0:
            result[key] = scaled_int(result[key], damage)
    for key in ('hitpoints', 'maxhitpoints'):
        if result.get(key, 0) > 0:
            result[key] = scaled_int(result[key], hp)
    return result

def scale_move(move, speed):
    """Time-dilate a lane, including acceleration and terminal velocities."""
    result = copy.deepcopy(move)
    velocity = result.get('vel') or {}
    for key, value in velocity.items():
        if key.endswith('vel'):
            velocity[key] = scaled_int(value, speed)
        elif key.endswith('accel'):
            velocity[key] = scaled_int(value, speed * speed)
    timed = list((result.get('waypoint') or {}).get('points') or [])
    if result.get('to'):
        timed.append(result['to'])
    for point in timed:
        if 'dt' in point:
            point['dt'] = max(1, scaled_int(point['dt'], 1. / speed))
        if 'pos' in point:
            point['pos'] = scaled_int(point['pos'], 1. / speed)
    ellipse = result.get('elliptical') or {}
    for key in ('period', 'pos'):
        if key in ellipse:
            ellipse[key] = max(1 if key == 'period' else 0,
                               scaled_int(ellipse[key], 1. / speed))
    return result

def actor_profile(profile, rules, platform=False):
    modifiers = rules.get('difficulty') or {}
    if platform or not modifiers:
        return profile
    result = copy.deepcopy(profile)
    result['props'] = scale_stats(result['props'],
        modifiers.get('enemy_hp', 1.), modifiers.get('enemy_damage', 1.))
    projectile = result['vid'] in PROJECTILES
    speed = modifiers.get('projectile_speed', 1.) if projectile else 1.

    time_scale = speed if result['vid'] != 'scboss.bomb' else 1.
    def motion_time(t):
        return t / time_scale
    if result['vid'] == 'scboss.rshot':
        recalls = [c['t'] for c in result.get('commands', [])
                   if c.get('modifyprops', {}).get('property') == 41
                   and (c.get('modifyprops', {}).get('decoded') or {}).get('to')]
        if recalls:
            recall = recalls[-1]
            outbound = max((c['t'] for c in result['commands'] if c['t'] < recall
                            and c.get('movetoxmp') is not None), default=0.)
            saved = outbound * (1. - 1. / speed)
            def motion_time(t):
                if t <= outbound:
                    return t / speed
                if t <= recall:
                    return t - saved
                return recall - saved + (t - recall) / speed

    if projectile and result['props'].get('move'):
        result['props']['move'] = scale_move(result['props']['move'], speed)
    for command in result.get('commands') or []:
        command['t'] = motion_time(command['t'])
        if command.get('movetodt'):
            command['movetodt'] = max(1, scaled_int(command['movetodt'], 1. / speed))
        modify = command.get('modifyprops') or {}
        if modify.get('property') == 41 and modify.get('decoded') and projectile:
            modify['decoded'] = scale_move(modify['decoded'], speed)
        factor = {17: modifiers.get('enemy_damage', 1.),
                  18: modifiers.get('enemy_damage', 1.),
                  19: modifiers.get('enemy_hp', 1.),
                  20: modifiers.get('enemy_hp', 1.)}.get(modify.get('property'))
        if factor and modify.get('intval', 0) > 0:
            modify['intval'] = scaled_int(modify['intval'], factor)
    end = result.get('observed_end') or {}
    if end.get('t') is not None and result.get('source_born') is not None:
        end['t'] = result['source_born'] + motion_time(end['t'] - result['source_born'])
    return result

def nova_groups(attacks):
    """Extract complete launch/bloom/four expanding rings as reusable bursts."""
    shots = [a for a in attacks if a['vid'] == 'scboss.gshot']
    launches = sorted((a for a in shots if (a['props'].get('move') or {}).get('to')),
                      key=lambda a: a['t'])
    blooms = [a for a in shots if not a['props'].get('move')
              and not a['props'].get('lifetime')]
    groups = []
    remaining = list(blooms)
    for launch in launches:
        target = launch['props']['move']['to']
        candidates = [a for a in remaining if a['props']['xmp'] == target['xmp']
                      and a['props']['ymp'] == target['ymp'] and a['t'] >= launch['t']]
        if not candidates:
            raise ValueError('Nova launch has no matching bloom')
        bloom = min(candidates, key=lambda a: a['t'])
        remaining.remove(bloom)
        groups.append(dict(launch=launch, bloom=bloom, attacks=[launch, bloom]))
    packets = {}
    for a in shots:
        if a['props'].get('lifetime'):
            packets.setdefault(round(a['t'], 6), []).append(a)
    for packet in packets.values():

        cx = sum(a['props']['xmp'] for a in packet) / len(packet)
        cy = sum(a['props']['ymp'] for a in packet) / len(packet)
        candidates = [g for g in groups if 0. <= packet[0]['t'] - g['bloom']['t'] <= 1.5]
        if not candidates:
            raise ValueError('Nova ring has no active bloom')
        group = min(candidates, key=lambda g: math.hypot(
            cx - g['bloom']['props']['xmp'], cy - g['bloom']['props']['ymp']))
        group['attacks'].extend(packet)
    for group in groups:
        if len(group['attacks']) != 130:
            raise ValueError('Incomplete Nova burst: %s objects' % len(group['attacks']))
    return groups

def nova_payload(pack, timing, attack_at, end):
    """Raise launch cadence inside an unchanged platform/phase window."""
    rate = pack.entities['difficulty']['nova_rate']
    speed = pack.entities['difficulty']['projectile_speed']
    key = id(timing)
    groups = pack.nova_groups[key]
    launches = [g['launch']['t'] for g in groups]
    cadence = statistics.median(b-a for a, b in zip(launches, launches[1:])) if len(launches)>1 else 1.8
    cursor = attack_at
    payload = []
    index = 0
    while cursor < end:
        group = groups[index % len(groups)]
        origin = group['launch']['t']
        duration = max((a['t']-origin)/speed + max(0, a['props'].get('lifetime', 0))/1000.
                       for a in group['attacks'])
        if cursor + duration > end + 1e-9:
            break
        for a in group['attacks']:
            q = copy.deepcopy(a)
            q['t'] = cursor + (a['t']-origin)/speed
            payload.append(q)
        index += 1

        interval = launches[index % len(groups)]-launches[(index-1) % len(groups)]
        cursor += (interval if interval > 0 else cadence) / rate
    return payload
