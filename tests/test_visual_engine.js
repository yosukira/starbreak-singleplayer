const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const base=require('../web/visuals.js'),heap=new Int32Array(65536),bytes=new Uint8Array(heap.buffer),events={},draws=[];
let time=1000,settings={...base.DEFAULTS,enemyHP:true};
const makeCanvas=()=>{
  const canvas={width:800,height:600,style:{width:'800px',height:'600px',left:'0px',top:'0px'},text:null};
  canvas.getContext=()=>({measureText:t=>({width:String(t).length*9}),strokeText(){},fillText(t){canvas.text=String(t);},drawImage(image,x,y,w=image.width,h=image.height){draws.push({label:image.text,x,y,w,h});},clearRect(){},fillRect(){draws.push({bar:true});},strokeRect(){},save(){},restore(){},beginPath(){},rect(){},clip(){}});return canvas;
};
const main=makeCanvas(),Module={HEAP32:heap,HEAPU8:bytes,canvas:main},parent={document:{getElementById:()=>({textContent:'Test room'})}};
const context={Module,parent,location:{origin:'https://test.local'},StarBreakVisuals:{...base,Store:class{async list(){return [];}},options:storage=>storage?base.options(storage):settings},document:{body:{append(){}},createElement:makeCanvas},addEventListener:(key,fn)=>events[key]=fn,performance:{now:()=>time},console,Map,Set,TextDecoder};
const put=(p,o,v)=>heap[(p+o)>>2]=v;
put(2000,0,47524);put(2000,72,1000);put(2000,76,5000);
put(1000,628,6000);put(1000,672,3000);put(1000,200,42);
put(5000,0,46960);put(5000,32,1000);put(5000,92,800);put(5000,96,600);
put(6000,60,7000);put(6000,64,7008);put(7000,4,8000);
for(const [offset,value]of [[16,300*256],[20,200*256],[24,40*256],[28,60*256],[52,2],[64,120],[68,200],[104,40]])put(8000,offset,value);
// A real visible enemy has a non-empty VID string.
bytes[8100]=1;put(8000,132,9000);bytes.set(new TextEncoder().encode('hulk.robot'),9000);bytes[9011]=10;
put(3000,64,150);put(3000,68,150);
vm.createContext(context);vm.runInContext(fs.readFileSync('web/visual-engine.js','utf8'),context);Module.visualContext(2000);Module.postMainLoop();
assert.equal(draws.filter(row=>row.bar).length,0,'Enemy display must not draw bars');
const labels=draws.filter(row=>!row.bar);assert.deepEqual(labels.map(row=>row.label),['120','40']);
assert.ok(Math.abs(labels[0].x+labels[0].w/2-labels[1].x-labels[1].w/2)<=1);assert.equal(labels[0].y+labels[0].h,230);assert.equal(labels[1].y,230,'Armor must start below the shared body-center anchor');
const cache=Module.visualStats.textCache;draws.length=0;time+=16;Module.postMainLoop();assert.equal(Module.visualStats.textCache,cache,'Repeated numbers must reuse their cached sprites');
put(8000,104,0);draws.length=0;Module.postMainLoop();assert.deepEqual(draws.map(row=>row.label),['120'],'No zero-armor line');
function apply(value){settings={...base.DEFAULTS,...value};events.message({source:parent,origin:context.location.origin,data:{type:'visual-options',options:settings}});}
apply({showEdges:false,showScatter:false,showHitboxes:true,noShake:true,noFlash:true,camera:2,damageFlash:0,hideEffects:true,hideDamageNumbers:true});
for(const p of [79486,79487,79488,79489])bytes[p]=1;put(5000,144,7);put(5000,148,9);put(5000,152,1);put(5000,156,2);put(5000,260,0xffffffff);
const before=Buffer.from(bytes);Module.visualView(5000,0);assert.equal(bytes[79487],0);assert.equal(bytes[169019],1);assert.equal(heap[(5000+100)>>2],-1000000);
Module.visualView(5000,1);assert.equal(heap[(5000+144)>>2],1);assert.equal(heap[(5000+260)>>2],0);Module.visualView(5000,2);
assert.deepEqual(Buffer.from(bytes),before,'Renderer overrides must restore every modified byte');
assert.equal(Module.visualDamageAge(0),132);assert.equal(Module.visualDamageAge(-1),-1);assert.equal(Module.visualSetting(4),1);assert.equal(Module.visualSetting(5),1);
apply({hideFriendlyObjects:true});put(8000,52,1);assert.equal(Module.visualEntity(5000,8000),0);put(8000,76,1);assert.equal(Module.visualEntity(5000,8000),1,'Friendly platforms stay visible');
apply({hideShell:true});assert.equal(Module.visualEntity(5000,3000),0);
apply({customBackground:true,background:'#808080',brightness:.25});assert.equal(Module.visualBackground(0xff000000)>>>0,0xff404040);
console.log('SBPE body-center health/armor numbers, sprite cache, render-only filtering and exact restoration passed');

apply({enemyHP:true});put(8000,52,2);put(8000,76,0);main.style.width='400px';draws.length=0;Module.postMainLoop();assert.equal(draws[0].h,labels[0].h,'Enemy labels retain world-space size');assert.equal(draws[0].h*.5,labels[0].h*.5,'Zooming out scales the label with the enemy');
for(const scale of [.25,.75,1,2]){main.style.width=(800*scale)+'px';draws.length=0;Module.postMainLoop();assert.equal(draws[0].h,labels[0].h);assert.equal(draws[0].w/40,labels[0].w/40,'Text-to-enemy size ratio stays constant across zoom levels');}

// Objects can leave the world and their heap storage can be reused between
// consecutive renders, before the old 100 ms cache would have expired.
main.style.width='800px';draws.length=0;put(6000,64,7000);time+=1;Module.postMainLoop();
assert.equal(draws.length,0,'Removed objects must stop drawing on the next frame');
put(6000,64,7008);put(8000,132,0);draws.length=0;time+=1;Module.postMainLoop();
assert.equal(draws.length,0,'Invisible triggers and reused empty object storage have no health label');
put(8000,132,9000);put(8000,52,1);draws.length=0;time+=1;Module.postMainLoop();
assert.equal(draws.length,0,'Owner changes at a reused pointer must not retain enemy labels');
put(8000,52,2);draws.length=0;time+=1;Module.postMainLoop();
assert.deepEqual(draws.map(row=>row.label),['120'],'A visible current enemy still draws');

bytes[8100]=0;draws.length=0;time+=1;Module.postMainLoop();assert.equal(draws.length,0,'Hidden objects must not show health labels');
