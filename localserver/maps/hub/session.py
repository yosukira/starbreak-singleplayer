"""Hub scenery and a reusable retail training dummy."""
import json
import math
from pathlib import Path
import threading
import time

from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from .rules import DUMMY_HP, DUMMY_VID, DUMMY_HIT_ANIM, DUMMY_HIT_MS

class HubLiveSession(object):
    HZ = 60.

    def __init__(self, server, channel, options, room=None, log=None, bridge=None):
        self.server, self.channel, self.options, self.room = server, channel, options, room
        self.log = log or (lambda message: None)
        self.map_id = options.map
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        self.batch = DeltaBatch(self.layouts, self.worldid)
        root = Path(__file__).resolve().parents[3] / 'maps' / self.map_id
        templates = json.loads((root / 'templates.json').read_text(encoding='utf8'))
        self.dummy_ids = {int(oid) for oid, props in templates.items()
                          if props.get('vid_') == DUMMY_VID}
        self.bridge = bridge or PlayerBridge(
            int(getattr(options, 'bridge_port', 21550) or 21550), log=self.log)
        self.owns_bridge = bridge is None
        self.player_type = 1
        self._stop = threading.Event()
        self._thread = None
        self._arrival_context=getattr(server,'_world_exit_arrival',None)
        if self._arrival_context is not None and self._arrival_context.get('map')==self.map_id:
            self._arrival_context=dict(self._arrival_context);server._world_exit_arrival=None
        else:self._arrival_context=None
        self.prepare()

    def prepare(self):
        self.phase = 'waiting'
        self.room_time, self.frames = 0., 0
        self.player = None
        self.dummy_hits, self.dummy_damage = 0, 0
        self._wire_time_ms, self._sent_time_ms = 0., 0
        self.batch.clear()
        self._ready_after=time.time()
        if self._arrival_context is not None:
            self.bridge.native_context=dict(self._arrival_context,room_ready=False)

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._thread = threading.Thread(target=self._run)
        self._thread.daemon = True
        self._thread.start()
        self.log(self.map_id + ' scenery session started')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        dt, deadline = 1. / self.HZ, time.monotonic()
        while not self._stop.is_set():
            deadline += dt
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log(self.map_id + ' tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            wait = deadline - time.monotonic()
            if wait > 0:
                self._stop.wait(wait)
            else:
                deadline = time.monotonic()

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        state, _keys = self.bridge.snapshot()
        if state is not None and (self._arrival_context is None or
                state.received_at>=self._ready_after and state.age()<=.5):
            self.player = state
            self.phase = 'active'
            if self._arrival_context is not None:
                self.bridge.native_context=dict(self._arrival_context,room_ready=True)

        for event in self.bridge.poll_events():
            self._feedback(event)
        self.room_time += dt
        self._wire_time_ms += dt * 1000.
        target = int(round(self._wire_time_ms))
        self.batch.dt = target - self._sent_time_ms
        self._sent_time_ms = target
        self.server._send_raw(self.channel, 12, self.batch.encode(),
                              self.map_id + '-scenery', record=False)
        self.frames += 1
        self.batch.clear()

    def _feedback(self, event):
        if event.get('kind') != 'damage':
            return
        if event.get('ownerid') is not None and int(event['ownerid']) != self.worldid:
            return
        victim, value = int(event.get('victim') or 0), int(event.get('value') or 0)
        if victim not in self.dummy_ids or value <= 0:
            return
        self.dummy_hits += 1
        self.dummy_damage += value

        self.batch.modify(victim, 'HITPOINTS', intval=DUMMY_HP)
        self.batch.animprops(victim, DUMMY_HIT_ANIM, DUMMY_HIT_MS)

    def status(self):
        value = dict(map=self.map_id, phase=self.phase,
                     time=round(self.room_time, 3), frames=self.frames)
        if self.player is not None:
            value['player'] = dict(x=self.player.xmp, y=self.player.ymp,
                                   hp=self.player.hp)
        if self.dummy_ids:
            value['dummy'] = dict(ids=sorted(self.dummy_ids), hits=self.dummy_hits,
                                  damage=self.dummy_damage)
        return value
