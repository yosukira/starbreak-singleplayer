'Turret batteries: rolled CreateWorld rows, tracking and volley cadence.'

import math

from . import constants

def _want_angle(origin, target):
    return (math.degrees(math.atan2(target[1] - origin[1],
                                    target[0] - origin[0])) + 360.0) % 360.0

def _turn(current, wanted, max_delta):
    delta = ((wanted - current + 540.0) % 360.0) - 180.0
    if delta > max_delta:
        delta = max_delta
    elif delta < -max_delta:
        delta = -max_delta
    return (current + delta + 360.0) % 360.0

class TurretSlot(object):
    """One spawned turret: object id, row, ship-relative offset and ammo."""

    __slots__ = ('objid', 'row', 'index', 'count', 'offset', 'ammo',
                 'fixed_forward')

    def __init__(self, objid, row, index, count, offset, ammo,
                 fixed_forward=False):
        self.objid = int(objid)
        self.row = str(row)
        self.index = int(index)
        self.count = int(count)
        self.offset = (int(offset[0]), int(offset[1]))
        self.ammo = str(ammo)
        self.fixed_forward = bool(fixed_forward)

    def __repr__(self):
        return 'TurretSlot(%d, %s[%d/%d], %s, offset=%r)' % (
            self.objid, self.row, self.index, self.count, self.ammo,
            self.offset)

TURRET_ROW_SLOTS = {

    'upper': tuple(range(43, 50)) + tuple(range(1000, 1013)),
    'front': tuple(range(50, 54)) + tuple(range(1013, 1019)),
    'lower': (59, 58, 57, 56, 55, 54, 62) + tuple(range(1019, 1032)),
}

TURRET_COUNT_LIMITS = {'upper': (0, 20), 'front': (0, 10), 'lower': (0, 20)}
MISSILE_ROW_LIMITS = {'upper': (0, 20), 'lower': (0, 20)}

TURRET_ROW_GEOMETRY = {
    'upper': {
        'anchor': (81920, -20480),
        'span': 473600,
        'direction': (1, 0),
        'counts': (3, 7),
        'family': 'side',
        'fixed_forward': False,
    },
    'front': {
        'anchor': (-16384, 77824),
        'span': 61440,
        'direction': (0, 1),
        'counts': (2, 4),
        'family': 'front',
        'fixed_forward': True,
    },
    'lower': {
        'anchor': (473600, 131072),
        'span': 473600,
        'direction': (-1, 0),
        'counts': (3, 7),
        'family': 'side',
        'fixed_forward': False,
    },
}

def row_offsets(row, count):
    """Ship-relative offsets for ``count`` turrets in one row.

    The measured rounding is deliberately asymmetric: the lower row is the
    mirrored copy of the canonical floor layout and therefore rounds away
    from its right anchor.  This is only visible for counts 3, 6 and 7.
    """
    geometry = TURRET_ROW_GEOMETRY[row]
    count = int(count)
    if count <= 0:
        raise ValueError('turret count must be positive')
    anchor_x, anchor_y = geometry['anchor']
    direction_x, direction_y = geometry['direction']
    span_px = geometry['span'] // 256
    offsets = []
    for index in range(count):
        numerator = span_px * index
        if row == 'lower':
            step_px = (numerator + count - 1) // count
        else:
            step_px = numerator // count
        offsets.append((anchor_x + direction_x * step_px * 256,
                        anchor_y + direction_y * step_px * 256))
    return tuple(offsets)

def _roll_ammo(rng, family, missile_chance, scatter_chance):
    if family == 'front':
        return 'scattershot' if rng.random() < scatter_chance else 'csbullet'
    return 'bossmissile' if rng.random() < missile_chance else 'csbullet'

def normalize_turret_counts(document):
    """Return editor counts with ``None`` meaning the normal random roll.

    Explicit counts must stay inside the sandbox ranges.  The function is
    shared by the desktop editor and the live session so the UI cannot write
    a row size that the object/slot table cannot represent.
    """
    document = document or {}
    if not isinstance(document, dict):
        raise ValueError('turret counts must be a mapping')
    result = {}
    for row, (low, high) in TURRET_COUNT_LIMITS.items():
        value = document.get(row)
        if value is None or value == '' or value == '随机' or value == 'random':
            result[row] = None
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            raise ValueError('turret count for {0} is not an integer'.format(row))
        if not low <= count <= high:
            raise ValueError('turret count for {0} must be {1}-{2}'.format(
                row, low, high))
        result[row] = count
    return result

