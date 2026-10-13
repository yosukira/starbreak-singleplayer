"""Shared live bridge transport with an independent Mid controller."""
import random
import threading
from pathlib import Path
from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ..ice_boss.session import IceBossLiveSession
from .scene import IceMidPack, roll_create_world
from .controller import IceMidController

class IceMidLiveSession(IceBossLiveSession):
    def __init__(self, server, channel, options, room=None, log=None, bridge=None, pack=None, seed=None, players=None):
        self.server, self.channel, self.options = server, channel, options
        self.log = log or (lambda message: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        self.seed = getattr(options, 'room_roll_seed', None)
        if self.seed is None:
            self.seed = seed if seed is not None else random.SystemRandom().randrange(1 << 30)
        root = Path(getattr(options, 'create_world_hex', '') or '').parent
        self.pack = pack or IceMidPack(root if (root/'entities.json').exists() else None,
                                      seed=self.seed if getattr(options, 'room_roll_seed', None) is not None else 0)
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550), log=self.log)
        self.owns_bridge = bridge is None; self.players_provider = players
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self._stop = threading.Event(); self._thread = None
        self.prepare()

    def prepare(self):
        self.controller = IceMidController(self.pack, self.seed)
        self.supplies = SupplyDrops(self, 'ice_mid'); self.batch.clear()
        self.phase = 'waiting'; self.room_time = 0.; self.bootstrapped = False
        self.players = {}; self.player = None; self.events = []; self.retired = set(); self.created = set()
        self.native_damage = self.native_destroy = self.frames = 0; self.max_tick_ms = 0.
        self._wire_ms = 0.; self._sent_ms = 0; self._frame_start = 0.; self._event_time = 0.

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare(); self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True); self._thread.start()
        self.log('ice_mid: Elite Frozen Blight Mid, procedural room / crossbeast / two1000HP refills')

    def _action(self, event):
        self._event_time = event['at']; kind = event['kind']; off = self._offset()
        if kind == 'death_begin':
            if 39 not in self.retired:
                self.batch.world_sound(self.pack.entities['sounds']['death'], offset=off)
                self.batch.destroy(39, self.worldid, 'INTERNAL', offset=off)
                self.retired.add(39)
        elif kind == 'victory':
            self.phase = 'done'
            self.batch.props(37, dict(VID='ice.nextDoor', INTERACT=255), offset=off)
        else:
            return super()._action(event)
        self.record(kind, **{k: v for k, v in event.items() if k not in ('kind', 'at')})

    def status(self):
        result = super().status(); result['map'] = 'ice_mid'
        return result
