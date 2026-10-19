'use strict';
const assert=require('node:assert/strict');
const {create,KEY,CACHE,optionsOnly,freshDefaults}=require('../web/game-preferences.js');
const storage=()=>{const values=new Map();return {getItem:k=>values.get(k)||null,setItem:(k,v)=>values.set(k,v)};};
(async()=>{
  const vi=value=>{const out=[];do{out.push((value&127)|(value>=128?128:0));value=Math.floor(value/128);}while(value);return Buffer.from(out);};
  const field=(id,value)=>Buffer.concat([vi(id*8+2),vi(value.length),Buffer.from(value)]);
  const optionBlob=rows=>Buffer.concat(rows.map(([key,value])=>field(1,Buffer.concat([field(1,Buffer.from(key)),field(2,Buffer.from(value))])))).toString('base64');
  const personal=optionBlob([['musicVol','17'],['sfxVol','33']]);
  assert.equal(optionsOnly(btoa(optionBlob([['account','private'],['musicVol','17'],['password','private'],['sfxVol','33']]))),btoa(personal));
  assert.equal(optionsOnly(''),'');
  assert.throws(()=>optionsOnly(btoa(btoa('\x0a\xff'))),/Truncated/);
  let disk={key:KEY,revision:0,content:null};const local=storage();
  const request=async row=>{assert.equal(row.revision,disk.revision);disk={...row,revision:row.revision+1};return disk;};
  let game=create(disk,{storage:local,request});
  assert.equal(game.load(KEY),atob(freshDefaults()));assert.ok(atob(game.load(KEY)).includes('2560'));assert.ok(atob(game.load(KEY)).includes('1600'));
  game.save(KEY,'\x00\x80\xff controls');await game.flush();
  assert.equal(disk.content,btoa('\x00\x80\xff controls'));
  // A different port/browser has no localStorage, but receives the same file.
  game=create(disk,{storage:storage(),request});
  assert.equal(game.load(KEY),'\x00\x80\xff controls');
  game.save('unknown-key','must not persist');await game.flush();assert.equal(disk.revision,1);
  // Existing settings in the old original-client key migrate once.
  const old=storage();old.setItem(KEY,'old settings');let migrated;
  game=create({revision:0,content:null},{storage:old,request:async d=>(migrated={...d,revision:1})});await game.flush();
  assert.equal(atob(migrated.content),'old settings');
  // Disk settings take precedence over stale browser copies.
  game=create(disk,{storage:old,request});assert.equal(game.load(KEY),'\x00\x80\xff controls');
  // Rapid edits are serialized, preserving the latest edit.
  let release;const waiting=new Promise(resolve=>release=resolve);let calls=0;
  game=create(disk,{storage:local,request:async row=>{if(calls++===0)await waiting;return request(row);}});
  game.save(KEY,'first');game.save(KEY,'last');release();await game.flush();assert.equal(atob(disk.content),'last');
  // An interrupted in-flight save retains its next edit for the next launch.
  local.setItem(CACHE,JSON.stringify({pending:true,content:btoa('unsent'),baseRevision:1,previous:btoa('old'),inflight:disk.content}));
  game=create(disk,{storage:local,request});await game.flush();assert.equal(atob(disk.content),'unsent');
  let failures=0;game=create(disk,{storage:local,request:async()=>{throw Error('offline');},status:ok=>{if(!ok)failures++;}});
  game.save(KEY,'offline edit');await game.flush();assert.equal(game.load(KEY),'offline edit');assert.ok(failures);
  assert.equal(JSON.parse(local.getItem(CACHE)).pending,true);
  // The local server strips login fields from the combined client document.
  // Accept its canonical options without repeatedly resending the original.
  let sanitizedCalls=0;
  game=create({revision:0,content:null},{storage:storage(),request:async()=>{sanitizedCalls++;return {revision:1,content:btoa('options only')};}});
  game.save(KEY,'options and login');await game.flush();await game.flush();
  assert.equal(game.load(KEY),'options only');assert.equal(sanitizedCalls,1);
  const vm=require('node:vm'),fs=require('node:fs');
  const playback={window:{},STARBREAK_WEB_BOOT:{replay:{mode:'play'},gamePreferences:{content:btoa('recorded bindings')}},Module:{},atob,btoa,addEventListener(){},
    get localStorage(){throw Error('Playback must never access current browser settings');}};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../web/game-preferences.js'),'utf8'),playback);
  assert.equal(playback.Module.gamePreferences.load(KEY),'recorded bindings');
  playback.Module.gamePreferences.save(KEY,'new bindings');
  assert.equal(playback.Module.gamePreferences.load(KEY),'recorded bindings','Playback never reads personal storage');
  assert.equal(await playback.Module.gamePreferences.flush(),true);
  // A recording made before any preferences existed still saves automatic
  // defaults in the replay sandbox. Use the latest in-game snapshot as the
  // takeover baseline, including any settings changed earlier in the replay.
  const baselineRequests=[],sandbox={window:{},Module:{},atob,btoa,queueMicrotask,AbortSignal,
    STARBREAK_WEB_BOOT:{replay:{mode:'play'},gamePreferences:{content:null}},
    localStorage:storage(),addEventListener(){},parent:{postMessage(){}},location:{origin:'http://localhost'},
    navigator:{sendBeacon(){}},fetch:async(_url,options)=>{
      baselineRequests.push(JSON.parse(options.body));return {ok:true,json:async()=>({revision:8,content:btoa('personal options and edited SFX')})};
    }};
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../web/game-preferences.js'),'utf8'),sandbox);
  sandbox.Module.gamePreferences.save(KEY,'automatically saved defaults');
  sandbox.Module.gamePreferences.save(KEY,'recorded options before takeover');
  assert.equal(sandbox.Module.gamePreferences.load(KEY),null);assert.equal(baselineRequests.length,0);
  sandbox.Module.gamePreferences.becomeLive({key:KEY,revision:7,content:btoa('personal options')});
  sandbox.Module.gamePreferences.save(KEY,'recorded options with edited SFX');await sandbox.Module.gamePreferences.flush();
  assert.equal(baselineRequests.length,1);
  assert.equal(baselineRequests[0].baseline,btoa('recorded options before takeover'));
  // Personal options are applied inside the original engine while playback
  // still owns saves. Loading options itself makes no persistent edit.
  const transfer={...sandbox,Module:{},STARBREAK_WEB_BOOT:{replay:{mode:'play'},gamePreferences:{content:btoa('old replay settings')}},localStorage:storage()};
  baselineRequests.length=0;
  vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname,'../web/game-preferences.js'),'utf8'),transfer);
  transfer.Module.gamePreferences.becomeLive({key:KEY,revision:7,content:btoa(personal)},content=>{
    assert.equal(content,btoa(personal));
    transfer.Module.gamePreferences.save(KEY,personal);
  });
  assert.equal(transfer.Module.gamePreferences.load(KEY),personal);
  assert.equal(baselineRequests.length,0);
  // Existing unsaved personal edits precede takeover edits. Neither is lost,
  // and imported options are never written merely by beginning takeover.
  const takeoverStorage=storage(),requests=[];
  let ownRelease;const ownWait=new Promise(resolve=>ownRelease=resolve);
  game=create({revision:0,content:btoa('own settings')},{storage:takeoverStorage,request:async row=>{
    requests.push(structuredClone(row));
    if(requests.length===1){await ownWait;return {revision:1,content:btoa('own pending edit')};}
    assert.equal(row.baseline,btoa('recorded settings'));
    return {revision:2,content:btoa('own pending edit plus new volume')};
  }});
  game.save(KEY,'own pending edit');game.beginTakeover(btoa('recorded settings'));
  game.save(KEY,'recorded settings plus new volume');ownRelease();await game.flush();
  assert.equal(requests.length,2);assert.equal(requests[0].baseline,undefined);
  assert.equal(game.load(KEY),'recorded settings plus new volume');
  assert.equal(JSON.parse(takeoverStorage.getItem(CACHE)).pending,false);
  // Two quick edits compare the second snapshot with the first acknowledged
  // game snapshot, allowing the player to return an option to its old value.
  let editRelease;const editWait=new Promise(resolve=>editRelease=resolve);const edits=[];
  game=create({revision:2,content:btoa('own options')},{storage:storage(),request:async row=>{
    edits.push(structuredClone(row));if(edits.length===1)await editWait;
    return {revision:row.revision+1,content:btoa('merged own options '+edits.length)};
  }});
  game.beginTakeover(btoa('recorded options'));game.save(KEY,'volume 20');game.save(KEY,'recorded options');
  editRelease();await game.flush();assert.equal(edits.length,2);
  assert.equal(edits[0].baseline,btoa('recorded options'));assert.equal(edits[1].baseline,btoa('volume 20'));
  // An offline takeover delta survives a new launch, where only the merged
  // personal options are exposed to the new game.
  const recovery=storage();
  game=create({revision:7,content:btoa('own bindings')},{storage:recovery,request:async()=>{throw Error('offline');}});
  game.beginTakeover(btoa('imported bindings'));game.save(KEY,'imported bindings and volume 30');await game.flush();
  const pending=JSON.parse(recovery.getItem(CACHE));assert.equal(pending.queue[0].baseline,btoa('imported bindings'));
  game=create({revision:7,content:btoa('own bindings')},{storage:recovery,request:async row=>{
    assert.equal(row.baseline,btoa('imported bindings'));return {revision:8,content:btoa('own bindings and volume 30')};
  }});
  assert.equal(game.load(KEY),'own bindings');await game.flush();assert.equal(game.load(KEY),'own bindings and volume 30');
  console.log('Original game settings: bytes, restart, cross-origin recovery, migration, rapid edits and offline preservation passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
