const assert=require('node:assert/strict');
const {factory}=require('../web/native-replay.js');
const Native=factory(class {});
function driver(ops){
  const replay=Object.create(Native.prototype);
  Object.assign(replay,{mode:'play',ops,cursor:0,driving:true,stack:[{kind:'inventory'}],lastNativeClock:50});
  return replay;
}
// The original platform may sample the clock more often while doing the
// same operation. Only samples at the completed call boundary are unused.
const fewer=driver([['clock',51],['clock',52],['return','inventory']]);
fewer.leave(7,0);assert.equal(fewer.cursor,3);assert.equal(fewer.lastNativeClock,52);
const more=driver([['return','inventory']]);
assert.equal(more.clock(900),50);assert.equal(more.cursor,0);
more.leave(7,0);assert.equal(more.cursor,1);
const wrong=driver([['input',2,1],['return','inventory']]);
assert.throws(()=>wrong.leave(7,0),/diverged/);
const bytes=new Uint8Array(128),heap=new Int32Array(bytes.buffer);
const inventory=driver([['inventory',2,3840,1024,7],['return','inventory']]);
inventory.stack=[];inventory.world=100;inventory.module={HEAPU8:bytes,HEAP32:heap,_malloc:()=>32,_free:()=>{},asm:{nativeReplayInventory(world,p){
  assert.equal(world,100);assert.deepEqual([heap[(p+4)>>2],heap[(p+8)>>2],heap[(p+12)>>2],heap[(p+20)>>2]],[2,3840,1024,7]);
  inventory.enter(7,world,p,0,0);inventory.leave(7,world);
}}};
inventory.invoke(inventory.ops[0]);assert.equal(inventory.cursor,2);
console.log('Portable inventory operations and platform clock boundaries passed.');

// The native adapter resets reserve/targetBuffer at loop end. Foreign-world
// interpolation uses that buffer, so missing this changes moving collisions.
const timing=driver([]);timing.world=64;timing.module={HEAP32:new Int32Array(256)};timing.document={};
const reserve=(timing.world+688)>>2;timing.module.HEAP32[reserve]=987;timing.module.HEAP32[reserve+1]=150;
timing.restoreTimeCredit();assert.equal(timing.module.HEAP32[reserve],1000);assert.equal(timing.module.HEAP32[reserve+1],0);
timing.module.HEAP32[reserve+1]=150;timing.restoreTimeCredit();assert.equal(timing.module.HEAP32[reserve+1],150);
timing.document.runners=[{cursor:10,engine:'browser'},{cursor:20,engine:'native'}];timing.cursor=10;timing.module.HEAP32[reserve]=987;
timing.restoreTimeCredit();assert.equal(timing.module.HEAP32[reserve],987);assert.equal(timing.module.HEAP32[reserve+1],150);
timing.cursor=9;timing.restoreTimeCredit();assert.equal(timing.module.HEAP32[reserve],1000);
timing.cursor=20;timing.module.HEAP32[reserve]=987;timing.restoreTimeCredit();assert.equal(timing.module.HEAP32[reserve],1000);
// WorldClient destruction and replacement cannot leave a freed world pointer.
timing.nativeClient=16;timing.module.HEAP32[(16+72)>>2]=64;timing.syncWorld();assert.equal(timing.world,64);
timing.worldReady=true;timing.clientContext(0);assert.equal(timing.world,0);assert.equal(timing.worldReady,false);
timing.clientContext(32);timing.module.HEAP32[(32+72)>>2]=128;timing.syncWorld();assert.equal(timing.world,128);
console.log('Native time-credit and WorldClient lifetime boundaries passed.');

// Replaying source clocks must not busy-wait the old display cadence. The
// ordinary game and a live continuation still call the original wait.
const waits=driver([]);let slept=0;const sleep=value=>{slept+=value;return 7;};
assert.equal(waits.wallSleep(6000,sleep),0);assert.equal(slept,0);assert.equal(waits.cursor,0);
waits.driving=false;assert.equal(waits.wallSleep(6000,sleep),7);assert.equal(slept,6000);
waits.driving=true;waits.mode='record';assert.equal(waits.wallSleep(3000,sleep),7);assert.equal(slept,9000);
console.log('Replay wall waits stay separate from recorded simulation time.');

