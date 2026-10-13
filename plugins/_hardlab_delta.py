'Read-only capture of *parsed* server deltas (``Delta`` trees).'

import base64
import json
import os
import struct
import sys
import time

SCHEMA = 'sb-hardlab-delta-v1'

MODULE_VERSION = '0.2.0'

MAX_STRING_BYTES = 4096

RAW_STRING_FIELDS = frozenset(('messageval',))

def _is_raw_string(field):
    """``messageval_`` and ``messageval`` both name the embedded-message field."""
    name = str(field.get('name') or '')
    return name.rstrip('_') in RAW_STRING_FIELDS

DEFAULT_LIMITS = {
    'maxDepth': 12,
    'maxRepeated': 4096,
    'maxNodes': 20000,
    'maxStringBytes': 4096,
}

SCALAR_TYPES = {
    'int8': 'int8_t',
    'uint8': 'uint8_t',
    'int16': 'int16_t',
    'uint16': 'uint16_t',
    'int32': 'int32_t',
    'uint32': 'uint32_t',
    'int64': 'int64_t',
    'uint64': 'uint64_t',
    'float': 'float',
    'double': 'double',
    'bool': 'uint8_t',
    'enum': 'int32_t',
}

MESSAGE_POINTER = 'messagePointer'
MESSAGE_INLINE = 'messageInline'
REPEATED_MESSAGE = 'repeatedMessage'
REPEATED_SCALAR = 'repeatedScalar'
REPEATED_STRING = 'repeatedString'
STRING_POINTER = 'stringPointer'
STRING_INLINE = 'stringInline'

def utc_now():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())

def load_layouts(path):
    """Load ``runtime_pb_layouts.json``; returns a :class:`LayoutTable`."""
    with open(path, 'r', encoding='utf-8') as handle:
        document = json.load(handle)
    return LayoutTable(document)

class LayoutTable(object):
    """Type/enum lookup for the generated protobuf classes."""

    def __init__(self, document):
        self.document = document or {}
        self.types = self.document.get('types') or {}
        self.enums = self.document.get('enums') or {}
        self._labels = {}

    def type(self, name):
        return self.types.get(name)

    def enum_label(self, enum_name, value):
        values = self.enums.get(enum_name)
        if not values:
            return None
        key = (enum_name, int(value))
        label = self._labels.get(key)
        if label is None:
            for name, number in values.items():
                if int(number) == int(value):
                    label = name
                    break
            else:
                label = ''
            self._labels[key] = label
        return label or None

class CffiReader(object):
    """Read primitives out of the running game process through CFFI."""

    def __init__(self, ffi, util):
        self.ffi = ffi
        self.util = util

    def u8(self, address):
        return int(self.ffi.cast('uint8_t *', address)[0])

    def u32(self, address):
        return int(self.ffi.cast('uint32_t *', address)[0])

    def pointer(self, address):
        return int(self.ffi.cast('uintptr_t *', address)[0])

    def scalar(self, kind, address):
        ctype = SCALAR_TYPES.get(kind)
        if ctype is None:
            raise ValueError('unsupported scalar kind: ' + str(kind))
        value = self.ffi.cast(ctype + ' *', address)[0]
        if kind in ('int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32'):
            return int(value)
        if kind == 'bool':
            return bool(value)
        if kind == 'enum':
            return int(value)
        return float(value)

    def strings(self, address):
        'Read a ``std::string`` object stored at *address* as ``bytes``.'
        data = self.pointer(address)
        if not data:
            return b''
        try:
            length = self.u32(data - 12)
            capacity = self.u32(data - 8)
        except Exception:
            return self._scan_cstring(data)
        if length > MAX_STRING_BYTES or length > capacity:

            return self._scan_cstring(data)
        return bytes(
            self.ffi.buffer(self.ffi.cast('char *', data), length)[:])

    def _scan_cstring(self, data, limit=256):
        out = bytearray()
        for index in range(limit):
            try:
                value = self.u8(data + index)
            except Exception:
                break
            if value == 0:
                break
            out.append(value)
        return bytes(out)

    def cstring(self, address):
        pointer = self.pointer(address)
        if not pointer:
            return None
        return self.ffi.string(self.ffi.cast('char *', pointer)).decode(
            'utf-8', errors='replace')

