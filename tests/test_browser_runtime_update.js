const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 for(const warm of [true,false]){
  const reads=[],unpacked=[],removed=[];
  const manifest={version:'r8',build:'new',runtime_hashes:{'runtime.zip':'r7'},runtime_patches:[
   {base:'r7',path:'from-r7.zip',size:10,remove:[]},
   {base:'r6',path:'from-r6.zip',size:20,remove:['tools/obsolete.py']}],runtime_files:{}};
  const cache={open:async()=>({updating:warm}),cached:async(path,hash)=>warm&&hash==='r6'?new Response('cached r6'):undefined,
   read:async path=>{reads.push(path);return new Response(path);},commit:async()=>{}};
  const py={globals:{set(){}},unpackArchive:bytes=>unpacked.push(Buffer.from(bytes).toString()),runPython(){return null;},
   FS:{filesystems:{IDBFS:{}},mkdirTree(){},mount(){},syncfs(_populate,callback){callback(null);},analyzePath:()=>({exists:true}),unlink:name=>removed.push(name)}};
  const context={URL,Response,Uint8Array,JSON,Promise,Error,console,location:{href:'https://test.local/singleweb/browser/worker.js'},
   fetch:async()=>Response.json(manifest),loadPyodide:async()=>py,runtimeCache:()=>cache,postMessage(){},performance:{now:()=>1},setInterval(){}};
  vm.createContext(context);vm.runInContext(fs.readFileSync('web/browser/worker.js','utf8').replace(/^import .*\n/gm,''),context);
  await vm.runInContext('ready',context);
  assert.deepEqual(reads,warm?['from-r6.zip']:['runtime.zip','from-r7.zip']);
  assert.deepEqual(unpacked,warm?['cached r6','from-r6.zip']:['runtime.zip','from-r7.zip']);
  assert.deepEqual(removed,warm?['/app/tools/obsolete.py']:[]);
 }
 console.log('Worker upgrades cached older releases using only the small patch; cold installs and deleted files passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
