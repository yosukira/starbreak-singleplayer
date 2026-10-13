"""Static Hulk Hard room model: the 72 real ``CreateWorld`` objects."""

import io
import json
import os
import struct

from .. import jsonio, pb_layout
from . import constants

_LAYOUTS = None

def _layouts():
    global _LAYOUTS
    if _LAYOUTS is None:
        _LAYOUTS = pb_layout.default_layouts()
    return _LAYOUTS

def _normalize_props(type_name, value):
    'Add the protobuf field suffixes the wire encoder expects.'
    if not isinstance(value, dict):
        return value
    fields = {}
    for spec in _layouts().types.get(type_name, {}).get('fields') or []:
        fields[spec['name']] = spec
    out = {}
    for key, item in value.items():
        name = key
        if name not in fields and name + '_' in fields:
            name = name + '_'
        spec = fields.get(name)
        if spec is not None and spec.get('item'):
            child = spec['item']
            if isinstance(item, dict):
                item = _normalize_props(child, item)
            elif isinstance(item, list):
                item = [_normalize_props(child, row)
                        if isinstance(row, dict) else row for row in item]
        out[name] = item
    return out

def _decode_create_world(path):
    with io.open(path, 'rb') as handle:
        data = handle.read()
    if path.endswith('.hex'):
        data = bytes(bytearray.fromhex(data.decode('ascii').strip()))
    if data[:1] == b'{':
        raise ValueError('expected a binary CreateWorld blob')
    size = struct.unpack('>I', data[:4])[0]
    body = data[4:]
    try:
        import lz4.block
        raw = lz4.block.decompress(body, uncompressed_size=size)
    except ImportError:

        from .. import lz4block
        raw = lz4block.decompress(body, size)
    return pb_layout.default_layouts().decode('CreateWorld', raw)

def _decode_literals(block, size):
    out = bytearray()
    offset = 0
    while offset < len(block) and len(out) < size:
        token = block[offset]
        offset += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = block[offset]
                offset += 1
                length += extra
                if extra != 255:
                    break
        out += block[offset:offset + length]
        offset += length
        if offset >= len(block):
            break
        distance = block[offset] | (block[offset + 1] << 8)
        offset += 2
        match = token & 15
        if match == 15:
            while True:
                extra = block[offset]
                offset += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        start = len(out) - distance
        for index in range(match):
            out.append(out[start + index])
    return bytes(out)

class RoomObject(object):
    """One retail world object with mutable props.

    ``rect()`` is called tens of thousands of times per tick (every falling
    body scans the standable surfaces and every projectile tests the room
    bounds), so the rectangle is cached and invalidated by ``set()``.  The
    room keeps a revision counter and caches ``bounds()`` on top of it.
    """

    __slots__ = ('objid', 'props', 'dirty', '_rect', 'room')

    def __init__(self, objid, props, room=None):
        self.objid = int(objid)
        self.props = dict(props or {})
        self.dirty = True
        self._rect = None
        self.room = room

    @property
    def vid(self):
        return self.props.get('vid_') or self.props.get('vid') or ''

    def get(self, name, default=None):
        if name in self.props:
            return self.props[name]
        return self.props.get(name.rstrip('_'), default)

    def set(self, **values):
        for key, value in values.items():
            self.props[key] = value
        self.dirty = True
        self._rect = None
        if self.room is not None:
            self.room.revision += 1

    def position(self):
        if self._rect is not None:
            return (self._rect[0], self._rect[1])
        return (int(self.get('xmp_', 0) or 0),
                int(self.get('ymp_', 0) or 0))

    def rect(self):
        rect = self._rect
        if rect is None:
            x, y = self.position()
            rect = (x, y,
                    int(self.get('wmp_', 0) or 0),
                    int(self.get('hmp_', 0) or 0))
            self._rect = rect
        return rect

    def create_props(self):
        return dict(self.props)

class AttachmentMap(dict):
    """Track structural/offset edits for the room's parent lookup cache."""
    version=0
    def __setitem__(self,key,value):
        super().__setitem__(key,value);self.version+=1
    def __delitem__(self,key):
        super().__delitem__(key);self.version+=1
    def update(self,*args,**kwargs):
        for key,value in dict(*args,**kwargs).items():self[key]=value
    def setdefault(self,key,default=None):
        if key not in self:self[key]=default
        return self[key]
    def pop(self,key,*default):
        if len(default)>1:raise TypeError('pop expected at most 2 arguments')
        if key in self:
            value=self[key];del self[key];return value
        return super().pop(key,*default)
    def popitem(self):
        value=super().popitem();self.version+=1;return value
    def clear(self):
        super().clear();self.version+=1
    def __ior__(self,values):self.update(values);return self

