"""Per-item projectile speed overrides, applied at the next client login.

Keep captured DescribeItem bytes intact except for the selected Obj.speed.
In particular, do not discard unknown fields through a decode/encode roundtrip.
"""
from .pb_layout import _read_varint, _varint

KEY = '_projectile_speeds'
MAX_SPEED = 1000000


def normalize(rows):
    if not isinstance(rows, dict):
        raise ValueError('弹速配置必须是对象')
    result = {}
    for item, objects in rows.items():
        if not str(item).isdigit() or not isinstance(objects, dict):
            raise ValueError('弹速配置中的物品 ID 或弹体配置无效')
        speeds = {}
        for index, speed in objects.items():
            # Retail movement equipment (Boosters/Gliders) uses signed speeds.
            if not str(index).isdigit() or type(speed) is not int or not -MAX_SPEED <= speed <= MAX_SPEED:
                raise ValueError('弹速必须是 -1000000–1000000 的整数，弹体索引必须非负')
            speeds[str(int(index))] = speed
        if speeds:
            result[str(int(item))] = speeds
    return result


def fields(blob):
    """Yield wire fields with byte spans and length-delimited contents."""
    offset = 0
    while offset < len(blob):
        start = offset
        tag, offset = _read_varint(blob, offset)
        wire = tag & 7
        value = None
        if wire == 0:
            _, offset = _read_varint(blob, offset)
        elif wire in (1, 5):
            offset += 8 if wire == 1 else 4
        elif wire == 2:
            size, offset = _read_varint(blob, offset)
            value = blob[offset:offset + size]
            offset += size
        else:
            raise ValueError('Unsupported DescribeItem wire type')
        if not tag >> 3 or offset > len(blob):
            raise ValueError('Truncated DescribeItem field')
        yield tag >> 3, start, offset, value


def patch_payload(layouts, payload, description, speeds):
    """Replace only explicitly supported object speeds; all other bytes survive."""
    if not speeds:
        return payload
    objects = description.get('obj_', [])
    for key in speeds:
        index = int(key)
        if index >= len(objects) or 'speed_' not in objects[index]:
            raise ValueError('物品 {0} 的弹体 {1} 没有可编辑的基础速度'.format(
                description.get('type_'), index))
    obj_spec = layouts.resolve_name('ItemDescription', 'obj_')
    speed_spec = layouts.resolve_name(obj_spec['item'], 'speed_')
    chunks = []
    index = 0
    for number, start, end, value in fields(payload):
        if number == obj_spec['number']:
            key = str(index)
            index += 1
            if key in speeds:
                obj = b''.join(value[a:b] for field, a, b, _ in fields(value)
                               if field != speed_spec['number'])
                obj += layouts.encode(obj_spec['item'], {'speed_': speeds[key]})
                # raw message support is for encoded children; use the codec's
                # wire helpers to wrap bytes without decoding any other field.
                chunks.append(_varint((number << 3) | 2) + _varint(len(obj)) + obj)
                continue
        chunks.append(payload[start:end])
    return b''.join(chunks)
