const assert=require('node:assert/strict');
const {installPlatformDrop}=require('../web/replay.js');
for(const mode of [null,'record','play']){
 const HEAP32=new Int32Array(2048),calls=[],module={HEAP32,replayControl:(_kind,values)=>calls.push(values)};
 const put=(p,o,v)=>HEAP32[(p+o)>>2]=v;let time=0;
 if(mode)module.clientReplay={mode,world:1000,control(_kind,_values,callback){callback();}};
 const retry=installPlatformDrop(module,{now:()=>time});
 put(1000,628,3000);put(1000,672,2000);put(1000,200,7);put(1000,668,8);
 put(2000,64,150);put(2000,304,1);put(2000,308,6);
 put(3000,4,1);put(3000,60,3500);put(3000,64,3508);put(3500,4,4000);
 put(4000,8,6);put(4000,76,2);
 retry.frame(1000);module.webDropControl('key',[1,32]);put(1000,668,24);
 time=16;retry.frame(1000);
 assert.deepEqual(calls,mode==='play'?[]:[[0,32],[1,32]],'Recording off must retain the same original input retry');
 calls.length=0;put(2000,308,0);time+=16;retry.frame(1000);assert.equal(calls.length,0,'No repeat while already detached');
 put(1000,668,8);time+=16;retry.frame(1000);assert.equal(retry.intent,null,'Releasing jump cancels the intent');
 put(2000,308,6);put(4000,76,1);module.webDropControl('key',[1,32]);assert.equal(retry.intent,null,'Solid ground never arms a platform drop');
 put(4000,76,2);module.webDropControl('key',[1,32]);put(1000,668,24);time+=251;retry.frame(1000);assert.equal(calls.length,0,'Expired intents do not repeat later');
}
console.log('Platform drop: correct browser attachment ABI, recording off/on, playback exclusion, release, solid ground and expiry passed.');