class BossRoom(object):
    'Real room geometry plus the runtime spawn templates.'

    def __init__(self, objects, templates, source=None):
        self.objects = {}

        self.revision = 0
        self._bounds = None
        self._bounds_revision = -1
        self._surfaces = None
        self._surfaces_revision = -1
        for item in objects:
            props = item.get('props_') or item.get('props') or {}
            self.objects[int(item['objid_'] if 'objid_' in item
                              else item['objid'])] = RoomObject(
                                  item.get('objid_', item.get('objid')), props,
                                  room=self)
        self.templates = templates or {}
        self.source = source
        self.ledges = [self.objects[i] for i in constants.LEDGE_IDS
                       if i in self.objects]
        self.gens = dict((i, self.objects[i]) for i in constants.GEN_IDS
                         if i in self.objects)
        self.panels = dict((i, self.objects[i]) for i in constants.PANEL_IDS
                           if i in self.objects)
        self._ensure_extra_turret()
        self.turrets = dict((i, self.objects[i]) for i in constants.TURRET_IDS
                            if i in self.objects)
        self.portals = dict((i, self.objects[i]) for i in constants.PORTAL_IDS
                            if i in self.objects)
        self.ship = self.objects.get(constants.SHIP_ID)
        self.attachments = AttachmentMap()
        for child in self.objects.values():
            movement = child.get('move_', {}) or {}
            parent_id = int((movement.get('relativeto_') or {}).get('objid_') or 0)
            parent = self.objects.get(parent_id)
            if parent is not None:
                cx, cy = child.position()
                px, py = parent.position()
                self.attachments[child.objid] = (parent_id, cx - px, cy - py)

    def _ensure_extra_turret(self):
        'Add object 62 (the seventh lower slot) when a capture lacks it.'
        extra = constants.EXTRA_TURRET_ID
        if extra in self.objects:
            return
        base = self.objects.get(54)
        ship = self.objects.get(constants.SHIP_ID)
        if base is None or ship is None:
            return
        from .turrets import row_offsets
        offset = row_offsets('lower', 7)[6]
        props = dict(base.props)
        ship_x, ship_y = ship.position()
        props['xmp_'] = ship_x + offset[0]
        props['ymp_'] = ship_y + offset[1]
        movement = dict(props.get('move_') or {})
        movement['relativeto_'] = {'objid_': constants.SHIP_ID}
        movement['to_'] = {'xmp_': int(offset[0]), 'ymp_': int(offset[1])}
        props['move_'] = movement
        props['draw_'] = False
        self.objects[extra] = RoomObject(extra, props)

    def add_turret(self, objid, props):
        """Register a created turret in the server room model.

        The client only receives the rolled turrets, but the server needs the
        same object for turret_center(), hit tests and the next ship Move.
        """
        objid = int(objid)
        item = RoomObject(objid, props)
        self.objects[objid] = item
        self.turrets[objid] = item
        movement = props.get('move_') or {}
        parent_id = int((movement.get('relativeto_') or {}).get('objid_') or 0)
        parent = self.objects.get(parent_id)
        if parent is not None:
            parent_x, parent_y = parent.position()
            item_x, item_y = item.position()
            self.attachments[objid] = (parent_id,
                                       item_x - parent_x,
                                       item_y - parent_y)
        return item

    @classmethod
    def from_capture(cls, create_world_path, templates_path=None, source=None):
        message = _decode_create_world(create_world_path)
        world = (message.get('worldstate_') or {}).get('serverworld_') or {}
        objects = world.get('obj_') or []
        templates = {}
        if templates_path and os.path.exists(templates_path):
            document, _ = jsonio.load_json(templates_path)
            for vid, rows in (document.get('templates') or {}).items():
                templates[vid] = [
                    row.get('create') or {} for row in rows
                    if row.get('create')]
        return cls(objects, templates, source or create_world_path)

    def template_props(self, vid, index=0, combat=False):
        rows = self.templates.get(vid) or []
        if combat:

            rows = [row for row in rows if int((row.get('props_') or
                    row.get('props') or {}).get('hitpoints_',
                    (row.get('props') or {}).get('hitpoints', 0)) or 0) > 0]
            if not rows:
                rows = [{'props_': item.create_props()}
                        for item in self.objects.values()
                        if item.vid == vid and int(item.get('hitpoints_', 0) or 0) > 0]
        if not rows:
            return None
        row = rows[min(index, len(rows) - 1)]
        props = row.get('props_') or row.get('props') or {}
        return _normalize_props('Properties', dict(props))

    def object(self, objid):
        return self.objects.get(int(objid))

    def move_object(self, objid, x, y):
        item = self.object(objid)
        if item is None:
            return
        item.set(xmp_=int(x), ymp_=int(y))

        table=self.attachments
        if isinstance(table,AttachmentMap):
            if getattr(self,'_attachment_index_source',None)is not table\
                    or getattr(self,'_attachment_index_version',None)!=table.version:
                index={}
                for child_id,(parent_id,dx,dy) in table.items():
                    index.setdefault(parent_id,{})[child_id]=(dx,dy)
                self._attachment_index=index;self._attachment_index_source=table
                self._attachment_index_version=table.version
            children=self._attachment_index.get(int(objid))
            if children:
                for child_id,(dx,dy) in children.items():self.move_object(child_id,x+dx,y+dy)
        else:

            for child_id,(parent_id,dx,dy) in table.items():
                if parent_id==int(objid):self.move_object(child_id,x+dx,y+dy)

    def place_attachment(self, objid, offset_x, offset_y):
        """Move an attached child to a new parent-relative offset.

        The rolled turret layout changes the relative Move offsets in the
        CreateWorld.  Update both the model position (so turret_center and
        targeting use the new spot) and the attachment table (so the next
        ship Move carries the turret with the same offset).
        """
        objid = int(objid)
        item = self.object(objid)
        if item is None:
            return None
        attachment = self.attachments.get(objid)
        if attachment is None:
            item.set(xmp_=int(offset_x), ymp_=int(offset_y))
            return (int(offset_x), int(offset_y))
        parent_id = attachment[0]
        parent = self.object(parent_id)
        if parent is None:
            return None
        parent_x, parent_y = parent.position()
        movement = dict(item.get('move_') or {})
        movement['relativeto_'] = {'objid_': parent_id}
        movement['to_'] = {'xmp_': int(offset_x), 'ymp_': int(offset_y)}
        item.set(move_=movement)
        item.set(xmp_=parent_x + int(offset_x), ymp_=parent_y + int(offset_y))
        self.attachments[objid] = (parent_id, int(offset_x), int(offset_y))
        return (int(offset_x), int(offset_y))

    def bounds(self):
        if self._bounds is not None and self._bounds_revision == self.revision:
            return self._bounds
        min_x = min_y = None
        max_x = max_y = None
        for item in self.objects.values():
            x, y, w, h = item.rect()
            right, bottom = x + w, y + h
            if min_x is None or x < min_x:
                min_x = x
            if min_y is None or y < min_y:
                min_y = y
            if max_x is None or right > max_x:
                max_x = right
            if max_y is None or bottom > max_y:
                max_y = bottom
        if min_x is None:
            result = (0, 0, 0, 0)
        else:
            result = (min_x, min_y, max_x - min_x, max_y - min_y)
        self._bounds = result
        self._bounds_revision = self.revision
        return result

    def surfaces(self):
        """Static standable/blocking rectangles, cached per revision."""
        if self._surfaces is not None and self._surfaces_revision == self.revision:
            return self._surfaces
        from .terrain import terrain_type
        rows = []
        for item in self.objects.values():
            kind = terrain_type(item)
            if kind in (1, 2):
                x, y, w, h = item.rect()
                if w > 0 and h > 0:
                    rows.append((item.objid, (x, y, w, h), kind))
        self._surfaces = rows
        self._surfaces_revision = self.revision
        return rows

    def turret_center(self, objid):
        item = self.turrets.get(int(objid))
        if item is None:
            return None
        x, y, w, h = item.rect()
        return (x + w // 2, y + h // 2)

    def surface_map(self):
        """Share the same immutable geometry lookup across ground bodies."""
        if getattr(self,'_surface_map_revision',None)!=self.revision:
            self._surface_map={objid:(rect,kind)for objid,rect,kind in self.surfaces()}
            self._surface_map_revision=self.revision
        return self._surface_map

    def portal_spawn(self, objid, height=None, width=None):
        'Original spawn marker for one portal (top-left corner).'
        item = self.portals.get(int(objid))
        if item is None:
            return None
        if height is None:
            height = constants.SPAWN_REFERENCE_HEIGHT
        if width is None:
            width = constants.SPAWN_DEFAULT_SIZE[0]
        x, y, w, h = item.rect()
        return (x + w // 2 - int(width) // 2,
                y + h - int(height))
