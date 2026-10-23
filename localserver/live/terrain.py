"""Collision surfaces shared by ground enemies and the native room model."""
import math

def terrain_type(item):
    value = item.get('terraintype_', 0) or 0
    return int(value.get('value', 0) if isinstance(value, dict) else value)

class GroundMotion(object):
    """A body falls onto surfaces, rides them, and loses support on retraction."""
    def __init__(self, room, gravity=340000.0, terminal_speed=160000.0):
        self.room = room
        self.gravity = float(gravity)
        self.vy = 0.0
        self.support = None
        self.support_rect = None
        self.grounded = False
        self.landing_serial = 0
        self.blocked = False
        self.drop_until = 0.0
        self.edge_blocked = False
        self.jumping = False
        self.requested_vx = 0.0
        self.vx = 0.0
        self.passive = False

        self.walked_off = False
        self.carry_velocity = (0.0, 0.0)

        self.terminal_speed = float(terminal_speed)
        self.previous_surfaces = {i:(r,k) for i,r,k in self.surfaces()}

    def surfaces(self):
        if self.room is None:
            return []

        cached = getattr(self.room, 'surfaces', None)
        if callable(cached):
            return cached()
        return [(item.objid, item.rect(), terrain_type(item))
                for item in self.room.objects.values()
                if terrain_type(item) in (1, 2) and item.rect()[2] > 0
                and item.rect()[3] > 0]

    def step(self, body, dt, vx, now, avoid_edges=False):
        surfaces = self.surfaces()
        lookup=getattr(self.room,'surface_map',None)
        by_id = lookup() if callable(lookup) else {objid: (rect, kind) for objid, rect, kind in surfaces}
        start_x,start_y = body.x,body.y
        was_grounded = self.grounded
        self.requested_vx = vx
        old_support = by_id.get(self.support)
        carry_x,carry_y = self.carry_velocity
        item = self.room.objects.get(self.support) if self.room and self.support is not None else None
        if item is not None and self.support_rect and dt>0:
            rect = item.rect()
            carry_x = (rect[0]-self.support_rect[0])/dt
            carry_y = (rect[1]-self.support_rect[1])/dt
        if old_support and self.support_rect and now >= self.drop_until:
            rect = old_support[0]
            body.x += rect[0] - self.support_rect[0]
            body.y += rect[1] - self.support_rect[1]
        else:
            if was_grounded:

                vx += carry_x
                self.vy = carry_y
                self.passive = True
            self.support = None
            self.grounded = False
        old_x, old_y = body.x, body.y
        body.x += vx * dt
        self.vx = vx
        self.blocked = False
        self.edge_blocked = False
        if avoid_edges and old_support and self.grounded and vx:

            left, top, width, _ = old_support[0]
            right = left+width
            changed = True
            while changed:
                changed = False
                for _, (x, y, w, _), _ in surfaces:
                    if abs(y-top) > 1 or x > right or x+w < left:
                        continue
                    a, b = min(left, x), max(right, x+w)
                    if (a, b) != (left, right):
                        left, right, changed = a, b, True

            center = body.x+body.w/2
            limit = right-1 if vx > 0 else left+1
            if (vx > 0 and center > limit) or (vx < 0 and center < limit):
                body.x = limit-body.w/2
                self.edge_blocked = True

        for objid, rect, kind in surfaces:
            x, y, w, h = rect
            if kind != 1 or not (body.y < y + h and body.y + body.h > y):
                continue
            if vx > 0 and old_x + body.w <= x and body.x + body.w > x:
                body.x = x - body.w
                self.blocked = True
            elif vx < 0 and old_x >= x + w and body.x < x + w:
                body.x = x + w
                self.blocked = True
        if self.blocked:
            self.vx = 0.0
        air_dt = dt
        if old_support and was_grounded and now >= self.drop_until:
            top = old_support[0][1]
            supported = [(i,r) for i,r,_ in surfaces if abs(r[1]-top)<=1
                         and body.x<r[0]+r[2] and body.x+body.w>r[0]]
            if supported:
                self.support,self.support_rect = min(supported,key=lambda p:p[0]!=self.support)
                body.y = self.support_rect[1]-body.h
                self.vy = 0.0
                self.grounded = True
                self.passive = self.jumping = False
                self.walked_off = False
                self.carry_velocity = (carry_x,carry_y)
                self.previous_surfaces = by_id
                return True

            own_vx = self.requested_vx
            if own_vx:
                x,_,w,_ = old_support[0]
                edge = x+w if own_vx>0 else x-body.w
                crossing = max(0.0,min(dt,(edge-old_x)/own_vx))
                air_dt = dt-crossing
            self.vx = own_vx+carry_x
            self.vy = carry_y
            self.passive = True
            self.walked_off = bool(own_vx)

        new_y = old_y + self._fall_displacement(air_dt)
        landing = None
        ceiling = None
        for objid, rect, kind in surfaces:
            x, y, w, h = rect
            if kind == 2 and now < self.drop_until:
                continue
            previous,previous_kind = self.previous_surfaces.get(objid,(rect,kind))
            if previous_kind!=kind:
                previous=rect
            px,py,pw,ph = previous
            a,b = start_y+body.h-py,new_y+body.h-y

            if a<=1 and b>=0 and b>=a:
                if was_grounded and abs(a)<=1 and not (body.x<x+w and body.x+body.w>x):
                    continue
                fraction=max(0,min(1,-a/(b-a))) if b>a else 0
                bx=start_x+(body.x-start_x)*fraction
                lx=px+(x-px)*fraction
                if bx<lx+w and bx+body.w>lx and (landing is None or fraction<landing[2]):
                    landing=(objid,rect,fraction)
            a,b = start_y-(py+ph),new_y-(y+h)
            if kind==1 and a>=0 and b<0:
                fraction=max(0,min(1,-a/(b-a)))
                bx=start_x+(body.x-start_x)*fraction
                lx=px+(x-px)*fraction
                if bx<lx+w and bx+body.w>lx and (ceiling is None or fraction<ceiling[2]):
                    ceiling=(objid,rect,fraction)
        self.grounded = landing is not None
        if landing:
            self.landing_serial += 1
            self.support, self.support_rect = landing[:2]
            body.y = landing[1][1] - body.h
            self.vy = 0.0
            self.jumping = False
            self.passive = False
            self.carry_velocity = (0.0,0.0)
        else:
            self.support = None
            self.support_rect = None
            body.y = ceiling[1][1] + ceiling[1][3] if ceiling else new_y
            if ceiling:
                self.vy = 0.0
                self.jumping = False
        self.previous_surfaces = by_id
        return self.grounded

    def _fall_displacement(self, dt):
        speed = min(self.vy,self.terminal_speed)
        accelerated = min(dt,max(0.0,(self.terminal_speed-speed)/self.gravity))
        distance = speed*accelerated+.5*self.gravity*accelerated*accelerated
        self.vy = min(self.terminal_speed,speed+self.gravity*dt)
        return distance+self.terminal_speed*(dt-accelerated)

    def support_velocity(self, dt):
        if not self.room or self.support is None or not self.support_rect or dt<=0:
            return (0.0,0.0)
        item=self.room.objects.get(self.support)
        if not item or terrain_type(item) not in (1,2):
            return (0.0,0.0)
        rect=item.rect()
        return ((rect[0]-self.support_rect[0])/dt,(rect[1]-self.support_rect[1])/dt)

    def jump(self, velocity, body=None):
        if body is not None and self.support is not None and self.support_rect and self.room:
            item=self.room.objects.get(self.support)
            if item and terrain_type(item) in (1,2):
                rect=item.rect()
                body.x+=rect[0]-self.support_rect[0]
                body.y+=rect[1]-self.support_rect[1]
                self.previous_surfaces={i:(r,k) for i,r,k in self.surfaces()}
        self.vy = -abs(float(velocity))
        self.jumping = True
        self.passive = False
        self.grounded = False
        self.support = self.support_rect = None

    def drop(self, now):
        self.drop_until = now + 0.3
        self.grounded = False
        self.jumping = False
        self.support = self.support_rect = None

    def visible(self, start, end):
        """Only solid walls block sight; ledges are transparent to targeting."""
        for _, rect, kind in self.surfaces():
            if kind != 1:
                continue
            if segment_hits_rect(start, end, rect):
                return False
        return True

def segment_hits_rect(start, end, rect):
    x, y, w, h = rect
    if w>=0 and h>=0 and (
            start[0]<x and end[0]<x or start[0]>x+w and end[0]>x+w
            or start[1]<y and end[1]<y or start[1]>y+h and end[1]>y+h):
        return False
    low, high = 0.0, 1.0
    for a, b, minimum, maximum in ((start[0], end[0], x, x + w),
                                  (start[1], end[1], y, y + h)):
        delta = b - a
        if delta == 0:
            if a <= minimum or a >= maximum:
                return False
            continue
        t0, t1 = (minimum - a) / delta, (maximum - a) / delta
        low, high = max(low, min(t0, t1)), min(high, max(t0, t1))
        if low >= high:
            return False
    return high > 0.0 and low < 1.0
