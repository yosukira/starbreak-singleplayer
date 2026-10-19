'Scavenger wave spawner and live enemy AI.'

import json
import math
import os
import random

from .. import game_settings
from .. import jsonio
from . import constants
from .aircraft import AircraftMotion
from .terrain import GroundMotion, terrain_type
from .rejoin import jump_speed, available_ledges

LAB_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
CARDS_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics', 'enemy-cards.json')
TRIGGERS_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics',
                             'enemy-triggers.json')
EMIT_VIDS_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics',
                              'attack-emit-vids.json')
EMIT_POINTS_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics',
                                'attack-emits.json')

CATALOGUE_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics',
                              'animation-catalogue.jsonl')
STATE_PROFILES_PATH = os.path.join(LAB_ROOT, 'data', 'mechanics',
                                  'enemy-state-profiles.json')

def _distance(first, second):

    return math.hypot(first[0] - second[0], first[1] - second[1])

def draw_dwell(rng, quantiles=constants.SCAVENGER_DWELL_QUANTILES,
               minimum=constants.SCAVENGER_DWELL_MINIMUM):
    """Sample one individual ship-residence deadline in seconds.

    Piecewise-linear inverse CDF through the measured percentiles. The
    original server never sends a residence timer: these quantiles describe
    the observed ordinary-mob departures and replace the old shared
    3/4-ship-pass counter, which the individual spread contradicts.
    """
    roll=rng.random()
    previous_p,previous_v=0.0,0.0
    for probability,value in quantiles:
        if roll<=probability:
            span=probability-previous_p
            fraction=(roll-previous_p)/span if span>0 else 1.0
            return max(minimum,
                       previous_v+(value-previous_v)*fraction)
        previous_p,previous_v=probability,value
    return value

DEFAULT_POINT_SCALE = 128

GROUND_ATTACK_TIMINGS = {
    'hulk.scavengergunman': (1.80, .744),
    'hulk.scavengershotgunner': (2.00, .944),
    'hulk.scavengermissiler': (2.13, 1.074),
    'hulk.scavengercommando': (1.65, .594),
    'hulk.rpgscavenger': (4.55, .600),
    'hulk.scavengerengineer': (2.20, .616),
}
GROUND_SHOOTER_VIDS = frozenset(GROUND_ATTACK_TIMINGS)-{'hulk.scavengerengineer'}

GROUND_HORIZONTAL_SPEEDS = {'walk': {-1: 20000.0, 1: 19000.0},
                           'run': {-1: 79000.0, 1: 78000.0}}
SHREDDER_VIDS = frozenset(('hulk.scavengershredder', 'hulk.scavengershredderenergy'))
GROUND_STATIONARY_RECOVERY_VIDS = frozenset(GROUND_ATTACK_TIMINGS) | SHREDDER_VIDS

SHREDDER_ATTACK_BUSY = 1.60
SHREDDER_STARTUP_WALK = .10
HEAVY_HORIZONTAL_SPEEDS = {
    'shredder': {'walk': {-1: 24000.0, 1: 23000.0},
                 'run': {-1: 94000.0, 1: 93000.0}},
    'loader': {'walk': {-1: 12000.0, 1: 11000.0},
               'run': {-1: 59000.0, 1: 58000.0}},
}

def _load_json(path):
    try:
        return jsonio.load_json(path)[0]
    except (OSError, ValueError, UnicodeError):
        return {}

def _load_catalogue():
    """vid -> {animation length ms: [POINT_EMIT points]}."""
    catalogue = {}
    try:
        text, _ = jsonio.read_text(CATALOGUE_PATH)
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            vid = record.get('vid')
            length = record.get('length')
            if not vid or length is None:
                continue
            points = list(record.get('points') or [])
            points = [dict(p, timeMs=p.get('timeMs', p.get('time', 0)))
                      for p in points if p.get('type') == 'POINT_EMIT']
            bucket = catalogue.setdefault(vid, {}).setdefault(
                int(length), [])
            if points and not bucket:
                bucket.extend(points)
    except OSError:
        pass
    return catalogue

CATALOGUE = _load_catalogue()

def _animation_points(document, vid):
    """anim length in ms -> POINT_EMIT list for one attack VID.

    Prefers the raw client catalogue; falls back to the derived JSON.
    """
    by_length = dict(CATALOGUE.get(vid) or {})
    for name, anim in (document.get('vids', {}).get(vid) or {}).items():
        length = anim.get('lengthMs')
        if length is None:
            continue
        points = list(anim.get('points') or [])
        if points and not by_length.get(int(length)):
            by_length[int(length)] = points
    return by_length

def build_behaviour():
    cards = _load_json(CARDS_PATH)
    triggers = _load_json(TRIGGERS_PATH).get('vids', {})
    emit_vids = _load_json(EMIT_VIDS_PATH).get('vids', {})
    emit_doc = _load_json(EMIT_POINTS_PATH)
    profiles = _load_json(STATE_PROFILES_PATH).get('vids', {})
    known = {card.get('vid') for card in cards or []}
    cards = list(cards or []) + [dict(vid=vid) for vid in profiles if vid not in known]
    behaviour = {}
    for card in cards or []:
        vid = card.get('vid')
        if not vid:
            continue
        points_by_length = _animation_points(emit_doc, vid)
        attacks = []
        for key, emitted in (emit_vids.get(vid) or {}).items():
            try:
                anim, length = key.split('@')
                anim, length = int(anim), int(length)
            except ValueError:
                continue
            emit_vid = max(emitted.items(), key=lambda item: item[1])[0]
            points = points_by_length.get(length)
            if not points:

                candidates = [(abs(candidate - length), candidate)
                              for candidate, values in points_by_length.items()
                              if values]
                points = (points_by_length[min(candidates)[1]]
                          if candidates else [])
            attacks.append({
                'anim': anim, 'time_ms': length, 'emit': emit_vid,
                'points': points,
                'weight': sum(emitted.values()),
            })
        attacks.sort(key=lambda item: (-item['weight'], item['anim']))
        rule = (triggers.get(vid) or {}).get('recommendedRule') or {}
        profile = profiles.get(vid, {})
        for attack in attacks:
            action = profile.get('actions', {}).get(str(attack['anim']))
            if action and action['length_ms'] == attack['time_ms']:
                attack['points'] = [dict(p, timeMs=p['time']) for p in action['points']
                                    if p.get('type') == 'POINT_EMIT']
        if vid == 'hulk.scavengerloader':

            attacks = []
            for slot in ('4', '5'):
                action = profile.get('actions', {}).get(slot) or {}
                if action:
                    attacks.append(dict(anim=int(slot), time_ms=action['length_ms'], emit=None, points=[],
                        weight=profile.get('animations', {}).get(slot, 1), boxes=action.get('boxes') or [],
                        charge=slot == '4'))
            rule = {'kind': 'melee', 'threshold': 160, 'seconds': 1.1}
        elif vid == 'hulk.eloader':
            attacks = []
            for slot in ('3', '4', '5'):
                action = profile.get('actions', {}).get(slot) or {}
                if not action:
                    continue
                attacks.append(dict(anim=int(slot), time_ms=action['length_ms'],
                    emit=None if slot == '3' else 'hulk.flame', charge=slot == '3',
                    boxes=action.get('boxes') or [], weight=profile.get('animations', {}).get(slot, 1),
                    points=[dict(p, timeMs=p['time']) for p in action.get('points', [])
                            if p.get('type') == 'POINT_EMIT']))
            rule = {'kind': 'melee', 'threshold': 600, 'seconds': 1.4}
        if 'shredder' in vid:
            for slot, action in profile.get('actions', {}).items():
                if action.get('name') not in ('ANIM_SLASH', 'ANIM_SWING'):
                    continue
                attacks.append({'anim': int(slot), 'time_ms': action['length_ms'],
                                'emit': None, 'points': [],
                                'weight': int(profile.get('animations', {}).get(slot, 1)),
                                'boxes': action.get('boxes') or []})
            rule = {'kind': 'melee', 'threshold': 90, 'seconds': SHREDDER_ATTACK_BUSY}
            for attack in attacks:
                attack['recovery'] = max(0.0, SHREDDER_ATTACK_BUSY-attack['time_ms']/1000.0)
        speed = card.get('speedMedian')
        behaviour[vid] = {
            'size': tuple(card.get('size') or constants.SPAWN_DEFAULT_SIZE),
            'hp': int(max(card.get('hp') or {375: 1}, key=lambda k: int(k))),
            'contact': int(max(card.get('contactDamage') or {20: 1},
                                key=lambda k: int(k))),
            'speed': int(round(float(speed) * 512)) if speed else 46 * 256,
            'rule': rule,
            'attacks': attacks,
        }
        if profile:

            behaviour[vid].update(size=tuple(profile.get('size') or behaviour[vid]['size']),
                                  hp=profile.get('hp') or behaviour[vid]['hp'],
                                  contact=profile.get('contact', behaviour[vid]['contact']),
                                  speed=(profile.get('pose_speeds', {}).get('6') or
                                         profile.get('horizontal_speed_median_mp_s') or
                                         behaviour[vid]['speed']))
        if vid == 'hulk.scavengerengineer':
            action = profile.get('actions', {}).get('3')
            if action:
                behaviour[vid]['attacks'] = [{'anim': 3, 'time_ms': action['length_ms'],
                    'emit': None, 'points': [], 'weight': 1, 'boxes': action.get('boxes') or []}]
                behaviour[vid]['rule'] = {'kind': 'melee', 'threshold': 90, 'seconds': 0.35}
        if vid in ('hulk.scavengerloader', 'hulk.eloader'):

            behaviour[vid]['speed'] = profile.get('pose_speeds', {}).get('5') or 12000
        if vid in ('hulk.bdrone', 'hulk.sdrone'):
            behaviour[vid]['speed'] = profile.get('horizontal_speed_median_mp_s') or 40000
        if vid in GROUND_ATTACK_TIMINGS:
            cooldown,recovery=GROUND_ATTACK_TIMINGS[vid]
            behaviour[vid]['rule']=dict(behaviour[vid]['rule'],seconds=cooldown)
            for attack in behaviour[vid]['attacks']:
                attack['recovery']=recovery
                if vid=='hulk.rpgscavenger' and attack['anim']==0:

                    attack['recovery']=cooldown-attack['time_ms']/1000.0
    return behaviour