def new_stats():
    return {
        'nodes': 0,
        'readErrors': 0,
        'skippedFields': 0,
        'stalePointerSkipped': 0,
        'presenceMismatch': 0,
        'presenceMismatchFields': {},
        'unknownTypes': 0,
        'unknownKinds': {},
        'depthTruncated': 0,
        'nodeBudgetExceeded': 0,
        'repeatedTruncated': 0,
        'stringErrors': 0,
    }

def _bump(mapping, key):
    mapping[key] = mapping.get(key, 0) + 1

def _has_bit(reader, layout, address, index):
    offset = layout.get('hasBitsOffset')
    if offset is None:
        return None
    word = int(index) // 32
    bit = int(index) % 32
    value = reader.u32(address + offset + word * 4)
    return bool(value & (1 << bit))

def _encode_raw(value):
    """Binary payloads (serialised sub-messages) survive as base64 text."""
    return {
        'raw': base64.b64encode(value).decode('ascii'),
        'bytes': len(value),
    }

def _decode_string(value, raw):
    if isinstance(value, str):

        value = value.encode('latin-1')
    if raw:
        return _encode_raw(value)
    try:
        return value.decode('utf-8')
    except UnicodeDecodeError:
        return _encode_raw(value)

def _safe_string(reader, address, stats, raw=False):
    try:
        value = reader.strings(address)
    except Exception:
        stats['stringErrors'] += 1
        return None
    return _decode_string(value, raw)

def serialize_message(reader, table, type_name, address, limits, stats,
                      depth=0):
    """Serialise one generated protobuf message into a plain dict.

    Returns ``None`` when a budget stopped the walk.  Never raises for a
    field-level problem: the counters in *stats* describe what was skipped.
    """
    if address in (0, None):
        return None
    layout = table.type(type_name)
    if layout is None:
        stats['unknownTypes'] += 1
        return {'_type': type_name, '_unreadable': 'no layout'}
    if depth > int(limits['maxDepth']):
        stats['depthTruncated'] += 1
        return None
    stats['nodes'] += 1
    if stats['nodes'] > int(limits['maxNodes']):
        stats['nodeBudgetExceeded'] += 1
        return None

    result = {}
    for field in layout.get('fields') or ():
        kind = field.get('kind')
        offset = int(field.get('offset') or 0)
        field_address = address + offset
        name = field.get('name') or ''
        if name.endswith('_'):
            name = name[:-1]
        bit = field.get('hasBit')
        present = True
        if bit is not None:
            try:
                present = bool(_has_bit(reader, layout, address, bit))
            except Exception:
                stats['readErrors'] += 1
                continue
        pointer_kind = kind in (MESSAGE_POINTER, STRING_POINTER)
        if bit is not None and not present:

            if pointer_kind:
                try:
                    if reader.pointer(field_address):
                        stats['stalePointerSkipped'] += 1
                except Exception:
                    stats['readErrors'] += 1
            continue
        try:
            value = _read_field(
                reader, table, field, kind, field_address, limits, stats,
                depth)
        except Exception:
            stats['readErrors'] += 1
            continue
        if value is _SKIP:
            continue
        if value is _EMPTY:

            if present:
                stats['presenceMismatch'] += 1
                _bump(stats['presenceMismatchFields'],
                      type_name + '.' + name + ' (null with bit)')
            continue
        enum_name = field.get('enum')
        if enum_name:
            label = table.enum_label(enum_name, value)
            if label:
                value = {'value': value, 'name': label}
        result[name] = value
    return result

class _Skip(object):
    pass

class _Empty(object):
    pass

_SKIP = _Skip()
_EMPTY = _Empty()

BRIDGE_SLOT_OFFSET = 48

