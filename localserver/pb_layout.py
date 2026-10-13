"Minimal protobuf wire codec driven by the client's DWARF layouts."

import base64
from functools import lru_cache
import json
import os
import struct

DEFAULT_LAYOUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'plugins', 'runtime_pb_layouts.json')

VARINT_KINDS = ('uint32', 'int32', 'sint32', 'bool')
SCALAR_FLOAT_KINDS = ('float', 'double')
SMALL_VARINTS = tuple(bytes((value,)) for value in range(128))

BUILTIN_WIRE_OVERRIDES = {
    ('Connect', 'hid_'): 5,
    ('Connect', 'enckey_'): 5,
    ('Connect', 'sessionid_'): 5,
}

WIRE_OVERRIDE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'wire_overrides.json')
SIGNED_OVERRIDE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'signed_wire_overrides.json')

class LayoutError(ValueError):
    pass

def _varint(value):
    if 0 <= value < 128:
        return SMALL_VARINTS[value]
    if value < 0:
        value &= 0xffffffffffffffff
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)

@lru_cache(maxsize=512)
def _tag(field_number, wire_type):
    return _varint((field_number << 3) | wire_type)

def _read_varint(data, offset):
    result = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise LayoutError('truncated varint')
        byte = data[offset]
        offset += 1
        result |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return result, offset
        shift += 7
        if shift >= 70:
            raise LayoutError('varint too long')

def _as_signed32(value):
    value &= 0xffffffff
    if value >= 0x80000000:
        value -= 0x100000000
    return value

