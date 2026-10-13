'Bounded read-only joint inventory/Loot/Chest snapshots for Win32 PyPy3.5.'
import copy
import json
import math
from pathlib import Path
import time
try:
    from ._full_run_inventory import (CffiInventoryAdapter, InventoryReader,
        ReadableMemory, GuardedReader, ReadFailure, BAD_STATS, REFRESH_SCOPE, plain)
    from ._hardlab_delta import serialize_message, new_stats, DEFAULT_LIMITS, _has_bit
except (ImportError, ValueError, SystemError):
    from _full_run_inventory import (CffiInventoryAdapter, InventoryReader,
        ReadableMemory, GuardedReader, ReadFailure, BAD_STATS, REFRESH_SCOPE, plain)
    from _hardlab_delta import serialize_message, new_stats, DEFAULT_LIMITS, _has_bit

SCHEMA = 'full-run-ground-joint-v1'
GROUND_SCHEMA = 'full-run-ground-v1'
ABI = 'win32-gcc48-ground-v1'
SI_TYPEINFO_VTABLE = 6877160
CLASS_TYPEINFO_VTABLE = 6876904
EXPECTED_VTABLES = {'Loot': 0x68c268, 'Chest': 0x68c608}
REACTION_CONVERTUC_CODE = ((0x5548d1, b'\xf6\xc4\x02'),
    (0x5548dc, b'\x8a\x43\x22'), (0x5548df, b'\xc7\x04\x24\x0a\x00\x00\x00'))
NATIVE_FIELDS = ('offsetDT', 'xVel', 'yVel', 'isPrivate', 'hasGravity',
    'xBlocked', 'yBlocked', 'crouching', 'running', 'attXVel', 'moveXVel',
    'moveYVel', 'walkAccel', 'instantWalkDecel', 'airAccel', 'gravityAccel',
    'redamageTime', 'createSoundGlobal')
BOOL_FIELDS = frozenset(('isPrivate', 'hasGravity', 'xBlocked', 'yBlocked',
    'crouching', 'running', 'instantWalkDecel', 'createSoundGlobal'))

def ascii_wire_bytes(value):
    """Canonical ASCII JSON size, including Unicode escape expansion."""
    return len(json.dumps(value, ensure_ascii=True, separators=(',', ':'),
        allow_nan=False).encode('ascii'))

class JointMemoryGuard(ReadableMemory):
    """Nested InventoryReader.begin cannot restart this joint read's budget."""
    def __init__(self, seconds):
        self.active = False
        super(JointMemoryGuard, self).__init__(seconds)
    def reset(self):
        if not self.active:
            super(JointMemoryGuard, self).reset()
    def begin(self):
        self.active = False
        self.reset()
        self.active = True
    def finish(self):
        self.active = False

