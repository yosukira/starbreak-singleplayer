// Exercise the pinned client's binary ABI and event lifecycle without a browser.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const heap = new ArrayBuffer(32768), i32 = new Int32Array(heap), u8 = new Uint8Array(heap);
const sent = [], messages = [];
let now = 0, currentSocket, socketsCreated=0;
const timers=[];
const listeners={};
class Socket {
  constructor(){currentSocket=this;socketsCreated++;}
  static OPEN = 1;
  readyState = 1;
  bufferedAmount = 0;
  send(value) { sent.push(JSON.parse(value)); }
}
const context = {
  WebSocket: Socket, STARBREAK_WEB_BOOT: {wsPort: 8766,keybindings:{interact:'Auto'}},
  location: {hostname: '127.0.0.1', origin: 'http://127.0.0.1:8765'},
  Module: {HEAP32: i32, HEAPU8: u8},
  performance: {now: () => now += 20}, Date,
  setTimeout(callback,delay){timers.push({callback,delay});return timers.length;},
  parent: {postMessage: value => messages.push(value)},
  addEventListener(name, callback) {listeners[name]=callback;}, document: {getElementById: () => ({focus() {}, hidden: false})},
  console: {error: error => { throw error; }},
};
context.window=context; context.document.addEventListener=()=>{};
context.PracticeShortcuts=require('../web/shortcuts.js');
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../web/bridge.js'), 'utf8'), context);
const set = (pointer, offset, value) => i32[(pointer + offset) >> 2] = value;
const world = 1024, player = 4096, subworld = 8192, update = 12288, damage = 16384;
set(world, 672, player); set(world, 664, subworld); set(world, 200, 77);
set(subworld, 4, 2); set(subworld, 8, 123456); // World time is not a world ID.
set(player, 8, 1); set(player, 64, 150); set(player, 68, 150);
context.Module.webFrame(world);
assert.equal(sent[0].player.worldid, 2);
assert.equal(sent[0].player.hp, 150);
set(update, 52, 8); set(update, 16, damage);
set(damage, 4, 1); set(damage, 8, 88); set(damage, 12, 17); set(damage, 36, 1);
u8[damage + 16] = 1;
context.Module.webUpdates(world, update);
context.Module.webFrame(world);
assert.equal(sent[1].events.length, 1);
assert.equal(sent[1].events[0].value, 17);
assert.equal(sent[1].events[0].victim, 88);
assert.equal(sent[1].events[0].ownerid, 1);
assert.equal(sent[1].events[0].crit, true);
assert.equal(sent[1].events[0].native_seed, 77);
context.Module.webFrame(world);
assert.equal(sent[2].events.length, 0, 'Events drain once per frame');
set(damage, 36, 0); // Omitted owner defaults to the local subworld.
context.Module.webUpdates(world, update);
context.Module.webFrame(world);
assert.equal(sent[3].events[0].ownerid, 2);
set(world, 672, 0);
context.Module.webFrame(world);
assert.equal(sent[4].player.hp, 0);
assert.equal(sent[4].player.alive, false, 'Player destruction ends the attempt');
// Original game totalInput owns interaction, independently of the physical key.
set(world,672,player);set(world,668,128);context.Module.webFrame(world);
assert.equal(sent.at(-1).keys.interact,true);assert.equal(sent.at(-1).keys.interact_edge,true);
context.Module.webFrame(world);assert.equal(sent.at(-1).keys.interact_edge,false);
set(world,668,0);listeners.keydown({code:'KeyL',preventDefault(){}});context.Module.webFrame(world);
assert.equal(sent.at(-1).keys.interact,false,'Hard-coded L cannot override original bindings');
// A new room cannot inherit a destroyed previous player or old damage events.
set(world,200,0xf1234567);set(world,672,0);const oldCount=sent.length;context.Module.webFrame(world);
assert.equal(sent.length,oldCount,'Do not report previous-room death before new player arrives');
set(world,672,player);set(damage,36,1);context.Module.webUpdates(world,update);context.Module.webFrame(world);
assert.equal(sent.at(-1).events[0].native_seed,0xf1234567);
console.log('Browser ABI: state, damage ownership, unsigned seed, death, room reset and player bindings passed.');
// Successful pickup observers count consumed packs, not spawned loot. Healing
// uses the central clamped health operation, including non-pickup healing.
const loot=20000,description=22000,list=24000,boost=26000;
set(loot,356,description);set(description,128,list);set(description,132,1);set(list,0,boost);set(boost,4,1);
set(player,64,100);context.Module.webFrame(world);
context.Module.webLoot(loot,player,0);context.Module.webHealth(player,0);set(player,64,150);context.Module.webHealth(player,1);context.Module.webLoot(loot,player,1);
context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.health_packs,1);
assert.equal(sent.at(-1).player.battle_metrics.healing,50);
context.Module.webHealth(player,0);context.Module.webHealth(player,1);context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.healing,50,'Full-health overheal counts as zero');
set(boost,4,3);context.Module.webLoot(loot,player,0);context.Module.webLoot(loot,player,1);context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.ammo_packs,1);
context.Module.webLoot(loot,player,1);context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.ammo_packs,1,'No completed pickup without its successful entry');
set(world,200,123);context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.health_packs,0,'Counters are scoped to one battle');
console.log('Pickup counts and actual clamped healing passed');

