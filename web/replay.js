/* Execution journal for the pinned original client. No heap snapshots or code
 * from a recording are ever loaded. Playback runs the installed game engine. */
(() => {
  'use strict';
  const bytes = value => value instanceof ArrayBuffer ? new Uint8Array(value) : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  const hash = value => {let result=2166136261;for(const byte of bytes(value)){result^=byte;result=Math.imul(result,16777619);}return result>>>0;};
  const base64 = value => {const data=bytes(value);let output='';for(let i=0;i<data.length;i+=8192)output+=String.fromCharCode(...data.subarray(i,i+8192));return btoa(output);};
  const unbase64 = value => Uint8Array.from(atob(value),c=>c.charCodeAt(0)).buffer;

  class ClientReplay {
    constructor({module, mode='record', document=null, socket=globalThis.WebSocket, save=null, stage=null, notice=()=>{}, keepOps=false, takeoverAllowed=true}) {
      this.module=module;this.mode=mode;this.NativeSocket=socket;this.save=save;this.notice=notice;this.keepOps=keepOps;
      this.ops=document?.ops||[];this.cursor=0;this.pending=[];this.pendingSize=0;this.totalSize=0;this.queuedSize=0;this.sequence=0;
      this.chain=Promise.resolve();this.error=null;this.finished=false;this.inLoop=false;this.injecting=false;this.world=0;
      this.liveSocket=null;this.frameCount=0;this.lastSeed=null;this.hadPlayer=false;this.endAfterLoop=false;
      this.sendQueue=[];
      this.assets=new Map();this.blockedAsset=false;this.lastValues={};
      this.paused=false;this.speed=1;this.iterationCredit=0;
      this.elapsed=0;this.totalDuration=this.mode==='play'?this.ops.reduce((sum,row)=>sum+(row[0]==='frame'?row[1]:0),0):0;
      this.heldKeys=new Set();this.liveOffsets={};this.releaseAfterFrame=[];
      this.takeoverAllowed=takeoverAllowed;
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
          if(this.live){
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
      if(stage===0&&this.mode==='play'&&this.paused)return false;
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
      this.write(['control',kind,values.map(value=>value|0)]);
      try{callback();}finally{this.stopIfLeaving();}
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
      this.iterationCredit+=this.speed;
      const count=Math.floor(this.iterationCredit);this.iterationCredit-=count;
      for(let index=0;index<count&&!this.paused&&!this.finished&&!this.error;index++)callback();
    }
    setPaused(value) {
      if(this.mode!=='play'||this.finished||this.error||this.handoffPending)return;
      this.paused=value===true;this.notice({state:this.paused?'paused':'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.canTakeOver()});
    }
    setSpeed(value) {
      if([.5,1,2,4].includes(value))this.speed=value;
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
      if(!this.takeoverAllowed||this.mode!=='play'||this.error||this.finished||this.inLoop||this.sendQueue.length||!this.world)return false;
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
          // A short takeover tap is released at this exact engine boundary.
          // Delaying it until the next clock import changes input timestamps.
          this.external();
          this.notice({state:'playing',frame:this.frameCount,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.takeoverAllowed&&state.length===14&&state[8]>0});
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
    for(let poll=0;poll<600;poll++){
      const status=await read('/api/replay/takeover?job='+encodeURIComponent(job.job));
      if(status.state==='ready')return status.result;
      if(status.state!=='pending')throw Error(status.error||'Takeover verification failed');
      notice(status.total>0?Math.min(99,Math.floor(100*status.current/status.total)):0);
      await sleep(1000);
    }
    throw Error('Takeover verification timed out');
  }

  if(typeof module!=='undefined'&&module.exports){module.exports={ClientReplay,hash,prepareTakeover};return;}
  globalThis.StarBreakClientReplay=ClientReplay;
  const boot=globalThis.STARBREAK_WEB_BOOT?.replay;
  if(!boot)return;
  const playback=boot.mode==='play';
  const replay=new ClientReplay({module:Module,mode:playback?'play':'record',document:boot.document,takeoverAllowed:boot.takeover!==false,
    stage:chunk=>StarBreakReplayStorage.stage(boot.id,chunk),
    save:chunk=>StarBreakReplayStorage.deliver(boot.id,chunk),notice:status=>{
      const element=document.getElementById('loading');
      if(status.state==='playing')element.hidden=true;
      else if(status.state==='closed')element.hidden=true;
      else if(status.state==='failed'||status.state==='ended'){element.hidden=status.state==='ended'&&parent!==window;element.textContent=status.state==='failed'?status.message:'Replay completed';}
      parent.postMessage({type:'replay-status',...status},location.origin);
    }});
  Module.clientReplay=replay;
  Module.webReplayFrame=(world,dt)=>replay.frame(world,dt);
  Module.webReplayEnd=world=>replay.frameEnd(world);
  Module.webReplayInput=(world,pointer)=>replay.input(world,pointer);
  Module.preMainLoop=()=>replay.loop(0);
  Module.postMainLoop=()=>replay.loop(1);
  const physical=new Set();
  let pendingTakeover=false;
  async function takeOver(tap=null){
    if(pendingTakeover||!replay.canTakeOver())return;
    pendingTakeover=true;
    const previousPreferences=Module.gamePreferences;
    const paused=replay.paused;replay.setPaused(true);
    replay.handoffPending=true;
    parent.postMessage({type:'replay-status',state:'taking-over'},location.origin);
    try{
      const branch=await prepareTakeover({id:boot.id,record:boot.record,cursor:replay.cursor,player:replay.playerState(replay.world)},
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
              boot.id=branch.id;boot.document=null;
              Module.webStartLiveTelemetry(feedback);
              ready=true;
              for(const data of buffered)replay.receiveLive(data);
              transport.send(JSON.stringify({type:'takeover-commit'}));
              parent.postMessage({type:'replay-status',state:'taken-over',record:branch.record},location.origin);
              document.getElementById('loading').hidden=true;
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
  addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==parent||event.data.type!=='replay-control')return;
    if(event.data.action==='pause')replay.setPaused(event.data.paused);
    if(event.data.action==='speed')replay.setSpeed(event.data.speed);
    if(event.data.action==='takeover')void takeOver();
  });
  if(playback){
    for(const type of ['keydown','keyup','mousedown','mouseup'])addEventListener(type,event=>{
      if(replay.mode!=='play')return;
      event.preventDefault();event.stopImmediatePropagation();
      if(type==='keydown'||type==='keyup'){
        const key=Module.replayTranslateKey?.(event.keyCode||event.which)||0;
        if(type==='keyup')physical.delete(key);else physical.add(key);
        if(type==='keydown'&&!event.repeat&&!event.ctrlKey&&!event.metaKey&&!event.altKey&&!/^(?:F\d+|Tab|Shift|Control|Alt|Meta|Backquote)/.test(event.code))void takeOver(key);
      }
    },true);
    parent.postMessage({type:'replay-status',state:'ready'},location.origin);
  }
  addEventListener('pagehide',()=>{replay.finish();});
})();
