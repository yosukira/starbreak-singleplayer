(async()=>{
  const boot=await parent.StarBreakBrowser.request('boot',{query:location.search});
  globalThis.STARBREAK_WEB_BOOT=boot;
  globalThis.Module={arguments:['-s','127.0.0.1:2048','-k'],TOTAL_MEMORY:536870912,preRun:[],postRun:[],canvas:document.getElementById('canvas'),websocket:{url:'ws://127.0.0.1:2048/game'},print:t=>{console.log(t);if(/^F .*Check failed:/.test(t))parent.postMessage({type:"game-error",message:String(t)},location.origin)},printErr:t=>console.error(t),setStatus:t=>{document.getElementById('loading').textContent=t;},onAbort:t=>{Module.clientReplay?.fail(t);parent.postMessage({type:'game-error',message:String(t)},location.origin);}};
  async function script(src){await new Promise((resolve,reject)=>{const element=document.createElement('script');element.src=parent.StarBreakBrowser.assetURL(src);element.onload=resolve;element.onerror=reject;document.body.append(element);});}
  for(const name of ['/browser/client.js','/soundjs.min.js','/canvas.js','/zoom.js','/game-preferences.js','/inventory.js','/shortcuts.js','/bridge.js','/replay-storage.js','/replay.js','/client.js'])await script(name);
})().catch(error=>parent.postMessage({type:'game-error',message:String(error)},location.origin));
