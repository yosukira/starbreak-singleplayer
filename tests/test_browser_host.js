'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
(async()=>{
  const requests=[],storage=new Map();let uploads=0;
  class Worker {
    constructor(url,options){assert.equal(options?.type,'module');assert.ok(url.endsWith('/singleweb/browser/worker.js'));this.calls=[];}
    postMessage(message){this.calls.push(message);queueMicrotask(()=>this.onmessage?.({data:{id:message.id,result:message.kind==='ready'?{version:'test'}:message.url==='/api/records/export'?{filename:'test.sbreplay',content:btoa('example replay')}:{records:[],saved:true}}}));}
    terminate(){}
  }
  const original=async(url,options={})=>{
    const parsed=new URL(url,'https://example.test/singleweb/');
    assert.ok(parsed.pathname.startsWith('/singleweb/'),'Game requests must not occupy the main website');
    url=parsed.pathname.slice('/singleweb'.length);
    requests.push({url,method:options.method||'GET'});
    if(url==='/sharing-config.json')return Response.json({url:''});
    if(url==='/replays'){uploads++;return Response.json({code:'ABCD-EFGH-JKMN',size:14});}
    if(url==='/browser-manifest.json')return Response.json({version:'test',assets:{egy_hulk:[]}});
    if(url==='/replays/ABCDEFGHJKMN')return new Response('downloaded replay');
    throw Error('Unexpected external request '+url);
  };
  const context={fetch:original,Worker,URL,Response,Uint8Array,crypto:webcrypto,atob,btoa,console,AbortSignal,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},dispatchEvent:()=>{},
    localStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value)},
    navigator:{sendBeacon:()=>true},location:{href:'https://example.test/singleweb/',origin:'https://example.test'},
    document:{currentScript:{src:'https://example.test/singleweb/browser/host.js'},getElementById:()=>null},addEventListener:()=>{}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/browser/host.js','utf8'),context);
  await context.StarBreakBrowser.ready;
  assert.equal(new URL(context.StarBreakBrowser.assetURL('/game.html'),context.location.href).pathname,'/singleweb/game.html');
  assert.equal(new URL(context.StarBreakBrowser.assetURL('/singleweb/texture/a.png'),context.location.href).pathname,'/singleweb/texture/a.png');
  for(const path of ['/singleweb/api/info','/api/info','/api/editor','/api/records'])assert.equal((await context.fetch(path)).status,200);
  for(const path of ['/api/draft','/api/game-preferences','/api/replay/client','/api/records/export'])assert.equal((await context.fetch(path,{method:'POST',body:JSON.stringify({ids:['test']})})).status,200);
  assert.equal(uploads,0,'Recording, saving and exporting never upload replays');
  assert.equal(requests.length,0,'The profile and battle APIs are completely player-local');
  const share=()=>context.fetch('/api/records/share',{method:'POST',body:JSON.stringify({id:'test'})});
  assert.equal((await (await share()).json()).code,'ABCD-EFGH-JKMN');assert.equal(uploads,1);
  await share();assert.equal(uploads,1,'Existing sharing code is reused locally');
  await context.fetch('/api/records');assert.equal(uploads,1,'History refresh never uploads');
  assert.equal((await context.fetch('/api/records/redeem',{method:'POST',body:JSON.stringify({code:'abcd-efgh-jkmn'})})).status,200);
  assert.equal(uploads,1,'Redeeming downloads but never uploads the local profile');
  const sendQueue=[],completions=[];
  const host={fetch(){},assetURL:x=>x,attach(socket){socket.opened();return Promise.resolve();},to64:x=>Buffer.from(x).toString('base64'),
    send(_socket,_url,data){sendQueue.push(data);return new Promise(resolve=>completions.push(resolve));},detach(){}};
  const client={parent:{StarBreakBrowser:host},navigator:{},XMLHttpRequest:class{open(){}},HTMLImageElement:class{},EventTarget,Event,MessageEvent,Uint8Array,console,addEventListener(){}};
  vm.createContext(client);vm.runInContext(fs.readFileSync('web/browser/client.js','utf8'),client);
  const socket=new client.WebSocket('ws://test/telemetry');
  socket.send('a');socket.send('bbb');
  assert.equal(socket.bufferedAmount,4);
  await new Promise(setImmediate);assert.deepEqual(sendQueue,['a']);
  completions.shift()();await new Promise(setImmediate);
  assert.equal(socket.bufferedAmount,3,'Completing one send must retain the bytes still queued');
  assert.deepEqual(sendQueue,['a','bbb']);
  completions.shift()();await socket.chain;assert.equal(socket.bufferedAmount,0);
  console.log('Player-local API routing and explicit-only, deduplicated replay sharing passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
