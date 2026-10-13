'Ballistic return hops against the upcoming moving ship, not air steering.'
from . import constants
from .terrain import terrain_type

def jump_speed(vid):
    if vid=='hulk.scavengershredderenergy':
        return constants.SHREDDER_ENERGY_JUMP_SPEED
    if vid=='hulk.scavengershredder':
        return constants.SHREDDER_JUMP_SPEED
    return constants.SCAVENGER_JUMP_SPEED

def landing_opportunity(unit, ledges, ship_path, vx, offset=(0,0)):
    feet=unit.y+unit.h+offset[1]
    velocity=jump_speed(unit.vid)
    previous_feet=feet

    for index,(seconds,dx,dy) in enumerate(ship_path):
        future_feet=feet-velocity*seconds+.5*unit.motion.gravity*seconds*seconds
        if index:
            last_seconds,last_dx,last_dy=ship_path[index-1]
            for ledge in ledges:
                x,y,w,h=ledge.rect()
                if y>=feet-24*256:
                    continue
                a=previous_feet-y-last_dy
                b=future_feet-y-dy
                if a<=0 and b>=0 and b>a:
                    fraction=-a/(b-a)
                    when=last_seconds+(seconds-last_seconds)*fraction
                    platform_x=x+last_dx+(dx-last_dx)*fraction
                    body_x=unit.x+offset[0]+vx*when
                    if body_x<platform_x+w and body_x+unit.w>platform_x:
                        return dict(ledge=ledge.objid,seconds=when,vx=vx,velocity=velocity)
        previous_feet=future_feet
    return None

def available_ledges(room):
    return [item for objid,item in room.objects.items()
            if objid in constants.LEDGE_IDS and terrain_type(item)==2]
