"""Native wire normalization and motion units for Underground Base maps."""
import copy
from ... import pb_layout
VELOCITY_TO_MP_S=1000./256.

def motion_position(start, move, elapsed):
    """Native linear/waypoint/velocity position, in mp and seconds."""
    if not move:
        return tuple(start)
    vel = move.get('vel')
    if vel:
        return tuple(start[j] + vel.get(v, 0)*VELOCITY_TO_MP_S*elapsed
                     + .5*vel.get(a, 0)*3906.25*elapsed*elapsed
                     for j, v, a in ((0, 'xvel', 'xaccel'), (1, 'yvel', 'yaccel')))
    points = (move.get('waypoint') or {}).get('points')
    if points is None:
        points = [move['to']] if 'to' in move else []
    position = tuple(start)
    remaining = max(0., elapsed)
    for point in points:
        end = (point.get('xmp', position[0]), point.get('ymp', position[1]))
        seconds = max(0., point.get('dt', 0)/1000.)
        if seconds > 0 and remaining < seconds:
            factor = remaining/seconds
            return tuple(a+(b-a)*factor for a,b in zip(position, end))
        position = end
        remaining -= seconds
    return position

def normalize(type_name, value, layouts=None):
    layouts = layouts or pb_layout.default_layouts()
    if isinstance(value, dict) and 'value' in value and 'name' in value:
        return value['value']
    if not isinstance(value, dict):
        return value
    fields = {f['name']: f for f in layouts.types.get(type_name, {}).get('fields', [])}
    result = {}
    for key, item in value.items():
        name = key if key in fields else key + '_'
        if name not in fields:
            raise ValueError('unknown %s field %s' % (type_name, key))
        child = fields[name].get('item')
        if child:
            item = ([normalize(child, row, layouts) for row in item]
                    if isinstance(item, list) else normalize(child, item, layouts))
        result[name] = item
    return result
