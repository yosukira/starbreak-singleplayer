"""Two reusable original ship motions with explicit encounter phase cues."""
from .motion import MotionTrack
from . import constants

class ShipMotion(object):
    def __init__(self, start, evidence, rng, ceiling_y=None):
        self.rng = rng
        self.ceiling_y = ceiling_y
        self.evidence = evidence
        self.started = 0.0
        self.state = 'entrance'
        self.track = MotionTrack(start, evidence['entrance'][0]['move'])
        self.arrival_time = self.track.times[-1]
        self.home = self.track.position(self.track.times[-1])
        self.diagonal = evidence['diagonal']
        self.horizontal = evidence['horizontal']
        self.bottom_sent = False
        self.serial = 0
        self.family = None

        self.leg = 0

    def position(self, at):
        return self.track.position(max(0,at-self.started))

    def return_window(self,at):
        return (self.state in ('diagonal-out','diagonal-back') and
                self.bottom_at-constants.SHIP_RETURN_LEAD<=at-self.started<=
                self.bottom_at+constants.SHIP_RETURN_TAIL)

    def _hold(self, start, state, seconds, profile_key=None):
        self.state = state
        profile = self.evidence.get('holds',{}).get(profile_key)
        if profile:
            previous, points = 0, []
            for t,x,y in profile['points']:
                if t < previous:
                    continue
                y = start[1]+y
                if self.ceiling_y is not None:
                    y = max(self.ceiling_y, y)
                points.append(dict(xmp_=int(start[0]+x),ymp_=int(y),dt_=t-previous))
                previous = t
            self.track = MotionTrack(start, {'waypoint_':{'points_':points}})
        else:
            self.track = MotionTrack(start, {'to_':dict(xmp_=start[0],ymp_=start[1],dt_=int(seconds*1000))})
        self.leg += 1

    def _diagonal(self, start, outbound):
        profile = self.diagonal[0 if outbound else 1]
        previous, points = 0, []
        for t,x,y in profile['points']:
            if t <= previous:
                continue

            target_y = start[1]+y
            if self.ceiling_y is not None:
                target_y = max(self.ceiling_y, target_y)
            points.append(dict(xmp_=int(start[0]+x),
                ymp_=int(target_y), dt_=t-previous))
            previous = t
        self.track = MotionTrack(start, {'waypoint_':{'points_':points}})
        self.leg += 1
        self.state = 'diagonal-out' if outbound else 'diagonal-back'
        self.bottom_sent = False
        self.bottom_at = profile['bottomMs']/1000

    def _horizontal(self, start, segment):
        movement = self.horizontal[segment]['move']
        original = movement['waypoint_']['points_']

        self.track = MotionTrack(start, {'waypoint_':{'points_':[
            dict(xmp_=p['xmp_'],ymp_=start[1],dt_=p['dt_']) for p in original]}})
        self.leg += 1
        self.state = ('horizontal-out', 'horizontal-out-slow',
                      'horizontal-back', 'horizontal-back-slow')[segment]

    def step(self, at):
        cues = []
        while True:
            elapsed = at-self.started
            if self.state.startswith('diagonal') and not self.bottom_sent and elapsed >= self.bottom_at:
                self.bottom_sent = True
                cues.append(dict(kind='ship-bottom', state=self.state, serial=self.serial))
            if elapsed+1e-8 < self.track.times[-1]:
                break
            end = self.track.position(self.track.times[-1])
            self.started += self.track.times[-1]
            old = self.state
            if old == 'entrance':
                self._hold(end, 'home-hold', 3.14, 'initial')
            elif old == 'home-hold':
                self.serial += 1

                self.family = 'diagonal' if self.rng.random()<constants.SHIP_DIAGONAL_PROBABILITY else 'horizontal'
                if self.family == 'diagonal':
                    self._diagonal(end, True)
                else:
                    self._horizontal(end, 0)
                cues.append(dict(kind='ship-motion-start', state=self.state, serial=self.serial))
            elif old == 'diagonal-out':
                cues.append(dict(kind='ship-pass-complete',state=old,serial=self.serial))
                self._hold(end, 'far-hold', 10.8, 'diagonalFar')
            elif old == 'diagonal-back':
                cues.append(dict(kind='ship-pass-complete',state=old,serial=self.serial))
                self._hold(end, 'home-hold', 10.8, 'diagonalHome')
            elif old == 'horizontal-out':
                gap = self.evidence.get('timing',{}).get('horizontalJoinMs',[0,0])[0]/1000
                self._hold(end, 'horizontal-out-join', max(.001,gap))
            elif old == 'horizontal-out-join':
                self._horizontal(end, 1)
                cues.append(dict(kind='ship-two-rows', state=self.state, serial=self.serial))
            elif old == 'horizontal-out-slow':

                gap = (self.horizontal[2]['t_ms']-self.horizontal[1]['t_ms'])/1000-self.track.times[-1]
                self._hold(end, 'far-hold', max(0.05,gap))
            elif old == 'far-hold':
                if self.family == 'diagonal':
                    self._diagonal(end, False)
                else:
                    self._horizontal(end, 2)
            elif old == 'horizontal-back':
                gap = self.evidence.get('timing',{}).get('horizontalJoinMs',[0,0])[1]/1000
                self._hold(end, 'horizontal-back-join', max(.001,gap))
            elif old == 'horizontal-back-join':
                self._horizontal(end, 3)
                cues.append(dict(kind='ship-two-rows', state=self.state, serial=self.serial))
            elif old == 'horizontal-back-slow':
                cues.append(dict(kind='ship-pass-complete',state=old,serial=self.serial))
                pause = self.evidence.get('timing',{}).get('horizontalHomePauseMs',3100)/1000
                self._hold(end, 'home-arrival-pause', pause)
            elif old == 'home-arrival-pause':
                self._hold(end, 'home-hold', 10.8, 'horizontalHome')
            else:
                raise ValueError('Unknown ship motion state: '+str(old))
        return cues
