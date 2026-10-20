const assert=require('node:assert/strict');
const {match,png,options,path,DEFAULTS}=require('../web/visuals.js');
const catalogue=[{path:'/texture/base.0.hash.png',width:2048,height:2048},{path:'/texture/ui.0.hash.png',width:1024,height:1024}];
assert.equal(match('base.0.hash.png',catalogue),catalogue[0]);
assert.equal(match('texture/ui.0.hash.png',catalogue),catalogue[1]);
assert.throws(()=>match('different.png',catalogue),/No matching/);
assert.throws(()=>match('base.0.hash.png',[...catalogue,{path:'/other/base.0.hash.png'}]),/Ambiguous/);
assert.equal(path('http://localhost/singleweb/texture/base.0.hash.png'),'/texture/base.0.hash.png');
assert.equal(path('http://localhost/assets/texture/base.0.hash.png'),'/texture/base.0.hash.png');
const bytes=new Uint8Array(33),view=new DataView(bytes.buffer);bytes.set([137,80,78,71,13,10,26,10]);view.setUint32(8,13);view.setUint32(12,0x49484452);view.setUint32(16,2048);view.setUint32(20,2048);
assert.deepEqual(png(bytes.buffer),{width:2048,height:2048});
view.setUint32(16,9000);assert.throws(()=>png(bytes.buffer),/dimensions/);
assert.throws(()=>png(new ArrayBuffer(40)),/PNG/);
assert.deepEqual(options({getItem:()=>null}),DEFAULTS);
assert.deepEqual(options({getItem:()=>'{"enemyHP":true,"textures":false}'}),{...DEFAULTS,textures:false,enemyHP:true});
console.log('Texture matching, PNG bounds and default switches passed');

const constrained=options({getItem:()=>JSON.stringify({brightness:3,damageFlash:-2,camera:99,enemyFontSize:200,minimapPosition:'bad',background:'url(evil)'})});assert.equal(constrained.brightness,1);assert.equal(constrained.damageFlash,0);assert.equal(constrained.camera,0);assert.equal(constrained.enemyFontSize,48);assert.equal(constrained.minimapPosition,'bottom-right');assert.equal(constrained.background,DEFAULTS.background);
