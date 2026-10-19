/* Browser battle history. The native editor uses its own Tk tab. */
(() => {
  'use strict';
  const classes={1:'Wildfire',2:'Duskwing',5:'Fabricator',3:'Ironclad'};
  const abbreviations={1:'WF',2:'DW',5:'FAB',3:'IC'};
  const metricLabels={damage_dealt:['造成伤害','Damage dealt'],kills:['击杀','Kills'],health_packs:['血包','Health packs'],ammo_packs:['弹药包','Ammo packs'],healing:['恢复生命','Healing'],damage_taken:['承受伤害','Damage taken'],generators_destroyed:['摧毁 Gen','Generators destroyed']};
  const graveyard=row=>['egy_hulk','hulk_normal','egy_run'].includes(row.map);
  function turretSummary(row){
    if(!graveyard(row)||!Array.isArray(row.turret_layout)||!row.turret_layout.length)return '';
    return ['front','upper','lower'].map(side=>{
      const slots=row.turret_layout.filter(t=>t.row===side);
      const special=side==='front'?'scattershot':'bossmissile';
      return `${slots.length}(${slots.filter(t=>t.ammo===special).length})`;
    }).join('-');
  }
  const equippedLoadout=row=>(row.parameters?.loadout?.items_||[]).filter(item=>Number.isInteger(item.slotpos_)&&item.slotpos_>0&&item.slotpos_<3840).sort((a,b)=>a.slotpos_-b.slotpos_);
  const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const politicalNames=['xijinping','maozedong','dengxiaoping','jiangzemin','hujintao','likeqiang','liqiang','wenjiabao','zhurongji','zhouenlai','donaldtrump','joebiden','vladimirputin','volodymyrzelensky'];
  function nickname(value){const name=String(value??'').trim();if(!name)return '';if(!/^[A-Za-z0-9][A-Za-z0-9 ._-]{0,31}$/.test(name)||['null','none','undefined','unknown','unknow','anonymous','nil','nan'].includes(name.toLowerCase().replace(/[^a-z0-9]/g,'')))throw Error('nickname');const plain=name.toLowerCase().replace(/[^a-z0-9]/g,''),latin=plain.replace(/[013457]/g,c=>({'0':'o','1':'i','3':'e','4':'a','5':'s','7':'t'}[c]));if(politicalNames.some(term=>plain.includes(term)||latin.includes(term)))throw Error('political-nickname');return name;}
  const day=value=>{const d=new Date(value);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
  function battleDuration(row){const full=duration(row.duration||0),combat=row.combat_duration;return ['egy_hulk','hulk_normal'].includes(row.map)&&Number.isFinite(combat)?duration(combat)+((row.parameters?.skip_intro??row.skip_intro)?'':` (${full})`):full;}
  const duration=value=>`${Math.floor(value/60)}:${String(Math.floor(value%60)).padStart(2,'0')}`;
  function filtered(rows,filters){
    return rows.filter(row=>{
      const date=day(row.started*1000);
      return (!filters.from||date>=filters.from)&&(!filters.to||date<=filters.to)&&
        (!filters.maps?.length||filters.maps.includes(row.map))&&(!filters.classes?.length||filters.classes.includes(String(row.shell)))&&
        (!filters.results?.length||filters.results.includes(row.result))&&(!filters.source||row.source===filters.source)&&
        (!filters.rules||row.rules_hash===filters.rules);
    }).sort((a,b)=>{
      const get=r=>filters.sort==='duration'?r.duration:filters.sort==='damage'?r.metrics.damage_dealt??-1:r.started;
      return (get(b)-get(a))*(filters.direction===1?-1:1);
    });
  }
  function totals(rows){
    const wins=rows.filter(r=>r.result==='victory').length,losses=rows.filter(r=>r.result==='defeat').length;
    const completed=rows.filter(r=>r.result!=='active');
    return {count:rows.length,wins,losses,winrate:wins+losses?wins/(wins+losses)*100:null,
      average:completed.length?completed.reduce((n,r)=>n+r.duration,0)/completed.length:null,
      damage:rows.some(r=>r.metrics.damage_dealt!==null)?rows.reduce((n,r)=>n+(r.metrics.damage_dealt??0),0):null};
  }
  function normalizeFilters(value){
    const result={maps:[],classes:[],results:[]};
    if(!value||typeof value!=='object')return result;
    for(const key of ['maps','classes','results'])if(Array.isArray(value[key]))result[key]=value[key].filter(v=>typeof v==='string').slice(0,100);
    for(const key of ['from','to'])if(typeof value[key]==='string'&&/^\d{4}-\d{2}-\d{2}$/.test(value[key]))result[key]=value[key];
    for(const key of ['rules','source','sort','preset'])if(typeof value[key]==='string')result[key]=value[key].slice(0,100);
    result.direction=value.direction===1?1:0;return result;
  }
  if(typeof module!=='undefined')module.exports={filtered,totals,day,duration,normalizeFilters,turretSummary,graveyard,equippedLoadout,nickname,battleDuration};
  if(typeof window==='undefined')return;
  window.BattleRecords=class {
    constructor(root,language,catalogue=()=>[],play=()=>{},maps=()=>[]){
      this.root=root;this.language=language;this.catalogue=catalogue;this.maps=maps;this.sharedFilters={};this.sharedRequest=0;this.play=play;this.rows=[];this.selected=new Set();this.current=null;this.visible=false;this.timer=null;this.notice='';this.shareResults={};this.view='local';this.sharedRows=[];this.sharedDetails=new Map();this.sharedNotice='';this.shareDraft=null;this.comments=new Map();this.commentDrafts=new Map();this.expandedEquipment=new Map();this.codeOpen=false;this.codeDraft='';
      try{this.filters=JSON.parse(localStorage.getItem('starbreak-record-filters'))||{};}catch(_){this.filters={};}
      this.filters=normalizeFilters(this.filters);
      try{const saved=JSON.parse(localStorage.getItem('starbreak-shared-filters'));if(saved&&typeof saved==='object')for(const key of ['map','shell','front','upper','lower'])if(typeof saved[key]==='string'&&saved[key].length<80)this.sharedFilters[key]=saved[key];}catch(_){}
      this.root.addEventListener('click',e=>this.click(e));
      this.root.addEventListener('change',e=>this.change(e));
      this.root.addEventListener('toggle',e=>{if(!e.target.matches?.('.record-equipped')||!this.root.contains(e.target))return;const {record,slot}=e.target.dataset;const open=this.expandedEquipment.get(record)||new Set();e.target.open?open.add(Number(slot)):open.delete(Number(slot));this.expandedEquipment.set(record,open);},true);
      this.root.addEventListener('input',e=>{if(e.target.matches?.('input[data-shared-filter]')){this.sharedFilters[e.target.dataset.sharedFilter]=e.target.value;this.rememberSharedFilters();clearTimeout(this.sharedFilterTimer);this.sharedFilterTimer=setTimeout(()=>{if(e.target.validity.valid)void this.refreshShared();},350);return;}if(e.target.hasAttribute('data-comment-text')||e.target.hasAttribute('data-comment-id')){const draft=this.commentDrafts.get(this.current)||{};draft[e.target.hasAttribute('data-comment-id')?'nickname':'text']=e.target.value;this.commentDrafts.set(this.current,draft);return;}if(e.target.hasAttribute('data-share-nickname')){if(this.shareDraft)this.shareDraft.nickname=e.target.value;return;}if(e.target.id==='record-share-code'){this.codeDraft=e.target.value;return;}if(e.target.dataset.date)this.change(e);else if(e.target.hasAttribute('data-map-search'))this.searchMaps(e.target.value);});
      this.root.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.current){this.closeDetail();}if(e.key==='Tab'&&this.current){const controls=[...document.querySelectorAll('.record-detail button:not(:disabled),.record-detail input,.record-detail summary')];const first=controls[0],last=controls.at(-1);if(e.shiftKey&&document.activeElement===first){e.preventDefault();last?.focus();}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first?.focus();}}});
    }
    t(zh,en){return this.language().startsWith('zh')?zh:en;}
    result(value){return {active:this.t('进行中','In progress'),victory:this.t('胜利','Victory'),defeat:this.t('失败','Defeat'),interrupted:this.t('中断','Interrupted')}[value]||value;}
    n(value){return value===null||value===undefined?'—':Intl.NumberFormat(this.language().startsWith('zh')?'zh-CN':'en-US',{maximumFractionDigits:1}).format(value);}
    replayLabel(row){return row.replay?.reason==='compatible'?this.t('尝试兼容回放','Try compatible replay'):this.t('回放本场','Watch replay');}
    async show(){void this.loadIcons();this.visible=true;this.root.hidden=false;await this.refresh();}
    hide(){this.visible=false;this.root.hidden=true;clearTimeout(this.timer);}
    async refresh(){
      clearTimeout(this.timer);if(this.view!=='local'){await this.refreshShared();return;}
      try{const response=await fetch('/api/records',{cache:'no-store'});if(!response.ok)throw Error();const data=await response.json();if(!Array.isArray(data.records))throw Error();const signature=JSON.stringify(data.records);this.rows=data.records;if(signature!==this.lastRows||this.notice!==this.lastNotice||this.language()!==this.lastLanguage){this.render();this.lastRows=signature;this.lastNotice=this.notice;this.lastLanguage=this.language();}}
      catch(_){this.notice=this.t('无法读取战斗记录，请稍后重试。','Could not load battle records. Try again.');this.render();}
      if(this.visible)this.timer=setTimeout(()=>this.refresh(),5000);
    }
    remember(){try{localStorage.setItem('starbreak-record-filters',JSON.stringify(this.filters));}catch(_){}this.render();}
    checks(group,options){return options.map(([value,label])=>`<label class="record-check"><input type="checkbox" data-filter="${group}" value="${escape(value)}" ${this.filters[group]?.includes(String(value))?'checked':''}><span>${escape(label)}</span></label>`).join('');}
    render(force=false){
      if(!this.visible)return;
      if(this.view!=='local'){if(!force&&this.current&&document.querySelector('.record-detail'))return;this.renderShared();if(this.current)this.detail(this.current);return;}
      // Preserve focus/selection while live rows refresh.
      const keepFilters=!force&&(this.root.contains(document.activeElement)&&/INPUT|SELECT/.test(document.activeElement.tagName)||this.root.querySelector('.record-map-picker[open]'));
      if(!force&&this.current&&document.querySelector('.record-detail')){const notice=this.root.querySelector('.record-notice');if(notice)notice.textContent=this.notice;const row=this.rows.find(r=>r.id===this.current),button=this.root.querySelector('[data-replay]');if(row&&button){button.disabled=!row.replay?.available;button.textContent=row.replay?.available?this.replayLabel(row):this.t('此场暂无录像','No replay available');}return;}
      const rows=filtered(this.rows,this.filters),sum=totals(rows),selectable=rows.filter(r=>r.result!=='active'),all=selectable.length>0&&selectable.every(r=>this.selected.has(r.id));
      const maps=Array.from(new Map([...this.rows].reverse().map(r=>[r.map,r.map_title]))).sort((a,b)=>a[1].localeCompare(b[1]));
      const active=this.rows.filter(r=>r.result==='active').length;
      const html=`${this.viewTabs()}<div class="records-heading"><div><span class="records-eyebrow">STARBREAK / ${this.t('本地战绩','LOCAL HISTORY')}</span><h1>${this.t('战斗记录','Battle records')}</h1><p>${this.t('每一场战斗，都有迹可循。','Every encounter, at a glance.')}</p></div><div class="records-actions">${active?`<span class="record-live">● ${this.t('战斗正在进行','Battle in progress')}</span>`:''}<button class="secondary" data-action="import">${this.t('导入记录','Import')}</button><button class="secondary" data-action="share-code">${this.t('输入分享码','Enter sharing code')}</button><button class="secondary" data-action="export" ${this.selected.size?'':'disabled'}>${this.t('导出所选','Export selected')} · ${this.selected.size}</button><button class="secondary" data-action="refresh" aria-label="${this.t('刷新','Refresh')}">↻</button><input id="record-import" type="file" accept=".sbreplay,.zip" hidden></div></div>
      <div class="record-code-import" ${this.codeOpen?'':'hidden'}><label for="record-share-code">${this.t('录像分享码','Replay sharing code')}</label><input id="record-share-code" value="${escape(this.codeDraft)}" placeholder="ABCD-EFGH-JKMN" maxlength="32" autocomplete="off" spellcheck="false"><button class="secondary" data-action="redeem-code">${this.t('导入录像','Import replay')}</button><button class="secondary" data-action="share-code">${this.t('取消','Cancel')}</button></div>
      <p class="record-notice" role="status">${escape(this.notice)}</p>
      <div class="records-layout"><aside class="record-filters"><div class="record-filter-title"><strong>${this.t('筛选','Filters')}</strong><button data-action="reset">${this.t('重置','Reset')}</button></div>
      <fieldset><legend>${this.t('日期范围','Date range')}</legend><input type="date" data-date="from" aria-label="${this.t('开始日期','Start date')}" value="${escape(this.filters.from||'')}"><input type="date" data-date="to" aria-label="${this.t('结束日期','End date')}" value="${escape(this.filters.to||'')}"><div class="record-date-presets">${[['today',this.t('今天','Today')],['yesterday',this.t('昨天','Yesterday')],['7',this.t('近 7 天','7 days')],['30',this.t('近 30 天','30 days')],['all',this.t('全部','All time')]].map(([v,l])=>`<button data-days="${v}" class="${this.filters.preset===v?'selected':''}">${l}</button>`).join('')}</div></fieldset>
      <fieldset><legend>${this.t('角色','Class')}</legend>${this.checks('classes',Object.entries(classes))}</fieldset>
      <fieldset><legend>${this.t('战斗结果','Result')}</legend>${this.checks('results',['victory','defeat','interrupted','active'].map(r=>[r,this.result(r)]))}</fieldset>
      <fieldset><legend>${this.t('地图','Map')}</legend><details class="record-map-picker"><summary><span data-map-summary>${this.mapSummary()}</span><span aria-hidden="true">⌄</span></summary><div class="record-map-menu"><input type="search" data-map-search placeholder="${this.t('搜索地图名称或 ID','Search map name or ID')}" aria-label="${this.t('搜索地图','Search maps')}"><div class="record-map-tools"><span>${maps.length} ${this.t('张地图','maps')}</span><button data-action="clear-maps">${this.t('清空选择','Clear selection')}</button></div><div class="record-map-filters">${maps.length?this.checks('maps',maps):`<span class="record-muted">${this.t('完成战斗后显示','Available after a battle')}</span>`}</div><p class="record-map-empty" hidden>${this.t('没有找到地图','No maps found')}</p></div></details><div class="record-map-selection">${this.mapSelection(maps)}</div></fieldset>
      <fieldset><legend>${this.t('来源','Source')}</legend><select data-value="source"><option value="">${this.t('全部记录','All records')}</option><option value="local" ${this.filters.source==='local'?'selected':''}>${this.t('本机战斗','Local battles')}</option><option value="import" ${this.filters.source==='import'?'selected':''}>${this.t('导入记录','Imported')}</option></select></fieldset>
      ${this.filters.rules?`<div class="record-rules-filter">${this.t('仅显示相同配装与参数','Matching equipment & parameters')}<button data-action="clear-rules">×</button></div>`:''}</aside>
      <div class="records-content"><div class="record-summary">${[[this.t('总场次','BATTLES'),sum.count,this.t('当前筛选范围','Within these filters')],[this.t('胜率','WIN RATE'),sum.winrate===null?'—':this.n(sum.winrate)+'%',`${sum.wins} ${this.t('胜','W')} / ${sum.losses} ${this.t('负','L')}`],[this.t('平均用时','AVG. DURATION'),sum.average===null?'—':duration(sum.average),this.t('已结束的对局','Finished battles')],[this.t('总伤害','TOTAL DAMAGE'),this.n(sum.damage),this.t('仅汇总已记录的伤害','Recorded damage only')]].map(([l,v,n],i)=>`<div class="record-summary-card"><span>${l}</span><strong class="summary-${i}">${v}</strong><small>${n}</small></div>`).join('')}</div>
      <div class="record-list-panel"><div class="record-list-toolbar"><strong>${this.t('对局列表','Encounters')} <small>${rows.length}</small></strong><div><select data-value="sort" aria-label="${this.t('排序','Sort')}">${[['started',this.t('开始时间','Start time')],['duration',this.t('战斗用时','Duration')],['damage',this.t('造成伤害','Damage')]].map(([v,l])=>`<option value="${v}" ${(this.filters.sort||'started')===v?'selected':''}>${l}</option>`).join('')}</select><button data-action="direction" class="secondary" aria-label="${this.t('反转排序','Reverse sort')}">${this.filters.direction===1?'↑':'↓'}</button></div></div>
      <div class="record-table-scroll"><table class="record-table"><thead><tr><th><input type="checkbox" data-select-all ${all?'checked':''} aria-label="${this.t('全选筛选结果','Select filtered records')}"></th>${[this.t('结果 / 角色','RESULT / CLASS'),this.t('地图 / 时间','MAP / DATE'),this.t('装备','EQUIPMENT'),this.t('用时','TIME'),this.t('伤害 / 击杀','DAMAGE / KILLS'),this.t('血包 / 弹药','HP / AMMO'),''].map(h=>`<th>${h}</th>`).join('')}</tr></thead><tbody>${rows.map(r=>this.row(r)).join('')}</tbody></table></div>
      ${rows.length?'':`<div class="record-empty"><span>◇</span><h2>${this.rows.length?this.t('没有符合条件的战斗','No matching battles'):this.t('你的下一场战斗，从这里开始','Your next battle starts the story')}</h2><p>${this.rows.length?this.t('调整日期或筛选条件试试。','Try another date range or filter.'):this.t('开始战斗后会自动记录，结束或重开后可在这里查看。','Battles are recorded automatically. Return here after finishing or restarting.')}</p></div>`}</div>
      <p class="record-footnote">${this.t('胜率不包含中断和进行中的对局。— 表示此版本尚未记录该项数据。','Win rate excludes interrupted and active battles. — means this version did not record that metric.')}</p></div></div><div id="record-detail"></div>`;
      if(keepFilters&&this.root.querySelector('.record-filters')){
        const template=document.createElement('template');template.innerHTML=html;
        for(const selector of ['.records-heading','.record-notice','.records-content'])this.root.querySelector(selector).replaceWith(template.content.querySelector(selector));
        this.root.querySelectorAll('[data-days]').forEach(button=>button.classList.toggle('selected',button.dataset.days===this.filters.preset));
        this.root.querySelectorAll('[data-date]').forEach(input=>{if(input!==document.activeElement)input.value=this.filters[input.dataset.date]||'';});
        this.root.querySelector('[data-map-summary]').textContent=this.mapSummary();
        this.root.querySelector('.record-map-selection').innerHTML=this.mapSelection(maps);
      }else this.root.innerHTML=html;
      if(this.current)this.detail(this.current);
    }
    async loadIcons(){
      if(this.icons||this.iconsLoading)return;
      this.iconsLoading=true;
      try{const response=await fetch('/api/item-icons');if(!response.ok)return;this.icons=await response.json();if(this.visible){if(this.current)this.detail(this.current);else this.render(true);}}
      catch(_){}finally{this.iconsLoading=false;}
    }
    portrait(shell){return `<span class="class-emblem shell-${shell}">${this.icons?.['portrait-'+shell]?this.itemIcon('portrait-'+shell):abbreviations[shell]||''}</span>`;}
    itemIcon(type){
      const icon=this.icons?.[type];if(!icon)return '<span class="record-item-icon record-icon-placeholder" aria-hidden="true">◇</span>';
      return `<svg class="record-item-icon" viewBox="${icon.viewbox.join(' ')}" aria-hidden="true">${icon.parts.map(p=>`<g transform="translate(${p.ox} ${p.oy}) rotate(${p.angle}) scale(${p.sx} ${p.sy})" opacity="${p.opacity}"><svg x="${-p.px}" y="${-p.py}" width="${p.w}" height="${p.h}" viewBox="${p.x} ${p.y} ${p.w} ${p.h}"><image href="${escape(globalThis.StarBreakBrowser?.assetURL(p.sheet)||p.sheet)}" width="${p.sheet_width}" height="${p.sheet_height}"/></svg></g>`).join('')}</svg>`;
    }
    loadoutHTML(r,statName){
      const slots={256:this.t('主武器','Main weapon'),512:this.t('副武器','Secondary weapon'),768:this.t('特殊武器','Special weapon'),1024:this.t('衣服','Armor'),1280:this.t('机动 / 鞋','Mobility'),1536:this.t('植入体','Implant')};
      return equippedLoadout(r).map(item=>`<details class="record-equipped" data-record="${r.id}" data-slot="${item.slotpos_}" ${this.equipmentOpen?.has(item.slotpos_)?'open':''}><summary>${this.itemIcon(item.type_)}<span><strong>${escape(this.itemName(item.type_,r))}</strong><small>${slots[item.slotpos_]||''}</small></span></summary><div class="record-equipped-stats"><small>ID ${item.type_}</small>${(r.parameters.items?.find(i=>i.type===item.type_)?.base||[]).map(b=>`<div><span>${escape(statName(b.stat_))}</span><b>${b.val_}</b></div>`).join('')}${item.statboosts_.map(b=>`<div><span>${escape(statName(b.stat_))}</span><b>${b.val_}</b></div>`).join('')}</div></details>`).join('');
    }
    mapSummary(){const n=this.filters.maps.length;return n?this.t(`已选 ${n} 张地图`,`${n} maps selected`):this.t('全部地图','All maps');}
    mapSelection(maps){return this.filters.maps.slice(0,3).map(id=>`<button data-remove-map="${escape(id)}" title="${escape(new Map(maps).get(id)||id)}">${escape(new Map(maps).get(id)||id)} <span>×</span></button>`).join('')+(this.filters.maps.length>3?`<span>+${this.filters.maps.length-3}</span>`:'');}
    searchMaps(query){const term=query.trim().toLowerCase();let found=0;this.root.querySelectorAll('.record-map-filters label').forEach(label=>{label.hidden=!`${label.textContent} ${label.querySelector('input').value}`.toLowerCase().includes(term);if(!label.hidden)found++;});this.root.querySelector('.record-map-empty').hidden=found>0;}
    itemName(id,record){const frozen=record?.parameters.items?.find(i=>Number(i.type)===Number(id));if(frozen)return frozen.name;const items=this.catalogue()||[];return items.find(i=>Number(i.type??i.id)===Number(id))?.name||`#${id}`;}
    row(r){const date=new Date(r.started*1000);const gear=r.parameters.loadout.items_.slice(0,3);return `<tr class="record-row result-${r.result}"><td><input type="checkbox" data-select="${r.id}" ${r.result==='active'?'disabled':''} ${this.selected.has(r.id)?'checked':''} aria-label="${escape(this.t('选择','Select')+' '+r.map_title)}"></td><td><div class="record-class">${this.portrait(r.shell)}<span><b>${classes[r.shell]}</b>${r.source==='import'?`<small class="record-shared-by">${escape(r.shared_by||'Unknown')}</small>`:''}<small class="result-label">${this.result(r.result)} <span>· MK${r.parameters.loadout.mk_}</span></small></span></div></td><td><button class="record-link" data-open="${r.id}">${escape(r.map_title)}</button>${turretSummary(r)?`<small class="record-turrets" title="${this.t('前(散射)-上(追踪弹)-下(追踪弹)','Front (scatter) – Upper (missiles) – Lower (missiles)')}">${turretSummary(r)}</small>`:''}<small class="record-date">${date.toLocaleString(this.language().startsWith('zh')?'zh-CN':'en-US',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',year:'numeric'})}</small></td><td><div class="record-equipment">${gear.map(i=>`<span title="${escape(this.itemName(i.type_,r))}">${escape(this.itemName(i.type_,r))}</span>`).join('')}</div></td><td class="record-number">${battleDuration(r)}</td><td class="record-number">${this.n(r.metrics.damage_dealt)}<small>${this.n(r.metrics.kills)} ${this.t('击杀','kills')}</small>${graveyard(r)?`<small class="record-gens">${this.n(r.metrics.generators_destroyed)} Gen</small>`:''}</td><td class="record-number">${this.n(r.metrics.health_packs)} <span class="record-muted">/</span> ${this.n(r.metrics.ammo_packs)}</td><td><button class="secondary" data-open="${r.id}">${this.t('详情','Details')} ↗</button></td></tr>`;}
    detail(id){
      void this.loadIcons();
      const r=this.rows.find(r=>r.id===id)||this.sharedDetails.get(id);if(!r)return;
      this.equipmentOpen=this.expandedEquipment.get(id)||new Set();
      this.current=id;
      if(r.shared_code&&!this.shareResults[id])this.shareResults[id]={code:r.shared_code,nickname:r.shared_by,original:true};
      const labels={enemy_hp:this.t('敌人血量','Enemy HP'),enemy_damage:this.t('敌人伤害','Enemy damage'),enemy_speed:this.t('敌人速度','Enemy speed'),spawn_rate:this.t('刷怪速率','Spawn rate'),spawn_cap:this.t('刷怪上限','Spawn cap'),health_percent:this.t('血包掉率','Health drop %'),ammo_percent:this.t('弹药掉率','Ammo drop %'),ec_percent:this.t('EC 掉率','EC drop %'),front:this.t('前方','Front'),upper:this.t('上方','Upper'),lower:this.t('下方','Lower')};
      const parameterHTML=p=>`<div class="record-parameter-groups">${[['balance',this.t('战斗参数','Battle parameters')],['drops',this.t('掉落','Drops')],['turrets',this.t('炮塔数量','Turret counts')],['missiles',this.t('追踪炮塔','Seeking turrets')]].map(([k,title])=>`<section><h3>${title}</h3><dl>${Object.entries(p[k]).map(([key,v])=>`<div><dt>${labels[key]||escape(key)}</dt><dd>${v===null?this.t('随机','Random'):escape(v)}</dd></div>`).join('')}</dl></section>`).join('')}<section><h3>${this.t('开局与追踪','Opening & aiming')}</h3><dl><div><dt>${this.t('跳过开局','Skip opening')}</dt><dd>${p.skip_intro?this.t('开','On'):this.t('关','Off')}</dd></div><div><dt>${this.t('追踪弹延迟','Homing delay')}</dt><dd>${p.missile_delay_ms} ms</dd></div></dl></section><section class="record-speed-parameters"><details><summary>${this.t('本场弹体基础速度','Battle projectile base speeds')}</summary><dl>${Object.entries(p.projectile_speeds).map(([id,values])=>Object.entries(values).map(([index,v])=>`<div><dt>${escape(this.itemName(id,r))} #${index}</dt><dd>${v}</dd></div>`).join('')).join('')||`<p class="record-muted">${this.t('此记录未保存弹速','No speeds in this record')}</p>`}</dl></details></section></div>`;
      const statName=id=>window.STARBREAK_RECORD_STAT_LABELS?.[id]?.[this.language()==='zh-affixes'?'zh':'en']||`Stat ${id}`;
      document.getElementById('record-detail').innerHTML=`<div class="record-detail-backdrop ${this.detailFullscreen?'record-detail-fullscreen':''}"><section class="record-detail" role="dialog" aria-modal="true" aria-label="${this.t('战斗详情','Battle details')}"><div class="record-detail-header">${this.portrait(r.shell)}<div><span class="record-eyebrow">${this.result(r.result)} · ${battleDuration(r)} · MK${r.parameters.loadout.mk_}</span><h2>${escape(r.map_title)}</h2><p>${classes[r.shell]} · ${new Date(r.started*1000).toLocaleString()} · ${escape(r.version)}${r.source==='import'?` · ${escape(r.shared_by||'Unknown')}`:''}</p></div><button class="secondary record-detail-size" data-action="detail-size" aria-label="${this.t('切换全屏详情','Toggle full-screen details')}">⤢</button><button class="secondary" data-action="close-detail" aria-label="${this.t('关闭详情','Close details')}">×</button></div><div class="record-detail-actions">${r.share_code?`<button class="secondary" data-redeem="${escape(r.share_code)}">${this.t('导入回放','Import replay')}</button><span class="record-muted">${escape(r.shared_by||'Unknown')} · ${escape(r.share_code)}</span>`:''}<button class="secondary" data-replay="${r.id}" ${r.replay?.available&&!r.share_code?'':'disabled'}>${r.replay?.available?this.replayLabel(r):this.t('此场暂无录像','No replay available')}</button><button class="secondary" data-share="${r.id}" ${r.replay?.available&&!r.share_code?'':'disabled'}>${r.source==='import'?this.t('原分享码','Original code'):this.t('生成分享码','Create sharing code')}</button><button class="secondary" data-export="${r.id}" ${r.result==='active'||r.share_code?'disabled':''}>${this.t('导出本场','Export battle')}</button><button class="secondary" data-rules="${r.rules_hash}" ${r.share_code?'disabled':''}>${this.t('筛选相同配装与参数','Find matching setup')}</button></div>${r.replay?.reason==='compatible'?`<p class="record-muted">${this.t('录像来自其他适配版本，将尝试兼容播放；状态偏差时会停止，暂不提供接管。','This recording uses another replay adapter. Playback will stop if game state diverges; takeover is unavailable.')}</p>`:''}${r.replay?.available?`<p class="record-muted">${this.t('生成分享码会将本场录像和配装参数发布到公开分享列表，其他记录保留在本机。','Creating a sharing code publishes this replay and its setup in the sharing lists. Other records stay on this device.')}</p>`:''}${this.shareDraft?.id===r.id?this.shareForm():''}${this.shareError?`<p class="record-share-result" role="status">${escape(this.shareError)}</p>`:''}${this.shareResults[r.id]?`<div class="record-share-result"><label>${this.t('录像分享码','Replay sharing code')}</label><input data-share-result value="${escape(this.shareResults[r.id].code)}" readonly aria-label="${this.t('录像分享码','Replay sharing code')}"><button class="secondary" data-copy-share>${this.t('复制分享码','Copy code')}</button><small>${escape(this.shareResults[r.id].nickname||'Unknown')} · ${this.shareResults[r.id].size?(this.shareResults[r.id].size/1e6).toFixed(2)+' MB':''}</small></div>`:''}${r.parent?`<p class="record-muted">${this.t('接管起点','Takeover point')} · ${duration(r.parent.at)}${this.rows.some(parent=>parent.id===r.parent.id)?` <button class="secondary" data-open="${r.parent.id}">${this.t('查看原对局','Original battle')}</button>`:''}</p>`:''}<div class="record-detail-metrics"><div class="record-duration"><span>${this.t('战斗耗时','Battle duration')}</span><strong>${battleDuration(r)}</strong></div>${Object.entries(metricLabels).filter(([k])=>k!=='generators_destroyed'||graveyard(r)).map(([k,n])=>`<div><span>${this.t(...n)}</span><strong>${this.n(r.metrics[k])}</strong></div>`).join('')}</div><div class="record-loadout-heading"><h3>${this.t('本场装备','Battle loadout')}</h3><div><button class="secondary" data-action="expand-equipment">${this.t('全部展开','Expand all')}</button><button class="secondary" data-action="collapse-equipment">${this.t('全部收起','Collapse all')}</button></div></div><div class="record-loadout">${this.loadoutHTML(r,statName)}</div><h3>${this.t('开局参数快照','Starting parameters')}</h3>${parameterHTML(r.parameters)}<details class="record-extra"><summary>${this.t('更多战斗信息','More battle information')}</summary>${r.turret_layout?.length?`<h3>${this.t('本场实际炮塔','Rolled turret layout')}</h3><div class="record-equipment">${['front','upper','lower'].map(side=>`<span>${labels[side]} · ${r.turret_layout.filter(t=>t.row===side).length} / ${r.turret_layout.filter(t=>t.row===side&&t.ammo==='bossmissile').length} ${this.t('追踪','seeking')}</span>`).join('')}</div>`:''}<div class="record-seed">${this.t('本场随机种子','Battle seed')} <code>${r.seed}</code> · ${this.t('房间','Room')} <code>${r.room_seed}</code></div>${r.rooms?.length?`<h3>${this.t('房间历程','Visited rooms')}</h3><div class="record-room-timeline">${r.rooms.map(room=>`<div><time>${duration(room.entered)}</time><span>${escape(room.role)}</span></div>`).join('')}</div>`:''}<h3>${this.t('对局内参数变更','Configuration changes')}</h3>${r.changes.length?r.changes.map(c=>`<details><summary>${duration(c.at)} · ${this.t('生效参数','Effective parameters')}</summary>${parameterHTML(c.parameters)}</details>`).join(''):`<p class="record-muted">${this.t('本场没有记录到参数变更。','No configuration changes recorded.')}</p>`}<p class="record-footnote">${r.share_code?this.t('导入后可在本地回放或接管；查看详情不会增加导入次数。','Import to replay or take over locally. Viewing details does not count as an import.'):r.replay?.available?this.t('导出文件包含本场录像、配装和参数；接管后会另存一场战斗。','Exports include the replay, equipment and parameters. Taking over saves a separate battle.'):this.t('此记录包含战绩和参数快照，未保存兼容的游戏录像。','This record contains statistics and configuration snapshots, without a compatible game replay.')}</p></details>${this.commentsHTML(r)}</section></div>`;
    }
    commentCode(r){return r.share_code||r.shared_code||this.shareResults[r.id]?.code||'';}
    commentsHTML(r){
      const code=this.commentCode(r);if(!code)return '';
      const thread=this.comments.get(code);
      if(!thread){this.comments.set(code,{rows:[],loading:true});queueMicrotask(()=>this.loadComments(r));}
      let draft=this.commentDrafts.get(r.id);
      if(!draft){let name='';try{name=nickname(localStorage.getItem('starbreak-comment-id')||localStorage.getItem('starbreak-share-nickname')||'');}catch(_){}draft={nickname:name,text:''};this.commentDrafts.set(r.id,draft);}
      const value=thread||{rows:[],loading:true};
      return `<section class="record-comments"><div class="record-loadout-heading"><h3>${this.t('录像评论','Replay comments')}</h3><button class="secondary" data-action="refresh-comments" ${value.loading?'disabled':''}>${this.t('刷新','Refresh')}</button></div><p class="record-muted">${this.t('此分享码下的评论公开可见；ID 会在本机记住。','Comments are public for this sharing code. Your ID is remembered on this device.')}</p>${value.more?`<button class="secondary" data-action="older-comments">${this.t('加载更早的评论','Load older comments')}</button>`:''}<div class="record-comment-list">${value.rows.map(c=>`<article><div><b>${escape(c.nickname)}</b><time>${escape(new Date(c.created*1000).toLocaleString(this.language().startsWith('zh')?'zh-CN':'en-US'))}</time></div><p>${escape(c.text)}</p></article>`).join('')}</div><p role="status">${escape(value.error||'')}${value.loading?this.t('正在读取评论…','Loading comments…'):!value.rows.length?this.t('还没有评论。','No comments yet.'):''}</p><label>${this.t('你的 ID（英文，必填）','Your ID (English, required)')}<input data-comment-id maxlength="32" value="${escape(draft.nickname)}" autocomplete="nickname"></label><label>${this.t('评论','Comment')}<textarea data-comment-text maxlength="1000" rows="3">${escape(draft.text)}</textarea></label><button class="secondary" data-action="post-comment" ${value.posting?'disabled':''}>${value.posting?this.t('正在发送…','Sending…'):this.t('发表评论','Post comment')}</button></section>`;
    }
    async loadComments(row,older=false){
      const code=this.commentCode(row);if(!code)return;
      const previous=this.comments.get(code)||{rows:[]};previous.loading=true;previous.error='';this.comments.set(code,previous);
      try{
        const before=older&&previous.rows.length?'&before='+previous.rows[0].id:'';
        const response=await fetch('/api/records/comments?code='+encodeURIComponent(code)+before,{cache:'no-store'});if(!response.ok)throw Error();
        const data=await response.json();if(!Array.isArray(data.comments))throw Error();
        this.comments.set(code,{rows:older?[...data.comments,...previous.rows]:data.comments,more:data.more,loading:false});
      }catch(_){previous.loading=false;previous.error=this.t('暂时无法读取评论，请重试。','Could not load comments. Please retry.');}
      if(this.current===row.id)this.detail(row.id);
    }
    async postComment(row){
      const code=this.commentCode(row),draft=this.commentDrafts.get(row.id)||{},thread=this.comments.get(code)||{rows:[]};
      if(thread.posting)return;
      let author;try{author=nickname(draft.nickname);if(!author||!draft.text?.trim())throw Error();}catch(error){thread.error=error.message==='political-nickname'?this.t('此 ID 不可使用，请换一个英文 ID。','This ID is not allowed. Choose another English ID.'):this.t('请填写有效的英文 ID 和评论。','Enter a valid English ID and a comment.');this.comments.set(code,thread);this.detail(row.id);return;}
      thread.posting=true;thread.error='';this.comments.set(code,thread);this.detail(row.id);
      try{
        const response=await fetch('/api/records/comments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code,nickname:author,text:draft.text})});const data=await response.json();if(!response.ok)throw Error(data.error||'');if(!data.comment)throw Error();
        try{localStorage.setItem('starbreak-comment-id',author);}catch(_){}
        thread.rows.push(data.comment);draft.text='';this.commentDrafts.set(row.id,draft);
      }catch(error){thread.error=error.message==='political-content'?this.t('评论包含政治相关内容，请修改后再发送。','Political content is not allowed. Please edit your comment.'):error.message==='political-nickname'?this.t('此 ID 不可使用，请换一个英文 ID。','This ID is not allowed. Choose another English ID.'):this.t('评论发送失败，请稍后重试。','Could not post comment. Please try again later.');}
      thread.posting=false;if(this.current===row.id)this.detail(row.id);
    }
    closeDetail(){const id=this.current;this.current=null;document.getElementById('record-detail').replaceChildren();this.render();this.root.querySelector(`[data-open="${id}"]`)?.focus();}
    setEquipmentExpanded(expanded){
      const record=this.rows.find(r=>r.id===this.current)||this.sharedDetails.get(this.current);
      if(!record)return;
      this.expandedEquipment.set(this.current,new Set(expanded?equippedLoadout(record).map(i=>i.slotpos_):[]));
      this.root.querySelectorAll('.record-equipped').forEach(item=>{if(item.dataset.record===this.current)item.open=expanded;});
    }
    async click(event){
      if(event.target.matches?.('.record-detail-backdrop')){this.closeDetail();return;}
      const button=event.target.closest('button');if(!button)return;
      if(['refresh-comments','older-comments','post-comment'].includes(button.dataset.action)){
        const row=this.rows.find(r=>r.id===this.current)||this.sharedDetails.get(this.current);if(!row)return;
        if(button.dataset.action==='post-comment')await this.postComment(row);else await this.loadComments(row,button.dataset.action==='older-comments');return;
      }
      if(button.dataset.removeMap){this.filters.maps=this.filters.maps.filter(id=>id!==button.dataset.removeMap);this.root.querySelectorAll('[data-filter="maps"]').forEach(input=>input.checked=this.filters.maps.includes(input.value));this.remember();return;}
      if(button.dataset.sharedDetail){await this.openShared(button,button.dataset.sharedDetail);return;}
      if(button.dataset.open){this.detail(button.dataset.open);document.querySelector('[data-action=close-detail]')?.focus();return;}
      if(button.dataset.view){this.view=button.dataset.view;this.current=null;await this.refresh();this.render();return;}
      if(button.dataset.redeem){await this.redeemCode(button,button.dataset.redeem);return;}
      if(button.dataset.share){const row=this.rows.find(r=>r.id===button.dataset.share);if(row?.source==='import'){await this.originalCode(row,button);return;}if(this.shareResults[button.dataset.share]){this.detail(button.dataset.share);return;}let previous='';try{previous=nickname(localStorage.getItem('starbreak-share-nickname'));}catch(_){}this.shareDraft={id:button.dataset.share,nickname:previous};this.shareError='';this.detail(button.dataset.share);this.root.querySelector('[data-share-nickname]')?.focus();return;}
      if(button.hasAttribute('data-copy-share')){const input=this.root.querySelector('[data-share-result]');try{await navigator.clipboard.writeText(input.value);button.textContent=this.t('已复制','Copied');}catch(_){input.focus();input.select();button.textContent=this.t('已选中，请复制','Selected; copy now');}return;}
      if(button.dataset.export){await this.export([button.dataset.export]);return;}
      if(button.dataset.replay){const row=this.rows.find(r=>r.id===button.dataset.replay);if(row?.replay?.available){this.current=null;document.getElementById('record-detail').replaceChildren();this.play(row);}return;}
      if(button.dataset.rules){this.filters.rules=button.dataset.rules;this.current=null;this.remember();return;}
      if(button.dataset.days){const value=button.dataset.days;let now=new Date();this.filters.preset=value;if(value==='all'){this.filters.from=this.filters.to='';}else{if(value==='yesterday')now.setDate(now.getDate()-1);this.filters.to=day(now);if(/^\d+$/.test(value))now.setDate(now.getDate()-Number(value)+1);this.filters.from=day(now);}this.remember();return;}
      switch(button.dataset.action){
        case 'submit-share':await this.share(this.shareDraft.id,button);break;
        case 'cancel-share':this.shareDraft=null;this.shareError='';this.detail(this.current);break;
        case 'expand-equipment':this.setEquipmentExpanded(true);break;
        case 'collapse-equipment':this.setEquipmentExpanded(false);break;
        case 'close-detail':this.closeDetail();break;
        case 'detail-size':this.detailFullscreen=!this.detailFullscreen;this.detail(this.current);break;
        case 'reset':this.filters={maps:[],classes:[],results:[]};this.root.querySelector('.record-map-picker').open=false;this.remember();break;
        case 'clear-maps':this.filters.maps=[];this.root.querySelectorAll('[data-filter=maps]').forEach(input=>input.checked=false);this.remember();break;
        case 'clear-rules':this.filters.rules='';this.remember();break;
        case 'direction':this.filters.direction=this.filters.direction===1?0:1;this.remember();break;
        case 'refresh':await this.refresh();break;
        case 'clear-shared-filters':this.sharedFilters={};this.rememberSharedFilters();await this.refreshShared();break;
        case 'import':document.getElementById('record-import').click();break;
        case 'share-code':this.codeOpen=!this.codeOpen;this.render(true);if(this.codeOpen)this.root.querySelector('#record-share-code').focus();break;
        case 'redeem-code':await this.redeemCode(button);break;
        case 'export':await this.export([...this.selected]);break;
      }
    }
    async change(event){
      const e=event.target;
      if(e.dataset.sharedFilter){clearTimeout(this.sharedFilterTimer);this.sharedFilters[e.dataset.sharedFilter]=e.value;if(!['egy_hulk','hulk_normal','egy_run'].includes(this.sharedFilters.map))for(const key of ['front','upper','lower'])delete this.sharedFilters[key];this.rememberSharedFilters();await this.refreshShared();return;}
      if(e.hasAttribute('data-share-nickname'))return;
      if(e.id==='record-share-code'||e.hasAttribute('data-share-result'))return;
      if(e.id==='record-import'&&e.files[0]){await this.import(e.files[0]);return;}
      if(e.dataset.filter){const list=new Set(this.filters[e.dataset.filter]);e.checked?list.add(e.value):list.delete(e.value);this.filters[e.dataset.filter]=[...list];}
      else if(e.dataset.date){this.filters[e.dataset.date]=e.value;this.filters.preset='';}
      else if(e.dataset.value)this.filters[e.dataset.value]=e.value;
      else if(e.dataset.select)e.checked?this.selected.add(e.dataset.select):this.selected.delete(e.dataset.select);
      else if(e.hasAttribute('data-select-all'))filtered(this.rows,this.filters).filter(r=>r.result!=='active').forEach(r=>e.checked?this.selected.add(r.id):this.selected.delete(r.id));
      this.remember();
    }
    viewTabs(){return `<nav class="record-view-tabs" aria-label="${this.t('记录与分享','Records and sharing')}">${[['local',this.t('本地战绩','Local history')],['latest',this.t('最新分享','Latest shares')],['popular',this.t('近 7 天热门','Popular this week')]].map(([value,label])=>`<button class="secondary ${this.view===value?'selected':''}" data-view="${value}" aria-pressed="${this.view===value}">${label}</button>`).join('')}</nav>`;}
    shareForm(){return `<div class="record-share-compose"><label for="record-share-nickname">${this.t('ID / 昵称（选填，英文）','ID / nickname (optional, English)')}</label><input id="record-share-nickname" data-share-nickname maxlength="32" autocomplete="off" value="${escape(this.shareDraft.nickname)}" placeholder="${this.t('留空显示 Unknown','Leave empty for Unknown')}"><div><button class="secondary" data-action="submit-share">${this.t('生成并分享','Create sharing code')}</button><button class="secondary" data-action="cancel-share">${this.t('取消','Cancel')}</button></div><small>${this.t('该昵称会随录像公开；下次自动填入。','This nickname will be public with the replay and remembered for next time.')}</small></div>`;}
    rememberSharedFilters(){try{localStorage.setItem('starbreak-shared-filters',JSON.stringify(this.sharedFilters));}catch(_){}}
    sharedFilterHTML(){
      const filters=this.sharedFilters,maps=this.maps()||[];
      const choices=maps.length?maps:Array.from(new Map(this.sharedRows.map(r=>[r.map,{id:r.map,title:r.map_title}])).values());
      const label=row=>row.names?.[this.language().startsWith('zh')?'zh':'en']||row.title||row.id;
      return `<div class="record-share-filters"><label>${this.t('地图','Map')}<select data-shared-filter="map"><option value="">${this.t('全部地图','All maps')}</option>${choices.map(row=>`<option value="${escape(row.id)}" ${filters.map===row.id?'selected':''}>${escape(label(row))}</option>`).join('')}</select></label><label>${this.t('职业','Class')}<select data-shared-filter="shell"><option value="">${this.t('全部职业','All classes')}</option>${Object.entries(classes).map(([id,name])=>`<option value="${id}" ${filters.shell===id?'selected':''}>${name}</option>`).join('')}</select></label>${['egy_hulk','hulk_normal','egy_run'].includes(filters.map)?['front','upper','lower'].map((side,index)=>`<label>${this.t(['前方炮台','上方炮台','下方炮台'][index],['Front turrets','Upper turrets','Lower turrets'][index])}<input type="number" min="0" max="100" step="1" data-shared-filter="${side}" value="${escape(filters[side]||'')}" placeholder="${this.t('不限','Any')}"></label>`).join(''):''}<button class="secondary" data-action="clear-shared-filters">${this.t('重置筛选','Reset filters')}</button><span role="status">${this.sharedBusy?this.t('正在筛选…','Filtering…'):this.t('按筛选条件展示最多 50 场','Up to 50 matching replays')}</span></div>`;
    }
    async refreshShared(){
      const request=++this.sharedRequest,view=this.view;
      this.sharedBusy=true;this.sharedNotice='';this.render();
      try{const query=new URLSearchParams({sort:view,...this.sharedFilters});const response=await fetch('/api/records/shared?'+query);if(!response.ok)throw Error();const result=await response.json();if(request===this.sharedRequest&&this.view===view)this.sharedRows=result.records||[];}
      catch(_){if(request===this.sharedRequest)this.sharedNotice=this.t('暂时无法读取分享榜，请稍后刷新。','Could not load shared replays. Try refreshing later.');}
      finally{if(request===this.sharedRequest){this.sharedBusy=false;this.render();}}
    }
    renderShared(){
      this.root.innerHTML=`${this.viewTabs()}<div class="records-heading"><div><span class="records-eyebrow">STARBREAK / REPLAYS</span><h1>${this.view==='popular'?this.t('近 7 天热门','Popular this week'):this.t('最新分享','Latest shares')}</h1><p>${this.t('热度按近 7 天成功导入数统计；重复导入同一录像不反复计数。','Ranked by successful imports over the last seven days; repeat imports of the same replay are not counted again.')}</p></div><button class="secondary" data-action="refresh">${this.t('刷新','Refresh')}</button></div>${this.sharedFilterHTML()}${this.sharedNotice?`<p class="record-notice">${escape(this.sharedNotice)}</p>`:''}<div class="record-list-panel"><div class="record-table-scroll"><table class="record-table"><thead><tr>${[this.t('分享者','SHARED BY'),this.t('地图 / 角色','MAP / CLASS'),this.t('用时 / 结果','TIME / RESULT'),this.t('分享时间','SHARED'),this.t('7 天导入','7-DAY IMPORTS'),''].map(label=>`<th>${label}</th>`).join('')}</tr></thead><tbody>${this.sharedRows.map(row=>`<tr><td>${escape(row.nickname||'Unknown')}</td><td>${escape(row.map_title||row.map||'Unknown')}${turretSummary(row)?`<span class="record-turrets">${turretSummary(row)}</span>`:''}<small class="record-date">${classes[row.shell]||'Unknown'}</small></td><td>${battleDuration(row)}<small class="record-date">${this.result(row.result)}</small></td><td>${new Date(row.shared_at*1000).toLocaleString()}</td><td>${this.n(row.imports_7d)}</td><td><button class="secondary" data-shared-detail="${escape(row.code)}">${this.t('详情','Details')}</button> <button class="secondary" data-redeem="${escape(row.code)}">${this.t('导入回放','Import replay')}</button></td></tr>`).join('')}</tbody></table></div>${!this.sharedRows.length?`<p class="record-empty">${this.sharedBusy?this.t('正在读取分享…','Loading shares…'):Object.values(this.sharedFilters).some(Boolean)?this.t('没有符合筛选条件的录像。','No replays match these filters.'):this.t('还没有分享的录像。','No shared replays yet.')}</p>`:''}</div><div id="record-detail"></div>`;
    }
    async originalCode(row,button){
      if(row.shared_code){this.shareResults[row.id]={code:row.shared_code,nickname:row.shared_by,original:true};this.detail(row.id);return;}
      button.disabled=true;
      try{const response=await fetch('/api/records/origin?id='+encodeURIComponent(row.id));const value=await response.json();if(!response.ok)throw Error();row.shared_code=value.code;this.shareResults[row.id]=value;this.detail(row.id);}
      catch(_){this.shareError=this.t('未找到原分享码；导入的录像不能重新上传。','Original sharing code unavailable. Imported replays cannot be reuploaded.');this.detail(row.id);}
      finally{button.disabled=false;}
    }
    async share(identifier,button){
      if(this.shareResults[identifier]){this.detail(identifier);return;}const row=this.rows.find(r=>r.id===identifier);if(row?.source==='import'){await this.originalCode(row,button);return;}
      let author;try{author=nickname(this.shareDraft?.nickname);localStorage.setItem('starbreak-share-nickname',author);}catch(_){this.shareError=this.t('昵称限 32 个英文字母、数字、空格或 . _ -，不能用 null、unknown 等占位名或政治相关名称。','Use up to 32 English letters, numbers, spaces or . _ -. Placeholder and political names are not allowed.');this.detail(identifier);return;}
      if(this.shareBusy)return;this.shareBusy=true;button.disabled=true;button.textContent=this.t('正在上传录像…','Uploading replay…');
      this.shareError='';const transferID=crypto.randomUUID(),started=Date.now();let last={stage:'preparing'},polling=false;
      const update=event=>{if(event)last=event.detail;const {stage,loaded=0,total=0}=last;
        const names={preparing:this.t('正在整理录像','Preparing replay'),upload:this.t('正在上传','Uploading'),processing:this.t('服务器正在保存','Server is saving')};
        button.textContent=(names[stage]||names.preparing)+(stage==='upload'?` · ${(loaded/1048576).toFixed(1)} / ${(total/1048576).toFixed(1)} MB · ${total?Math.floor(loaded/total*100):0}%`:'')+` · ${Math.floor((Date.now()-started)/1000)}s`;
      };update();addEventListener('starbreak-transfer',update);const timer=setInterval(async()=>{update();if(!globalThis.StarBreakBrowser&&!polling){polling=true;try{const r=await fetch('/api/records/transfer?id='+transferID);if(r.ok)update({detail:await r.json()});}catch(_){}finally{polling=false;}}},700);
      try{const response=await fetch('/api/records/share',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:identifier,nickname:author,transfer_id:transferID})});const result=await response.json();if(!response.ok)throw Error();this.shareResults[identifier]={...result,nickname:result.nickname??author};this.shareDraft=null;if(this.current===identifier)this.detail(identifier);}
      catch(_){this.shareError=this.t('生成分享码失败，请稍后重试；本地录像保留，仍可导出文件。','Could not create sharing code. Try again later; your local replay remains available for export.');if(this.current===identifier)this.detail(identifier);}
      finally{clearInterval(timer);removeEventListener('starbreak-transfer',update);this.shareBusy=false;button.disabled=false;button.textContent=this.t('生成分享码','Create sharing code');}
    }
    async openShared(button,code){
      button.disabled=true;button.textContent=this.t('正在读取…','Loading…');
      try{const response=await fetch('/api/records/shared-detail?code='+encodeURIComponent(code));const value=await response.json();if(!response.ok)throw Error();const record={...value.record,share_code:code};this.sharedDetails.set(record.id,record);this.detail(record.id);}
      catch(_){this.sharedNotice=this.t('读取详情失败，请稍后重试。','Could not load details. Try again.');this.render();}
      finally{button.disabled=false;button.textContent=this.t('详情','Details');}
    }
    async redeemCode(button,provided){
      const value=(provided||this.root.querySelector('#record-share-code')?.value||'').trim();if(!value||this.redeeming)return;this.redeeming=true;
      button.disabled=true;const transferID=crypto.randomUUID();let polling=false;const started=Date.now();let last={stage:'connecting'};
      const update=event=>{if(event)last=event.detail;const {stage,loaded=0,total=0}=last;
        const names={connecting:this.t('正在连接','Connecting'),download:this.t('正在下载录像','Downloading replay'),validate:this.t('正在校验并保存到本机','Validating and saving locally'),saved:this.t('已保存','Saved')};
        const amount=stage==='download'?` · ${(loaded/1048576).toFixed(1)}${total>0?' / '+(total/1048576).toFixed(1):''} MB${total>0?' · '+Math.floor(loaded/total*100)+'%':''}`:'';
        button.textContent=(names[stage]||names.connecting)+amount+` · ${Math.floor((Date.now()-started)/1000)}s`;
      };update();addEventListener('starbreak-transfer',update);const timer=setInterval(async()=>{update();if(!globalThis.StarBreakBrowser&&!polling){polling=true;try{const response=await fetch('/api/records/transfer?id='+transferID);if(response.ok)update({detail:await response.json()});}catch(_){}finally{polling=false;}}},700);
      try{const response=await fetch('/api/records/redeem',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({code:value,transfer_id:transferID})});const result=await response.json();if(!response.ok)throw Error();this.notice=result.imported?this.t('录像已导入，可在战斗记录中回放。','Replay imported. Open it in Battle records.'):this.t('本地已存在这份录像。','This replay is already in your history.');this.codeOpen=false;this.codeDraft='';this.view='local';await this.refresh();this.render(true);}
      catch(_){this.sharedNotice=this.notice=this.t('获取录像失败，请检查分享码、分享服务配置及网络。','Could not retrieve replay. Check the code, sharing service configuration and connection.');this.render();}
      finally{this.redeeming=false;clearInterval(timer);removeEventListener('starbreak-transfer',update);button.disabled=false;button.textContent=this.t('导入录像','Import replay');}
    }
    async export(ids){
      try{const response=await fetch('/api/records/export',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({ids})});const value=await response.json();if(!response.ok)throw Error(value.error);const bytes=Uint8Array.from(atob(value.content),c=>c.charCodeAt(0));const url=URL.createObjectURL(new Blob([bytes],{type:value.filename.endsWith('.zip')?'application/zip':'application/octet-stream'}));const link=document.createElement('a');link.href=url;link.download=value.filename;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);this.notice=this.t('已导出所选记录。','Selected records exported.');}catch(_){this.notice=this.t('导出失败；请确认所选对局已经结束。','Export failed. Make sure the selected battles have finished.');}this.render();
    }
    async import(file){
      try{if(file.size>256*1024*1024)throw Error();const response=await fetch('/api/records/import',{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});const value=await response.json();if(!response.ok)throw Error();this.notice=this.t(`已导入 ${value.imported} 场，跳过 ${value.duplicates} 场重复记录。`,`Imported ${value.imported} battles; skipped ${value.duplicates} duplicates.`);await this.refresh();}catch(_){this.notice=this.t('导入失败：请选择有效的战绩文件或 ZIP，最大 256 MB。','Import failed: choose a valid battle file or ZIP, up to 256 MB.');this.render();}
    }
  };
})();