class CffiGroundAdapter(object):
    """Actual Win32 structs + strict complete PB decoding, no guessed schema."""
    def __init__(self, ffi, util, layouts_path=None, seconds=.05):
        self.ffi = ffi
        if ffi.sizeof('void *') != 4:
            raise ReadFailure('unsupported ground ABI: requires32bit pointers')
        expected = (('SortedVecElement', 'key', 0), ('SortedVecElement', 'obj', 4),
            ('World', 'nextObjId', 0xa8), ('WorldObject', 'objId', 8),
            ('Loot', 'itemProps', 0x13c), ('Loot', 'itemDesc', 0x160),
            ('Loot', 'untilAutoApply', 0x164), ('Chest', 'items', 0x13c),
            ('Chest', 'angles', 0x14c), ('Chest', 'pos', 0x158),
            ('Chest', 'untilThrow', 0x15c), ('RepeatedPtrField', 'elements', 0),
            ('RepeatedPtrField', 'current_size', 4), ('RepeatedPtrField', 'allocated_size', 8),
            ('RepeatedPtrField', 'total_size', 12), ('NativeObject', 'offsetDT', 0xd8),
            ('NativeObject', 'xVel', 0xdc), ('NativeObject', 'yVel', 0xe0),
            ('NativeObject', 'damaged', 0x108), ('NativeObject', 'attached', 0x12c),
            ('ClientObject', 'subWorld', 0x134), ('ClientObject', 'action', 0x138))
        for kind, field, offset in expected:
            if ffi.offsetof('struct ' + kind, field) != offset:
                raise ReadFailure('unsupported ground ABI offset: ' + kind + '.' + field)
        for kind, size in (('SortedVecElement', 8), ('WorldObject', 0xd8),
                ('NativeObject', 0x134), ('ClientObject', 0x13c), ('Loot', 0x168), ('Chest', 0x160)):
            if ffi.sizeof('struct ' + kind) != size:
                raise ReadFailure('unsupported ground ABI size: ' + kind)
        inventory_adapter = CffiInventoryAdapter(ffi, util, layouts_path, seconds)
        self.guard = JointMemoryGuard(seconds)
        self.reader = GuardedReader(ffi, util, self.guard)
        inventory_adapter.guard = self.guard
        inventory_adapter.reader = self.reader
        self.inventory_reader = InventoryReader(adapter=inventory_adapter, max_json_bytes=48000,
            max_total_boosts=2048)
        self.table = inventory_adapter.table
        self.cffi_missing_fields = []
        self._validate_layouts(('Properties', 'ItemProperties'))
        self.abi = dict(name=ABI, pointer_size=4, sorted_element_size=8,
            fields={kind + '.' + field: offset for kind, field, offset in expected},
            inventory=copy.deepcopy(inventory_adapter.abi),
            loot_vtable=EXPECTED_VTABLES['Loot'], chest_vtable=EXPECTED_VTABLES['Chest'],
            si_typeinfo_vtable=SI_TYPEINFO_VTABLE, class_typeinfo_vtable=CLASS_TYPEINFO_VTABLE,
            cffi_missing_fields=copy.deepcopy(self.cffi_missing_fields))

    def _field_offset(self, kind, layout, field):
        try:
            return self.ffi.offsetof('struct ' + kind, field['name'].rstrip('_'))
        except KeyError:

            if not (kind == 'Reaction' and field['name'] == 'convertuc_'
                    and field['offset'] == 34 and field.get('hasBit') == 9
                    and field['kind'] == 'bool' and layout['size'] == 44
                    and layout.get('hasBitsOffset') == 40
                    and self.ffi.offsetof('struct Reaction', 'openfactionregistrar') == 33
                    and self.ffi.offsetof('struct Reaction', '_cached_size') == 36
                    and self.ffi.offsetof('struct Reaction', '_has_bits') == 40):
                raise ReadFailure('unaudited missing CFFI field: ' + kind + '.' + field['name'])
            for address, expected in REACTION_CONVERTUC_CODE:
                actual = bytes(bytearray(self.reader.u8(address+i) for i in range(len(expected))))
                if actual != expected:
                    raise ReadFailure('Reaction.convertuc retail serializer signature mismatch')
            self.cffi_missing_fields.append(dict(type='Reaction', field='convertuc', offset=34,
                hasBit=9, source='D DWARF + code0x5548d1/0x5548dc/0x5548df; bool in CFFI padding'))
            return 34

    def _validate_layouts(self, roots):
        pending = list(roots)
        seen = set()
        while pending:
            kind = pending.pop()
            if kind in seen:
                continue
            seen.add(kind)
            layout = self.table.type(kind)
            if layout is None or layout['size'] != self.ffi.sizeof('struct ' + kind):
                raise ReadFailure(kind + ' DWARF/CFFI size mismatch')
            bit_offset = layout.get('hasBitsOffset')
            if bit_offset is not None and bit_offset != self.ffi.offsetof('struct ' + kind, '_has_bits'):
                raise ReadFailure(kind + ' DWARF/CFFI presence mismatch')
            for field in layout['fields']:
                if field['offset'] != self._field_offset(kind, layout, field):
                    raise ReadFailure(kind + ' DWARF/CFFI field offset mismatch')
                if field['kind'] in ('messagePointer', 'messageInline', 'repeatedMessage'):
                    pending.append(field['item'])

    def address(self, value):
        return int(self.ffi.cast('uintptr_t', value))
    def protect(self, value, kind):
        self.guard(self.address(value), self.ffi.sizeof('struct ' + kind))
    def begin(self, cw, context):
        self.guard.begin()
        self.stats = new_stats()
        self.pass_boosts = 0
        self.protect(cw, 'ClientWorld')
    def finish(self):
        self.guard.finish()

    def _cstr(self, address, limit=128):
        result = bytearray()
        for index in range(limit):
            value = self.reader.u8(address + index)
            if not value:
                return bytes(result).decode('ascii')
            result.append(value)
        raise ReadFailure('unterminated bounded RTTI name')

    def rtti(self, pointer):
        vtable = self.reader.pointer(pointer)
        info = self.reader.pointer(vtable - 4)
        chain = []
        seen = set()
        for unused in range(12):
            if not info or info in seen:
                raise ReadFailure('native RTTI null/cycle')
            seen.add(info)
            kind = self.reader.pointer(info)
            raw = self._cstr(self.reader.pointer(info + 4))

            name = raw.lstrip('0123456789') if raw[:1].isdigit() else raw
            chain.append(name)
            if kind == CLASS_TYPEINFO_VTABLE:
                if chain[-1] != 'WorldObject':
                    raise ReadFailure('local object RTTI does not derive from WorldObject')
                if any(n in ('Loot', 'Chest') for n in chain[1:]):
                    raise ReadFailure('unsupported derived ground container: ' + chain[0])
                if chain[0] in EXPECTED_VTABLES and vtable != EXPECTED_VTABLES[chain[0]]:
                    raise ReadFailure('ground vtable mismatch')
                return chain[0], chain, vtable
            if kind != SI_TYPEINFO_VTABLE:
                raise ReadFailure('unsupported native RTTI inheritance kind')
            info = self.reader.pointer(info + 8)
        raise ReadFailure('native RTTI inheritance depth exceeds bound')

    @staticmethod
    def _deque_empty(deque):
        return int(deque.start.node) == int(deque.finish.node) and int(deque.start.cur) == int(deque.finish.cur)

    def state(self, cw, context, max_local_objects):
        self.protect(cw, 'ClientWorld')
        if cw.mySubWorld == self.ffi.NULL or cw.serverSubWorld == self.ffi.NULL:
            raise ReadFailure('missing native local/server subworld')
        self.protect(cw.mySubWorld, 'ClientSubWorld')
        self.protect(cw.serverSubWorld, 'ForeignSubWorld')
        sub = cw.mySubWorld.asNativeSubWorld.asSubWorldImpl
        if self.address(cw.mySubWorld.world) != self.address(cw):
            raise ReadFailure('local subworld belongs to a different ClientWorld')
        if not self._deque_empty(sub.toAdd) or not self._deque_empty(sub.toRemove):
            raise ReadFailure('local object add/remove queues are not drained')
        character, scope = self.inventory_reader.adapter.world(cw, context)
        self.protect(character, 'PlayerCharacter')
        scope.update(self.inventory_reader.adapter.identity(character))
        vector = sub.objs.vec
        start, finish, capacity = (self.address(v) for v in (vector.start, vector.finish, vector.endOfStorage))
        if not start:
            if finish or capacity:
                raise ReadFailure('invalid empty local map vector')
            count = 0
        else:
            if finish < start or capacity < finish or (finish-start) % 8 or (capacity-start) % 8:
                raise ReadFailure('invalid local map vector range/alignment')
            count = (finish-start)//8
            if count > max_local_objects or (capacity-start)//8 > 1048576:
                raise ReadFailure('native local object count/capacity exceeds bound')
            self.guard(start, finish-start)
        map_count = int(sub.objs.map.t.element_count)
        if map_count != count:
            raise ReadFailure('native sorted vector/hash count disagreement')
        entries = []
        previous = -1
        for index in range(count):
            address = start + index*8
            key = self.reader.u32(address)
            pointer = self.reader.pointer(address + 4)
            if not key or key <= previous or not pointer:
                raise ReadFailure('invalid/duplicate/unordered local map key or null object')
            previous = key
            obj = self.ffi.cast('struct WorldObject *', pointer)
            self.protect(obj, 'WorldObject')
            if int(obj.objId) != key:
                raise ReadFailure('native local map key/object ID mismatch')
            live = bool(obj.isLive)
            name, chain, vtable = self.rtti(pointer) if live else (None, [], self.address(obj.classptr))
            entries.append(dict(key=key, ptr=pointer, live=live, rtti=name, hierarchy=chain, classptr=vtable))
        return dict(scope=scope, native_server_t=int(cw.serverSubWorld.asSubWorldImpl.asSubWorld.t),
            native_local_t=int(sub.asSubWorld.t), world_ptr=self.address(cw),
            local_subworld_ptr=self.address(cw.mySubWorld), server_subworld_ptr=self.address(cw.serverSubWorld),
            player_ptr=self.address(character), nextObjId=int(cw.asWorld.nextObjId), total=int(cw.total),
            inUpdate=bool(sub.inUpdate), map_vector=[start, finish, capacity], map_count=map_count, entries=entries)

    def inventory(self, cw, context, now, request_id):
        return self.inventory_reader.snapshot_world(cw, context, now=now, request_id=request_id)

    def _preflight_pb(self, kind, address, max_repeated, depth=0, path=(), nodes=None):
        """The shared serializer silently skips NULL repeated elements: reject
        those and malformed headers before accepting a complete ground PB."""
        nodes = [0] if nodes is None else nodes
        nodes[0] += 1
        if depth > 12 or address in path or nodes[0] > 10000:
            raise ReadFailure('protobuf graph depth/cycle/node budget exceeded')
        layout = self.table.type(kind)
        if layout is None:
            raise ReadFailure('unknown protobuf type ' + kind)
        self.guard(address, layout['size'])
        for field in layout['fields']:
            field_kind = field['kind']
            bit = field.get('hasBit')
            if bit is not None and _has_bit(self.reader, layout, address, bit) is False:
                continue
            where = address + field['offset']
            children = []
            if field_kind == 'messagePointer':
                child = self.reader.pointer(where)
                if not child:
                    raise ReadFailure('present protobuf message has NULL pointer')
                children = [child]
            elif field_kind == 'messageInline':
                children = [where]
            elif field_kind in ('repeatedMessage', 'repeatedString', 'repeatedScalar'):
                elements = self.reader.pointer(where)
                count = self.reader.scalar('int32', where+4)
                if count < 0 or count > max_repeated or (count and not elements):
                    raise ReadFailure('invalid/oversize protobuf repeated field')
                if field_kind in ('repeatedMessage', 'repeatedString'):
                    allocated = self.reader.scalar('int32', where+8)
                    total = self.reader.scalar('int32', where+12)
                    if not 0 <= count <= allocated <= total <= 1048576:
                        raise ReadFailure('invalid protobuf pointer-array capacity')
                    if count:
                        self.guard(elements, count*4)
                    children = [self.reader.pointer(elements+i*4) for i in range(count)]
                    if any(not child for child in children):
                        raise ReadFailure('NULL element in protobuf repeated pointer field')
                    if field_kind == 'repeatedString':
                        children = []
                else:
                    total = self.reader.scalar('int32', where+8)
                    if not count <= total <= 1048576:
                        raise ReadFailure('invalid protobuf scalar-array capacity')
            for child in children:
                self._preflight_pb(field['item'], child, max_repeated, depth+1, path+(address,), nodes)

    def message(self, kind, address, max_repeated):
        self._preflight_pb(kind, address, max_repeated)
        limits = dict(DEFAULT_LIMITS, maxRepeated=max_repeated, maxNodes=20000)
        result = plain(serialize_message(self.reader, self.table, kind, address, limits, self.stats))
        if not isinstance(result, dict) or any(self.stats.get(k) for k in BAD_STATS):
            raise ReadFailure('incomplete protobuf read: ' + kind)
        return result

    def item(self, pointer, max_boosts):
        self.guard(pointer, self.ffi.sizeof('struct ItemProperties'))
        value = self.ffi.cast('struct ItemProperties *', pointer)
        count = int(value.statboosts.current_size)
        if not 0 <= count <= max_boosts:
            raise ReadFailure('ground item affix count exceeds bound')
        props = self.message('ItemProperties', pointer, max_boosts)
        if type(props.get('type')) is not int or props['type'] != int(value.type) or props['type'] <= 0:
            raise ReadFailure('ground item type/presence mismatch')
        if len(props.get('statboosts', [])) != count:
            raise ReadFailure('ground item affix count/presence mismatch')
        allowed = {v for k,v in self.table.enums['Stat'].items() if k.startswith('STAT_') and v != 0}
        if any(type(b.get('stat')) is not int or b['stat'] not in allowed for b in props.get('statboosts', [])):
            raise ReadFailure('unknown/invalid ground StatBoost')
        self.pass_boosts += count
        return props

    def start_pass(self):
        self.pass_boosts = 0

    def ground(self, entry, worldid, max_chest_items, max_boosts):
        kind = entry['rtti']
        pointer = self.ffi.cast('struct ' + kind + ' *', entry['ptr'])
        self.protect(pointer, kind)
        client = pointer.asClientObject
        native = client.asNativeObject
        obj = native.asWorldObject
        if not bool(obj.isLive) or int(obj.objId) != entry['key'] or self.address(obj.classptr) != entry['classptr']:
            raise ReadFailure('ground identity/liveness changed during read')
        self.protect(client.subWorld, 'ClientSubWorld')
        actual_worldid = int(client.subWorld.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId)
        if actual_worldid != worldid:
            raise ReadFailure('ground belongs to a different local subworld')
        props = self.message('Properties', self.address(self.ffi.addressof(obj, 'props')), max_boosts)
        motion = {field:bool(getattr(native, field)) if field in BOOL_FIELDS else int(getattr(native, field))
            for field in NATIVE_FIELDS}
        motion.update(attached=dict(worldId=int(native.attached.worldId), objId=int(native.attached.objId)),
            createSound=self.reader.strings(self.address(self.ffi.addressof(native, 'createSound'))).decode('utf8'),
            lastXmp=int(obj.lastXmp), lastYmp=int(obj.lastYmp), lastDamageT=int(obj.lastDamageT),
            subWorld=self.address(client.subWorld), action=self.address(client.action),
            damagedCount=int(native.damaged.t.element_count))
        if not 0 <= motion['damagedCount'] <= 4096:
            raise ReadFailure('invalid/beyond-bound NativeObject.damaged count')
        lifetime_present = 'lifetime' in props
        row = dict(key=dict(worldid=actual_worldid, objid=int(obj.objId)), ptr=entry['ptr'], rtti=kind,
            props=props, native=motion, lifetime_present=lifetime_present,
            remaining_lifetime_ms=props['lifetime'] if lifetime_present else None)
        if lifetime_present and type(props['lifetime']) is not int:
            raise ReadFailure('invalid remaining ground lifetime')
        if kind == 'Loot':
            row['loot'] = dict(itemprops=self.item(self.address(self.ffi.addressof(pointer[0], 'itemProps')), max_boosts),
                untilAutoApply=int(pointer.untilAutoApply))
            row['native']['itemDesc'] = self.address(pointer.itemDesc)
        elif kind == 'Chest':
            repeated = pointer.items
            count, allocated, total = (int(repeated.current_size), int(repeated.allocated_size), int(repeated.total_size))
            elements = self.address(repeated.elements)
            if not 0 <= count <= allocated <= total <= 1048576 or count > max_chest_items or (count and not elements):
                raise ReadFailure('invalid/oversize Chest.items header')
            if count:
                self.guard(elements, count*4)
            items = []
            for index in range(count):
                item = self.reader.pointer(elements + index*4)
                if not item:
                    raise ReadFailure('NULL Chest.items element')
                items.append(self.item(item, max_boosts))
            vector = pointer.angles
            start, end, capacity = (self.address(v) for v in (vector.start, vector.finish, vector.endOfStorage))
            if not start:
                if end or capacity:
                    raise ReadFailure('invalid empty Chest.angles vector')
                angles = []
            else:
                if end < start or capacity < end or (end-start)%4 or (capacity-start)%4 or (end-start)//4 > max_chest_items:
                    raise ReadFailure('invalid/oversize Chest.angles vector')
                self.guard(start, end-start)
                angles = [self.reader.scalar('int32', p) for p in range(start, end, 4)]
            pos = int(pointer.pos)
            row['chest'] = dict(items=items, angles=angles, pos=pos, untilThrow=int(pointer.untilThrow))
        else:
            raise ReadFailure('unknown ground container')
        return row

