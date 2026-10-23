"""Panel -> gen (weakspot) live state machine."""

import random

from . import constants
from ..interactions import can_interact

PANEL_ANIM = (1, 594)
GEN_OPEN_ANIM = (1, 990)

GEN_CLOSE_ANIM = (1, 990)

class GenCycle(object):
    """Runs the five panel/gen pairs as a live interaction loop.

    The player lights a panel, stands on it and presses the interact key; the
    panel turns off, the paired gen opens for ~3 seconds and keeps whatever
    hitpoints it has left for the next opening.  Destroying all five ends the
    fight.
    """

    def __init__(self, session, auto_interact=False):
        self.rules = getattr(session, 'rules', constants)
        self.session = session
        self.room = session.room
        self.auto_interact = bool(auto_interact)
        self.order_index = 0
        self.active_panel = None
        self.panel_state = 'idle'
        self.panel_timer = 0.0
        self.panel_interactable = False
        self.pending_press = 0.0
        self.gen_state = 'closed'
        self.active_gen = None
        self.gen_timer = 0.0
        balance = getattr(session, 'balance', None)
        starting_hp = (balance.hp(self.rules.GEN_HP) if balance is not None
                       else int(self.rules.GEN_HP))

        self.hp = dict((gen_id, starting_hp) for gen_id in self.rules.GEN_IDS)
        self.destroyed = []
        self.overlap_seconds = 0.0
        self.next_panel_delay = self.rules.PANEL_FIRST_DELAY
        self.enabled = False
        self.finished = False
        self.elapsed = 0.0
        self.cycle_deadline = None
        self.random = random.Random(session.seed + 0x6E4)
        self._choose_pair()

    def pair(self):
        return self.rules.GEN_PANEL_ORDER[
            self.order_index % len(self.rules.GEN_PANEL_ORDER)]

    def _light_panel(self):
        panel_id, gen_id = self.pair()
        if gen_id in self.destroyed:
            self._advance_panel(self.rules.PANEL_NEXT_AFTER_DESTROY)
            return
        self.active_panel = panel_id
        self.active_gen = gen_id
        self.panel_state = 'lit'
        self.panel_timer = 0.0
        self.panel_interactable = False
        self.pending_press = 0.0
        self.cycle_deadline = self.elapsed + self.rules.PANEL_LIT_INTERVAL
        batch = self.session.batch
        batch.modify(panel_id, 'INTERACT', intval=0)
        batch.modify(panel_id, 'VID', stringval='hulk.accesspanelon')
        batch.animprops(panel_id, PANEL_ANIM[0], PANEL_ANIM[1])
        self.session.event('panel-lit', panel=panel_id, gen=gen_id)

    def _auto_close_panel(self):
        """Unused panel: switch off and rotate to the next pair.

        The original server sends accesspaneloff and accesspanelon for the
        next pair in the same delta, so the live room never has a lit panel
        standing dark indefinitely.
        """
        panel_id = self.active_panel
        batch = self.session.batch
        batch.modify(panel_id, 'INTERACT', intval=0)
        batch.modify(panel_id, 'VID', stringval='hulk.accesspaneloff')
        batch.animprops(panel_id, PANEL_ANIM[0], PANEL_ANIM[1])
        self.session.event('panel-timeout', panel=panel_id,
                           gen=self.active_gen)
        self._advance_panel(0.0)
        self._light_panel()

    def _request_panel_activation(self):

        self.panel_state = 'activating'
        self.panel_timer = 0.0
        self.panel_interactable = False
        self.pending_press = 0.0
        self.session.batch.modify(self.active_panel, 'INTERACT', intval=0)
        self.session.event('panel-interaction-start', panel=self.active_panel,
                           gen=self.active_gen)

    def _activate_panel(self):
        panel_id = self.active_panel
        batch = self.session.batch
        batch.modify(panel_id, 'INTERACT', intval=0)
        batch.modify(panel_id, 'VID', stringval='hulk.accesspaneloff')
        batch.animprops(panel_id, PANEL_ANIM[0], PANEL_ANIM[1])
        self.panel_state = 'off'
        self.panel_timer = 0.0
        self.panel_interactable = False
        self.gen_state = 'opening'
        self.gen_timer = 0.0
        self.session.event('panel-activated', panel=panel_id,
                           gen=self.active_gen)

    def _open_gen(self):
        gen_id = self.active_gen
        batch = self.session.batch
        batch.modify(gen_id, 'DRAW', boolval=True)
        batch.modify(gen_id, 'VID', stringval='hulk.weakspotopen')

        batch.modify(gen_id, 'HITPOINTS')
        batch.modify(gen_id, 'INVULNERABLE', boolval=True)
        batch.modify(gen_id, 'BLOCKTYPE', intval=0)
        batch.modify(gen_id, 'OWNERTYPE', intval=0)
        batch.modify(gen_id, 'TERRAINTYPE', intval=0)
        self.room.object(gen_id).set(hitpoints_=-1, invulnerable_=True,
                                    blocktype_=0, ownertype_=0, terraintype_=0)
        batch.animprops(gen_id, GEN_OPEN_ANIM[0], GEN_OPEN_ANIM[1])
        batch.sound(gen_id, 'hulk.panelopen')
        self.gen_state = 'opening'
        self.session.event('gen-opening', gen=gen_id)

    def _arm_gen(self):
        gen_id = self.active_gen
        batch = self.session.batch
        batch.modify(gen_id, 'HITPOINTS', intval=self.hp[gen_id])

        batch.modify(gen_id, 'INVULNERABLE', boolval=False)
        batch.modify(gen_id, 'BLOCKTYPE', intval=1)
        batch.modify(gen_id, 'OWNERTYPE', intval=2)
        batch.modify(gen_id, 'TERRAINTYPE',
                     intval=self.rules.GEN_TERRAIN_TYPE)
        self.room.object(gen_id).set(hitpoints_=self.hp[gen_id], invulnerable_=False,
                                    blocktype_=1, ownertype_=2,
                                    terraintype_=self.rules.GEN_TERRAIN_TYPE)
        self.gen_state = 'armed'
        self.gen_timer = 0.0
        self.session.event('gen-armed', gen=gen_id, hp=self.hp[gen_id])

    def _close_gen(self):
        gen_id = self.active_gen
        batch = self.session.batch
        batch.modify(gen_id, 'VID', stringval='hulk.weakspotclosed')
        batch.animprops(gen_id, GEN_CLOSE_ANIM[0], GEN_CLOSE_ANIM[1])
        batch.sound(gen_id, 'hulk.panelclose')
        self.gen_state = 'closing'
        self.gen_timer = 0.0
        self.session.event('gen-closing', gen=gen_id)

    def _finish_close(self):
        gen_id = self.active_gen
        batch = self.session.batch
        batch.modify(gen_id, 'HITPOINTS')
        batch.modify(gen_id, 'TERRAINTYPE', intval=0)
        batch.modify(gen_id, 'INVULNERABLE', boolval=True)
        batch.modify(gen_id, 'BLOCKTYPE', intval=0)
        batch.modify(gen_id, 'OWNERTYPE', intval=0)
        self.room.object(gen_id).set(hitpoints_=-1,terraintype_=0,invulnerable_=True,blocktype_=0,ownertype_=0)
        self.gen_state = 'closed'
        self.session.event('gen-closed', gen=gen_id, hp=self.hp[gen_id])

        self.panel_state = 'relight'
        self.panel_timer = 0.0

    def _destroy_gen(self, gen_id):
        batch = self.session.batch
        batch.modify(gen_id, 'VID', stringval='hulk.weakspotdestroyed')
        batch.modify(gen_id, 'HITPOINTS')
        batch.modify(gen_id, 'TERRAINTYPE', intval=0)
        batch.modify(gen_id, 'INVULNERABLE', boolval=True)
        batch.modify(gen_id, 'BLOCKTYPE', intval=0)
        batch.modify(gen_id, 'OWNERTYPE', intval=0)
        self.room.object(gen_id).set(hitpoints_=-1,terraintype_=0,invulnerable_=True,blocktype_=0,ownertype_=0)
        batch.sound(gen_id, 'hulk.paneldeath')
        self.destroyed.append(gen_id)
        self.gen_state = 'destroyed'
        if len(self.destroyed) >= len(self.rules.GEN_IDS):
            self.finished = True
        self.session.on_gen_destroyed(gen_id)
        self._advance_panel(self.rules.PANEL_NEXT_AFTER_DESTROY)

    def _choose_pair(self):
        available = [i for i,(_,gen) in enumerate(self.rules.GEN_PANEL_ORDER)
                     if gen not in self.destroyed]
        if available:
            weights=self.rules.GEN_PANEL_WEIGHTS
            roll=self.random.random()*sum(weights[i] for i in available)
            self.order_index=available[-1]
            for i in available:
                roll-=weights[i]
                if roll<0:
                    self.order_index=i
                    break

    def _advance_panel(self, delay):
        self._choose_pair()
        self.panel_state = 'idle'
        self.panel_timer = 0.0
        self.pending_press = 0.0
        self.next_panel_delay = delay

    def _log_dropped_press(self, reason):
        """Leave one line per attempt so a missed press can be traced later."""
        log = getattr(self.session, 'log', None)
        if not callable(log):
            return
        if self.elapsed - getattr(self, '_press_log_at', -10.0) < 2.0:
            return
        self._press_log_at = self.elapsed
        log('gen press: ' + reason)

    def step(self, dt):
        if not self.enabled or self.finished:
            return
        self.elapsed += dt
        self.panel_timer += dt
        self.gen_timer += dt
        get_players = getattr(self.session, 'combat_players', None)
        players = (get_players() if callable(get_players)
                   else (() if self.session.player is None else (self.session.player,)))
        if self.panel_state != 'lit':
            interacting = getattr(self.session, 'interacting_players', None)
            if callable(interacting):
                interacting()
            else:
                self.session.interact_edge()

        if self.panel_state == 'idle':
            if self.panel_timer >= self.next_panel_delay:
                self._light_panel()
            return

        if self.panel_state == 'lit':

            if not self.panel_interactable and self.panel_timer >=\
                    self.rules.PANEL_ACTIVATION_DELAY:
                self.session.batch.modify(self.active_panel, 'INTERACT',
                                          intval=1)
                self.panel_interactable = True
                self.session.event('panel-interactable',
                                   panel=self.active_panel)
            if self.panel_timer >= self.rules.PANEL_LIT_DURATION:
                self._auto_close_panel()
                return
            panel = self.room.object(self.active_panel)
            if panel is None or not players:
                return
            panel_rect = panel.rect()

            reach = 24 * 256
            def can_reach(player, press=False):
                player_rect = player.rect()
                target = panel_rect
                raw = getattr(player, 'raw', {})
                if press and 'gen_press' in raw:
                    from ..interactions import gen_press_document
                    try:
                        sample = gen_press_document(raw['gen_press'])
                        if (sample['seed'] != raw.get('room_seed') or
                                sample['objid'] != getattr(player, 'objid', 0) or
                                sample['worldid'] != getattr(player, 'worldid', 0)):
                            return False
                        target = next(row['rect'] for row in sample['panels'] if row['objid'] == self.active_panel)
                        if tuple(target[2:]) != tuple(panel_rect[2:]):return False
                        player_rect = sample['rect']
                    except (ValueError, TypeError, KeyError, StopIteration):return False
                return (can_interact(player) and player.alive and player_rect[0] < target[0] + target[2]
                        and target[0] < player_rect[0] + player_rect[2]
                        and player_rect[1] < target[1] + target[3]
                        and target[1] - reach <= player_rect[1] + player_rect[3])
            def near(player):
                """Same check with one reach of slack for a press still in flight."""
                player_rect = player.rect()
                return (can_interact(player) and player.alive
                        and player_rect[0] < panel_rect[0] + panel_rect[2] + reach
                        and panel_rect[0] - reach < player_rect[0] + player_rect[2]
                        and player_rect[1] < panel_rect[1] + panel_rect[3] + reach
                        and panel_rect[1] - reach - reach <= player_rect[1] + player_rect[3])
            overlaps = any(can_reach(p) for p in players)
            if overlaps:
                self.overlap_seconds += dt
            else:
                self.overlap_seconds = 0.0
            interacting = getattr(self.session, 'interacting_players', None)
            pressed_states = tuple(interacting()) if callable(interacting) else ()
            sampled_press = any('gen_press' in getattr(p, 'raw', {}) and can_reach(p, True) for p in pressed_states)
            pressed = (any(can_reach(p, True) for p in pressed_states) if callable(interacting)
                       else self.session.interact_edge())
            if self.auto_interact and overlaps and self.overlap_seconds > 0.4:
                pressed = True
            if pressed:
                if (overlaps or sampled_press) and self.panel_interactable:
                    self.pending_press = 0.0
                    self._request_panel_activation()
                    return
                # Keep a press that lands while the panel is still coming up, or
                # while the reported player rectangle is a step short of the
                # panel, instead of dropping it and asking for another press.
                if overlaps or any(near(p) for p in pressed_states):
                    self.pending_press = self.rules.PANEL_ACTIVATION_DELAY
                    self._log_dropped_press('kept until the panel was ready')
                else:
                    self._log_dropped_press('player was not on the panel')
            else:
                self.pending_press = max(0.0, self.pending_press - dt)
            if self.pending_press > 0 and overlaps and self.panel_interactable:
                self.pending_press = 0.0
                self._request_panel_activation()
            return

        if self.panel_state == 'activating':
            delay = self.rules.PANEL_INTERACTION_DELAY
            if self.panel_timer < delay:
                return
            remainder = self.panel_timer - delay
            self._activate_panel()
            self.gen_timer = remainder

        if self.panel_state == 'off' and self.gen_state == 'opening':
            if self.gen_timer >= self.rules.PANEL_OPEN_DELAY:
                self._open_gen()
                self.panel_state = 'done'
                self.gen_state = 'opening'

                self.gen_timer -= self.rules.PANEL_OPEN_DELAY
            else:
                return

        if self.gen_state == 'opening':
            if self.gen_timer >= self.rules.GEN_ARM_DELAY:
                self._arm_gen()
            return

        if self.gen_state == 'armed':
            if self.gen_timer >= self.rules.GEN_WINDOW_SECONDS:
                self._close_gen()
            return

        if self.gen_state == 'closing':
            if self.gen_timer >= self.rules.GEN_DISARM_DELAY:
                self._finish_close()
            return

        if self.panel_state == 'relight':

            if self.cycle_deadline is not None and self.elapsed >= self.cycle_deadline:
                self._advance_panel(0.0)
                self._light_panel()
            return

    def apply_damage(self, gen_id, value):
        if gen_id not in self.hp:
            return None
        if self.gen_state not in ('armed', 'closing') or self.active_gen != gen_id:
            return None
        self.hp[gen_id] = max(0, self.hp[gen_id] - max(0, int(value)))
        self.session.event('gen-damage', gen=gen_id, hp=self.hp[gen_id],
                           value=int(value))
        if self.hp[gen_id] <= 0:
            self._destroy_gen(gen_id)
            return {'destroyed': True, 'hp': 0}
        return {'destroyed': False, 'hp': self.hp[gen_id]}

    def start(self):
        self.enabled = True
        self.panel_state = 'idle'
        self.panel_timer = 0.0
        self.next_panel_delay = self.rules.PANEL_FIRST_DELAY
        self.elapsed = 0.0
        self.cycle_deadline = None
