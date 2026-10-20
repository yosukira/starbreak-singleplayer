/* Display adapters for the SHA-pinned 1.14.6 asm.js client.
 * WorldView: world+32, bounds+84, offset+144. SubWorld: object vector+60.
 * Temporary renderer/UI overrides are restored before the next game update.
 * Player HP, armor, positions, collisions and attack state are never edited.
 */
(()=>{
  'use strict';
  const api=StarBreakVisuals,store=new api.Store();let settings=api.options(),textures=new Map(),client=0,hiddenStatus=null,last=0;const applied=new Set();
  let displayScale=1,cachedRows=[],cachedWorld='',renderCamera=null,frameEdits=[],lastMove=performance.now(),frames=[],fps=0,ecScope='',lastEC=0,roomEC=0,totalEC=0;
  const viewEdits=new Map(),textCache=new Map();
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
  const write=(changes,pointer,value,byte=false)=>{const heap=byte?Module.HEAPU8:Module.HEAP32,index=byte?pointer:pointer>>2;changes.push([heap,index,heap[index]]);heap[index]=value;};
  const undo=changes=>{for(let i=changes.length-1;i>=0;i--){const [heap,index,value]=changes[i];heap[index]=value;}changes.length=0;};
  const flags={showUniform:79486,showEdges:79487,showScatter:79488,showTiles:79489,showBoxes:169018,showHitboxes:169019};
  const string=pointer=>{if(!valid(pointer,12))return '';const long=Module.HEAPU8[pointer+11]&128,length=long?read(pointer,4):Module.HEAPU8[pointer+11],data=long?read(pointer):pointer;if(length<0||length>128||data<=0||data+length>Module.HEAPU8.length)return '';return new TextDecoder().decode(Module.HEAPU8.subarray(data,data+length));};
  Module.visualSetting=key=>Number(settings[({1:'hideNames',2:'hideFactions',3:'hideHealthbars',4:'hideEffects',5:'hideDamageNumbers'})[key]]===true);
  Module.visualDamageAge=age=>age>=0&&age<132?Math.min(132,age+Math.round((1-settings.damageFlash)*132)):age;
  Module.visualEntity=(view,obj)=>{
    if(!valid(obj,216)||!valid(view))return 1;const world=read(view,32),player=valid(world,700)?read(world,672):0;
    if(settings.hideShell&&obj===player)return 0;
    if(settings.hideFriendlyObjects&&obj!==player&&read(obj,52)===1&&read(obj,76)===0)return 0;
    if(settings.hideExtras&&string(read(obj,132))==='city.hbshot')return 0;
    return 1;
  };
  Module.visualView=(view,stage)=>{
    if(stage===2){const changes=viewEdits.get(view);if(changes){undo(changes);viewEdits.delete(view);}return;}
    if(!valid(view)||read(view)!==46960)return;
    if(stage===0){
      const old=viewEdits.get(view);if(old)undo(old);const changes=[];viewEdits.set(view,changes);
      for(const [key,address]of Object.entries(flags))write(changes,address,Number(settings[key]&&!(settings.customBackground&&['showUniform','showEdges','showScatter'].includes(key))),true);
      if(settings.camera){write(changes,view+140,0,true);if(settings.camera===2){for(const [offset,value]of [[100,-1000000],[104,-1000000],[108,2000000],[112,2000000]])write(changes,view+offset,value);}}
    }else{
      const changes=viewEdits.get(view);if(!changes)return;
      if(settings.noShake){write(changes,view+144,read(view,152));write(changes,view+148,read(view,156));}
      if(settings.noFlash)write(changes,view+260,0);
      renderCamera={view,x:read(view,144),y:read(view,148)};
    }
  };
  Module.visualBackground=color=>{
    let result=settings.customBackground?parseInt(settings.background.slice(1),16)|0xff000000:color;
    const r=result>>>16&255,g=result>>>8&255,b=result&255,maximum=Math.max(r,g,b),limit=settings.brightness*255;
    if(maximum>limit){const scale=limit/maximum;result=(result&0xff000000)|(Math.round(r*scale)<<16)|(Math.round(g*scale)<<8)|Math.round(b*scale);}
    return result|0;
  };
  addEventListener('mousemove',()=>{lastMove=performance.now();Module.canvas.style.cursor='';},{passive:true});
  function frameStart(){
    undo(frameEdits);if(!Module.HEAP32||!valid(client,120)||read(client)!==47524)return;
    write(frameEdits,79490,Number(settings.showHUD),true);
    const parent=read(client,8);if(settings.hideChat&&valid(parent,124)&&read(parent)===34844){const chat=read(parent,84);if(valid(chat,44))write(frameEdits,chat+12,0,true);}
    Module.canvas.style.cursor=settings.cursorHide>0&&performance.now()-lastMove>=settings.cursorHide*1000?'none':'';
  }
  const previousBefore=Module.preMainLoop;Module.preMainLoop=()=>{const result=previousBefore?.();if(result===false)return false;frameStart();return result;};
  function restore(){if(hiddenStatus&&valid(hiddenStatus.pointer,32)&&read(hiddenStatus.pointer)===hiddenStatus.type)Module.HEAPU8[hiddenStatus.pointer+12]=hiddenStatus.show;hiddenStatus=null;}
  function number(value,x,y,bottom=true,negative=false,size=settings.enemyFontSize){
    const label=String(value),color=negative?'#8080ff':'#ffff80',key=size+'|'+color+'|'+label;let sprite=textCache.get(key);
    if(!sprite){
      sprite=document.createElement('canvas');let paint=sprite.getContext('2d');paint.font=`bold ${size}px HemiHeadBold, sans-serif`;sprite.width=Math.ceil(paint.measureText(label).width)+6;sprite.height=Math.ceil(size*1.3)+4;paint=sprite.getContext('2d');paint.font=`bold ${size}px HemiHeadBold, sans-serif`;paint.textBaseline='top';paint.lineJoin='round';paint.lineWidth=4;paint.strokeStyle='#000';paint.fillStyle=color;paint.strokeText(label,3,2);paint.fillText(label,3,2);textCache.set(key,sprite);if(textCache.size>256)textCache.delete(textCache.keys().next().value);
    }
    // World-space labels scale with the same canvas as enemy sprites.
    const width=sprite.width,height=sprite.height;ctx.drawImage(sprite,Math.round(x-width/2),Math.round(y-(bottom?height:0)),width,height);
  }
  function objects(world){
    const rows=[],seen=new Set(),subs=[read(world,628),read(world,664)],start=read(world,632),end=read(world,636);
    if(start&&end>=start&&(end-start)%8===0&&end-start<=256*8&&valid(start,end-start))for(let p=start;p<end;p+=8)subs.push(read(p,4));
    for(const sub of new Set(subs)){
      if(!valid(sub,144))continue;const first=read(sub,60),finish=read(sub,64);
      if(!first||finish<first||(finish-first)%8||finish-first>8192*8||!valid(first,finish-first))continue;
      for(let p=first;p<finish;p+=8){const obj=read(p,4);if(!valid(obj,220)||seen.has(obj))continue;seen.add(obj);
        const x=read(obj,16)/256,y=read(obj,20)/256,w=read(obj,24)/256,h=read(obj,28)/256;
        if(w<=0||h<=0||w>100000||h>100000)continue;
        rows.push({ptr:obj,x,y,w,h,hp:read(obj,64),max:read(obj,68),armor:read(obj,104),owner:read(obj,52),terrain:read(obj,76),interact:read(obj,84),shape:read(obj,36)});
      }
    }
    return rows;
  }
  function minimap(view,rows,player){
    const bounds={x:read(view,84),y:read(view,88),w:read(view,92),h:read(view,96)};if(bounds.w<=0||bounds.h<=0)return;
    const maxW=canvas.width*settings.minimapScale,maxH=canvas.height*settings.minimapScale,scale=Math.min(maxW/bounds.w,maxH/bounds.h),w=bounds.w*scale,h=bounds.h*scale,x=settings.minimapPosition==='center'?(canvas.width-w)/2:canvas.width-w-15,y=settings.minimapPosition==='center'?(canvas.height-h)/2:settings.minimapPosition==='top-right'?15:canvas.height-h-15;
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
    const now=performance.now();if(settings.fps){frames.push(now);while(frames.length>240||frames.length&&frames[0]<now-1000)frames.shift();fps=frames.length>1?(frames.length-1)*1000/(now-frames[0]):0;}else frames.length=0;
    if(!(settings.enemyHP||settings.minimap||settings.playerHUD||settings.miniHP||settings.miniAmmo||settings.ecCounter||settings.fps||settings.roomInfo)||overlay){canvas.style.display='none';return;}
    const original=Module.canvas;if(canvas.width!==original.width||canvas.height!==original.height){canvas.width=original.width;canvas.height=original.height;}
    displayScale=parseFloat(original.style.width)/original.width||1;for(const name of ['width','height','left','top'])canvas.style[name]=original.style[name];canvas.style.display='block';ctx.clearRect(0,0,canvas.width,canvas.height);
    const key=world+'/'+(read(world,200)>>>0);if(cachedWorld!==key||now-last>=100){cachedWorld=key;last=now;cachedRows=settings.enemyHP||settings.minimap?objects(world):[];}
    const rows=settings.enemyHP||settings.minimap?cachedRows:[],ox=renderCamera?.view===view?renderCamera.x:read(view,144),oy=renderCamera?.view===view?renderCamera.y:read(view,148);let labels=0,armors=0;
    for(const row of rows)if(valid(row.ptr,216)){row.x=read(row.ptr,16)/256;row.y=read(row.ptr,20)/256;row.hp=read(row.ptr,64);row.armor=read(row.ptr,104);}
    if(settings.enemyHP){
      for(const row of rows){if(row.owner!==2||row.terrain||!(row.max>0||row.hp<-1||row.armor>0)||labels>=300)continue;const x=row.x+row.w/2-ox,y=row.y+row.h/2-oy;if(row.x+row.w-ox<=0||row.y+row.h-oy<=0||row.x-ox>=canvas.width||row.y-oy>=canvas.height)continue;labels++;
        if(row.hp>=0)number(row.hp,x,y);else if(row.hp!==-1)number(Math.abs(row.hp),x,y,true,true);
        if(row.armor>0){number(row.armor,x,y,false);armors++;}
      }
    }
    if(settings.minimap)minimap(view,rows,player);
    if(settings.playerHUD&&alive&&!overlay){
      ctx.save();ctx.scale(1/displayScale,1/displayScale);ctx.textAlign='left';ctx.lineWidth=4;ctx.strokeStyle='#071018';ctx.fillStyle=read(player,64)>=read(player,68)?'#bde877':'#fff';const size=settings.hudFontSize,y=size+11;ctx.font=`bold ${size}px sans-serif`;const hp=String(read(player,64));ctx.strokeText(hp,24,y);ctx.fillText(hp,24,y);const width=ctx.measureText(hp).width;ctx.font=`bold ${Math.round(size*.575)}px sans-serif`;ctx.strokeText('/'+read(player,68),28+width,y);ctx.fillText('/'+read(player,68),28+width,y);ctx.fillStyle='#ffe56b';ctx.font='bold 19px sans-serif';const ammo='Ammo '+read(player,540)+'/'+Math.max(0,read(player,532)+read(player,536));ctx.strokeText(ammo,24,y+27);ctx.fillText(ammo,24,y+27);ctx.font='14px sans-serif';ctx.fillStyle='#b4c7dc';ctx.strokeText(read(player,380)+' EC · '+read(player,384)+' UC',24,y+49);ctx.fillText(read(player,380)+' EC · '+read(player,384)+' UC',24,y+49);ctx.restore();
    }
    function text(value,x,y,color='#ffff80',align='left'){ctx.font=`bold ${14/displayScale}px sans-serif`;ctx.textAlign=align;ctx.strokeStyle='#000';ctx.lineWidth=3/displayScale;ctx.strokeText(value,x,y);ctx.fillStyle=color;ctx.fillText(value,x,y);}
    if(alive){
      const ec=read(player,380);if(ecScope!==key){ecScope=key;lastEC=roomEC=ec;}else{totalEC+=ec-lastEC;lastEC=ec;}
      let top=read(player,20)/256-oy-12/displayScale;const center=read(player,16)/256+read(player,24)/512-ox;
      if(settings.miniHP){text(read(player,64)+'/'+read(player,68),center,top,read(player,64)>=read(player,68)?'#bde877':'#fff','center');top-=18/displayScale;}
      if(settings.miniAmmo)text(read(player,540)+'/'+Math.max(0,read(player,532)+read(player,536)),center,top,'#ffff80','center');
      if(settings.ecCounter){const y=settings.playerHUD?settings.hudFontSize+90:165;text((ec-roomEC>=0?'+':'')+(ec-roomEC)+' EC · room',24/displayScale,y/displayScale);text((totalEC>=0?'+':'')+totalEC+' EC · session',24/displayScale,(y+18)/displayScale);}
    }
    if(settings.fps)text(fps.toFixed(1)+' FPS · '+(fps>0?1000/fps:0).toFixed(1)+' ms',canvas.width-12/displayScale,22/displayScale,'#ffff80','right');
    if(settings.roomInfo){const title=parent.document.getElementById('arena-title')?.textContent||'StarBreak',zone=string(read(world,184))||string(read(world,196)),floor=read(world,188);text(zone?zone+(floor>0?' '+(floor+1):''):title,canvas.width-12/displayScale,canvas.height-9/displayScale,'#fff','right');}
    Module.visualStats={objects:rows.length,enemies:rows.filter(row=>row.owner===2&&row.max>0).length,labels,armors,textCache:textCache.size,player,view,bounds:[read(view,84),read(view,88),read(view,92),read(view,96)],offset:[ox,oy]};
  }
  const previous=Module.postMainLoop;Module.postMainLoop=()=>{try{previous?.();}finally{undo(frameEdits);for(const changes of viewEdits.values())undo(changes);viewEdits.clear();}try{draw();}catch(error){restore();canvas.style.display='none';console.warn('Display enhancement:',error);}};
  addEventListener('pagehide',()=>{restore();undo(frameEdits);for(const changes of viewEdits.values())undo(changes);Module.canvas.style.cursor='';});
})();
