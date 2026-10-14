"""UDP telemetry bridge between the retail client plugin and the server.

The retail protocol never sends the player's position to the server (the
original server simulated players itself).  The live controller therefore
runs next to the retail client and receives a small JSON telemetry stream
from the SBPE plugin:

* player rect/hp/facing up to 60 Hz (limited by the native frame rate);
* keyboard edges (interact / attack) when they change;
* locally emitted, already-resolved ``GeneralUpdate_Damage`` events.

The server answers every datagram with ``{"ack": <seq>}`` so the plugin can
show a link indicator without opening a second connection.
"""

import json
import math
import select
import socket
import threading
import time
from .restore import RestoreChannel

from .egress import EgressMailbox

class PlayerState(object):
    def __init__(self, **values):
        self.objid = int(values.get('objid') or 0)
        self.worldid = int(values.get('worldid') or 0)
        self.xmp = int(values.get('xmp') or 0)
        self.ymp = int(values.get('ymp') or 0)
        self.wmp = int(values.get('wmp') or 10 * 1024)
        self.hmp = int(values.get('hmp') or 20 * 1024)
        self.hp = int(values.get('hp') or 0)
        self.maxhp = int(values.get('maxhp') or 0)
        self.facing = int(values.get('facing',1))
        self.alive = bool(values.get('alive', True))
        self.received_at = float(values.get('received_at') or time.time())
        self.raw = dict(values)

    def rect(self):
        return (self.xmp, self.ymp, self.wmp, self.hmp)

    def center(self):
        return (self.xmp + self.wmp // 2, self.ymp + self.hmp // 2)

    def age(self):
        return time.time() - self.received_at

class PlayerBridge(object):
    def __init__(self, port, host='127.0.0.1', log=None):
        self.port = int(port)
        self.host = host
        self.log = log
        self.state = None
        self.events = []
        self.keys = {}
        self._interactions = []
        self._interaction_seq = -1
        self.datagrams = 0
        self.last_error = None
        self.native_context = None
        self._lock = threading.Lock()
        self._socket = None
        self._stop = threading.Event()
        self._thread = None
        self._teleport_serial = 0
        self._teleport_command = None
        self._egress = EgressMailbox()
        self._restore = RestoreChannel()

    def start(self):
        if self._thread is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.host, self.port))
        sock.setblocking(False)
        self._socket = sock
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self._log('live bridge listening on {0}:{1}'.format(
            self.host, self.port))

    def stop(self):
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    def _run(self):
        while not self._stop.is_set():
            try:
                ready, _, _ = select.select([self._socket], [], [], 0.25)
            except Exception:
                break
            if not ready:
                continue
            try:
                payload, address = self._socket.recvfrom(65535)
            except Exception as error:
                self.last_error = str(error)
                continue
            try:
                document = json.loads(payload.decode('utf-8'))
            except Exception as error:
                self.last_error = 'bad datagram: ' + str(error)
                continue
            if not isinstance(document,dict):continue
            seq = document.get('seq')
            try:
                self._socket.sendto(json.dumps(self.acknowledgement(seq),
                    separators=(',', ':')).encode('ascii'), address)
            except Exception:
                pass
            if not self._restore.absorb(document, payload):
                self._absorb(document)

            restore = self._restore.outgoing()
            if restore is not None:
                try:self._socket.sendto(restore, address)
                except Exception:pass

    def acknowledgement(self, seq):
        message = {'ack': int(seq or 0)}
        context = self.native_context
        if context and context.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','home'):
            message.update(context)
        with self._lock:
            message.update(self._egress.fields())
            command = self._teleport_command
            if command is not None:
                same_room = context and all(command[key] == context.get(key)
                    for key in ('map', 'room_token', 'room_seed'))
                if same_room and time.time() <= command['expires_at']:
                    message['player_teleport'] = dict(command)
                else:
                    self._teleport_command = None
        return message

    def arm_ground_egress(self,control):
        with self._lock:self._egress.arm(control)

    def attach_ground_restore(self, endpoint):
        return self._restore.attach(endpoint)

    def detach_ground_restore(self, endpoint):
        return self._restore.detach(endpoint)

    def advance_ground_restore(self, endpoint, now):
        return self._restore.advance(endpoint, now)

    def ground_restore_status(self):
        return self._restore.status()

    def acknowledge_ground_egress(self,ack):
        with self._lock:self._egress.acknowledge(ack)

    def cancel_ground_egress(self,control):
        with self._lock:self._egress.request_cancel(control)

    def ground_egress_snapshot(self):
        with self._lock:return self._egress.drain()

    def ground_egress_status(self):
        import copy
        with self._lock:return copy.deepcopy(self._egress.ready)

    def finish_ground_egress_ack(self):
        with self._lock:self._egress.finishing=None

    def request_player_teleport(self, source_oid, player, destination):
        """Issue one reliable, room-scoped local-player placement command.

        The client owns the player. Position is never sent as a server MTC;
        repeated ACKs retry the command, and the client commits each serial once.
        """
        from ..interactions import can_interact
        if not can_interact(player):
            return False
        context = self.native_context
        if (not context or context.get('map') != 'eub_run' or not context.get('room_ready')
                or player is None or not player.alive or player.age() > .5):
            return False
        if len(destination) != 2 or any(not math.isfinite(v) or abs(v) > 2147483647 for v in destination):
            raise ValueError('Invalid native teleport destination')
        with self._lock:
            if self._teleport_command is not None:
                return False
            self._teleport_serial += 1
            self._teleport_command = dict(
                command_id=self._teleport_serial, source_oid=int(source_oid),
                player_id=player.objid, player_owner=player.worldid,
                to=[round(v) for v in destination], expires_at=time.time()+1.5,
                **{key: context[key] for key in ('map', 'room_token', 'room_seed')})
        return True

    def _absorb(self, document):
        if not isinstance(document,dict):return
        if self._restore.absorb(document):return
        with self._lock:
            if self._egress.absorb(document):return
        context = self.native_context
        scoped = context and context.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','home')
        if scoped:
            source = document.get('room_context') or {}
            if any(source.get(key) != context.get(key) for key in ('map','room_token','room_seed')):
                return
        player = document.get('player')
        state = None
        if isinstance(player, dict) and int(player.get('objid') or 0) > 0:
            player = dict(player)
            player['received_at'] = time.time()
            state = PlayerState(**player)
        events = document.get('events') or []
        with self._lock:
            self.state = state
            keys = document.get('keys') or {}
            edge = bool(keys.get('interact_edge')) or (bool(keys.get('interact'))
                   and not self.keys.get('interact'))
            seq = document.get('seq')
            if type(seq) is int:
                edge = edge and seq > self._interaction_seq
                self._interaction_seq = max(self._interaction_seq, seq)
            if edge and state is not None and not keys.get('interaction_blocked'):
                self._interactions.append(state)
                self._interactions = self._interactions[-64:]
            self.keys = keys
            for event in events:
                if isinstance(event, dict):
                    if scoped and event.get('kind') in ('damage','destroy') and event.get('native_seed') != context['room_seed']:
                        continue
                    if event.get('kind') == 'player_teleport_result':
                        command = self._teleport_command
                        if command is None or event.get('command_id') != command['command_id']:
                            continue
                        event = dict(event, source_oid=command['source_oid'])
                        self._teleport_command = None
                    self.events.append(event)
            if len(self.events) > 4096:
                self.events = self.events[-2048:]
            self.datagrams += 1

    RESTART_KINDS = ('restart', 'switch_character')

    def poll_events(self, limit=256):
        """Drain queued gameplay events, leaving the long-press holds.

        Map sessions call this every tick and discard what they do not
        understand; the R restart / 7-8-9-0 class-switch holds belong to
        the server's map-independent handler, so they stay queued here for
        every current and future map session.
        """
        events, remaining = [], []
        with self._lock:
            for event in self.events:
                kind = event.get('kind') if isinstance(event, dict) else None
                if kind in self.RESTART_KINDS:
                    remaining.append(event)
                elif len(events) < limit:
                    events.append(event)
                else:
                    remaining.append(event)
            self.events = remaining
        return events

    def peek_events(self, limit=256):
        """Inspect pending events without consuming them."""
        with self._lock:
            return list(self.events[:limit])

    def consume_events(self, kinds):
        """Drop pending events of the given kinds (restart hand-off)."""
        wanted = set(kinds)
        with self._lock:
            self.events = [
                event for event in self.events
                if not (isinstance(event, dict)
                        and event.get('kind') in wanted)]

    def snapshot(self):
        with self._lock:
            return self.state, dict(self.keys)

    def consume_interactions(self):
        """Keep fast presses until the next simulation tick, once per edge."""
        with self._lock:
            players, self._interactions = self._interactions, []
        return tuple(player for player in players if player.age() <= .5)

    def connected(self, max_age=2.0):
        state = self.snapshot()[0]
        return state is not None and state.age() <= max_age

    def _log(self, message):
        if self.log is not None:
            self.log(message)
