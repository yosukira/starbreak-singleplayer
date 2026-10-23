// Run the actual iframe handoff coordinator against controllable HTTP/socket
// boundaries. No browser windows, user settings or network services are used.
'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
async function fixture(){
  const events=new Map(),notices=[],transports=[],controls=[],heap=new Int32Array(32768);
  const write=(p,o,v)=>heap[(p+o)>>2]=v,world=1024,player=4096,subworld=8192;
  write(world,200,123);write(world,672,player);write(world,664,subworld);write(subworld,4,2);write(player,8,1);write(player,64,150);
  const element={hidden:false},preferences={becomeLive(){}};
  const module={HEAP32:heap,HEAPU8:new Uint8Array(heap.buffer),gamePreferences:preferences,
    canvas:{focus(){}},replayTranslateKey:key=>key>=65&&key<=90?key+32:key,
    replayControl:(kind,args)=>controls.push([kind,...args]),webStartLiveTelemetry(){}};
  const parent={postMessage:status=>notices.push(status)};
  class Socket{constructor(){transports.push(this);}send(){}close(){this.onclose?.();}}
  const boot={replay:{mode:'play',id:'old',record:'battle',document:{ops:[]}},wsPort:1};
  const context={Module:module,STARBREAK_WEB_BOOT:boot,StarBreakReplayStorage:{stage(){},deliver(){}},
    parent,location:{origin:'http://test',hostname:'test'},document:{getElementById:()=>element},
    WebSocket:Socket,fetch:async url=>({ok:true,json:async()=>url==='/api/game-preferences'?{content:null}:
      url.includes('?job=')?{state:'ready',result:{id:'branch',sequence:0,token:'token',record:'fork',preferences:{}}}:{job:'job'}}),
    addEventListener:(name,handler)=>events.set(name,[...(events.get(name)||[]),handler]),
    setInterval:()=>1,clearInterval(){},setTimeout,clearTimeout,queueMicrotask,performance,AbortSignal,atob,btoa,console,Int32Array,Uint8Array,ArrayBuffer};
  context.window=context;context.focus=()=>{};
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/replay.js','utf8'),context);
  const replay=module.clientReplay;replay.world=world;replay.worldReady=true;replay.liveSocket={readyState:1};
  const fire=(name,event={})=>(events.get(name)||[]).forEach(fn=>fn(event));
  const control=action=>fire('message',{origin:context.location.origin,source:parent,data:{type:'replay-control',action}});
  const key=(type,code)=>fire(type,{keyCode:code,preventDefault(){},stopImmediatePropagation(){}});
  await new Promise(resolve=>setImmediate(resolve));
  return {replay,controls,notices,transports,module,preferences,fire,control,key};
}
(async()=>{
  const lostFocus=await fixture();lostFocus.control('takeover');lostFocus.key('keydown',39);lostFocus.fire('blur');
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(lostFocus.transports.length,1,'Concurrent keys cannot open a second takeover');
  lostFocus.transports[0].onmessage({data:JSON.stringify({type:'takeover-ready'})});
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(lostFocus.replay.mode,'record');
  assert.ok(!lostFocus.controls.some(row=>row[0]==='key'&&row[1]===1),'A key released outside the window cannot remain held after loading');
  assert.equal(lostFocus.notices.at(-1).state,'taken-over');
  assert.equal(lostFocus.replay.handoffPending,false);

  const failed=await fixture();failed.replay.setPaused(true);failed.control('takeover');
  await new Promise(resolve=>setImmediate(resolve));failed.transports[0].close();
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(failed.replay.mode,'play');assert.equal(failed.replay.paused,true);
  assert.equal(failed.replay.handoffPending,false);assert.equal(failed.module.gamePreferences,failed.preferences);
  assert.equal(failed.notices.at(-1).state,'takeover-failed');
  failed.control('takeover');await new Promise(resolve=>setImmediate(resolve));
  assert.equal(failed.transports.length,2,'A failed connection permits a clean retry');
  failed.transports[1].onmessage({data:JSON.stringify({type:'takeover-ready'})});
  await new Promise(resolve=>setImmediate(resolve));assert.equal(failed.replay.mode,'record');
  console.log('Browser takeover focus loss, concurrent gestures, connection failure and retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
