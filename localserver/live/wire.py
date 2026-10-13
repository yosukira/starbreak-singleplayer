"""Retail ``Delta`` message builders for the live controller."""

import base64

from .. import pb_layout

PROPERTY = {
    'XMP': 1, 'YMP': 4, 'WMP': 7, 'HMP': 9, 'Z': 11, 'FACING': 13, 'LIFETIME': 14,
    'BLOCKTYPE': 15, 'OWNERTYPE': 16, 'DAMAGE': 17, 'HITBOXDAMAGE': 18,
    'HITPOINTS': 19, 'MAXHITPOINTS': 20, 'REMOVETYPE': 21,
    'PLAYERDATA': 23, 'TERRAINTYPE': 24, 'INTERACT': 25, 'DRAW': 28,
    'DECORATE': 29, 'INVULNERABLE': 31, 'ARMOR': 34, 'MOVE': 41,
    'MOVE_TO': 42, 'VID': 43, 'DRAWMODS': 44, 'ROTATE': 45, 'POSE': 46,
    'POSETIME': 47, 'ANIMPROPS': 49, 'SOUNDPREFIX': 51,
}

DESTROY_REASON = {
    'INTERNAL': 1, 'HITPOINTS': 2, 'SHIELD': 3, 'DAMAGES': 4, 'BLOCKED': 5,
    'LIFETIME': 6, 'CANCEL': 7,
}

class DeltaBatch(object):
    """Accumulates one ``MT_SERVER_DELTA`` payload."""

    def __init__(self, layouts=None, worldid=1, dt=0):
        self.layouts = layouts or pb_layout.default_layouts()
        self.worldid = int(worldid)
        self.dt = int(dt)
        self.general = []
        self.objects = []
        self.extra = {}

    def create(self, objid, props, creatorid=0, offset=0, t=None):
        update = {
            'offset_': int(offset),
            'create_': {
                'objid_': int(objid),
                'props_': dict(props),
            },
        }
        if creatorid:
            update['create_']['creatorid_'] = int(creatorid)
        if t is not None:
            update['t_'] = int(t)
        self.general.append(update)
        return update

    def destroy(self, objid, ownerid=0, reason='LIFETIME', offset=0):
        self.general.append({
            'offset_': int(offset),
            'destroy_': {
                'objid_': int(objid),
                'ownerid_': int(ownerid),
                'reason_': _reason(reason),
            },
        })

    def damage(self, ownerid, objid, value, iscrit=False, mitigated=False,
               x=0, y=0, fx=None, offset=0):
        message = {
            'offset_': int(offset),
            'damage_': {
                'ownerid_': int(ownerid),
                'objid_': int(objid),
                'val_': int(value),
            },
        }
        damage = message['damage_']
        if iscrit:
            damage['iscrit_'] = True
        if mitigated:
            damage['armormitigated_'] = True
        if x or y:
            damage['x_'] = int(x)
            damage['y_'] = int(y)
        if fx:
            damage['damagefxvid_'] = str(fx)
        self.general.append(message)

    def shake(self, magnitude, duration=None, offset=0):
        payload = {'magnitude_': int(magnitude)}
        if duration is not None:
            payload['duration_'] = int(duration)
        self.general.append({
            'offset_': int(offset),
            'screenshake_': payload,
        })

    def sound(self, objid, name, offset=0):
        self.objects.append({
            'objid_': int(objid),
            'offset_': int(offset),
            'playsound_': {'soundname_': str(name)},
        })

    def world_sound(self, name, offset=0):
        """A general (world-level) sound, e.g. the phase-change roar."""
        self.general.append({
            'offset_': int(offset),
            'playsound_': {'soundname_': str(name)},
        })

    def modify(self, objid, prop, intval=None, boolval=None, stringval=None,
               message=None, offset=0):
        modify = {'property_': _property(prop)}
        if intval is not None:
            modify['intval_'] = int(intval)
        if boolval is not None:
            modify['boolval_'] = bool(boolval)
        if stringval is not None:
            modify['stringval_'] = str(stringval)
        if message is not None:
            if isinstance(message, (bytes, bytearray)):
                blob = bytes(message)
            else:
                blob = self.layouts.encode(str(message[0]), message[1])
            modify['messageval_'] = {
                'raw': base64.b64encode(blob).decode('ascii'),
            }
        self.objects.append({
            'objid_': int(objid),
            'offset_': int(offset),
            'modifyprops_': modify,
        })
        return modify

    def animprops(self, objid, animid, time_ms, loop=False, offset=0):

        params = {'animid_': int(animid), 'time_': int(time_ms)}
        if loop:
            params['loop_'] = True
        return self.modify(objid, 'ANIMPROPS',
                           message=('AnimationProperties', params), offset=offset)

    def move_to(self, objid, xmp=None, ymp=None, dt=None, offset=0):
        update = {'objid_': int(objid), 'offset_': int(offset)}
        if xmp is not None:
            update['movetoxmp_'] = int(xmp)
        if ymp is not None:
            update['movetoymp_'] = int(ymp)
        if dt is not None:
            update['movetodt_'] = int(dt)
        self.objects.append(update)

    def linear_motion(self, objid, start, end, duration_ms, offset=0):
        """A complete native interpolation, including its explicit origin."""
        return self.modify(objid, 'MOVE', message=('Properties_Move', {
            'to_': {'xmp_': int(end[0]), 'ymp_': int(end[1]),
                    'dt_': max(1, int(duration_ms)), 'pos_': 0,
                    'start_': {'xmp_': int(start[0]), 'ymp_': int(start[1])}}
        }),offset=offset)

    def props(self, objid, values, offset=0):
        for name, value in values.items():
            if isinstance(value, bool):
                self.modify(objid, name, boolval=value, offset=offset)
            elif isinstance(value, int):
                self.modify(objid, name, intval=value, offset=offset)
            elif isinstance(value, str):
                self.modify(objid, name, stringval=value, offset=offset)
            else:
                self.modify(objid, name, message=value, offset=offset)

    def __len__(self):
        return len(self.general) + len(self.objects)

    def empty(self):
        return not self.general and not self.objects and not self.extra

    def message(self):
        world = {'worldid_': self.worldid}
        if self.dt:
            world['dt_'] = self.dt
        if self.general:
            world['generalupdates_'] = self.general
        if self.objects:
            world['objectupdates_'] = self.objects
        message = dict(self.extra)
        message['worldupdate_'] = world
        return message

    def encode(self):
        return self.layouts.encode('Delta', self.message())

    def clear(self):
        self.general = []
        self.objects = []
        self.extra = {}

def _property(value):
    if isinstance(value, int):
        return value
    return PROPERTY.get(str(value).upper().replace('PROPERTY_', ''), 0)

def _reason(value):
    if isinstance(value, int):
        return value
    text = str(value).upper().replace('DESTROYREASON_', '')
    return DESTROY_REASON.get(text, DESTROY_REASON['LIFETIME'])

def static_create(objid, props, creatorid=0):
    """Single-create ``Delta`` used by unit tests and bootstrapping."""
    batch = DeltaBatch()
    batch.create(objid, props, creatorid=creatorid)
    return batch
