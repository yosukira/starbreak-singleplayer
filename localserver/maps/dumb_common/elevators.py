"""Shared EUB lifts: rise through a head-blocked rider, then return empty."""
def step_elevators(self,start,end,rule,identities):
    if not self.player or not self.player.alive:return
    if self.player.age()>.15:return
    dt=max(0.,end-start);bottom=float(rule['bottom_ymp']);speed=float(rule['speed_mp_s'])
    for objid in identities:
        row=self.actors.get(objid)
        if row is None:continue
        p=row['props'];x,y=row['start'];state=self.lifts.get(objid,{})
        feet=self.player.ymp+self.player.hmp;gap=feet-y
        overlap=self.player.xmp+self.player.wmp>x and self.player.xmp<x+p['wmp']
        ceiling=float(rule.get('ceiling_ymp',0))
        for solid in self.actors.values():
            sp=solid['props']
            if sp.get('terraintype')!=1:continue
            sx,sy=self.position(solid['objid'],start);underside=sy+sp.get('hmp',0)
            if self.player.xmp+self.player.wmp>sx and self.player.xmp<sx+sp.get('wmp',0) and underside<=self.player.ymp+256:
                ceiling=max(ceiling,underside)
        released=state.get('released',False)
        if not overlap or self.player.ymp>ceiling+self.player.hmp+512:released=False
        occupied=overlap and not released and abs(gap)<=(self.RIDE_BAND_MP if state.get('occupied')else self.RIDE_MOUNT_MP)
        squeeze=state.get('squeeze',False)and overlap
        if occupied and self.player.ymp<=ceiling+512 and y<=ceiling+self.player.hmp+1024:
            squeeze=True

        if squeeze:
            occupied=True
            if y<=ceiling+1:
                squeeze=False;occupied=False;released=True
                self._record('elevator_passed_rider',end,objid=objid,y=y)
        target=ceiling if occupied else bottom
        new_y=y+max(-speed*dt,min(speed*dt,target-y))
        new_y=max(ceiling,min(bottom,new_y))
        if abs(new_y-y)>1e-6:
            row['start']=(x,new_y);row['pos']=[x,new_y]
            self.batch.move_to(objid,round(x),round(new_y),dt=max(1,round(dt*1000)),offset=self._offset(end))
        if state.get('occupied')!=occupied:
            self._record('elevator_move',end,objid=objid,target=target,occupied=occupied,y=round(new_y,1))
        self.lifts[objid]=dict(y=new_y,target=target,occupied=occupied,squeeze=squeeze,released=released)
