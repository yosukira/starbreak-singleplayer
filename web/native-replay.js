/* Execute typed native journals in the same 1.14.6 game engine in the browser. */
(() => {
  'use strict';
  const decode=value=>Uint8Array.from(atob(value),c=>c.charCodeAt(0));
  const encode=value=>{let s='';for(let i=0;i<value.length;i+=8192)s+=String.fromCharCode(...value.subarray(i,i+8192));return btoa(s);};
  class Cipher{
    constructor(key){this.s=Uint8Array.from({length:256},(_,i)=>i);let j=0;for(let i=0;i<256;i++){j=(j+this.s[i]+key[i%key.length])&255;[this.s[i],this.s[j]]=[this.s[j],this.s[i]];}this.i=this.j=0;}
    crypt(bytes){return Uint8Array.from(bytes,b=>{this.i=(this.i+1)&255;this.j=(this.j+this.s[this.i])&255;[this.s[this.i],this.s[this.j]]=[this.s[this.j],this.s[this.i]];return b^this.s[(this.s[this.i]+this.s[this.j])&255];});}
  }
  const factory=Base=>class NativeClientReplay extends Base{
    constructor(options){
      super({...options,document:{ops:[]}});
      this.ops=options.document.ops;this.document=options.document;this.cursor=0;this.stack=[];this.driving=false;
      this.nativeClient=0;this.nativeServer=0;this.world=0;this.joined=false;this.pendingAssets=0;this.engineElapsed=0;
      this.elapsed=0;this.totalDuration=this.ops.reduce((n,r)=>n+(r[0]==='step'?r[1]:0),0);
      this.ends=[];let elapsed=0;for(const row of this.ops){if(row[0]==='step')elapsed+=row[1];if(row[0]==='loop'&&row[1]===1)this.ends.push(elapsed);}
      this.completed=0;this.hasPlayerFrames=this.ops.some(r=>r[0]==='state'&&r[1].length===14);
      this.runner=this.execute();this.playTime=0;this.wall=null;this.bootstrap=options.document.bootstrap;
      this.runnerInfo=options.document.runner;this.clockOffset=0;this.lastNativeClock=null;this.liveControls=[];
      this.save=chunk=>options.save?.({...chunk,native:true});this.stage=chunk=>options.stage?.({...chunk,native:true});
      if(!this.bootstrap)throw Error('Portable replay bootstrap is missing');
      this.module.nativeJournal=this;
    }
    write(row){if(this.mode==='record'&&row[0]!=='close')super.write(row);}
    flush(end=false){return this.mode==='record'?super.flush(end):Promise.resolve();}
    finish(){return this.mode==='record'?super.finish():Promise.resolve();}
    value(_kind,actual){return actual;}
    loop(stage){
      if(this.mode!=='record'||this.error||this.finished)return true;
      if(stage===0){
        this.inLoop=true;this.write(['loop',0]);
        this.initialInput?.();this.initialInput=null;
        for(const callback of this.liveControls.splice(0))callback();
      }else{
        for(const key of this.releaseAfterFrame.splice(0))this.module.replayControl('key',[0,key]);
        this.write(['state',this.world?this.playerState(this.world):[0,0]]);this.write(['loop',1]);this.inLoop=false;
        if(this.frameCount%30===0)this.flush();
        this.stopIfLeaving();
      }
      return true;
    }
    control(_kind,_values,callback){if(this.mode==='record'){if(this.liveControls.length>=256){this.fail('Portable input queue is full');return;}this.liveControls.push(callback);}}
    timer(callback){if(!this.driving)callback();}
    preferences(content){this.module.replayPreferences(content);}
    requestQuit(){this.leaving=true;}
    quit(){this.leaving=true;this.stopIfLeaving();}
    asset(_kind,_path,callback){callback();}
    viewport(){return this.document.viewport;}
    input(){return 1;}
    frame(_world,dt){return dt;}
    frameEnd(){}
    canTakeOver(){return this.takeoverAllowed&&this.mode==='play'&&!this.error&&!this.finished&&!this.inLoop&&!this.stack.length&&this.worldReady&&this.seekTarget===null&&this.pendingAssets===0&&this.playerState(this.world)[8]>0;}
    transparent(){
      while(this.ops[this.cursor]?.[0]==='delta'||this.ops[this.cursor]?.[0]==='return'&&this.ops[this.cursor][1]==='delta')this.cursor++;
    }
    take(kind){this.transparent();const row=this.ops[this.cursor++];if(row?.[0]!==kind)throw Error(`Portable replay diverged at ${this.cursor-1} (${kind})`);return row;}
    expect(row){const actual=this.take(row[0]);if(JSON.stringify(actual)!==JSON.stringify(row))throw Error(`Portable replay arguments changed at ${this.cursor-1}`);}
    clock(actual){
      if(this.mode==='record'){
        const value=(actual+this.clockOffset)|0;if(this.inLoop&&this.stack.length)this.write(['clock',value]);return value;
      }
      if(!this.driving||!this.stack.length)return actual;
      this.lastNativeClock=this.take('clock')[1]|0;return this.lastNativeClock;
    }
    seed(actual){
      if(this.mode==='record'){if(this.inLoop&&this.stack.length)this.write(['random32',actual>>>0]);return actual;}
      if(!this.driving||!this.stack.length)return actual;
      this.transparent();const kind=this.ops[this.cursor]?.[0];
      if(kind!=='random32'&&kind!=='random64')throw Error('Portable replay random boundary changed');
      const row=this.take(kind);if(kind==='random64'&&row[2]!==((row[1]|0)<0?0xffffffff:0))throw Error('Unsupported portable 64-bit seed');
      return row[1]|0;
    }
    enter(kind,object,a,b,c){
      if(kind===1||kind===2)this.nativeClient=object;
      if(kind===3||kind===4)this.world=object;
      if(kind===6)this.nativeServer=object;
      if(kind===5&&a===8&&!this.driving)this.joined=true;
      if(this.mode!=='record'&&!this.driving)return !this.joined||kind===5&&[1,8].includes(a)||kind===6?1:0;
      if(this.mode==='record'&&(!this.inLoop||this.error||this.finished))return 1;
      let row;
      if(kind===1)row=['input',a,b];
      if(kind===2)row=['frame'];
      if(kind===3)row=['engine-frame',a];
      if(kind===4){const h=this.module.HEAP32;row=['player-input',h[(a+4)>>2],h[(a+8)>>2],h[(a+12)>>2],h[(a+20)>>2]];}
      if(kind===5){row=['message',a,encode(this.module.HEAPU8.subarray(b,b+c))];if(a===21)this.pendingAssets++;}
      if(kind===6)row=['read'];
      const entry={kind:row[0],before:this.engineElapsed,object,dt:kind===3?a:0};
      if(this.mode==='record'){
        this.write(row);
        if(kind===5&&[12,37,38].includes(a)){this.write(['delta',a,row[2]]);entry.delta=true;}
      }else this.expect(row);
      this.stack.push(entry);return 1;
    }
    leave(kind,object){
      if(this.mode==='record'){
        if(!this.inLoop||this.error||this.finished)return;
        const entry=this.stack.pop();if(!entry){this.fail('Portable call stack changed');return;}
        if(kind===2){const step=this.engineElapsed-entry.before;this.write(['step',step]);this.elapsed+=step;}
        if(entry.delta)this.write(['return','delta']);
        this.write(['return',entry.kind]);
        if(kind===3){this.engineElapsed+=entry.dt;this.frameCount++;this.write(['engine-state',this.playerState(object)]);}
        return;
      }
      if(!this.driving)return;
      const entry=this.stack.pop();if(!entry)throw Error('Portable replay call stack changed');
      if(kind===2){const step=this.take('step')[1];if(step!==this.engineElapsed-entry.before)throw Error('Portable replay clock step changed');this.elapsed+=step;}
      this.expect(['return',entry.kind]);
      if(kind===3){
        this.engineElapsed+=entry.dt;
        const check=this.take('engine-state')[1],state=this.playerState(object);
        // Count the source dt, independent of how the display is scheduled.
        if(JSON.stringify(check)!==JSON.stringify(state))throw Error(`Portable physics checkpoint changed at ${this.cursor-1}`);
        this.frameCount++;
      }
    }
    invoke(row){
      const asm=this.module.asm;
      if(row[0]==='message'){
        if(!this.nativeServer)throw Error('Portable replay transport is not initialized');
        const bytes=decode(row[2]),pointer=this.module._malloc(bytes.length);
        try{this.module.HEAPU8.set(bytes,pointer);asm.nativeReplayMessage(this.nativeServer+72,row[1],pointer,bytes.length);}
        finally{this.module._free(pointer);}
      }else if(row[0]==='input')asm.nativeReplayInput(this.nativeClient,row[1],row[2]);
      else if(row[0]==='frame')asm.nativeReplayFrame(this.nativeClient);
      else if(row[0]==='engine-frame')asm.nativeReplayEngine(this.world,row[1]);
      else if(row[0]==='player-input'){
        const p=this.module._malloc(24),h=this.module.HEAP32;h.fill(0,p>>2,(p+24)>>2);
        try{for(const [index,offset]of [[1,4],[2,8],[3,12],[4,20]])h[(p+offset)>>2]=row[index];asm.nativeReplayPlayerInput(this.world,p);}
        finally{this.module._free(p);}
      }else throw Error('Unsupported portable driver operation: '+row[0]);
    }
    *execute(){
      while(this.cursor<this.ops.length){
        this.expect(['loop',0]);this.inLoop=true;
        while(this.ops[this.cursor]?.[0]!=='state'){
          const row=this.ops[this.cursor];
          if(['frame','input'].includes(row[0])&&!this.nativeClient){yield 'context';continue;}
          if(row[0]==='read'){
            this.expect(['read']);this.stack.push({kind:'read',before:this.engineElapsed});
            while(JSON.stringify(this.ops[this.cursor])!==JSON.stringify(['return','read'])){
              const next=this.ops[this.cursor];
              if(['clock','random32','random64'].includes(next[0]))this.cursor++;
              else this.invoke(next);
              if(this.pendingAssets>0)yield 'assets';
            }
            this.stack.pop();this.expect(['return','read']);
          }else this.invoke(row);
        }
        const expected=this.take('state')[1],state=this.world?this.playerState(this.world):[0,0];
        if(JSON.stringify(expected)!==JSON.stringify(state))throw Error(`Portable player checkpoint changed at ${this.cursor-1}`);
        this.expect(['loop',1]);this.inLoop=false;this.completed++;
        if(state.length===14)this.worldReady=true;
        if(this.pauseWhenReady&&this.worldReady){this.pauseWhenReady=false;this.paused=true;this.wall=null;}
        this.notice({state:this.paused?'paused':'playing',frame:this.worldReady?this.frameCount:0,seconds:this.elapsed/1000,duration:this.totalDuration/1000,takeover:this.canTakeOver()});
        yield 'frame';
      }
      this.finished=true;this.notice({state:'ended',seconds:this.elapsed/1000,duration:this.totalDuration/1000});
    }
    iterate(callback){
      if(this.mode==='record'){callback();return;}
      if(this.error||this.finished)return;
      try{
        callback();
        if(!this.joined||!this.nativeServer||this.pendingAssets>0){this.wall=null;return;}
        if(this.paused&&this.seekTarget===null)return;
        const started=this.now();if(this.wall!==null)this.playTime+=Math.min(250,Math.max(0,started-this.wall))*this.speed;this.wall=started;
        this.driving=true;
        for(let count=0;count<240&&!this.finished&&!this.paused;count++){
          if(this.seekTarget===null&&this.ends[this.completed]>this.playTime)break;
          const value=this.runner.next();
          if(value.done||value.value==='assets'||value.value==='context'||this.now()-started>12)break;
          if(this.seekTarget!==null&&this.elapsed>=this.seekTarget&&(!this.seekPaused||this.worldReady)){
            this.seekTarget=null;this.paused=this.seekPaused;this.wall=null;this.playTime=this.elapsed;break;
          }
        }
      }catch(error){this.fail(error);}finally{this.driving=false;}
    }
    becomeLive(transport,branch,physical=[],tap=null,beforeInput=null){
      if(!this.canTakeOver())throw Error('No verified portable player to take over');
      const total=this.playerState(this.world)[1];
      const real=this.module.asm.nativeReplayClock();this.clockOffset=this.lastNativeClock===null?0:(this.lastNativeClock-real)|0;
      this.mode='record';this.live=true;this.nativeTransport=transport;this.paused=false;this.speed=1;this.sequence=branch.sequence;
      this.pending=[];this.pendingSize=this.queuedSize=this.totalSize=0;this.stack=[];
      this.initialInput=()=>{
        for(let bit=0;bit<14;bit++)if(total&(1<<bit))this.module.asm.nativeReplayInput(this.nativeClient,1<<bit,0);
        this.module.replayControl('reset',[]);beforeInput?.();
        for(const key of physical)this.module.replayControl('key',[1,key]);
        if(tap!==null&&!physical.includes(tap)){this.module.replayControl('key',[1,tap]);this.releaseAfterFrame.push(tap);}
      };
      this.ops=[];
    }
    receiveLive(data){const bytes=new Uint8Array(data);this.deliver(this.tx.crypt(bytes));}
    createSocket(){
      const key=decode(this.bootstrap.key);this.rx=new Cipher(key);this.tx=new Cipher(key);this.outgoing=new Uint8Array();let connected=false;
      const deliver=this.deliver=data=>peer.onmessage?.({data:data.buffer.slice(data.byteOffset,data.byteOffset+data.byteLength)});
      const peer={readyState:0,CONNECTING:0,OPEN:1,CLOSING:2,CLOSED:3,bufferedAmount:0,binaryType:'arraybuffer',close:()=>{peer.readyState=3;this.nativeTransport?.close();},send:value=>{
        const bytes=value instanceof ArrayBuffer?new Uint8Array(value):new Uint8Array(value.buffer,value.byteOffset,value.byteLength);
        if(!connected){connected=true;queueMicrotask(()=>deliver(this.tx.crypt(decode(this.bootstrap.joined))));return;}
        const plain=this.rx.crypt(bytes);
        if(this.nativeTransport){this.nativeTransport.send(plain);return;}
        const old=this.outgoing;this.outgoing=new Uint8Array(old.length+plain.length);this.outgoing.set(old);this.outgoing.set(plain,old.length);
        while(this.outgoing.length>=5){const size=new DataView(this.outgoing.buffer).getInt32(1);if(size<0||size>262144)throw Error('Invalid portable acknowledgement');if(this.outgoing.length<size+5)break;if(this.outgoing[0]===22)this.pendingAssets=Math.max(0,this.pendingAssets-1);this.outgoing=this.outgoing.slice(size+5);}
      }};
      this.sendHello=()=>{if(peer.readyState===1&&!this.helloSent){this.helloSent=true;queueMicrotask(()=>deliver(decode(this.bootstrap.hello)));}};
      this.liveSocket=peer;queueMicrotask(()=>{peer.readyState=1;peer.onopen?.({type:'open'});this.sendHello();});return peer;
    }
  };
  if(typeof module!=='undefined'&&module.exports)module.exports={factory,Cipher};else globalThis.StarBreakNativeReplay=factory;
})();
