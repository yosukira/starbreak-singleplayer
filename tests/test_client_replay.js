const assert=require('node:assert/strict');
const {ClientReplay,prepareTakeover,gameplayKeys,takeoverKey}=require('../web/replay.js');

const mappedKeys=new Set([100,1073742048,1073742050,1073742051,1073742049]);
for(const [keyCode,key,flags] of [[68,100,{}],[17,1073742048,{ctrlKey:true}],[18,1073742050,{altKey:true}],[91,1073742051,{metaKey:true}],[16,1073742049,{shiftKey:true}]])assert.equal(takeoverKey({keyCode,...flags},key,mappedKeys),true,'Mapped modifier keys are game inputs too');
assert.equal(takeoverKey({keyCode:68,ctrlKey:true},100,mappedKeys),false,'Browser Ctrl+D is not a takeover gesture');
assert.equal(takeoverKey({keyCode:16,ctrlKey:true,shiftKey:true},1073742049,mappedKeys),false,'Browser Ctrl+Shift shortcuts do not start a takeover');
assert.equal(takeoverKey({keyCode:68,repeat:true},100,mappedKeys),false,'Repeated key events do not create another handoff');

function engine(){
  const HEAP32=new Int32Array(32768),module={HEAP32,_malloc:()=>24000,asm:{}};
  const world=1024,player=4096,subworld=8192;
  const set=(p,o,v)=>HEAP32[(p+o)>>2]=v;
  set(world,200,123);set(world,672,player);set(world,664,subworld);set(subworld,4,2);set(player,8,1);set(player,64,100);
  module.asm.replaySetInput=(w,p)=>{set(w,668,HEAP32[(p+4)>>2]);set(player,520,HEAP32[(p+4)>>2]);};
  return {module,world,player,set};
}

