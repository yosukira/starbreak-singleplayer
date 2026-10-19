"""Read accepted combat results without changing controller decisions or packets.

Display HP is sometimes a one-HP sentinel or a refillable phase bar. Each adapter
therefore observes the controller's actual gate, rather than incoming hit values.
Unsupported encounters keep unknown statistics in the battle record.
"""
import math


SUPPORTED = frozenset(('city_mid', 'city_boss', 'forest_mid', 'forest_boss',
                       'ice_mid', 'ice_boss', 'sc_patriarch', 'sc_hive',
                       'dumb_wave', 'dumb_normal_wave', 'jungle_mid', 'sc_bonesaw',
                       'hulk_mid', 'sc_boss', 'sc_boss_elite', 'jungle_boss', 'dumb_boss',
                       'cave_boss', 'dumb_mid', 'lab_mid', 'lab_hard'))
CITY_NPCS = frozenset(('flyer', 'infantry', 'skiff', 'hawk', 'copter', 'sentry'))
DEATH_STATES = {
    'city_mid': ('dying', 'done'),
    'city_boss': ('exploding', 'death_fall', 'done'),
    'forest_mid': ('dying', 'done'), 'forest_boss': ('dying', 'done'),
    'ice_mid': ('dying', 'done'), 'ice_boss': ('death', 'done'),
    'sc_patriarch': ('dying', 'done'), 'sc_hive': ('cleared', 'done'),
    'dumb_wave': (), 'dumb_normal_wave': (), 'jungle_mid': ('defeated',),
    'sc_boss': ('done',), 'sc_boss_elite': ('done',), 'jungle_boss': ('defeated',),
    'dumb_boss': ('death', 'done'),
    'cave_boss': ('death', 'done'),
    'dumb_mid': ('done',),
}


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Unknown combat counter')
    return max(0, value)


class Target:
    def __init__(self, read, npc=False, present=None, spent=False, native_hp=False, generator=False):
        self.read, self.npc, self.present, self.spent = read, npc, present, spent
        self.native_hp = native_hp or npc
        self.generator = generator

    def snapshot(self):
        value, phase = self.read()
        return number(value), phase


