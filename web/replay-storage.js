/* A synchronous local outbox survives iframe removal and tab closure. */
(() => {
  'use strict';
  const PREFIX='starbreak-replay-chunk-v1:';
  const UUID='[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
  const pattern=new RegExp('^'+PREFIX+'('+UUID+'):(0|[1-9][0-9]{0,7})$');
  class ReplayStorage {
    constructor({storage,request}){this.storage=storage;this.request=request;this.busy=null;}
    key(id,sequence){const key=PREFIX+id+':'+sequence;if(!pattern.test(key))throw Error('Invalid replay outbox identity');return key;}
    stage(id,chunk){
      const key=this.key(id,chunk.sequence),value=JSON.stringify(chunk);
      if(!Array.isArray(chunk.ops)||chunk.ops.length>4096||typeof chunk.end!=='boolean'||value.length>512*1024)throw Error('Invalid replay outbox chunk');
      const previous=this.storage.getItem(key);
      if(previous!==null&&previous!==value)throw Error('Conflicting replay outbox chunk');
      // One key per immutable chunk avoids read/modify/write races between
      // the parent page, its iframe and another tab recovering an old upload.
      this.storage.setItem(key,value);
    }
    async send(id,chunk){
      await this.request({id,sequence:chunk.sequence,ops:chunk.ops,end:chunk.end,...(chunk.native===true?{native:true}:{})});
      const key=this.key(id,chunk.sequence);
      if(this.storage.getItem(key)===JSON.stringify(chunk))this.storage.removeItem(key);
    }
    async deliver(id,chunk){
      // The immutable chunk was staged synchronously. A temporary transport
      // failure must not stop recording the rest of this battle.
      try{await this.send(id,chunk);return true;}catch(_){return false;}
    }
    recover(){
      if(this.busy)return this.busy;
      this.busy=(async()=>{
        const rows=[];
        for(let i=0;i<this.storage.length;i++){
          const key=this.storage.key(i),match=pattern.exec(key||'');
          if(match)rows.push({key,id:match[1],sequence:Number(match[2])});
        }
        rows.sort((a,b)=>a.id.localeCompare(b.id)||a.sequence-b.sequence);
        const failed=new Set();let saved=0;
        for(const row of rows){
          if(failed.has(row.id))continue;
          try{
            const raw=this.storage.getItem(row.key);if(raw===null)continue;
            if(raw.length>512*1024)throw Error('Replay outbox chunk too large');
            const chunk=JSON.parse(raw);
            if(chunk.sequence!==row.sequence)throw Error('Replay outbox sequence changed');
            this.stage(row.id,chunk);
            await this.send(row.id,chunk);saved++;
          }catch(_){failed.add(row.id);}
        }
        return {saved,pending:failed.size};
      })().finally(()=>{this.busy=null;});
      return this.busy;
    }
  }
  if(typeof module!=='undefined')module.exports={ReplayStorage,PREFIX};
  if(typeof window==='undefined')return;
  let storage;
  try{storage=localStorage;}catch(_){storage={getItem:()=>null,setItem:()=>{throw Error('Local replay storage unavailable');},removeItem:()=>{},length:0};}
  const outbox=new ReplayStorage({storage,request:async document=>{
    const body=JSON.stringify(document);
    const response=await fetch('/api/replay/client',{method:'POST',signal:AbortSignal.timeout(10000),
      headers:{'Content-Type':'application/json'},body});
    if(!response.ok)throw Error('Could not save client replay');
    await response.json();
  }});
  window.StarBreakReplayStorage=outbox;
  setInterval(()=>void outbox.recover(),5000);
  // A new top-level page retries leftovers from an unloaded game. Storage
  // events also cover iframe removal without navigating the parent page.
  if(window===parent){
    let timer;
    const schedule=()=>{clearTimeout(timer);timer=setTimeout(()=>void outbox.recover(),1200);};
    addEventListener('storage',event=>{if(event.key?.startsWith(PREFIX))schedule();});
    void outbox.recover();
  }
})();