class Layouts(object):
    def __init__(self, path=None, wire_overrides=None):
        self.path = path or DEFAULT_LAYOUT_PATH
        with open(self.path, 'r') as handle:
            document = json.load(handle)
        self.types = document['types']

        if os.path.exists(SIGNED_OVERRIDE_PATH):
            with open(SIGNED_OVERRIDE_PATH, 'r') as handle:
                signed = json.load(handle).get('fields', {})
            for type_name, item in self.types.items():
                for field in item.get('fields', []):
                    if type_name + '.' + field['name'] in signed:
                        if field.get('kind') == 'repeatedScalar':
                            field['item'] = 'sint32'
                        else:
                            field['kind'] = 'sint32'
        self.enums = document.get('enums', {})
        self.wire_overrides = dict(BUILTIN_WIRE_OVERRIDES)
        if wire_overrides:
            for key, wire_type in wire_overrides.items():
                if isinstance(key, str):
                    type_name, _, field_name = key.partition('.')
                    self.wire_overrides[(type_name, field_name)] = int(
                        wire_type)
                else:
                    self.wire_overrides[tuple(key)] = int(wire_type)
        self._fields = {}
        self._field_names = {}
        for name, item in self.types.items():
            fields = {}
            for field in item.get('fields', []):
                spec = dict(field)
                spec['_index'] = len(fields)
                fields[int(field['number'])] = spec
            self._fields[name] = fields
            names = {}
            for spec in fields.values():

                names.setdefault(spec['name'], spec)
                names.setdefault(spec['name'].rstrip('_'), spec)
            self._field_names[name] = names

    def fields(self, type_name):
        if type_name not in self._fields:
            raise LayoutError('unknown message type: ' + str(type_name))
        return self._fields[type_name]

    def field(self, type_name, number):
        return self.fields(type_name).get(int(number))

    def resolve_name(self, type_name, value):
        """Return the field spec for a name or number in ``value``."""
        if isinstance(value, int):
            return self.field(type_name, value)
        names = self._field_names.get(type_name)
        if names is None:
            self.fields(type_name)
        return names.get(str(value))

    def enum_number(self, enum_name, value):
        if isinstance(value, int):
            return value
        values = self.enums.get(enum_name) or {}
        if value in values:
            return int(values[value])
        raise LayoutError('unknown enum value {0}.{1}'.format(enum_name, value))

    def encode(self, type_name, values):
        if values is None:
            values = {}
        if not isinstance(values, dict):
            raise LayoutError('message value must be a dict: ' + type_name)
        output = bytearray()
        for key, value in values.items():
            spec = self.resolve_name(type_name, key)
            if spec is None:
                raise LayoutError('{0}: unknown field {1!r}'.format(
                    type_name, key))
            self._encode_field(output, type_name, spec, value)
        return bytes(output)

    def wire_type(self, type_name, spec):
        return self.wire_overrides.get((type_name, spec['name']))

    def _encode_field(self, output, type_name, spec, value):
        kind = spec.get('kind')
        number = int(spec['number'])
        if value is None:
            return
        if (kind in VARINT_KINDS and isinstance(value, dict)
                and isinstance(value.get('value'), int)):
            value = value['value']
        override = self.wire_type(type_name, spec)
        if override == 5 and kind in VARINT_KINDS:
            output.extend(_tag(number, 5))
            output.extend(struct.pack('<I', int(value) & 0xffffffff))
            return
        if override == 1 and kind in VARINT_KINDS:
            output.extend(_tag(number, 1))
            output.extend(struct.pack('<Q', int(value) & 0xffffffffffffffff))
            return
        if kind in VARINT_KINDS:
            if isinstance(value, dict) and 'value' in value:
                value = value['value']
            output.extend(_tag(number, 0))
            value = int(value)
            if kind == 'sint32':
                value = ((value << 1) ^ (value >> 31)) & 0xffffffff
            output.extend(_varint(value))
            return
        if kind == 'float':
            output.extend(_tag(number, 5))
            output.extend(struct.pack('<f', float(value)))
            return
        if kind == 'double':
            output.extend(_tag(number, 1))
            output.extend(struct.pack('<d', float(value)))
            return
        if kind == 'stringPointer':
            if isinstance(value, dict) and 'raw' in value:
                import base64 as _base64
                blob = _base64.b64decode(value['raw'].encode('ascii'))
            elif isinstance(value, (bytes, bytearray)):
                blob = bytes(value)
            else:
                blob = str(value).encode('utf-8')
            output.extend(_tag(number, 2))
            output.extend(_varint(len(blob)))
            output.extend(blob)
            return
        if kind == 'messagePointer':
            blob = self.encode(spec['item'], value)
            output.extend(_tag(number, 2))
            output.extend(_varint(len(blob)))
            output.extend(blob)
            return
        if kind == 'repeatedString':
            for item in value:
                blob = str(item).encode('utf-8')
                output.extend(_tag(number, 2))
                output.extend(_varint(len(blob)))
                output.extend(blob)
            return
        if kind == 'repeatedMessage':
            for item in value:
                blob = self.encode(spec['item'], item)
                output.extend(_tag(number, 2))
                output.extend(_varint(len(blob)))
                output.extend(blob)
            return
        if kind == 'repeatedScalar':
            scalar = spec.get('item')
            for item in value:
                if scalar == 'float':
                    output.extend(_tag(number, 5))
                    output.extend(struct.pack('<f', float(item)))
                elif scalar == 'double':
                    output.extend(_tag(number, 1))
                    output.extend(struct.pack('<d', float(item)))
                else:
                    output.extend(_tag(number, 0))
                    value = int(item)
                    if scalar == 'sint32':
                        value = ((value << 1) ^ (value >> 31)) & 0xffffffff
                    output.extend(_varint(value))
            return
        raise LayoutError('{0}.{1}: unsupported kind {2}'.format(
            type_name, spec.get('name'), kind))

    def decode(self, type_name, data, raw_strings=False):
        """Decode a protobuf message into JSON-shaped values.

        ``raw_strings`` keeps ``stringPointer``/``repeatedString`` payloads as
        ``{"raw": <base64>}`` instead of utf-8 text.  Types such as
        ``ObjectUpdate_ModifyProps.messageval_`` carry binary sub-messages
        (Move, AnimProps, ...) that are not valid utf-8, so a text decode
        destroys them and the packet can no longer be re-encoded unchanged.
        """
        if isinstance(data, str):
            data = data.encode('latin1')
        data = bytes(data)
        fields = self.fields(type_name)
        result = {}
        offset = 0
        while offset < len(data):
            tag, offset = _read_varint(data, offset)
            number = tag >> 3
            wire_type = tag & 7
            spec = fields.get(number)
            if spec is None:
                offset = self._skip_unknown(data, offset, wire_type)
                continue
            offset = self._decode_field(
                result, data, offset, type_name, spec, wire_type,
                raw_strings)
        return result

    def _decode_field(self, result, data, offset, type_name, spec, wire_type,
                      raw_strings=False):
        kind = spec.get('kind')
        name = spec['name']
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
            if kind == 'int32':
                value = _as_signed32(value)
            elif kind == 'sint32':
                value = (value >> 1) ^ -(value & 1)
            elif kind == 'bool':
                value = bool(value)
        elif wire_type == 1:
            if offset + 8 > len(data):
                raise LayoutError('truncated fixed64')
            raw = data[offset:offset + 8]
            offset += 8
            if kind == 'double':
                value = struct.unpack('<d', raw)[0]
            elif kind in VARINT_KINDS:
                value = struct.unpack('<Q', raw)[0]
            else:
                value = raw
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            if offset + length > len(data):
                raise LayoutError('truncated length-delimited field')
            raw = data[offset:offset + length]
            offset += length
            value = self._decode_length_value(
                type_name, spec, raw, kind, raw_strings)
        elif wire_type == 5:
            if offset + 4 > len(data):
                raise LayoutError('truncated fixed32')
            raw = data[offset:offset + 4]
            offset += 4
            if kind == 'float':
                value = struct.unpack('<f', raw)[0]
            elif kind in ('uint32', 'int32'):
                value = struct.unpack('<I', raw)[0]
                if kind == 'int32':
                    value = _as_signed32(value)
            elif kind == 'bool':
                value = bool(struct.unpack('<I', raw)[0])
            else:
                value = raw
        else:
            raise LayoutError('unsupported wire type {0} for {1}.{2}'.format(
                wire_type, type_name, name))
        self._store(result, kind, name, value)
        return offset

    def _decode_length_value(self, type_name, spec, raw, kind,
                             raw_strings=False):
        if kind == 'stringPointer':
            if raw_strings:
                return {'raw': base64.b64encode(raw).decode('ascii')}
            return raw.decode('utf-8', 'replace')
        if kind == 'messagePointer':
            return self.decode(spec['item'], raw, raw_strings=raw_strings)
        if kind == 'repeatedMessage':
            return self.decode(spec['item'], raw, raw_strings=raw_strings)
        if kind == 'repeatedString':
            if raw_strings:
                return {'raw': base64.b64encode(raw).decode('ascii')}
            return raw.decode('utf-8', 'replace')
        if kind == 'repeatedScalar':
            return self._decode_packed_scalar(spec, raw)
        return raw

    def _decode_packed_scalar(self, spec, raw):
        scalar = spec.get('item')
        values = []
        if scalar in SCALAR_FLOAT_KINDS:
            fmt = '<f' if scalar == 'float' else '<d'
            size = 4 if scalar == 'float' else 8
            for index in range(0, len(raw) - size + 1, size):
                values.append(struct.unpack(fmt, raw[index:index + size])[0])
            return values
        offset = 0
        while offset < len(raw):
            value, offset = _read_varint(raw, offset)
            values.append((value >> 1) ^ -(value & 1)
                          if scalar == 'sint32' else _as_signed32(value))
        return values

    def _store(self, result, kind, name, value):
        if kind in ('repeatedScalar', 'repeatedString', 'repeatedMessage'):
            result.setdefault(name, []).append(value)
        else:
            result[name] = value

    def _skip_unknown(self, data, offset, wire_type):
        if wire_type == 0:
            _value, offset = _read_varint(data, offset)
            return offset
        if wire_type == 1:
            return offset + 8
        if wire_type == 2:
            length, offset = _read_varint(data, offset)
            return offset + length
        if wire_type == 5:
            return offset + 4
        raise LayoutError('unknown wire type: ' + str(wire_type))

    def observe_wire_types(self, type_name, data):
        """Map field name -> wire type for one payload (no value decoding).

        Used to learn which fields are really ``fixed32``/``fixed64`` so that
        encoding a message back to the client stays byte compatible.
        """
        if isinstance(data, str):
            data = data.encode('latin1')
        data = bytes(data)
        fields = self.fields(type_name)
        observed = {}
        offset = 0
        while offset < len(data):
            tag, offset = _read_varint(data, offset)
            number = tag >> 3
            wire_type = tag & 7
            spec = fields.get(number)
            if spec is None:
                offset = self._skip_unknown(data, offset, wire_type)
                continue
            observed[spec['name']] = wire_type
            if wire_type == 0:
                _value, offset = _read_varint(data, offset)
            elif wire_type == 1:
                offset += 8
            elif wire_type == 2:
                length, offset = _read_varint(data, offset)
                offset += length
            elif wire_type == 5:
                offset += 4
            else:
                raise LayoutError('unknown wire type: ' + str(wire_type))
        return observed

    def decode_file(self, type_name, path):
        with open(path, 'rb') as handle:
            return self.decode(type_name, handle.read())

_default = None

def default_layouts():
    global _default
    if _default is None:
        overrides = None
        if os.path.exists(WIRE_OVERRIDE_PATH):
            try:
                with open(WIRE_OVERRIDE_PATH, 'r') as handle:
                    overrides = json.load(handle)
            except ValueError:
                overrides = None
        _default = Layouts(wire_overrides=overrides)
    return _default
