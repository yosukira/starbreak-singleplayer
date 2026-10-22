'Native full-level transport and persistent room coordination.'
import copy
import math
import threading
import time
from types import SimpleNamespace
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...world_snapshot import WorldSnapshot, decode_delta
from .actors import ActorSession
from .run import LevelRun
from .commit import RoomCommitState, tracked_tick
from ..hulk_mid.scene import normalize

class RoomServer:
    def __init__(self, wrapper, instance):
        self.wrapper, self.instance = wrapper, instance
        self.commit = RoomCommitState()

    def __getattr__(self, key):
        return getattr(self.wrapper.server, key)

    def _send_raw(self, channel, kind, payload, label='', record=False):
        if self.wrapper.suppress_send:
            self.commit.suppressed += 1
            return
        try:
            if kind == 12:
                message=decode_delta(self.layouts,payload)
                self.instance.snapshot.apply(message)
                self.commit.snapshot_revision += 1
                if 'createloot_' in message:
                    self.wrapper._inventory_effect_epoch=getattr(self.wrapper,'_inventory_effect_epoch',0)+1
        except Exception as error:
            self.commit.fail('snapshot failed: '+type(error).__name__+': '+str(error))
            raise
        self.commit.attempted += 1
        try:
            result = self.wrapper.server._send_raw(channel, kind, payload, label, record=record)
        except Exception as error:
            self.commit.fail('TCP send failed: '+type(error).__name__+': '+str(error), transport_uncertain=True)
            raise
        if result is False:
            self.commit.fail('TCP sender explicitly rejected payload', transport_uncertain=True)
        else:
            self.commit.sent += 1
        return result

    def request_live_restart(self, channel, session, player_type):
        return self.wrapper.server.request_live_restart(channel, self.wrapper, player_type)

def change(instance, oid, field, value, emit=False):
    obj = instance.snapshot.objects.get((1, int(oid)))
    if obj is None:
        return
    changed = obj['props'].get(field+'_') != value
    obj['props'][field+'_'] = value
    runtime = instance.runtime
    actor = getattr(runtime, 'actors', {}).get(int(oid))
    if actor is not None:
        actor['props'][field] = value
        if field == 'vid':
            actor['vid'] = value
    if changed and emit and getattr(runtime, 'batch', None) is not None:
        kwargs = {'boolval':value} if isinstance(value, bool) else {'stringval':value} if isinstance(value, str) else {'intval':value}
        runtime.batch.modify(int(oid), field.upper(), **kwargs)

