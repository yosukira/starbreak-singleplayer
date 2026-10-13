"""Shared native EUB completion presentation."""
def open_doors(host,at):
    if getattr(host,'doors_open',False):return
    host.doors_open=True
    for identity in host.pack.scene.get('doors',[]):
        row=host.actors.get(identity)
        if row is None:continue
        row['vid']=row['props']['vid']='dumb.nextDoor'
        host.batch.modify(identity,'VID',stringval='dumb.nextDoor',offset=host._offset(at))
        host._anim(identity,0,660,at)
    host.batch.general.append(dict(offset_=host._offset(at),worldtitle_=dict(title_='Complete',x_=50,y_=20)))
    host._record('doors_open',at)
