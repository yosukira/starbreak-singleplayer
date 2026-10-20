'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
  const callbacks=[],messages=[];
  const py={globals:{set:()=>{}},FS:{syncfs:(populate,callback)=>{assert.equal(populate,false);callbacks.push(callback);}},runPython:source=>source.includes('runtime.drain')?'[]':'null'};
  const context={fakePy:py,fetch:async()=>{},postMessage:value=>messages.push(value),JSON,Promise,Error,console};
  vm.createContext(context);
  const source=fs.readFileSync('web/browser/worker.js','utf8')
    .replace(/^import .*\n/m,'')
    .replace('let queue=Promise.resolve(),ready=initialize();','let queue=Promise.resolve(),ready=Promise.resolve({version:"test"});py=globalThis.fakePy;profileReady=true;');
  vm.runInContext(source,context);
  const drain=()=>new Promise(resolve=>setImmediate(resolve));
  const send=(id,kind,extra={})=>context.onmessage({data:{id,kind,...extra}});
  send(1,'api',{method:'POST',url:'/api/replay/client'});await drain();
  assert.equal(callbacks.length,1);assert.ok(!messages.some(row=>row.id===1),'Do not ACK before persistence');
  send(2,'send',{socket:1,url:'/game',data:'input'});await drain();
  assert.ok(messages.some(row=>row.id===2),'Game input must proceed during an IndexedDB save');
  send(3,'api',{method:'POST',url:'/api/preferences'});
  send(4,'api',{method:'POST',url:'/api/draft'});await drain();
  assert.equal(callbacks.length,1,'Never overlap syncfs calls');
  callbacks.shift()(null);await drain();
  assert.ok(messages.some(row=>row.id===1));
  assert.ok(!messages.some(row=>row.id===3),'Later mutations need a later snapshot');
  assert.equal(callbacks.length,1,'Coalesce later mutations into one snapshot');
  callbacks.shift()(null);await drain();
  assert.ok(messages.some(row=>row.id===3));assert.ok(messages.some(row=>row.id===4));
  send(5,'flush');await drain();
  assert.equal(callbacks.length,0,'A clean profile needs no new write');
  send(6,'api',{method:'POST',url:'/api/replay/client'});await drain();
  callbacks.shift()(new Error('disk full'));await drain();
  assert.ok(messages.some(row=>row.id===6&&row.error==='disk full'));
  send(7,'flush');await drain();assert.equal(callbacks.length,1,'Failed saves must be retryable');
  callbacks.shift()(null);await drain();assert.ok(messages.some(row=>row.id===7&&row.result.saved));
  console.log('Browser worker: ordered durable ACKs, nonblocking input, coalescing and retry passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