// Initial map loading must run, but CreateWorld cannot construct ChatWindow
// until the original UI/base data callbacks have installed their resources.
const startup=driver([['loop',0],['message',21,'map'],['return','message'],['message',10,'world'],['return','message'],['state',[0,0]],['loop',1]]);
Object.assign(startup,{stack:[],bootstrap:{assets:['ui','base']},coreAssets:new Set(),completed:0,world:0,priming:false,notice(){},restoreTimeCredit(){}});
const invoked=[];startup.invoke=row=>{invoked.push(row[1]);startup.expect(row);startup.expect(['return','message']);};
const loading=startup.execute();
assert.equal(loading.next().value,'assets');assert.deepEqual(invoked,[21]);
startup.asset('data','texture/ui.d.checked.bpb',()=>{});
assert.equal(loading.next().value,'assets');assert.deepEqual(invoked,[21]);
let installed=false;startup.asset('data','texture/base.d.checked.bpb',()=>{installed=true;});
assert.ok(installed);assert.equal(loading.next().value,'frame');assert.deepEqual(invoked,[21,10]);
assert.equal(loading.next().done,true);
console.log('Cold native replay waits for core resources only at world creation.');

// A paused seek must publish its final verified boundary after clearing the
// seek target. Otherwise the parent keeps its takeover button disabled.
const seek=driver([]),notices=[];
Object.assign(seek,{driving:false,stack:[],joined:true,nativeServer:1,nativeClient:1,pendingAssets:0,priming:false,
  paused:false,seekTarget:1000,seekPaused:true,elapsed:0,totalDuration:2000,frameCount:10,worldReady:true,world:1,
  finished:false,error:null,inLoop:false,takeoverAllowed:true,wall:null,playTime:0,now:()=>0,
  playerState:()=>[1,0,1,2,0,0,40,80,150,150,0,12,0,1],notice:value=>notices.push(value),
  runner:{next(){seek.elapsed=1000;return {value:'frame',done:false};}}});
seek.iterate(()=>{});assert.equal(seek.paused,true);assert.equal(seek.seekTarget,null);
assert.equal(notices.at(-1).state,'paused');assert.equal(notices.at(-1).takeover,true);
console.log('Paused portable seeking publishes the usable takeover boundary.');

// A cold-cache wait must stop the wall clock, even when the asynchronous
// decoder completes before the next iteration checks pendingAssets.
const loadingWait=driver([]);let wall=0,phase=0;
Object.assign(loadingWait,{driving:false,stack:[],joined:true,nativeServer:1,nativeClient:1,pendingAssets:0,priming:false,
  paused:false,seekTarget:null,elapsed:1000,playTime:1000,ends:[1000],completed:0,wall:0,now:()=>wall,speed:1,
  runner:{next(){phase++;return phase===1?{value:'assets',done:false}:{done:true};}}});
wall=25;loadingWait.iterate(()=>{});
assert.equal(loadingWait.wall,null);assert.equal(loadingWait.playTime,1000);
wall=9025;loadingWait.iterate(()=>{});
assert.equal(loadingWait.playTime,1000,'Decoder delay cannot become fast-forward debt');

// All checkpoints execute at source cadence; only parent UI notifications
// are throttled. Seeking must still publish the correct state and target.
const progress=driver([]),updates=[];
for(let i=0;i<100;i++)progress.ops.push(['loop',0],['state',[0,0]],['loop',1]);
Object.assign(progress,{stack:[],bootstrap:{},completed:0,world:0,priming:false,elapsed:0,totalDuration:0,
  seekTarget:1000,now:()=>wall,notice:state=>updates.push(state),restoreTimeCredit(){},canTakeOver:()=>false});
const run=progress.execute();wall=0;
for(let i=0;i<100;i++){wall=i;assert.equal(run.next().value,'frame');}
assert.equal(progress.cursor,300,'Throttling never skips a recorded checkpoint');
assert.equal(updates.length,1,'A 1000 Hz recording cannot flood the editor with status messages');
assert.equal(updates[0].state,'seeking');assert.equal(updates[0].target,1);
assert.equal(run.next().done,true);assert.equal(updates.at(-1).state,'ended');
console.log('Portable cold-cache pacing and bounded progress updates passed.');