def normalize_missile_counts(document):
    """Return optional per-side tracking-turret counts.

    ``None`` keeps the measured 30% random roll for that row.  An integer
    deterministically sets the number of tracking turrets on the upper or
    lower row.  Front turrets are never tracking and are not accepted here.
    """
    document = document or {}
    if not isinstance(document, dict):
        raise ValueError('missile counts must be a mapping')
    result = {}
    for row, (low, high) in MISSILE_ROW_LIMITS.items():
        value = document.get(row)
        if value is None or value == '' or value == '随机' or value == 'random':
            result[row] = None
            continue
        try:
            count = int(value)
        except (TypeError, ValueError):
            raise ValueError('missile count for {0} is not an integer'.format(row))
        if not low <= count <= high:
            raise ValueError('missile count for {0} must be {1}-{2}'.format(
                row, low, high))
        result[row] = count
    return result

def roll_turret_layout(rng, missile_chance=0.30, scatter_chance=0.30,
                       missile_floor=2, counts=None, missile_counts=None,
                       count_ranges=None):
    """Roll one battle's rows, counts, ammo and ship-relative positions.

    Side rows are missile 30% / normal 70%; the fixed-forward front row is
    scatter 30% / normal 70%.  With no override, counts are the
    user-confirmed live ranges: upper/lower 3-7, front 2-4.  ``counts`` is
    the editor's optional sandbox override (0 disables a row).
    ``missile_counts`` optionally fixes the exact tracking-turret count on
    the upper/lower row; when present it replaces both the random ammo roll
    and the global two-missile floor for that battle.
    """
    counts = normalize_turret_counts(counts)
    missile_counts = normalize_missile_counts(missile_counts)
    has_missile_override = any(value is not None
                               for value in missile_counts.values())
    layout = {}
    for row in ('upper', 'front', 'lower'):
        geometry = TURRET_ROW_GEOMETRY[row]
        low, high = (count_ranges or {}).get(row, geometry['counts'])
        wanted_missiles = missile_counts.get(row)
        if counts.get(row) is None:
            count = rng.randint(low, high)

            if wanted_missiles is not None:
                count = max(count, wanted_missiles)
        else:
            count = counts[row]
            if wanted_missiles is not None and wanted_missiles > count:
                raise ValueError(
                    '{0} missile count {1} exceeds turret count {2}'.format(
                        row, wanted_missiles, count))
        if count == 0:
            continue
        offsets = row_offsets(row, count)
        for index in range(count):
            objid = TURRET_ROW_SLOTS[row][index]
            layout[objid] = TurretSlot(
                objid, row, index, count, offsets[index],
                _roll_ammo(rng, geometry['family'], missile_chance,
                           scatter_chance),
                geometry['fixed_forward'])
    if has_missile_override:

        for row in ('upper', 'lower'):
            wanted = missile_counts.get(row)
            if wanted is None:
                continue
            row_slots = [slot for slot in layout.values() if slot.row == row]
            for index, slot in enumerate(row_slots):
                slot.ammo = 'bossmissile' if index < wanted else 'csbullet'

        floor = max(0, int(missile_floor))
        missiles = [slot for slot in layout.values()
                    if slot.row in ('upper', 'lower')
                    and slot.ammo == 'bossmissile']
        if len(missiles) < floor:
            candidates = [slot for slot in layout.values()
                          if slot.row in ('upper', 'lower')
                          and missile_counts.get(slot.row) is None
                          and slot.ammo != 'bossmissile']
            rng.shuffle(candidates)
            for slot in candidates[:floor - len(missiles)]:
                slot.ammo = 'bossmissile'
    else:
        floor = max(0, int(missile_floor))
        missiles = [objid for objid, slot in layout.items()
                    if slot.ammo == 'bossmissile'
                    and not slot.fixed_forward]
        if len(missiles) < floor:
            candidates = [objid for objid, slot in layout.items()
                          if slot.row in ('upper', 'lower')
                          and slot.ammo != 'bossmissile']
            rng.shuffle(candidates)
            for objid in candidates[:floor - len(missiles)]:
                layout[objid].ammo = 'bossmissile'
    return layout

def recorded_layout():
    "The runtime battle's own 7/4/6 layout, for fixtures/reference."
    counts = {'upper': 7, 'front': 4, 'lower': 6}
    ammo = {}
    for turret in constants.BOSS_MISSILE_TURRETS:
        ammo[turret] = 'bossmissile'
    for turret in constants.SCATTERSHOT_TURRETS:
        ammo[turret] = 'scattershot'
    for turret in constants.CSBULLET_TURRETS:
        ammo[turret] = 'csbullet'
    layout = {}
    for row in ('upper', 'front', 'lower'):
        count = counts[row]
        offsets = row_offsets(row, count)
        for index in range(count):
            objid = TURRET_ROW_SLOTS[row][index]
            layout[objid] = TurretSlot(
                objid, row, index, count, offsets[index],
                ammo.get(objid, 'csbullet'),
                TURRET_ROW_GEOMETRY[row]['fixed_forward'])
    return layout

