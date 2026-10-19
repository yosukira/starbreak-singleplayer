'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const ids=[...fs.readFileSync('web/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]),elements=new Map();let frame=null;
 const element=()=>({hidden:false,disabled:false,value:'',dataset:{},style:{},classList:{toggle(){},remove(){}},setAttribute(){},replaceChildren(...children){if(this===elements.get('stage'))frame=children.find(c=>c.tagName==='IFRAME')||null;},close(){this.open=false;},focus(){}});
 for(const id of ids)elements.set(id,element());elements.get('map').value='egy_hulk';
 const layout=element(),events={};
 class Editor{constructor(){this.map='egy_hulk';this.shell=1;this.profile={settings:{map:'egy_hulk'}};this.preferences={keys:{}};this.data={stats:[],items:[]};}async load(){}render(){}async save(){}async post(){return {assets_ready:true};}}
 const document={getElementById:id=>elements.get(id),documentElement:{},querySelectorAll:()=>[],querySelector:q=>q==='iframe'?frame:q==='.game-layout'?layout:element(),createElement:tag=>({...element(),tagName:tag.toUpperCase(),contentWindow:{focus(){},postMessage(){}}})};
 const context={document,window:null,location:{origin:'https://test.local'},localStorage:{getItem:()=>null,setItem(){}},navigator:{language:'en'},WebEquipment:Editor,BattleRecords:class{show(){this.visible=true;}hide(){this.visible=false;}render(){}},PracticeShortcuts:class{update(){}},Option:class{},setTimeout,clearTimeout,Promise,console,
  fetch:async()=>Response.json({maps:[{id:'egy_hulk',title:'Elite Graveyard Boss',ready:true}]}),addEventListener:(name,fn)=>events[name]=fn};context.window=context;
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
 await new Promise(r=>setTimeout(r,10));
 await elements.get('start').onclick();assert.ok(frame);assert.equal(elements.get('tab-records').disabled,true,'History cannot interrupt world initialization');
 elements.get('tab-records').onclick();assert.equal(layout.hidden,false);
 for(let n=0;n<35;n++){events.message({origin:context.location.origin,source:frame.contentWindow,data:{type:'player-state'}});await new Promise(r=>setTimeout(r,10));}
 assert.equal(elements.get('loading-overlay').hidden,true,'Continuous ready events must not postpone hiding the overlay');
 assert.equal(elements.get('tab-records').disabled,false);elements.get('tab-records').onclick();assert.equal(layout.hidden,true);
 console.log('Continuous world-ready events and loading navigation lock passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
