"""The six fixed-wall cannons in the user-designed Patriarch variant."""
import math

class WallTurrets:
    def __init__(self,host):
        self.host=host;self.rules=host.pack.entities['turrets'];self.active=False
        self.rate=host.parameters['turret_attack_rate']
        self.rows={m['id']:dict(m,angle=float(m['facing']),next=math.inf,
                               next_cycle=math.inf,remaining=0,pending=None,next_sync=0.)
                   for m in self.rules['mounts']}

    def activate(self):
        self.active=True;h=self.host;r=self.rules
        for order,oid in enumerate(r['fire_order']):
            t=self.rows[oid];t.update(next=h.time+r['first_delay_s']/self.rate+order*(r['stagger_s']/self.rate),
                                     remaining=0,pending=None,next_cycle=math.inf)

    def stop(self):
        self.active=False
        for t in self.rows.values():t.update(next=math.inf,next_cycle=math.inf,remaining=0,pending=None)

    def step(self,dt):
        h=self.host;r=self.rules;target=h.target()
        if not self.active or h.state!='fighting' or target is None:return
        for t in self.rows.values():
            if t['pending'] is not None:
                if h.time+1e-9>=t['pending']['at']:
                    self._fire(t,t['pending']['angle']);t['pending']=None
                continue
            dx,dy=target[0]-(t['x']+32),target[1]-(t['y']+32)
            if dx*(1 if t['side']=='left' else -1)<=0:continue
            desired=math.degrees(math.atan2(dy,dx))%360
            delta=(desired-t['angle']+180)%360-180
            t['angle']=(t['angle']+max(-r['turn_deg_s']*dt,min(r['turn_deg_s']*dt,delta)))%360
            if h.time>=t['next_sync'] and abs(delta)>1:
                h.emit('props',id=t['id'],values=dict(FACING=round(t['angle'])%360));t['next_sync']=h.time+.1
            if h.time+1e-9<t['next'] or abs(delta)>5:continue
            if not t['remaining']:
                t['remaining']=r['burst_count'];t['next_cycle']=h.time+r['cycle_s']/self.rate
            t['remaining']-=1;t['pending']=dict(at=h.time+r['windup_s'],angle=t['angle'])
            t['next']=h.time+r['shot_interval_s']/self.rate if t['remaining'] else t['next_cycle']
            h.emit('props',id=t['id'],values=dict(FACING=round(t['angle'])%360))
            h.emit('anim',id=t['id'],anim=0,duration=r['animation_s'])
            h.emit('sound',id=t['id'],name='sc.cturret1')
            h.emit('turret_warning',id=t['id'],angle=t['angle'],target=list(target))

    def _fire(self,t,angle):
        h=self.host;r=self.rules
        if sum(a['kind']=='spirit' for a in h.actors.values())>=r['cap']:return
        direction=[math.cos(math.radians(angle)),math.sin(math.radians(angle))]
        origin=[t['x']+32,t['y']+32]
        pos=[origin[j]+direction[j]*r['muzzle_px']-16 for j in (0,1)]
        a=h.new('sc.spirit',pos,'spirit',vel=[d*r['speed_px_s'] for d in direction],life=r['life_s'],
                creator=t['id'],properties=dict(damage=r['damage'],facing=round(angle)%360))
        h.emit('sound',id=a['id'],name='sc.tbullet')
        h.emit('turret_shot',id=t['id'],projectile=a['id'],angle=angle,damage=r['damage'])

    def status(self):
        return dict(active=self.active,devices=len(self.rows),
                    pending=sum(t['pending'] is not None for t in self.rows.values()))