class TurretBattery(object):
    """One ammo type fired by the turrets that rolled it."""

    def __init__(self, room, slots, ammo, period, first_phase,
                 acquire_phase=None, rules=constants):
        self.rules = rules
        self.room = room
        self.slots = tuple(slots)
        self.turret_ids = tuple(slot.objid for slot in self.slots)
        self.ammo = ammo
        self.period = float(period)
        self.first_phase = float(first_phase)
        self.acquire_phase = (self.rules.TURRET_ACQUIRE_DELAY
                              if acquire_phase is None else acquire_phase)
        self.fixed = dict((slot.objid, slot.fixed_forward)
                          for slot in self.slots)
        self.facing = {}
        self.next_fire = dict((i, None) for i in self.turret_ids)
        self.preparing_until = dict((i, None) for i in self.turret_ids)
        self.locked_targets = dict((i, None) for i in self.turret_ids)
        self.locked_lives = dict((i, None) for i in self.turret_ids)
        for slot in self.slots:
            item = room.object(slot.objid)
            self.facing[slot.objid] = (
                180.0 if slot.fixed_forward else
                float(item.get('facing_', 0) if item is not None else 0))

    def step(self, dt, phase, player, emit, players=None):
        """Track, hold aim while charging, then release the attached rounds.

        emit(turret_id, ammo, aim, stage) receives 'prepare' or 'release'.
        The same object survives both stages, as in the retail packets.
        """
        from ..participants import active_players, nearest, identity, life_identity
        candidates = active_players(players if players is not None else (player,))
        live = {identity(p): p for p in candidates}
        max_delta = self.rules.TURRET_TURN_PER_SECOND * dt
        fired = 0
        for turret_id in self.turret_ids:
            fixed = self.fixed[turret_id]
            center = self.room.turret_center(turret_id)
            if center is None:
                continue
            selected = None if fixed else live.get(self.locked_targets[turret_id])
            if (selected is not None and self.locked_lives[turret_id] is not None
                    and life_identity(selected) != self.locked_lives[turret_id]):

                selected = None
            if not fixed and selected is None:
                self.locked_targets[turret_id] = None
                self.locked_lives[turret_id] = None
                if phase >= self.acquire_phase:
                    selected = nearest(candidates, center, radius=self.rules.TURRET_LOCK_RANGE)
                    if selected is not None:
                        self.locked_targets[turret_id] = identity(selected)
                        self.locked_lives[turret_id] = life_identity(selected)

            target = selected.center() if selected is not None else None
            tracked = selected is not None
            if tracked and not fixed and self.preparing_until[turret_id] is None:
                wanted = _want_angle(center, target)
                self.facing[turret_id] = _turn(
                    self.facing[turret_id], wanted, max_delta)

            if (self.preparing_until[turret_id] is not None
                    and phase >= self.preparing_until[turret_id]):
                emit(turret_id, self.ammo, self.facing[turret_id], 'release')
                fired += 1
                self.preparing_until[turret_id] = None
            if phase < self.first_phase or (not fixed and not tracked):
                continue
            if self.next_fire[turret_id] is None:
                self.next_fire[turret_id] = self.first_phase
            if (self.preparing_until[turret_id] is None
                    and phase >= self.next_fire[turret_id]):
                emit(turret_id, self.ammo, self.facing[turret_id], 'prepare')
                self.preparing_until[turret_id] = (
                    phase + self.rules.TURRET_CHARGE_SECONDS[self.ammo])
                self.next_fire[turret_id] = max(
                    self.next_fire[turret_id] + self.period,
                    phase + self.period)
        return fired

def build_batteries(room, rng=None, layout=None, rules=constants):
    """One TurretBattery per ammo type for the battle's rolled layout."""
    if layout is None:
        layout = (recorded_layout() if rng is None
                  else roll_turret_layout(rng))
    groups = {'csbullet': [], 'scattershot': [], 'bossmissile': []}
    for turret, slot in sorted(layout.items()):
        groups[slot.ammo].append(slot)
    batteries = []
    phases = {'csbullet': rules.TURRET_FIRST_VOLLEY,
              'scattershot': rules.TURRET_FIRST_VOLLEY + 2.5,
              'bossmissile': rules.TURRET_FIRST_VOLLEY + 4.0}
    periods = {'csbullet': rules.CSBULLET_PERIOD,
               'scattershot': rules.SCATTERSHOT_PERIOD,
               'bossmissile': rules.BOSS_MISSILE_PERIOD}
    for ammo in ('csbullet', 'scattershot', 'bossmissile'):
        if groups[ammo]:
            batteries.append(TurretBattery(room, groups[ammo], ammo,
                                           periods[ammo], phases[ammo], rules=rules))
    return tuple(batteries)
