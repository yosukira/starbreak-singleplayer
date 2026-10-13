'Current shared native world, reconstructed from authoritative map deltas.'
import base64
import copy
import math
import collections

from .maps.sc_boss.motion import NativeMotion

def plain(value):
    if isinstance(value,dict):return {key.rstrip('_'):plain(item) for key,item in value.items()}
    if isinstance(value,list):return [plain(item) for item in value]
    return value

def decode_delta(layouts,payload):
    def text(value,key=None):
        if isinstance(value,dict):
            if set(value)=={'raw'}:
                return value if key=='messageval_' else base64.b64decode(value['raw']).decode('utf-8')
            return {name:text(item,name) for name,item in value.items()}
        if isinstance(value,list):return [text(item,key) for item in value]
        return value
    return text(layouts.decode('Delta',payload,raw_strings=True))

class SnapshotError(RuntimeError):
    pass

class WorldSnapshot:
    MESSAGE_FIELDS={23:('playerdata_','PlayerData'),26:('interactdescription_','InteractDescription'),
        41:('move_','Properties_Move'),42:('move_to','Linear'),44:('drawmods_','DrawMods'),
        49:('animprops_','AnimationProperties'),52:('interactreaction_','Reaction'),53:('triggerreaction_','Reaction')}
    ALIASES={'DISPLAY_NAME':'displayname','IDESCRIPTION':'interactdescription','IREACTION':'interactreaction',
             'TREACTION':'triggerreaction'}

    def __init__(self,layouts,document,max_objects=16000):
        self.layouts=layouts;self.template=copy.deepcopy(document);self.time=0.;self.revision=0
        self.max_objects=max_objects;self.objects={};self.unsupported=set()
        self.retired=collections.OrderedDict()
        fields={row['name'] for row in layouts.types['Properties']['fields']}
        self.properties={}
        for name,number in layouts.enums['SharedProperty'].items():
            stem=name.replace('PROPERTY_','');field=self.ALIASES.get(stem,stem.lower())+'_'
            if field in fields:self.properties[number]=field
        state=document.get('worldstate_',{})
        for world in [state.get('serverworld_',{}),*state.get('clientworld_',[])]:
            owner=world.get('ownerid_')
            if owner is None:continue
            for obj in world.get('obj_',[]):self.create(owner,obj['objid_'],obj.get('props_',{}),0.)

    def create(self,owner,objid,props,at):
        key=(int(owner),int(objid))
        self.retired.pop(key,None)
        if key not in self.objects and len(self.objects)>=self.max_objects:raise SnapshotError('SnapshotObjectLimit')
        self.objects[key]=dict(props=copy.deepcopy(props),born=at,motion_at=at,anim_at=at,pose_at=at,
            origin=(props.get('xmp_',0),props.get('ymp_',0)),follow_position=(props.get('xmp_',0),props.get('ymp_',0)),correction=None)

    def apply(self,message):
        update=message.get('worldupdate_')
        if not update:return
        owner=int(update['worldid_'])
        for row in update.get('generalupdates_',[]):
            at=self.time+row.get('offset_',0)/1000.
            if 'create_' in row:
                value=row['create_'];self.create(owner,value['objid_'],value.get('props_',{}),at)
            if 'destroy_' in row:
                value=row['destroy_'];target=value.get('ownerid_',owner) or owner
                self.retire((target,value['objid_']))
            if 'damage_' in row:
                value=row['damage_'];target=value.get('ownerid_',owner) or owner
                obj=self.objects.get((target,value['objid_']))
                if obj and 'hitpoints_' in obj['props']:
                    obj['props']['hitpoints_']=max(0,obj['props']['hitpoints_']-value.get('val_',0))
        for row in update.get('objectupdates_',[]):
            key=(owner,int(row['objid_']));obj=self.objects.get(key)
            if obj is None:continue
            at=self.time+row.get('offset_',0)/1000.
            current=self.position(key,at)
            modify=row.get('modifyprops_')
            if modify:
                number=modify['property_'];field=self.properties.get(number)
                if number in self.MESSAGE_FIELDS:
                    field,kind=self.MESSAGE_FIELDS[number]
                    encoded=modify.get('messageval_')
                    raw=encoded.get('raw') if isinstance(encoded,dict) else encoded
                    if raw is None:raise SnapshotError('SnapshotMessagePropertyMissing')
                    value=self.layouts.decode(kind,base64.b64decode(raw))
                    if number==42:
                        value=dict(to_=value);field='move_'
                    obj['props'][field]=value
                    if field=='move_':obj.update(origin=current,motion_at=at,follow_position=current,correction=None)
                    elif field=='animprops_':obj['anim_at']=at
                elif field:
                    for payload in ('intval_','boolval_','stringval_'):
                        if payload in modify:
                            obj['props'][field]=modify[payload];break
                    if field in ('pose_','posetime_'):obj['pose_at']=at
                    if field=='pose_':obj['props']['posetime_']=0
                    if field=='lifetime_':obj['born']=at
                    if field in ('xmp_','ymp_'):
                        obj.update(origin=(obj['props'].get('xmp_',current[0]),obj['props'].get('ymp_',current[1])),motion_at=at)
                        obj['props'].pop('move_',None)
                elif number in (2,5,3,6,8,10):

                    target={2:'xmp_',5:'ymp_',3:'xmp_',6:'ymp_',8:'wmp_',10:'hmp_'}[number]
                    value=modify.get('intval_',0)*256
                    if number in (3,6):value-=obj['props'].get('wmp_' if number==3 else 'hmp_',0)//2
                    obj['props'][target]=value
                else:self.unsupported.add(number)
            if 'movetoxmp_' in row or 'movetoymp_' in row:
                end=(row.get('movetoxmp_',current[0]),row.get('movetoymp_',current[1]))
                obj['props']['move_']=dict(to_=dict(xmp_=end[0],ymp_=end[1],dt_=max(1,row.get('movetodt_',1)),
                    pos_=0,start_=dict(xmp_=current[0],ymp_=current[1])))
                obj.update(origin=current,motion_at=at,correction=None)
        self.revision+=1
        if update.get('dt_',0)>0:self.advance(update['dt_']/1000.)

    def apply_bytes(self,payload):self.apply(decode_delta(self.layouts,payload))

    def advance(self,dt):

        if len(self.objects)<=1:
            entry=next(iter(self.objects.items()),None)
            move=entry[1]['props'].get('move_') if entry else None
            if not move or ('to_' not in move and 'follow_' not in move):
                self.time+=dt
                if entry:
                    key,obj=entry;life=obj['props'].get('lifetime_',0)
                    if life>0 and (self.time-obj['born'])*1000>=life:self.retire(key)

                if len(self.retired)<=1:
                    retired=next(iter(self.retired.items()),None)
                    if retired and self.time-retired[1][0]>2:self.retired.pop(retired[0],None)
                else:
                    for key,(at,_row) in list(self.retired.items()):
                        if self.time-at>2:self.retired.pop(key,None)
                return
        self.time+=dt
        for key,obj in self.objects.items():
            point=obj['props'].get('move_',{}).get('to_',{})
            duration=point.get('dt_',0)
            if duration>0 and (self.time-obj['motion_at'])*1000+point.get('pos_',0)>=duration:
                end=obj['motion_at']+max(0,duration-point.get('pos_',0))/1000.
                position=self.position(key,end)
                obj['props'].pop('move_',None)
                obj['props'].update(xmp_=round(position[0]),ymp_=round(position[1]))
                obj.update(origin=position,motion_at=end)

        memo={}
        def follow(key,seen):
            if key in memo:return memo[key]
            obj=self.objects[key];move=obj['props'].get('move_',{})
            parent=move.get('relativeto_',{});target=(parent.get('worldid_',key[0]) or key[0],parent.get('objid_',0))
            if 'follow_' not in move or target not in self.objects or key in seen:return self.position(key)
            p=follow(target,seen|{key});old=obj['follow_position']
            dx=(int(old[0])>>8)-(int(p[0])>>8);dy=(int(old[1])>>8)-(int(p[1])>>8)
            distance2=dx*dx+dy*dy;maximum=max(0,move['follow_'].get('maxdist_',0))
            if distance2>maximum*maximum:
                scale=(maximum<<10)//max(1,math.isqrt(distance2))
                old=(p[0]+math.trunc(dx*scale/1024)*256,p[1]+math.trunc(dy*scale/1024)*256)
            obj['follow_position']=old;memo[key]=old;return old
        for key in self.objects:
            if 'follow_' in self.objects[key]['props'].get('move_',{}):follow(key,set())

        expired=[key for key,obj in self.objects.items() if obj['props'].get('lifetime_',0)>0
            and (self.time-obj['born'])*1000>=obj['props']['lifetime_']]
        for key in expired:self.retire(key)
        for key,(at,_row) in list(self.retired.items()):
            if self.time-at>2:self.retired.pop(key,None)

    def retire(self,key):
        row=self.objects.pop(key,None)
        if row is not None:
            self.retired[key]=(self.time,row)
            self.retired.move_to_end(key)
            while len(self.retired)>2048:self.retired.popitem(last=False)

    def recent_source(self,key):
        row=self.objects.get(key)
        if row is not None:return row
        recent=self.retired.get(key)
        return recent[1] if recent and self.time-recent[0]<=2 else None

    def position(self,key,at=None,seen=None):
        obj=self.objects.get(key)
        if obj is None:return (0.,0.)
        at=self.time if at is None else at;seen=set() if seen is None else seen
        if key in seen:return obj['origin']
        seen=seen|{key};props=obj['props'];movement=props.get('move_',{})
        if movement.get('follow_'):return obj['follow_position']
        elapsed=max(0.,at-obj['motion_at']);ms=elapsed*1000.
        parent=None;relative=movement.get('relativeto_')
        if relative:
            parent=(relative.get('worldid_',key[0]) or key[0],relative.get('objid_',0))
        origin=obj['origin']
        if parent in self.objects:
            original_parent=self.position(parent,obj['motion_at'],seen)
            origin=(origin[0]-original_parent[0],origin[1]-original_parent[1])
        if movement.get('lerp_') and parent in self.objects:
            lerp=movement['lerp_'];target=lerp.get('target_',{})
            target=(target.get('worldid_',key[0]) or key[0],target.get('objid_',0))
            if target in self.objects:
                p=self.position(parent,at,seen);q=self.position(target,at,seen);amount=lerp.get('percentage_',0)/100.
                if lerp.get('center_'):
                    pprops=self.objects[parent]['props'];qprops=self.objects[target]['props']
                    p=tuple(v+pprops.get(a+'mp_',0)/2 for a,v in zip('wh',p))
                    q=tuple(v+qprops.get(a+'mp_',0)/2 for a,v in zip('wh',q))
                    return tuple(p[i]+(q[i]-p[i])*amount-props.get(a+'mp_',0)/2 for i,a in enumerate('wh'))
                return tuple(p[i]+(q[i]-p[i])*amount for i in range(2))
        model=NativeMotion(*origin);model.move=plain(movement)
        local=model.position(elapsed)
        if movement.get('waypoint_'):
            local,_,_,_=self.waypoint_state(movement['waypoint_'],origin,ms)
        if movement.get('sin_'):
            s=movement['sin_'];progress=s.get('pos_',0)+ms
            angle=math.radians(s.get('angle_',0));period=max(1,s.get('period_',1000))
            velocity=s.get('vel_',0);travel=(velocity>>8)*progress

            phase=s.get('offset_',0)+math.trunc((travel/(period*256) if velocity else progress/period)*360)
            wave=s.get('amp_',0)*math.sin(math.radians(phase))
            local=(s.get('xmp_',origin[0])+travel*math.cos(angle)-wave*math.sin(angle),
                   s.get('ymp_',origin[1])+travel*math.sin(angle)+wave*math.cos(angle))
        if parent in self.objects:
            p=self.position(parent,at,seen);local=(local[0]+p[0],local[1]+p[1])
        return local

    @staticmethod
    def waypoint_state(waypoint,origin,elapsed):
        points=waypoint.get('points_',[]);cursor=origin
        if not points:return cursor,None,0,cursor
        for turn in range(2):
            for index,point in enumerate(points):
                start=point.get('start_',{}) if turn==0 else {}
                start=(start.get('xmp_',cursor[0]),start.get('ymp_',cursor[1]))
                duration=max(1,point.get('dt_',1));progress=elapsed+(point.get('pos_',0) if turn==0 else 0)
                target=(point.get('xmp_',cursor[0]),point.get('ymp_',cursor[1]))
                if progress<duration:
                    return tuple(start[i]+(target[i]-start[i])*progress/duration for i in range(2)),index,progress,start
                elapsed=progress-duration;cursor=target
            if not waypoint.get('loop_'):return cursor,None,0,cursor
            elapsed%=sum(max(1,p.get('dt_',1)) for p in points)
        raise SnapshotError('SnapshotInvalidWaypoint')

    def properties_at(self,key):
        obj=self.objects[key];props=copy.deepcopy(obj['props']);x,y=self.position(key)
        props['xmp_']=round(x);props['ymp_']=round(y)
        age=max(0.,self.time-obj['born'])*1000
        if props.get('lifetime_',0)>0:props['lifetime_']=max(1,round(props['lifetime_']-age))
        if 'posetime_' in props:props['posetime_']+=round(max(0.,self.time-obj['pose_at'])*1000)
        animation=props.get('animprops_')
        if animation:
            elapsed=round(max(0.,self.time-obj['anim_at'])*1000);animation['pos_']=animation.get('pos_',0)+elapsed
            if animation.get('loop_') and animation.get('time_',0)>0:animation['pos_']%=animation['time_']
            elif animation.get('time_',0)>0 and animation['pos_']>=animation['time_']:

                props.pop('animprops_',None)
        movement=props.get('move_')
        if movement:
            elapsed=round(max(0.,self.time-obj['motion_at'])*1000)
            if 'to_' in movement:
                point=movement['to_'];origin=obj['origin'];relative=movement.get('relativeto_')
                if relative:
                    parent=(relative.get('worldid_',key[0]) or key[0],relative.get('objid_',0))
                    p=self.position(parent,obj['motion_at']);origin=(origin[0]-p[0],origin[1]-p[1])
                point.setdefault('start_',dict(xmp_=round(origin[0]),ymp_=round(origin[1])))
                point['pos_']=point.get('pos_',0)+elapsed
            elif 'vel_' in movement:
                v=movement['vel_']
                for axis in 'xy':
                    velocity=v.get(axis+'vel_',0)+v.get(axis+'accel_',0)*elapsed
                    v[axis+'vel_']=round(max(v.get('min'+axis+'vel_',-2**31),min(v.get('max'+axis+'vel_',2**31-1),velocity)))
            elif 'waypoint_' in movement:
                waypoint=movement['waypoint_'];points=waypoint.get('points_',[])
                _,index,progress,start=self.waypoint_state(waypoint,obj['origin'],elapsed)
                if index is None:props.pop('move_',None)
                else:
                    remaining=points[index:]+(points[:index] if waypoint.get('loop_') else [])
                    for point in remaining:point.pop('pos_',None);point.pop('start_',None)
                    remaining[0]['pos_']=round(progress);remaining[0]['start_']=dict(xmp_=round(start[0]),ymp_=round(start[1]))
                    waypoint['points_']=remaining
            elif 'elliptical_' in movement:
                e=movement['elliptical_'];e['pos_']=e.get('pos_',0)+elapsed
            elif 'sin_' in movement:movement['sin_']['pos_']=movement['sin_'].get('pos_',0)+elapsed
        return props

    def document(self,viewer_worldid):
        if self.unsupported:raise SnapshotError('SnapshotUnsupportedProperties:'+','.join(map(str,sorted(self.unsupported))))
        result=copy.deepcopy(self.template);result['worldid_']=viewer_worldid
        server_owner=result['worldstate_']['serverworld_']['ownerid_']
        result['worldstate_']['serverworld_']['obj_']=[dict(objid_=key[1],props_=self.properties_at(key))
            for key in sorted(self.objects) if key[0]==server_owner]
        result['worldstate_']['clientworld_']=[]
        return result
