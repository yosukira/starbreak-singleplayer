"""Bounded collision queries and continuous cargo contact for City actors."""
import math

class Grid:
    def __init__(self, entries, cell=256):
        self.cell=cell; self.cells={}
        for box,oid in entries:
            for x in range(math.floor(box[0]/cell),math.floor((box[0]+box[2])/cell)+1):
                for y in range(math.floor(box[1]/cell),math.floor((box[1]+box[3])/cell)+1):
                    self.cells.setdefault((x,y),[]).append((box,oid))

    def query(self, rect):
        x,y,w,h=rect; seen=set()
        for gx in range(math.floor(x/self.cell),math.floor((x+w)/self.cell)+1):
            for gy in range(math.floor(y/self.cell),math.floor((y+h)/self.cell)+1):
                for box,oid in self.cells.get((gx,gy),()):
                    key=(oid,tuple(box))
                    if key not in seen:
                        seen.add(key); yield box,oid

def motion_bounds(pos,new,size):
    return [min(pos[0],new[0])-1,min(pos[1],new[1])-1,
            abs(new[0]-pos[0])+size[0]+2,abs(new[1]-pos[1])+size[1]+2]

class CollisionMixin:
    def cargo_step(self,dt):
        cargo=sorted((a for a in self.actors.values() if a['kind']=='cargo'),key=lambda a:-a['pos'][1])
        fixed=self.pack.solids+self.pack.ledges

        for a in cargo:
            x,y=a['pos'];w,h=a['size'];bottom=y+h
            a['vel'][1]+=781.25*dt;end=y+a['vel'][1]*dt
            supports=fixed+[b['pos']+b['size'] for b in cargo if b['id']!=a['id'] and b['id'] in self.actors]
            tops=[box[1] for box in supports if min(x+w,box[0]+box[2])-max(x,box[0])>1 and bottom<=box[1]+.05 and end+h>=box[1]]
            if tops:end=min(tops)-h;a['vel'][1]=0
            self.pose(a,4 if tops else 1)
            if abs(end-y)>.001:
                if not a.get('falling'):self.emit('cargo_fall',id=a['id'],from_y=y)
                a['falling']=True;a['pos'][1]=end
            elif a.get('falling'):
                a['falling']=False;self.emit('cargo_landed',id=a['id'],y=end)

    def claw_crush(self,oid,claw):
        cargo=self.actors.get(oid)
        if cargo and cargo['kind']=='cargo':
            self.remove(cargo,'HITPOINTS');self.emit('claw_crush',id=oid,claw=claw['id'])

    def cargo_damage(self,oid,value,attacker=None):
        cargo=self.actors.get(oid)
        if not cargo or cargo['kind']!='cargo': return
        if attacker is not None:
            key=(attacker['id'],oid)
            if self.time<self.contact_cooldowns.get(key,0): return
            self.contact_cooldowns[key]=self.time+.5
        cargo['hp']=max(0,cargo['hp']-value)
        self.emit('damage_visual',id=oid,value=round(value))
        if cargo['hp']<=0:self.remove(cargo,'HITPOINTS')
        else:self.props(cargo,HITPOINTS=math.ceil(cargo['hp']))

    def move_actor(self,a,dt,gravity=0,platforms=False):
        from .common import overlap
        old=list(a['pos']);w,h=a['size'];a['vel'][1]+=gravity*dt
        proposed=[old[i]+a['vel'][i]*dt for i in (0,1)]
        boxes=[(box,oid) for box,oid in self.solid_grid.query(motion_bounds(old,proposed,a['size']))
               if a['kind'] not in ('worm','wormpart') or oid]
        if a.get('entering'):

            left,right=self.pack.scene['left'],self.pack.scene['right']
            boxes=[(box,oid) for box,oid in boxes if oid or
                   (box[0]+box[2]>left+64 and box[0]<right-64)]
        contacts=[];blocked=[False,False];pos=list(old)

        for box,oid in boxes:
            if not overlap(pos+[w,h],box):continue
            choices=[(abs(pos[0]+w-box[0]),0,-1),(abs(box[0]+box[2]-pos[0]),0,1),
                     (abs(pos[1]+h-box[1]),1,-1),(abs(box[1]+box[3]-pos[1]),1,1)]
            depth,axis,sign=min(choices)
            pos[axis]+=sign*min(8,depth+.01);blocked[axis]=True;contacts.append(oid)
        for axis in (0,1):
            delta=a['vel'][axis]*dt;other=1-axis;extent=a['size'][axis]
            target=pos[axis]+delta
            for box,oid in boxes:
                if oid and oid not in self.actors:continue
                if pos[other]+a['size'][other]<=box[other]+.01 or pos[other]>=box[other]+box[other+2]-.01:continue
                if delta>0 and pos[axis]+extent<=box[axis]+.1 and target+extent>=box[axis]:
                    target=min(target,box[axis]-extent);blocked[axis]=True;contacts.append(oid)
                elif delta<0 and pos[axis]>=box[axis]+box[axis+2]-.1 and target<=box[axis]+box[axis+2]:
                    target=max(target,box[axis]+box[axis+2]);blocked[axis]=True;contacts.append(oid)
            pos[axis]=target
        a['grounded']=blocked[1] and a['vel'][1]>=0
        if platforms and a['vel'][1]>=0:
            for box in self.platform_grid.query(motion_bounds(old,pos,a['size'])):
                b=box[0]
                if old[1]+h<=b[1]+1 and pos[1]+h>=b[1] and pos[0]+w>b[0] and pos[0]<b[0]+b[2]:
                    pos[1]=min(pos[1],b[1]-h);a['grounded']=True;blocked[1]=True
        a['pos']=pos
        for axis in (0,1):
            if blocked[axis]:a['vel'][axis]=0
        damage=a.get('contact_damage',self.pack.dynamic_templates.get(a['vid'],{}).get('damage',
                 self.pack.dynamic_templates.get(a['vid'],{}).get('hitboxdamage',15)))
        for oid in set(contacts):
            if oid:
                if a['kind']=='claw':self.claw_crush(oid,a)
                else:self.cargo_damage(oid,damage,a)
        a['blocked']=blocked
        a['contact_ids']=list(set(contacts))
        return blocked

    def cargo_contacts(self):
        from .common import overlap
        for a in list(self.actors.values()):
            if a['kind'] not in ('flyer','infantry','skiff','hawk','copter','claw','arm','hammer','blast','worm','wormpart'):continue
            if a['kind']=='hammer' and not a.get('active'):continue
            p=self.pack.dynamic_templates.get(a['vid'],{})
            value=p.get('damage') or p.get('hitboxdamage') or 0
            if value<=0:continue
            rect=[a['pos'][0]-1,a['pos'][1]-1,a['size'][0]+2,a['size'][1]+2]
            for box,oid in self.solid_grid.query(rect):
                if oid and overlap(rect,box):
                    if a['kind']=='claw':self.claw_crush(oid,a)
                    else:self.cargo_damage(oid,value,a)
