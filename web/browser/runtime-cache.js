/* Content-addressed runtime bytes survive frontend releases and first visits
 * before a service worker controls the page. Player saves are never stored here. */
export function runtimeCache({base,fetch:network=globalThis.fetch.bind(globalThis),storage=globalThis.caches,crypto=globalThis.crypto,report=()=>{}}){
  const root=new URL(base),cacheName='starbreak-runtime-v1:'+root.pathname;
  const metadata=new URL('runtime-cache-state',root).href;
  let cache,previous,manifest;
  const key=(path,hash)=>{const url=new URL(path,root);url.searchParams.set('sha256',hash);return url.href;};
  async function open(value){
    manifest=value;
    try{cache=await storage?.open(cacheName);const hit=await cache?.match(metadata);previous=hit?await hit.json():null;}catch(_){}
    if(!previous){try{if(await storage?.match(new URL('pyodide/pyodide.asm.wasm',root).href))previous={build:'legacy'};}catch(_){}}
    return {updating:!!previous&&previous.build!==manifest.build};
  }
  async function read(path,size=0,onProgress=()=>{}){
    const url=new URL(path,root).href,hash=manifest?.runtime_hashes?.[path];
    let hit;
    if(cache&&hash){try{hit=await cache.match(key(path,hash));}catch(_){}
      // Adopt already-cached Pyodide files from earlier site releases after
      // verifying their actual bytes; no full runtime download for migration.
      if(!hit){try{
        const legacy=await storage.match(url);
        if(legacy?.ok){const bytes=await legacy.arrayBuffer(),digest=await crypto.subtle.digest('SHA-256',bytes);
          if([...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('')===hash){hit=new Response(bytes,{headers:legacy.headers});try{await cache.put(key(path,hash),hit.clone());}catch(_){}}
        }
      }catch(_){}}
    }
    const step=hit?'cached':previous?'update':'download';report(step);
    onProgress(0,step);
    const response=hit||await network(hash?key(path,hash):url);
    if(!response.ok)throw Error('Runtime resource could not load: '+path);
    let data;
    if(!response.body)data=new Uint8Array(await response.arrayBuffer());
    else{
      const reader=response.body.getReader(),parts=[];let received=0;
      while(true){const {done,value}=await reader.read();if(done)break;parts.push(value);received+=value.length;onProgress(size?Math.min(1,received/size):0,step);}
      data=new Uint8Array(received);let offset=0;for(const part of parts){data.set(part,offset);offset+=part.length;}
    }
    onProgress(1,step);
    if(hash&&!hit){
      const digest=await crypto.subtle.digest('SHA-256',data);
      if([...new Uint8Array(digest)].map(b=>b.toString(16).padStart(2,'0')).join('')!==hash)throw Error('Runtime resource checksum mismatch: '+path);
      if(cache){try{await cache.put(key(path,hash),new Response(data,{headers:response.headers}));}catch(_){}}
    }
    return new Response(data,{status:response.status,headers:response.headers});
  }
  async function commit(){if(cache){try{await cache.put(metadata,Response.json({build:manifest.build,version:manifest.version}));}catch(_){}}}
  async function cached(path,hash){try{return await cache?.match(key(path,hash));}catch(_){return undefined;}}
  return {open,read,commit,cached};
}
