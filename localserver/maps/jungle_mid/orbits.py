"""Native elliptical ring rules decoded from five original recordings."""
import math

class Orbit:
    def __init__(self, member, wave, born, config):
        self.sign = -1 if wave % 2 else 1
        self.radius, self.completed, self.target = 0., 0, 1
        self.period = int(config['initial_period_ms'])
        self.phase = ((member+1)/5) % 1
        self.hold_until = born+config['birth_hold_s']
        self.step_mp = float(config['step_mp'])
        self.speed_mp_s = float(config['radial_speed_mp_s'])
        self.period_factor = float(config['period_factor'])

    def expand(self):
        self.target += 1

    def advance(self, start, end):
        """Return whether a native MOVE change is required at this boundary."""
        changed = start < self.hold_until <= end
        cursor = start
        while cursor < end-1e-10:
            if cursor < self.hold_until:
                seconds = min(end, self.hold_until)-cursor
                self.phase = (self.phase+seconds*1000/self.period) % 1
                cursor += seconds
                continue
            distance = (self.completed+1)*self.step_mp-self.radius
            growth = self.completed < self.target
            seconds = min(end-cursor, distance/self.speed_mp_s) if growth else end-cursor
            self.phase = (self.phase+seconds*1000/self.period) % 1
            if growth:
                self.radius += self.speed_mp_s*seconds
            cursor += seconds
            if growth and distance <= self.speed_mp_s*seconds+1e-6:
                self.completed += 1
                self.radius = self.completed*self.step_mp
                self.period = int(self.period*self.period_factor)
                changed = True
        return changed

    def growing(self, at):
        return at >= self.hold_until and self.completed < self.target

    def wire(self, parent, at):
        ellipse = dict(xmp=4096, ymp=4096, radiusamp=round(self.radius)*self.sign,
                       radiusbmp=round(self.radius), period=self.period,
                       pos=round(self.phase*self.period) % self.period)
        if self.growing(at):
            ellipse.update(ravel=self.sign*round(self.speed_mp_s/1000), rbvel=round(self.speed_mp_s/1000))
        return dict(relativeto=dict(objid=parent), elliptical=ellipse)

    def position(self, mouth):
        angle = self.phase*math.tau
        return [mouth[0]+4096+self.sign*self.radius*math.cos(angle),
                mouth[1]+4096+self.radius*math.sin(angle)]
