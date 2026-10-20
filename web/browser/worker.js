/* All encounter computation and persistent files stay on the player's device. */
import {loadPyodide} from '../pyodide/pyodide.mjs';
let py, runtime, profileReady=false, lastTick=0;
const encode=value=>JSON.stringify(value);
function run(source){return py.runPython(source);}
let revision=1,saved=0,syncing=false,waiters=[];
function changed(){revision++;}
function synchronize(){
  if(syncing||!waiters.length)return;
  syncing=true;const snapshot=revision;
  py.FS.syncfs(false,error=>{
    syncing=false;
    if(error){const pending=waiters;waiters=[];postMessage({type:'storage-error'});for(const waiter of pending)waiter.reject(error);return;}
    saved=snapshot;
    const pending=waiters;waiters=[];
    for(const waiter of pending){if(waiter.revision<=saved)waiter.resolve();else waiters.push(waiter);}
    synchronize();
  });
}
function flush(){
  if(!profileReady||revision<=saved)return Promise.resolve();
  return new Promise((resolve,reject)=>{waiters.push({revision,resolve,reject});synchronize();});
}
function events(){const rows=JSON.parse(run('json.dumps(runtime.drain())'));for(const row of rows)postMessage({type:'network',...row});}
const nativeFetch=globalThis.fetch.bind(globalThis);
function progress(percent,step){postMessage({type:'loading-progress',phase:'environment',percent,step});}
async function readProgress(response,total,update){
  if(!response.ok)throw Error('Runtime download failed');
  if(!response.body)return new Uint8Array(await response.arrayBuffer());
  const reader=response.body.getReader(),parts=[];let received=0;
  while(true){const {done,value}=await reader.read();if(done)break;parts.push(value);received+=value.length;update(total?Math.min(1,received/total):0);}
  const bytes=new Uint8Array(received);let offset=0;for(const part of parts){bytes.set(part,offset);offset+=part.length;}update(1);return bytes;
}
async function initialize(){
  progress(0,'download');
  const manifest=await nativeFetch('../browser-manifest.json').then(r=>r.json());
  const sizes=manifest.runtime_files||{},downloaded={};let lastPercent=-1;
  globalThis.fetch=async(input,options)=>{
    const response=await nativeFetch(input,options),name=new URL(typeof input==='string'?input:input.url,location.href).pathname.split('/').pop();
    if(!sizes[name])return response;
    const data=await readProgress(response,sizes[name],fraction=>{
      downloaded[name]=fraction*sizes[name];const total=Object.values(sizes).reduce((a,b)=>a+b,0),percent=Math.floor(55*Object.values(downloaded).reduce((a,b)=>a+b,0)/total);
      if(percent!==lastPercent){lastPercent=percent;progress(percent,'download');}
    });
    return new Response(data,{status:response.status,headers:response.headers});
  };
  py=await loadPyodide({stdout:()=>{},indexURL:new URL('../pyodide/',location.href).href});
  globalThis.fetch=nativeFetch;progress(60,'storage');
  py.FS.mkdirTree('/profile');py.FS.mount(py.FS.filesystems.IDBFS,{},'/profile');
  await new Promise((resolve,reject)=>py.FS.syncfs(true,error=>error?reject(error):resolve()));
  profileReady=true;
  const bundle=await readProgress(await nativeFetch('../runtime.zip'),manifest.runtime_size||0,fraction=>progress(65+25*fraction,'download'));
  progress(92,'initialize');
  py.unpackArchive(new Uint8Array(bundle),'zip',{extractDir:'/app'});
  py.globals.set('browser_manifest',encode(manifest));
  run("import sys, os, json\nsys.path.insert(0, '/app')\nos.environ['SB_GAME_SETTINGS_PATH']='/profile/game_settings.json'\nfrom tools.browser_runtime import BrowserRuntime\nruntime=BrowserRuntime('/profile',json.loads(browser_manifest))");
  await flush();
  progress(100,'ready');
  lastTick=performance.now();
  setInterval(()=>{
    const now=performance.now(),dt=Math.min(.05,Math.max(1/60,(now-lastTick)/1000));lastTick=now;
    try{py.globals.set('browser_dt',dt);if(run('runtime.tick(browser_dt)'))changed();events();}
    catch(error){postMessage({type:'runtime-error',message:String(error)});}
  },1000/60);
  setInterval(()=>{if(revision>saved)void flush().catch(()=>{});},2000);
  return manifest;
}
let queue=Promise.resolve(),ready=initialize();
ready.catch(error=>postMessage({type:'runtime-error',message:String(error)}));
globalThis.onmessage=event=>{queue=queue.then(async()=>{
  const message=event.data;
  try{
    const manifest=await ready;
    py.globals.set('browser_request',encode(message));
    let result;
    if(message.kind==='ready')result={version:manifest.version,build:manifest.build};
    else if(message.kind==='flush')result={saved:true};
    else {
      const source={api:"runtime.api(req['method'],req['url'],req.get('document'))",boot:"runtime.boot(req['query'])",open:"runtime.open(req['socket'],req['url'])",send:"runtime.send(req['socket'],req['url'],req['data'],req.get('binary',False))",close:"runtime.close_socket(req['socket'],req['url'])"}[message.kind];
      if(!source)throw Error('Unknown browser request');
      result=JSON.parse(run(`req=json.loads(browser_request)\njson.dumps(${source})`));
      events();
    }
    const persist=message.kind==='close'||message.kind==='api'&&message.method==='POST'&&message.url!=='/api/replay/warm';
    if(persist)changed();
    // Python requests remain ordered, but an IndexedDB write must not block
    // gameplay socket input behind a recording upload. ACK only after durable.
    if(persist||message.kind==='flush')void flush().then(()=>postMessage({id:message.id,result}),error=>postMessage({id:message.id,error:String(error.message||error)}));
    else postMessage({id:message.id,result});
  }catch(error){postMessage({id:message.id,error:String(error.message||error)});}
});};
