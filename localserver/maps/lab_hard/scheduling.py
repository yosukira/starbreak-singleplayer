'Phase-relative schedules for runtime ETL encounter patterns.'

import bisect

ANIM_LEAD_S = {'single': 1.01, 'triple': 0.56, 'ring': 0.205}

def phase_wall_pulls(pack):
    """Keep complete pulls matching the phase's one/two/three/four hands.

    Captured phase boundaries can include the tail of an earlier action. Those
    rows are useful motion examples, but are not new actions for the next phase.
    In particular the first captured P5 group contains only three wall hands.
    """
    starts = [float(p['start'])-pack.fight_start for p in pack.phase_doc['phases']]
    groups = {}
    for original in pack.arm_motion.get('wall_pulls') or []:
        row = dict(original)
        phase = row.get('phase_index')
        if phase is None:
            phase = bisect.bisect_right(starts, float(row.get('at', 0))) - 1
            row['phase_index'] = phase
        key = (phase, row.get('group_at', row.get('at')))
        groups.setdefault(key, []).append(row)
    rows = []
    for (phase, _), group in groups.items():
        if (1 <= phase <= 4 and len(group) == phase
                and len({r['wall'] for r in group}) == phase
                and len({r['claw'] for r in group}) == phase):
            rows.extend(group)
    return rows

def arm_layout(pack):
    'base objid <-> claw objid <-> the wall that claw pulls, from capture.'
    claws = {}
    bases = {}
    for claw in pack.claw_ids:
        move = pack.template(claw).get('move') or {}
        base = int((move.get('relativeto') or {}).get('objid') or 0)
        if base:
            claws[base] = int(claw)
            bases[int(claw)] = base
    walls = {}
    for row in pack.arm_motion.get('wall_pulls') or []:
        base = bases.get(int(row.get('claw') or 0))
        if base:
            walls[int(row['wall'])] = base
    return claws, bases, walls

def randomised_pull_plan(pack, rng):
    '[USER] every pull picks its own direction.'
    pool = sorted(int(w) for w in pack.wall_ids)
    groups = {}
    for row in phase_wall_pulls(pack):
        key = (row.get('phase_index'), row.get('group_at'))
        groups.setdefault(key, []).append(int(row['wall']))
    plan = {}
    for key, recorded in groups.items():
        size = len(recorded)
        drawn = rng.sample(pool, size) if 0 < size <= len(pool) else list(recorded)
        plan[key] = dict(zip(sorted(recorded), drawn))
    return plan

def remap_pull_row(row, plan, claws, bases, walls):
    'Point one runtime pull row (and its arm family) at the drawn wall.'
    key = (row.get('phase_index'), row.get('group_at'))
    mapping = (plan or {}).get(key)
    group = [int(w) for w in (row.get('group_walls') or [row.get('wall')])]
    if mapping:
        group = [mapping.get(w, w) for w in group]
    row['group_walls'] = group
    old_wall = int(row.get('wall') or 0)
    new_wall = mapping.get(old_wall, old_wall) if mapping else old_wall
    if new_wall != old_wall:
        row['wall'] = new_wall
        row['walls'] = [new_wall]
        old_base = walls.get(old_wall)
        new_base = walls.get(new_wall)
        if old_base and new_base:
            row['claw'] = claws.get(new_base, row.get('claw'))
            row['claws'] = [row['claw']]
            home = dict(row.get('claw_home') or {})
            home['relativeto'] = {'objid': int(new_base)}
            row['claw_home'] = home

            seekers = [dict(seeker) for seeker in (row.get('seekers') or [])]
            for seeker in seekers:
                seeker['arm_base'] = int(new_base)
                seeker['arm_claw'] = int(claws.get(new_base) or 0)
                seeker['arm_segment'] = 0
            row['seekers'] = seekers
    return row

class PhaseSchedule(object):
    def __init__(self, pack, rows, repeat=True, map_event=None, periods=None):
        phases = pack.phase_doc['phases']
        starts = [float(p['start']) - pack.fight_start for p in phases]
        self.periods = (list(periods) if periods is not None else
                        [float(p.get('pattern_duration_s',p['duration_s']))
                         for p in phases])
        self.rows = [[] for _ in phases]
        for at, event in rows:
            index = int(event['phase_index']) if 'phase_index' in event else bisect.bisect_right(starts, float(at) + 1e-9) - 1
            if 0 <= index < len(phases):
                offset = max(0.0, float(event.get('phase_at',float(at)-starts[index])))
                self.rows[index].append((offset, dict(event)))
        for pattern in self.rows:
            pattern.sort(key=lambda row: row[0])
        self.repeat = repeat
        self.map_event=map_event
        self.phase = None
        self.cursor = 0
        self.cycle = 0

    def step(self, now, phase, age, suppressed=()):
        if phase != self.phase:
            self.phase, self.cursor, self.cycle = phase, 0, 0
        if phase < 0 or phase >= len(self.rows):
            return []
        pattern = self.rows[phase]
        if not pattern:
            return []
        period = self.periods[phase]
        epoch = now - age
        result = []
        while True:
            if self.cursor == len(pattern):
                if not self.repeat or period <= 0:
                    break
                self.cursor = 0
                self.cycle += 1
            offset, event = pattern[self.cursor]
            local_at = offset + self.cycle * period
            if local_at > age + 1e-9:
                break
            self.cursor += 1

            if event['kind'] in suppressed:
                continue
            if event.get('suppress_with') in suppressed:
                continue
            event = dict(event, at=round(epoch + local_at, 6),
                         phase='p%d' % (phase + 1), loop=self.cycle)
            if self.map_event:event=self.map_event(event)
            result.append(event)
        return result

