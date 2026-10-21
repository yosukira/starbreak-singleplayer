const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 for(const mount of ['/','/singleweb/'])for(const playback of [false,true]){
  const scripts=[],messages=[];
  const boot=playback?{replay:{mode:'play',document:{ops:[],pages:{token:'t',total:2}}}}:{};
  const host={ready:Promise.resolve({scripts:{'/client.js':'engine-hash'}}),assetURL:path=>mount+path.replace(/^\//,''),
   request:async kind=>kind==='boot'?boot:{ops:[['frame']]}};
  const context={URL,Uint8Array,console,location:{href:'https://test.local'+mount+'game.html',search:playback?'?replay=1':'?attempt=1',origin:'https://test.local'},parent:{StarBreakBrowser:host,postMessage:message=>messages.push(message)},
   document:{getElementById:()=>({}),createElement:()=>({}),body:{append:script=>scripts.push(script)}}};
  vm.createContext(context);const done=vm.runInContext(fs.readFileSync('web/browser/game.js','utf8'),context);
  await new Promise(resolve=>setImmediate(resolve));
  assert.equal(scripts.length,14,'All scripts must start fetching before any one download finishes');
  assert.ok(scripts.every(script=>script.async===false),'Browser must execute dependencies in insertion order');
  assert.ok(scripts.at(-1).src.endsWith('client.js?sha256=engine-hash'));
  for(const script of scripts)script.onload();await done;
  assert.equal(messages.some(message=>message.step==='reading-replay'),playback,'Normal battle must never report replay loading');
  if(playback)assert.equal(boot.replay.document.ops.length,2);
  assert.equal(messages.at(-1).step,'engine');
 }
 console.log('Normal/replay startup labels, parallel fetches, ordered execution and immutable engine URL passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
