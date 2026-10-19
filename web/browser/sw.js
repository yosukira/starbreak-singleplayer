/* Cache immutable release assets only. Private recordings live in IndexedDB. */
const BASE=new URL('./',self.location.href).pathname;
const PREFIX='starbreak-static-'+BASE;
const CACHE=PREFIX+'BUILD_ID';
self.addEventListener('install',()=>self.skipWaiting());
self.addEventListener('activate',event=>event.waitUntil((async()=>{for(const key of await caches.keys())if(key.startsWith(PREFIX)&&key!==CACHE)await caches.delete(key);await self.clients.claim();})()));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  if(event.request.method!=='GET'||url.origin!==location.origin||!url.pathname.startsWith(BASE))return;
  const path='/'+url.pathname.slice(BASE.length);
  if(path.startsWith('/api/')||path.startsWith('/replays')||path==='/sharing-config.json')return;
  if(!/^\/(?:texture|data|client\/data|sfx|music|pyodide)\//.test(path))return;
  event.respondWith((async()=>{const cache=await caches.open(CACHE),hit=await cache.match(event.request);if(hit)return hit;const response=await fetch(event.request);if(response.ok)await cache.put(event.request,response.clone());return response;})());
});