class CombatObserver:
    def __init__(self, session, recorder):
        self.session, self.recorder = session, recorder
        self.map = session.options.map
        self.failed = False
        # A takeover starts with a new record; do not credit an earlier death.
        self.boss_counted = self.boss_deaths()
        if self.map in ('lab_mid', 'lab_hard'):
            self._lab_controller = None
            self._watch_lab()
        else:
            name = getattr(self, 'feedback_method', '_feedback')
            self.original = getattr(session, name)
            setattr(session, name, self.feedback)

    def dead(self):
        if self.map in ('lab_mid', 'lab_hard'):
            return self.session.phase in ('death', 'done')
        controller = getattr(self.session, 'controller', None)
        return getattr(controller, 'state', None) in DEATH_STATES.get(self.map, ())

    def _watch_lab(self):
        # These hosts consume bridge events inline. Observe their existing
        # damage entry points; never poll the bridge again or change its result.
        session = self.session
        begin = session._begin_fight

        def begin_fight(*args, **kwargs):
            result = begin(*args, **kwargs)
            try:
                self._watch_lab_controller()
            except Exception:
                self.failed = True
            return result

        session._begin_fight = begin_fight
        methods = ('_damage_zombie',) if self.map == 'lab_mid' else ('_damage_add', '_damage_seeker')
        for name in methods:
            original = getattr(session, name)

            def damage(oid, value, original=original):
                return self.watch_call(lambda: self.target(oid), lambda: original(oid, value))

            setattr(session, name, damage)
        self._watch_lab_controller()

    def _watch_lab_controller(self):
        controller = self.session.controller
        if controller is None or controller is self._lab_controller:
            return
        original = controller.add_damage

        def damage(value, objid=None):
            oid = objid if objid is not None else (
                self.session.pack.boss_objid if self.map == 'lab_hard' else 0)
            return self.watch_call(lambda: self.target(oid), lambda: original(value, objid))

        controller.add_damage = damage
        self._lab_controller = controller

    def watch_call(self, read_target, invoke):
        target = before = None
        if not self.failed:
            try:
                target = read_target()
                if target is not None:
                    before = target.snapshot()
            except Exception:
                self.failed = True
        result = invoke()  # The game callback runs exactly once, even on failure.
        self.observe_after(target, before)
        return result

    def boss_deaths(self):
        if self.map in ('sc_bonesaw', 'hulk_mid'):
            c = getattr(self.session, 'controller', None)
            return {oid for oid, boss in getattr(c, 'bosses', {}).items() if boss['dead']}
        return {'encounter'} if self.dead() else set()

    def tick(self):
        if self.failed:
            return
        try:
            deaths = self.boss_deaths()
            for _identity in deaths - self.boss_counted:
                self.recorder.damage(0, killed=True)
            self.boss_counted.update(deaths)
        except Exception:
            self.failed = True

    def target(self, oid):
        session = self.session
        c = getattr(session, 'controller', None)
        if c is None:
            return None
        if self.map in ('dumb_wave', 'dumb_normal_wave', 'jungle_mid', 'dumb_boss'):
            if self.map == 'jungle_mid' and oid == session.mouth_id:
                # The mouth immediately refills at each broken bar. This
                # controller already caps its cumulative accepted damage.
                return Target(lambda: (c.damage_total, None), spent=True)
            if self.map == 'dumb_boss' and oid == c.pack.boss_id:
                return Target(lambda: (min(c.gate, c.stage_damage), c.stage), spent=True)
            actor = session.actors.get(oid)
            if actor is not None and actor['kind'] == 'npc' and actor.get('hp') is not None:
                return Target(lambda: (actor['hp'], None), True, lambda: oid in session.actors)
        elif self.map.startswith('city_'):
            actor = c.actors.get(oid)
            if actor is None:
                return None
            kind = actor['kind']
            if kind == 'tank':
                return Target(lambda: (c.pack.entities['encounter']['tank_hp'] - actor['damage_taken'], None))
            if kind in CITY_NPCS or kind in ('midboss', 'body', 'claw'):
                return Target(lambda: (actor['hp'], None), kind in CITY_NPCS,
                              lambda: oid in c.actors)
        elif self.map.startswith('forest_'):
            if oid == c.boss['id']:
                return Target(lambda: (c.damage, c.phase), spent=True)
            actor = c.actors.get(oid)
            if actor is not None and actor['kind'] in ('frog', 'bug'):
                return Target(lambda: (actor['hp'], None), True, lambda: oid in c.actors)
        elif self.map == 'ice_mid':
            if oid == c.boss['id']:
                return Target(lambda: (c.damage, c.phase), spent=True)
        elif self.map == 'ice_boss':
            if oid in (c.small['id'], c.large['id']):
                return Target(lambda: (c.phase_damage, (c.state, c.combat_phase)), spent=True)
            actor = c.actors.get(oid)
            if actor is not None and actor['kind'] in ('npc', 'egg'):
                return Target(lambda: (actor['hp'], None), True, lambda: oid in c.actors)
        elif self.map == 'sc_patriarch' and oid == 4:
            return Target(lambda: (c.hp, None))
        elif self.map == 'sc_bonesaw' and oid in c.bosses:
            boss = c.bosses[oid]
            return Target(lambda: (boss['damage'], None), spent=True)
        elif self.map == 'sc_hive' and oid == session.overlay_objid:
            return Target(lambda: (c.hp, None))
        elif self.map == 'hulk_mid':
            if oid in c.bosses:
                boss = c.bosses[oid]
                return Target(lambda: (boss['damage'] + boss['late_damage'], None), spent=True)
            trap = session.traps.get(oid)
            if trap is not None:
                return Target(lambda: (trap['hp'], None), present=lambda: oid in session.traps,
                              native_hp=True)
            actor = session.actors.get(oid)
            if actor is not None and actor.get('kind') == 'mini':
                return Target(lambda: (actor['hp'], None), True, lambda: oid in session.actors)
        elif self.map in ('sc_boss', 'sc_boss_elite'):
            if oid == session.head_id:
                # A gate-opening hit only starts the next bar; it does not
                # damage that refill. The wire head HP is a sentinel.
                return Target(lambda: (c.hp or 0, c.stage))
            squid = session.squids.get(oid)
            if squid is not None:
                # Early native destruction repairs the same squid under a new
                # object ID. Follow its identity, rather than counting a kill.
                return Target(lambda: (squid['hp'], None), True,
                              lambda: any(row is squid for row in session.squids.values()))
            actor = session.actors.get(oid)
            capability = session.native_retirement_capability(oid)
            if actor is not None and capability and capability['role'] == 'npc':
                return Target(lambda: (actor['hp'], None), True, lambda: oid in session.actors)
        elif self.map == 'jungle_boss':
            if oid in session.boss['parts']:
                return Target(lambda: (c.remaining_hp(), None))
            actor = session.actors.get(oid)
            if actor is not None and actor['kind'] == 'npc' and actor.get('hp') is not None:
                return Target(lambda: (actor['hp'], None), True, lambda: oid in session.actors)
        elif self.map == 'cave_boss':
            mouth = c.mouths.get(oid)
            if mouth is not None:
                return Target(lambda: (mouth['hp'], c.weak_round))
            actor = c.actors.get(oid)
            if actor is not None and actor['kind'] in ('hive', 'insect', 'roller') and actor.get('hp') is not None:
                return Target(lambda: (actor['hp'], None), True, lambda: oid in c.actors)
        elif self.map == 'dumb_mid':
            piece = c.parts.get(oid)
            if piece is not None:
                # Formation bars and detached pieces are components of one
                # encounter. The depleted formation wire HP is a sentinel.
                return Target(lambda: (c.config['piece_damage_gate'] - piece['damage']
                              if oid in c.group else piece['hp'], (c.cycle, oid in c.group)))
        elif self.map == 'lab_mid':
            if oid == session.pack.boss_objid:
                return Target(lambda: (c.boss_hp, None))
            block = c.blocks.get(oid)
            if block is not None:
                return Target(lambda: (block['hp'], None))
            zombie = session.zombies.get(oid)
            if zombie is not None:
                return Target(lambda: (zombie['hp'], None), True, lambda: oid in session.zombies)
        elif self.map == 'lab_hard':
            if oid == session.pack.boss_objid:
                machine = c.machine
                return Target(lambda: (min(machine.spec.damage_required, machine.damage_in_phase),
                                       machine.index), spent=True)
            for actors in (session.adds, session.seekers):
                actor = actors.get(oid)
                if actor is not None:
                    return Target(lambda: (actor['hp'], None), True, lambda: oid in actors)
        return None

    def feedback(self, event):
        target = before = None
        if not self.failed:
            try:
                if (isinstance(event, dict) and event.get('kind') in ('damage', 'destroy')
                        and int(event.get('ownerid', self.session.worldid) or 0) in
                        ((0, self.session.worldid) if self.map == 'jungle_boss' else (self.session.worldid,))):
                    target = self.target(int(event.get('victim') or 0))
                    if target is not None:
                        before = target.snapshot()
            except Exception:
                self.failed = True
        # Never retry an original callback, including when it raises.
        result = self.original(event)
        self.observe_after(target, before, event)
        return result

    def observe_after(self, target, before, event=None):
        if target is not None and before is not None and not self.failed:
            try:
                after = target.snapshot()
                if target.spent:
                    accepted = max(0, after[0] - (before[0] if after[1] == before[1] else 0))
                else:
                    accepted = max(0, before[0] - after[0]) if before[1] == after[1] else 0
                killed = target.npc and not target.present()
                if event is not None and event['kind'] == 'destroy' and target.native_hp:
                    hp_death = (not target.present() and
                                event.get('reason') in (2, 'HITPOINTS', 'hitpoints'))
                    killed = target.npc and hp_death
                    accepted = before[0] if hp_death else 0
                self.recorder.damage(accepted, killed=killed)
                if target.generator and before[0] > 0 and accepted > 0 and (after[0] <= 0 or not target.present()):
                    self.recorder.generator_destroyed()
                self.tick()
            except Exception:
                self.failed = True


