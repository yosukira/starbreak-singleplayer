"""In-client telemetry bridge for the live Hulk Hard simulator.

Native feedback is read-only except for the explicitly scoped EGY input
handoff and existing local practice teleports/time-credit maintenance:

1. every frame, read the local player rect/hp/facing and the interact key
   edge, and send a small JSON datagram to the local server (UDP 21550);
2. observe ``World::sendGeneralUpdate`` (subhook + x86 thiscall bridge)
   and queue finalized Damage and projectile Destroy records. The former
   playerDoDamage argument was simulation time, not damage.

3. read the two arena barrels' presence/HP at 5 Hz to retire stale terrain.

The original function is still executed exactly once through the subhook
trampoline; the hook copies finalized fields into a bounded queue.

Target runtime: Python 3.5 / PyPy3 embedded in SBPE.  No f-strings.
"""

import json
import math
import os
import socket
import struct
import time

SEND_GENERAL_UPDATE = 0x45A730
INPUT_MAPPING_GET = 0x471620
WORLD_CLIENT_SET_INPUT = 0x4E03E0
INTERACT_INPUT = 7

ACK_RECV_BYTES = 65536
ACK_MAX_DATAGRAM = 64000
ACK_FRAME_PACKETS = 8
ACK_FRAME_BYTES = 128000
ACK_FRAME_SECONDS = .004

def _ack_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate ACK JSON field')
        result[key] = value
    return result

def _ack_constant(value):
    raise ValueError('nonfinite ACK JSON constant')

