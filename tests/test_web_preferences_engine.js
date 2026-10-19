/* Runs the pinned original engine without starting a world or opening a UI. */
'use strict';
const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
const varint=n=>{const out=[];do{out.push((n&127)|(n>=128?128:0));n=Math.floor(n/128);}while(n);return Buffer.from(out);};
const field=(id,data)=>Buffer.concat([varint(id*8+2),varint(data.length),Buffer.from(data)]);
const options=rows=>Buffer.concat(rows.map(([key,value])=>field(1,Buffer.concat([field(1,Buffer.from(key)),field(2,Buffer.from(value))])))).toString('base64');
const initial=options([['private-test-field','retained'],['musicVol','9']]),saved=[],cleared=[];
let exitStatus=null;
const sandbox={Storage:function(){},document:{createElement(){return {getContext(){return {};}};}},
  Module:{noInitialRun:true,noExitRuntime:true,
    gamePreferences:{load(){return initial;},save(key,value){assert.equal(key,'LocalStorage.pb');saved.push(value);}},print(){},printErr(){}},
  require,process:new Proxy(process,{get(target,key){if(key==='exit')return status=>{exitStatus=status;throw Error('Expected runtime exit');};const value=Reflect.get(target,key);return typeof value==='function'?value.bind(target):value;}}),
  __dirname:require('node:path').dirname(process.argv[2]),__filename:process.argv[2],
  console,Buffer,atob,btoa,setTimeout,clearTimeout,setInterval,clearInterval:id=>{cleared.push(id);clearInterval(id);}};
try{
  vm.createContext(sandbox);
  vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),sandbox,{timeout:10000,displayErrors:false});
  const binding=field(1,Buffer.concat([Buffer.from([8,0,16]),varint(122)]));
  const personal=options([['impbstr',binding],['musicVol','17'],['sfxVol','33']]);
  sandbox.Module.replayPreferences(btoa(personal));
  // The pinned qL() returns the original InputMapping singleton at 161136.
  assert.equal(sandbox.Module.HEAP32[161136>>2],122);
  assert.equal(sandbox.Module.HEAP32[(161136+12)>>2],1073741903,'Unspecified actions retain original defaults');
  assert.equal(sandbox.XDL.musicVolume,17/128);
  assert.equal(sandbox.XDL.soundVolume,33/128);
  assert.ok(Buffer.from(saved.at(-1),'base64').includes(Buffer.from('private-test-field')),'Other local-client state survives');
  // Repeated switches exercise original map/string cleanup, not a replacement
  // input implementation. Missing personal preferences use original defaults.
  for(let i=0;i<100;i++){
    sandbox.Module.replayPreferences('');
    assert.equal(sandbox.Module.HEAP32[161136>>2],1073741904);
    assert.equal(sandbox.XDL.musicVolume,64/128);
    assert.equal(sandbox.XDL.soundVolume,1);
    sandbox.Module.replayPreferences(btoa(personal));
    assert.equal(sandbox.Module.HEAP32[161136>>2],122);
  }
  let sealed=false;sandbox.Module.clientReplay={quit:status=>{assert.equal(status,0);sealed=true;}};
  sandbox.Module.replayTimerId=98765;
  assert.throws(()=>sandbox.__exit(0),/Expected runtime exit/);
  assert.equal(sealed,true);assert.equal(exitStatus,0);assert.ok(cleared.includes(98765));
  assert.equal(sandbox.Module.noExitRuntime,true,'Keep original runtime teardown policy');
  console.log('Original engine: personal options, defaults, repeated reload, local state and explicit runtime exit passed.');
}catch(error){
  // A one-line engine source must never be dumped by a VM error report.
  console.error(error.name,error.message);
  console.error(error.stack.split('\n').filter(line=>/^\s+at /.test(line)).slice(0,4).join('\n'));
  process.exitCode=1;
}