def encounter_schedules(pack, cadence, pull_plan=None,rng=None):
    claws, bases, walls = arm_layout(pack)
    volleys, structures, adds = [], [], []
    carry_rows = []
    for row in (pack.phase_doc.get('attack_sequence') or
                pack.phase_doc.get('volley_sequence') or []):
        count = int(row.get('count') or 1)
        pattern = row.get('pattern') or ('single' if count == 1 else
                                         'triple' if count == 3 else 'ring')
        event = dict(row, kind='volley', pattern=pattern, damage=45)
        if pattern == 'ring':
            event['step_deg'] = 14.4
        at = float(row['at']) - pack.fight_start
        spec = row.get('animation') or {}
        lead = float(spec.get('lead_s') or ANIM_LEAD_S.get(pattern) or 0.)
        if lead:
            animation = dict(
                kind='boss_anim',
                anim='SHOOT4' if pattern == 'ring' else 'SHOOT',
                suppress_with='volley', pattern=pattern, at=at)
            if spec.get('animid') is not None:
                animation.update(animid=spec['animid'],time_ms=spec['time_ms'])
            if 'phase_index' in row:
                animation.update(phase_index=row['phase_index'],phase_at=max(0.,row['phase_at']-lead))
            volleys.append((at - lead, animation))
            if (pattern == 'triple' and row.get('phase_index') == 1
                    and row.get('phase_at') is not None):
                carry_rows.append((max(0.0, float(row['phase_at']) - lead),
                                   animation))
        volleys.append((at, event))
        if (pattern == 'triple' and row.get('phase_index') == 1
                and row.get('phase_at') is not None):
            carry_rows.append((float(row['phase_at']), event))
    for wave in pack.add_mechanics.get('bramble_waves') or []:
        event=dict(kind='arm_pull', segments=len(wave['tendrils']), wave=wave)
        if 'phase_index' in wave:event.update(phase_index=wave['phase_index'],phase_at=wave['phase_at'])
        structures.append((wave['at'],event))

    pulls = phase_wall_pulls(pack)
    groups = {}
    for row in pulls:
        key = (row.get('phase_index'), row.get('group_at'))
        groups.setdefault(key, []).append(int(row['wall']))
    for row in pulls:
        key = (row.get('phase_index'), row.get('group_at'))
        group_walls = list(groups.get(key) or [row['wall']])
        event = dict(row, kind='wall_pull', walls=[row['wall']],
                     claws=[row['claw']], group_walls=group_walls)
        event['captured_pull']=dict(event)
        remap_pull_row(event, pull_plan, claws, bases, walls)
        structures.append((row['at']-(0. if 'phase_index' in row else float(row.get('anim_lead_s') or 0.)),
                           event))

    params = pack.seeker_params
    by_wall = {}
    for key, mapping in (pull_plan or {}).items():
        for old_wall, new_wall in mapping.items():
            by_wall[(int(key[0] or 0), int(old_wall))] = int(new_wall)
    coupled = any(row.get('seekers') for row in pulls)
    for row in ([] if coupled else pack.seeker_spawns()):
        new_wall = by_wall.get((int(row.get('phase_index') or 0),
                                int(row.get('wall') or 0)))
        if new_wall is not None and new_wall != int(row.get('wall') or 0):
            new_base = walls.get(new_wall)
            if new_base:
                row = dict(row, arm_base=int(new_base),
                           arm_claw=int(claws.get(new_base) or 0),
                           arm_segment=0)
        event=dict(row,kind='seeker_spawn', props=row['props'],
                                     first_shot_delay_s=params.get('first_shot_delay_s'),
                                     shot_period_s=params.get('shot_period_s'))
        if 'at' in row:
            at = float(row['at'])
        else:
            index = int(row.get('phase_index') or 0)
            phases = pack.phase_doc.get('phases') or []
            start = float(phases[index].get('start') or 0.0) if index < len(phases) else 0.0
            at = start + float(row.get('phase_at') or 0.0)
        adds.append((at,event))
    repeated_plan={}
    def remap_repeated(event):
        if not rng or event['kind']!='wall_pull' or not event.get('loop'):return event
        captured=event['captured_pull']
        key=(captured.get('phase_index'),captured.get('group_at'))
        cycle_key=key+(event['loop'],)
        if cycle_key not in repeated_plan:
            original=sorted(int(w) for w in captured['group_walls'])
            pool = sorted(pack.wall_ids)
            drawn = (rng.sample(pool, len(original))
                     if len(original) <= len(pool)
                     else [rng.choice(pool) for _ in original])
            repeated_plan[cycle_key]=dict(zip(original, drawn))
        fresh=dict(captured,at=event['at'],phase=event['phase'],loop=event['loop'])
        return remap_pull_row(fresh,{key:repeated_plan[cycle_key]},claws,bases,walls)

    volley_periods = None
    phases = pack.phase_doc.get('phases') or []
    if carry_rows and len(phases) > 1:
        base_period = float(phases[1].get('pattern_duration_s',
                                          phases[1]['duration_s']))
        for phase_index in range(2, len(phases)):
            for offset, event in carry_rows:
                volleys.append((offset, dict(event, phase_index=phase_index,
                                             phase_at=offset)))
        volley_periods = [float(p.get('pattern_duration_s', p['duration_s']))
                          for p in phases]
        for phase_index in range(2, len(volley_periods)):
            volley_periods[phase_index] = base_period
    return (PhaseSchedule(pack, volleys, periods=volley_periods),
            PhaseSchedule(pack, structures,map_event=remap_repeated),
            PhaseSchedule(pack, adds))