def _ack_json(data):
    document = json.loads(data.decode('utf-8'), object_pairs_hook=_ack_pairs,
                          parse_constant=_ack_constant)
    if not isinstance(document, dict):
        raise ValueError('ACK must be an object')
    pending = [(document, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 32 or nodes > 20000:
            raise ValueError('ACK JSON structure budget')
        if type(value) is float and not math.isfinite(value):
            raise ValueError('nonfinite ACK number')
        if isinstance(value, dict):
            pending.extend((v, depth+1) for v in value.values())
        elif isinstance(value, list):
            pending.extend((v, depth+1) for v in value)
    if 'ack' in document and (type(document['ack']) is not int or not 0 <= document['ack'] <= 4294967295):
        raise ValueError('invalid ACK sequence')

    if 'ground_restore' not in document and document.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run','home'):
        for key, low in (('room_token', 1), ('room_seed', 0)):
            if key in document and (type(document[key]) is not int or not low <= document[key] <= 4294967295):
                raise ValueError('invalid ACK room identity')
        for key in ('room_ready', 'room_handoff'):
            if key in document and type(document[key]) is not bool:
                raise ValueError('invalid ACK room flag')
    return document

class RoomInputHandoff(object):
    """One native total-only reapplication per confirmed full-level room load.

    The first room establishes the baseline. A server load token, rather
    than an allocation address, distinguishes revisits and reused pointers.
    Loading ACKs retain the pending handoff until the native player exists.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.room = None
        self.pending = False

    def due(self, context, native_seed):
        if (context.get('map') not in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run','home') or
                context.get('room_token') is None or
                context.get('room_seed') is None):
            self.reset()
            return False
        identity = (str(context['map']), str(context['room_token']), int(context['room_seed']))
        if self.room is None:
            self.room = identity

            self.pending = bool(context.get('room_handoff'))
        elif identity != self.room:
            self.room = identity
            self.pending = True
        return (self.pending and bool(context.get('room_ready')) and
                native_seed == identity[2])

    def committed(self):
        self.pending = False

class RoomTeleport(object):
    """Deduplicate room-scoped placement ACKs; never apply stale room commands."""

    def __init__(self):
        self.room = None
        self.last_id = 0
        self.last_result = None

    def apply(self, command, context, native_seed, player_id, player_owner, place):
        if (not isinstance(command, dict) or context.get('map') != 'eub_run'
                or not context.get('room_ready') or native_seed != context.get('room_seed')
                or any(command.get(key) != context.get(key)
                       for key in ('map', 'room_token', 'room_seed'))):
            return None
        serial = command.get('command_id')
        room = tuple(context[key] for key in ('map', 'room_token', 'room_seed'))
        if room != self.room:
            self.room, self.last_id, self.last_result = room, 0, None
        if type(serial) is not int or serial < self.last_id:
            return None
        if serial == self.last_id:
            return self.last_result
        if command.get('player_id') != player_id or command.get('player_owner') != player_owner:
            return None
        target = command.get('to')
        if (not isinstance(target, list) or len(target) != 2
                or any(type(v) is not int or abs(v) > 2147483647 for v in target)):
            return None
        if time.time() > command.get('expires_at', 0):
            applied = False
        else:
            applied = bool(place(target[0], target[1]))
        self.last_id = serial
        self.last_result = dict(kind='player_teleport_result', command_id=serial,
                                applied=applied)
        return self.last_result

class DamageHook(object):
    """Observe locally emitted, finalized hits/destructions read-only."""

    HOOK_CODE_CAPACITY = 64
    THUNK_SLOT_OFFSET = 48
    MEM_COMMIT_RESERVE = 0x3000
    PAGE_EXECUTE_READWRITE = 0x40

    def __init__(self, ffi, lib, target=SEND_GENERAL_UPDATE, max_events=256):
        self.ffi = ffi
        self.lib = lib
        self.target = int(target)
        self.max_events = int(max_events)
        self.events = []
        self.dropped = 0
        self._callback = None
        self._hook = None
        self._stub = 0
        self._trampoline = 0
        self._ctypes = None
        self._kernel32 = None
        self._load_kernel32()

    def _load_kernel32(self):
        import ctypes
        self._ctypes = ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.VirtualAlloc.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
            ctypes.c_uint32]
        kernel32.VirtualAlloc.restype = ctypes.c_void_p
        kernel32.VirtualFree.argtypes = [
            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
        kernel32.VirtualFree.restype = ctypes.c_int
        kernel32.FlushInstructionCache.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
        kernel32.FlushInstructionCache.restype = ctypes.c_int
        self._kernel32 = kernel32

    @property
    def installed(self):
        return self._hook is not None

    def install(self):
        if self._hook is not None:
            return True
        ffi = self.ffi

        self._callback = ffi.callback(
            'void(void *, void *, void *)', self._on_update)
        stub = self._kernel32.VirtualAlloc(
            None, self.HOOK_CODE_CAPACITY, self.MEM_COMMIT_RESERVE,
            self.PAGE_EXECUTE_READWRITE)
        if not stub:
            raise MemoryError('VirtualAlloc failed for damage hook')
        self._stub = int(stub)
        hook = self.lib.subhook_new(
            ffi.cast('void *', self.target), ffi.cast('void *', self._stub),
            0)
        if hook == ffi.NULL:
            self._free_stub()
            raise RuntimeError('subhook_new failed for sendGeneralUpdate')
        self._hook = hook
        trampoline = self.lib.subhook_get_trampoline(hook)
        if trampoline == ffi.NULL:
            self.uninstall()
            raise RuntimeError('subhook_get_trampoline failed')
        self._trampoline = int(ffi.cast('uintptr_t', trampoline))
        self._write_stub()
        self.lib.subhook_install(hook)
        if not self.lib.subhook_is_installed(hook):
            self.uninstall()
            raise RuntimeError('subhook_install failed')
        return True

    def _write_stub(self):
        callback = int(self.ffi.cast('uintptr_t', self._callback))
        slot = self._stub + self.THUNK_SLOT_OFFSET
        code = bytearray()
        code.extend(b'\x89\x0d')
        code.extend(struct.pack('<I', slot))
        code.append(0xA1)
        code.extend(struct.pack('<I', slot))
        code.append(0x50)
        code.append(0xB8)
        code.extend(struct.pack('<I', callback))
        code.extend(b'\xff\xd0')
        code.extend(b'\x83\xc4\x04')
        code.extend(b'\x8b\x0d')
        code.extend(struct.pack('<I', slot))
        code.append(0xB8)
        code.extend(struct.pack('<I', self._trampoline))
        code.extend(b'\xff\xe0')
        self._ctypes.memmove(self._stub, bytes(code), len(code))
        self._kernel32.FlushInstructionCache(
            self._kernel32.GetCurrentProcess(), self._stub, len(code))

    def _on_update(self, world, caller_return, general_update):
        try:
            del caller_return
            if general_update is None or general_update == self.ffi.NULL:
                return
            update = self.ffi.cast('struct GeneralUpdate *', general_update)
            bits = int(update._has_bits[0])
            if bits & (1 << 4) and update.destroy != self.ffi.NULL:
                destroy = update.destroy
                if int(destroy.objid) > 0 and len(self.events) < self.max_events:
                    self.events.append({'kind': 'destroy', 'victim': int(destroy.objid),
                        'ownerid': self._owner_id(destroy, world), 'reason': int(destroy.reason),
                        'owner_explicit': bool(int(destroy._has_bits[0]) & 1),
                        'at': round(time.time(), 4), 'native_seed': self._world_seed(world)})

            if not (bits & (1 << 3)) or\
                    update.damage == self.ffi.NULL:
                return
            damage = update.damage
            objid = int(damage.objid)
            value = int(damage.val)

            if objid <= 0 or value < 0:
                return
            if len(self.events) >= self.max_events:
                self.dropped += 1
                return
            self.events.append({
                'kind': 'damage',
                'native_seed': self._world_seed(world),
                'victim': objid,
                'ownerid': self._owner_id(damage, world),
                'owner_explicit': bool(int(damage._has_bits[0]) & 1),
                'value': value,
                'crit': bool(damage.iscrit),
                'mitigated': bool(damage.armormitigated),
                'at': round(time.time(), 4),
            })
        except Exception:
            self.dropped += 1

    def _world_seed(self, world):
        try:
            return int(self.ffi.cast('struct ClientWorld *', world).asWorld.props.seed)
        except Exception:
            return None

    def _owner_id(self, message, world):

        if int(message._has_bits[0]) & 1:
            return int(message.ownerid)
        cw = self.ffi.cast('struct ClientWorld *', world)
        if cw.mySubWorld != self.ffi.NULL:
            return int(cw.mySubWorld.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId)
        return 0

    def drain(self):
        events, self.events = self.events, []
        return events

    def _free_stub(self):
        if self._stub:
            self._kernel32.VirtualFree(self._stub, 0, 0x8000)
            self._stub = 0

    def uninstall(self):
        if self._hook is not None:
            try:
                self.lib.subhook_remove(self._hook)
            except Exception:
                pass
            try:
                self.lib.subhook_free(self._hook)
            except Exception:
                pass
        self._hook = None
        self._trampoline = 0
        self._callback = None
        self._free_stub()

class LiveBridge(object):
    def __init__(self, ffi, lib, util, refs, port=21550, host='127.0.0.1'):
        self.ffi = ffi
        self.lib = lib
        self.util = util
        self.refs = refs
        self.host = host
        self.port = int(port)
        self.socket = None
        self.seq = 0

        self.reserve_cap = 1000
        self.ack = 0
        self.last_ack_time = 0.0
        self.sent = 0
        self.hook = DamageHook(ffi, lib)
        self.hook_error = None
        self.health_metrics = None
        self.health_error = None
        self.enabled = False
        self._key_states = None
        self._old_interact = False
        self._input_interact_down = False
        self._input_interact_edge = False

        self._hold_since = {}
        self._hold_fired = set()
        self._terrain_world = None
        self._terrain_seen = set()
        self._terrain_snapshot = []
        self._terrain_next = 0.0
        self._appearance_next = 0.0
        self._appearance_key = None
        self._appearance_cache = {}
        self._appearance_decoder = None
        self.input_context = {}
        self._input_handoff = RoomInputHandoff()
        self._room_teleport = RoomTeleport()
        self._command_results = []
        self._input_setter = None
        self._input_mapping_get = None
        self._input_stub = 0
        self._scancode_from_key = None
        self.input_handoffs = 0
        self.last_input_handoff = None
        self._inventory_refresh = None
        self._ground_egress = None
        self._ground_restore = None
        self._ground_restore_sent_frame = None
        self._udp_target = None
        self._network_clock = time.perf_counter
        self._network_frame_id = 0
        self._network_frame = None
        self._network_previous_frame = None
        self._network_totals = dict(packets=0, bytes=0, rejected=0, restore_received=0, restore_sent=0)
        self._network_last_error = None
        self.begin_native_frame()
        self._interaction_was_blocked = False
        self._interaction_held_blocked = False
        self._interaction_pre_local = False

    def open_socket(self):
        if self.socket is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        try:
            target = (socket.gethostbyname(self.host), self.port)
        except Exception:
            sock.close()
            raise
        self.socket = sock
        self._udp_target = target

    def close_socket(self):
        if self.socket is not None:
            try:
                self.socket.close()
            except Exception:
                pass
            self.socket = None
            self._udp_target = None

    def enable(self):
        self.open_socket()
        if not hasattr(self,'death_observer'):
            self.death_observer=None
            try:
                try:from ._native_death import create
                except (ImportError,ValueError,SystemError):from _native_death import create
                self.death_observer=create(self.ffi,self.lib,self.refs)
            except Exception as error:
                self.death_error=type(error).__name__+': '+str(error)
                __import__('logging').getLogger(__name__).warning('Death feedback unavailable: %s',self.death_error)
        if self.health_metrics is None and self.health_error is None:
            try:
                try:from ._native_battle_metrics import create
                except (ImportError,ValueError,SystemError):from _native_battle_metrics import create
                self.health_metrics=create(self.ffi,self.lib,self.refs)
            except Exception as error:
                self.health_error=type(error).__name__
        if not self.hook.installed and self.hook_error is None:
            try:
                self.hook.install()
            except Exception as error:
                self.hook_error = type(error).__name__ + ': ' + str(error)
        self.enabled = True

    def disable(self):
        self.enabled = False
        if self._ground_egress is not None:
            self._ground_egress.close()
            self._ground_egress=None
        self.input_context = {}
        self._input_handoff.reset()
        self.close_socket()

    def uninstall(self):
        self.disable()
        if self._input_stub:
            self.hook._kernel32.VirtualFree(self._input_stub, 0, 0x8000)
            self._input_stub = 0
            self._input_setter = None
        try:
            self.hook.uninstall()
        except Exception:
            pass

    def key_states(self):
        if self._key_states is None:
            count = self.ffi.new('int *')
            self._key_states = (self.lib.SDL_GetKeyboardState(count),
                                count[0])
        return self._key_states

    def interact_down(self, scancode=0):
        """Use the game's combined keyboard/gamepad input, with a keyboard fallback.

        ClientWorld.total is written before the player's action consumes input,
        so controller presses still pass through the release/repress gate below.
        Never infer a fixed controller button: Controls owns those bindings.
        """
        cw = self._current_world()
        if cw is not None and int(cw.total) & (1 << INTERACT_INPUT):
            return True
        if not scancode:
            try:
                return bool(self.physical_input_mask() & (1 << INTERACT_INPUT))
            except (AttributeError, RuntimeError, OSError):
                scancode = 15
        states, count = self.key_states()
        return states != self.ffi.NULL and 0 < scancode < count and bool(states[scancode])

    def observe_input(self, mask, pressed, keyboard=False):
        # Sample keyboard/controller input before the native action consumes
        # its interaction bit. Repeated downs must still require a release.
        if pressed and int(mask)&0x2ff and not self._overlay_open():
            manual=bool(keyboard)
            if not manual:
                try:manual=bool(self.physical_input_mask()&int(mask))
                except (AttributeError,RuntimeError,OSError):pass
            if manual:self.sample_battle_activity(True)
        if not int(mask) & (1 << INTERACT_INPUT):return
        down=bool(pressed)
        if down and not self._input_interact_down:self._input_interact_edge=True
        self._input_interact_down=down

    def interact_edge(self, scancode=0):
        down = self.interact_down(scancode)
        edge = (getattr(self,'_input_interact_edge',False) or
                down and not self._old_interact and not getattr(self,'_input_interact_down',False))
        self._input_interact_edge=False
        self._old_interact = down
        return down, edge

    def player_action_type(self):
        """Read the action vtable used by the supported retail client build."""
        cw = self._current_world()
        if cw is None or cw.player == self.ffi.NULL:
            return ''
        action = cw.player.asClientObject.action
        if action == self.ffi.NULL:
            return ''
        table = int(self.ffi.cast('uintptr_t', action.classptr))
        return {0x6897A8: 'FlailSpinAction', 0x686228: 'DashAction',
                0x688EE8: 'DashDownAction'}.get(table, '')

    def _overlay_open(self):
        try:
            client=self.refs.WorldClient
            return client is not None and client!=self.ffi.NULL and client.overlay!=self.ffi.NULL
        except (AttributeError,TypeError):return False

    def sample_interaction_context(self, scancode=0):
        """Latch UI/action-consumed presses on every frame, including UDP skips."""
        blocked=self._overlay_open() or bool(self.player_action_type())
        down=self.interact_down(scancode)
        if not down:self._interaction_held_blocked=False
        elif (blocked or getattr(self,'_interaction_was_blocked',False) or
                getattr(self,'_interaction_pre_local',False)):self._interaction_held_blocked=True
        self._interaction_was_blocked=blocked
        self._interaction_pre_local=False
        self.sample_battle_activity(down and not self.interaction_blocked())

    def sample_battle_activity(self, interact=False):
        """Latch real gameplay input per world, independently of UDP timing."""
        cw = self._current_world()
        if cw is None:
            self._battle_activity_world = None
            self._battle_activity = False
            return
        identity = (int(self.ffi.cast('uintptr_t', cw)), int(cw.asWorld.props.seed))
        if identity != getattr(self, '_battle_activity_world', None):
            self._battle_activity_world = identity
            self._battle_activity = False
        if (not self._overlay_open() and cw.player != self.ffi.NULL and
                int(cw.player.asClientObject.asNativeObject.asWorldObject.props.hitpoints) > 0 and
                (int(cw.total) & 0x2ff or interact)):
            self._battle_activity = True

    def local_interaction_selected(self):
        """Resolve the pre-input ObjKey; local objects use native interaction."""
        cw=self._current_world()
        if cw is None or cw.player==self.ffi.NULL or cw.mySubWorld==self.ffi.NULL:return False
        key=cw.player.interactObj
        sub=cw.mySubWorld.asNativeSubWorld.asSubWorldImpl
        target=int(key.objId)
        if not target or int(key.worldId)!=int(sub.asSubWorld.worldId):return False
        vector=sub.objs.vec
        start=int(self.ffi.cast('uintptr_t',vector.start));finish=int(self.ffi.cast('uintptr_t',vector.finish))
        size=self.ffi.sizeof('struct SortedVecElement')
        if not start or finish<start or (finish-start)%size or (finish-start)//size>4096:return False
        entries=self.ffi.cast('struct SortedVecElement *',vector.start)
        left,right=0,(finish-start)//size
        while left<right:
            middle=(left+right)//2;candidate=int(entries[middle].key)
            if candidate<target:left=middle+1
            elif candidate>target:right=middle
            else:
                obj=self.ffi.cast('struct WorldObject *',entries[middle].obj)
                return obj!=self.ffi.NULL and int(obj.objId)==target and bool(obj.isLive)
        return False

    def sample_interaction_target(self):
        # Native-only objects consume interaction in full rooms. Boss panels
        # are handled by the local server and must retain their keyboard edge.
        self._interaction_pre_local=(self.input_context.get('map') in
            ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run','home')
            and self.local_interaction_selected())

    def interaction_blocked(self):
        return self._overlay_open() or bool(self.player_action_type()) or getattr(self,'_interaction_held_blocked',False)

    def input_mapping(self):
        """Read the live InputMapping singleton; never overwrite bindings."""
        if self._input_mapping_get is None:
            ffi = self.ffi
            expected = b'\x55\x89\xe5\x83\xec\x58'
            if bytes(ffi.buffer(ffi.cast('char *', INPUT_MAPPING_GET), 6)) != expected:
                raise RuntimeError('unsupported InputMapping::get signature')
            self._input_mapping_get = ffi.cast(
                'struct InputMapping *(*)(void)', INPUT_MAPPING_GET)
        return self._input_mapping_get()

    def _keyboard_scancode(self, keycode):

        keycode = int(keycode)
        if keycode & 0x40000000:
            return keycode & 0x3fffffff
        if not 0 < keycode < 0x10000000:
            return 0
        if self._scancode_from_key is None:
            ctypes = self.hook._ctypes
            function = ctypes.CDLL('SDL2.dll').SDL_GetScancodeFromKey
            function.argtypes = [ctypes.c_int32]
            function.restype = ctypes.c_int
            self._scancode_from_key = function
        return int(self._scancode_from_key(keycode))

    def physical_input_mask(self):
        """Current keyboard state through all three user-configured binds.

        Menu/emote bindings and virtual controller inputs are excluded.
        This reads SDL's state, rather than remembered keydown events, so a
        continuously held key survives a ClientWorld replacement.
        """
        states, count = self.key_states()
        if states == self.ffi.NULL:
            return 0
        mapping = self.input_mapping()
        if mapping == self.ffi.NULL:
            return 0
        total = 0
        for index in range(14):
            binding = mapping.cik[index]
            for name in ('primary', 'secondary', 'tertiary'):
                scancode = self._keyboard_scancode(getattr(binding, name))
                if 0 < scancode < count and bool(states[scancode]):
                    total |= 1 << index
                    break
        return total

    def keyboard_input_mask(self,keycode):
        """Match an original gameplay binding even when a death overlay consumes it."""
        game=self.refs.GameClient
        if game is not None and game!=self.ffi.NULL:
            stage=game.asUIElementContainer.asUIElement.stage
            if stage!=self.ffi.NULL and stage.textInputTarget!=self.ffi.NULL and stage.textInputTarget.show:return 0
        mapping=self.input_mapping()
        if mapping==self.ffi.NULL:return 0
        mask=0
        for index in range(10):
            if not (1<<index)&0x2ff:continue
            binding=mapping.cik[index]
            if any(int(getattr(binding,name))==int(keycode) for name in ('primary','secondary','tertiary')):mask|=1<<index
        return mask

    def _native_set_input(self, mask):
        "Use WorldClient::setInput's total-only native/protobuf path."
        if self._input_setter is None:
            ffi = self.ffi

            expected = b'\x55\x89\xe5\x83\xec\x78'
            if bytes(ffi.buffer(ffi.cast('char *', WORLD_CLIENT_SET_INPUT), 6)) != expected:
                raise RuntimeError('unsupported WorldClient::setInput signature')
            code = (b'\x55\x89\xe5\xff\x75\x0c\x8b\x4d\x08\xb8' +
                    struct.pack('<I', WORLD_CLIENT_SET_INPUT) +
                    b'\xff\xd0\xc9\xc3')
            allocation = self.hook._kernel32.VirtualAlloc(
                None, len(code), 0x3000, 0x40)
            if not allocation:
                raise MemoryError('VirtualAlloc failed for input handoff')
            self._input_stub = int(allocation)
            self.hook._ctypes.memmove(allocation, code, len(code))
            self.hook._kernel32.FlushInstructionCache(
                self.hook._kernel32.GetCurrentProcess(), allocation, len(code))
            self._input_setter = ffi.cast(
                'void (*)(struct WorldClient *, unsigned int)', allocation)
        self._input_setter(self.refs.WorldClient, int(mask))

    def reapply_room_input(self):
        # USER: every door in the Cathedral run froze telemetry. The stall sits
        # inside this native reapply after a room load, so the run never performs
        # it: held keys are re-pressed by the player instead.
        if str((self.input_context or {}).get('map', '')) == 'sc_run':
            self._sc_skipped = getattr(self, '_sc_skipped', 0)+1
            self._input_handoff.reset()
            return False
        """Restore held native input once after an EGY room becomes ready."""
        marker = str(os.environ.get('HARDLAB_LIVE_SESSION', '')).strip().lower()
        if not self.enabled or marker not in ('1', 'yes', 'true', 'on'):
            self._input_handoff.reset()
            return False
        cw = self._current_world()
        seed = int(cw.asWorld.props.seed) if cw is not None else None
        if not self._input_handoff.due(self.input_context, seed):
            return False
        if (cw is None or cw.player == self.ffi.NULL or
                self.refs.WorldClient.overlay != self.ffi.NULL):
            return False
        total = self.physical_input_mask()

        if total & (1 << INTERACT_INPUT):
            self._old_interact = True
            self._interaction_held_blocked = True
        mask = total & ~(1 << INTERACT_INPUT)
        before = int(cw.total)
        self._native_set_input(mask)
        if int(cw.total) != mask:
            return False
        self._input_handoff.committed()
        self.input_handoffs += 1
        self.last_input_handoff = dict(at=time.time(),
            room_token=self.input_context['room_token'], seed=seed,
            physical=total, before=before, applied=mask,
            inputBuffer=int(cw.inputBuffer))
        return True

    def character_holds(self, hold_seconds=2.0, shortcuts=None):
        """Return long-press restart/class-switch events.

        Hold R=2s to restart as the current class.  Hold 7/8/9/0 for
        WF/DW/FAB/IC respectively to switch class.  Both the top-row number
        keys and the numeric keypad are accepted.  Every shortcut fires once
        after ``hold_seconds`` and re-arms only after release.
        """
        states, count = self.key_states()
        if states == self.ffi.NULL:
            return []
        now = time.time()
        events = []
        if shortcuts is None:
            shortcuts = (
            (21, 'R', None, 'restart'),
            (36, '7', 1, 'switch_character'),
            (37, '8', 2, 'switch_character'),
            (38, '9', 5, 'switch_character'),
            (39, '0', 3, 'switch_character'),

            (95, 'Num7', 1, 'switch_character'),
            (96, 'Num8', 2, 'switch_character'),
            (97, 'Num9', 5, 'switch_character'),
            (98, 'Num0', 3, 'switch_character'),
            )
        for scancode, label, char_type, kind in shortcuts:
            down = scancode < count and bool(states[scancode])
            if not down:
                self._hold_since.pop(scancode, None)
                self._hold_fired.discard(scancode)
                continue
            started = self._hold_since.setdefault(scancode, now)
            if (now - started >= float(hold_seconds)
                    and scancode not in self._hold_fired):
                self._hold_fired.add(scancode)
                event = {
                    'kind': kind,
                    'key': label,
                    'heldSeconds': round(now - started, 3),
                }
                if kind == 'switch_character':
                    event['type'] = int(char_type)
                events.append(event)
        return events

    def _current_world(self):
        world_client = self.refs.WorldClient
        if world_client == self.ffi.NULL:
            return None
        cw = world_client.clientWorld
        if cw == self.ffi.NULL:
            return None
        return cw

    def _view_player_bounds(self, cw):
        """Return the player rect the client keeps for its camera."""
        view = None
        try:
            world_client = self.refs.WorldClient
            if world_client != self.ffi.NULL:
                view = world_client.worldView
        except Exception:
            view = None
        if view is None or view == self.ffi.NULL:
            return None
        try:
            bounds = view.playerBounds
            x = int(bounds.x)
            y = int(bounds.y)
            w = int(bounds.w)
            h = int(bounds.h)
            world = view.worldBounds
            wx = int(world.x)
            wy = int(world.y)
            ww = int(world.w)
            wh = int(world.h)
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None

        if (x, y, w, h) == (wx, wy, ww, wh):
            return None
        return x, y, w, h

    def place_player(self, xmp, ymp):
        'Move the local player and report success.'
        cw = self._current_world()
        if cw is None or cw.player == self.ffi.NULL:
            return False
        try:
            player = cw.player
            native = player.asClientObject.asNativeObject
            world = native.asWorldObject
            props = world.props
            props.xmp = int(xmp)
            props.ymp = int(ymp)
            world.lastXmp = int(xmp)
            world.lastYmp = int(ymp)

            native.attached.worldId = 0
            native.attached.objId = 0
            native.xBlocked = False
            native.yBlocked = False
            world.dirty = True
            try:
                native.xVel = 0
                native.yVel = 0
            except Exception:
                pass
            return True
        except Exception as error:
            self._log('place_player failed: {0}'.format(error))
            return False

    def begin_player_epoch(self):
        self.player_epoch=getattr(self,'player_epoch',0)+1
        self.player_protection=None

    def player_document(self):
        cw = self._current_world()
        if cw is None:
            return None
        document = {'room_seed': int(cw.asWorld.props.seed),'player_epoch':getattr(self,'player_epoch',0)}
        try:
            sub = cw.mySubWorld
            if sub != self.ffi.NULL:
                document['worldid'] = int(
                    sub.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId)
        except Exception:
            pass

        rect = self._view_player_bounds(cw)
        if rect is not None:
            document.update({
                'xmp': rect[0], 'ymp': rect[1],
                'wmp': rect[2], 'hmp': rect[3],
            })

        try:
            player = cw.player
            if player != self.ffi.NULL:
                world = player.asClientObject.asNativeObject.asWorldObject
                props = world.props
                document['objid'] = int(world.objId)
                try:
                    document['vid'] = self.util.getstr(props.vid)
                except Exception:
                    pass
                document['lastXmp'] = int(world.lastXmp)
                document['lastYmp'] = int(world.lastYmp)
                document['propsXmp'] = int(props.xmp)
                document['propsYmp'] = int(props.ymp)
                document['xmp'] = int(props.xmp)
                document['ymp'] = int(props.ymp)
                document['wmp'] = int(props.wmp)
                document['hmp'] = int(props.hmp)
                document['positionSource'] = 'player'
                document['hp'] = int(props.hitpoints)
                document['maxhp'] = int(props.maxhitpoints)
                document['facing'] = int(props.facing)
                document['pose'] = int(getattr(props, 'pose', 4))
                if document.get('vid') == 'assassin':
                    document['action_type'] = self.player_action_type()
                key = (document.get('worldid'), document.get('vid'))
                if key != self._appearance_key:
                    self._appearance_key = key
                    self._appearance_next = 0.0
                    self._appearance_cache = {}
                if time.monotonic() >= self._appearance_next:
                    self._appearance_next = time.monotonic() + .2
                    self._appearance_cache = self._drawmods_document(props)
                document['drawmods'] = self._appearance_cache
                document['alive'] = bool(getattr(props, 'draw', True)) and int(props.hitpoints) > 0
                character = self.ffi.cast('struct PlayerCharacter *', player)
                document['ammo'] = int(character.ammo)
                document['maxammo'] = int(character.maxAmmo.base + character.maxAmmo.bonus)

                document['maxAirJumps'] = int(character.maxAirJumps)
                document['numAirJumps'] = int(character.numAirJumps)
                document['maxAirDashes'] = int(character.maxAirDashes)
                document['numAirDashes'] = int(character.numAirDashes)
                document['knockbackResist'] = int(character.knockbackResist)
                document['lastDamageT'] = int(world.lastDamageT)
                native = player.asClientObject.asNativeObject
                document['xVel'] = int(native.xVel)
                document['yVel'] = int(native.yVel)
                document['yBlocked'] = bool(native.yBlocked)
                document['attachedWorldId'] = int(native.attached.worldId)
                document['attachedObjId'] = int(native.attached.objId)
        except Exception:
            pass
        if not document:
            return None
        document.setdefault('objid', 0)
        document.setdefault('hp', 0)
        try:
            view = self.refs.WorldClient.worldView
            if view != self.ffi.NULL:
                wb = view.worldBounds
                pb = view.playerBounds
                document['worldBounds'] = [int(wb.x), int(wb.y),
                                           int(wb.w), int(wb.h)]
                document['playerBounds'] = [int(pb.x), int(pb.y),
                                            int(pb.w), int(pb.h)]
                document['targetPos'] = [int(view.targetPos.x),
                                         int(view.targetPos.y)]

                if 'xmp' not in document:
                    document['wmp'] = 30 * 256
                    document['hmp'] = 70 * 256
                    document['xmp'] = int(view.targetPos.x) * 256 - 15 * 256
                    document['ymp'] = int(view.targetPos.y) * 256 - 35 * 256
                    document['positionSource'] = 'targetPos'
        except Exception:
            pass
        document.setdefault('maxhp', 0)
        document.setdefault('facing', 1)
        document.setdefault('alive', True)
        terrain=self.terrain_document(cw)
        if terrain:document['terrainObjects']=terrain
        document['interaction_blocked']=bool(self.interaction_blocked())
        if self.health_metrics is not None:
            metrics=self.health_metrics.document()
            if metrics:document['battle_metrics']=metrics
        observer=getattr(self,'death_observer',None)
        if observer is not None:document=observer.document(cw,document)
        return document

    def _drawmods_document(self, props):
        """Read native equipment appearance without editing gameplay state."""
        try:
            pointer = getattr(props, 'drawmods', self.ffi.NULL)
            if pointer == self.ffi.NULL:
                return {}
            if self._appearance_decoder is None:
                try:
                    import _hardlab_delta as decoder
                except ImportError:
                    from plugins import _hardlab_delta as decoder
                table = decoder.load_layouts(os.path.join(os.path.dirname(__file__), 'runtime_pb_layouts.json'))
                self._appearance_decoder = (decoder, table, decoder.CffiReader(self.ffi, self.util))
            decoder, table, reader = self._appearance_decoder
            limits = dict(decoder.DEFAULT_LIMITS, maxRepeated=32, maxNodes=128, maxDepth=6)
            result = decoder.serialize_message(reader, table, 'DrawMods',
                int(self.ffi.cast('uintptr_t', pointer)), limits, decoder.new_stats())
            return result or {}
        except Exception:
            return {}

    def terrain_document(self,cw):
        'Read two destructible supports at 5 Hz, without disk I/O.'
        try:
            sub=cw.serverSubWorld
            if sub==self.ffi.NULL:return []
            identity=int(self.ffi.cast('uintptr_t',sub))
            if identity!=self._terrain_world:
                self._terrain_world=identity;self._terrain_seen=set()
                self._terrain_snapshot=[];self._terrain_next=0
            now=time.time()
            if now<self._terrain_next:return self._terrain_snapshot
            self._terrain_next=now+.2
            found={}
            for ptr in self.util.worldobjects(sub):
                world=self.ffi.cast('struct WorldObject *',ptr)
                objid=int(world.objId)
                if objid not in (60,61):continue
                vid=self.util.getstr(world.props.vid)
                if vid not in ('hulk.standingbarrel','hulk.leaningbarrel'):continue
                found[objid]=dict(objid=objid,present=True,hp=int(world.props.hitpoints))
                self._terrain_seen.add(objid)
            rows=[found[i] if i in found else dict(objid=i,present=False)
                  for i in sorted(self._terrain_seen)]
            self._terrain_snapshot=rows
            return rows
        except Exception:
            return []

    def send(self, payload):
        sink=getattr(self,'feedback_sink',None)
        if sink is not None and sink(payload):return
        if self.socket is None:
            self._socket_missing = getattr(self, '_socket_missing', 0) + 1
            return
        try:
            document = json.dumps(payload,ensure_ascii=True,separators=(',',':'),allow_nan=False)
            self.socket.sendto(document.encode('utf-8'),
                               self._udp_target or (self.host, self.port))
            self.sent += 1
            self._drain_ack()
        except Exception as error:
            self._send_error = type(error).__name__ + ': ' + str(error)[:160]
            self._network_reject(self._send_error)

    def top_up_reserve(self):
        """Keep the client's world-time credit pinned at its retail cap.

        ``ClientWorld::addTimePasses`` only grows ``timeReserve_`` while
        ``targetBuffer_`` still tracks a fresh frame time.  The world-loading
        burst pushes the counter to its cap, after which the reserve can only
        shrink: the fps HUD then shows r:880-900 instead of the retail 1000
        even though the server keeps real time.

        The local server is the only time source here, so a local session must
        always show the same full credit retail does.  Clearing
        ``targetBuffer_`` re-opens the client's own refill path, and the value
        itself is raised back to the 1000 ms cap whenever it dips, which keeps
        ``r:`` stable instead of oscillating (measured 969-1000 before).
        """
        cw = self._current_world()
        if cw is None:
            return None
        try:
            reserve = int(cw.timeReserve)
            target = int(cw.targetBuffer)

            if 0 <= reserve < self.reserve_cap:
                if target != 0:
                    cw.targetBuffer = 0
                cw.timeReserve = self.reserve_cap
            return reserve, target
        except Exception:
            return None

    def native_diagnostics(self):
        'Read-only native scene state for local integration verification.'
        cw = self._current_world()
        if cw is None:
            return {'world': None}
        ffi = self.ffi
        result = {'at': time.time(), 'player': self.player_document()}

        try:
            result['timeReserve'] = int(cw.timeReserve)
            result['targetBuffer'] = int(cw.targetBuffer)
            result['inputBuffer'] = int(cw.inputBuffer)
            result['inputTotal'] = int(cw.total)
            result['inputHandoff'] = dict(count=self.input_handoffs,
                context=self.input_context, last=self.last_input_handoff)
        except Exception:
            pass
        try:
            view = self.refs.WorldClient.worldView
            if view != ffi.NULL:
                tm = view.tileMap
                result['view'] = {'recalculate':bool(view.recalculateWorldBounds),
                    'rebuildTiles':bool(view.rebuildTiles),'show':bool(view.asUIElement.show),
                    'size':[int(view.asUIElement.w),int(view.asUIElement.h)]}
                result['drawTiles'] = bool(self.refs.drawTiles[0])
                if tm != ffi.NULL:
                    count = int(tm.tw) * int(tm.th)
                    result['tileMap'] = {'tw': int(tm.tw), 'th': int(tm.th),
                        'has60': bool(tm.has60), 'has70': bool(tm.has70),
                        'external': int(tm.externalSetId), 'internal': int(tm.internalSetId),
                        'transition': int(tm.transitionSetId),
                        'bounds': [int(tm.bounds.x),int(tm.bounds.y),int(tm.bounds.w),int(tm.bounds.h)]}
                    if 0 < count <= 100000 and tm.tileTypes != ffi.NULL:
                        counts = {}

                        tile_types = ffi.cast('unsigned char *', tm.tileTypes)
                        for index in range(count):
                            key = str(int(tile_types[index]))
                            counts[key] = counts.get(key, 0) + 1
                        result['tileMap']['tileTypes'] = counts
                for name in ('tiles60', 'tiles70'):
                    tiles = getattr(view, name)
                    if tiles != ffi.NULL:
                        count = int(tiles.tdw) * int(tiles.tdh)
                        result[name] = {'z':int(tiles.z),'tw':int(tiles.tw),
                            'th':int(tiles.th),'tdw':int(tiles.tdw),'tdh':int(tiles.tdh)}
                        if 0 < count <= 100000 and tiles.tileData != ffi.NULL:
                            result[name]['nonzeroTiles'] = sum(
                                1 for index in range(count) if tiles.tileData[index])
                            result[name]['missingTiles'] = sum(
                                1 for index in range(count) if tiles.tileData[index] == 65534)
                            result[name]['renderedTiles'] = sum(
                                1 for index in range(count) if tiles.tileData[index] < 65024)
                            values = {}
                            for index in range(count):
                                value = str(int(tiles.tileData[index]))
                                values[value] = values.get(value, 0) + 1
                            result[name]['tileValues'] = values
            actors = []
            sub = cw.serverSubWorld
            if sub != ffi.NULL:
                for obj in self.util.worldobjects(sub):
                    world = ffi.cast('struct WorldObject *', obj)
                    p = world.props
                    vid = self.util.getstr(p.vid)
                    actors.append({'objid':int(world.objId),'vid':vid,
                        'rect':[int(p.xmp),int(p.ymp),int(p.wmp),int(p.hmp)],
                        'pose':int(p.pose),'hp':int(p.hitpoints),
                        'terrain':int(p.terraintype),'draw':bool(p.draw),
                        'internal':bool(p.isinternal)})
                    actors[-1]['boundsworld']=bool(p.boundsworld)
                    actors[-1]['facing']=int(p.facing)
                    actors[-1]['damage']=int(p.damage)
                    if int(world.objId) in (1,2,3,4):
                        actors[-1]['z']=int(p.z)
                        actors[-1]['decorate']=bool(p.decorate)
                        actors[-1]['hasbits']=[int(p._has_bits[0]),int(p._has_bits[1])]
            result['actors'] = actors
        except Exception as error:
            result['error'] = type(error).__name__ + ': ' + str(error)
        return result

    def begin_native_frame(self):
        """Once per native frame; send/drain NEVER reset these budgets."""
        self._network_previous_frame = self._network_frame
        self._network_frame_id += 1
        self._network_frame = dict(frame_id=self._network_frame_id, packets=0, bytes=0,
            elapsed_s=0., overrun_s=0., rejected=0, drain_calls=0, restore_calls=0,
            budget_stop=None, budgets_are_SIM=True, callbacks_preempted=False)

    def network_diagnostics(self):
        return dict(frame=dict(self._network_frame),
            previous_frame=dict(self._network_previous_frame) if self._network_previous_frame else None,
            totals=dict(self._network_totals), last_error=self._network_last_error,
            limits=dict(recv_bytes=ACK_RECV_BYTES, datagram_bytes=ACK_MAX_DATAGRAM,
                packets=ACK_FRAME_PACKETS, bytes=ACK_FRAME_BYTES, seconds_SIM=ACK_FRAME_SECONDS))

    def _network_elapsed(self, started):
        spent = max(0., self._network_clock()-started)
        self._network_frame['elapsed_s'] += spent
        self._network_frame['overrun_s'] = max(0., self._network_frame['elapsed_s']-ACK_FRAME_SECONDS)

    def _network_reject(self, reason):
        self._network_frame['rejected'] += 1
        self._network_totals['rejected'] += 1
        self._network_last_error = str(reason)[:256]

    def _receive_document(self, document, raw):
        if 'ack' in document:
            self.ack = document['ack']
            self.last_ack_time = time.time()
        if 'ground_restore' in document:
            if self._ground_restore is not None and self._ground_restore.enqueue(raw):
                self._network_totals['restore_received'] += 1
            return
        if 'ack' not in document:
            return

        self.player_protection=document.get('player_protection') if isinstance(document.get('player_protection'),dict) else None
        self.input_context = ({key: document[key] for key in
            ('map', 'room_token', 'room_seed', 'room_ready', 'room_handoff', 'inventory_request')
            if key in document} if document.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run','home') else {})
        if self._ground_egress is not None or isinstance(document.get('egress_request'),dict):
            if self._ground_egress is None:
                try:from ._full_run_egress_transport import EgressTransport
                except (ImportError,ValueError,SystemError):from _full_run_egress_transport import EgressTransport
                self._ground_egress=EgressTransport(self)
            self._ground_egress.absorb_ack(document)
        command = document.get('player_teleport')
        if command:
            cw = self._current_world()
            player = self.player_document()
            if cw is not None and player:
                result = self._room_teleport.apply(command, self.input_context,
                    int(cw.asWorld.props.seed), player['objid'], player['worldid'], self.place_player)
                if result and result not in self._command_results:
                    self._command_results.append(result)

    def receive_native_ack(self,document):
        raw=json.dumps(document,separators=(',',':'),allow_nan=False).encode('ascii')
        if len(raw)>ACK_MAX_DATAGRAM:raise ValueError('Native acknowledgement is too large')
        self._receive_document(_ack_json(raw),raw)

    def _drain_ack(self):
        if getattr(self,'feedback_isolated',False):
            return
        if self.socket is None:
            self._socket_missing = getattr(self, '_socket_missing', 0) + 1
            return
        frame = self._network_frame
        frame['drain_calls'] += 1
        started = self._network_clock()
        try:
            while True:
                if frame['packets'] >= ACK_FRAME_PACKETS:
                    frame['budget_stop'] = 'packets'; break

                if frame['bytes']+ACK_RECV_BYTES > ACK_FRAME_BYTES:
                    frame['budget_stop'] = 'bytes'; break
                if frame['elapsed_s']+self._network_clock()-started >= ACK_FRAME_SECONDS:
                    frame['budget_stop'] = 'time'; break
                try:
                    data, address = self.socket.recvfrom(ACK_RECV_BYTES)
                except (BlockingIOError, InterruptedError):
                    break
                frame['packets'] += 1; frame['bytes'] += len(data)
                self._network_totals['packets'] += 1; self._network_totals['bytes'] += len(data)
                try:
                    if self._udp_target is not None and address != self._udp_target:
                        raise ValueError('ACK from unexpected endpoint')
                    if not 0 < len(data) <= ACK_MAX_DATAGRAM:
                        raise ValueError('ACK datagram exceeds64000 or empty')
                    self._receive_document(_ack_json(data), data)
                except Exception as error:
                    self._network_reject(type(error).__name__+': '+str(error))
        except OSError as error:
            self._network_reject(type(error).__name__+': '+str(error))
        finally:
            self._network_elapsed(started)

    def attach_ground_restore(self, endpoint):
        """Explicit opt-in; no provider, hooks or restore success are fabricated."""
        try:from ._full_run_ground_restore_transport import ClientRestoreTransport
        except (ImportError,ValueError,SystemError):from _full_run_ground_restore_transport import ClientRestoreTransport
        if not isinstance(endpoint, ClientRestoreTransport):
            raise TypeError('explicit ClientRestoreTransport required')
        if self._ground_restore is not None and self._ground_restore is not endpoint:
            raise ValueError('detach and retain previous restore endpoint before replacement')
        self._ground_restore = endpoint
        return endpoint

    def detach_ground_restore(self, endpoint):
        'Local battle simulator runtime.'
        if endpoint is not self._ground_restore or endpoint is None:
            raise ValueError('exact attached restore endpoint required')
        self._ground_restore = None
        return endpoint

    def ground_restore_attached(self):
        return self._ground_restore is not None

    def send_ground_restore(self, now=None):
        endpoint = self._ground_restore
        if endpoint is None or self.socket is None:
            return
        if self._ground_restore_sent_frame == self._network_frame_id:
            return
        if self._network_frame['elapsed_s'] >= ACK_FRAME_SECONDS:
            self._network_frame['budget_stop'] = 'time'; return
        self._ground_restore_sent_frame = self._network_frame_id
        self._network_frame['restore_calls'] += 1
        started = self._network_clock()
        try:
            now = time.time() if now is None else now
            if type(now) not in (int,float) or not math.isfinite(now):
                raise ValueError('invalid restore send time')

            raw = endpoint.frame(dict(seq=self.seq+1, t=round(now,4)), now)
            if raw is not None:
                if not isinstance(raw,bytes) or not 0 < len(raw) <= 60000:
                    raise ValueError('restore wire must be bounded ASCII bytes')
                raw.decode('ascii')
                sink=getattr(self,'feedback_sink',None)
                if sink is None or not sink(json.loads(raw.decode('ascii'))):
                    self.socket.sendto(raw, self._udp_target or (self.host,self.port))
                self.seq += 1; self.sent += 1
                self._network_totals['restore_sent'] += 1
        except Exception as error:
            self._network_reject(type(error).__name__+': '+str(error))
        finally:
            self._network_elapsed(started)
        self._drain_ack()

    def link_ok(self, max_age=2.0):
        return (self.last_ack_time > 0.0
                and time.time() - self.last_ack_time <= max_age)

    def send_ground_egress(self,now=None):
        if self._ground_egress is None:return
        for document in self._ground_egress.packets(now):self.send(document)

    def build_datagram(self, interact_down, interact_edge, hold_events=None):
        events = list(self.hook.drain())
        events.extend(self._command_results)
        self._command_results = []
        if hold_events:
            events.extend(hold_events)
        document = {
            'seq': self.seq + 1,
            't': round(time.time(), 4),
            'player': self.player_document() or {},
            'keys': {
                'interact': bool(interact_down),
                'interact_edge': bool(interact_edge),
                'interaction_blocked': bool(self.interaction_blocked()),
                'gameplay': bool(getattr(self, '_battle_activity', False)),
            },
            'events': events,
        }
        if self.input_context.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run'):
            cw = self._current_world()
            document['player']['inventory_protocol']='full-run-inventory-v1'
            control=self.input_context.get('inventory_request')
            if control:
                try:
                    if self._inventory_refresh is None:
                        try:from ._full_run_inventory import InventoryRefresh
                        except (ImportError,ValueError,SystemError):from _full_run_inventory import InventoryRefresh
                        self._inventory_refresh=InventoryRefresh(self.ffi,self.util)
                    inventory=self._inventory_refresh.document(cw,self.input_context,control)
                    if inventory is not None:document['player']['inventory']=inventory
                except Exception as error:
                    document['player']['inventory_error']=type(error).__name__+': '+str(error)
        if self.input_context.get('map') in ('egy_run','eub_run','etl_run','est_run','efb_run','eff_run','sc_run','home'):
            cw = self._current_world()
            document['room_context'] = dict(self.input_context)
            document['room_context']['room_seed'] = int(cw.asWorld.props.seed) if cw is not None else None
        return document
