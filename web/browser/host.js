/* Local-only API transport. The only remote upload path is explicit sharing. */
(() => {
  'use strict';
  const originalFetch=globalThis.fetch.bind(globalThis);
  const base=new URL('../',document.currentScript?.src||new URL('/browser/host.js',location.href)).pathname;
  function assetURL(value){
    const target=new URL(value,location.href);
    if(target.origin!==location.origin||target.pathname.startsWith(base))return target.href;
    return base+target.pathname.replace(/^\/+/,'')+target.search+target.hash;
  }
  function virtualPath(path){return path.startsWith(base)?'/'+path.slice(base.length):path;}
  const worker=new Worker(assetURL('/browser/worker.js'),{type:'module'}),pending=new Map(),sockets=new Map();
  let serial=0,socketSerial=0,progress={phase:'environment',percent:0,step:'download'};
  function report(value){progress=value;globalThis.dispatchEvent(new CustomEvent('starbreak-loading',{detail:value}));}
  function request(kind,value={}){return new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});worker.postMessage({id,kind,...value});});}
  worker.onmessage=event=>{
    const message=event.data;
    if(message.type==='loading-progress'){report(message);return;}
    if(message.type==='network'){sockets.get(message.socket)?.receive(message);return;}
    if(message.type==='runtime-error'||message.type==='storage-error'){
      const error=document.getElementById('error');
      if(error){error.textContent=message.type==='storage-error'?'无法写入浏览器本地存储，请导出重要录像。 / Browser storage failed; export important replays.':message.message;error.hidden=false;}return;
    }
    const task=pending.get(message.id);if(!task)return;pending.delete(message.id);
    if(message.error)task.reject(Error(message.error));else task.resolve(message.result);
  };
  worker.onerror=event=>{
    const message=event.message||'Browser runtime could not start';
    for(const task of pending.values())task.reject(Error(message));pending.clear();
    const box=document.getElementById('error');if(box){box.textContent=message;box.hidden=false;}
  };
  const ready=request('ready');
  function from64(value){return Uint8Array.from(atob(value),c=>c.charCodeAt(0));}
  function to64(value){let text='';for(let i=0;i<value.length;i+=8192)text+=String.fromCharCode(...value.subarray(i,i+8192));return btoa(text);}
  function transfer(stage,loaded=0,total=0){globalThis.dispatchEvent(new CustomEvent('starbreak-transfer',{detail:{stage,loaded,total}}));}
  async function remote(url,options={}){return originalFetch(url,{...options,signal:AbortSignal.timeout(90000)});}
  let sharingConfig;
  async function sharing(){sharingConfig||=remote(assetURL('/sharing-config.json')).then(r=>{if(!r.ok)throw Error('Replay sharing unavailable');return r.json();}).then(value=>({...value,url:value.url||base.replace(/\/$/,'')})).catch(error=>{sharingConfig=null;throw error;});return sharingConfig;}
  async function share(id,nickname=''){
    const settings=await sharing();
    const file=await request('api',{method:'POST',url:'/api/records/export',document:{ids:[id]}});
    const cacheKey='starbreak-shared-v1',data=from64(file.content),hash=to64(new Uint8Array(await crypto.subtle.digest('SHA-256',data)));
    let cache;try{cache=JSON.parse(localStorage.getItem(cacheKey)||'{}');}catch(_){cache={};}
    const key=settings.url+'|'+nickname+'|'+hash;
    if(cache[key])return cache[key];
    const response=await remote(settings.url+'/replays',{method:'POST',headers:{'Content-Type':'application/octet-stream','X-Replay-Nickname':nickname},body:data});
    if(!response.ok)throw Error('Replay upload failed');
    const result=await response.json();
    if(!/^[A-HJKMNP-Z2-9]{4}(?:-[A-HJKMNP-Z2-9]{4}){2}$/.test(result.code))throw Error('Invalid sharing response');
    cache[key]=result;localStorage.setItem(cacheKey,JSON.stringify(cache));return result;
  }
  async function redeem(value){
    const code=value.replace(/[-\s]/g,'').toUpperCase();if(!/^[A-HJKMNP-Z2-9]{12}$/.test(code))throw Error('Invalid sharing code');
    transfer('connecting');const settings=await sharing(),response=await remote(settings.url+'/replays/'+code);
    if(!response.ok)throw Error('Sharing code not found');
    const size=Number(response.headers.get('content-length'));if(size>32*1024*1024)throw Error('Replay exceeds sharing limit');
    transfer('download',0,size);const reader=response.body.getReader(),chunks=[];let total=0;
    while(true){const {done,value}=await reader.read();if(done)break;total+=value.length;if(total>32*1024*1024){await reader.cancel();throw Error('Replay exceeds sharing limit');}chunks.push(value);transfer('download',total,size);}
    const data=new Uint8Array(total);let offset=0;for(const part of chunks){data.set(part,offset);offset+=part.length;}
    transfer('validate',total,total);const result=await request('api',{method:'POST',url:'/api/records/import',document:{content:to64(data)}});
    if(result.imported){
      let viewer=localStorage.getItem('starbreak-sharing-viewer');if(!viewer){viewer=crypto.randomUUID();localStorage.setItem('starbreak-sharing-viewer',viewer);}
      void originalFetch(settings.url+'/replays/'+code+'/imported',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({viewer,token:response.headers.get('X-Replay-Import-Token')})}).catch(()=>{});
    }
    transfer('saved',total,total);return result;
  }
  async function fetchLocal(url,options={}){
    const target=new URL(typeof url==='string'?url:url.url,location.href);
    const route=virtualPath(target.pathname);
    if(target.origin!==location.origin||!route.startsWith('/api/'))return originalFetch(target.origin===location.origin?assetURL(target.href):url,options);
    try{
      await ready;
      if(route==='/api/item-icons')return originalFetch(assetURL('/item-icons.json'));
      const method=options.method||'GET';let value=null;
      if(options.body){if(route==='/api/records/import'){const bytes=new Uint8Array(await new Response(options.body).arrayBuffer());value={content:to64(bytes)};}else value=JSON.parse(await new Response(options.body).text());}
      let result;
      if(route==='/api/records/share'&&method==='POST')result=await share(value.id,value.nickname||'');
      else if(route==='/api/records/shared'){const settings=await sharing();const response=await remote(settings.url+'/replays/feed'+target.search);if(!response.ok)throw Error('Sharing list unavailable');result=await response.json();}
      else if(route==='/api/records/shared-detail'){const settings=await sharing();const code=(target.searchParams.get('code')||'').replace(/[-\s]/g,'').toUpperCase();if(!/^[A-HJKMNP-Z2-9]{12}$/.test(code))throw Error('Invalid sharing code');const response=await remote(settings.url+'/replays/'+code+'/details');if(!response.ok)throw Error('Replay details unavailable');result=await response.json();}
      else if(route==='/api/records/redeem'&&method==='POST')result=await redeem(value.code);
      else if(route==='/api/update'){
        const current=await ready,latest=await originalFetch(assetURL('/browser-manifest.json'),{cache:'no-store'}).then(r=>r.json());
        result={state:latest.build===current.build?'current':'available',version:current.version,messages:latest.build===current.build?{zh:'已是当前网页版本。',en:'This is the current web version.'}:{zh:'有新版本；结束本场后刷新网页即可更新。',en:'A new version is ready. Refresh after this battle to update.'}};
      }else if(route==='/api/prepare'){
        const manifest=await originalFetch(assetURL('/browser-manifest.json')).then(r=>r.json());
        const paths=manifest.assets[value.map]||[];let completed=0;
        report({phase:'assets',current:0,total:paths.length,percent:5,step:'assets'});
        await Promise.all(paths.map(path=>originalFetch(assetURL(path)).then(r=>{if(!r.ok)throw Error('Game asset download failed');return r.arrayBuffer();}).then(()=>report({phase:'assets',current:++completed,total:paths.length,percent:5+60*completed/paths.length,step:'assets'}))));
        result=await request('api',{method,url:route,document:value});
      }else result=await request('api',{method,url:route+target.search,document:value});
      return Response.json(result);
    }catch(error){return Response.json({error:String(error.message||error)},{status:400});}
  }
  globalThis.fetch=fetchLocal;
  const originalBeacon=navigator.sendBeacon.bind(navigator);
  navigator.sendBeacon=(url,body)=>{if(virtualPath(new URL(url,location.href).pathname).startsWith('/api/')){void fetchLocal(url,{method:'POST',body});return true;}return originalBeacon(url,body);};
  globalThis.StarBreakBrowser={get progress(){return progress;},ready,request,fetch:fetchLocal,to64,assetURL,base,
    attach(socket,url){const id=++socketSerial;sockets.set(id,socket);socket.id=id;return ready.then(()=>{socket.opened();return request('open',{socket:id,url});});},
    detach(socket,url){sockets.delete(socket.id);void request('close',{socket:socket.id,url});},
    send(socket,url,data,binary){return request('send',{socket:socket.id,url,data,binary});}};
  addEventListener('pagehide',()=>{void request('flush');});
  if('serviceWorker'in navigator)navigator.serviceWorker.register(assetURL('/sw.js')).catch(()=>{});
  // One owner per origin prevents two tabs from overwriting the same profile.
  if(navigator.locks)navigator.locks.request('starbreak-profile',{ifAvailable:true},lock=>{
    if(!lock){worker.terminate();for(const task of pending.values())task.reject(Error('请使用已打开的游戏标签页。 / Use the game tab already open.'));pending.clear();const box=document.getElementById('error');if(box){box.textContent='请使用已打开的游戏标签页。 / Use the game tab already open.';box.hidden=false;}return;}
    return new Promise(()=>{});
  });
})();
