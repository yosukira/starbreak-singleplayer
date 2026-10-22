"""SC motion in map units; shared convex geometry, no foreign creature AI."""
import math

from ..efb_run.physics import polygon, surface, swept_shape


def rect(row):
    return (*row['pos'], row['props'].get('wmp', 0), row['props'].get('hmp', 0))


def geometry(host):
    # Actors are live geometry: moving ledges and opening walls cannot use a
    # room-load-only collision cache. The controller refreshes once per slice.
    return [(oid, rect(row), int(row['props']['terraintype']),
             int(row['props'].get('shapetype', 0)))
            for oid, row in host.actors.items()
            if not row.get('dead') and row['props'].get('terraintype') in (1, 2)]


def clear(host, bounds, ignore=()):
    x, y, w, h = bounds
    for oid, r, kind, shape in geometry(host):
        if oid in ignore or kind != 1:
            continue
        if swept_shape(r, shape, (x, y), (x, y), (w, h),
                       penetrating=True, tolerance=1.) is not None:
            return False
    return True


def sweep(terrain, start, end, size, ignore=()):
    hits = [(t, oid) for oid, r, kind, shape in terrain
            if kind == 1 and oid not in ignore
            for t in [swept_shape(r, shape, start, end, size,
                                 penetrating=True, tolerance=1.)]
            if t is not None]
    return min(hits) if hits else None


def ground(terrain, row, state, dt, vx, gravity, *, drop=False):
    """Separate horizontal blocking, ceilings, and one-way/sloped landing."""
    x, y, w, h = rect(row)
    vy = state.get('vy', 0.)
    support = state.get('support')
    nx = x + vx * dt
    hit = sweep(terrain, (x, y), (nx, y), (w, h), (row['id'],))
    if hit:
        nx = x + (nx-x) * max(0., hit[0]-1e-7)
    ny = y + vy * dt + .5 * gravity * dt * dt
    state['vy'] = min(vy+gravity*dt, 220000.)
    state.update(grounded=False, support=None, blocked=hit is not None)
    if drop:
        # Preserve the old support until the body has really crossed it.
        state.setdefault('drop_support', support)
    if state.get('drop_support') is not None:
        old = next((r for oid, r, kind, shape in terrain
                    if oid == state['drop_support']), None)
        if old is None or y > old[1]+old[3]+1:
            state.pop('drop_support', None)
    floors = []
    ceilings = []
    for oid, r, kind, shape in terrain:
        if oid == row['id']:
            continue
        top = surface(r, shape, nx, w)
        if top is None:
            continue
        oldtop = surface(r, shape, x, w)
        if state['vy'] >= 0 and not (kind == 2 and oid == state.get('drop_support')):
            before = oldtop if oldtop is not None else top
            if y+h <= before+1 and ny+h >= top-1:
                floors.append((top, oid))
        if kind == 1 and vy < 0:
            bottom = surface(r, shape, nx, w, upper=False)
            if y >= bottom-1 and ny <= bottom:
                ceilings.append(bottom)
    if floors:
        top, oid = min(floors)
        ny = top-h
        state.update(vy=0., grounded=True, support=oid)
    elif ceilings:
        ny = max(ceilings)
        state['vy'] = 0.
    return (nx, ny)


def edge_ahead(terrain, row, state, direction, distance):
    x, y, w, h = rect(row)
    foot = x+w/2+direction*(w/2+distance)
    return not any(oid != row['id'] and r[0] <= foot <= r[0]+r[2]
                   and (top := surface(r, shape, foot, 1.)) is not None
                   and abs(top-y-h) < 24*256
                   for oid, r, kind, shape in terrain)


def flight(terrain, row, velocity, dt, ignore=()):
    x, y, w, h = rect(row)
    end = (x+velocity[0]*dt, y+velocity[1]*dt)
    hit = sweep(terrain, (x, y), end, (w, h), (row['id'], *ignore))
    if hit:
        f = max(0., hit[0]-1e-7)
        end = (x+(end[0]-x)*f, y+(end[1]-y)*f)
    return end, hit


def sinusoid(movement, origin, elapsed):
    """Native MOVE.sin: wire velocity is mp*256/ms, angle is fixed at birth."""
    s = movement['sin']
    ms = elapsed*1000+s.get('pos', 0)
    theta = math.radians(s.get('angle', 0))
    velocity = int(s.get('vel', 0))
    travel = (velocity >> 8)*ms
    period = max(1, s.get('period', 1000))
    phase = s.get('offset', 0)+math.trunc((travel/(period*256) if velocity else ms/period)*360)
    wave = s.get('amp', 0)*math.sin(math.radians(phase))
    return (s.get('xmp', origin[0])+travel*math.cos(theta)-wave*math.sin(theta),
            s.get('ymp', origin[1])+travel*math.sin(theta)+wave*math.cos(theta))