class RunLiveSession:
    HZ = 60.
    INTERACT_RANGE = 96*256
    MAP_ID = None
    PACK_CLASS = None
    RUN_CLASS = LevelRun

    @classmethod
    def prepare_document(cls, run, instance, document):
        return document

    @classmethod
    def apply_progress(cls, run, instance, emit=False):
        for oid, link in instance.recipe.get('links', {}).items():
            if not link.get('requires'):
                continue
            enabled = run.allows(link, role=instance.role)
            change(instance, oid, 'interact', 1 if enabled else 0, emit)
            if link.get('external') or link.get('destination') in ('home', 'lobby'):
                change(instance, oid, 'draw', enabled, emit)

    @classmethod
    def initial_snapshot(cls, server, run, instance):
        if instance.snapshot is None:
            document = copy.deepcopy(instance.recipe['world'])

            chosen = instance.recipe['objects']
            rows = []
            for oid, props in sorted(chosen.items()):
                row = dict(objid_=int(oid), props_=normalize('Properties', props, server.layouts))

                rows.append(row)
            document['worldstate_']['serverworld_']['obj_'] = rows
            document['spawnobj_'] = dict(worldid_=1,objid_=instance.recipe['metadata']['spawn_marker_id'])
            document['worldid_'] = 2
            document['worldstate_']['serverworld_']['ownerid_'] = 1
            document['worldstate_']['clientworld_'] = []
            instance.snapshot = WorldSnapshot(server.layouts, cls.prepare_document(run, instance, document))
        cls.apply_progress(run, instance)

    @classmethod
    def world_blob(cls, server, run, role=None, entry=None):
        from tools.make_create_world import wrap_compress
        instance = run.room(role)
        cls.initial_snapshot(server, run, instance)
        document = instance.snapshot.document(2)
        point = run.destination_spawn(instance.role, entry or run.entry)
        marker = document.get('spawnobj_', {}).get('objid_')
        width, height = instance.recipe['metadata'].get('spawn_footprint_mp', (10240,20480))
        for row in document['worldstate_']['serverworld_']['obj_']:
            if row['objid_'] == marker:
                row['props_'].update(xmp_=point[0]+width//2, ymp_=point[1]+height)
        return wrap_compress(server.layouts.encode('CreateWorld', document))

    @classmethod
    def initial_world(cls, server, options):
        run = cls.RUN_CLASS(cls.PACK_CLASS(), getattr(options, 'live_seed', None))
        server.level_run = run
        options.room_roll_seed = run.room().seed
        return cls.world_blob(server, run)

    def __init__(self, server, channel, options, room=None, log=None, bridge=None):
        self.server, self.channel, self.options = server, channel, options
        self.layouts, self.log = server.layouts, log or (lambda _:None)
        self.worldid, self.map_id = 1, self.MAP_ID
        self.run = getattr(server, 'level_run', None)
        if self.run is None:
            raise RuntimeError(self.map_id+' run must be created before CreateWorld')
        self.instance = self.run.room()
        self.initial_snapshot(server, self.run, self.instance)
        self._snapshot_native_origin=self.instance.snapshot.time
        self.bridge = bridge or PlayerBridge(getattr(options, 'bridge_port',21550), log=self.log)
        self.owns_bridge = bridge is None or getattr(self.bridge, '_level_bridge_owned', False)
        if self.owns_bridge:
            self.bridge._level_bridge_owned = True
        self.player_type = int(getattr(options, 'player_type',1) or 1)
        self.suppress_send = False
        self._stop, self._thread = threading.Event(), None
        self._interact_down, self._ready = True, False
        self._ready_after = time.time()
        self._boundary_armed = set()
        server._level_input_serial = getattr(server, '_level_input_serial',0)+1
        self._input_serial = server._level_input_serial
        self.proxy = RoomServer(self, self.instance)
        self._publish_input_context()
        if self.instance.runtime is None:
            child = SimpleNamespace(**vars(options))
            child.live_seed = child.room_roll_seed = self.instance.seed
            self.instance.runtime = self.create_runtime(child, room)
        self.current = self.instance.runtime
        self.current.server, self.current.channel, self.current.bridge = self.proxy, channel, self.bridge
        self.current.player_type, self.current.suppress_send = self.player_type, False
        if hasattr(self.current, 'supplies'):
            self.current.supplies.map_id = self.map_id
        self.instance.visits += 1
        self.apply_progress(self.run, self.instance)

    def create_runtime(self, options, room):
        raise NotImplementedError

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _loop(self):
        deadline = time.monotonic(); previous = deadline-1/self.HZ
        while not self._stop.is_set():
            deadline += 1/self.HZ
            now = time.monotonic(); dt = min(.05,max(.000001,now-previous)); previous=now
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log(self.map_id+' tick failed: '+traceback.format_exc())
                self._stop.set(); break
            self._stop.wait(max(0,deadline-time.monotonic()))
            if deadline < time.monotonic()-1/self.HZ:
                deadline = time.monotonic()

    @staticmethod
    def _distance(player, props):
        dx=max(props.get('xmp_',0)-player.xmp-player.wmp, player.xmp-props.get('xmp_',0)-props.get('wmp_',0),0)
        dy=max(props.get('ymp_',0)-player.ymp-player.hmp, player.ymp-props.get('ymp_',0)-props.get('hmp_',0),0)
        return math.hypot(dx,dy)

    def interact(self, oid, player, check_range=True):
        from ...interactions import can_interact
        if not can_interact(player):
            return False
        props = self.instance.snapshot.properties_at((1,int(oid)))
        if props is None or (check_range and self._distance(player,props)>self.INTERACT_RANGE):
            return False
        link = self.instance.recipe.get('links',{}).get(int(oid))
        if link:
            trigger=link.get('trigger',link.get('interaction','interact'))
            if trigger!='interact' or not self.run.allows(link):
                return False
            return self._transition(link, player)
        mechanics = getattr(self.current,'mechanics',None)
        if mechanics is not None and mechanics.interact(int(oid),player):
            self.apply_progress(self.run,self.instance,emit=True)
            return True
        return False

    def _transition(self, link, player):
        if not self.run.allows(link):
            return False
        destination = link['destination']
        if destination not in self.run.pack.room_names:
            destination='home' if destination=='lobby' else destination
            return self.server.request_live_world_exit(self.channel,self,dict(link,destination=destination),player)
        return self.server.request_live_room_transition(self.channel,self,link,player)

    def encounter_finished(self):
        return getattr(self.current,'phase',None)=='done'

    def _publish_input_context(self):
        self.bridge.native_context=dict(map=self.map_id, room_token=self._input_serial,
            room_seed=self.instance.recipe['world']['worldprops_'].get('seed_',0),
            room_ready=self._ready, room_handoff=self.run.transitions>0)

    @tracked_tick
    def step(self, dt):
        if not math.isfinite(dt) or not 0<=dt<=1:
            raise ValueError('Invalid full-level tick duration')
        player, keys = self.bridge.snapshot()
        fresh=player is not None and player.received_since(self._ready_after) and player.age()<=.5
        if not self._ready and fresh:
            x,y=self.run.destination_spawn(self.run.current,self.run.entry)
            if math.hypot(player.xmp-x,player.ymp-y)<256*256:
                self._ready=True; self._publish_input_context()
        if not self._ready:
            self.server._send_raw(self.channel,12,DeltaBatch(self.layouts,1).encode(),self.map_id+'-loading',record=False)
            return
        edge=False
        if fresh:
            down=bool(keys.get('interact')); edge=down and not self._interact_down; self._interact_down=down
            if keys.get('interaction_blocked'):edge=False
        if edge:
            candidates=[(self._distance(player,self.instance.snapshot.properties_at(key)),key[1])
                for key,obj in self.instance.snapshot.objects.items() if key[0]==1 and obj['props'].get('interact_',0)]
            for distance,oid in sorted(candidates):
                if distance>self.INTERACT_RANGE:break
                if self.interact(oid,player):break
            if self.suppress_send:return
        self.current.step(dt)
        if self.encounter_finished() or self.instance.flags.get('encounter:'+self.instance.role):
            self.run.mark_defeated(self.instance.role)
        self.apply_progress(self.run,self.instance,emit=True)
        if self.server.poll_live_room_inventory(self.channel,self):return
        if fresh and player.alive:
            for oid,link in self.instance.recipe.get('links',{}).items():
                if link.get('trigger',link.get('interaction')) not in ('native-door-boundary','boundary'):continue
                props=self.instance.snapshot.properties_at((1,int(oid)))
                if props is None:continue
                inside=(player.xmp<props.get('xmp_',0)+props.get('wmp_',0) and
                    player.xmp+player.wmp>props.get('xmp_',0) and
                    player.ymp<props.get('ymp_',0)+props.get('hmp_',0) and
                    player.ymp+player.hmp>props.get('ymp_',0))
                if not inside:self._boundary_armed.add(int(oid))
                elif int(oid) in self._boundary_armed and self.run.allows(link):
                    if self._transition(link,player):
                        self._boundary_armed.discard(int(oid))
                        return

    def status(self):
        return dict(map=self.map_id,ready=self._ready,phase='done' if self.run.completed else 'exploring',
                    run=self.run.status(),room=self.current.status(),inventory=getattr(self,'_inventory_status',None),
                    commit=self.commit_status())

    def commit_status(self):
        return self.proxy.commit.status(self)

def session_class(map_id):
    """Internal adapter availability; this does not register editor entries."""
    if map_id == 'eub_run':
        from ..eub_run.session import EubRunLiveSession
        return EubRunLiveSession
    if map_id == 'etl_run':
        from ..etl_run.session import EtlRunLiveSession
        return EtlRunLiveSession
    if map_id == 'est_run':
        from ..est_run.session import EstRunLiveSession
        return EstRunLiveSession
    if map_id == 'efb_run':
        from ..efb_run.session import EfbRunLiveSession
        return EfbRunLiveSession
    if map_id == 'eff_run':
        from ..eff_run.session import EffRunLiveSession
        return EffRunLiveSession
    if map_id == 'sc_run':
        from ..sc_run.session import ScRunLiveSession
        return ScRunLiveSession
    return None