class GroundReader(object):
    """Whole local ground manifest + twice-read inventory, no cached payloads."""
    def __init__(self, ffi=None, util=None, layouts_path=None, max_ground=64,
            max_chest_items=128, max_boosts=128, max_total_boosts=4096,
            max_local_objects=4096, max_read_seconds=.05, max_json_bytes=60000, adapter=None):
        self.adapter = adapter or CffiGroundAdapter(ffi, util, layouts_path, max_read_seconds)
        self.max_ground = int(max_ground)
        self.max_chest_items = int(max_chest_items)
        self.max_boosts = int(max_boosts)
        self.max_total_boosts = int(max_total_boosts)
        self.max_local_objects = int(max_local_objects)
        self.max_read_seconds = float(max_read_seconds)
        self.max_json_bytes = int(max_json_bytes)
        self.serial = 0
        self.generation_scope = None
        self.generations = {}
        self.last_present = {}

    @staticmethod
    def _inventory_material(value):
        return {k:value.get(k) for k in ('schema', 'ok', 'items_ok', 'ec_ok', 'scope', 'items', 'ec', 'count', 'slots')}

    def _objects(self, state):
        self.adapter.start_pass()
        objects = []
        for entry in state['entries']:
            if not entry['live']:
                continue
            if any(name in ('Loot', 'Chest') for name in entry['hierarchy'][1:]):
                raise ReadFailure('unsupported derived ground container')
            if entry['rtti'] not in ('Loot', 'Chest'):
                continue
            if len(objects) >= self.max_ground:
                raise ReadFailure('ground object count exceeds bound')
            row = self.adapter.ground(entry, state['scope']['worldid'], self.max_chest_items, self.max_boosts)
            objects.append(row)
            if self.adapter.pass_boosts > self.max_total_boosts:
                raise ReadFailure('total ground affix count exceeds bound')
        return objects

    def _with_generations(self, scope, objects):
        scope_key = tuple(scope[k] for k in REFRESH_SCOPE)
        generations = copy.deepcopy(self.generations) if scope_key == self.generation_scope else {}
        old_present = self.last_present if scope_key == self.generation_scope else {}
        present = {}
        result = copy.deepcopy(objects)
        for row in result:
            key = (row['key']['worldid'], row['key']['objid'])
            identity = (row['ptr'], row['rtti'])
            generation = generations.get(key, 0)
            if old_present.get(key) != identity:
                generation += 1
            generations[key] = generation
            if len(generations) > 4096:
                raise ReadFailure('observed generation history exceeds bound')
            present[key] = identity
            row['observed_generation'] = generation
        return result, scope_key, generations, present

    def snapshot_world(self, cw, context=None, now=None, request_id=None, request_attempt=None):
        started = time.perf_counter()
        sampled = time.time() if now is None else now
        self.serial += 1
        result = dict(schema=SCHEMA, ok=False, ready=False, sampled_at=sampled,
            request_id=request_id, request_attempt=request_attempt, scope={},
            native_server_t=None, native_local_t=None,
            ground=dict(schema=GROUND_SCHEMA, ok=False, quality={}),
            quality=dict(errors=[], abi=copy.deepcopy(self.adapter.abi), stable=False,
                snapshot_serial=self.serial, memory_access_budget_s=self.max_read_seconds,
                budget_policy='SIM bounded memory accesses; final elapsed is measured, not a hard whole-function deadline',
                observed_generation_limits='Only observed disappearance/pointer/class changes increment; unobserved ABA is not excluded',
                snapshot_limits='One native-thread observation; no egress seal, onAdded/onRemoved journal or restore proof',
                native_fields_not_interpreted=['NativeObject.damaged', 'Action subclass state']))
        q = result['quality']
        begun = False
        try:
            if type(sampled) not in (int, float) or not math.isfinite(sampled):
                raise ReadFailure('invalid snapshot time')
            for name, value in (('request_id', request_id), ('request_attempt', request_attempt)):
                if value is not None and (type(value) is not int or value <= 0):
                    raise ReadFailure('invalid ' + name)
            if (request_id is None) != (request_attempt is None):
                raise ReadFailure('nonce and attempt must both be present or absent')
            context = context if isinstance(context, dict) else {}
            if context.get('room_ready') is not True or not isinstance(context.get('map'), str) or not context['map']:
                raise ReadFailure('native room context is not ready')
            begun = True
            self.adapter.begin(cw, context)
            before = self.adapter.state(cw, context, self.max_local_objects)
            scope = before['scope']
            if any(k not in scope for k in REFRESH_SCOPE):
                raise ReadFailure('incomplete joint scope')
            if any(type(scope[k]) is not int for k in REFRESH_SCOPE if k != 'map'):
                raise ReadFailure('invalid joint scope integer')
            if any(scope.get(k) != context.get(k) for k in ('map', 'room_token', 'room_seed')):
                raise ReadFailure('native world/context scope mismatch')
            if any(scope[k] <= 0 for k in ('room_token', 'worldid', 'objid')) or not 0 <= scope['room_seed'] <= 4294967295:
                raise ReadFailure('joint scope range mismatch')
            result.update(scope=copy.deepcopy(scope), native_server_t=before['native_server_t'], native_local_t=before['native_local_t'])
            inventory_before = self.adapter.inventory(cw, context, sampled, request_id)
            if inventory_before.get('ok') is not True or inventory_before.get('scope') != scope:
                raise ReadFailure('initial joint inventory read is incomplete/mismatched: ' + str(inventory_before.get('quality', {}).get('errors', [])))
            objects_before = self._objects(before)
            ground_boosts = self.adapter.pass_boosts
            inventory_boosts = sum(len(item.get('statboosts', [])) for item in inventory_before['items'])
            if ground_boosts + inventory_boosts > self.max_total_boosts:
                raise ReadFailure('joint inventory/ground affix count exceeds bound')
            objects_after = self._objects(before)
            inventory_after = self.adapter.inventory(cw, context, sampled, request_id)
            after = self.adapter.state(cw, context, self.max_local_objects)
            if before != after:
                raise ReadFailure('native local set/count/clocks/identity changed during joint read')
            if objects_before != objects_after:
                raise ReadFailure('native Loot/Chest full state changed during joint read')
            if self._inventory_material(inventory_before) != self._inventory_material(inventory_after) or inventory_after.get('ok') is not True:
                raise ReadFailure('native inventory/EC changed during joint read')
            objects, generation_scope, generations, present = self._with_generations(scope, objects_after)
            inventory_after.update(sampled_at=sampled, request_id=request_id, request_attempt=request_attempt,
                native_server_t=before['native_server_t'])
            result['inventory'] = inventory_after
            result['ground'] = dict(schema=GROUND_SCHEMA, ok=True, count=len(objects), objects=objects,
                quality=dict(stable=True, passes=2, local_declared_count=before['map_count'],
                    local_traversed_count=len(before['entries']), nextObjId=before['nextObjId'],
                    native_clocks_equal=True, set_key_ptr_class_live_equal=True,
                    ground_boost_count=ground_boosts, inventory_boost_count=inventory_boosts,
                    local_map_vector=before['map_vector'], local_classes=sorted({e['rtti'] for e in before['entries'] if e['live']}),
                    chest_array_counts_equal=all(len(o['chest']['items'])==len(o['chest']['angles']) for o in objects if o['rtti']=='Chest')))
            result.update(ok=True, ready=True)
            q.update(stable=True, inventory_passes=2, ground_passes=2,
                read_elapsed_s=time.perf_counter()-started, wire_encoding='ASCII ensure_ascii=True canonical separators')

            q['snapshot_json_bytes'] = 0
            for unused in range(4):
                size = ascii_wire_bytes(result)
                if q['snapshot_json_bytes'] == size:
                    break
                q['snapshot_json_bytes'] = size
            if ascii_wire_bytes(result) > self.max_json_bytes:
                raise ReadFailure('joint ASCII JSON payload exceeds transport budget')
        except Exception as error:
            result.update(ok=False, ready=False)
            result.pop('inventory', None)
            result['ground'] = dict(schema=GROUND_SCHEMA, ok=False, quality=dict(stable=False))
            q['stable'] = False
            q['errors'].append(type(error).__name__ + ': ' + str(error))
        finally:
            if begun:
                self.adapter.finish()
        q['read_elapsed_s'] = time.perf_counter()-started

        if result['ok']:
            for unused in range(4):
                size = ascii_wire_bytes(result)
                if q['snapshot_json_bytes'] == size:
                    break
                q['snapshot_json_bytes'] = size
            if ascii_wire_bytes(result) > self.max_json_bytes:
                result.update(ok=False, ready=False)
                result.pop('inventory', None)
                result['ground'] = dict(schema=GROUND_SCHEMA, ok=False, quality=dict(stable=False))
                q['stable'] = False
                q['errors'].append('ReadFailure: final joint ASCII JSON payload exceeds transport budget')
        if result['ok']:

            self.generation_scope = generation_scope
            self.generations = generations
            self.last_present = present
        return result
