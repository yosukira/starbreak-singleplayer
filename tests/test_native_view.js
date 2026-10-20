const assert=require('node:assert/strict'),{factory,nativeView}=require('../web/native-replay.js');
const ops=[];for(let i=0;i<3;i++)ops.push(['loop',0],['message',10,'world'],['step',1000],['state',[0,0]],['loop',1]);
assert.deepEqual(nativeView(ops,2),{start:5,end:10,offset:1000,duration:1000});
let notices=[];
class Base{constructor(options){this.notice=options.notice;this.module={};}seek(seconds){this.seekArgument=seconds;}setPaused(value){this.paused=value;}}
const Native=factory(Base),replay=new Native({document:{ops,view_world:2,bootstrap:{}},module:{},notice:value=>notices.push(value)});
replay.notice({state:'failed',message:'real failure'});assert.equal(notices.at(-1).state,'failed');assert.equal(notices.at(-1).message,'real failure');
replay.notice({state:'playing',seconds:.5,duration:3});assert.equal(notices.at(-1).state,'preparing');assert.equal(notices.at(-1).seconds,0);
replay.seek(.2);assert.equal(replay.seekArgument,1.2);
replay.priming=false;replay.notice({state:'playing',seconds:1.5,duration:3});assert.equal(notices.at(-1).seconds,.5);assert.equal(notices.at(-1).duration,1);
console.log('Selected native battle timing and visible reconstruction failures passed.');