(async()=>{
  const defaults=gameplayKeys(null);
  assert.ok(defaults.has(1073741904)&&defaults.has(32)&&defaults.has(100));
  assert.ok(!defaults.has(113)&&!defaults.has(27)&&!defaults.has(104),'Unbound keys, menus and Home cannot take over');
  const field=(id,value)=>Buffer.concat([Buffer.from([id*8+2,value.length]),value]);
  const binding=field(1,Buffer.from([8,0,16,122]));
  const content=btoa(btoa(field(1,Buffer.concat([field(1,Buffer.from('impbstr')),field(2,binding)])).toString('binary')));
  const personal=gameplayKeys(content);assert.ok(personal.has(122));assert.ok(!personal.has(1073741904));assert.ok(personal.has(1073741903));
  let jobPolls=0,jobSleeps=0;
  const progress=[];
  const jobBranch=await prepareTakeover({cursor:10000},{
    request:async(url,options)=>({ok:true,json:async()=>{
      assert.ok(options.signal instanceof AbortSignal);
      if(url==='/api/replay/takeover'){
        assert.equal(JSON.parse(options.body).cursor,10000);return {job:'verification'};
      }
      assert.equal(url,'/api/replay/takeover?job=verification');
      return ++jobPolls>30?{state:'ready',result:{id:'new-branch'}}:{state:'pending',current:jobPolls,total:40};
    }}),sleep:async ms=>{assert.equal(ms,50);jobSleeps++;},notice:value=>progress.push(value)
  });
  assert.equal(jobBranch.id,'new-branch');assert.equal(jobSleeps,30);
  assert.equal(progress.length,30,'Long verification must continue beyond the old 20-second timeout');
  await assert.rejects(prepareTakeover({}, {request:async url=>({ok:true,json:async()=>url.includes('?')?{state:'failed',error:'Incompatible recording'}:{job:'failed'}})}),/Incompatible recording/);
  const chunks=[],source=engine();
  const protectionRows=[],protectionRecorder=new ClientReplay({module:source.module,keepOps:true});
  protectionRecorder.control('protection',[0xfedcba98,2,1,1],()=>protectionRows.push('applied'));
  assert.deepEqual(protectionRecorder.ops,[['control','protection',[0xfedcba98,2,1,1]]],'Keep unsigned room seeds intact in saved controls');
  const protectionPlayback=new ClientReplay({module:{replayControl:(kind,args)=>protectionRows.push([kind,args])},mode:'play',document:{ops:protectionRecorder.ops}});
  protectionPlayback.external();assert.deepEqual(protectionRows,['applied',['protection',[0xfedcba98,2,1,1]]]);
  const record=new ClientReplay({module:source.module,keepOps:true,save:async chunk=>chunks.push(chunk)});
  record.value('random',117);record.value('clock',123456789);
  record.loop(0);record.frame(source.world,17);record.frameEnd(source.world);record.loop(1);
  const input=source.module._malloc(24);source.set(input,4,8);source.set(input,8,8);source.set(input,12,0);source.set(input,20,1);
  record.control('key',[1,100],()=>{record.input(source.world,input);source.module.asm.replaySetInput(source.world,input);});
  record.loop(0);record.value('clock',123456806);record.frame(source.world,17);source.set(source.player,16,500);record.frameEnd(source.world);record.loop(1);
  await record.finish();
  assert.equal(chunks.at(-1).end,true);assert.deepEqual(chunks.flatMap(c=>c.ops),record.ops);
  const destination=engine(),notices=[];
  const replay=new ClientReplay({module:destination.module,mode:'play',document:{ops:record.ops},notice:value=>notices.push(value)});
  destination.module.replayControl=(kind,args)=>{
    assert.equal(kind,'key');assert.deepEqual(args,[1,100]);
    const p=destination.module._malloc(24);destination.set(p,4,8);destination.set(p,8,8);destination.set(p,12,0);destination.set(p,20,1);
    assert.equal(replay.input(destination.world,p),1);
    destination.module.asm.replaySetInput(destination.world,p);
  };
  assert.equal(replay.value('random',4),117);assert.equal(replay.value('clock',99999),123456789);
  assert.equal(replay.loop(0),true);assert.equal(replay.frame(destination.world,90),17);replay.frameEnd(destination.world);replay.loop(1);
  let physical=false;replay.control('key',[1,100],()=>physical=true);assert.equal(physical,false,'Physical gameplay input cannot change playback');
  assert.equal(replay.loop(0),true);assert.equal(destination.module.HEAP32[(destination.world+668)>>2],8,'Recorded input is executed through the original input handler');
  assert.equal(replay.value('clock',0),123456806);assert.equal(replay.frame(destination.world,100),17);destination.set(destination.player,16,500);replay.frameEnd(destination.world);replay.loop(1);
  assert.equal(replay.loop(0),false);assert.equal(replay.error,null);assert.equal(notices.at(-1).state,'ended');
  // Drop assistance can dispatch original key edges inside a render loop,
  // immediately before the engine's physics frame. Playback must execute
  // these recorded controls before that frame even with assistance disabled.
  const dropSource=engine(),dropRecord=new ClientReplay({module:dropSource.module,keepOps:true});
  const edge=(runner,target,pressed)=>{
    const ptr=target.module._malloc(24),total=pressed?24:8;
    target.set(ptr,4,total);target.set(ptr,8,pressed?16:0);target.set(ptr,12,pressed?0:16);target.set(ptr,20,7);
    runner.input(target.world,ptr);target.module.asm.replaySetInput(target.world,ptr);
  };
  dropRecord.loop(0);
  for(const pressed of [0,1])dropRecord.control('key',[pressed,32],()=>edge(dropRecord,dropSource,pressed));
  dropRecord.frame(dropSource.world,17);dropSource.set(dropSource.player,20,256);dropRecord.frameEnd(dropSource.world);dropRecord.loop(1);await dropRecord.finish();
  const dropTarget=engine(),dropPlayback=new ClientReplay({module:dropTarget.module,mode:'play',document:{ops:dropRecord.ops}});
  dropTarget.module.replayControl=(kind,values)=>{assert.equal(kind,'key');edge(dropPlayback,dropTarget,values[0]);};
  dropPlayback.loop(0);assert.equal(dropPlayback.frame(dropTarget.world,99),17);
  assert.equal(dropTarget.module.HEAP32[(dropTarget.world+668)>>2],24,'Both retry edges replay before physics');
  dropTarget.set(dropTarget.player,20,256);dropPlayback.frameEnd(dropTarget.world);dropPlayback.loop(1);
  assert.equal(dropPlayback.error,null);assert.equal(dropPlayback.loop(0),false);
  const corrupt=structuredClone(record.ops);corrupt.find(row=>row[0]==='state')[1][8]=99;
  const wrong=new ClientReplay({module:engine().module,mode:'play',document:{ops:corrupt}});
  wrong.value('random',0);wrong.value('clock',0);wrong.loop(0);wrong.frame(destination.world,17);wrong.frameEnd(destination.world);
  assert.match(wrong.error,/state diverged/);assert.equal(wrong.loop(0),false);

  // A full storage device must not change live input, clocks or simulation dt.
  const failure=new ClientReplay({module:engine().module,save:async()=>{throw Error('disk full');}});
  failure.write(['clock',1]);await failure.finish();
  assert.match(failure.error,/disk full/);assert.equal(failure.value('clock',55),55);assert.equal(failure.frame(1024,17),17);assert.equal(failure.loop(0),true);
  // Async HTTP/image completion cannot move the recorded game timeline. A
  // cold load waits; a warm load queues the callback until the same boundary.
  let applied=0;
  const delayed=new ClientReplay({module:engine().module,mode:'play',document:{ops:[['asset','texture:base.1.png'],['loop',0],['tick',12],['loop',1]]}});
  assert.equal(delayed.loop(0),false);assert.equal(delayed.cursor,0);assert.equal(delayed.error,null);
  delayed.asset('texture','texture/base.1.png',()=>applied++);
  assert.equal(applied,0);assert.equal(delayed.loop(0),true);assert.equal(applied,1);
  assert.equal(delayed.value('tick',999),12);delayed.loop(1);assert.equal(delayed.loop(0),false);assert.equal(delayed.error,null);
  const periodic=new ClientReplay({module:engine().module,mode:'play',document:{ops:[['timer'],['tick',500],['loop',0],['loop',1]]}});
  let timers=0;periodic.module.replayTimer=()=>{timers++;assert.equal(periodic.value('tick',10),500);};
  periodic.timer(()=>timers++);assert.equal(timers,0,'The browser interval cannot advance a recorded battle');
  assert.equal(periodic.loop(0),true);assert.equal(timers,1);periodic.loop(1);assert.equal(periodic.error,null);
  const controls=new ClientReplay({module:engine().module,mode:'play',document:{ops:[]}});
  let iterations=0;controls.setPaused(true);controls.iterate(()=>iterations++);assert.equal(iterations,0);
  controls.setPaused(false);controls.setSpeed(.5);controls.iterate(()=>iterations++);assert.equal(iterations,0);controls.iterate(()=>iterations++);assert.equal(iterations,1);
  controls.setSpeed(4);controls.iterate(()=>iterations++);assert.equal(iterations,5);
  // Real elapsed time, not the playback monitor's refresh rate, controls pace.
  for(const hz of [24,30,60,75,120,144,165,240,360,720])for(const speed of [.25,.5,1,2,4]){
    let now=0;
    const ops=[];
    for(let i=0;i<2001;i++)ops.push(['loop',0],['tick',i*5],['loop',1]);
    const timed=new ClientReplay({module:{},mode:'play',document:{ops},now:()=>now});
    timed.setSpeed(speed);
    const step=()=>{timed.loop(0);timed.value('tick',0);timed.loop(1);};
    timed.iterate(step);
    for(let frame=1;frame<=hz;frame++){now=frame*1000/hz;timed.iterate(step);}
    const elapsed=(timed.cursor/3-1)*5;
    assert.ok(Math.abs(elapsed-1000*speed)<=5,`${hz}Hz ${speed}x must follow recorded time: ${elapsed}`);
    timed.setPaused(true);now+=5000;timed.iterate(step);timed.setPaused(false);timed.iterate(step);
    assert.equal((timed.cursor/3-1)*5,elapsed,'Pausing cannot accumulate catch-up debt');
  }
  const seekEngine=engine(),seekState=new ClientReplay({module:seekEngine.module}).playerState(seekEngine.world),seekOps=[];
  for(let i=0;i<100;i++)seekOps.push(['loop',0],['tick',i*10],['frame',10],['state',seekState],['loop',1]);
  const seeking=new ClientReplay({module:seekEngine.module,mode:'play',document:{ops:seekOps},now:()=>0});
  seeking.seek(.5,true);
  seeking.iterate(()=>{seeking.loop(0);seeking.value('tick',0);seeking.frame(seekEngine.world,99);seeking.frameEnd(seekEngine.world);seeking.loop(1);});
  assert.equal(seeking.elapsed,500);assert.equal(seeking.paused,true);assert.equal(seeking.seekTarget,null);assert.equal(seeking.error,null);
  assert.equal(seeking.cursor,50*5,'Seeking executes and verifies every original operation in order');
  seeking.seek(.8,true);seeking.setPaused(false);
  seeking.iterate(()=>{seeking.loop(0);seeking.value('tick',0);seeking.frame(seekEngine.world,99);seeking.frameEnd(seekEngine.world);seeking.loop(1);});
  assert.equal(seeking.elapsed,800);assert.equal(seeking.paused,false,'Resume during a seek must not be undone when seeking finishes');
  seeking.seek(.95,true);seeking.setSpeed(2);seeking.setPaused(true);
  assert.equal(seeking.seekTarget,null,'Pause must cancel a pending seek at the current verified frame');
  assert.equal(seeking.paused,true);assert.equal(seeking.speed,2);assert.equal(seeking.elapsed,800);
  seeking.setPaused(false);seeking.seek(.95);seeking.cancelSeek(false);
  assert.equal(seeking.seekTarget,null);assert.equal(seeking.paused,false,'Keyboard takeover can cancel seeking without discarding the verified checkpoint');
  const initializeOps=[['loop',0],['tick',0],['loop',1],['loop',0],['tick',5000],['loop',1],
    ['loop',0],['tick',6000],['frame',17],['state',seekState],['loop',1]];
  const initialize=new ClientReplay({module:seekEngine.module,mode:'play',document:{ops:initializeOps},now:()=>0});
  initialize.setPaused(true);assert.equal(initialize.pauseWhenReady,true);
  initialize.iterate(()=>{
    initialize.loop(0);initialize.value('tick',0);
    if(initialize.ops[initialize.cursor]?.[0]==='frame'){initialize.frame(seekEngine.world,99);initialize.frameEnd(seekEngine.world);}
    initialize.loop(1);
  });
  assert.equal(initialize.cursor,initializeOps.length,'Paused-at-start must still execute the recorded initialization');
  assert.equal(initialize.paused,true);assert.equal(initialize.pauseWhenReady,false);assert.equal(initialize.error,null);
  assert.equal(initialize.elapsed,17,'Initialization stops at the first verified player frame, without running the battle ahead');
  // Camera/audio decisions depend on the original logical viewport, even
  // when the player opens an imported recording in a larger window.
  const screenRecord=new ClientReplay({module:engine().module,keepOps:true});
  assert.deepEqual(screenRecord.viewport(1200,600),[1200,600]);
  const screenReplay=new ClientReplay({module:engine().module,mode:'play',document:{ops:screenRecord.ops}});
  assert.deepEqual(screenReplay.viewport(2560,1600),[1200,600]);assert.equal(screenReplay.error,null);
  const releaseEngine=engine(),releasePlayback=new ClientReplay({module:releaseEngine.module,mode:'play',document:{ops:[
    ['state',new ClientReplay({module:releaseEngine.module}).playerState(releaseEngine.world)],['control','key',[0,100]],['tick',8]
  ]}});
  let atEnd=false,releasedAtEnd=false;
  releaseEngine.module.replayControl=()=>{releasedAtEnd=atEnd;};
  atEnd=true;releasePlayback.frameEnd(releaseEngine.world);atEnd=false;
  assert.equal(releasedAtEnd,true,'Takeover tap releases inside the same frame callback, before the engine resumes');
  assert.equal(releasePlayback.value('tick',99),8);assert.equal(releasePlayback.error,null);
  const resized=new ClientReplay({module:engine().module,mode:'play',document:{ops:[['control','resize',[]],['viewport',1218,600],['loop',0]]}});
  resized.module.replayControl=kind=>{assert.equal(kind,'resize');assert.deepEqual(resized.viewport(2560,1600),[1218,600]);};
  assert.equal(resized.loop(0),true);assert.equal(resized.error,null);
  // A takeover preserves the recorded time origin, including when storage
  // fails, and releases keys held by the previous player before live input.
  const branchEngine=engine(),branch=new ClientReplay({module:branchEngine.module,mode:'play',document:{ops:[]},keepOps:true});
  const watchOnly=new ClientReplay({module:branchEngine.module,mode:'play',document:{ops:[]},takeoverAllowed:false});
  watchOnly.world=branchEngine.world;
  assert.equal(watchOnly.canTakeOver(),false,'A client tape without compatible server state is watchable without offering a broken takeover');
  assert.throws(()=>watchOnly.becomeLive({send(){},close(){}},{sequence:0}),/verified living player/);
  branch.world=branchEngine.world;branch.liveSocket={};branch.heldKeys.add(100);branch.lastValues.tick=1234;
  const injected=[];branchEngine.module.replayControl=(...args)=>injected.push(args);
  branch.becomeLive({send(){},close(){}},{sequence:3},[97]);
  assert.deepEqual(injected,[['key',[0,100]],['reset',[]],['key',[1,97]]]);
  assert.equal(branch.sequence,3);assert.equal(branch.value('tick',9999),1251);
  branch.lastValues.random=250;
  assert.equal(branch.value('random',0),0,'Takeover must preserve fresh entropy rather than offsetting it like a clock');
  assert.equal(branch.value('random',255),255,'Every subsequent random byte stays inside the journal format');
  assert.equal(branch.liveOffsets.random,undefined);
  branch.fail('storage unavailable');assert.equal(branch.value('tick',10009),1261);
  assert.equal(branch.loop(0),true);assert.equal(branch.frame(branch.world,17),17);
  const prefEngine=engine(),prefBranch=new ClientReplay({module:prefEngine.module,mode:'play',document:{ops:[]},keepOps:true});
  prefBranch.world=prefEngine.world;prefBranch.liveSocket={};prefBranch.heldKeys.add(100);
  const order=[];
  prefEngine.module.replayControl=(kind,args)=>order.push([kind,args]);
  prefEngine.module.replayPreferences=content=>order.push(['preferences',content]);
  prefBranch.becomeLive({send(){},close(){}},{sequence:0},[122],null,()=>prefBranch.preferences('personal-options'));
  assert.deepEqual(order,[['key',[0,100]],['reset',[]],['preferences','personal-options'],['key',[1,122]]]);
  const playOrder=[],prefPlayback=new ClientReplay({module:{replayControl:(kind,args)=>playOrder.push([kind,args]),replayPreferences:content=>playOrder.push(['preferences',content])},mode:'play',document:{ops:prefBranch.pending}});
  prefPlayback.external();assert.deepEqual(playOrder,order);
  assert.throws(()=>prefPlayback.preferences('other'),/Cannot change playback/);
  const exitChunks=[],exiting=new ClientReplay({module:engine().module,keepOps:true,stage:chunk=>exitChunks.push(chunk)});
  exiting.loop(0);exiting.value('tick',10);await exiting.quit(0);
  assert.deepEqual(exiting.ops,[['loop',0],['tick',10],['exit',0],['loop',1]]);
  assert.equal(exiting.finished,true);assert.equal(exitChunks.at(-1).end,true,'Explicit Exit Game durably seals the tape immediately');
  const exitNotices=[],exitPlayback=new ClientReplay({module:engine().module,mode:'play',document:{ops:exiting.ops},notice:value=>exitNotices.push(value)});
  exitPlayback.loop(0);exitPlayback.value('tick',99);exitPlayback.quit(0);
  assert.equal(exitPlayback.error,null);assert.equal(exitPlayback.finished,true);assert.equal(exitNotices.at(-1).state,'ended');
  const badExit=new ClientReplay({module:engine().module,mode:'play',document:{ops:[['exit',1]]}});
  badExit.quit(0);assert.match(badExit.error,/exit status changed/);
  const quitChunks=[],quitNotices=[];let stopped=0;
  const leaving=new ClientReplay({module:{replayStop(){stopped++;}},socket:class{constructor(){throw Error('Login reconnect must not open');}},keepOps:true,stage:chunk=>quitChunks.push(chunk)});
  leaving.loop(0);
  leaving.control('button',[1,2,0,0],()=>{leaving.requestQuit();leaving.value('tick',27);});
  assert.equal(leaving.finished,false,'Quit must let the current native callback and frame finish');
  assert.equal(leaving.createSocket('ws://localhost','binary').readyState,3);
  leaving.loop(1);assert.equal(leaving.finished,true);assert.equal(stopped,1);assert.equal(quitChunks.at(-1).end,true);
  const quitModule={replayStop(){stopped++;},replayControl(){quitPlayback.requestQuit();quitPlayback.value('tick',0);}};
  const quitPlayback=new ClientReplay({module:quitModule,mode:'play',document:{ops:leaving.ops},notice:row=>quitNotices.push(row)});
  quitPlayback.loop(0);quitPlayback.loop(1);
  assert.equal(quitPlayback.error,null);assert.equal(quitPlayback.finished,true);assert.equal(stopped,2);assert.equal(quitNotices.at(-1).state,'ended');
  const outside=new ClientReplay({module:{replayStop(){}},keepOps:true});
  outside.control('key',[1,27],()=>{outside.requestQuit();outside.value('clock',100);});
  assert.equal(outside.finished,true);assert.deepEqual(outside.ops,[['control','key',[1,27]],['quit'],['clock',100]]);
  const outsideModule={replayStop(){},replayControl(){outsidePlayback.requestQuit();outsidePlayback.value('clock',0);}};
  const outsidePlayback=new ClientReplay({module:outsideModule,mode:'play',document:{ops:outside.ops}});
  outsidePlayback.external();assert.equal(outsidePlayback.error,null);assert.equal(outsidePlayback.finished,true);
  console.log('Client journal: exact inputs/timing, state divergence, durable chunks, and nonblocking storage failures passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
