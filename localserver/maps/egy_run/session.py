'Native multi-room Graveyard host. Captures are never read by this host.'
import copy
import math
import random
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ...world_snapshot import WorldSnapshot, decode_delta, plain
from ..run_common.actors import ActorSession
from ..run_common.session import RoomServer
from ..run_common.commit import tracked_tick
from .run import EgyRun
from .scene import EgyRunPack
from ..hulk_mid.scene import normalize

def _initial_snapshot(server, run, instance):
    if instance.snapshot is not None:
        return
    document = copy.deepcopy(instance.recipe['world'])
    document['worldid_'] = 2
    document['worldstate_']['serverworld_']['ownerid_'] = 1
    document['worldstate_']['clientworld_'] = []
    if instance.role == 'boss':

        objects = document['worldstate_']['serverworld_']['obj_']
        document['worldstate_']['serverworld_']['obj_'] = [o for o in objects
            if o['props_'].get('vid_') != 'hulk.bossgunturret']
    instance.snapshot = WorldSnapshot(server.layouts, document)
    _progress(run, instance)

def _progress(run, instance, emit=False):
    """Apply persistent logical progress to both paused and active worlds."""
    snapshot = instance.snapshot
    if snapshot is None:
        return
    runtime = instance.runtime
    actors = getattr(runtime, 'actors', {})
    batch = getattr(runtime, 'batch', None) if emit else None

    def change(oid, field, value):
        obj = snapshot.objects.get((1, oid))
        if obj is None:
            return
        changed = obj['props'].get(field + '_') != value
        obj['props'][field + '_'] = value
        if oid in actors:
            actors[oid]['props'][field] = value
            actors[oid]['vid'] = actors[oid]['props'].get('vid', actors[oid]['vid'])
        if batch is not None and changed:
            kwargs = {'boolval': value} if isinstance(value, bool) else\
                     {'stringval': value} if isinstance(value, str) else {'intval': value}
            batch.modify(oid, field.upper(), **kwargs)

    for (_owner, oid), row in list(snapshot.objects.items()):
        if _owner != 1:
            continue
        vid = row['props'].get('vid_', '')
        if instance.role == 'hall':
            colour = {'1': 'green', '2': 'purple', '3': 'yellow'}.get(vid[-1:])
            if vid.startswith('hulk.forcefield') and run.activated.get(colour):
                snapshot.retire((1, oid))
                actors.pop(oid, None)
                if batch is not None:
                    batch.destroy(oid, ownerid=1, reason='INTERNAL')
            elif vid.startswith('hulk.door') and colour:

                change(oid, 'interact', 255 if run.activated.get(colour) else 1)
            elif vid in ('hulk.bossgate', 'hulk.bossgateopen'):
                change(oid, 'vid', 'hulk.bossgateopen' if run.boss_open else 'hulk.bossgate')
                change(oid, 'interact', 1 if run.boss_open else 0)
        colour = instance.recipe.get('switches', {}).get(oid)
        if colour is None:
            colour = instance.recipe.get('switches', {}).get(str(oid))
        if colour:
            number = {'green': 1, 'purple': 2, 'yellow': 3}[colour]
            enabled = colour != 'yellow' or run.mid_defeated
            inactive = run.activated[colour] or not enabled
            change(oid, 'vid', 'hulk.switch%s%s' % (number, 'open' if inactive else ''))
            change(oid, 'interact', 255 if run.activated[colour] else 1 if enabled else 0)
        link = instance.recipe.get('links', {}).get(oid)
        if link is None:
            link = instance.recipe.get('links', {}).get(str(oid))
        if link and link.get('requires') in ('green', 'purple', 'yellow'):
            enabled = run.allows(link)
            change(oid, 'draw', enabled)
            change(oid, 'interact', 1 if enabled else 0)

