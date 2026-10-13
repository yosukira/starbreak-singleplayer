"""Evaluate the retail movement messages for server-side encounter geometry.

The client still plays the original Move message. This copy only keeps the
positions used by targeting, portals and interaction in the same coordinate
space as the moving ship, rather than using its initial off-screen position.
"""

import bisect

class MotionTrack(object):
    def __init__(self, start, movement):
        self.points = [(0.0, float(start[0]), float(start[1]))]
        elapsed = 0.0
        waypoint = movement.get('waypoint_') or {}
        points = waypoint.get('points_') or []
        if not points and movement.get('to_'):
            points = [movement['to_']]
        for point in points:
            elapsed += max(0, int(point.get('dt_') or 0)) / 1000.0
            self.points.append((elapsed, float(point.get('xmp_', start[0])),
                                float(point.get('ymp_', start[1]))))
        self.times = [point[0] for point in self.points]

    def position(self, elapsed):
        index = bisect.bisect_right(self.times, max(0.0, elapsed))
        if index >= len(self.points):
            return tuple(int(round(v)) for v in self.points[-1][1:])
        before, after = self.points[index - 1], self.points[index]
        duration = after[0] - before[0]
        fraction = (elapsed - before[0]) / duration if duration else 1.0
        return tuple(int(round(before[i] + (after[i] - before[i]) * fraction))
                     for i in (1, 2))

    def native_movement(self,elapsed=0.):
        """Retain a whole leg for the native interpolator, including its cursor.

        Replacing a 50 ms MoveTo every packet repeatedly restarts platform
        motion. A waypoint leg has the same measured trajectory and clock, and
        late join snapshots already advance its first Linear.pos/start.
        """
        elapsed=max(0.,elapsed);index=bisect.bisect_right(self.times,elapsed)
        if index>=len(self.points):
            x,y=self.position(elapsed)
            return dict(to_=dict(xmp_=x,ymp_=y,dt_=1,pos_=0,start_=dict(xmp_=x,ymp_=y)))
        result=[]
        for i in range(index,len(self.points)):
            before,after=self.points[i-1],self.points[i]
            point=dict(xmp_=int(round(after[1])),ymp_=int(round(after[2])),
                dt_=max(1,round((after[0]-before[0])*1000)))
            if i==index:point.update(pos_=round((elapsed-before[0])*1000),
                start_=dict(xmp_=round(before[1]),ymp_=round(before[2])))
            result.append(point)
        return dict(waypoint_=dict(points_=result))

class LoopingTrack(object):
    """Join a closed patrol smoothly, then keep it running indefinitely."""
    def __init__(self, start, patrol, join_seconds=2.0):
        self.start = tuple(start)
        self.patrol = patrol
        self.join_seconds = float(join_seconds)

    def position(self, elapsed):
        if elapsed < self.join_seconds:
            fraction = max(0.0, elapsed) / self.join_seconds
            return tuple(int(round(a + (b-a)*fraction))
                         for a, b in zip(self.start, self.patrol.points[0][1:]))
        duration = self.patrol.times[-1]
        return self.patrol.position((elapsed-self.join_seconds) % duration)
