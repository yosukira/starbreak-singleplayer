'Headless controller for the ETL lab_mid (LabHard5) encounter.'

import random

from .. import MapController
from .scene import LabMidPack

class LabMidController(MapController):

    name = 'lab_mid'

    def __init__(self, pack=None, sink=None, seed_damage=None, rng=None):
        self.pack = pack or LabMidPack()
        self.sink = sink or (lambda event: None)
        rule = self.pack.mechanics
        seed_rule = rule.get('seeds') or {}
        gates = (getattr(self.pack, 'timegates', None) or {}).get('rules') or {}
        death_rule = gates.get('block_death') or {}
        cycle = gates.get('attack_cycle') or {}
        self.check_period = float(
            death_rule.get('time_gate_s')
            or rule['blocks']['check_period_s'])
        self.first_gate = float(
            death_rule.get('first_gate_s') or self.check_period)

        self.first_check = self.first_gate

        self.seed_per_event = False
        self.seed_damage = float(seed_damage
                                 or seed_rule.get('damage_per_spawn') or 200)
        self.death_seed_min = int(seed_rule.get('death_seed_min') or 5)
        self.death_seed_max = int(seed_rule.get('death_seed_max') or 10)
        self.rng = rng or random.Random(20261003)
        boss_rule = rule.get('boss') or {}

        self.boss_body_hp = float(boss_rule.get('body_hitpoints')
                                  or boss_rule.get('hitpoints') or 1)
        self.boss_phase2_fraction = float(
            boss_rule.get('phase2_damage_fraction') or 0.75)
        self.boss_phase2_anim = list(boss_rule.get('phase2_anim')
                                     or [2, 4224])
        self.bullet_period = float(rule['bullets']['volley_period_s'])
        self.bullet_first = 3.0
        self.segment_period = float(cycle.get('period_s') or 10.3)
        self.segment_pause = float(cycle.get('pause_s') or 1.63)
        self.segment_resume = float(cycle.get('resume_delay_s') or 1.39)
        self.prepare()

    def prepare(self):
        self.time = 0.0
        self.events = []
        self.state = 'blocks'
        self.blocks = {}
        self.order = []
        self.next_death_at = self.first_gate
        self.next_segment = (self.bullet_first + self.segment_period
                             - self.segment_pause)
        self.pattern_size = 2
        self.vulnerable = False
        self.boss_hp = float(self.boss_body_hp)
        self.boss_max_hp = self.boss_hp
        self.boss_damage = 0.0
        self.boss_phase = 1
        self.death_emitted = False
        self.next_bullet = self.bullet_first
        self._seq = 0
        self._delivered = 0
        for row in self.pack.scene['blocks']:
            objid = int(row['objid'])
            hp = float(row.get('hitpoints') or 9000)
            self.blocks[objid] = {
                'objid': objid,
                'hp': hp,
                'max_hp': hp,
                'pending': False,
                'alive': True,
                'x': int(row.get('xmp') or 0),
                'y': int(row.get('ymp') or 0),
                'w': int(row.get('wmp') or 0),
                'h': int(row.get('hmp') or 0),
                'seed_acc': 0.0,
            }
            self.order.append(objid)

    def _emit(self, event):
        self.events.append(event)
        self.sink(event)

    @property
    def alive_blocks(self):
        return [row for row in self.blocks.values() if row['alive']]

    @property
    def alive_count(self):
        return len(self.alive_blocks)

    @property
    def all_blocks_dead(self):
        return self.alive_count == 0

    def pending_blocks(self):
        """Alive blocks whose damage is complete, lowest objid first."""
        return sorted((row for row in self.blocks.values()
                       if row['alive'] and row['pending']),
                      key=lambda row: row['objid'])

    def add_damage(self, value, objid=None):
        if self.state == 'done':
            return
        value = float(value or 0)
        if value <= 0 or objid is None:
            return
        objid = int(objid)
        block = self.blocks.get(objid)
        if block is not None and block['alive']:
            self._damage_block(block, value)
            return
        if objid == self.pack.boss_objid and self.state == 'vulnerable':

            self.boss_hp -= value
            self.boss_damage += value
            if self.boss_hp <= 0 and not self.death_emitted:
                self.death_emitted = True
                self.state = 'death'
                self._emit({
                    'kind': 'death', 'at': round(self.time, 3),
                })

    def _damage_block(self, block, value):

        block['hp'] -= float(value)
        if block['hp'] <= 0 and not block['pending']:
            block['pending'] = True
            block['pending_at'] = self.time

        block['seed_acc'] += float(value)
        spawn = int(block['seed_acc'] // self.seed_damage)
        if spawn > 0:
            block['seed_acc'] -= spawn * self.seed_damage
            self._emit_seeds(block, spawn, source='damage')

    def _emit_seeds(self, block, count, source):
        if count <= 0:
            return
        self._seq += 1
        self._emit({
            'kind': 'seed_spawn', 'at': round(self.time, 3),
            'objid': block['objid'], 'count': int(count),
            'x': block['x'] + block['w'] // 2,
            'y': block['y'] + block['h'] // 2,
            'source': source,
        })

    def _kill_block(self, block):
        block['alive'] = False
        block['pending'] = False

        burst = self.rng.randint(self.death_seed_min, self.death_seed_max)
        self._emit({
            'kind': 'block_destroy', 'at': round(self.time, 3),
            'objid': block['objid'], 'hp': 0,
            'reason': 'HITPOINTS', 'binding': 'damage+time',
            'death_seed_count': burst,
        })
        self._emit_seeds(block, burst, source='death')

    def _enter_segment(self):
        self.pattern_size = self._pattern_for(self.alive_count)
        self._emit({
            'kind': 'boss_segment', 'at': round(self.time, 3),
            'alive': self.alive_count, 'pattern': self.pattern_size,
            'resume_s': round(self.segment_resume, 3),
        })
        if self.state == 'blocks' and self.all_blocks_dead:
            self.vulnerable = True
            self.state = 'vulnerable'
            self._emit({
                'kind': 'boss_vulnerable',
                'at': round(self.time, 3),
                'boss': self.pack.boss_objid,
            })
        elif (self.state == 'vulnerable' and self.boss_phase == 1
                and self.boss_damage
                >= self.boss_max_hp * self.boss_phase2_fraction):

            self.boss_phase = 2
            self._emit({
                'kind': 'boss_phase', 'at': round(self.time, 3),
                'phase': 2, 'anim': list(self.boss_phase2_anim),
                'damage': round(self.boss_damage, 1),
            })

    def _fire_volley(self):
        at = self.time
        count = self._volley_size()
        self._emit({
            'kind': 'volley', 'at': round(at, 3),
            'count': count,
            'pattern': self._pattern_name(count),
            'damage': int(self.pack.mechanics['bullets']['damage']),
        })

    def _pattern_for(self, alive):
        if self.state == 'vulnerable':
            return int(self.pack.mechanics['bullets']
                       .get('volley_size_vulnerable') or 2)
        if 1 <= alive <= 2:
            return int(self.pack.mechanics['bullets']
                       .get('volley_size_le_two_blocks') or 5)
        return int(self.pack.mechanics['bullets']
                   .get('volley_size_many_blocks') or 2)

    def step(self, dt):
        dt = float(dt)
        if dt < 0:
            raise ValueError('dt must be non-negative')
        end = self.time + dt
        if self.state == 'done':
            self.time = end
            return []

        while self.state in ('blocks', 'vulnerable'):
            pending = self.pending_blocks() if self.state == 'blocks' else []
            kill_at = max(self.next_death_at, self.time) if pending else None
            when = None
            for candidate in (kill_at, self.next_segment, self.next_bullet):
                if candidate is not None and (when is None or candidate < when):
                    when = candidate
            if when is None or when > end + 1e-9:
                break
            self.time = when
            if (kill_at is not None and when + 1e-9 >= kill_at
                    and self.pending_blocks()):
                block = self.pending_blocks()[0]
                self._kill_block(block)
                self.next_death_at = when + self.check_period
                continue
            if when + 1e-9 >= self.next_segment:
                self._enter_segment()
                self.next_segment += self.segment_period

                self.next_bullet = max(self.next_bullet,
                                       when + self.segment_resume)
                continue
            if when + 1e-9 >= self.next_bullet:
                self._fire_volley()
                self.next_bullet += self.bullet_period
                continue
            break
        self.time = end
        out = self.events[self._delivered:]
        self._delivered = len(self.events)
        return out

    def _volley_size(self):
        return self.pattern_size

    def _pattern_name(self, count):
        return 'dense' if count >= 5 else 'pair'

    def status(self):
        return {
            'map': self.name,
            'time': round(self.time, 3),
            'state': self.state,
            'alive_blocks': self.alive_count,
            'pending_blocks': len(self.pending_blocks()),
            'blocks': dict((str(objid), {
                'hp': round(row['hp'], 1),
                'alive': row['alive'],
                'pending': row['pending'],
            }) for objid, row in self.blocks.items()),
            'boss_hp': round(self.boss_hp, 1),
            'boss_damage': round(self.boss_damage, 1),
            'boss_phase': self.boss_phase,
            'vulnerable': self.vulnerable,
            'next_death_s': round(max(0.0, self.next_death_at - self.time), 3),
            'next_segment_s': round(max(0.0, self.next_segment - self.time), 3),
            'pattern_size': self.pattern_size,
            'events': len(self.events),
        }