def world_blob(server, run, role=None, entry=None):
    from tools.make_create_world import wrap_compress
    instance = run.room(role)
    _initial_snapshot(server, run, instance)
    _progress(run, instance)
    document = instance.snapshot.document(2)
    spawn = run.destination_spawn(instance.role, entry or run.entry)

    spawn_key = document.get('spawnobj_', {})
    spawn_id = spawn_key.get('objid_') if isinstance(spawn_key, dict) else None
    for obj in document['worldstate_']['serverworld_']['obj_']:
        if obj['objid_'] == spawn_id:
            width, height = instance.recipe['metadata'].get('spawn_footprint_mp', (10240, 20480))
            obj['props_'].update(xmp_=spawn[0] + width // 2, ymp_=spawn[1] + height)
    return wrap_compress(server.layouts.encode('CreateWorld', document))

def initial_world(server, options):
    """Called only for a new run/restart; internal doors do not call this."""
    pack = EgyRunPack()
    configured_seed = getattr(options, 'live_seed', None)
    previous = getattr(server, 'egy_run', None)
    run = EgyRun(pack, configured_seed)
    if configured_seed is None and previous is not None:
        old_variant = previous.room('entry').recipe['variant']

        for _ in range(64):
            if run.room('entry').recipe['variant'] != old_variant:
                break
            run = EgyRun(pack)
    server.egy_run = run
    options.room_roll_seed = run.room().seed
    return world_blob(server, run)

class _RoomServer(RoomServer):
    """EGY shares the same send-disposition contract as the other full runs."""

class ExplorationSession(ActorSession):
    """Graveyard components configured on the shared native actor host."""
    def __init__(self, server, channel, options, instance, bridge, pack):
        from .enemies import ExplorationEnemies
        from .mechanics import ExplorationMechanics
        from .presentation import Presentation
        from .profiles import TEMPLATES
        super().__init__(server, channel, options, instance, bridge, pack,
            map_id='egy_run', enemies_factory=ExplorationEnemies,
            mechanics_factory=ExplorationMechanics, presentation_factory=Presentation,
            templates=TEMPLATES)

    def _expire_actor(self, oid):
        if oid in self.enemies.owned:
            projectile = next((p for p in self.enemies.projectiles.projectiles
                               if p.objid == oid), None)
            if projectile is not None:
                self.enemies._finish_projectile(projectile, 'LIFETIME')
            else:
                self.enemies._retire(oid, 'LIFETIME')
        elif self.actors[oid].get('_mechanic'):
            self.mechanics._retire(oid, 'LIFETIME')
        else:
            self._retire(oid, 'LIFETIME')

class EgyRunLiveSession:
    HZ = 60.
    INTERACT_RANGE = 96 * 256

    def __init__(self, server, channel, options, room=None, log=None, bridge=None):
        self.server, self.channel, self.options = server, channel, options
        self.layouts = server.layouts
        self.log = log or (lambda _: None)
        self.worldid, self.map_id = 1, 'egy_run'
        self.run = getattr(server, 'egy_run', None)
        if self.run is None:
            raise RuntimeError('EGY run must be created before sending CreateWorld')
        self.instance = self.run.room()
        _initial_snapshot(server, self.run, self.instance)
        self._snapshot_native_origin=self.instance.snapshot.time
        self.bridge = bridge or PlayerBridge(getattr(options, 'bridge_port', 21550), log=self.log)
        self.owns_bridge = bridge is None
        self.player_type = int(getattr(options, 'player_type', 1) or 1)
        self.suppress_send = False
        self._stop, self._thread = threading.Event(), None
        self._interact_down = True
        self._ready_after = time.time()
        self._ready = False
        server._egy_input_serial = getattr(server, '_egy_input_serial', 0) + 1
        self._input_serial = server._egy_input_serial
        self._publish_input_context()
        self.proxy = _RoomServer(self, self.instance)
        if self.instance.runtime is None:
            child_options = SimpleNamespace(**vars(options))
            child_options.room_roll_seed = self.instance.seed
            child_options.live_seed = self.instance.seed
            if self.instance.role == 'mid':
                from ..hulk_mid.session import HulkMidLiveSession
                from ..hulk_mid.scene import HulkMidPack
                child_options.map = 'hulk_mid'
                pack = HulkMidPack(self.run.pack.root / 'encounters' / 'mid')
                self.instance.runtime = HulkMidLiveSession(self.proxy, channel, child_options,
                    log=self.log, bridge=self.bridge, pack=pack, seed=self.instance.seed)
                self.instance.runtime._next_id = max(1000,
                    max((key[1] for key in self.instance.snapshot.objects), default=0)) + 1000
            elif self.instance.role == 'boss':
                from ...live.room import BossRoom
                from ...live.session import LiveSession
                source = self.instance.recipe['world']['worldstate_']['serverworld_']['obj_']
                backing_ids = set(self.instance.recipe['metadata'].get('wall_backing', {}).get('added_ids', []))
                source = [obj for obj in source if obj['objid_'] not in backing_ids]
                boss_room = BossRoom(copy.deepcopy(source), getattr(room, 'templates', {}), source='egy_run:boss')
                child_options.map = 'egy_hulk'
                self.instance.runtime = LiveSession(self.proxy, channel, child_options, boss_room,
                    log=self.log, bridge=self.bridge)
                self.instance.runtime.prepare()
            else:
                self.instance.runtime = ExplorationSession(self.proxy, channel, child_options,
                    self.instance, self.bridge, self.run.pack)
        self.current = self.instance.runtime
        self.current.server, self.current.channel = self.proxy, channel
        self.current.bridge = self.bridge
        self.current.player_type = self.player_type
        self.current.suppress_send = False
        if hasattr(self.current, 'supplies'):
            self.current.supplies.map_id = 'egy_run'
        self.instance.visits += 1
        _progress(self.run, self.instance)

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log('EGY full run: room=%s, run seed=%s' % (self.run.current, self.run.seed))

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _loop(self):
        deadline = time.monotonic()
        previous = deadline - 1 / self.HZ
        while not self._stop.is_set():
            deadline += 1 / self.HZ
            now = time.monotonic()
            dt = min(.05, max(.000001, now - previous))
            previous = now
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log('egy_run tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            self._stop.wait(max(0, deadline - time.monotonic()))
            if deadline < time.monotonic() - 1 / self.HZ:
                deadline = time.monotonic()

    @staticmethod
    def _distance(player, props):
        x, y, w, h = player.rect()
        a, b, c, d = (props.get(k, 0) for k in ('xmp_', 'ymp_', 'wmp_', 'hmp_'))
        return math.hypot(max(a - x - w, x - a - c, 0), max(b - y - h, y - b - d, 0))

    def interact(self, oid, player, check_range=True):
        from ...interactions import can_interact
        if self.suppress_send or not can_interact(player) or player.hp <= 0:
            return False
        obj = self.instance.snapshot.objects.get((1, int(oid)))
        if obj is None:
            return False
        props = self.instance.snapshot.properties_at((1, int(oid)))
        if not props.get('interact_', 0) or props.get('draw_', True) is False:
            return False
        if check_range and self._distance(player, props) > self.INTERACT_RANGE:
            return False
        switches = self.instance.recipe.get('switches', {})
        colour = switches.get(oid, switches.get(str(oid)))
        if colour:
            if not self.run.activate(colour):
                return False
            self._present_interaction(oid, props)
            _progress(self.run, self.instance, emit=True)
            self.log('EGY switch activated: ' + colour)
            return True
        links = self.instance.recipe.get('links', {})
        link = links.get(oid, links.get(str(oid)))
        if link and self.run.allows(link):
            self._present_interaction(oid, props, flush=True)
            if link['destination'] not in self.run.pack.room_names:
                return self.server.request_live_world_exit(self.channel,self,link,player)
            return self.server.request_live_room_transition(self.channel, self, link, player)
        if self.instance.role == 'boss' and self.run.completed and props.get('vid_') == 'lobbyportal':
            return self.server.request_live_world_exit(self.channel,self,dict(destination='home',external=True),player)
        mechanics = getattr(self.current, 'mechanics', None)
        return bool(mechanics and mechanics.interact(int(oid), player))

    def _present_interaction(self, oid, props, flush=False):
        from .presentation import DOOR_USED
        action = DOOR_USED.get(props.get('vid_'))
        if action:
            self.current.batch.animprops(oid, *action)

        if flush and (self.current.batch.objects or self.current.batch.general):
            self.current.batch.dt = 0
            self.proxy._send_raw(self.channel, 12, self.current.batch.encode(),
                                 'egy-door-presentation', record=False)
            self.current.batch.clear()

    def _publish_input_context(self):
        self.bridge.native_context = dict(map='egy_run', room_token=self._input_serial,
            room_seed=self.instance.recipe['world']['worldprops_'].get('seed_', 0),
            room_ready=self._ready, room_handoff=self.run.transitions > 0)

    @tracked_tick
    def step(self, dt):
        if not math.isfinite(dt) or not 0 <= dt <= 1:
            raise ValueError('Invalid EGY tick duration')
        state, keys = self.bridge.snapshot()
        consume = getattr(self.bridge, 'consume_interactions', None)
        pressed = tuple(consume()) if callable(consume) else None
        if not self._ready:
            spawn = self.run.destination_spawn(self.run.current, self.run.entry)
            if state is not None and state.received_since(self._ready_after) and\
                    math.hypot(state.xmp - spawn[0], state.ymp - spawn[1]) < 256 * 256:
                self._ready = True
                self._publish_input_context()
        if not self._ready:

            self.server._send_raw(self.channel, 12,
                DeltaBatch(self.layouts, 1).encode(), 'egy-loading', record=False)
            return

        edge = False
        if state is not None and state.received_since(self._ready_after) and state.age() <= .5:
            down = bool(keys.get('interact'))
            if pressed is None:
                edge = down and not self._interact_down
            else:
                edge = any(p.received_since(self._ready_after) for p in pressed)
            self._interact_down = down
            if keys.get('interaction_blocked'):edge=False
        if edge and state is not None:
            candidates = [(self._distance(state, self.instance.snapshot.properties_at(key)), key[1])
                for key, obj in self.instance.snapshot.objects.items()
                if key[0] == 1 and obj['props'].get('interact_', 0)]
            for distance, oid in sorted(candidates):
                if distance > self.INTERACT_RANGE:
                    break
                if self.interact(oid, state):
                    break
            if self.suppress_send:
                return
        self.current.step(dt)
        if self.instance.role == 'mid' and self.current.phase == 'done' and not self.run.mid_defeated:
            self.run.mid_defeated = True
            _progress(self.run, self.instance, emit=True)
        elif self.instance.role == 'boss' and self.current.phase == 'done':
            self.run.completed = True
        if self.server.poll_live_room_inventory(self.channel,self):return

    def status(self):
        return dict(map='egy_run', phase='done' if self.run.completed else 'exploring',
                    ready=self._ready, run=self.run.status(), room=self.current.status(),
                    inventory=getattr(self,'_inventory_status',None), commit=self.commit_status())

    def commit_status(self):
        return self.proxy.commit.status(self)