def build_bridge_code(stub_address, callback_address, trampoline_address,
                      slot_offset=BRIDGE_SLOT_OFFSET):
    """Return the x86 bridge that lets a cdecl callback observe __thiscall.

    Layout (all little-endian):

    ``mov [slot], ecx``   - keep ``this``
    ``mov eax, [slot]``   - first cdecl argument is ``this``
    ``push eax``
    ``mov eax, callback``
    ``call eax``          - callback(ecx_this, caller_return, &Delta)
    ``add esp, 4``        - caller cleans the pushed argument
    ``mov ecx, [slot]``   - restore ``this`` for the real function
    ``mov eax, trampoline``
    ``jmp eax``           - subhook trampoline re-runs the original bytes
    """
    slot = int(stub_address) + int(slot_offset)
    code = bytearray()
    code.extend(b'\x89\x0d')
    code.extend(struct.pack('<I', slot))
    code.extend(b'\xa1')
    code.extend(struct.pack('<I', slot))
    code.append(0x50)
    code.append(0xB8)
    code.extend(struct.pack('<I', int(callback_address)))
    code.extend(b'\xff\xd0')
    code.extend(b'\x83\xc4\x04')
    code.extend(b'\x8b\x0d')
    code.extend(struct.pack('<I', slot))
    code.append(0xB8)
    code.extend(struct.pack('<I', int(trampoline_address)))
    code.extend(b'\xff\xe0')
    if len(code) > int(slot_offset):
        raise ValueError('bridge code overlaps its scratch slot')
    return bytes(code)

def _read_field(reader, table, field, kind, address, limits, stats, depth):
    if kind == MESSAGE_POINTER:
        pointer = reader.pointer(address)
        if not pointer:
            return _EMPTY
        value = serialize_message(
            reader, table, field.get('item'), pointer, limits, stats,
            depth + 1)
        return _SKIP if value is None else value
    if kind == MESSAGE_INLINE:
        value = serialize_message(
            reader, table, field.get('item'), address, limits, stats,
            depth + 1)
        return _SKIP if value is None else value
    if kind == STRING_POINTER:
        pointer = reader.pointer(address)
        if not pointer:
            return _EMPTY
        value = _safe_string(
            reader, pointer, stats,
            raw=_is_raw_string(field))
        return value if value is not None else _SKIP
    if kind == STRING_INLINE:
        value = _safe_string(
            reader, address, stats,
            raw=_is_raw_string(field))
        return value if value is not None else _SKIP
    if kind == REPEATED_MESSAGE:
        return _read_repeated_message(
            reader, table, field, address, limits, stats, depth)
    if kind == REPEATED_STRING:
        return _read_repeated_string(reader, address, limits, stats)
    if kind == REPEATED_SCALAR:
        return _read_repeated_scalar(
            reader, field, address, limits, stats)
    if kind in SCALAR_TYPES:
        return reader.scalar(kind, address)
    _bump(stats['unknownKinds'], str(kind))
    return _SKIP

def _read_repeated_message(reader, table, field, address, limits, stats, depth):
    elements = reader.pointer(address)
    count = reader.u32(address + 4)
    if not elements or count <= 0:
        return _SKIP
    limit = int(limits['maxRepeated'])
    if count > limit:
        stats['repeatedTruncated'] += 1
        count = limit
    items = []
    item_type = field.get('item')
    for index in range(count):
        pointer = reader.pointer(elements + index * 4)
        if not pointer:
            continue
        item = serialize_message(
            reader, table, item_type, pointer, limits, stats, depth + 1)
        if item is not None:
            items.append(item)
    if not items:
        return _SKIP
    return items

def _read_repeated_string(reader, address, limits, stats):
    """``RepeatedPtrField<std::string>``: element pointers, not messages."""
    elements = reader.pointer(address)
    count = reader.u32(address + 4)
    if not elements or count <= 0:
        return _SKIP
    limit = int(limits['maxRepeated'])
    if count > limit:
        stats['repeatedTruncated'] += 1
        count = limit
    items = []
    for index in range(count):
        pointer = reader.pointer(elements + index * 4)
        if not pointer:
            continue
        value = _safe_string(reader, pointer, stats, raw=False)
        if value is not None:
            items.append(value)
    if not items:
        return _SKIP
    return items

def _read_repeated_scalar(reader, field, address, limits, stats):
    elements = reader.pointer(address)
    count = reader.u32(address + 4)
    if not elements or count <= 0:
        return _SKIP
    limit = int(limits['maxRepeated'])
    if count > limit:
        stats['repeatedTruncated'] += 1
        count = limit
    kind = field.get('item') or ''
    kind = SCALAR_ALIASES.get(kind, kind)
    if kind not in SCALAR_TYPES:
        _bump(stats['unknownKinds'], 'repeated:' + str(kind))
        return _SKIP
    size = SCALAR_SIZES[kind]
    items = []
    for index in range(count):
        items.append(reader.scalar(kind, elements + index * size))
    return items

