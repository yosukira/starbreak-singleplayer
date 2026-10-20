(async()=>{
  parent.postMessage({type:'loading-progress',percent:5,step:'reading-replay'},location.origin);
  const boot=await parent.StarBreakBrowser.request('boot',{query:location.search+'&paged=1'});
  const replayDocument=boot.replay?.document;
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
  async function script(src){await new Promise((resolve,reject)=>{const element=document.createElement('script');element.src=parent.StarBreakBrowser.assetURL(src);element.onload=resolve;element.onerror=reject;document.body.append(element);});}
  for(const name of ['/browser/client.js','/soundjs.min.js','/canvas.js','/zoom.js','/game-preferences.js','/visuals.js','/inventory.js','/shortcuts.js','/bridge.js','/replay-storage.js','/native-replay.js','/replay.js','/visual-engine.js','/client.js'])await script(name);
})().catch(error=>parent.postMessage({type:'game-error',message:String(error)},location.origin));
