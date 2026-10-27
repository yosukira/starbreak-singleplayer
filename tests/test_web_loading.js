'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
(async()=>{
 const ids=[...fs.readFileSync('web/index.html','utf8').matchAll(/id="([^"]+)"/g)].map(m=>m[1]),elements=new Map();let frame=null,nativePlay=null,nativeWatch=false,shortcut=null;
 const pendingNativeStatuses=[],commands=[];
 const element=()=>({hidden:false,disabled:false,value:'',dataset:{},style:{},attributes:{},classList:{add(){},toggle(){},remove(){}},setAttribute(key,value){this.attributes[key]=value;},replaceChildren(...children){if(this===elements.get('stage'))frame=children.find(c=>c.tagName==='IFRAME')||null;},prepend(){},show(){this.open=true;},close(){this.open=false;},focus(){}});
 for(const id of ids)elements.set(id,element());elements.get('map').value='egy_hulk';
 const layout=element(),arena=element(),events={};let fullscreenRequests=0;
 arena.requestFullscreen=()=>{fullscreenRequests++;document.fullscreenElement=arena;return Promise.resolve();};
 class Editor{constructor(){this.map='egy_hulk';this.shell=1;this.profile={settings:{map:'egy_hulk'}};this.preferences={keys:{}};this.data={stats:[],items:[]};}async load(){}render(){}remember(){}selectClass(shell){this.shell=shell;}async save(){}async post(){return {assets_ready:true};}}
 const document={addEventListener(){},getElementById:id=>elements.get(id),documentElement:{},querySelectorAll:()=>[],querySelector:q=>q==='iframe'?frame:q==='.game-layout'?layout:q==='.arena-panel'?arena:element(),createElement:tag=>({...element(),tagName:tag.toUpperCase(),contentWindow:{focus(){},postMessage(){}}})};
 const context={URLSearchParams,document,window:null,location:{origin:'https://test.local'},localStorage:{getItem:()=>null,setItem(){}},navigator:{language:'en'},WebEquipment:Editor,StarBreakVisuals:{Page:class{render(){}show(){}}},BattleRecords:class{constructor(root,language,catalogue,play){nativePlay=play;}show(){this.visible=true;}hide(){this.visible=false;}render(){}},PracticeShortcuts:class{constructor(_window,_keys,run){shortcut=run;}update(){}},Option:class{},setTimeout,clearTimeout,Promise,console,
  fetch:async (url,options)=>{if(url==='/api/native/command'){nativeWatch=true;commands.push(JSON.parse(options.body));return Response.json({accepted:true});}if(url==='/api/native/status')return new Promise(resolve=>pendingNativeStatuses.push(value=>resolve(Response.json(value))));return Response.json({maps:[{id:'egy_hulk',title:'Elite Graveyard Boss',ready:true}]});},addEventListener:(name,fn)=>events[name]=fn};context.window=context;
 context.StarBreakStorage=context.localStorage;
 vm.createContext(context);vm.runInContext(fs.readFileSync('web/guide.js','utf8'),context);vm.runInContext(fs.readFileSync('web/battle-view.js','utf8'),context);vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
 await new Promise(r=>setTimeout(r,10));
 await elements.get('start').onclick();assert.ok(frame);assert.equal(elements.get('tab-records').disabled,true,'History cannot interrupt world initialization');
 elements.get('tab-records').onclick();assert.equal(layout.hidden,false);
 elements.get('tab-guide').onclick();assert.equal(layout.hidden,false,'Guide cannot interrupt world initialization');assert.equal(elements.get('tab-guide').disabled,true);
 for(let n=0;n<35;n++){events.message({origin:context.location.origin,source:frame.contentWindow,data:{type:'player-state'}});await new Promise(r=>setTimeout(r,10));}
 assert.equal(elements.get('loading-overlay').hidden,true,'Continuous ready events must not postpone hiding the overlay');
 assert.equal(elements.get('tab-records').disabled,false);
 assert.equal(fullscreenRequests,1,'An explicit initial start may enter fullscreen');
 document.fullscreenElement=null;const oldFrame=frame;
 shortcut('restart');await new Promise(r=>setTimeout(r,10));
 assert.notEqual(frame,oldFrame);assert.equal(fullscreenRequests,1);assert.equal(document.fullscreenElement,null,'Restart preserves windowed mode');
 events.message({origin:context.location.origin,source:frame.contentWindow,data:{type:'player-state'}});await new Promise(r=>setTimeout(r,240));
 document.fullscreenElement=arena;
 shortcut('ic');await new Promise(r=>setTimeout(r,10));
 assert.equal(fullscreenRequests,1);assert.equal(document.fullscreenElement,arena,'Class changes preserve existing fullscreen');
 events.message({origin:context.location.origin,source:frame.contentWindow,data:{type:'player-state'}});await new Promise(r=>setTimeout(r,240));
 const running=frame;elements.get('tab-guide').onclick();assert.equal(layout.hidden,true);assert.equal(elements.get('guide-page').hidden,false);
 assert.equal(elements.get('tab-guide').attributes['aria-pressed'],'true');assert.equal(elements.get('tab-battle').attributes['aria-pressed'],'false');
 shortcut('restart');assert.equal(frame,running,'Reading the guide cannot restart an active game');
 assert.ok(elements.get('guide-page').innerHTML.includes('Quick start'));
 elements.get('language').onchange({target:{value:'zh-affixes'}});assert.ok(elements.get('guide-page').innerHTML.includes('快速上手'));
 elements.get('language').onchange({target:{value:'en'}});assert.ok(elements.get('guide-page').innerHTML.includes('Quick start'));
 elements.get('tab-battle').onclick();assert.equal(layout.hidden,false);assert.equal(elements.get('guide-page').hidden,true);assert.equal(frame,running);
 elements.get('tab-records').onclick();assert.equal(layout.hidden,true);
 await nativePlay({id:'owned-native-record',platform:'web',map_title:'Native fixture',replay:{id:'native-attempt',engine:'native'}});
 assert.equal(nativeWatch,true);assert.equal(frame,null,'Native replays never create a browser game iframe');
 await new Promise(r=>setTimeout(r,240));
 await nativePlay({id:'second-native-record',platform:'native',map_title:'Second fixture',replay:{id:'second-attempt',engine:'native'}});
 assert.equal(pendingNativeStatuses.length,2);
 pendingNativeStatuses[0]({state:'live'});
 await new Promise(r=>setTimeout(r,0));
 assert.equal(elements.get('replay-controls').hidden,false,'A delayed status from the old replay must not clear the new replay');
 pendingNativeStatuses[1]({state:'paused',seconds:1,duration:10,takeover:true});
 await new Promise(r=>setTimeout(r,0));
 assert.equal(elements.get('replay-pause').textContent,'Resume','The pause label follows authoritative engine status');
 elements.get('replay-pause').onclick();
 assert.equal(commands.at(-1).action,'pause');assert.equal(commands.at(-1).paused,false,'One click resumes a replay paused outside the editor');
 // Switching to browser playback also invalidates the native polling loop.
 await new Promise(r=>setTimeout(r,240));
 await nativePlay({id:'browser-record',map_title:'Browser fixture',replay:{id:'browser-attempt',engine:'browser'}});
 assert.ok(frame);
 // The native editor must initialize even without the browser-only fullscreen
 // controller. A missing optional game helper cannot disable every editor tab.
 context.location.search='?desktop=1';document.body=element();
 context.setTimeout=(fn,delay)=>delay===2000?0:setTimeout(fn,delay);
 delete context.StarBreakBattleView;
 vm.runInContext(fs.readFileSync('web/app.js','utf8'),context);
 await new Promise(r=>setTimeout(r,10));
 assert.equal(elements.get('equipment-dialog').open,true,'Native equipment opens without a fullscreen helper');
 assert.equal(elements.get('battle-fullscreen').hidden,true);
 elements.get('tab-guide').onclick();assert.equal(elements.get('equipment-dialog').open,false);
 elements.get('tab-equipment').onclick();assert.equal(elements.get('equipment-dialog').open,true);
 console.log('Continuous world-ready events and loading navigation lock passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
