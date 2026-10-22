"""SC mechanism geometry and native motion primitives; no encounter policy."""
import copy
import math

from ..efb_run.abilities import NativeTrack, center, nearest, rect, changed, move, prop, swept


def clear(host, oid, name):
    row = host.actors.get(oid)
    if row is None or name not in row['props']:
        return
    row['props'].pop(name, None)
    host.batch.modify(oid, name.upper(), offset=host._offset())
    if name in ('terraintype', 'wmp', 'hmp', 'shapetype'):
        changed(host)


def set_motion(host, oid, movement):
    row = host.actors[oid]
    row['props']['move'] = copy.deepcopy(movement)
    host.batch.modify(oid, 'MOVE', message=('Properties_Move', host.normalize('Properties_Move', movement)),
                      offset=host._offset())


def stop(host, oid):
    clear(host, oid, 'move')
    row = host.actors.get(oid)
    if row:
        row['vel'] = [0., 0.]


def place(host, oid, point):
    """Stop then correct a settled body immediately, without an interpolation.

    A MoveTo followed by clear MOVE at the same wire offset freezes its old
    interpolation origin. Native XMP/YMP corrections retain the intended final
    position both in the live client and the reconnect snapshot.
    """
    row = host.actors.get(oid)
    if row is None:
        return
    stop(host, oid)
    row['pos'] = [round(point[0]), round(point[1])]
    for field, value in zip(('xmp', 'ymp'), row['pos']):
        row['props'][field] = value
        host.batch.modify(oid, field.upper(), intval=value, offset=host._offset())
    if row['props'].get('terraintype') in (1, 2):
        changed(host)


def animate(host, oid, anim, duration, sound=None):
    row = host.actors.get(oid)
    if row is None:
        return
    row['props']['animprops'] = dict(animid=anim, time=duration)
    host.batch.animprops(oid, anim, duration, offset=host._offset())
    if sound:
        host.batch.sound(oid, sound, offset=host._offset())


class MechanismTrack(NativeTrack):
    """Native MOVE including the sine lane used by engine fire.

    Sine travel deliberately matches WorldSnapshot's integer wire-velocity
    truncation. Relative-to placement is resolved by the owning mechanism.
    """
    def position(self, clock_ms):
        s = self.movement.get('sin')
        if not s:
            return super().position(clock_ms)
        progress = max(0, clock_ms-self.started_ms)+s.get('pos', 0)
        angle = math.radians(s.get('angle', 0))
        period = max(1, s.get('period', 1000))
        velocity = int(s.get('vel', 0))
        travel = (velocity >> 8)*progress
        phase = s.get('offset', 0)+math.trunc((travel/(period*256) if velocity else progress/period)*360)
        wave = s.get('amp', 0)*math.sin(math.radians(phase))
        return (s.get('xmp', self.origin[0])+travel*math.cos(angle)-wave*math.sin(angle),
                s.get('ymp', self.origin[1])+travel*math.sin(angle)+wave*math.cos(angle))


def distance_to_rect(point, bounds):
    x, y, w, h = bounds
    return math.hypot(max(x-point[0], 0, point[0]-x-w), max(y-point[1], 0, point[1]-y-h))


def overlaps(a, b, inset=0):
    x, y, w, h = a
    bx, by, bw, bh = b
    return x+w > bx+inset and x < bx+bw-inset and y+h > by+inset and y < by+bh-inset


def legal_book_sites(host, point, size, ignore=(), radius_cells=24):
    """Nearby vacant 32px grid sites, ordered by distance.

    Books are authored floating terrain; requiring a floor underneath would
    incorrectly erase the library's airborne stepping stones.
    """
    grid = 32*256
    sx, sy = round(point[0]/grid)*grid, round(point[1]/grid)*grid
    obstacles = [rect(r) for oid, r in host.actors.items()
                 if oid not in ignore and r['props'].get('terraintype') in (1, 2)]
    candidates = []
    # The configured radius is the ordinary work budget, not permission to
    # lose a carried block on death. If entirely obstructed, continue out to
    # the finite room geometry's nearest free perimeter.
    perimeter = 1+math.ceil(max((max(abs(x-sx), abs(x+w-sx), abs(y-sy), abs(y+h-sy))
                                for x, y, w, h in obstacles), default=0)/grid)
    limit = max(radius_cells, perimeter)
    for ring in range(limit+1):
        for dx in range(-ring, ring+1):
            for dy in range(-ring, ring+1):
                if max(abs(dx), abs(dy)) != ring:
                    continue
                x, y = sx+dx*grid, sy+dy*grid
                if not any(overlaps((x, y, *size), o, .5) for o in obstacles):
                    candidates.append((x, y))
        if len(candidates) >= 8:
            break
        if ring >= radius_cells and candidates:
            break
    return sorted(candidates, key=lambda p: math.dist(point, p))
