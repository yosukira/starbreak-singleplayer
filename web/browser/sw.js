/* Cache immutable release assets only. Private recordings live in IndexedDB. */
const BASE=new URL('./',self.location.href).pathname;
const PREFIX='starbreak-static-'+BASE;
const CACHE=PREFIX+'assets-v2';
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil((async()=>{
  const current=await caches.open(CACHE);
  for(const key of await caches.keys()){
    if(!key.startsWith(PREFIX)||key===CACHE)continue;
    const previous=await caches.open(key);let copied=true;
    for(const request of await previous.keys()){
      try{if(!await current.match(request)){const response=await previous.match(request);if(response?.status===200)await current.put(request,response);}}
      catch(_){copied=false;break;}
    }
    if(copied)await caches.delete(key);
  }
  await self.clients.claim();
})()));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==location.origin||!url.pathname.startsWith(BASE))return;
  const path='/'+url.pathname.slice(BASE.length);
  if(path.startsWith('/api/')||path.startsWith('/replays')||path==='/sharing-config.json')return;
  if(!/^\/(?:texture|data|client\/data|sfx|music|pyodide)\//.test(path))return;
  // Audio requests may contain Range. Never put a partial response into Cache,
  // and never let a cache/quota error turn a successful download into a failure.
  event.respondWith((async()=>{
    let cache;
    try{cache=await caches.open(CACHE);}catch(_){}
    const request=event.request,range=request.headers.get('Range');
    if(cache){
      try{
        const hit=await cache.match(request,{ignoreVary:!!range});
        if(hit){
          if(!range)return hit;
          const match=/^bytes=(\d*)-(\d*)$/.exec(range);
          if(match&&(match[1]||match[2])){
            const body=await hit.arrayBuffer(),size=body.byteLength;
            const start=match[1]?Number(match[1]):Math.max(0,size-Number(match[2]));
            const end=match[1]&&match[2]?Math.min(Number(match[2]),size-1):size-1;
            if(start>=size||end<start)return new Response(null,{status:416,headers:{'Content-Range':`bytes */${size}`}});
            const headers=new Headers(hit.headers);headers.set('Content-Range',`bytes ${start}-${end}/${size}`);headers.set('Content-Length',String(end-start+1));headers.set('Accept-Ranges','bytes');
            return new Response(body.slice(start,end+1),{status:206,headers});
          }
        }
      }catch(_){}
    }
    const response=await fetch(request);
    if(cache&&response.status===200){try{await cache.put(request,response.clone());}catch(_){}}
    return response;
  })());
});
