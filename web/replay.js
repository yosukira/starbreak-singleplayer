/* Execution journal for the pinned original client. No heap snapshots or code
 * from a recording are ever loaded. Playback runs the installed game engine. */
(() => {
  'use strict';
  const bytes = value => value instanceof ArrayBuffer ? new Uint8Array(value) : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  const hash = value => {let result=2166136261;for(const byte of bytes(value)){result^=byte;result=Math.imul(result,16777619);}return result>>>0;};
  const base64 = value => {const data=bytes(value);let output='';for(let i=0;i<data.length;i+=8192)output+=String.fromCharCode(...data.subarray(i,i+8192));return btoa(output);};
  const unbase64 = value => Uint8Array.from(atob(value),c=>c.charCodeAt(0)).buffer;

  function gameplayKeys(content,action=null){
    // Original 1.14.6 defaults. Controller buttons and menu/emote bindings
    // are not takeover gestures. Explicit personal bindings replace a row.
    const bindings=[[1073741904],[1073741903],[1073741906],[1073741905],[32,102],[100],[115],[97],[],[1073742049,1073742053],[],[],[],[]];
    function fields(bytes){
      let cursor=0;const result=[];
      const integer=()=>{let n=0,scale=1;for(let i=0;i<10;i++){if(cursor>=bytes.length)throw Error();const b=bytes[cursor++];n+=(b&127)*scale;if(!(b&128))return n;scale*=128;}throw Error();};
      while(cursor<bytes.length){const tag=integer(),kind=tag%8;let value;
        if(kind===0)value=integer();
        else if(kind===2){const size=integer();if(cursor+size>bytes.length)throw Error();value=bytes.subarray(cursor,cursor+size);cursor+=size;}
        else throw Error();
        result.push([Math.floor(tag/8),kind,value]);
      }return result;
    }
    try{
      if(content){
        const bytes=new Uint8Array(unbase64(atob(content)));
        if(bytes.length>65536)throw Error();
        for(const [,kind,entry] of fields(bytes))if(kind===2){
          const pair=fields(entry),name=pair.find(v=>v[0]===1)?.[2],value=pair.find(v=>v[0]===2)?.[2];
          if(!name||String.fromCharCode(...name)!=='impbstr'||!value)continue;
          for(const [field,wire,row] of fields(value))if(field===1&&wire===2){
            const values=fields(row),action=values.find(v=>v[0]===1&&v[1]===0)?.[2]??0;
            if(action>=0&&action<14&&action!==8)bindings[action]=values.filter(v=>v[1]===0&&[2,3,4].includes(v[0])).map(v=>v[2]);
          }
        }
      }
    }catch(_){}
    return new Set((action===null?bindings.flat():bindings[action]||[]).filter(v=>Number.isInteger(v)&&v>0&&(v<0x20000000||v>=0x40000000)));
  }

  function takeoverKey(event,key,keys){
    const code=event.keyCode||event.which||0;
    return !event.repeat&&keys.has(key)&&(!event.ctrlKey||code===17)&&
      (!event.altKey||code===18)&&(!event.metaKey||[91,92,93].includes(code));
  }

  function installPlatformDrop(module,{now:clock=()=>performance.now()}={}){
    let lastWorld=0;
  let dropIntent=null,dropStep=0,retryingDrop=false;
  function dropState(world){
    const h=module.HEAP32;if(!h)return null;const read=(p,o)=>h[(p+o)>>2]|0;
    const valid=(p,size)=>p>0&&!(p&3)&&p+size<=h.byteLength;
    if(!valid(world,700))return null;
    const player=read(world,672);if(!valid(player,744)||read(player,64)<=0)return null;
    const owner=read(player,304),id=read(player,308),key=world+'/'+(read(world,200)>>>0);
    for(const sub of [read(world,628),read(world,664)]){
      if(!valid(sub,144)||read(sub,4)!==owner)continue;
      const first=read(sub,60),end=read(sub,64);
      if(!first||end<first||(end-first)%8||end-first>8192*8||!valid(first,end-first))continue;
      for(let p=first;p<end;p+=8){const obj=read(p,4);if(valid(obj,216)&&read(obj,8)===id)return {key,total:read(world,668),support:read(obj,76)===2?owner+'/'+id:'solid'};}
    }
    return {key,total:read(world,668),support:null};
  }
  module.webDropControl=(kind,values)=>{
    if(module.clientReplay?.mode==='play'||retryingDrop||kind!=='key'||!values[0])return;
    const saved=module.gamePreferences?.load('LocalStorage.pb');
    if(!gameplayKeys(saved?btoa(saved):null,4).has(values[1]))return;
    const state=dropState(module.clientReplay?.world||lastWorld),now=clock();
    if(!state||!(state.total&8)||state.total&16||!state.support||state.support==='solid')return;
    if(dropIntent&&dropIntent.key===state.key&&dropIntent.support===state.support&&now<=dropIntent.until)return;
    dropIntent={key:state.key,support:state.support,step:dropStep,until:now+250};
  };
  function retryDrop(world){
    if(!dropIntent||module.clientReplay?.mode==='play')return;
    const state=dropState(world),now=clock();
    if(!state||state.key!==dropIntent.key||now>dropIntent.until||(state.total&24)!==24||state.support!==null&&state.support!==dropIntent.support){dropIntent=null;return;}
    if(state.support!==dropIntent.support||dropStep<=dropIntent.step)return;
    dropIntent.step=dropStep;
    // Use the current mapped jump key and the original event dispatcher.
    // These controls are recorded, so old and new tapes replay exact inputs.
    const saved=module.gamePreferences?.load('LocalStorage.pb');
    const jump=[...gameplayKeys(saved?btoa(saved):null,4)][0];if(!jump)return;
    retryingDrop=true;
    try{for(const pressed of [0,1]){const apply=()=>module.replayControl('key',[pressed,jump]);if(module.clientReplay)module.clientReplay.control('key',[pressed,jump],apply);else apply();}}
    finally{retryingDrop=false;}
  }
    return {frame(world){lastWorld=world;dropStep++;retryDrop(world);},get intent(){return dropIntent;}};
  }

  function applyPlayerProtection(module,world,values){
    if(!Array.isArray(values)||values.length!==4||values.some(value=>!Number.isInteger(value))||![0,1].includes(values[3]))throw Error('Invalid player protection control');
    const h=module.HEAP32,valid=(p,size)=>p>0&&!(p&3)&&p+size<=h.byteLength;
    if(!valid(world,700))throw Error('Player protection world is unavailable');
    const player=h[(world+672)>>2],sub=h[(world+664)>>2];
    if(!valid(player,744)||!valid(sub,144)||h[(player+8)>>2]!==values[2]||h[(sub+4)>>2]!==values[1]||(h[(world+200)>>2]>>>0)!==values[0])throw Error('Player protection target changed');
    module.HEAPU8[player+103]=values[3];
  }
  function installPlayerProtection(module){
    let world=0;
    module.applyProtectionFlag=values=>applyPlayerProtection(module,module.clientReplay?.world||world,values);
    module.applyPlayerProtection=values=>module.nativeJournal?module.nativeJournal.protection(values):module.applyProtectionFlag(values);
    return {frame(current){
      world=current;if(module.clientReplay?.mode==='play')return;
      const command=module.pendingPlayerProtection;if(!command||typeof command.enabled!=='boolean')return;
      const h=module.HEAP32,player=h[(world+672)>>2],sub=h[(world+664)>>2];if(!player||!sub)return;
      const values=[command.room_seed,command.worldid,command.objid,Number(command.enabled)];
      if((h[(world+200)>>2]>>>0)!==values[0]||h[(player+8)>>2]!==values[2]||h[(sub+4)>>2]!==values[1]||!!module.HEAPU8[player+103]===command.enabled)return;
      const apply=()=>module.replayControl('protection',values);
      if(module.clientReplay)module.clientReplay.control('protection',values,apply);else apply();
    }};
  }

  class ClientReplay {
    constructor({module, mode='record', document=null, socket=globalThis.WebSocket, save=null, stage=null, notice=()=>{}, keepOps=false, takeoverAllowed=true, now=()=>performance.now()}) {
      this.module=module;this.mode=mode;this.NativeSocket=socket;this.save=save;this.notice=notice;this.keepOps=keepOps;
      this.ops=document?.ops||[];this.cursor=0;this.pending=[];this.pendingSize=0;this.totalSize=0;this.queuedSize=0;this.sequence=0;
      this.chain=Promise.resolve();this.error=null;this.finished=false;this.inLoop=false;this.injecting=false;this.world=0;
      this.liveSocket=null;this.frameCount=0;this.lastSeed=null;this.hadPlayer=false;this.endAfterLoop=false;
      this.sendQueue=[];
      this.assets=new Map();this.blockedAsset=false;this.lastValues={};
      this.paused=false;this.pauseWhenReady=false;this.worldReady=false;this.speed=1;this.iterationCredit=0;
      this.hasPlayerFrames=this.mode==='play'&&this.ops.some(row=>row[0]==='state'&&row[1].length===14);
      this.now=now;this.wall=null;this.playTime=0;this.scheduleIndex=0;
      // Iterations without a physics step still consume recorded time. A tape
      // made at 190 Hz must retain its pace on a 60 Hz display.
      this.schedule=[];this.seekTarget=null;this.seekPaused=false;
      if(mode==='play'){
        let previous=0,origin=null;
        for(let i=0;i<this.ops.length;i++)if(this.ops[i][0]==='loop'&&this.ops[i][1]===0){
          let tick=null,dt=0;
          for(let j=i+1;j<this.ops.length&&this.ops[j][0]!=='loop';j++){
            if(tick===null&&this.ops[j][0]==='tick')tick=this.ops[j][1];
            if(this.ops[j][0]==='frame')dt+=this.ops[j][1];
          }
          if(tick!==null){if(origin===null)origin=tick;previous=Math.max(previous,tick-origin);}
          else if(this.schedule.length)previous+=dt||1000/60;
          this.schedule.push({cursor:i,time:previous});
        }
      }
      this.elapsed=0;this.totalDuration=this.mode==='play'?this.ops.reduce((sum,row)=>sum+(row[0]==='frame'?row[1]:0),0):0;
      this.heldKeys=new Set();this.liveOffsets={};this.releaseAfterFrame=[];
      this.takeoverAllowed=takeoverAllowed;this.lastPlaybackNotice=-Infinity;
      this.stage=stage;
      this.leaving=false;this.runtimeStopped=false;
    }
    fail(error) {
      if(this.error)return;
      if(typeof window!=='undefined')console.error('Replay stopped:',error);
      this.error=String(error?.message||error);this.notice({state:'failed',message:this.error});
    }
    write(op) {
      if(this.finished||this.error)return;
      const size=JSON.stringify(op).length+1;
      if(this.totalSize+size>128*1024*1024||this.queuedSize+this.pendingSize+size>4*1024*1024){this.fail('Replay storage cannot keep up');return;}
      if(this.pendingSize+size>450000)this.flush();
      this.pending.push(op);this.pendingSize+=size;this.totalSize+=size;
      if(this.keepOps)this.ops.push(op);
      if(this.pendingSize>100000||this.pending.length>=1000)this.flush();
    }
    flush(end=false) {
      if(this.mode!=='record'||this.error)return this.chain;
      if(!this.pending.length&&!end)return this.chain;
      const document={sequence:this.sequence++,ops:this.pending,end},size=this.pendingSize;
      this.pending=[];this.pendingSize=0;this.queuedSize+=size;
      try{this.stage?.(document);}catch(error){this.fail(error);return this.chain;}
      if(this.save)this.chain=this.chain.then(async()=>{
        // Identical sequence/data are retryable; the local store deduplicates.
        let last;
        for(let attempt=0;attempt<3;attempt++){
          try{await this.save(document);this.queuedSize-=size;return;}
          catch(error){last=error;if(attempt<2)await new Promise(resolve=>setTimeout(resolve,200*(attempt+1)));}
        }
        this.fail(last);
      });
      else this.queuedSize-=size;
      return this.chain;
    }
    finish() {
      if(this.finished)return this.chain;
      this.finished=true;
      return this.flush(true);
    }
    quit(status) {
      if(this.finished)return this.chain;
      try{
        if(this.mode==='record'){
          this.write(['exit',status]);
          // Original _exit throws ExitStatus before postMainLoop can run.
          // Close only that execution scope; never synthesize a game frame.
          if(this.inLoop){this.write(['loop',1]);this.inLoop=false;}
          return this.finish();
        }
        if(this.next('exit')[1]!==status)throw Error('Client replay exit status changed');
        if(this.inLoop){if(this.next('loop')[1]!==1)throw Error('Client replay exited outside its loop boundary');this.inLoop=false;}
        if(this.cursor!==this.ops.length)throw Error('Client replay exited before its recorded end');
        this.finished=true;this.notice({state:'ended',seconds:this.elapsed/1000,duration:this.totalDuration/1000});
      }catch(error){this.fail(error);}
    }
    requestQuit() {
      if(this.leaving)return;
      this.leaving=true;
      try{if(this.mode==='record')this.write(['quit']);else this.next('quit');}
      catch(error){this.fail(error);}
    }
    stopIfLeaving() {
      if(!this.leaving||this.inLoop||this.runtimeStopped)return;
      this.runtimeStopped=true;
      if(this.mode==='play'&&this.cursor!==this.ops.length)this.fail('Client replay quit before its recorded end');
      this.finish();this.module.replayStop?.();
      if(this.mode==='play'&&!this.error)this.notice({state:'ended',seconds:this.elapsed/1000,duration:this.totalDuration/1000});
      else if(this.mode==='record')this.notice({state:'closed'});
    }
    next(kind) {
      this.external();
      const op=this.ops[this.cursor++];
      if(!op||op[0]!==kind)throw Error(`Client replay diverged at operation ${this.cursor-1} (${kind})`);
      return op;
    }
    external() {
      this.blockedAsset=false;
      while(this.cursor<this.ops.length){
        const op=this.ops[this.cursor];
        if(op[0]==='asset'){
          const queue=this.assets.get(op[1]);
          if(!queue?.length){this.blockedAsset=true;break;}
          this.cursor++;queue.shift()();
        } else if(op[0]==='timer'){
          this.cursor++;this.module.replayTimer();
        } else if(op[0]==='preferences'){
          this.cursor++;this.module.replayPreferences(op[1]);
        } else if(op[0]==='control'){
          this.cursor++;
          if(op[1]==='key')op[2][0]?this.heldKeys.add(op[2][1]):this.heldKeys.delete(op[2][1]);
          if(op[1]==='reset')this.heldKeys.clear();
          this.injecting=true;
          try{this.module.replayControl(op[1],op[2]);}finally{this.injecting=false;this.stopIfLeaving();}
        } else if(op[0]==='send'&&this.sendQueue.length){
          const actual=this.sendQueue.shift();this.cursor++;
          if(op[1]!==actual[0]||op[2]!==actual[1])throw Error(`Client replay outgoing data changed at operation ${this.cursor-1} (${op[1]}/${actual[0]} bytes)`);
        } else if(['open','net','close'].includes(op[0])) {
          if(!this.liveSocket)break;
          this.cursor++;
          if(op[0]==='open'){this.liveSocket.readyState=1;this.liveSocket.onopen?.({type:'open'});}
          else if(op[0]==='net')this.liveSocket.onmessage?.({data:unbase64(op[1])});
          else{this.liveSocket.readyState=3;this.liveSocket.onclose?.({type:'close'});}
        } else break;
      }
    }
    value(kind, actual) {
      if((this.error||this.finished)&&this.mode==='play')return actual;
      try{
        if(this.mode==='record'){
          if(this.live&&['clock','time','tick'].includes(kind)){
            if(!(kind in this.liveOffsets)&&kind in this.lastValues)this.liveOffsets[kind]=this.lastValues[kind]+(kind==='tick'?17:0)-actual;
            actual+=this.liveOffsets[kind]||0;
          }
          this.lastValues[kind]=actual;this.write([kind,actual]);return actual;
        }
        this.external();
        if(this.blockedAsset&&!this.inLoop)return this.lastValues[kind]??actual;
        const value=this.next(kind)[1];this.lastValues[kind]=value;return value;
      }catch(error){this.fail(error);return actual;}
    }
    loop(stage) {
      if(stage===0&&this.mode==='play'&&this.paused&&this.seekTarget===null)return false;
      if(this.error||this.finished){if(stage===1){this.inLoop=false;this.stopIfLeaving();}return this.mode==='record';}
      try{
        if(this.mode==='record')this.write(['loop',stage]);
        else{
          this.external();
          if(stage===0&&this.blockedAsset)return false;
          if(stage===0&&this.cursor===this.ops.length){this.finished=true;this.notice({state:'ended',seconds:this.elapsed/1000,duration:this.totalDuration/1000});return false;}
          if(this.next('loop')[1]!==stage)throw Error('Client replay loop order changed');
        }
        this.inLoop=stage===0;
        if(stage===1&&this.endAfterLoop)this.finish();
        if(stage===1)this.stopIfLeaving();
        return true;
      }catch(error){this.fail(error);return this.mode==='record';}
    }
    input(world, pointer) {
      this.world=world;
      const heap=this.module.HEAP32;
      const op=['input',heap[(pointer+4)>>2],heap[(pointer+8)>>2],heap[(pointer+12)>>2],heap[(pointer+20)>>2]];
      if(this.mode==='play'){
        try{if(JSON.stringify(this.next('input'))!==JSON.stringify(op))throw Error('Client replay input changed');}
        catch(error){this.fail(error);return 0;}
      } else this.write(op);
      return 1;
    }
    control(kind, values, callback) {
      if(this.mode==='play')return;
      if(kind==='key')values[0]?this.heldKeys.add(values[1]):this.heldKeys.delete(values[1]);
      if(kind==='reset')this.heldKeys.clear();
      this.write(['control',kind,kind==='protection'?values.slice():values.map(value=>value|0)]);
      try{this.module.webDropControl?.(kind,values);callback();}finally{this.stopIfLeaving();}
    }
    preferences(content) {
      if(this.mode!=='record')throw Error('Cannot change playback options');
      this.write(['preferences',content]);
      this.module.replayPreferences(content);
    }
    timer(callback) {
      if(this.mode==='play')return;
      this.write(['timer']);callback();
    }
    iterate(callback) {
      if(this.mode==='record'){callback();return;}
      if(this.paused||this.handoffPending||this.finished||this.error)return;
      if(!this.schedule.length){
        this.iterationCredit+=this.speed;
        const count=Math.floor(this.iterationCredit);this.iterationCredit-=count;
        for(let i=0;i<count&&!this.paused&&!this.finished&&!this.error;i++)callback();
        return;
      }
      const started=this.now();
      if(this.wall!==null)this.playTime+=Math.min(250,Math.max(0,started-this.wall))*this.speed;
      this.wall=started;
      let count=0;
      while(!this.paused&&!this.handoffPending&&!this.finished&&!this.error){
        while(this.scheduleIndex<this.schedule.length&&this.schedule[this.scheduleIndex].cursor<this.cursor)this.scheduleIndex++;
        const next=this.schedule[this.scheduleIndex];
        if(this.seekTarget===null&&!this.pauseWhenReady&&next&&next.time>this.playTime)break;
        const cursor=this.cursor;callback();
        if(this.cursor===cursor||this.blockedAsset){
          // Loading is outside playback time; do not sprint to catch up after
          // an image download or a backgrounded browser tab.
          this.playTime=next?.time??this.playTime;this.wall=null;break;
        }
        if(this.seekTarget!==null&&this.elapsed>=this.seekTarget&&(!this.seekPaused||this.worldReady||!this.hasPlayerFrames)){
          this.seekTarget=null;this.paused=this.seekPaused;this.wall=null;
          this.playTime=this.schedule[this.scheduleIndex]?.time??this.playTime;
          this.notice({state:this.paused?'paused':'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.canTakeOver()});break;
        }
        if(++count>=240||this.now()-started>=12)break;
      }
    }
    seek(seconds, paused=false){
      if(this.mode!=='play'||this.finished||this.error||this.handoffPending||!Number.isFinite(seconds))return;
      this.seekTarget=Math.max(this.elapsed,Math.min(this.totalDuration,Math.max(0,seconds)*1000));
      this.seekPaused=paused;this.paused=false;this.pauseWhenReady=false;this.wall=null;
      this.notice({state:'seeking',seconds:this.elapsed/1000,duration:this.totalDuration/1000,target:this.seekTarget/1000});
    }
    setPaused(value) {
      if(this.mode!=='play'||this.finished||this.error||this.handoffPending)return;
      // A newer play/pause request also owns the state after an in-flight seek.
      // Otherwise completion can silently restore the seek's older pause flag.
      if(this.seekTarget!==null){
        this.seekPaused=value===true;
        if(value===true)this.cancelSeek(true);
      }
      // Initial UI/network/texture callbacks must reach a real player frame
      // before pausing. Otherwise seeking back to 0 while paused deadlocks the
      // loading overlay and leaves its navigation permanently disabled.
      this.pauseWhenReady=value===true&&!this.worldReady&&this.hasPlayerFrames;
      this.paused=value===true&&!this.pauseWhenReady;this.wall=null;
      if(!this.pauseWhenReady)this.notice({state:this.paused?'paused':'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.canTakeOver()});
    }
    cancelSeek(paused=false){
      if(this.mode!=='play'||this.seekTarget===null||this.handoffPending)return;
      this.seekTarget=null;this.seekPaused=paused;this.paused=paused;this.wall=null;
      this.playTime=this.schedule.find(row=>row.cursor>=this.cursor)?.time??this.elapsed;
      this.notice({state:paused?'paused':'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.canTakeOver()});
    }
    setSpeed(value) {
      if([.25,.5,1,2,4].includes(value)){this.speed=value;this.wall=null;}
    }
    asset(kind, path, callback) {
      if(this.error||this.finished){callback();return;}
      const key=kind+':'+path.split('/').pop();
      if(this.mode==='record'){this.write(['asset',key]);callback();}
      else{
        const queue=this.assets.get(key)||[];queue.push(callback);this.assets.set(key,queue);
        // Async completion only makes data ready. The original callback runs
        // at its recorded boundary, regardless of HTTP/image decode speed.
      }
    }
    frame(world, dt) {
      this.world=world;
      if(this.error||this.finished)return dt;
      try{
        if(this.mode==='record'){this.write(['frame',dt]);return dt;}
        const recorded=this.next('frame')[1];this.elapsed+=recorded;return recorded;
      }catch(error){this.fail(error);return dt;}
    }
    viewport(width,height) {
      if(this.mode==='record'){if(this.module.webZoom)[width,height]=this.module.webZoom.viewport(width,height);this.write(['viewport',width,height]);return [width,height];}
      try{return this.next('viewport').slice(1);}
      catch(error){this.fail(error);return [width,height];}
    }
    playerState(world) {
      const heap=this.module.HEAP32,read=(p,o)=>heap[(p+o)>>2]|0;
      const seed=read(world,200)>>>0,total=read(world,668)>>>0,player=read(world,672),subworld=read(world,664);
      if(player<=0||subworld<=0||player+744>=heap.byteLength||subworld+8>=heap.byteLength)return [seed,total];
      return [seed,total,read(player,8),read(subworld,4),...[16,20,24,28,64,68,40,540,520].map(offset=>read(player,offset)),Number(read(player,64)>0)];
    }
    canTakeOver() {
      if(this.seekTarget!==null||!this.takeoverAllowed||this.mode!=='play'||this.error||this.finished||this.inLoop||this.sendQueue.length||!this.world)return false;
      const state=this.playerState(this.world);
      return state.length===14&&state[8]>0&&state[13]===1;
    }
    becomeLive(transport, branch, physicalKeys=[], tap=null, beforeInput=null) {
      if(!this.canTakeOver())throw Error('No verified living player to take over');
      const held=[...this.heldKeys];
      this.mode='record';this.live=true;this.paused=false;this.speed=1;this.finished=false;
      this.sequence=branch.sequence;this.pending=[];this.pendingSize=0;this.queuedSize=0;this.totalSize=0;
      const peer=this.liveSocket;
      peer.send=value=>{this.write(['send',bytes(value).byteLength,hash(value)]);transport.send(value);};
      peer.close=()=>transport.close();
      for(const key of held)this.control('key',[0,key],()=>this.module.replayControl('key',[0,key]));
      this.control('reset',[],()=>this.module.replayControl('reset',[]));
      beforeInput?.();
      for(const key of physicalKeys)this.control('key',[1,key],()=>this.module.replayControl('key',[1,key]));
      if(tap!==null&&!physicalKeys.includes(tap)){
        this.control('key',[1,tap],()=>this.module.replayControl('key',[1,tap]));this.releaseAfterFrame.push(tap);
      }
      for(const [key,callbacks] of this.assets){
        while(callbacks.length){this.write(['asset',key]);callbacks.shift()();}
      }
      this.assets.clear();
      this.ops=[];
    }
    receiveLive(data) {
      this.write(['net',base64(data)]);this.liveSocket.onmessage?.({data});
    }
    frameEnd(world) {
      if(this.error||this.finished)return;
      try{
        const state=this.playerState(world);this.frameCount++;
        if(this.mode==='record'){
          this.write(['state',state]);
          if(state[0]!==this.lastSeed){this.lastSeed=state[0];this.hadPlayer=false;}
          if(state.length===14&&state[8]>0)this.hadPlayer=true;
          else if(this.hadPlayer)this.endAfterLoop=true;
          if(this.frameCount%60===0)this.flush();
          for(const key of this.releaseAfterFrame.splice(0))this.control('key',[0,key],()=>this.module.replayControl('key',[0,key]));
        } else {
          if(JSON.stringify(this.next('state')[1])!==JSON.stringify(state))throw Error(`Client player state diverged at frame ${this.frameCount}`);
          if(state.length===14)this.worldReady=true;
          // A short takeover tap is released at this exact engine boundary.
          // Delaying it until the next clock import changes input timestamps.
          this.external();
          const now=this.now();
          const initialized=this.pauseWhenReady&&this.worldReady;
          if(initialized){this.pauseWhenReady=false;this.paused=true;this.wall=null;}
          if(initialized||this.frameCount===1||now-this.lastPlaybackNotice>=100){this.lastPlaybackNotice=now;this.notice({state:this.seekTarget!==null?'seeking':this.paused?'paused':'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,target:this.seekTarget===null?undefined:this.seekTarget/1000,takeover:this.seekTarget===null&&this.takeoverAllowed&&state.length===14&&state[8]>0});}
        }
      }catch(error){this.fail(error);}
    }
    createSocket(url, protocols) {
      // Exit Game returns the retail client to its online account screen.
      // The offline battle ends at its confirmed callback; do not open a new
      // login connection after that callback while closing the current frame.
      if(this.leaving)return {readyState:3,bufferedAmount:0,binaryType:'arraybuffer',send(){},close(){},addEventListener(){}};
      if(this.mode==='record'){
        const socket=new this.NativeSocket(url,protocols);this.liveSocket=socket;
        socket.addEventListener('open',()=>this.write(['open']));
        socket.addEventListener('message',event=>{try{this.write(['net',base64(event.data)]);}catch(error){this.fail(error);}});
        socket.addEventListener('close',()=>this.write(['close']));
        const send=socket.send.bind(socket);
        socket.send=value=>{this.write(['send',bytes(value).byteLength,hash(value)]);return send(value);};
        return socket;
      }
      const socket={readyState:0,CONNECTING:0,OPEN:1,CLOSING:2,CLOSED:3,bufferedAmount:0,binaryType:'arraybuffer',
        // Cached texture decoding can finish earlier during playback. Preserve
        // the recorded wire ordering without changing the original loader.
        send:value=>{try{if(this.sendQueue.length>256)throw Error('Replay outgoing queue exceeded');this.sendQueue.push([bytes(value).byteLength,hash(value)]);this.external();}catch(error){this.fail(error);}},
        close:()=>{socket.readyState=3;}};
      this.liveSocket=socket;
      return socket;
    }
  }

  async function prepareTakeover(document,{request=fetch,sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),notice=()=>{}}={}){
    async function read(url,options){
      const response=await request(url,{...options,signal:AbortSignal.timeout(10000)});
      const result=await response.json();if(!response.ok)throw Error(result.error||'Takeover verification unavailable');
      return result;
    }
    const job=await read('/api/replay/takeover',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(document)});
    if(typeof job.job!=='string')throw Error('Invalid takeover verification');
    // Each HTTP request stays short; reconstructing a long battle may take
    // minutes. Keep the original replay paused until verification completes.
    for(let poll=0;poll<12000;poll++){
      const status=await read('/api/replay/takeover?job='+encodeURIComponent(job.job));
      if(status.state==='ready')return status.result;
      if(status.state!=='pending')throw Error(status.error||'Takeover verification failed');
      notice(status.total>0?Math.min(99,Math.floor(100*status.current/status.total)):0);
      await sleep(50);
    }
    throw Error('Takeover verification timed out');
  }

  if(typeof module!=='undefined'&&module.exports){module.exports={ClientReplay,hash,prepareTakeover,gameplayKeys,takeoverKey,installPlatformDrop,applyPlayerProtection,installPlayerProtection};return;}
  globalThis.StarBreakGameplayKeys=gameplayKeys;
  globalThis.StarBreakClientReplay=ClientReplay;
  const drop=installPlatformDrop(Module);
  const protection=installPlayerProtection(Module);
  const boot=globalThis.STARBREAK_WEB_BOOT?.replay;
  if(!boot){Module.webReplayFrame=(world,dt)=>{protection.frame(world);drop.frame(world);return dt;};return;}
  const playback=boot.mode==='play';
  const ReplayEngine=boot.document?.schema==='starbreak-native-journal-v1'?StarBreakNativeReplay(ClientReplay):ClientReplay;
  const replay=new ReplayEngine({module:Module,mode:playback?'play':'record',document:boot.document,takeoverAllowed:boot.takeover!==false,
    stage:chunk=>StarBreakReplayStorage.stage(boot.id,chunk),
      save:chunk=>StarBreakReplayStorage.deliver(boot.id,chunk),notice:status=>{
      const element=document.getElementById('loading');
      if(status.state==='playing')element.hidden=true;
      else if(status.state==='closed')element.hidden=true;
      else if(status.state==='failed'||status.state==='ended'){element.hidden=status.state==='ended'&&parent!==window;element.textContent=status.state==='failed'?status.message:'Replay completed';}
      parent.postMessage({type:'replay-status',...status},location.origin);
    }});
  Module.clientReplay=replay;
  Module.webReplayFrame=(world,dt)=>{protection.frame(world);drop.frame(world);return replay.frame(world,dt);};
  Module.webReplayEnd=world=>replay.frameEnd(world);
  Module.webReplayInput=(world,pointer)=>replay.input(world,pointer);
  Module.preMainLoop=()=>replay.loop(0);
  Module.postMainLoop=()=>{
    replay.loop(1);
    if(playback&&!keyboardPublished)publishKeyboard();
    if(queuedTakeover!==undefined&&replay.canTakeOver()){
      const tap=queuedTakeover;queuedTakeover=undefined;queueMicrotask(()=>takeOver(tap));
    }
  };
  const physical=new Set();
  addEventListener('blur',()=>physical.clear());
  let takeoverKeys=gameplayKeys(null),keyboardPublished=false;
  function publishKeyboard(){
    if(!Module.replayTranslateKey)return;
    const keys=[];for(let key=1;key<256;key++)if(takeoverKeys.has(Module.replayTranslateKey(key)))keys.push(key);
    parent.postMessage({type:'replay-keyboard',keys},location.origin);keyboardPublished=true;
  }
  if(playback)void fetch('/api/game-preferences',{cache:'no-store'}).then(response=>response.json()).then(value=>{takeoverKeys=gameplayKeys(value.content);keyboardPublished=false;publishKeyboard();}).catch(()=>{});
  let pendingTakeover=false,queuedTakeover;
  let warming=false;
  const warmTimer=playback?setInterval(async()=>{
    if(warming||pendingTakeover||!replay.canTakeOver())return;
    warming=true;
    try{await fetch('/api/replay/warm',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:boot.id,record:boot.record,cursor:replay.cursor,player:replay.playerState(replay.world),...(replay.runnerInfo?{runner:replay.runnerInfo}:{})}),signal:AbortSignal.timeout(60000)});}catch(_){}
    finally{warming=false;}
  },400):null;
  async function takeOver(tap=null){
    if(pendingTakeover)return;
    if(replay.takeoverAllowed)replay.cancelSeek(false);
    if(!replay.canTakeOver()){
      if(replay.seekTarget===null&&replay.takeoverAllowed&&replay.mode==='play'&&!replay.error&&!replay.finished&&replay.world)queuedTakeover=tap;
      return;
    }
    pendingTakeover=true;
    const previousPreferences=Module.gamePreferences;
    const paused=replay.paused;replay.setPaused(true);
    replay.handoffPending=true;
    parent.postMessage({type:'replay-status',state:'taking-over'},location.origin);
    try{
      const branch=await prepareTakeover({id:boot.id,record:boot.record,cursor:replay.cursor,player:replay.playerState(replay.world),...(replay.runnerInfo?{runner:replay.runnerInfo}:{})},
        {notice:progress=>parent.postMessage({type:'replay-status',state:'taking-over',progress},location.origin)});
      const transport=new replay.NativeSocket('ws://'+location.hostname+':'+STARBREAK_WEB_BOOT.wsPort+'/takeover?token='+encodeURIComponent(branch.token),'binary');
      transport.binaryType='arraybuffer';
      const buffered=[];let ready=false;
      const feedback={readyState:1,get bufferedAmount(){return transport.bufferedAmount;},send:value=>transport.send(value),onmessage:null};
      let connectionTimer;
      try{await new Promise((resolve,reject)=>{
        connectionTimer=setTimeout(()=>{reject(Error('Takeover connection timed out'));transport.close();},10000);
        transport.onerror=()=>reject(Error('Takeover connection failed'));
        transport.onmessage=event=>{
          if(typeof event.data!=='string'){
            if(ready)replay.receiveLive(event.data);else buffered.push(event.data);
            return;
          }
          let value;try{value=JSON.parse(event.data);}catch(_){reject(Error('Invalid takeover response'));transport.close();return;}
          if(value.type==='takeover-ready'){
            try{
              replay.becomeLive(transport,branch,[...physical],tap,()=>
                Module.gamePreferences?.becomeLive?.(branch.preferences,content=>replay.preferences(content)));
              clearInterval(warmTimer);queuedTakeover=undefined;boot.id=branch.id;boot.document=null;
              Module.webStartLiveTelemetry(feedback);
              ready=true;
              for(const data of buffered)replay.receiveLive(data);
              transport.send(JSON.stringify({type:'takeover-commit'}));
              parent.postMessage({type:'replay-status',state:'taken-over',record:branch.record},location.origin);
              document.getElementById('loading').hidden=true;
              window.focus();Module.canvas?.focus();
              resolve();
            }catch(error){reject(error);transport.close();}
          }else feedback.onmessage?.({data:event.data});
        };
        transport.onclose=()=>{
          if(!ready){reject(Error('Takeover connection closed'));return;}
          replay.write(['close']);replay.finish();
          replay.liveSocket.readyState=3;replay.liveSocket.onclose?.({type:'close'});
        };
      });}finally{clearTimeout(connectionTimer);}
    }catch(error){
      if(replay.mode==='play')Module.gamePreferences=previousPreferences;
      replay.handoffPending=false;
      replay.setPaused(paused);
      parent.postMessage({type:'replay-status',state:'takeover-failed',message:String(error.message||error)},location.origin);
    }finally{pendingTakeover=false;replay.handoffPending=false;}
  }
  function playbackKeyboard(type,event){
    if(replay.mode!=='play'||!['keydown','keyup'].includes(type))return;
    const key=Module.replayTranslateKey?.(event.keyCode||event.which)||0;
    if(type==='keyup')physical.delete(key);else if(takeoverKeys.has(key))physical.add(key);
    if(type==='keydown'&&takeoverKey(event,key,takeoverKeys))void takeOver(key);
  }
  addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==parent||event.data.type!=='replay-control')return;
    if(event.data.action==='pause')replay.setPaused(event.data.paused);
    if(event.data.action==='seek')replay.seek(event.data.seconds,event.data.paused);
    if(event.data.action==='speed')replay.setSpeed(event.data.speed);
    if(event.data.action==='takeover')void takeOver();
    if(event.data.action==='keyboard'&&Number.isInteger(event.data.keyCode)&&event.data.keyCode>0&&event.data.keyCode<256)playbackKeyboard(event.data.event,event.data);
  });
  if(playback){
    for(const type of ['keydown','keyup','mousedown','mouseup'])addEventListener(type,event=>{
      if(replay.mode!=='play')return;
      event.preventDefault();event.stopImmediatePropagation();
      playbackKeyboard(type,event);
    },true);
    parent.postMessage({type:'replay-status',state:'ready'},location.origin);
  }
  addEventListener('pagehide',()=>{clearInterval(warmTimer);replay.finish();});
})();
