const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto}=require('node:crypto');
(async()=>{
 const stores=new Map(),calls=[],steps=[];let failPut=false;
 const open=async name=>{if(!stores.has(name))stores.set(name,new Map());const files=stores.get(name);return {match:async key=>files.get(typeof key==='string'?key:key.url)?.clone(),put:async(key,value)=>{if(failPut)throw Error('quota');files.set(typeof key==='string'?key:key.url,value);}};};
 const storage={open,match:async key=>{for(const store of stores.values()){const response=store.get(key);if(response)return response.clone();}}};
 const bytes=new Map([['pyodide/pyodide.asm.wasm','old engine'],['runtime.zip','old code']]);
 const hash=async text=>Buffer.from(await webcrypto.subtle.digest('SHA-256',Buffer.from(text))).toString('hex');
 const manifest=async build=>({build,version:build,runtime_hashes:Object.fromEntries(await Promise.all([...bytes].map(async([name,body])=>[name,await hash(body)])))});
 const context={URL,Response,Uint8Array,crypto:webcrypto,caches:storage,fetch:async url=>{calls.push(url);return new Response(bytes.get(new URL(url).pathname.replace('/singleweb/','')));},console};
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/browser/runtime-cache.js','utf8').replace('export function','function'),context);
 const create=()=>context.runtimeCache({base:'https://example.test/singleweb/',report:step=>steps.push(step)});
 // Migrate an existing unversioned wasm cache without downloading its bytes.
 await(await open('starbreak-static-/singleweb/assets-v2')).put('https://example.test/singleweb/pyodide/pyodide.asm.wasm',new Response('old engine'));
 let cache=create();await cache.open(await manifest('first'));
 await cache.read('pyodide/pyodide.asm.wasm');await cache.read('runtime.zip');await cache.commit();
 assert.equal(calls.length,1);assert.ok(calls[0].includes('runtime.zip'));assert.deepEqual(steps,['cached','update']);
 calls.length=0;steps.length=0;cache=create();assert.equal((await cache.open(await manifest('first'))).updating,false);
 await cache.read('pyodide/pyodide.asm.wasm');await cache.read('runtime.zip');assert.equal(calls.length,0);assert.deepEqual(steps,['cached','cached']);
  const oldHash=await hash('old code');
  assert.equal(await(await cache.cached('runtime.zip',oldHash)).text(),'old code');
  assert.equal(await cache.cached('runtime.zip','not-on-this-device'),undefined);
  assert.equal(calls.length,0,'Selecting a cached runtime for a small overlay must not download anything');
 // A frontend-only update reuses both files. A runtime update only gets new code.
 cache=create();assert.equal((await cache.open(await manifest('frontend update'))).updating,true);
 await cache.read('runtime.zip');await cache.read('pyodide/pyodide.asm.wasm');assert.equal(calls.length,0);
 bytes.set('runtime.zip','new code');cache=create();await cache.open(await manifest('code update'));steps.length=0;
 await cache.read('pyodide/pyodide.asm.wasm');await cache.read('runtime.zip');assert.equal(calls.length,1);assert.deepEqual(steps,['cached','update']);
 // A failed cache write still permits launching; corrupt network bytes never commit.
 bytes.set('runtime.zip','yet another');cache=create();await cache.open(await manifest('quota'));failPut=true;await cache.read('runtime.zip');failPut=false;
 const wrong=await manifest('bad');bytes.set('runtime.zip','corrupt');cache=create();await cache.open(wrong);await assert.rejects(()=>cache.read('runtime.zip'),/checksum mismatch/);
 console.log('Runtime cache: legacy migration, zero-network reopen, content-only updates, quota and integrity passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
