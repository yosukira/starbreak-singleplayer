'Aircraft movement reconstructed from the original 27 flight histories.'
import math

RISE_SPEED = 40000.0
TAKEOFF_RISE = 60000.0
FIRST_ATTACK_DELAY = 1.60
PATROL_PHASE_SECONDS = 5.1
HORIZONTAL_SPEEDS = {-1: 40000.0, 1: 39000.0}
TARGET_CLEARANCE = 60000.0
ACQUIRE_RANGE = 1000*256

def _distance(first, second):

    return math.hypot(first[0] - second[0], first[1] - second[1])

class AircraftMotion:
    def __init__(self, body):
        self.spawn_y = float(body.y)
        self.launch_top = self.spawn_y-TAKEOFF_RISE
        self.active = False
        self.started_at = None
        self.clear = False
        self.ready_at = math.inf
        self.goal_y = self.launch_top
        self.target_x = float(body.x+body.w/2)
        self.next_patrol_at = None
        self.pause_until = None
        self.direction = body.facing
        self.bounce_until = 0.0

    def _goal(self, body, player, initial=False):
        y = float(player.ymp)-TARGET_CLEARANCE
        return self._safe_height(body,min(self.launch_top,y) if initial else y)

    def _speed(self, body, value):
        """Per-map enemy-speed multiplier for the plane's own constants."""
        balance = getattr(body, 'balance', None)
        return balance.speed(value) if balance is not None else value

    def _safe_height(self,body,goal):

        for _,(x,y,w,h),kind in body.motion.surfaces():
            if kind!=1 or body.x>=x+w or body.x+body.w<=x:
                continue
            if goal<body.y and body.y>=y+h:
                goal=max(goal,y+h)
            elif goal>body.y and body.y+body.h<=y:
                goal=min(goal,y-body.h)
        return goal

    def step(self, body, dt, player, now):
        valid = player is not None and player.alive
        if (not self.active and valid and
                _distance(body.center(),player.center())<=ACQUIRE_RANGE and
                body.motion.visible(body.center(),player.center())):
            self.active=True
            self.started_at=now
            self.ready_at=now+FIRST_ATTACK_DELAY
            self.launch_top=self._safe_height(body,self.launch_top)
            self.goal_y=self._goal(body,player,initial=True)
            self.target_x = player.center()[0]
            self.direction=-1 if self.target_x<body.center()[0] else 1
        if not self.active:
            body.state,body.pose='aircraft-wait',1
            return
        if not self.clear:
            body.y = max(self.launch_top, body.y-self._speed(body, RISE_SPEED)*dt)
            body.state, body.pose = 'aircraft-takeoff', 3
            if body.y <= self.launch_top:
                self.clear = True
                if self.goal_y >= self.launch_top:

                    self.pause_until = now+.05
            return
        if self.pause_until is not None and now < self.pause_until:
            body.state, body.pose = 'aircraft-hover', 1
            return
        if self.next_patrol_at is not None and now >= self.next_patrol_at:

            self.direction *= -1
            self.next_patrol_at = now+PATROL_PHASE_SECONDS
            if valid and _distance(body.center(),player.center())<=ACQUIRE_RANGE:
                self.goal_y = self._goal(body,player)
                if abs(self.goal_y-body.y) > .001:

                    self.next_patrol_at = None
        delta_y = self.goal_y-body.y
        vertical = abs(delta_y) > .001
        if vertical:
            rise = self._speed(body, RISE_SPEED)
            body.y += max(-rise*dt, min(rise*dt, delta_y))
            body.state, body.pose = 'aircraft-height', 3
            if abs(self.goal_y-body.y) <= .001:
                self.next_patrol_at = now+PATROL_PHASE_SECONDS
        else:
            body.state, body.pose = 'aircraft-level', 2
            if self.next_patrol_at is None:
                self.next_patrol_at = now+PATROL_PHASE_SECONDS

        step=self.direction*self._speed(
            body, HORIZONTAL_SPEEDS[self.direction])*dt
        self._move_horizontal(body,step,now)
        body.facing = self.direction

    def _move_horizontal(self, body, distance, now):
        old_x = body.x
        body.x += distance

        for _, (x, y, w, h), kind in body.motion.surfaces():
            if kind != 1 or not (body.y < y+h and body.y+body.h > y):
                continue
            if distance > 0 and old_x+body.w <= x and body.x+body.w >= x:
                body.x = x-body.w
                self.direction, self.bounce_until = -1, now+.15
            elif distance < 0 and old_x >= x+w and body.x <= x+w:
                body.x = x+w
                self.direction, self.bounce_until = 1, now+.15
