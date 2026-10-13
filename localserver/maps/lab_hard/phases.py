'ETL phase gates, with runtime pattern lengths kept separately.'

import json
import os

DEFAULT_DAMAGE_REQUIRED = 10000
EPSILON = 1e-9

class PhaseSpec(object):
    __slots__ = ('phase_id', 'duration_s', 'damage_required',
                 'damage_bound', 'patterns')

    def __init__(self, phase_id, duration_s, damage_required,
                 damage_bound=None, patterns=None):
        self.phase_id = phase_id
        self.duration_s = float(duration_s)
        self.damage_required = float(damage_required)
        self.damage_bound = damage_bound
        self.patterns = dict(patterns or {})

class PhaseTransition(object):
    __slots__ = ('index', 'spec', 'at_time', 'phase_damage',
                 'carry_over_s')

    def __init__(self, index, spec, at_time, phase_damage, carry_over_s):
        self.index = index
        self.spec = spec
        self.at_time = at_time
        self.phase_damage = phase_damage
        self.carry_over_s = carry_over_s

    def as_dict(self):
        return {'index': self.index, 'phase': self.spec.phase_id,
                'at': round(self.at_time, 3),
                'phase_damage': self.phase_damage,
                'carry_over_s': round(self.carry_over_s, 3)}

class PhaseMachine(object):
    'Time+damage gate for the Zomputer phases.'

    def __init__(self, specs, damage_required=None, hold_final=False):
        self.specs = list(specs)
        if not self.specs:
            raise ValueError('at least one phase is required')
        if damage_required is not None:
            for spec in self.specs:
                spec.damage_required = float(damage_required)
        self.index = 0
        self.time_in_phase = 0.0
        self.damage_in_phase = 0.0
        self.total_damage = 0.0
        self.clock = 0.0
        self.transitions = []
        self.finished = False

        self.hold_final = bool(hold_final)
        self.final_ready = False

        self.hold = False

    @property
    def spec(self):
        return self.specs[min(self.index, len(self.specs) - 1)]

    @property
    def phase_id(self):
        return self.spec.phase_id

    def add_damage(self, value):
        if self.finished:
            return
        value = float(value or 0)
        if value <= 0:
            return
        self.damage_in_phase += value
        self.total_damage += value

    def ready(self):
        return (not self.hold
                and self.time_in_phase + EPSILON >= self.spec.duration_s
                and self.damage_in_phase + EPSILON >= self.spec.damage_required)

    def step(self, dt):
        if self.finished:
            return []
        dt = float(dt)
        if dt < 0:
            raise ValueError('dt must be non-negative')
        cursor = self.clock
        self.clock += dt
        if self.hold:
            self.time_in_phase += dt
            return []
        remaining = dt
        out = []
        while not self.finished:
            spec = self.spec
            if self.final_ready:
                self.time_in_phase += remaining
                break
            wait = max(0.0, spec.duration_s - self.time_in_phase)
            if (self.damage_in_phase + EPSILON < spec.damage_required
                    or wait > remaining + EPSILON):
                self.time_in_phase += remaining
                break
            cursor += wait
            remaining = max(0.0, remaining - wait)
            if (self.hold_final and self.index == len(self.specs) - 1):

                self.final_ready = True
                self.time_in_phase += remaining
                break
            transition = PhaseTransition(self.index, spec, cursor,
                                        self.damage_in_phase, remaining)
            self.transitions.append(transition)
            out.append(transition)
            self.index += 1
            if self.index >= len(self.specs):
                self.finished = True
                self.time_in_phase = 0.0
                self.damage_in_phase = 0.0
                break
            self.time_in_phase = 0.0
            self.damage_in_phase = 0.0
        return out

    def status(self):
        return {'phase': self.phase_id, 'index': self.index,
                'time_in_phase': round(self.time_in_phase, 3),
                'damage_in_phase': self.damage_in_phase,
                'damage_required': self.spec.damage_required,
                'total_damage': self.total_damage,
                'final_ready': self.final_ready,
                'finished': self.finished}

def specs_from_document(document, damage_required=None):
    specs = []
    for row in document.get('phases') or []:
        gate = row.get('gate')
        required = row.get('damage_required') or {}
        value = required.get('value') if isinstance(required, dict) else required
        if value is None:
            value = DEFAULT_DAMAGE_REQUIRED
        if damage_required is not None:
            value = damage_required
        if gate == 'time':

            duration = row.get('min_time_s', row['duration_s'])
            value = 0.0
        elif gate == 'damage':
            duration = 0.0
        else:
            duration = row.get('min_time_s', row['duration_s'])
        specs.append(PhaseSpec(row['id'], duration, value,
                               damage_bound=row.get('damage_observed'),
                               patterns=row.get('patterns')))
    return specs

def load_phase_specs(path, damage_required=None):
    handle = open(path, 'r', encoding='utf-8')
    try:
        document = json.load(handle)
    finally:
        handle.close()
    return specs_from_document(document, damage_required)
