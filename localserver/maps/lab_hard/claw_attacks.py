"""Independent native hand attacks, committed at their own green warning.

Cooldown and animation lead come from raw commands. Range, front-sector
eligibility and target-history delay remain fitted interactive rules.
"""
import math

class ClawAttacks(object):
    def __init__(self, session):
        self.session = session
        self.policy = (getattr(session.pack,'attack_policy',{}) or {}).get('claw',{})
        self.enabled = bool(self.policy.get('enabled'))
        self.states = {}
        self.ready = {}
        self.scan_at = 0.
        self.started = None

    def reserve_wall(self, claw, home_at):
        """Reserve only a returned hand; never cancel an ordinary attack."""
        if int(claw) in self.states:
            raise ValueError('Cannot reserve an attacking hand for a wall pull')
        self.ready[int(claw)] = float(home_at)+float(self.policy.get('cooldown_s',4.1))

    def _move(self, claw, target_center, face=False):
        'Schedule a uniform leg; only the outward leg sets facing.'
        s = self.session
        props = s.pack.template(claw)
        half_w = float(props.get('wmp') or 0) / 2.0
        half_h = float(props.get('hmp') or 0) / 2.0
        origin = s.motion.position(claw,s.room_time)
        origin_center = (origin[0] + half_w, origin[1] + half_h)
        dx = float(target_center[0]) - origin_center[0]
        dy = float(target_center[1]) - origin_center[1]
        if face and (abs(dx) > 1 or abs(dy) > 1):
            s.batch.modify(claw, 'FACING',
                           intval=int(round(math.degrees(math.atan2(dy, dx)))) % 360)
        target_top = (target_center[0] - half_w, target_center[1] - half_h)
        seconds = max(.001,math.hypot(target_top[0]-origin[0],
                                      target_top[1]-origin[1])/s.CLAW_SPED_MP_S)
        s.motion.schedule(s.room_time,claw,dict(to=dict(
            xmp=int(round(target_top[0])),ymp=int(round(target_top[1])),
            dt=int(round(seconds*1000)))))
        return s.room_time+seconds

    def step(self):
        s = self.session
        if not self.enabled or s.player is None:
            return
        now = s.room_time
        if self.started is None:
            self.started = now
        for claw,state in list(self.states.items()):
            if now+1e-9 < state['due']:
                continue
            if state['mode'] == 'warning':
                state.update(mode='out',due=self._move(claw,state['target'],face=True))
                continue
            home = s.pack.template(claw)['move']
            root = home['relativeto']['objid']
            rx,ry = s.motion.position(root,now)
            to = home.get('to') or {}
            props = s.pack.template(claw)
            half_w = float(props.get('wmp') or 0) / 2.0
            half_h = float(props.get('hmp') or 0) / 2.0
            target_top = (rx+to.get('xmp',0),ry+to.get('ymp',0))
            target_center = (target_top[0]+half_w,target_top[1]+half_h)
            origin = s.motion.position(claw,now)
            distance = math.hypot(origin[0]-target_top[0],
                                  origin[1]-target_top[1])
            if distance <= 5*256:

                persistent_to={key:value for key,value in to.items() if key!='dt'}
                s.motion.schedule(now,claw,dict(home,to=persistent_to))
                s.batch.modify(claw,'FACING',intval=int(s.pack.template(claw).get('facing',0)))
                self.ready[claw] = now+float(self.policy.get('cooldown_s',4.1))
                s.claw_busy[claw] = now
                self.states.pop(claw)
                s._event('claw_home',claw=claw,next_warning_at=self.ready[claw])
            else:
                arrival = self._move(claw,target_center)
                state.update(mode='back',due=min(arrival,now+float(self.policy.get('return_scan_interval_s',.15))))
        if now < self.scan_at:
            return
        self.scan_at = now+float(self.policy.get('scan_interval_s',.05))
        if now-self.started < float(self.policy.get('initial_delay_s',2.52)):
            return
        reach = float(self.policy.get('max_reach_mp',140000))
        players = s.combat_players()
        for claw in self.policy.get('claw_ids',[]):
            claw = int(claw)
            if s.wall_pull_waiting_for(claw):
                continue
            if (claw in self.states or self.ready.get(claw,0)>now
                    or s.claw_busy.get(claw,0)>now):
                continue
            props = s.pack.template(claw)
            home = props.get('move') or {}
            root = home.get('relativeto',{}).get('objid')
            if root is None:
                continue
            rx,ry = s.motion.position(root,now)
            rx += props.get('wmp',0)/2.
            ry += props.get('hmp',0)/2.
            from ...participants import nearest
            rest = float(props.get('facing',0))
            eligible = []
            for player in players:
                point = s._lagged_player_center(float(self.policy.get('target_history_s',.3)), player)
                if point is None or math.dist(point, s._boss_center()) > reach:
                    continue
                bearing = math.degrees(math.atan2(point[1]-ry, point[0]-rx)) % 360.
                error = (bearing-rest+180) % 360-180
                if root == 143 or abs(error) <= float(self.policy.get('telegraph_half_angle_deg',70.)):
                    eligible.append(player)
            player = nearest(eligible, (rx, ry))
            if player is None:
                continue
            target = s._lagged_player_center(float(self.policy.get('target_history_s',.3)), player)
            angle = math.degrees(math.atan2(target[1]-ry,target[0]-rx))%360.
            delta = (angle-rest+180)%360-180

            if root != 143 and abs(delta)>float(self.policy.get('telegraph_half_angle_deg',70.)):
                continue
            s.motion.cancel(claw)
            s.batch.modify(claw,'FACING',intval=int(round(angle))%360)
            s.batch.animprops(claw,1,1122)
            self.states[claw] = dict(mode='warning',target=tuple(target),
                due=now+float(self.policy.get('telegraph_s',1.025)))
            s.claw_busy[claw] = float('inf')
            s._event('claw_attack',claw=claw,x=int(target[0]),y=int(target[1]),
                     facing_deg=angle,interactive=True)
