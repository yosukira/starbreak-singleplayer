'use strict';
const assert=require('node:assert/strict');
const {filtered,totals}=require('../web/records.js');
const base={started:new Date(2026,9,16,12).getTime()/1000,duration:30,map:'egy_hulk',shell:1,result:'victory',source:'local',rules_hash:'same',metrics:{damage_dealt:100}};
const rows=[base,{...base,shell:3,result:'defeat',duration:60},{...base,result:'interrupted',duration:90},{...base,result:'active',duration:100},{...base,started:base.started-86400,map:'hulk_normal',source:'import',rules_hash:'other',metrics:{damage_dealt:null}}];
assert.equal(filtered(rows,{from:'2026-10-16',to:'2026-10-16'}).length,4);
assert.equal(filtered(rows,{classes:['3'],maps:['egy_hulk'],results:['defeat']}).length,1);
assert.equal(filtered(rows,{source:'import',rules:'other'}).length,1);
assert.equal(filtered(rows,{sort:'duration',direction:1})[0].duration,30);
const stats=totals(rows.slice(0,4));assert.equal(stats.winrate,50);assert.equal(stats.average,60);assert.equal(stats.damage,400);
assert.equal(totals([{...base,metrics:{damage_dealt:null}}]).damage,null);
console.log('Battle record filters, sorting and summary denominators passed');
const {normalizeFilters}=require('../web/records.js');
assert.deepEqual(normalizeFilters({maps:123,classes:[1,'2'],results:null,from:'wrong',direction:'up'}),{maps:[],classes:['2'],results:[],direction:0});
assert.equal(normalizeFilters({from:'2026-10-16'}).from,'2026-10-16');

const {turretSummary}=require('../web/records.js');
const layout=['front','upper','lower'].flatMap((row,index)=>Array.from({length:[4,7,7][index]},(_,i)=>({row,ammo:i<2?(row==='front'?'scattershot':'bossmissile'):'csbullet'})));
assert.equal(turretSummary({map:'egy_hulk',turret_layout:layout}),'4(2)-7(2)-7(2)');
assert.equal(turretSummary({map:'hulk_normal',turret_layout:[{row:'upper',ammo:'csbullet'}]}),'0(0)-1(0)-0(0)');
assert.equal(turretSummary({map:'city_mid',turret_layout:layout}),'');
assert.equal(turretSummary({map:'egy_hulk'}),'');
console.log('Per-battle Graveyard turret counts use front scatter and upper/lower missiles.');
const {equippedLoadout}=require('../web/records.js');
const equipped={type_:216,slotpos_:256},implant={type_:309,slotpos_:1536},bag={type_:215,slotpos_:3840};
assert.deepEqual(equippedLoadout({parameters:{loadout:{items_:[bag,implant,equipped]}}}),[equipped,implant]);
assert.deepEqual(equippedLoadout({}),[]);
console.log('Starting loadout excludes backpack contents and preserves equipped slot order.');
const {nickname}=require('../web/records.js');
assert.equal(nickname(' Player_34 '),'Player_34');assert.equal(nickname(''),'');
for(const invalid of ['null','UnKnOwN','unknow','n_u_l_l','中文','<script>', 'a'.repeat(33)])assert.throws(()=>nickname(invalid));
console.log('Explicit English sharing nicknames reject reserved and unsafe names.');
for(const invalid of ['XiJinPing','xi_jin_ping','Xi.Jin-Ping_42','x1j1np1ng','MaoZeDong','Hu_Jin_Tao'])assert.throws(()=>nickname(invalid));
for(const valid of ['Player_34','ChinaPlayer','Xi']){
  assert.equal(nickname(valid),valid);
}
// Run the real controller method with both local and unimported shared details.
const vm=require('node:vm'),fs=require('node:fs');
const ui={window:{},localStorage:{getItem(){return null;}}};
vm.runInNewContext(fs.readFileSync(require.resolve('../web/records.js'),'utf8'),ui);
// A blocked browser store used to abort construction before app.js bound the
// equipment button, leaving the desktop welcome screen with inert controls.
const blocked={window:{}};
Object.defineProperty(blocked,'localStorage',{get(){throw Error('Storage disabled');}});
vm.runInNewContext(fs.readFileSync(require.resolve('../web/records.js'),'utf8'),blocked);
const blockedRecords=new blocked.window.BattleRecords({addEventListener(){}},()=> 'en');
assert.equal(blockedRecords.pageSize,50);
vm.runInNewContext(fs.readFileSync(require.resolve('../web/editor.js'),'utf8'),blocked);
assert.equal(vm.runInNewContext("StarBreakStorage.getItem('missing')",blocked),null);
vm.runInNewContext("StarBreakStorage.setItem('draft','kept')",blocked);
assert.equal(vm.runInNewContext("StarBreakStorage.getItem('draft')",blocked),'kept');
const original={id:'local',parameters:{loadout:{items_:[equipped,implant,bag]}}};
const shared={...original,id:'shared'};
for(const row of [original,shared]){
  const cards=[{dataset:{record:row.id},open:false},{dataset:{record:'other'},open:false}];
  const instance={rows:[original],sharedDetails:new Map([['shared',shared]]),current:row.id,expandedEquipment:new Map(),root:{querySelectorAll(){return cards;}}};
  ui.window.BattleRecords.prototype.setEquipmentExpanded.call(instance,true);
  assert.equal(cards[0].open,true);assert.equal(cards[1].open,false);
  assert.deepEqual([...instance.expandedEquipment.get(row.id)],[256,1536]);
  ui.window.BattleRecords.prototype.setEquipmentExpanded.call(instance,false);
  assert.equal(cards[0].open,false);assert.equal(instance.expandedEquipment.get(row.id).size,0);
}
console.log('Local/shared expand-collapse and English political-name restrictions passed.');
const {battleDuration}=require('../web/records.js');
assert.equal(battleDuration({map:'egy_hulk',duration:150,combat_duration:130,parameters:{skip_intro:false}}),'2:10 (2:30)');
assert.equal(battleDuration({map:'hulk_normal',duration:130,combat_duration:130,parameters:{skip_intro:true}}),'2:10');
assert.equal(battleDuration({map:'egy_hulk',duration:150,combat_duration:null}),'2:30');
assert.equal(battleDuration({map:'ice_boss',duration:150,combat_duration:130}),'2:30');
console.log('Graveyard adjusted/full duration and legacy unknown timing passed.');
