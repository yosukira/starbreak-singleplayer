/* Logical viewport scaling: the original engine renders a wider/narrower world. */
(function(scope){
  'use strict';
  function create({module,storage,resize,status=()=>{}}){
    const key='starbreak-view-zoom-v1';let level=1,percent=10;
    try{const saved=Number(storage.getItem(key));if(Number.isFinite(saved)&&saved>=.5&&saved<=2)level=saved;}catch(_){}
    return {
      get level(){return level;},
      setStep(value){if(Number.isFinite(value)&&value>=5&&value<=50)percent=value;},
      viewport(width,height){return [Math.max(500,Math.min(4096,Math.round(width/level))),Math.max(300,Math.min(2560,Math.round(height/level)))];},
      step(direction){
        // A replay uses its recorded viewport for deterministic camera culling.
        if(module.clientReplay?.mode==='play'){status(null);return;}
        const next=Math.max(.5,Math.min(2,Math.round(level*Math.pow(1+percent/100,direction)*1000)/1000));
        if(next===level)return;
        level=next;try{storage.setItem(key,String(level));}catch(_){}
        if(module.clientReplay)module.clientReplay.control('resize',[],resize);else resize();
        status(Math.round(level*100));
      }
    };
  }
  if(typeof module!=='undefined')module.exports=create;
  else {
    let timer,box;
    Module.webZoom=create({module:Module,storage:localStorage,resize:()=>{if(typeof XDL!=='undefined')XDL.onWindowResize();},status:percent=>{
      if(!box){box=document.createElement('div');box.style.cssText='position:absolute;top:18px;left:50%;transform:translateX(-50%);padding:8px 14px;border-radius:8px;background:#101b28dd;color:#c1f391;font:14px sans-serif;pointer-events:none;z-index:10';document.body.append(box);}
      const zh=document.documentElement.lang==='zh'||parent.document.documentElement.lang==='zh';
      box.textContent=percent===null?(zh?'接管后可以调整视野':'Take over to adjust the view'):(zh?'视野缩放 ':'Zoom ')+percent+'%';box.hidden=false;clearTimeout(timer);timer=setTimeout(()=>box.hidden=true,1400);
    }});
  }
})(typeof window==='undefined'?{}:window);
