// Exercise the pinned client's binary ABI and event lifecycle without a browser.
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const heap = new ArrayBuffer(32768), i32 = new Int32Array(heap), u8 = new Uint8Array(heap);
const sent = [], messages = [];
let now = 0, currentSocket;
const listeners={};
class Socket {
  constructor(){currentSocket=this;}
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
