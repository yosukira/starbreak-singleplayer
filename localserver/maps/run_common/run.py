"""Transport-independent persistent room state for native full levels.

Unoccupied runtimes pause (SIM). Nothing is rebuilt on a room revisit.
"""
import copy
import hashlib
import random

def room_seed(seed, role):
    value = hashlib.sha256(('%s:%s' % (seed, role)).encode('ascii')).digest()
    return int.from_bytes(value[:4], 'big') & 0x7fffffff

class RoomInstance:
    def __init__(self, recipe, seed):
        self.recipe = recipe
        self.role = recipe['role']
        self.seed = seed
        self.runtime = None
        self.snapshot = None
        self.visits = 0
        self.flags = {}

    def document(self, viewer=2):
        if self.snapshot is not None:
            return self.snapshot.document(viewer)
        return copy.deepcopy(self.recipe['world'])

class LevelRun:
    def __init__(self, pack, seed=None):
        self.pack = pack
        self.seed = random.SystemRandom().getrandbits(63) if seed is None else int(seed)
        self.rooms = {}
        self.current, self.entry = 'entry', 'default'
        self.completed = False
        self.defeated = set()
        self.transitions = 0
        self.player, self.events = {}, []

    def room(self, role=None):
        role = role or self.current
        if role not in self.rooms:
            seed = room_seed(self.seed, role)
            recipe = self.pack.room(role, seed)
            self.rooms[role] = RoomInstance(recipe, seed)
        return self.rooms[role]

    def mark_defeated(self, role):
        if role in self.defeated:
            return False
        self.defeated.add(role)
        if role == 'boss':
            self.completed = True
        self.events.append(dict(kind='encounter_defeated', room=role))
        return True

    def allows(self, link, role=None):
        if link.get('trigger', link.get('interaction')) == 'disabled':
            return False
        required = link.get('requires')
        if required is None or required is False or required == '':
            return True
        requirements = required if isinstance(required, (list, tuple)) else [required]
        for key in requirements:
            if not isinstance(key, str):
                raise ValueError('Unsupported level requirement: '+str(key))
            if key.startswith('encounter:'):
                passed = key.split(':', 1)[1] in self.defeated or bool(self.room(role).flags.get(key))
            elif key.endswith(('_defeated', '_cleared')):
                passed = key.rsplit('_', 1)[0] in self.defeated
            else:
                passed = bool(self.room(role).flags.get(key))
            if not passed:
                return False
        return True

    def destination_spawn(self, role, entry='default'):
        recipe = self.room(role).recipe
        entries = recipe.get('entries', recipe.get('anchors', {}))
        value = entries.get(entry, entries.get('default', recipe['spawn']))
        if isinstance(value, dict):
            value = value.get('spawn', value.get('position', value.get('pos')))
        if value is None or len(value) != 2:
            raise ValueError('Invalid spawn for %s:%s' % (role, entry))
        return tuple(int(v) for v in value)

    def move(self, link, player=None):
        if not self.allows(link):
            return False
        role, entry = link['destination'], link.get('entry', 'default')
        if role not in self.pack.room_names:
            raise ValueError('External destination is handled by the host: ' + role)
        self.destination_spawn(role, entry)
        if player is not None:
            self.player = dict(getattr(player, 'raw', {}) or {})
            self.player.update(hp=player.hp, maxhp=player.maxhp, facing=player.facing,
                               wmp=player.wmp, hmp=player.hmp)
        old = self.current
        self.current, self.entry = role, entry
        self.transitions += 1
        self.events.append(dict(kind='room', source=old, destination=role, entry=entry))
        return True

    def status(self):
        return dict(seed=self.seed, room=self.current, entry=self.entry,
                    completed=self.completed, defeated=sorted(self.defeated),
                    transitions=self.transitions,
                    visited={role:room.visits for role,room in self.rooms.items()},
                    flags={role:room.flags.copy() for role,room in self.rooms.items() if room.flags})