BEHAVIOUR = build_behaviour()

def _template_props(room, vid):
    props = room.template_props(vid, combat=True)
    if props is None:
        props = {
            'vid_': vid, 'wmp_': 12800, 'hmp_': 23040,
            'ownertype_': {'name': 'OWNERTYPE_ENEMY', 'value': 2},
            'blocktype_': {'name': 'BLOCK_MOVE_ALL', 'value': 1},
        }
    for key in ('move', 'move_', 'lifetime', 'lifetime_', 'xmp', 'xmp_',
                'ymp', 'ymp_', 'facing', 'facing_'):
        props.pop(key, None)
    if not props.get('soundprefix_') and vid in constants.COMBAT_SOUND_FALLBACKS:
        props['soundprefix_'] = constants.COMBAT_SOUND_FALLBACKS[vid]
    return props

def loader_flame_move(slot, facing):
    'The two Move_Vel commands found on all 330 runtime loader flames.'
    diagonal = slot == 5
    return {'vel_':dict(xvel_=facing*(8838 if diagonal else 12500),
        yvel_=-8838 if diagonal else 0,
        xaccel_=facing*(2 if diagonal else 4), yaccel_=-2 if diagonal else 0,
        minxvel_=-12500 if facing<0 else 0, maxxvel_=0 if facing<0 else 12500,
        minyvel_=-12500, maxyvel_=0)}