SCALAR_SIZES = {
    'int8': 1, 'uint8': 1, 'int16': 2, 'uint16': 2,
    'int32': 4, 'uint32': 4, 'int64': 8, 'uint64': 8,
    'float': 4, 'double': 8, 'bool': 1, 'enum': 4,
}

SCALAR_ALIASES = {
    'int': 'int32',
    'signed int': 'int32',
    'unsigned int': 'uint32',
    'long int': 'int32',
    'long unsigned int': 'uint32',
    'long long int': 'int64',
    'long long unsigned int': 'uint64',
    'bool': 'bool',
    'float': 'float',
    'double': 'double',
}

def delta_document(reader, table, address, limits=None, stats=None):
    """Serialise one ``Delta`` into a JSON-safe document."""
    limits = dict(DEFAULT_LIMITS, **(limits or {}))
    stats = stats if stats is not None else new_stats()
    payload = serialize_message(
        reader, table, 'Delta', address, limits, stats)
    if payload is None:
        return None
    mismatch = dict(
        sorted(stats['presenceMismatchFields'].items(),
               key=lambda item: (-item[1], item[0]))[:8])
    return {
        'schema': SCHEMA,
        'receivedAt': round(time.time(), 6),
        'delta': payload,
        'stats': {
            'nodes': stats['nodes'],
            'readErrors': stats['readErrors'],
            'skippedFields': stats['skippedFields'],
            'stalePointerSkipped': stats['stalePointerSkipped'],
            'presenceMismatch': stats['presenceMismatch'],
            'presenceMismatchFields': mismatch,
            'unknownTypes': stats['unknownTypes'],
            'unknownKinds': dict(stats['unknownKinds']),
            'depthTruncated': stats['depthTruncated'],
            'nodeBudgetExceeded': stats['nodeBudgetExceeded'],
            'repeatedTruncated': stats['repeatedTruncated'],
            'stringErrors': stats['stringErrors'],
        },
    }

MOVE_FIELDS = ('movetoxmp', 'movetoymp', 'movetodt')

def _project_document(payload, include_move_fields, stats):
    if include_move_fields or not isinstance(payload, dict):
        return payload
    world_update = payload.get('worldupdate')
    if not isinstance(world_update, dict):
        return payload
    updates = world_update.get('objectupdates')
    if not isinstance(updates, list):
        return payload
    for update in updates:
        if not isinstance(update, dict):
            continue
        for name in MOVE_FIELDS:
            if name in update:
                del update[name]
                stats['skippedFields'] += 1
    return payload

