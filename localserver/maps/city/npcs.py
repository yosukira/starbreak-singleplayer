"""City NPC decisions: accelerate, brake/shoot, retreat, charge and blocked approach."""
import math

class NpcMixin:
    def emit_point(self,a,slot,index=0):
        points=self.pack.emitters.get(a['vid'],{}).get(str(slot),[])
        point=points[min(index,len(points)-1)] if points else dict(x=0,y=-a['size'][1]/2,time=0)
        sign=1 if a['facing']==0 else -1
        return [a['pos'][0]+a['size'][0]/2+sign*point['x'],a['pos'][1]+a['size'][1]+point['y']]

    def start_volley(self,a,target):
        from .common import center
        c=center(a);dx,dy=target[0]-c[0],target[1]-c[1]
        self.face(a,dx);angle=math.degrees(math.atan2(dy,max(1,abs(dx))))
        sniper=a['vid']=='city.jsnipe'
        slot=(3 if angle < -20 else 4 if angle>20 else 2) if sniper else (2 if angle < -20 else 4 if angle>20 else 3)
        self.anim(a,slot,1.32);self.pose(a,1)
        a['mode']='shoot';a['mode_until']=self.time+1.32;a['attack_facing']=a['facing']
        points=self.pack.emitters[a['vid']][str(slot)]
        spread=self.rng.uniform(2,6)
        for n,p in enumerate(points):
            self.schedule(p['time']/1000,'npc_emit',owner=a['id'],slot=slot,index=n,
                          vid='city.jsshot' if sniper else 'city.jtshot',spread=0 if sniper else (-spread,0,spread)[n])
        a['next_attack']=self.time+(3.6 if sniper else self.rng.uniform(1.9,2.25))
        self.emit('flyer_volley',id=a['id'],slot=slot,facing=a['facing'])

    def npc_emit(self,e):
        from .common import center
        a=self.actors.get(e['owner'])
        if not a:return
        target=self.target(a)
        if not target:return
        origin=self.emit_point(a,e['slot'],e.get('index',0));sign=1 if a['facing']==0 else -1
        if (target[0]-center(a)[0])*sign<0:
            self.emit('volley_backshot_cancelled',id=a['id']);return
        angle=math.atan2(target[1]-origin[1],max(.01,abs(target[0]-origin[0])))+math.radians(e.get('spread',0))
        angle=max(-math.pi/2+.02,min(math.pi/2-.02,angle))
        self.fire(a,e['vid'],pos=origin,direction=[sign,0] if e['vid']=='city.gshot' else [sign*math.cos(angle),math.sin(angle)])

    def flyer_step(self,a,dt,target):
        from .common import center,approach
        if not target:
            a['vel']=[approach(v,0,160*dt) for v in a['vel']]
            self.move_actor(a,dt);return
        c=center(a);dx,dy=target[0]-c[0],target[1]-c[1];distance=math.hypot(dx,dy)
        mode=a.get('mode','approach')
        if self.time>=a.get('mode_until',0):
            if mode=='shoot':
                mode='retreat' if distance<360 else 'approach';a['mode_until']=self.time+self.rng.uniform(.55,.95)
            elif mode=='charge':mode='retreat';a['mode_until']=self.time+.6
            elif self.time>=a['next_attack']:
                self.start_volley(a,target);mode='shoot'
            elif abs(dy)<60 and 60<abs(dx)<600 and self.time>=a.get('next_charge',0):
                mode='charge';a['charge_sign']=1 if dx>=0 else -1;a['mode_until']=self.time+.75;a['next_charge']=self.time+4
            else:mode='retreat' if distance<180 else 'approach';a['mode_until']=self.time+.35
            a['mode']=mode
        if mode=='shoot':
            a['vel']=[approach(v,0,390.625*dt) for v in a['vel']]
        else:
            self.face(a,dx)
            if mode=='charge':desired=[a['charge_sign']*625,0];accel=937.5;self.pose(a,2)
            else:
                if mode=='retreat':desired=[-(1 if dx>=0 else -1)*300,-120 if dy>0 else 90]
                else:
                    goal=[dx,dy-self.rng.uniform(100,160)]
                    length=max(1,math.hypot(*goal));speed=min(460,length*.9)
                    desired=[v/length*speed for v in goal]
                accel=468.75;self.pose(a,3 if abs(desired[1])>abs(desired[0]) else 2)
            a['vel']=[approach(a['vel'][i],desired[i],accel*dt) for i in (0,1)]
        self.move_actor(a,dt)

    def skiff_stop(self,side,y):
        """Front of the carrier stays outside the nearest tower ledge."""
        w,h=self.pack.rect(self.pack.props('city.skiff'))[2:]
        shelves=[b for b in self.pack.ledges if (b[0]<0 if side==0 else b[0]>=192 and b[2]>=300)]
        shelf=min(shelves,key=lambda b:abs(b[1]-(y+h/2)))
        edge=shelf[0] if side==0 else shelf[0]+shelf[2]

        for cargo in self.actors.values():
            if cargo['kind']!='cargo':continue
            x,cy=cargo['pos'];cw,ch=cargo['size']
            if (side==0 and x>=0) or (side==1 and x<192):continue
            if cy>=y+h or cy+ch<=y-20:continue
            supports=[b for b in shelves if b[1]>=cy+ch-.1 and x+cw>b[0] and x<b[0]+b[2]]
            if not supports:continue
            support=min(supports,key=lambda b:b[1])
            candidate=support[0] if side==0 else support[0]+support[2]
            edge=min(edge,candidate) if side==0 else max(edge,candidate)
        gap=self.pack.entities['npc']['skiff_edge_gap_px']
        return (edge-w-gap if side==0 else edge+gap),edge

    def skiff_step(self,a,dt):
        from .common import approach
        if self.time<a['depart']:return
        if a.get('entering') and self.pack.scene['left']+64<=a['pos'][0] and a['pos'][0]+a['size'][0]<=self.pack.scene['right']-64:
            a['entering']=False
        goal=a['stop_x'];dx=goal-a['pos'][0]
        speed=0 if a.get('arrived') else min(a['max_speed'],math.sqrt(max(0,2*390.625*abs(dx))))
        a['vel'][0]=approach(a['vel'][0],math.copysign(speed,dx) if abs(dx)>1 else 0,390.625*dt)

        a['pos'][1]-=20;a['size'][1]+=20
        self.move_actor(a,dt)
        a['pos'][1]+=20;a['size'][1]-=20
        if abs(dx)<4 or a['blocked'][0] or dx*(goal-a['pos'][0])<=0:
            if not a.get('arrived'):
                if not a['blocked'][0]:a['pos'][0]=goal
                self.emit('skiff_arrived',id=a['id'],position=list(a['pos']),edge=a.get('platform_edge'))
            a['arrived']=True;a['vel'][0]=0
        self.pose(a,1 if a.get('arrived') else 2)
        if a.get('arrived') and self.time>=a['next_attack'] and self.target(a):
            self.anim(a,1,.528);self.schedule(.330,'skiff_lob',owner=a['id'])
            a['next_attack']=self.time+self.rng.uniform(3.3,4.5)

    def skiff_lob(self,e):
        a=self.actors.get(e['owner'])
        if not a or not a.get('arrived'):return
        sign=1 if a['facing']==0 else -1
        shot=self.fire(a,'city.overseerb',pos=self.emit_point(a,1),direction=[sign,0],kind='bomb')
        shot['vel']=[sign*self.rng.uniform(175.78,292.97),-self.rng.uniform(100,260)]
        self.emit('skiff_bomb',id=a['id'],position=list(a['pos']))

    def infantry_step(self,a,dt,target):
        from .common import center,approach
        r=self.pack.entities['npc'];carrier=self.actors.get(a.get('carrier'))
        if carrier:a['pos']=[carrier['pos'][0]+36,carrier['pos'][1]-20]
        else:
            if target:
                c=center(a);dx,dy=target[0]-c[0],target[1]-c[1];self.face(a,dx)
                speed=0 if self.time<a.get('attack_until',0) or abs(dx)<180 else math.copysign(183.59,dx)
                a['vel'][0]=approach(a['vel'][0],speed,700*dt)
                choices=[b for b in self.tick_surfaces if 80<a['pos'][1]+a['size'][1]-b[1]<175 and abs(b[0]+b[2]/2-c[0])<280]
                if a['grounded'] and dy<-100 and abs(dx)<650 and choices and self.time>=a['next_jump']:
                    b=min(choices,key=lambda b:abs(b[0]+b[2]/2-target[0]));a['vel']=[max(-380,min(380,(b[0]+b[2]/2-c[0])*1.6)),-560]
                    a['next_jump']=self.time+2.5;self.pose(a,1)
            self.ground_physics(a,dt)
        if target and self.time>=a['next_attack']:
            c=center(a);dx,dy=target[0]-c[0],target[1]-c[1]
            if a['vid']=='city.petter':
                hawk=self.actors.get(a.get('hawk'))
                if hawk and abs(dy)<90 and abs(dx)<700:
                    self.anim(a,6,1.386);hawk['mode']='charge';hawk['until']=self.time+1.1;hawk['sign']=1 if dx>=0 else -1
                    a['next_attack']=self.time+3.4
            elif abs(dy)<90 and abs(dx)<(900 if a['vid']=='city.gunner' else 250):
                self.face(a,dx);self.pose(a,4);self.anim(a,5 if a['vid']=='city.gunner' else 4,1.386 if a['vid']=='city.gunner' else 1.254)
                if a['vid']=='city.gunner':
                    for index,p in enumerate(self.pack.emitters[a['vid']]['5']):
                        self.schedule(p['time']/1000,'npc_emit',owner=a['id'],slot=5,index=index,vid='city.gshot',spread=0)
                else:a['hammer_until']=self.time+1.1;a['hammer_start']=self.time+.33;self.sound(a,'baller')
                a['attack_until']=self.time+1.386;a['next_attack']=self.time+2.6
        if not carrier and a['grounded'] and self.time>=a.get('attack_until',0):self.pose(a,5 if abs(a['vel'][0])>1 else 4)
        self.hammer_step(a)
        if a['pos'][1]>1150:self.remove(a)

    def npcs_step(self,dt):
        from .common import center,approach
        for a in list(self.actors.values()):
            if a['id'] not in self.actors:continue
            kind=a['kind'];target=self.target(a)
            if kind=='flyer':self.flyer_step(a,dt,target)
            elif kind=='skiff':self.skiff_step(a,dt)
            elif kind=='infantry':self.infantry_step(a,dt,target)
            elif kind=='hawk':
                owner=self.actors.get(a['owner'])
                if not owner:continue
                if a.get('mode')=='charge' and self.time<a.get('until',0):
                    a['vel']=[approach(a['vel'][0],a['sign']*625,937.5*dt),approach(a['vel'][1],0,400*dt)]
                else:
                    dx,dy=owner['pos'][0]-24-a['pos'][0],owner['pos'][1]-a['pos'][1]
                    a['vel']=[max(-400,min(400,dx*3)),max(-400,min(400,dy*3))]
                self.move_actor(a,dt);self.face(a,a['vel'][0])
            elif kind=='copter' and target:
                c=center(a);dx,dy=target[0]-c[0],target[1]-300-c[1];length=max(1,math.hypot(dx,dy))
                wanted=[dx/length*min(200,length),dy/length*min(200,length)]
                a['vel']=[approach(a['vel'][i],wanted[i],300*dt) for i in (0,1)];self.move_actor(a,dt)
                if self.time>=a['next_attack']:
                    self.anim(a,1,.594);self.launch_missile(a,False);a['next_attack']=self.time+5.5
