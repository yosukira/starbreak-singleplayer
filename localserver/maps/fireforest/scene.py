'Local battle simulator runtime.'
import copy
import json
import math
import random
from functools import lru_cache
from pathlib import Path
from ... import pb_layout
from ..ice_boss.scene import normalize
from tools.make_create_world import unwrap_decompress, wrap_compress

ROOT = Path(__file__).resolve().parents[3] / 'maps'

class ForestPack:
    def __init__(self, kind='mid', root=None, seed=0):
        self.kind = kind
        self.root = Path(root or ROOT/('forest_'+kind))
        self.entities = json.loads((self.root/'entities.json').read_text(encoding='utf8'))
        self.dynamic_templates = json.loads((self.root/'dynamic-templates.json').read_text(encoding='utf8'))
        self.templates, self.scene = generate(kind, seed, self.dynamic_templates)
        self.solids = self.scene['collision_solids']
        self.surfaces = self.solids

    @staticmethod
    def rect(p):
        return tuple(p.get(k, 0)/256 for k in ('xmp','ymp','wmp','hmp'))

    def props(self, vid):
        return copy.deepcopy(self.dynamic_templates[vid])

    def wire_props(self, p):
        return normalize('Properties', copy.deepcopy(p))

def generate(kind, seed, assets):
    rng = random.Random(seed)
    profile_rng = random.Random(int(seed) ^ 0x465246)
    templates = {}
    next_id = 100
    def put(vid, rect, **props):
        nonlocal next_id
        p = copy.deepcopy(assets.get(vid, {}))
        for key in ('move','animprops','posetime','prevpose'):
            p.pop(key, None)
        p.update(vid=vid, **dict(zip(('xmp','ymp','wmp','hmp'),(round(v*256) for v in rect))))
        p.update(props)
        templates[str(next_id)] = p
        next_id += 1
        return next_id-1
    def wall(rect, vid='forest.ground'):
        return put(vid, rect, terraintype=1, isinternal=False, boundsworld=True, draw=True,
                   decorate=True, z=60, ownertype=0, hitpoints=-1, damage=0, hitboxdamage=0)
    def portal(rect):
        return put('lobbyportal',rect,interact=0)
    if kind=='mid':

        underground = rng.random()<.5
        vid = 'forest.underground' if underground else 'forest.ground'
        left,right,bottom = 224,1888,1440
        entrance = rng.choice((832,864,896))
        approach_base=entrance-rng.choice((64,96,128))
        boss_top=approach_base-128
        cliff_x=rng.choice((1344,1376,1408))
        floor=[]
        for i in range(19):
            x=224+i*64
            if x>=cliff_x:break
            if i<3:y=entrance
            elif i<12:y=entrance+32*round(2*math.sin((i-3)/9*math.pi))+rng.choice((0,0,32))
            else:y=entrance-round((entrance-approach_base)*min(1,(i-11)/4)/32)*32
            floor.append([x,y,min(64,cliff_x-x),bottom-y]);wall(floor[-1],vid)
        plateau=[cliff_x,boss_top,right-cliff_x,bottom-boss_top]
        wall(plateau,vid)
        wall((192,-448,32,bottom+448),vid);wall((right,-448,32,bottom+448),vid)
        if underground:

            for i in range(27):
                y=64-round(192*math.sin(i/26*math.pi)/32)*32
                wall((192+i*64,-480,64,y+480),vid)
        else:wall((192,-480,right-192+32,32),vid)
        boss=put('forest.egoblinmaster',(cliff_x-32,boss_top-160,256,160),pose=1,hitpoints=1000,removetype=0)
        spawn=put('',(400,entrance,0,0),spawn=True,draw=False)
        put('forest.ucloseddoor' if underground else 'forest.closeddoor',(224,entrance-200,200,200),facing=180)
        gate=put('forest.vgate288',(1632,boss_top-288,32,288),terraintype=1,z=61)

        exit_roof=[1632,-480,right+32-1632,boss_top-288+480]
        wall(exit_roof,vid)
        exit_id=portal((1776,boss_top-160,110,160))
        scene=dict(boss_id=boss,spawn_id=spawn,gate_id=gate,portal_id=exit_id,left=224,right=right,
                   floor=bottom,kind=kind,underground=underground,entrance=entrance,boss_top=boss_top,
                   bounds=[192,-480,1728,1920],floor_steps=floor,pillars=[],exit_roof=exit_roof,
                   plateau=plateau,cliff=dict(x=cliff_x,top=boss_top,base=floor[-1][1]))
    else:

        left,bottom=-128,1504
        x=512;tops=[];flats=[];previous=None

        high_tops={i:rng.choice((480,576,608)) for i in rng.sample(range(4),2)}
        height_choices=(480,576,608,672,704,736,832,896,960,1024,1088,1248)
        @lru_cache(None)
        def can_finish(index,previous_top):
            if index==4:return True
            choices=(high_tops[index],) if index in high_tops else height_choices
            return any((previous_top is None or abs(y-previous_top)<=320)
                       and can_finish(index+1,y) for y in choices)
        for i in range(4):
            options=[y for y in height_choices
                     if (previous is None or abs(y-previous)<=320)
                     and can_finish(i+1,y)]
            y=high_tops[i] if i in high_tops else rng.choice(options);previous=y
            tops.append([x,y,192]);x+=192+rng.choice((192,224,256))
        perch=[x,rng.choice([y for y in (608,704,832,960,1088) if abs(y-previous)<=320]),192]
        right=x+576
        wall((-160,-1600,32,bottom+1632));wall((-160,-1632,right+352,32))
        wall((-128,768,352,bottom+32-768))
        spawn=put('',(100,768,0,0),spawn=True,draw=False)
        put('forest.bossgateclosed',(-128,383,240,385),facing=180)
        profiles=[];spawner_ids=[]

        def spawner(rect):

            x,y,w,h=rect
            spawner_ids.append(put('forest.rockspawner',rect,terraintype=1,decorate=False,
                                  isinternal=False,boundsworld=True,draw=True,z=60))
            wall((x,y+h,w,32))

        def column_segment(px,py,pw,end):

            rows=[]
            left_inset=profile_rng.choice((0,0,32));right_inset=profile_rng.choice((0,0,32))
            remaining_l=profile_rng.choice((64,96,128,160))
            remaining_r=profile_rng.choice((64,96,128,160))
            for cy in range(py,end,32):
                if cy in (py,end-32):li=ri=0
                else:
                    if remaining_l<=0:
                        left_inset=32 if left_inset==0 else profile_rng.choice((0,0,0,0,0,64))
                        remaining_l=profile_rng.choice((64,96,128,160))
                    if remaining_r<=0:
                        right_inset=32 if right_inset==0 else profile_rng.choice((0,0,0,0,0,64))
                        remaining_r=profile_rng.choice((64,96,128,160))
                    li,ri=left_inset,right_inset
                    if li+ri>pw-96:ri=max(0,pw-96-li)
                    remaining_l-=32;remaining_r-=32
                rows.append([px+li,cy,pw-li-ri,32])

            rects=[]
            for row in rows:
                if rects and row[0]==rects[-1][0] and row[2]==rects[-1][2] and row[1]==rects[-1][1]+rects[-1][3]:
                    rects[-1][3]+=32
                else:rects.append(row[:])
            for rect in rects:wall(rect)
            profiles.append(dict(bounds=[px,py,pw,end-py],solids=rects))

        last=224
        for px,py,pw in tops+[perch]:
            flats.append([last,1408,px-last,96]);spawner(flats[-1])

            cut = py<=1024 and rng.random()<.45
            if cut:
                gap_top=py+128;gap_bottom=min(1280,gap_top+rng.choice((192,256)))
                column_segment(px,py,pw,gap_top)
                column_segment(px,gap_bottom,pw,bottom+32)

                wall((px,gap_top-32,pw,32),'forest.underground')
                wall((px,gap_bottom,pw,32),'forest.underground')
                height=gap_bottom-gap_top

                top_height=min(64,height-96)
                for vid,gy,gh in (('forest.midgroundtop',gap_top,top_height),
                                  ('forest.midgroundmid',gap_top+top_height,height-top_height-64),
                                  ('forest.midgroundbot',gap_bottom-64,64)):
                    put(vid,(px,gy,pw,gh),draw=True,terraintype=0,z=100)
            else:column_segment(px,py,pw,bottom+32)
            last=px+pw
        exit_bay=[last,1408,right-last,96]
        spawner(exit_bay)
        wall((right,512,32,bottom+32-512));wall((right,-1600,32,1824))
        wall((right+32,192,576,32));wall((right+32,512,576,1024));wall((right+608,192,32,352))
        gate=put('forest.vgate288',(right,224,32,288),terraintype=1,z=61)
        exit_id=portal((right+384,352,110,160))

        supports=tops+[perch];index=rng.choice((3,4));px,py,pw=supports[index]
        boss=put('forest.goblinmother',(px-64,py-320,320,320),pose=4,hitpoints=1000,armor=50,removetype=0)
        scene=dict(boss_id=boss,spawn_id=spawn,gate_id=gate,portal_id=exit_id,left=left,right=right,
                   floor=bottom,kind=kind,pillars=supports,central_pillars=tops,flats=flats,boss_pillar=index,
                   tall_pillar_indices=[i for i,p in enumerate(tops) if 1408-p[1]>=800],
                   tall_pillar_min_height=800,
                   bounds=[-160,-1632,right+800,3168],column_profiles=profiles,spawner_ids=spawner_ids,
                   spawn_regions=flats+[exit_bay])

    scene['collision_solids']=[list(ForestPack.rect(p)) for p in templates.values() if p.get('terraintype')==1]
    terrain={i:p for i,p in templates.items() if p.get('vid') in ('forest.ground','forest.underground')}

    scene['solid_ids']=[]
    for p in terrain.values():
        scene['solid_ids'].append(put('',ForestPack.rect(p),terraintype=1,shapetype=0,
            draw=False,decorate=False,isinternal=False,boundsworld=False,ownertype=0,hitpoints=-1))
    cells={}
    for p in terrain.values():
        x,y,w,h=ForestPack.rect(p)
        for tx in range(round(x/32),round((x+w)/32)):
            for ty in range(round(y/32),round((y+h)/32)):cells[tx,ty]=p['vid']

    neighbours=[(dx,dy) for dx in (-1,0,1) for dy in (-1,0,1) if dx or dy]
    occupied=set(cells)
    for p in templates.values():
        if p.get('vid')=='forest.rockspawner':
            x,y,w,h=ForestPack.rect(p)
            occupied.update((tx,ty) for tx in range(round(x/32),round((x+w)/32)) for ty in range(round(y/32),round((y+h)/32)))
    edge={cell:vid for cell,vid in cells.items() if any((cell[0]+dx,cell[1]+dy) not in occupied
                                                     for dx,dy in neighbours)}
    for i in terrain:templates.pop(i)
    for ty in sorted({y for x,y in edge}):
        xs=sorted(x for x,y in edge if y==ty)
        while xs:
            first=last=xs.pop(0);vid=edge[first,ty]
            while xs and xs[0]==last+1 and edge[xs[0],ty]==vid:last=xs.pop(0)
            wall((first*32,ty*32,(last-first+1)*32,32),vid)
    scene['wall_shell_cells']=len(edge)
    scene['deep_fill_cells']=len(cells)-len(edge)

    for p in templates.values():
        if p.get('ownertype',0)!=2:

            for field in ('hitpoints','maxhitpoints','damage','hitboxdamage'):p.pop(field,None)
            p.update(invulnerable=True,removetype=0,knockback=False,noleech=True,lifetime=-1)
    return templates,scene

def roll_world(blob, seed, kind):
    pack=ForestPack(kind,seed=seed);layouts=pb_layout.default_layouts()
    msg=layouts.decode('CreateWorld',unwrap_decompress(blob))
    msg['worldprops_']['seed_']=int(seed)
    msg['worldstate_']['serverworld_']['obj_']=[dict(objid_=int(i),props_=pack.wire_props(p)) for i,p in pack.templates.items()]
    msg['spawnobj_']=dict(worldid_=1,objid_=pack.scene['spawn_id'])
    return wrap_compress(layouts.encode('CreateWorld',msg))
