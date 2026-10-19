const assert=require('node:assert/strict');
const {ReplayStorage,PREFIX}=require('../web/replay-storage.js');
const {ClientReplay}=require('../web/replay.js');
const id='00000000-0000-4000-8000-000000000001',other='00000000-0000-4000-8000-000000000002';
function storage(){const rows=new Map();return {get length(){return rows.size;},key:i=>[...rows.keys()][i],getItem:k=>rows.get(k)??null,setItem:(k,v)=>rows.set(k,v),removeItem:k=>rows.delete(k)};}
(async()=>{
  const disk=storage(),uploaded=[];
  const oldPage=new ReplayStorage({storage:disk,request:async()=>{throw Error('Page was unloaded');}});
  const chunk={sequence:0,ops:[['loop',0],['loop',1]],end:false};
  oldPage.stage(id,chunk);oldPage.stage(id,{sequence:1,ops:[],end:true});
  try{await oldPage.send(id,chunk);}catch(_){}
  assert.equal(await oldPage.deliver(id,chunk),false,'Durably staged chunks can retry without stopping gameplay');
  assert.equal(disk.length,2,'An aborted upload must leave its durable outbox entries');
  const newPage=new ReplayStorage({storage:disk,request:async document=>uploaded.push(document)});
  assert.deepEqual(await newPage.recover(),{saved:2,pending:0});
  assert.deepEqual(uploaded.map(d=>[d.id,d.sequence,d.end]),[[id,0,false],[id,1,true]]);
  assert.equal(disk.length,0);
  const nativeChunk={...chunk,native:true};newPage.stage(id,nativeChunk);
  const nativeUploads=[];
  await new ReplayStorage({storage:disk,request:async d=>nativeUploads.push(d)}).recover();
  assert.equal(nativeUploads[0].native,true,'A portable native chunk retains its journal format after unload recovery');
  // A failed sequence blocks only that attempt, preserving ordering while
  // allowing another finished battle to recover in the same browser session.
  newPage.stage(id,chunk);newPage.stage(id,{sequence:1,ops:[],end:true});newPage.stage(other,{sequence:0,ops:[],end:true});
  const independent=new ReplayStorage({storage:disk,request:async d=>{if(d.id===id)throw Error('Server not ready');}});
  assert.deepEqual(await independent.recover(),{saved:1,pending:1});assert.equal(disk.length,2);
  assert.throws(()=>newPage.stage(id,{...chunk,end:true}),/Conflicting/);
  assert.throws(()=>newPage.stage('../file',chunk),/Invalid/);
  disk.setItem('unrelated-player-options','kept');await newPage.recover();
  assert.equal(disk.getItem('unrelated-player-options'),'kept');
  // Stage the final marker before returning from pagehide/finish. The browser
  // may never execute the following promise callback after unloading.
  const finalDisk=storage();let finishSend;
  const durable=new ReplayStorage({storage:finalDisk,request:()=>new Promise(resolve=>finishSend=resolve)});
  const replay=new ClientReplay({module:{},stage:c=>durable.stage(id,c),save:c=>durable.send(id,c)});
  replay.write(['loop',0]);replay.write(['loop',1]);const finishing=replay.finish();
  assert.equal(finalDisk.length,1,'Final chunk is synchronous, before the upload starts');
  assert.equal(JSON.parse(finalDisk.getItem(PREFIX+id+':0')).end,true);
  await Promise.resolve();finishSend();await finishing;assert.equal(finalDisk.length,0);
  // Outbox quota errors disable recording while leaving live frame timing intact.
  const full=new ReplayStorage({storage:{getItem:()=>null,setItem:()=>{throw Error('Storage quota');}},request:async()=>{}});
  const failure=new ClientReplay({module:{},stage:c=>full.stage(id,c),save:async()=>{}});
  failure.write(['clock',1]);await failure.finish();assert.match(failure.error,/Storage quota/);
  assert.equal(failure.frame(1024,17),17);assert.equal(failure.loop(0),true);
  console.log('Replay outbox: unload recovery, final-marker durability, per-attempt ordering and quota failures passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
