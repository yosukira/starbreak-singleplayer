/* Read-only display observations for the SHA-pinned 1.14.6 asm.js client.
 * WorldView: world+32, bounds+84, offset+144. SubWorld: object vector+60.
 * The only write is HUDStatus.UIElement.show; it is restored when disabled.
 */
(()=>{
  'use strict';
  const api=StarBreakVisuals,store=new api.Store();let settings=api.options(),textures=new Map(),client=0,hiddenStatus=null,last=0;const applied=new Set();
  const ready=store.list().then(rows=>{for(const row of rows)textures.set(api.path(row.path),row);}).catch(error=>console.warn('Local textures unavailable:',error));
  Module.visualTextures={get applied(){return [...applied];},choose(filename,original,done){ready.then(()=>{
    const row=settings.textures&&textures.get(api.path(filename));
    if(!row||row.width!==original.naturalWidth||row.height!==original.naturalHeight){done(original);return;}
    const image=new Image(),url=URL.createObjectURL(row.blob);image.onload=()=>{URL.revokeObjectURL(url);applied.add(row.path);done(image);};image.onerror=()=>{URL.revokeObjectURL(url);console.warn('Custom texture could not load:',row.path);done(original);};image.src=url;
  });}};
  Module.visualContext=value=>{if(!value)restore();client=value;};
  addEventListener('message',event=>{if(event.source===parent&&event.origin===location.origin&&event.data.type==='visual-options')settings=api.options({getItem:()=>JSON.stringify(event.data.options)});});
  const canvas=document.createElement('canvas');canvas.id='visual-overlay';canvas.style.cssText='position:absolute;pointer-events:none;z-index:2;display:none';document.body.append(canvas);const ctx=canvas.getContext('2d');
  const read=(p,o=0)=>Module.HEAP32[(p+o)>>2]|0,valid=(p,size=352)=>Number.isInteger(p)&&p>0&&!(p&3)&&p+size<=Module.HEAP32.byteLength;
  function restore(){if(hiddenStatus&&valid(hiddenStatus.pointer,32)&&read(hiddenStatus.pointer)===hiddenStatus.type)Module.HEAPU8[hiddenStatus.pointer+12]=hiddenStatus.show;hiddenStatus=null;}
  function objects(world){
    const rows=[],seen=new Set(),subs=[read(world,628),read(world,664)],start=read(world,632),end=read(world,636);
    if(start&&end>=start&&(end-start)%8===0&&end-start<=256*8&&valid(start,end-start))for(let p=start;p<end;p+=8)subs.push(read(p,4));
    for(const sub of new Set(subs)){
      if(!valid(sub,144))continue;const first=read(sub,60),finish=read(sub,64);
      if(!first||finish<first||(finish-first)%8||finish-first>8192*8||!valid(first,finish-first))continue;
      for(let p=first;p<finish;p+=8){const obj=read(p,4);if(!valid(obj,220)||seen.has(obj))continue;seen.add(obj);
        const x=read(obj,16)/256,y=read(obj,20)/256,w=read(obj,24)/256,h=read(obj,28)/256;
        if(w<=0||h<=0||w>100000||h>100000)continue;
        rows.push({ptr:obj,x,y,w,h,hp:read(obj,64),max:read(obj,68),owner:read(obj,52),terrain:read(obj,76),interact:read(obj,84),shape:read(obj,36)});
      }
    }
    return rows;
  }
  function minimap(view,rows,player){
    const bounds={x:read(view,84),y:read(view,88),w:read(view,92),h:read(view,96)};if(bounds.w<=0||bounds.h<=0)return;
    const maxW=Math.min(260,canvas.width*.26),maxH=Math.min(180,canvas.height*.26),scale=Math.min(maxW/bounds.w,maxH/bounds.h),w=bounds.w*scale,h=bounds.h*scale,x=canvas.width-w-15,y=canvas.height-h-15;
    ctx.fillStyle='#071018e6';ctx.fillRect(x-5,y-5,w+10,h+10);ctx.strokeStyle='#91a8bc';ctx.strokeRect(x-5,y-5,w+10,h+10);ctx.save();ctx.beginPath();ctx.rect(x,y,w,h);ctx.clip();
    for(const row of rows){let color=row.ptr===player?'#bde877':row.terrain===1?'#e5edf5':row.terrain===2?'#91a8bc':row.owner===2&&row.max>0?'#ff726b':row.interact?'#69dfe0':null;if(!color)continue;ctx.fillStyle=color;ctx.fillRect(x+(row.x-bounds.x)*scale,y+(row.y-bounds.y)*scale,Math.max(2,row.w*scale),Math.max(2,row.h*scale));}
    ctx.restore();
  }
  function draw(){
    if(!Module.HEAP32||!valid(client,120)||read(client)!==47524){restore();canvas.style.display='none';return;}
    const world=read(client,72),view=read(client,76),hud=read(client,80),overlay=read(client,84);
    if(!valid(world,700)||!valid(view)||read(view)!==46960||read(view,32)!==world){restore();canvas.style.display='none';return;}
    const player=read(world,672),alive=valid(player,744)&&read(player,64)>0;
    const status=valid(hud,116)&&read(hud)===42476?read(hud,52):0;
    if(settings.playerHUD&&alive&&!overlay&&valid(status,76)){
      if(hiddenStatus?.pointer!==status){restore();hiddenStatus={pointer:status,type:read(status),show:Module.HEAPU8[status+12]};}
      Module.HEAPU8[status+12]=0;
    }else restore();
    if(!(settings.enemyHP||settings.minimap||settings.playerHUD)||overlay){canvas.style.display='none';return;}
    const now=performance.now();if(now-last<100)return;last=now;
    const original=Module.canvas;if(canvas.width!==original.width||canvas.height!==original.height){canvas.width=original.width;canvas.height=original.height;}
    for(const name of ['width','height','left','top'])canvas.style[name]=original.style[name];canvas.style.display='block';ctx.clearRect(0,0,canvas.width,canvas.height);
    const rows=settings.enemyHP||settings.minimap?objects(world):[],ox=read(view,144),oy=read(view,148);
    if(settings.enemyHP){ctx.font='bold 13px sans-serif';ctx.textAlign='center';ctx.lineWidth=3;let count=0;
      for(const row of rows){if(row.owner!==2||row.hp<=0||row.max<=0||row.terrain||count>=300)continue;const x=row.x+row.w/2-ox,y=row.y-oy-9;if(x<0||x>canvas.width||y<12||y>canvas.height)continue;count++;
        const width=Math.max(32,Math.min(100,row.w));ctx.fillStyle='#08101fe6';ctx.fillRect(x-width/2-1,y-4,width+2,7);ctx.fillStyle='#e98564';ctx.fillRect(x-width/2,y-3,width*Math.max(0,Math.min(1,row.hp/row.max)),5);ctx.strokeStyle='#071018';ctx.strokeText(String(row.hp),x,y-7);ctx.fillStyle='#fff4da';ctx.fillText(String(row.hp),x,y-7);
      }
    }
    if(settings.minimap)minimap(view,rows,player);
    if(settings.playerHUD&&alive&&!overlay){
      ctx.textAlign='left';ctx.lineWidth=4;ctx.strokeStyle='#071018';ctx.fillStyle=read(player,64)>=read(player,68)?'#bde877':'#fff';ctx.font='bold 40px sans-serif';const hp=String(read(player,64));ctx.strokeText(hp,24,51);ctx.fillText(hp,24,51);const width=ctx.measureText(hp).width;ctx.font='bold 23px sans-serif';ctx.strokeText('/'+read(player,68),28+width,51);ctx.fillText('/'+read(player,68),28+width,51);ctx.fillStyle='#ffe56b';ctx.font='bold 19px sans-serif';const ammo='Ammo '+read(player,540);ctx.strokeText(ammo,24,78);ctx.fillText(ammo,24,78);ctx.font='14px sans-serif';ctx.fillStyle='#b4c7dc';ctx.strokeText(read(player,380)+' EC',24,100);ctx.fillText(read(player,380)+' EC',24,100);
    }
    Module.visualStats={objects:rows.length,enemies:rows.filter(row=>row.owner===2&&row.max>0).length,player,view,bounds:[read(view,84),read(view,88),read(view,92),read(view,96)],offset:[ox,oy]};
  }
  const previous=Module.postMainLoop;Module.postMainLoop=()=>{previous?.();try{draw();}catch(error){restore();canvas.style.display='none';console.warn('Display enhancement:',error);}};
  addEventListener('pagehide',restore);
})();
