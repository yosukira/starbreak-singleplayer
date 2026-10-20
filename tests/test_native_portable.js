const assert=require('node:assert/strict');
const {factory}=require('../web/native-replay.js');
const Native=factory(class {});
function driver(ops){
  const replay=Object.create(Native.prototype);
  Object.assign(replay,{mode:'play',ops,cursor:0,driving:true,stack:[{kind:'inventory'}],lastNativeClock:50});
  return replay;
}
// The original platform may sample the clock more often while doing the
// same operation. Only samples at the completed call boundary are unused.
const fewer=driver([['clock',51],['clock',52],['return','inventory']]);
fewer.leave(7,0);assert.equal(fewer.cursor,3);assert.equal(fewer.lastNativeClock,52);
const more=driver([['return','inventory']]);
assert.equal(more.clock(900),50);assert.equal(more.cursor,0);
more.leave(7,0);assert.equal(more.cursor,1);
const wrong=driver([['input',2,1],['return','inventory']]);
assert.throws(()=>wrong.leave(7,0),/diverged/);
const bytes=new Uint8Array(128),heap=new Int32Array(bytes.buffer);
const inventory=driver([['inventory',2,3840,1024,7],['return','inventory']]);
inventory.stack=[];inventory.world=100;inventory.module={HEAPU8:bytes,HEAP32:heap,_malloc:()=>32,_free:()=>{},asm:{nativeReplayInventory(world,p){
  assert.equal(world,100);assert.deepEqual([heap[(p+4)>>2],heap[(p+8)>>2],heap[(p+12)>>2],heap[(p+20)>>2]],[2,3840,1024,7]);
  inventory.enter(7,world,p,0,0);inventory.leave(7,world);
}}};
inventory.invoke(inventory.ops[0]);assert.equal(inventory.cursor,2);
console.log('Portable inventory operations and platform clock boundaries passed.');
