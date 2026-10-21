(async()=>{
  parent.postMessage({type:'loading-progress',percent:5,step:'engine'},location.origin);
  const boot=await parent.StarBreakBrowser.request('boot',{query:location.search+'&paged=1'});
  const replayDocument=boot.replay?.document;
  if(boot.replay?.mode==='play')parent.postMessage({type:'loading-progress',percent:8,step:'reading-replay'},location.origin);
  if(replayDocument?.pages){
    const {token,total}=replayDocument.pages;
    for(let sequence=0;sequence<total;sequence++){
      const page=await parent.StarBreakBrowser.request('api',{method:'GET',url:'/api/replay/page?token='+encodeURIComponent(token)+'&sequence='+sequence});
      for(const row of page.ops)replayDocument.ops.push(row);
      parent.postMessage({type:'loading-progress',percent:10+60*(sequence+1)/total,step:'reading-replay'},location.origin);
    }
    delete replayDocument.pages;
  }
  globalThis.STARBREAK_WEB_BOOT=boot;
  globalThis.Module={arguments:['-s','127.0.0.1:2048','-k'],TOTAL_MEMORY:536870912,preRun:[],postRun:[],canvas:document.getElementById('canvas'),websocket:{url:'ws://127.0.0.1:2048/game'},print:t=>{console.log(t);if(/^F .*Check failed:/.test(t))parent.postMessage({type:"game-error",message:String(t)},location.origin)},printErr:t=>console.error(t),setStatus:t=>{document.getElementById('loading').textContent=t;},onAbort:t=>{Module.clientReplay?.fail(t);parent.postMessage({type:'game-error',message:String(t)},location.origin);}};
  const names=['/browser/client.js','/soundjs.min.js','/canvas.js','/zoom.js','/game-preferences.js','/visuals.js','/inventory.js','/shortcuts.js','/bridge.js','/replay-storage.js','/native-replay.js','/replay.js','/visual-engine.js','/client.js'];
  const manifest=await parent.StarBreakBrowser.ready;let loaded=0;
  // Fetch together, execute in insertion order: dependencies remain ordered
  // without fourteen consecutive network round trips on every new battle.
  await Promise.all(names.map(src=>new Promise((resolve,reject)=>{
    const element=document.createElement('script');element.async=false;
    const url=new URL(parent.StarBreakBrowser.assetURL(src),location.href);
    if(manifest.scripts?.[src])url.searchParams.set('sha256',manifest.scripts[src]);
    element.src=url.href;element.onload=()=>{loaded++;parent.postMessage({type:'loading-progress',percent:10+70*loaded/names.length,step:'engine'},location.origin);resolve();};
    element.onerror=()=>reject(Error('Game resource could not load: '+src));document.body.append(element);
  })));
})().catch(error=>parent.postMessage({type:'game-error',message:String(error)},location.origin));