class RoomCombatObserver(CombatObserver):
    feedback_method = 'feedback'

    def dead(self):
        return False

    def target(self, oid):
        session = self.session
        actor = session.actors.get(oid)
        if actor is None or actor.get('hp') is None:
            return None
        mechanic = getattr(session.mechanics, 'states', {}).get(oid, {})
        if self.map == 'egy_run' and mechanic.get('kind') == 'generator':
            # Shield generators have terrain collision but real destructible
            # health. Match the standalone GY host without counting a monster.
            return Target(lambda: (actor['hp'], None), present=lambda: oid in session.actors,
                          native_hp=True, generator=True)
        capability = session.native_retirement_capability(oid)
        if not capability or capability['role'] != 'npc':
            return None
        enemies = session.enemies
        npc = oid in getattr(enemies, 'units', {}) or oid in getattr(enemies, 'states', {})
        for rows in (getattr(enemies, 'actors', {}),
                     getattr(getattr(enemies, 'mites', None), 'actors', {})):
            npc = npc or rows.get(oid, {}).get('kind') in ('npc', 'special')
        return Target(lambda: (actor['hp'], None), npc, lambda: oid in session.actors, native_hp=True)


class IntegratedCombatObserver:
    """GY Boss already reports accepted NPC/generator damage in its host."""
    def __init__(self, session, recorder):
        self.session, self.recorder = session, recorder
        self.failed = False
        self.counted = session.phase in ('death', 'done')
        self.generators_counted = set(session.gens.destroyed)
        session.battle_recorder = recorder

    def tick(self):
        destroyed = set(self.session.gens.destroyed)
        self.recorder.generator_destroyed(len(destroyed - self.generators_counted))
        self.generators_counted.update(destroyed)
        if not self.counted and self.session.phase in ('death', 'done'):
            self.recorder.damage(0, killed=True)
            self.counted = True