class DeltaHook(object):
    """Observe ``World::addDelta`` without changing game behaviour.

    Unlike the raw byte hook in ``_hardlab_net.py`` this one has to inspect the
    message *during* the call, because the ``Delta`` lives on the caller's
    stack.  The document is therefore built inside the callback, but the work
    is bounded by ``limits`` and every failure is swallowed.
    """

    HOOK_CODE_CAPACITY = 64

    THUNK_SLOT_OFFSET = 48
    MEM_COMMIT_RESERVE = 0x3000
    PAGE_EXECUTE_READWRITE = 0x40

    def __init__(self, ffi, lib, table, reader, limits=None,
                 max_queue_nodes=200000, include_move_fields=False):
        self.ffi = ffi
        self.lib = lib
        self.table = table
        self.reader = reader
        self.limits = dict(DEFAULT_LIMITS, **(limits or {}))
        self.max_queue_nodes = int(max_queue_nodes)
        self.include_move_fields = bool(include_move_fields)

        self._records = []
        self._queued_nodes = 0
        self._dropped = 0
        self._received = 0
        self._error = 0
        self._callback_calls = 0
        self._callback_seconds = 0.0
        self._callback_max_seconds = 0.0
        self._last_error = None
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

    def stats(self):
        calls = self._callback_calls or 1
        return {
            'installed': self.installed,
            'received': int(self._received),
            'queued': len(self._records),
            'queuedNodes': int(self._queued_nodes),
            'dropped': int(self._dropped),
            'errors': int(self._error),
            'lastError': self._last_error,
            'callbackMicrosAvg': round(
                self._callback_seconds / calls * 1000000.0, 2),
            'callbackMicrosMax': round(
                self._callback_max_seconds * 1000000.0, 2),
            'target': 'World::addDelta',
            'moduleVersion': MODULE_VERSION,
            'includeMoveFields': self.include_move_fields,
        }

    def _on_delta(self, this_world, caller_return_address, delta_address):
        started = time.time()
        try:
            del caller_return_address
            self._received += 1
            address = self._address(delta_address)
            if not address:
                return
            stats = new_stats()
            document = delta_document(
                self.reader, self.table, address, self.limits, stats)
            if document is None:
                self._dropped += 1
                return
            passive = sys.modules.get('_drop_stats_native')
            if passive is not None:
                passive.relay_full_delta(this_world, document['delta'], document['stats'])
            _project_document(document['delta'], self.include_move_fields,
                              stats)
            nodes = int(document['stats']['nodes']) + 1
            if self._queued_nodes + nodes > self.max_queue_nodes:
                self._dropped += 1
                return
            self._records.append((time.time(), document))
            self._queued_nodes += nodes
        except Exception as error:

            self._error += 1
            try:
                self._last_error = '{0}: {1}'.format(
                    type(error).__name__, error)
            except Exception:
                self._last_error = 'unprintable error'
            self._dropped += 1
        finally:
            elapsed = time.time() - started
            self._callback_calls += 1
            self._callback_seconds += elapsed
            if elapsed > self._callback_max_seconds:
                self._callback_max_seconds = elapsed

    def _address(self, pointer):
        """CFFI pointer -> int, tolerating plain ints and NULL."""
        if pointer == self.ffi.NULL:
            return 0
        try:
            return int(pointer)
        except TypeError:
            return int(self.ffi.cast('uintptr_t', pointer))

    def install(self, target_address):
        if self._hook is not None:
            return True
        ffi = self.ffi
        lib = self.lib
        target = int(target_address or 0)
        if not target:
            raise ValueError('missing World::addDelta address')

        self._callback = ffi.callback(
            'void(void *, void *, void *)', self._on_delta)
        stub = self._kernel32.VirtualAlloc(
            None, self.HOOK_CODE_CAPACITY, self.MEM_COMMIT_RESERVE,
            self.PAGE_EXECUTE_READWRITE)
        if not stub:
            raise MemoryError('VirtualAlloc failed for delta hook')
        self._stub = int(stub)

        hook = lib.subhook_new(
            ffi.cast('void *', target), ffi.cast('void *', self._stub), 0)
        if hook == ffi.NULL:
            self._free_stub()
            raise RuntimeError('subhook_new failed for World::addDelta')
        self._hook = hook
        trampoline = lib.subhook_get_trampoline(hook)
        if trampoline == ffi.NULL:
            self.uninstall()
            raise RuntimeError(
                'subhook_get_trampoline failed for World::addDelta')
        self._trampoline = int(ffi.cast('uintptr_t', trampoline))
        self._write_stub()

        lib.subhook_install(hook)
        if not lib.subhook_is_installed(hook):
            self.uninstall()
            raise RuntimeError(
                'subhook_install failed for World::addDelta')
        return True

    def _write_stub(self):
        callback_address = int(self.ffi.cast('uintptr_t', self._callback))
        code = build_bridge_code(
            self._stub, callback_address, self._trampoline,
            self.THUNK_SLOT_OFFSET)
        self._ctypes.memmove(self._stub, code, len(code))
        self._kernel32.FlushInstructionCache(
            self._kernel32.GetCurrentProcess(), self._stub, len(code))

    def _free_stub(self):
        if self._stub:
            try:
                self._kernel32.VirtualFree(self._stub, 0, 0x8000)
            except Exception:
                pass
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

    def drain(self):
        records, self._records = self._records, []
        self._queued_nodes = 0
        return records

def jsonl_records(received_at, document):
    """Flatten a queued document into a JSONL record."""
    record = dict(document)
    record['type'] = 'delta'
    record['queuedAt'] = round(float(received_at), 6)
    return record