class Enemy(object):
    __slots__ = ('rules', 'balance', 'objid', 'vid', 'name', 'kind', 'x', 'y', 'w', 'h', 'hp',
                 'armor', 'speed', 'contact', 'alive', 'facing', 'shot_vid',
                 'shot_cd', 'shot_timer', 'attack_until', 'attack_started',
                 'attacks', 'rule', 'emits', 'emits_done', 'knockback', 'age',
                 'emitted', 'contact_cd', 'rng', 'launch_until',
                 'launch_vx', 'launch_vy', 'motion', 'state', 'pose',
                 'next_jump', 'recovery_until', 'attack', 'attack_target', 'run_ready_at',
                 'summon_timer', 'summoned_objid', 'flying', 'melee_hit_done', 'climb_side',
                 'coop_attack', 'coop_melee_hits', 'coop_contact_due',
                 'climb_started', 'climb_route', 'climb_start_y', 'climb_hold_until', 'wall_jump_until', 'wall_jump_vx',
                 'climb_dismount_until', 'crouch_until', 'crouch_release',
                 'deck_exit_mode', 'ledge_exit_phase', 'deck_exit_armed',
                 'deck_rim_turns', 'deck_rim_decided',
                 'charge_ready_at', 'charge_active', 'charge_facing',
                 'burst_remaining', 'patrol_direction', 'patrol_until', 'patrol_in_reach',
                 'ship_jump_vx', 'ship_jump_until', 'ship_jump_pass',
                 'ledge_patrol_id', 'activated',
                 'ship_passes', 'boarded_pass', 'ledge_leave_at', 'create_vid',
                 'ledge_exit_direction', 'bomb_armed',
                 'bomb_settle_until', 'bomb_direction', 'flight', 'portal_id')

    def __init__(self, objid, vid, stats, x, y, width=25600, height=46080,
                 rng=None, room=None, rules=constants, balance=None):
        self.rules = rules
        self.balance = balance if balance is not None else game_settings.Balance()
        self.objid = int(objid)
        self.vid = vid
        self.name = vid
        self.rng = rng or random
        self.kind = stats.get('kind', 'melee')
        self.x = int(x)
        self.y = int(y)
        self.w = int(width)
        self.h = int(height)

        self.hp = self.balance.hp(int(stats.get('hp', 375)))
        self.armor = int(stats.get('armor', 0))
        self.speed = self.balance.speed(int(stats.get('speed', 46 * 256)))
        self.contact = self.balance.damage(int(stats.get('contact', 20)))
        self.alive = True
        self.activated = False
        self.facing = -1
        self.attacks = list(stats.get('attacks') or [])
        self.rule = dict(stats.get('rule') or {})
        self.emits = []
        self.emits_done = 0
        self.attack_until = 0.0
        self.attack_started = 0.0
        interval = self.rule.get('seconds') or\
            (self.attacks[0]['time_ms'] / 1000.0 if self.attacks else 2.0)
        self.shot_cd = float(interval)
        self.shot_timer = self.rng.uniform(0.4, max(0.5, self.shot_cd))
        self.knockback = 0
        self.age = 0.0
        self.emitted = False
        self.contact_cd = 0.0
        self.launch_until = 0.0
        self.launch_vx = 0.0
        self.launch_vy = 0.0
        self.motion = GroundMotion(room)
        self.state = 'falling'
        self.pose = 1
        self.next_jump = 0.0
        self.recovery_until = 0.0
        self.run_ready_at = 0.0
        self.attack = None
        self.attack_target = None
        self.summon_timer = self.rng.uniform(0.214, 2.084) if vid == 'hulk.scavengerengineer' else 0.0
        self.summoned_objid = None
        self.flying = vid in ('hulk.bdrone', 'hulk.sdrone')
        self.flight = AircraftMotion(self) if self.flying else None
        if self.flying:

            self.shot_timer = 0.0
        self.melee_hit_done = False
        self.coop_attack = None
        self.coop_melee_hits = set()
        self.coop_contact_due = None
        self.climb_side = 0
        self.climb_started = None
        self.climb_route = None
        self.climb_start_y = None
        self.climb_hold_until = 0.0
        self.climb_dismount_until = 0.0
        self.wall_jump_until = 0.0
        self.wall_jump_vx = 0.0
        self.charge_ready_at = None
        self.charge_active = False
        self.charge_facing = -1
        self.burst_remaining = 0
        self.patrol_direction = self.rng.choice((-1, 1))
        self.patrol_until = 0.0
        self.patrol_in_reach = False
        self.ship_jump_vx = 0.0
        self.ship_jump_until = 0.0
        self.ship_jump_pass = None
        self.ledge_patrol_id = None
        self.ship_passes = 0
        self.boarded_pass = None
        self.ledge_leave_at = None

        self.create_vid = None
        self.portal_id = None
        self.ledge_exit_direction = 0

        self.crouch_until = 0.0
        self.crouch_release = None
        self.deck_exit_mode = None

        self.ledge_exit_phase = None
        self.deck_exit_armed = False
        self.deck_rim_turns = 0
        self.deck_rim_decided = False
        self.bomb_armed = vid == 'hulk.pathlightarmed'
        self.bomb_settle_until = 0.0
        self.bomb_direction = 0
        if room is not None:
            self.motion.step(self, 0.0, 0.0, 0.0)

    def rect(self):
        return (int(self.x), int(self.y), self.w, self.h)

    def center(self):
        return (self.x + self.w // 2, self.y + self.h // 2)

    def step(self, dt, player, now):
        """Advance one tick; return the events this unit produced."""
        results = []
        self.age += dt
        if self.contact_cd > 0:
            self.contact_cd = max(0.0, self.contact_cd - dt)
        if not self.alive:
            return results
        if not self.motion.grounded or player is None or not player.alive:
            self.patrol_in_reach = False
        self._observe_ship_support(now)
        if self.kind == 'bomb-rover':
            self._step_bomb_rover(dt, player, now, results)
            return results

        self.shot_timer = max(0.0, self.shot_timer - dt)
        if self.shot_timer<1e-9:self.shot_timer=0.0
        if self.flying:
            self._step_aircraft(dt, player, now, results)
            return results
        if self.age < self.launch_until:
            if self.age <= dt + 1e-6:
                self.motion.jump(abs(self.launch_vy))

            boost = self.age <= 0.22
            if boost:
                self.motion.vy = self.launch_vy - .5*self.motion.gravity*dt
            self.motion.step(self, dt, 0 if boost else self.launch_vx, now)
            self.state, self.pose = 'launch', 3
            return results
        if self.crouch_until or self.crouch_release is not None:

            self._step_crouch(dt, now, results)
            return results
        if self.charge_active or self.charge_ready_at is not None:
            if self._step_charge(dt, player, now, results):
                return results
        if now < self.ship_jump_until:
            self._step_ship_return(dt,player,now,results)
            return results
        if (self.motion.passive and not self.motion.grounded and
                self.vid not in ('hulk.scavengerloader','hulk.eloader') and
                'shredder' not in self.vid):
            self._step_ledge_flight(dt,player,now,results)
            return results
        if (self.vid in ('hulk.scavengerloader', 'hulk.eloader') and
                self.state == 'charge-recovery' and now < self.recovery_until):

            support = (self.motion.room.object(self.motion.support)
                       if self.motion.room is not None and self.motion.support is not None
                       else None)
            on_ledge = support is not None and terrain_type(support) == 2
            self.motion.step(self, dt, self._travel_speed(self.charge_facing,False)*self.charge_facing,
                             now, avoid_edges=on_ledge)
            self.facing = self.charge_facing
            self.pose = 5 if self.motion.grounded else 1
            return results
        if player is None or not player.alive:
            if not self.rule.get('idle_patrol',True):
                self.motion.step(self,dt,0.0,now)
                self.state,self.pose='idle',4 if self.motion.grounded else 1
                return results
            vx=self._patrol_velocity(now)
            self.motion.step(self,dt,vx,now,avoid_edges=(self._deck_support() is not None or 'shredder' not in self.vid))
            self.facing=1 if vx>0 else -1
            self.state='patrol' if self.motion.grounded else 'falling'
            if not self.motion.grounded:

                self.pose=self._air_pose()
            elif self.vid in ('hulk.scavengerloader','hulk.eloader'):
                self.pose=5
            elif self.vid=='hulk.scavengerengineer':
                self.pose=5
            elif self.vid in GROUND_SHOOTER_VIDS | SHREDDER_VIDS and abs(vx)<30000:
                self.pose=5
            else:
                self.pose=6
            if self.motion.edge_blocked:
                self.state,self.pose='edge-hold',4
            return results
        target = player.center()
        cx, cy = self.center()
        dx = target[0] - cx
        dy = target[1] - cy
        distance = math.hypot(dx, dy) or 1.0
        if self.climb_dismount_until > 0.0:
            self._step_wall_dismount(dt, player, now, results)
            return results
        if self.climb_hold_until > 0.0:
            self._step_wall_climb_hold(dt, player, now, results)
            return results
        if self.climb_started is not None:
            self._step_wall_climb(dt, player, now, results)
            return results
        if now < self.wall_jump_until:
            self.motion.step(self, dt, self.wall_jump_vx, now)
            self.state, self.pose = 'wall-jump', 3
            if self.motion.grounded:
                self.wall_jump_until = 0.0
            return results
        if 'shredder' in self.vid and not self.motion.grounded:

            self._step_ledge_flight(dt,player,now,results)
            return results
        if now < self.attack_until:
            self.motion.step(self, dt, 0.0, now)
            self.state = 'attack'
            self.pose = 4 if self.motion.grounded else 3 if self.motion.vy<0 else 1
            results.extend(self._emit_due(now, player))
            return results
        if self.attack:

            results.extend(self._emit_due(self.attack_until, player))
            self.attack = None
            self.patrol_in_reach = False
            if self.deck_exit_armed and self.ledge_exit_phase != 'stepping':

                self.ledge_exit_phase = 'seeking-rim'
        if self._maybe_begin_deck_departure(now, player):

            self.motion.step(self, dt, 0.0, now, avoid_edges=True)
            self.state, self.pose = 'crouch', 7
            return results
        melee = self.kind == 'melee' or 'shredder' in self.vid or self.vid == 'hulk.scavengerengineer'
        reach = float(self.rule.get('threshold') or 90) * 256
        if self.vid.endswith('scavengerloader'):
            reach = 160 * 256
        visible = self.motion.visible(self.center(), target)

        if (not self.activated
                and distance<self.rules.SCAVENGER_ACTIVATION_RANGE
                and visible):
            self.activated=True
        engaged=distance<self.rules.SCAVENGER_AGGRO_RANGE and visible
        activated=self.activated
        if melee:
            in_reach = abs(dx) <= reach and abs(dy) < self.h
        else:

            band=self.rules.SCAVENGER_ATTACK_RANGE.get(self.vid)
            in_reach=visible and distance<=(band if band is not None
                                             else self.rules.SCAVENGER_AGGRO_RANGE)
        if now<self.recovery_until-1e-9 and self.vid in GROUND_STATIONARY_RECOVERY_VIDS:

            self.motion.step(self,dt,0.0 if self.motion.grounded else self.motion.vx,now)
            self.state='attack-recover' if self.motion.grounded else 'falling'
            self.pose=4 if self.motion.grounded else 3 if self.motion.vy<0 else 1
            return results
        if self.motion.grounded:

            self.facing = -1 if dx < 0 else 1
        if in_reach and self.motion.grounded and not self.patrol_in_reach:

            self.patrol_direction = self.facing
        self.patrol_in_reach = bool(in_reach and self.motion.grounded)
        if (self.attacks and self.shot_timer <= 0 and now+1e-9 >= self.recovery_until
                and in_reach and self.motion.grounded
                and not self.ledge_exit_direction and self.ledge_exit_phase != 'seeking-rim'):
            self._begin_attack(now, player, results)

            self.motion.step(self,dt,0.0,now)
            self.state = 'attack'
            self.pose = 4 if self.motion.grounded else 3 if self.motion.vy<0 else 1
            return results
        vx = 0.0

        retreating=self.vid=='hulk.scavengerengineer' and activated
        if now >= self.recovery_until or self.vid not in ('hulk.scavengerloader', 'hulk.eloader'):
            if retreating:

                vx = -self._travel_speed(-self.facing,False) * self.facing
            elif self.vid in ('hulk.scavengerloader', 'hulk.eloader') and self._deck_support() is not None:
                vx = self._loader_deck_patrol(now)
            elif melee and ('shredder' in self.vid or engaged or not self.rule.get('idle_patrol',True)):
                if (self.vid in SHREDDER_VIDS and self.motion.grounded
                        and self._deck_support() is not None
                        and (not self._deck_leave_allowed(player) or
                             self.deck_rim_turns < self.rules.SHREDDER_DECK_MIN_RIM_TURNS)):

                    vx = self._patrol_velocity(now, running=True)
                elif abs(dx) > max(8 * 256, reach * 0.65):

                    vx = self._travel_speed(self.facing, self.activated or self.vid in SHREDDER_VIDS)*self.facing
            else:

                vx=self._patrol_velocity(now,running=activated)
        was_grounded = self.motion.grounded
        higher = (self.y + self.h) - (player.ymp + player.hmp)
        if 'shredder' in self.vid:

            jump_height=jump_speed(self.vid)**2/(2*self.motion.gravity)
            if self.climb_route is not None and not self._climb_route_reachable(*self.climb_route):
                self.climb_route = None
            if higher > jump_height-24*256 and engaged and self.climb_route is None:
                self.climb_route = self._nearest_climb_route()
            elif higher <= 40 * 256 or not engaged:
                self.climb_route = None
            if self.climb_route is not None:
                side, wall_x = self.climb_route
                self.facing = side
                vx = self._travel_speed(side,True)*side
                if abs((self.x+self.w if side > 0 else self.x)-wall_x) < 512:
                    wall = self._touching_wall(side)
                    if wall is not None:
                        self.climb_side, self.climb_started = side, now
                        self.climb_start_y = self.y
                        self.motion.support = self.motion.support_rect = None
                        self.motion.grounded = False
                        self.motion.vy = 0.0
                        self.state, self.pose = 'wall-climb', 2
                        results.append({'kind':'enemy-attack','objid':self.objid,
                            'vid':self.vid,'anim':8,'time_ms':5346})
                        return results
        if (was_grounded and now >= self.next_jump and 'shredder' in self.vid):
            if self.climb_route is None and (self.motion.blocked or
                    (higher > 40 * 256 and abs(dx) < 400 * 256)):

                self.motion.jump(jump_speed(self.vid),self)

                self.next_jump = now + (
                    self.rules.SHREDDER_PURSUIT_JUMP_INTERVAL
                    if higher > 40 * 256 and abs(dx) < 400 * 256 else 1.0)

        avoid_edges = self._platform_edge_policy(now, player)
        if self.crouch_release is not None:

            self.motion.step(self, dt, 0.0, now, avoid_edges=True)
            self.state, self.pose = 'crouch', 7
            return results
        if self.ledge_exit_direction:
            vx = self._travel_speed(self.ledge_exit_direction,True)*self.ledge_exit_direction
        if vx and self.vid in SHREDDER_VIDS:
            vx=self._movement_velocity(1 if vx>0 else -1,now)
        self.motion.step(self, dt, vx, now, avoid_edges=avoid_edges)
        if was_grounded and vx and not retreating:

            self.facing = 1 if vx>0 else -1
        self.state = ('recover' if now < self.recovery_until else
                      'chase' if self.motion.grounded and vx else
                      'hold' if self.motion.grounded else
                      'ledge-departure' if self.motion.passive else
                      'jump' if self.motion.jumping else 'falling')
        self.pose = (6 if self.motion.grounded and vx else 4 if self.motion.grounded
                     else self._air_pose())
        if self.motion.grounded and vx and self.vid in GROUND_SHOOTER_VIDS | SHREDDER_VIDS and abs(vx)<30000:
            self.pose=5
        if self.motion.grounded and vx and self.vid in (
                'hulk.scavengerloader', 'hulk.eloader'):

            self.pose = 5
        if self.motion.grounded and vx and self.vid=='hulk.scavengerengineer':
            self.pose = 5
        if self.motion.grounded and self.motion.edge_blocked:
            self.state, self.pose = 'edge-hold', 4
        return results

    def _air_pose(self):
        'runtime airborne pose: 3 while rising/jumping, else 1 (POSE_AIR).'
        if self.motion.jumping or self.motion.vy < 0:
            return 3
        return 1

    def _travel_speed(self,direction,running=True):
        speeds = getattr(self.rules, 'HORIZONTAL_SPEEDS', {}).get(self.vid)
        if speeds:
            if self.vid in ('hulk.scavengerloader', 'hulk.eloader'):
                running = running and self.charge_active
            if self.vid == 'hulk.scavengerengineer':
                running = False
            return self.balance.speed(
                speeds['run' if running else 'walk'][direction])
        if self.vid in SHREDDER_VIDS or self.vid in ('hulk.scavengerloader','hulk.eloader'):
            family='shredder' if self.vid in SHREDDER_VIDS else 'loader'
            if family == 'loader':
                running = running and self.charge_active
            return self.balance.speed(
                HEAVY_HORIZONTAL_SPEEDS[family]['run' if running else 'walk'][direction])
        if self.vid in GROUND_SHOOTER_VIDS or self.vid=='hulk.scavengerengineer':
            mode='run' if running and self.vid!='hulk.scavengerengineer' else 'walk'
            return self.balance.speed(GROUND_HORIZONTAL_SPEEDS[mode][direction])

        return self.speed

    def _movement_velocity(self,direction,now,running=True):
        if self.vid in SHREDDER_VIDS and self.motion.grounded:
            previous=1 if self.motion.vx>0 else -1 if self.motion.vx<0 else 0
            if previous!=direction:
                self.run_ready_at=now+SHREDDER_STARTUP_WALK
            running=running and now+1e-9>=self.run_ready_at
        return self._travel_speed(direction,running)*direction

    def _loader_deck_patrol(self, now):
        """Short walking patrol at the left of the current contiguous shelf.

        The interval is relative to the moving support, never a world-space
        waypoint. Explicit attacks/charges are handled before this branch.
        """
        support = self._deck_support()
        if support is None:
            return self._patrol_velocity(now, running=False)
        left, top, width, _ = support.rect()
        right = left + width
        pieces = [item.rect() for item in self.motion.room.objects.values()
                  if terrain_type(item) == 2 and abs(item.rect()[1]-top) <= 256]

        changed = True
        while changed:
            changed = False
            for x, _, w, _ in pieces:
                if x <= right+256 and x+w >= left-256:
                    new_left, new_right = min(left, x), max(right, x+w)
                    if (new_left, new_right) != (left, right):
                        left, right, changed = new_left, new_right, True
        low = left + self.w/2
        high = max(low, min(right-self.w/2, low+self.rules.LOADER_DECK_PATROL_WIDTH))
        center = self.center()[0]
        if center <= low+256:
            self.patrol_direction = 1
        elif center >= high:
            self.patrol_direction = -1
        self.facing = self.patrol_direction
        return self._travel_speed(self.patrol_direction, False)*self.patrol_direction

    def _patrol_velocity(self,now,running=None):

        if running is None:
            running=self.activated
        elif self.activated:
            running=True
        if self.motion.blocked or self.motion.edge_blocked:
            self.patrol_direction*=-1
            self.patrol_until=0.0
            if self.vid in SHREDDER_VIDS and self._deck_support() is not None:

                self.deck_rim_turns += 1
        return self._movement_velocity(self.patrol_direction,now,running=running or self.vid in SHREDDER_VIDS)

    def _step_ledge_flight(self,dt,player,now,results):

        self.motion.step(self,dt,self.motion.vx,now)
        self.state=('jump' if self.motion.jumping else 'ledge-departure') if not self.motion.grounded else 'hold'

        self.pose=(4 if self.motion.grounded else self._air_pose())
        if self.attack:
            results.extend(self._emit_due(min(now,self.attack_until),player))
            if now<self.attack_until:
                self.state='airborne-shot' if not self.motion.grounded else 'attack'
                return
            self.attack=None
        band=self.rules.SCAVENGER_ATTACK_RANGE.get(
            self.vid, self.rules.SCAVENGER_AGGRO_RANGE)
        if (not self.motion.grounded and player is not None and player.alive and
                self.kind!='melee' and self.vid!='hulk.scavengerengineer' and
                self.attacks and self.shot_timer<=0 and now>=self.recovery_until and
                _distance(self.center(),player.center())<=band and
                self.motion.visible(self.center(),player.center())):
            self._begin_attack(now,player,results)
            self.state='airborne-shot'

    def _step_ship_return(self,dt,player,now,results):
        self.motion.step(self,dt,self.ship_jump_vx,now)
        self.state='ship-return-jump'
        self.pose=4 if self.motion.grounded else 3
        if self.motion.grounded:
            self.ship_jump_until=0.0

        if self.attack:
            results.extend(self._emit_due(min(now,self.attack_until),player))
            if now<self.attack_until:
                return
            self.attack=None
        band=self.rules.SCAVENGER_ATTACK_RANGE.get(
            self.vid, self.rules.SCAVENGER_AGGRO_RANGE)
        if (player is not None and player.alive and self.kind!='melee' and
                self.vid!='hulk.scavengerengineer' and self.attacks and
                self.shot_timer<=0 and now>=self.recovery_until and
                _distance(self.center(),player.center())<=band and
                self.motion.visible(self.center(),player.center())):
            self._begin_attack(now,player,results)

    def _step_bomb_rover(self, dt, player, now, results):

        if not self.bomb_armed:
            self.motion.step(self, dt, 0.0, now)
            self.state, self.pose = 'bomb-falling', 1
            if self.motion.grounded:
                self.bomb_armed = True
                self.vid = 'hulk.pathlightarmed'
                self.hp = self.balance.hp(280)
                self.contact = self.balance.damage(40)
                self.bomb_settle_until = now+.3
                self.state, self.pose = 'bomb-arming', 4
            return
        if not self.bomb_direction:
            self.bomb_direction = (-1 if player is not None and
                player.center()[0] < self.center()[0] else 1)
        self.facing = self.bomb_direction
        if now < self.bomb_settle_until:
            self.motion.step(self, dt, 0.0, now)
            self.state, self.pose = 'bomb-arming', 4
            return
        self.motion.step(self, dt, self.speed*self.bomb_direction, now)
        self.state = 'bomb-roll'
        self.pose = 5 if self.motion.grounded else 3 if self.motion.vy < 0 else 1
        if self.motion.blocked:

            self.alive, self.state = False, 'bomb-detonated'
            results.append(dict(kind='enemy-bomb-explode', unit=self))

    def _observe_ship_support(self,now=0.0):
        if not self.motion.grounded or not self.motion.room or self.motion.support is None:
            return
        support=self.motion.room.objects.get(self.motion.support)
        if support is None:
            return
        if terrain_type(support)==1:
            self.boarded_pass=self.ledge_leave_at=None
            self.ledge_exit_direction=0
            self.ledge_exit_phase=None
            self.deck_exit_armed=False
            self.deck_rim_turns=0
            self.deck_rim_decided=False
        elif terrain_type(support)==2 and self.boarded_pass is None:
            self.boarded_pass=self.ship_passes

            if self.vid in SHREDDER_VIDS:
                self.ledge_leave_at=self.age+draw_dwell(
                    self.rng, self.rules.SHREDDER_DWELL_QUANTILES,
                    self.rules.SHREDDER_DWELL_MINIMUM)
                runoff=self.rules.SHREDDER_RUNOFF_PROBABILITY
            else:
                self.ledge_leave_at=self.age+draw_dwell(self.rng)
                runoff=self.rules.SCAVENGER_RUNOFF_PROBABILITY

            self.deck_exit_mode=('run-off' if self.rng.random()<runoff
                                 else 'drop')
            self.deck_rim_turns=0
            self.deck_exit_armed=False
            self.deck_rim_decided=False

    def _room_object(self, objid):
        """Resolve a room object without requiring the cached accessor."""
        room = self.motion.room
        if room is None or objid is None:
            return None
        getter = getattr(room, 'object', None)
        if getter is not None:
            return getter(objid)
        return (getattr(room, 'objects', {}) or {}).get(objid)

    def _deck_support(self):
        """The one-way deck this body is standing on, or None."""
        if self.motion.room is None or self.motion.support is None:
            return None
        support = self._room_object(self.motion.support)
        if support is None or terrain_type(support) != 2:
            return None
        return support

    def _deck_leave_allowed(self, player):
        'Individual residence over, with a target below the deck.'
        if self.boarded_pass is None or self.ledge_leave_at is None:
            return False
        if self.age+1e-9 < self.ledge_leave_at:
            return False
        if player is None or not player.alive:
            return False
        return player.ymp+player.hmp > self.y+self.h+32*256

    def _maybe_begin_deck_departure(self, now, player):
        "Start this body's own deck exit when its residence is over."
        if (self.flying or not self.motion.grounded or
                self._deck_support() is None):
            return False

        if self.ledge_exit_phase == 'stepping' or self.ledge_exit_direction:
            return False
        if self.vid == 'hulk.scavengerengineer':

            return False
        if self.vid in ('hulk.scavengerloader', 'hulk.eloader'):
            return False
        if not self._deck_leave_allowed(player):
            self.deck_exit_armed = False
            if self.ledge_exit_phase == 'seeking-rim':
                self.ledge_exit_phase = None
            return False
        if self.vid in SHREDDER_VIDS:

            if self.deck_rim_turns < self.rules.SHREDDER_DECK_MIN_RIM_TURNS:
                return False
            self.facing = -1 if player.center()[0] < self.center()[0] else 1
            self.attack = None
            if self.deck_exit_mode=='run-off':
                self.ledge_exit_direction=self.facing
                self.ledge_exit_phase='stepping'
                return False
            self._begin_crouch(now, 'drop')
            return True

        if not self.deck_exit_armed:
            self.deck_exit_armed = True
            self.ledge_exit_phase = 'seeking-rim'
        if not self.motion.edge_blocked:
            self.deck_rim_decided = False
        if self.motion.edge_blocked:
            if self.deck_rim_decided:
                return False
            self.deck_rim_decided = True

            direction = (1 if self.motion.requested_vx > 0 else -1
                         if self.motion.requested_vx < 0 else self.patrol_direction)
            if (self.deck_exit_mode=='run-off' or
                    self.rng.random()<self.rules.DECK_EXIT_ROLL_PROBABILITY):
                self.ledge_exit_phase='stepping'
                self.ledge_exit_direction=direction
                return False
            self.ledge_exit_phase = None

        return False

    def _begin_crouch(self, now, release):
        'Start the runtime POSE_CROUCH (client pose 7) on the support.'
        if self.crouch_release is not None:
            return False
        self.crouch_until = now+self.rules.SCAVENGER_CROUCH_SECONDS
        self.crouch_release = release
        self.state, self.pose = 'crouch', 7
        self.attack = None
        return True

    def _step_crouch(self, dt, now, results):

        self.motion.step(self, dt, 0.0, now, avoid_edges=True)
        self.state, self.pose = 'crouch', 7
        if now+1e-9 < self.crouch_until:
            return
        release, self.crouch_release = self.crouch_release, None
        self.crouch_until = 0.0
        if release == 'drop' and self.motion.grounded:

            self.motion.drop(now)
            self.motion.passive = True
            self.motion.walked_off = True
            self.ledge_exit_direction = 0
        elif release == 'step-off':

            self.ledge_exit_phase = 'stepping'
        self.state, self.pose = 'crouch-release', self._air_pose()

    def _platform_edge_policy(self, now, player):

        if self.vid == 'hulk.scavengerengineer':
            return True
        shredder = self.vid in SHREDDER_VIDS
        support = self._room_object(self.motion.support)
        if support is None or terrain_type(support) != 2:
            self.ledge_patrol_id = None
            self.ledge_exit_direction = 0

            return not shredder
        movement = support.get('move_', {}) or {}
        relative = movement.get('to_') or {}
        row = ('ship-row', relative['ymp_']) if 'ymp_' in relative else ('ledge',support.objid)
        if row != self.ledge_patrol_id:
            self.ledge_patrol_id = row

            if self.ledge_exit_phase != 'stepping':
                self.ledge_exit_direction = 0
        self._observe_ship_support(now)

        if self.vid in ('hulk.scavengerloader', 'hulk.eloader'):
            return True
        if self._maybe_begin_deck_departure(now, player):
            return True
        if self.ledge_exit_phase=='stepping':

            return False
        if self.ledge_exit_direction:

            return True
        if shredder:

            return True
        return True

    def _step_charge(self, dt, player, now, results):
        'Observed 990-ms cue -> 400-ms settle -> fixed-direction RUN.'
        if self.charge_ready_at is not None:
            if now < self.attack_until:
                return False
            if now < self.charge_ready_at:
                self.motion.step(self, dt, 0.0, now)
                self.state, self.pose = 'charge-settle', 4
                return True
            self.charge_active, self.charge_ready_at = True, None
            self.attack = None
            results.append(dict(kind='enemy-charge-start', objid=self.objid, vid=self.vid))
        self.facing = self.charge_facing

        support = (self.motion.room.object(self.motion.support)
                   if self.motion.room is not None and self.motion.support is not None
                   else None)
        on_ledge = support is not None and terrain_type(support) == 2
        self.motion.step(self, dt, self._travel_speed(self.charge_facing,True)*self.charge_facing,
                         now, avoid_edges=on_ledge)
        self.state, self.pose = 'charge', 6 if self.motion.grounded else 1
        behind = player is not None and self.charge_facing*(player.center()[0]-self.center()[0]) < 0
        if (self.motion.blocked or self.motion.edge_blocked or behind
                or player is None or not player.alive):
            self.charge_active = False
            self.recovery_until = now+1.6
            self.shot_timer = 1.6
            self.state = 'charge-recovery'
            results.append(dict(kind='enemy-charge-end', objid=self.objid, vid=self.vid,
                reason='edge' if self.motion.edge_blocked else
                       'wall' if self.motion.blocked else
                       'target-crossed' if behind else 'no-target'))
        return True

    def _nearest_climb_route(self):
        if self.motion.room is None:
            return None
        choices = []
        for item in self.motion.room.objects.values():
            x,y,w,h = item.rect()
            if (terrain_type(item)!=1 or h < self.h or w > self.w
                    or self.y >= y+h or self.y+self.h <= y):
                continue
            if x >= self.x+self.w-512 and self._climb_route_reachable(1, x):
                choices.append((abs(x-self.x-self.w),1,x))
            elif x+w <= self.x+512 and self._climb_route_reachable(-1, x+w):
                choices.append((abs(self.x-x-w),-1,x+w))
        if not choices:
            return None
        _, side, wall_x = min(choices)
        return side, wall_x

    def _climb_route_reachable(self, side, wall_x):
        """A deck patrol cannot walk across a gap to a distant arena wall.

        Use the same contiguous support and center bounds as GroundMotion's
        edge guard. Otherwise pursuit overrides every patrol turn at the rim.
        Recheck moving/retracting decks rather than retaining a stale route.
        """
        support = self._deck_support()
        if support is None:
            return True
        left, top, width, _ = support.rect()
        right = left+width
        changed = True
        while changed:
            changed = False
            for _, (x, y, w, _), _ in self.motion.surfaces():
                if abs(y-top) <= 1 and x <= right and x+w >= left:
                    low, high = min(left, x), max(right, x+w)
                    if (low, high) != (left, right):
                        left, right, changed = low, high, True
        center_at_wall = wall_x-side*self.w/2
        return left+1 <= center_at_wall <= right-1

    def _touching_wall(self, side):
        if self.motion.room is None:
            return None
        for item in self.motion.room.objects.values():
            x,y,w,h = item.rect()
            boundary = x if side > 0 else x+w
            body_edge = self.x+self.w if side > 0 else self.x
            if (terrain_type(item)==1 and w <= self.w and h >= self.h
                    and abs(body_edge-boundary)<512 and self.y < y+h and self.y+self.h > y):
                return item
        return None

    def _climb_wall_candidates(self, side):
        """Wall segments at the current climb boundary, ignoring vertical span."""
        if self.motion.room is None:
            return []
        result = []
        for item in self.motion.room.objects.values():
            x, y, w, h = item.rect()
            if terrain_type(item) != 1 or w > self.w or h < self.h:
                continue
            boundary = x if side > 0 else x + w
            body_edge = self.x + self.w if side > 0 else self.x
            if abs(body_edge - boundary) < 512:
                result.append(item)
        return result

    def _climb_wall(self, side):
        """Nearest wall segment at the climb boundary, including a small gap.

        ``_touching_wall`` deliberately requires vertical overlap, which is
        correct for starting a climb but not while crossing a stack of wall
        pieces.  During the climb the body may be between two pieces; choose
        the nearest piece so the x pin and the effective top stay correct.
        """
        candidates = self._climb_wall_candidates(side)
        if not candidates:
            return None
        top, bottom = self.y, self.y + self.h
        best = best_distance = None
        for item in candidates:
            x, y, w, h = item.rect()
            if y <= bottom + 512 and y + h >= top - 512:
                distance = 0.0
            elif y + h < top:
                distance = top - (y + h)
            else:
                distance = y - bottom
            if best_distance is None or distance < best_distance:
                best, best_distance = item, distance
        return best

    def _climb_wall_top(self, side):
        """Merge contiguous wall pieces above the current piece.

        The right wall is several stacked CreateWorld objects (short platform
        piece, long wall piece, upper wall piece).  Using only the first
        piece's top made the body hold halfway up in place.  The monster must
        continue through contiguous pieces until the cap or the real top.
        """
        current = self._climb_wall(side)
        if current is None:
            return None
        top = current.rect()[1]
        candidates = self._climb_wall_candidates(side)
        changed = True
        while changed:
            changed = False
            for item in candidates:
                x, y, w, h = item.rect()
                if y < top - 512 and y + h >= top - 512:
                    top = y
                    changed = True
        return top

    def _step_wall_climb(self, dt, player, now, results):
        wall = self._climb_wall(self.climb_side)
        if wall is None:

            self._detach_from_wall(now, player, results)
            return

        if player is not None and player.alive and now >= self.climb_started:
            reach = float(self.rule.get('threshold') or 90) * 256
            px, py = player.center()
            cx, cy = self.center()
            if abs(px - cx) <= reach and abs(py - cy) < self.h:

                self._begin_wall_dismount(now, results)
                return
        wall_top = self._climb_wall_top(self.climb_side)
        cap_goal_y = self.climb_start_y - self.rules.SHREDDER_CLIMB_MAX_HEIGHT
        if wall_top is None:
            wall_goal_y = cap_goal_y
        else:

            wall_goal_y = wall_top - self.h - 24 * 256
            wall_goal_y = min(wall_goal_y, self.climb_start_y - 24 * 256)

        stop_y = max(cap_goal_y, wall_goal_y)
        if self.y <= stop_y + 1e-6:

            self.climb_started = self.climb_route = None
            self.climb_start_y = None
            self.climb_hold_until = now + max(
                0.0, self.rules.SHREDDER_CLIMB_TOP_HOLD
                - self.rules.SHREDDER_CLIMB_DISMOUNT_HOLD)
            self.state, self.pose = 'wall-climb-top', 2
            return
        x, y, w, h = wall.rect()
        self.x = x - self.w if self.climb_side > 0 else x + w
        self.y -= self.rules.SHREDDER_CLIMB_SPEED * dt
        self.facing = self.climb_side
        self.state, self.pose = 'wall-climb', 2

    def _step_wall_climb_hold(self, dt, player, now, results):

        self.state, self.pose = 'wall-climb-top', 2
        wall = self._climb_wall(self.climb_side)
        if wall is not None:
            x,y,w,h = wall.rect()
            self.x = x-self.w if self.climb_side>0 else x+w
        if now >= self.climb_hold_until:
            self._begin_wall_dismount(now, results)

    def _begin_wall_dismount(self, now, results):
        'anim10 is played while the body still clings to the wall.'
        self.climb_hold_until = 0.0
        self.climb_dismount_until = now + self.rules.SHREDDER_CLIMB_DISMOUNT_HOLD
        self.state, self.pose = 'wall-dismount', 2
        wall = self._climb_wall(self.climb_side)
        if wall is not None:
            x, y, w, h = wall.rect()
            self.x = x-self.w if self.climb_side > 0 else x+w
        results.append({'kind':'enemy-attack','objid':self.objid,
            'vid':self.vid,'anim':10,'time_ms':528})

    def _step_wall_dismount(self, dt, player, now, results):
        self.state, self.pose = 'wall-dismount', 2
        wall = self._climb_wall(self.climb_side)
        if wall is not None:
            x,y,w,h = wall.rect()
            self.x = x-self.w if self.climb_side>0 else x+w
        if now+1e-9 >= self.climb_dismount_until:
            self._detach_from_wall(now, player, results)

    def _detach_from_wall(self, now, player, results):
        self.climb_started = self.climb_route = None
        self.climb_start_y = None
        self.climb_hold_until = 0.0
        self.climb_dismount_until = 0.0
        if player is not None and player.alive:
            self.facing = -1 if player.center()[0] < self.center()[0] else 1

        self.motion.jump(0.0)
        self.wall_jump_vx = self.facing * self._travel_speed(self.facing,True)
        self.wall_jump_until = now+1.2
        self.next_jump = now+1.5
        self.state, self.pose = 'wall-jump', 3

    def _step_aircraft(self, dt, player, now, results):
        self.flight.step(self, dt, player, now)

        if now < self.attack_until:
            self.state = 'aircraft-attack'
            results.extend(self._emit_due(now, player))
            return
        if self.attack:
            results.extend(self._emit_due(self.attack_until, player))
            self.attack = None
        if (self.flight.active and now >= self.flight.ready_at and self.attacks
                and self.shot_timer <= 0 and now >= self.recovery_until
                and player is not None and player.alive):

            self._begin_attack(now, player, results)
            self.state = 'aircraft-attack'

    def _begin_attack(self, now, player, results, selected=None):
        if not self.attacks:
            return
        choices = [selected] if selected is not None else self.attacks

        directed = [a for a in choices if a.get('points') and a.get('emit') in
                    ('hulk.sgmbullet', 'hulk.sshot', 'hulk.smissile') and not self.flying]
        if directed:
            tx, ty = player.center()
            cx, cy = self.center()
            wanted = math.degrees(math.atan2(ty-cy, abs(tx-cx) or 1))
            def angular_error(a):
                angle = float(a['points'][0].get('angle') or 0)
                angle = (angle + 180) % 360 - 180
                return abs(angle - wanted)
            best = min(angular_error(a) for a in directed)
            choices = [a for a in directed if angular_error(a) <= best + 1]

            if self.vid == 'hulk.scavengercommando' and self.rng.random() < 0.25:
                choices = [a for a in self.attacks if a['anim'] == 0] or choices
        weights = [max(1, a.get('weight', 1)) for a in choices]
        roll = self.rng.random() * sum(weights)
        attack = choices[-1]
        for candidate, weight in zip(choices, weights):
            roll -= weight
            if roll <= 0:
                attack = candidate
                break
        self.attack = attack
        self.attack_target = player.center()
        self.melee_hit_done = False
        self.attack_started = now
        self.attack_until = now + attack['time_ms'] / 1000.0
        if attack.get('charge'):
            self.charge_ready_at = self.attack_until + 0.4
            self.charge_facing = self.facing
        points = list(attack.get('points') or [])
        angles = sorted(float(point.get('angle') or 0) for point in points)
        centre = angles[len(angles) // 2] if angles else 0.0
        self.emits = [(
            point.get('timeMs', point.get('time', 0)) / 1000.0,
            dict(point, _spread=(float(point.get('angle') or 0) - centre)),
            attack['emit'],
        ) for point in points]
        self.emits.sort(key=lambda item: item[0])
        if attack.get('emit') == 'hulk.sshot' and len(self.emits) > 3:

            count = self.rng.randint(3, len(self.emits))
            self.emits = sorted(self.rng.sample(self.emits, count),
                                key=lambda item: item[0])
        self.emits_done = 0

        recovery = 0.35 if self.kind == 'melee' else 0.5
        if 'recovery' in attack:recovery=float(attack['recovery'])
        if self.vid == 'hulk.sdrone':

            recovery = 0.0
        elif self.vid == 'hulk.bdrone':

            recovery = 0.25
        self.recovery_until = self.attack_until + recovery
        self.shot_timer = max(0.05, float(self.rule.get('seconds')
                                          or self.shot_cd))
        if self.vid == 'hulk.sdrone':

            if self.burst_remaining == 0:
                self.burst_remaining = self.rng.randint(5, 10)
            self.burst_remaining -= 1
            self.shot_timer = (2.3 if self.burst_remaining == 0 else
                               0.16 if self.rng.random() < 0.75 else 0.2)
        results.append({
            'kind': 'enemy-attack',
            'objid': self.objid,
            'vid': self.vid,
            'anim': attack['anim'],
            'time_ms': attack['time_ms'],
        })
        results.extend(self._emit_due(now, player))

    def _emit_due(self, now, player):
        results = []
        elapsed = now - self.attack_started
        while (self.emits_done < len(self.emits)
               and elapsed >= self.emits[self.emits_done][0]):
            _at, point, emit_vid = self.emits[self.emits_done]
            self.emits_done += 1
            results.append(self._spawn_emit(point, emit_vid, player))
        return results

    def attack_rects(self, now):
        """Animation hit regions, in native world units and current facing."""
        if (not self.attack or (self.kind != 'melee' and self.vid != 'hulk.scavengerengineer')
                or now >= self.attack_until):
            return []
        at=(now-self.attack_started)*1000
        boxes=self.attack.get('boxes') or []
        result=[]
        for box in boxes:
            if box.get('type')!='BOX_HIT' or not (box['time'] <= at < box['time']+box['length']):
                continue
            x=self.x+self.w/2
            if self.facing>0:
                x+=box['x']*128
            else:
                x-=(box['x']+box['w'])*128
            result.append((x,self.y+self.h-128-box['y']*128,box['w']*128,box['h']*128))
        return result

    def _spawn_emit(self, point, emit_vid, player):
        facing = self.facing or -1
        scale = int(point.get('_scale', DEFAULT_POINT_SCALE))
        x = self.x + self.w / 2 + facing * int(point.get('x', 0)) * scale
        y = self.y + self.h - 128 - int(point.get('y', 0)) * scale
        target = self.attack_target or player.center()
        aim = math.degrees(math.atan2(target[1] - y, target[0] - x))

        if (emit_vid in ('hulk.sgmbullet', 'hulk.sshot', 'hulk.smissile') and
                (not self.flying or self.vid == 'hulk.sdrone')):
            angle = float(point.get('angle') or 0)
            aim = angle if facing > 0 else 180.0 - angle
            if emit_vid == 'hulk.sshot':
                aim += self.rng.uniform(-18.0, 18.0)
        elif emit_vid in ('hulk.flame', 'hulk.rpg'):
            aim = 0.0 if facing > 0 else 180.0
        elif emit_vid in ('hulk.ngrenade', 'hulk.boundinggrenade'):
            aim = -58.0 if facing > 0 else -122.0
        event = {
            'kind': 'enemy-shot',
            'vid': emit_vid,
            'owner': self.objid,
            'origin': (int(x), int(y)),
            'aim': aim,
        }
        if self.vid == 'hulk.eloader' and emit_vid == 'hulk.flame':
            slot = self.attack['anim'] if self.attack else 4

            diagonal = slot == 5
            event['native_move'] = loader_flame_move(slot,facing)
            event['aim'] = (-45 if facing>0 else -135) if diagonal else aim
        return event

class UnitList(list):
    """Ordered runtime units with a revision for stable object-ID lookup."""
    def __init__(self,items=()):
        super().__init__(items);self.version=0

    def _mutate(self,operation,*args,**kwargs):
        try:return operation(*args,**kwargs)
        finally:self.version+=1

    def append(self,value):return self._mutate(super().append,value)
    def extend(self,values):return self._mutate(super().extend,values)
    def insert(self,index,value):return self._mutate(super().insert,index,value)
    def pop(self,index=-1):return self._mutate(super().pop,index)
    def remove(self,value):return self._mutate(super().remove,value)
    def clear(self):return self._mutate(super().clear)
    def reverse(self):return self._mutate(super().reverse)
    def sort(self,*args,**kwargs):return self._mutate(super().sort,*args,**kwargs)
    def __setitem__(self,index,value):return self._mutate(super().__setitem__,index,value)
    def __delitem__(self,index):return self._mutate(super().__delitem__,index)
    def __iadd__(self,values):return self._mutate(super().__iadd__,values)
    def __imul__(self,count):return self._mutate(super().__imul__,count)

class EnemyManager(object):
    def __init__(self, room, projectiles, seed=None, first_objid=30000,
                 rules=constants, behaviour=None, balance=None,spawn_baseline=1.0):
        self.rules = rules
        self.behaviour = BEHAVIOUR if behaviour is None else behaviour
        self.room = room
        self.projectiles = projectiles
        self.random = random.Random(seed)
        self.balance = balance if balance is not None else game_settings.Balance()
        self.spawn_baseline=float(spawn_baseline)
        self.spawn_population_multiplier=1.0
        self.units = UnitList()
        self.next_objid = int(first_objid)
        self.wave_timer = self.first_wave_delay()
        self.waves = 0
        self.enabled = False
        self.portal_ids = list(self.rules.PORTAL_IDS)
        self.room_time = 0.0
        self.pending = []
        self.portal_due = {}

        self.portal_refills = []
        self.ship_return_check_at=0.0
        self.ship_passes=0

    def ship_pass_completed(self):
        self.ship_passes+=1

    def allocate(self):
        self.next_objid += 1
        return self.next_objid

    def find_unit(self,objid,alive_only=False):
        units=self.units
        if not isinstance(units,UnitList):

            return next((u for u in units if u.objid==objid and (not alive_only or u.alive)),None)
        if (getattr(self,'_unit_index_table',None)is not units
                or getattr(self,'_unit_index_version',None)!=units.version):
            index={}
            for unit in units:index.setdefault(unit.objid,[]).append(unit)
            self._unit_index=index;self._unit_index_table=units;self._unit_index_version=units.version

        return next((u for u in self._unit_index.get(objid,())if not alive_only or u.alive),None)

    def big_robot_counts(self):
        """(total, per portal) live+queued big robots, aircraft excluded."""
        total = 0
        per_portal = {}
        for unit in self.units:
            if not unit.alive or unit.vid not in self.rules.BIG_ROBOT_VIDS:
                continue
            total += 1
            portal = getattr(unit, 'portal_id', None)
            per_portal[portal] = per_portal.get(portal, 0) + 1
        for item in self.pending:
            if item.get('vid') not in self.rules.BIG_ROBOT_VIDS:
                continue
            total += 1
            portal = item.get('portal')
            per_portal[portal] = per_portal.get(portal, 0) + 1
        return total, per_portal

    def _big_robot_slot_open(self, portal_id):
        total, per_portal = self.big_robot_counts()
        return (total < self.rules.BIG_ROBOT_MAX_ALIVE
                and per_portal.get(portal_id, 0) < self.rules.BIG_ROBOT_PER_PORT)

    def pick_vid(self,include_bombs=True):
        table=[(vid,weight) for vid,weight in self.rules.SPAWN_TABLE
               if include_bombs or vid!='hulk.pathlighton']
        total = sum(weight for _, weight in table)
        roll = self.random.random() * total
        accum = 0.0
        for vid, weight in table:
            accum += weight
            if roll < accum:
                return vid
        return table[0][0]

    def spawn(self, vid, portal_id=None, position=None, create_vid=None):
        stats = self.rules.UNIT_STATS.get(vid, {})
        behaviour = self.behaviour.get(vid, {})
        merged = dict(stats)
        merged.update({key: behaviour[key] for key in
                       ('hp', 'contact', 'speed', 'attacks', 'rule')
                       if key in behaviour})
        width, height = behaviour.get(
            'size', self.rules.SPAWN_SIZES.get(
                vid, self.rules.SPAWN_DEFAULT_SIZE))
        if position is None:
            portal_id = portal_id or self.random.choice(self.portal_ids)
            position = self.room.portal_spawn(portal_id, height, width)
        if position is None:
            return None
        unit = Enemy(self.allocate(), vid, merged, position[0], position[1],
                     width=width, height=height, rng=self.random, room=self.room,
                     rules=self.rules, balance=self.balance)
        unit.create_vid = create_vid
        unit.portal_id = portal_id
        self.units.append(unit)
        return unit

    def queue_spawn(self, vid, portal_id, delay=0.0):
        """Show a harmless preview, then release a real body from the portal.

        A delayed body is positioned against the ship at release time. It
        does not chase, attack or count as an attackable enemy while inside
        the spawn cover. ``vid=None`` is an explicitly empty debug slot;
        pathlight is a real falling/arming bomb rover, not an empty slot.
        """
        show_at = self.room_time + delay
        item = {'vid': vid, 'portal': portal_id, 'show_at': show_at,
                'spawn_at': show_at + self.rules.SPAWN_PREVIEW_DELAY,
                'shown': False, 'cover_removed': False, 'preview_id': self.allocate(),
                'cover_id': self.allocate()}
        self.pending.append(item)
        return item

    def _step_spawns(self, events):
        for item in list(self.pending):
            if not item['shown'] and self.room_time >= item['show_at']:
                item['shown'] = True
                events.append(dict(item, kind='portal-preview'))
            if (item['shown'] and not item['cover_removed'] and self.room_time + 1e-8 >=
                    item['show_at'] + self.rules.SPAWN_COVER_DELAY):
                item['cover_removed'] = True
                events.append({'kind': 'portal-preview-expired', 'objid': item['cover_id']})
            if item['shown'] and not item.get('preview_landed'):

                portal = self.room.object(item['portal'])
                if portal is not None:
                    x, y, w, h = portal.rect()
                    size = (self.behaviour.get(item['vid']) or {}).get(
                        'size', self.rules.SPAWN_DEFAULT_SIZE) if item['vid'] else (8192, 8192)
                    from_x = x + w // 2 - int(size[0]) // 2
                    from_y = y + self.rules.SPAWN_PREVIEW_FEET - int(size[1])
                    to_y = y + h - int(size[1])
                    if 'preview_next' not in item:
                        item['preview_next'] = item['show_at']
                        item['preview_x'] = from_x
                        item['preview_y'] = from_y
                    while (self.room_time + 1e-8 >= item['preview_next']
                           and item['preview_next'] < item['spawn_at'] - 1e-8):
                        step_end = min(item['spawn_at'], item['preview_next'] + 0.05)
                        progress = min(1.0, max(0.0, (step_end - item['show_at']) /
                                                self.rules.SPAWN_PREVIEW_DELAY))
                        target_y = from_y + (to_y - from_y) * progress * progress
                        px = x + w // 2 - int(size[0]) // 2
                        events.append({'kind': 'portal-preview-move',
                                       'objid': item['preview_id'],
                                       'from': (item['preview_x'], item['preview_y']),
                                       'to': (px, target_y),
                                       'ms': max(1, int(round((step_end - item['preview_next']) * 1000)))})
                        item['preview_x'], item['preview_y'] = px, target_y
                        item['preview_next'] = step_end
                    if self.room_time + 1e-8 >= item['spawn_at'] - 1e-8:
                        item['preview_landed'] = True
            if self.room_time + 1e-8 >= item['spawn_at']:
                events.append({'kind': 'portal-preview-expired', 'objid': item['preview_id']})
                if item['vid'] is not None:
                    vid, create_vid = item['vid'], None
                    if vid == 'hulk.scavengerloader':

                        if self.random.random() < (self.rules.LOADER_B_VARIANT /
                                                   float(self.rules.LOADER_SPAWN_TOTAL)):
                            vid, create_vid = 'hulk.eloader', 'hulk.scavengerloader'
                    self.spawn(vid, portal_id=item['portal'], create_vid=create_vid)
                self.pending.remove(item)

    def reserved(self):
        return sum(item['vid'] is not None for item in self.pending)

    def inherit_portal(self, child, parent):
        """Keep a loader's slot occupied by its ejected surviving pilot."""
        child.portal_id = getattr(parent, 'portal_id', None)
        self.portal_refills[:] = [request for request in self.portal_refills
                                  if request['objid'] != parent.objid]

    def spawn_rate(self):
        'Apply the saved multiplier to the original runtime cadence.'
        return max(0.01, float(self.balance.spawn_rate())*self.spawn_baseline*self.spawn_population_multiplier)

    def first_wave_delay(self):
        """Fixed opening time; spawn-rate multipliers affect later rolls only."""
        return float(self.rules.SPAWN_FIRST_DELAY)

    def max_alive(self):
        """Editor's ground-monster cap (aircraft never count)."""
        return max(1, int(self.balance.spawn_cap()))

    def _next_portal_roll(self, portal, after=0.0):
        samples = getattr(self.rules, 'SPAWN_PORT_DELAYS', {}).get(portal)
        wait = (self.random.choice(samples) if samples else
                self.random.uniform(*self.rules.SPAWN_PORT_INTERVAL))
        self.portal_due[portal] = (self.room_time + after +
                                  wait / self.spawn_rate())

    def _queue_portal_roll(self, portal, delay=0.0, include_bombs=True):
        vid = self.pick_vid(include_bombs=include_bombs)
        if vid in self.rules.BIG_ROBOT_VIDS and not self._big_robot_slot_open(portal):
            for _ in range(3):
                vid = self.pick_vid(include_bombs=include_bombs)
                if vid not in self.rules.BIG_ROBOT_VIDS or self._big_robot_slot_open(portal):
                    break
            else:
                normal = [(v, w) for v, w in self.rules.SPAWN_TABLE
                          if v not in self.rules.BIG_ROBOT_VIDS and
                          (include_bombs or v != 'hulk.pathlighton')]
                roll = self.random.random() * sum(w for _, w in normal)
                vid = normal[0][0]
                for candidate, weight in normal:
                    roll -= weight
                    if roll < 0:
                        vid = candidate
                        break
        return self.queue_spawn(vid, portal, delay)

    def step(self, dt, player, events, players=None):
        from ..participants import active_players, ActiveTargetIndex
        candidates = active_players(players if players is not None else (player,))
        targets = ActiveTargetIndex(candidates)
        self.room_time += dt
        self.wave_timer -= dt
        if self.enabled:
            capacity = self.max_alive()-self.population_count()-self.reserved()
            if self.waves == 0 and self.wave_timer <= 0 and capacity > 0:
                spawned = min(capacity, self.rules.SPAWN_INITIAL_WAVE)
                for index in range(spawned):
                    self._queue_portal_roll(self.portal_ids[index % len(self.portal_ids)],
                                           (0.0, .10, .26)[index // 5],
                                           getattr(self.rules, 'SPAWN_INITIAL_INCLUDE_BOMBS', False))
                queued = spawned
                bomb_slots = getattr(self.rules, 'SPAWN_OPENING_BOMBS', 2)
                for portal in self.portal_ids[-bomb_slots:] if bomb_slots else ():
                    if queued >= capacity:
                        break
                    self.queue_spawn('hulk.pathlighton', portal, .26)
                    queued += 1
                self.waves = 1
                for portal in self.portal_ids:
                    self._next_portal_roll(portal, .26)
                self.wave_timer = min(self.portal_due.values())-self.room_time
                events.append(dict(kind='wave',count=spawned,queued=queued,
                                   next_delay=self.wave_timer))
            elif self.waves > 0:
                if not self.portal_due:
                    for portal in self.portal_ids:
                        self._next_portal_roll(portal)
                due = sorted(p for p, at in self.portal_due.items() if at <= self.room_time)
                queued_ports, refilled = [], []
                for portal in due:
                    if len(queued_ports) < capacity:
                        request = self.portal_refills[0] if self.portal_refills else None
                        target = request['portal'] if request else portal
                        self._queue_portal_roll(target)
                        queued_ports.append(target)
                        if request:

                            refilled.append(self.portal_refills.pop(0))

                    self._next_portal_roll(portal)
                self.wave_timer = min(self.portal_due.values())-self.room_time
                if queued_ports:
                    self.waves += 1
                    events.append(dict(kind='wave',count=len(queued_ports),queued=len(queued_ports),
                                       portals=queued_ports,refills=refilled,next_delay=self.wave_timer))
        self._step_spawns(events)
        for unit in list(self.units):
            unit.ship_passes=self.ship_passes
            target = targets.nearest(unit.center()) if unit.alive else None
            unit_events = unit.step(dt, target, self.room_time)
            events.extend(unit_events)
            if unit.alive and unit.vid == 'hulk.scavengerengineer':
                self._step_engineer_summon(unit, dt, target, events)

            if unit.alive and unit.y > 114688 + 800 * 256:
                unit.alive = False
                unit.state = 'removed'
                self._dismiss_summon(unit)
        from ..participants import life_identity
        for unit in self.units:
            if not unit.alive:
                continue
            if unit.coop_attack != unit.attack_started:
                unit.coop_attack = unit.attack_started
                unit.coop_melee_hits.clear()
            if unit.coop_contact_due is None:
                unit.coop_contact_due = {life_identity(p): self.room_time+unit.contact_cd for p in candidates}
            live_keys = {life_identity(p) for p in candidates}
            unit.coop_contact_due = {k: v for k, v in unit.coop_contact_due.items() if k in live_keys}
            for participant in candidates:
                key = life_identity(participant)
                player_rect = participant.rect()
                if (key not in unit.coop_melee_hits and any(_rects_overlap(rect, player_rect)
                        for rect in unit.attack_rects(self.room_time))):
                    unit.coop_melee_hits.add(key)
                    unit.melee_hit_done = True
                    events.append(dict(kind='player_damage', value=unit.contact,
                                       owner=unit.objid, player_key=key, x=0, y=0))
                if (_rects_overlap(unit.rect(), player_rect)
                        and self.room_time >= unit.coop_contact_due.get(key, 0)):
                    unit.coop_contact_due[key] = self.room_time+1.
                    unit.contact_cd = 1.
                    events.append(dict(kind='player_damage', value=unit.contact,
                                       owner=unit.objid, player_key=key, x=0, y=0))

    def _step_engineer_summon(self, unit, dt, player, events):
        'Summoning proceeds during retreat and does not play a build cue.'
        if unit.summoned_objid is not None:
            child = self.find_unit(unit.summoned_objid)
            if child is not None and child.alive:
                return
            unit.summoned_objid = None
            unit.summon_timer = self.rules.ENGINEER_RESUMMON_SECONDS
            return
        if player is None or not player.alive:
            return
        unit.summon_timer = max(0.0, unit.summon_timer-dt)
        cx,cy = unit.center()
        tx,ty = player.center()

        if (player is None or unit.summon_timer>0
                or not unit.motion.visible(unit.center(),player.center())):
            return

        vid = 'hulk.bdrone' if self.random.random()<15/27 else 'hulk.sdrone'
        child = self.spawn(vid, portal_id=unit.portal_id, position=(unit.x,unit.y))
        unit.summoned_objid = child.objid
        events.append({'kind':'enemy-summon','owner':unit.objid,'objid':child.objid,
                       'vid':child.vid,'position':(child.x,child.y)})

    def ship_bottom_jump(self,predict_ship=None,dt=1/60,pass_id=None):
        'Try runtime-size hops near the low part of a diagonal sweep.'
        if self.room_time+1e-8<self.ship_return_check_at:
            return []
        self.ship_return_check_at=self.room_time+self.rules.SHIP_RETURN_CHECK_INTERVAL
        if not available_ledges(self.room):
            return []
        jumped = []
        for unit in self.live():
            if (unit.flying or unit.kind == 'bomb-rover' or
                    unit.vid in ('hulk.scavengerloader', 'hulk.eloader') or
                    not unit.motion.grounded or self.room_time<unit.next_jump):
                continue
            if pass_id is not None and unit.ship_jump_pass==pass_id:

                continue
            if unit.motion.support != self.rules.ARENA_FLOOR_ID:

                continue
            if 'shredder' in unit.vid:
                vx=unit._travel_speed(unit.facing,True)*unit.facing
            else:
                vx=0.0
            unit.ship_jump_vx=vx

            velocity=jump_speed(unit.vid)
            unit.motion.jump(velocity,unit)
            unit.ship_jump_until=self.room_time+2*velocity/unit.motion.gravity+.6
            unit.next_jump=self.room_time+self.rules.SHIP_RETURN_JUMP_COOLDOWN
            unit.ship_jump_pass=pass_id
            unit.state,unit.pose = 'ship-return-jump',3
            jumped.append(unit.objid)
        return jumped

    def apply_damage(self, objid, value):
        unit=self.find_unit(int(objid),alive_only=True) if self.units else None
        if unit is None:return None
        if unit.kind == 'bomb-rover' and not unit.bomb_armed:
            return None
        unit.hp = max(0, unit.hp - max(0, int(value)))
        if unit.hp <= 0:
            unit.alive = False
            self._dismiss_summon(unit)
            if not unit.flying and unit.portal_id in self.portal_ids:
                self.portal_refills.append(dict(portal=unit.portal_id, objid=unit.objid))
            return {'destroyed': True, 'unit': unit}
        return {'destroyed': False, 'unit': unit, 'hp': unit.hp}

    def _dismiss_summon(self, unit):
        if unit.vid == 'hulk.scavengerengineer':
            for child in self.units:
                if child.objid == unit.summoned_objid and child.alive:
                    child.alive = False
                    child.state = 'owner-death'

    def live(self):
        return [unit for unit in self.units if unit.alive]

    def population_count(self):

        return sum(u.alive and not u.flying for u in self.units)

    def start(self):
        self.enabled = True
        self.wave_timer = self.first_wave_delay()

def _rects_overlap(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])
