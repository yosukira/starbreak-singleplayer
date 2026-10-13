"""Arena geometry and the native relative-object motion contract."""

import heapq

def wall_normal(props, bounds):
    """Inward normal of a crush wall; all four walls have equal dimensions."""
    x, y = props['xmp'], props['ymp']
    w, h = props['wmp'], props['hmp']
    faces = [(abs(x + w - bounds[0]), (1, 0)),
             (abs(x - bounds[2]), (-1, 0)),
             (abs(y + h - bounds[1]), (0, 1)),
             (abs(y - bounds[3]), (0, -1))]
    return min(faces, key=lambda row: row[0])[1]

def swept_hit(x, y, tx, ty, rectangle, radius=0):
    """First contact of a swept projectile with an expanded terrain box."""
    lo, hi = 0.0, 1.0
    for origin, delta, lower, upper in (
            (x, tx - x, rectangle[0] - radius, rectangle[2] + radius),
            (y, ty - y, rectangle[1] - radius, rectangle[3] + radius)):
        if abs(delta) < 1e-12:
            if origin < lower or origin > upper:
                return None
            continue
        a, b = (lower - origin) / delta, (upper - origin) / delta
        lo, hi = max(lo, min(a, b)), min(hi, max(a, b))
        if lo > hi:
            return None
    return lo if 0.0 <= lo <= 1.0 else None

class ArenaMotion(object):
    """Mirror Properties_Move locally for collision with moving walls.

    The wire uses relative claw/wall links.  Keeping the same links here
    lets server collision follow the native client's interpolated geometry.
    """

    def __init__(self, pack, boss_position):
        self.pack = pack
        self.boss_position = boss_position
        self.tracks = {}
        self.queue = []
        self.seq = 0

    def position(self, objid, now, visited=None, tracks=None):
        objid = int(objid)
        if objid == self.pack.boss_objid:
            return tuple(self.boss_position)
        visited = set(visited or ())
        if objid in visited:
            raise ValueError('cyclic arena attachment %s' % objid)
        visited.add(objid)
        props = self.pack.template(objid)
        tracks=self.tracks if tracks is None else tracks
        track = tracks.get(objid)
        move = track['move'] if track else props.get('move') or {}
        to = move.get('to') or {}
        parent = (move.get('relativeto') or {}).get('objid')
        if parent is not None:
            px, py = self.position(parent, now, visited, tracks)
            target = (px + int(to.get('xmp') or 0),
                      py + int(to.get('ymp') or 0))
            lerp = move.get('lerp') or {}
            target_id = (lerp.get('target') or {}).get('objid')
            if target_id is not None:
                tx, ty = self.position(target_id, now, visited, tracks)
                amount = float(lerp.get('percentage') or 0) / 100.0
                if lerp.get('center'):
                    start_props = self.pack.template(parent)
                    end_props = self.pack.template(target_id)
                    px += (start_props.get('wmp') or 0) / 2.0
                    py += (start_props.get('hmp') or 0) / 2.0
                    tx += (end_props.get('wmp') or 0) / 2.0
                    ty += (end_props.get('hmp') or 0) / 2.0
                    target = (px + (tx-px)*amount - (props.get('wmp') or 0)/2.0,
                              py + (ty-py)*amount - (props.get('hmp') or 0)/2.0)
                else:
                    target = (px + (tx-px)*amount, py + (ty-py)*amount)
        else:
            target = (int(to.get('xmp', props.get('xmp') or 0)),
                      int(to.get('ymp', props.get('ymp') or 0)))
        if not track:
            return target
        duration = float(to.get('dt') or 0) / 1000.0
        amount = min(1.0, max(0.0, (now - track['start']) / duration))\
            if duration else 1.0
        return tuple(a + (b - a) * amount
                     for a, b in zip(track['from'], target))

    def projected_position(self, objid, when):
        """Preview already scheduled legs without advancing the live mirror.

        Spike lookahead spans only one interpolation step. Wall legs can
        start inside that step, so the current track alone is insufficient.
        """
        objid=int(objid)
        legs=sorted(r for r in self.queue if r[2]==objid and r[0]<=when)
        if not legs:return self.position(objid,when)
        tracks=dict(self.tracks)
        for at,_seq,_obj,move,hold in legs:
            origin=self.position(objid,at,tracks=tracks)
            if hold:move=dict(to=dict(xmp=origin[0],ymp=origin[1],dt=0))
            tracks[objid]=dict(start=at,move=move,**{'from':origin})
        return self.position(objid,when,tracks=tracks)

    def schedule(self, when, objid, move, hold=False):
        self.seq += 1
        heapq.heappush(self.queue,
                       (float(when), self.seq, int(objid), move, bool(hold)))

    def cancel(self, objid):
        """Keep the current pose, discard an older action's future legs."""
        self.queue = [row for row in self.queue if row[2] != int(objid)]
        heapq.heapify(self.queue)

    def retime(self, objid, old_at, new_at):
        """Move one pending wall-home command after the last hand returns."""
        self.queue = [(new_at,seq,oid,move,hold) if oid==int(objid)
                      and abs(at-old_at)<1e-8 else (at,seq,oid,move,hold)
                      for at,seq,oid,move,hold in self.queue]
        heapq.heapify(self.queue)

    def due(self, now):
        result = []
        while self.queue and self.queue[0][0] <= now + 1e-9:
            when, _seq, objid, move, hold = heapq.heappop(self.queue)
            origin = self.position(objid, when)
            if hold:

                move = {'to': {'xmp': int(round(origin[0])),
                               'ymp': int(round(origin[1])), 'dt': 0}}
            self.tracks[objid] = dict(start=when, move=move, **{'from': origin})
            result.append((objid, None if hold else move))
        return result