// The takeover transport must still deliver room controls to the same real
// inventory reader. A passed time barrier returns a fresh scoped inventory.
const ordinarySocket=currentSocket;
ordinarySocket.onclose();
assert.equal(timers.length,1);assert.equal(timers[0].delay,500);
timers.shift().callback();
const replacementSocket=currentSocket;
assert.notEqual(replacementSocket,ordinarySocket,'Ordinary telemetry reconnects after a startup disconnect');
ordinarySocket.onclose();
assert.equal(timers.length,0,'A retired socket cannot schedule a second reconnect');
replacementSocket.onclose();
const pendingReconnect=timers.shift().callback;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/inventory.js'),'utf8'),context);
set(world,628,subworld);set(player,468,1);set(player,472,28000);set(28000,4,1);
const branchSocket=new Socket();context.Module.webStartLiveTelemetry(branchSocket);
const branchSocketCount=socketsCreated;
pendingReconnect();
assert.equal(socketsCreated,branchSocketCount,'A pending ordinary reconnect cannot replace the takeover transport');
assert.equal(replacementSocket.onmessage,null,'Old acknowledgements cannot replace the takeover room context');
const scope={map:'egy_run',room_token:3,room_seed:123,worldid:2,objid:1,charid:1,player_type:1};
branchSocket.onmessage({data:JSON.stringify({...scope,room_ready:true,inventory_request:{id:3,attempt:1,
  scope,expires_at:Date.now()/1000+10,native_t_min:123450}})});
context.Module.webFrame(world);
assert.equal(sent.at(-1).player.inventory.request_id,3);
assert.equal(sent.at(-1).player.inventory.native_server_t,123456);
assert.equal(sent.at(-1).player.inventory.scope.room_token,3);
assert.equal(sent.at(-1).room_context.map,'egy_run');
set(subworld,8,123000);context.Module.webFrame(world);
assert.equal(sent.at(-1).player.inventory,undefined,'Never bypass the native time barrier');
assert.equal(sent.at(-1).player.inventory_wait.reason,'native-time');
assert.equal(sent.at(-1).player.inventory_wait.required,123450);
console.log('Takeover transport preserves scoped inventory handoff and native time barrier');

const playbackContext={...context,Module:{HEAP32:i32,HEAPU8:u8},
  STARBREAK_WEB_BOOT:{wsPort:8766,keybindings:{interact:'Auto'},replay:{mode:'play'}}};
playbackContext.window=playbackContext;
const beforePlayback=socketsCreated;
vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../web/bridge.js'),'utf8'),playbackContext);
assert.equal(socketsCreated,beforePlayback,'Playback must never open ordinary live telemetry');
console.log('Startup reconnect, stale socket isolation and replay telemetry isolation passed');
// Participation latches before telemetry throttling and survives key release.
context.performance.now=()=>100000;
set(world,200,9876);set(world,672,player);set(player,64,150);set(world,668,0);
context.Module.webFrame(world);assert.equal(sent.at(-1).keys.gameplay,false);
set(world,668,1);context.Module.webFrame(world);
set(world,668,0);context.Module.webFrame(world);
context.performance.now=()=>100020;context.Module.webFrame(world);
assert.equal(sent.at(-1).keys.gameplay,true,'A short tap between UDP samples counts');
set(world,200,9877);set(world,668,1<<8|1<<10);context.performance.now=()=>100040;context.Module.webFrame(world);
assert.equal(sent.at(-1).keys.gameplay,false,'New worlds and non-gameplay shortcuts do not inherit activity');
console.log('Browser activity: idle/menu excluded, short taps retained, and new-world reset passed.');
// Count actual clamped HP losses, including several hits and healing in one
// frame. Damage events/armor estimates cannot substitute for these results.
set(world,200,11111);set(world,668,1);set(player,64,150);context.performance.now=()=>100060;context.Module.webFrame(world);
context.Module.webHealth(player,0);set(player,64,133);context.Module.webHealth(player,1);
context.Module.webHealth(player,0);set(player,64,150);context.Module.webHealth(player,1);
context.Module.webHealth(player,0);context.Module.webHealth(player,1);
context.performance.now=()=>100080;context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.damage_taken,17);
assert.equal(sent.at(-1).player.battle_metrics.healing,17);
set(player,64,100);context.Module.webHealth(player,0);context.Module.webHealth(player,0);
set(player,64,110);context.Module.webHealth(player,1);set(player,64,90);context.Module.webHealth(player,1);
context.Module.webHealth(player,0);set(player,64,-1000);context.Module.webHealth(player,1);
context.performance.now=()=>100100;context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.damage_taken,127);
assert.equal(sent.at(-1).player.battle_metrics.healing,27);
set(world,200,11112);set(player,64,150);context.performance.now=()=>100120;context.Module.webFrame(world);
assert.equal(sent.at(-1).player.battle_metrics.damage_taken,0,'Next battle starts at zero');
console.log('Damage taken: actual HP, mitigation, same-frame healing, nested changes, fatal overkill and room reset passed.');
