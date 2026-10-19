'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const handlers={},files=new Map();let puts=0,network=0,failPut=false;
 const cache={match:async r=>files.get(r.url)?.clone(),put:async(r,v)=>{puts++;if(failPut)throw Error('Quota exceeded');files.set(r.url,v);}};
 const context={URL,Request,Response,Headers,location:{origin:'https://test.local'},self:{location:{href:'https://test.local/singleweb/sw.js'},addEventListener:(name,fn)=>handlers[name]=fn},
  caches:{open:async()=>cache},fetch:async req=>{network++;return new Response('0123456789',{status:req.headers.has('Range')?206:200});}};
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/browser/sw.js','utf8'),context);
 const get=async(headers={})=>{let result;handlers.fetch({request:new Request('https://test.local/singleweb/client/data/music/test.ogg',{headers}),respondWith:p=>result=p});return result;};
 let response=await get({Range:'bytes=0-3'});assert.equal(response.status,206);assert.equal(puts,0,'Partial responses are never cached');
 failPut=true;response=await get();assert.equal(await response.text(),'0123456789','Cache quota failure must not break successful audio downloads');
 failPut=false;await get();const before=network;
 response=await get({Range:'bytes=2-5'});assert.equal(response.status,206);assert.equal(await response.text(),'2345');assert.equal(response.headers.get('Content-Range'),'bytes 2-5/10');
 response=await get({Range:'bytes=-3'});assert.equal(await response.text(),'789');
 assert.equal((await get({Range:'bytes=10-'})).status,416);assert.equal(network,before,'Cached complete audio supports range requests without redownloading');
 context.caches.open=async()=>{throw Error('Storage blocked');};response=await get();assert.equal(response.status,200);
 console.log('Audio range requests, offline ranges and nonfatal cache failures passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