class RunCombatObserver:
    def __init__(self, session, recorder):
        self.failed = False
        self.bind(session, recorder)

    def bind(self, session, recorder):
        self.session, self.recorder = session, recorder
        child = session.current
        observer = getattr(child, '_battle_metric_observer', None)
        if observer is None:
            from .maps.run_common.actors import ActorSession
            if isinstance(child, ActorSession):
                observer = RoomCombatObserver(child, recorder)
            elif child.options.map in ('egy_hulk', 'hulk_normal'):
                observer = IntegratedCombatObserver(child, recorder)
            elif child.options.map in SUPPORTED:
                observer = CombatObserver(child, recorder)
            else:
                self.failed = True
                observer = None
            if observer is not None:
                child._battle_metric_observer = observer
        elif observer is not None:
            observer.recorder = recorder
            if isinstance(observer, IntegratedCombatObserver):
                child.battle_recorder = recorder
        self.current = observer

    def tick(self):
        if self.current is not None:
            self.current.tick()
            self.failed = self.failed or self.current.failed


def bind(session, recorder):
    run = getattr(session, 'run', None)
    if run is not None:
        observer = getattr(run, '_battle_metric_observer', None)
        try:
            if observer is None:
                observer = RunCombatObserver(session, recorder)
                run._battle_metric_observer = observer
            else:
                observer.bind(session, recorder)
        except Exception:
            if observer is not None:
                observer.failed = True
            return observer
        return observer
    integrated = session.options.map in ('egy_hulk', 'hulk_normal')
    if session.options.map not in SUPPORTED and not integrated:
        return None
    observer = getattr(session, '_battle_metric_observer', None)
    if observer is None:
        try:
            observer = session._battle_metric_observer = (
                IntegratedCombatObserver(session, recorder) if integrated else CombatObserver(session, recorder))
        except Exception:
            # Optional statistics must never prevent starting the actual game.
            return None
    else:
        observer.recorder = recorder
        if integrated:
            session.battle_recorder = recorder
    return observer
