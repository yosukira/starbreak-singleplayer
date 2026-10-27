const assert=require('node:assert/strict');
const PracticeShortcuts=require('../web/shortcuts.js');
let now=0,next=0;const timers=new Map(),listeners={},actions=[];
const originalSet=global.setTimeout,originalClear=global.clearTimeout;
global.setTimeout=(callback,delay)=>{const id=++next;timers.set(id,{callback,at:now+delay});return id;};
global.clearTimeout=id=>timers.delete(id);
const advance=ms=>{now+=ms;for(const [id,timer]of timers)if(timer.at<=now){timers.delete(id);timer.callback();}};
try{
  const target={addEventListener:(name,fn)=>listeners[name]=fn,document:{addEventListener(){}}};
  let blocked=false;
  const shortcuts=new PracticeShortcuts(target,{restart:'R',wf:'7',dw:'8',fab:'9',ic:'0',editor_toggle:'Backtick',hold_seconds:2},action=>actions.push(action),()=>blocked);
  const down=(code,extra={})=>listeners.keydown({code,preventDefault(){},target:{closest:()=>false},...extra});
  down('KeyR');advance(1999);assert.deepEqual(actions,[]);advance(1);assert.deepEqual(actions,['restart']);
  down('KeyR',{repeat:true});advance(3000);assert.equal(actions.length,1);listeners.keyup({code:'KeyR'});
  down('Digit7');advance(500);listeners.keyup({code:'Digit7'});advance(2000);assert.equal(actions.length,1);
  down('Numpad8');advance(2000);assert.equal(actions.at(-1),'dw');listeners.keyup({code:'Numpad8'});
  down('Digit9');listeners.blur();advance(2000);assert.equal(actions.at(-1),'dw');
  down('KeyR',{target:{closest:()=>true}});advance(2000);assert.equal(actions.at(-1),'dw');
  down('KeyR');blocked=true;advance(2000);assert.equal(actions.at(-1),'dw');listeners.keyup({code:'KeyR'});blocked=false;
  shortcuts.update({restart:'T',hold_seconds:.5});down('KeyR');advance(500);assert.equal(actions.at(-1),'dw');down('KeyT');advance(500);assert.equal(actions.at(-1),'restart');
  let reserved=true;
  shortcuts.gameplayKey=event=>reserved&&['Numpad7','KeyR'].includes(event.code);
  shortcuts.update({restart:'R',wf:'7',hold_seconds:2});
  const before=actions.length;
  down('Numpad7',{preventDefault(){throw Error('Gameplay input must reach the engine');}});advance(3000);
  down('KeyR',{preventDefault(){throw Error('A remapped gameplay key cannot restart');}});advance(3000);
  assert.equal(actions.length,before);
  down('Digit7');advance(2000);assert.equal(actions.at(-1),'wf','Unbound top-row shortcut still works');listeners.keyup({code:'Digit7'});
  reserved=false;down('KeyR');advance(1000);reserved=true;advance(1000);
  assert.equal(actions.at(-1),'wf','A Controls edit during a hold cancels the pending action');
  console.log('Browser shortcuts: hold threshold, one-shot, release/blur/input cancellation, numpad and rebinding passed.');
}finally{global.setTimeout=originalSet;global.clearTimeout=originalClear;}
const zoomActions=[],zoomListeners={};
new PracticeShortcuts({addEventListener:(key,fn)=>zoomListeners[key]=fn,document:{addEventListener(){}}},{zoom_in:'Equal',zoom_out:'Minus',hold_seconds:2},action=>zoomActions.push(action));
for(const code of ['Equal','NumpadAdd','Minus','NumpadSubtract']){zoomListeners.keydown({code,preventDefault(){},target:{closest:()=>false}});zoomListeners.keyup({code});}
assert.deepEqual(zoomActions,['zoom_in','zoom_in','zoom_out','zoom_out']);
