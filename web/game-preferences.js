/* The original game owns its options format. Persist only its one settings key. */
(() => {
  'use strict';
  const KEY='LocalStorage.pb',CACHE='starbreak-game-options-v1',LIMIT=65536;
  const OPTIONS=new Set(['impbstr','gq','windowWidth','windowHeight','fullScreen','musicVol','defMusicVol','sfxVol','defSFXVol','tipIndex']);
  const encode=value=>btoa(Array.from(value,c=>String.fromCharCode(c.charCodeAt(0)&255)).join(''));
  function optionsOnly(content){
    // Cache entries may precede the server's account-field filtering. Reuse
    // only original protobuf rows whose names are explicit game options.
    const raw=atob(content),data=Uint8Array.from(atob(raw),c=>c.charCodeAt(0));
    if(raw.length>LIMIT||data.length>LIMIT)throw Error('Game options exceed limit');
    function rows(bytes){
      let cursor=0;
      const integer=()=>{let result=0,scale=1;for(let i=0;i<5;i++){if(cursor>=bytes.length)throw Error('Truncated game options');const byte=bytes[cursor++];result+=(byte&127)*scale;if(!(byte&128))return result;scale*=128;}throw Error('Invalid game options integer');};
      const result=[];
      while(cursor<bytes.length){const start=cursor,tag=integer();if(tag%8!==2)throw Error('Invalid game options field');const length=integer(),end=cursor+length;if(end>bytes.length)throw Error('Truncated game options');result.push([Math.floor(tag/8),bytes.subarray(cursor,end),bytes.subarray(start,end)]);cursor=end;}
      return result;
    }
    const kept=[];
    for(const [tag,entry,wire] of rows(data)){
      const pair=rows(entry);
      if(tag!==1||pair.length!==2||new Set(pair.map(row=>row[0])).size!==2||!pair.some(row=>row[0]===1)||!pair.some(row=>row[0]===2))throw Error('Invalid game options pair');
      const name=String.fromCharCode(...pair.find(row=>row[0]===1)[1]);
      if(OPTIONS.has(name))kept.push(wire);
    }
    let binary='';for(const wire of kept)for(let i=0;i<wire.length;i+=8192)binary+=String.fromCharCode(...wire.subarray(i,i+8192));
    return btoa(btoa(binary));
  }
  function create(boot,environment){
    const {storage,request,beacon,status=()=>{}}=environment;
    let revision=boot.revision||0,stored=boot.content??null,content=stored,busy=null,inflight=null;
    let queue=[],takeover=false,gameBaseline=null,editVersion=0;
    const valid=value=>typeof value==='string'&&value.length<=Math.ceil(LIMIT/3)*4&&(()=>{try{return atob(value).length<=LIMIT;}catch(_){return false;}})();
    const writeCache=()=>{try{storage.setItem(CACHE,JSON.stringify({content,baseRevision:revision,previous:stored,inflight,pending:queue.length>0,queue}));}catch(_){}};
    const validEntry=row=>row&&valid(row.content)&&(!('baseline' in row)||row.baseline===null||valid(row.baseline));
    try{
      const cache=JSON.parse(storage.getItem(CACHE)||'null');
      if(cache?.pending&&(cache.baseRevision===revision||cache.previous===stored||cache.inflight===stored)){
        if(Array.isArray(cache.queue)&&cache.queue.length<=32&&cache.queue.every(validEntry))queue=cache.queue;
        else if(valid(cache.content))queue=[{content:cache.content}];
        // A pending takeover edit is a delta against somebody else's options.
        // Keep this player's own settings visible until the merge completes.
        if(queue.length&&!('baseline' in queue.at(-1)))content=queue.at(-1).content;
      }
      if(content===null&&!queue.length){const legacy=storage.getItem(KEY);if(typeof legacy==='string'&&legacy.length<=LIMIT){content=encode(legacy);queue=[{content}];}}
    }catch(_){}
    async function flush(){
      if(busy)return busy;
      if(!queue.length)return true;
      busy=(async()=>{
        try{
          while(queue.length){
            const entry=queue[0],sent=entry.content,version=editVersion;
            const document={key:KEY,revision,content:sent};
            if('baseline' in entry)document.baseline=entry.baseline;
            inflight=sent;writeCache();
            const response=await request(document);
            if(!response||!valid(response.content)||!Number.isSafeInteger(response.revision))throw Error('Invalid save response');
            if('baseline' in entry){
              // If a new edit arrived while saving, compare it with the engine
              // snapshot just acknowledged, rather than the imported defaults.
              entry.baseline=sent;
              if(takeover)gameBaseline=sent;
            }
            if(entry.content===sent)queue.shift();
            if(!takeover&&version===editVersion&&!queue.length)content=response.content;
            stored=response.content;revision=response.revision;inflight=null;writeCache();
          }
          status(true);return true;
        }catch(_){writeCache();status(false);return false;}
        finally{busy=null;}
      })();
      return busy;
    }
    const api={
      load(key){return key===KEY&&content!==null?atob(content):null;},
      beginTakeover(recorded){
        if(recorded!==null&&!valid(recorded))throw Error('Invalid recorded game options');
        takeover=true;gameBaseline=recorded;content=recorded;
      },
      save(key,value){
        if(key!==KEY)return;
        if(typeof value!=='string'||value.length>LIMIT){status(false);return;}
        const next=encode(value);if(next===content)return;
        content=next;editVersion++;
        const last=queue.at(-1),same=last&&('baseline' in last)===takeover;
        if(same)last.content=next;
        else queue.push(takeover?{content:next,baseline:gameBaseline}:{content:next});
        writeCache();void flush();
      },
      flush,
      leave(){
        writeCache();
        if(queue.length&&beacon){const entry=queue[0];beacon({key:KEY,revision,...entry});}
      }
    };
    if(queue.length)queueMicrotask(flush);
    return api;
  }
  if(typeof module!=='undefined')module.exports={create,KEY,CACHE,optionsOnly};
  if(typeof window==='undefined')return;
  function liveEnvironment(){
    let storage;try{storage=localStorage;}catch(_){storage={getItem:()=>null,setItem:()=>{}};}
    return {storage,
      async request(document){const response=await fetch('/api/game-preferences',{method:'POST',signal:AbortSignal.timeout(10000),headers:{'Content-Type':'application/json'},body:JSON.stringify(document),keepalive:JSON.stringify(document).length<60000});if(!response.ok)throw Error('Game settings save failed');return response.json();},
      beacon:document=>navigator.sendBeacon('/api/game-preferences',new Blob([JSON.stringify(document)],{type:'application/json'})),
      status:saved=>parent.postMessage({type:'game-preferences-status',saved},location.origin)
    };
  }
  if(STARBREAK_WEB_BOOT.replay?.mode==='play'){
    // Playback remains read-only. The handoff can reload personal options via
    // the original engine; only later explicit edits enter persistent storage.
    const value=STARBREAK_WEB_BOOT.gamePreferences?.content??null;
    let baseline=value;
    Module.gamePreferences={load:key=>key===KEY&&value!==null?atob(value):null,
      save(key,raw){if(key===KEY&&typeof raw==='string'&&raw.length<=LIMIT)baseline=encode(raw);},flush:async()=>true,leave(){},
      becomeLive(preferences,apply){
        if(!preferences||preferences.key!==KEY)throw Error('Live game options unavailable');
        const api=create(preferences,liveEnvironment());
        if(apply)apply(optionsOnly(encode(api.load(KEY)??'')));
        api.beginTakeover(baseline);Module.gamePreferences=api;
      }
    };
  }else Module.gamePreferences=create(STARBREAK_WEB_BOOT.gamePreferences||{},liveEnvironment());
  addEventListener('pagehide',()=>Module.gamePreferences.leave());
})();
