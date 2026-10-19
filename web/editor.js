/* Equipment drafts are validated by the Python model before being saved. */
class WebEquipment {
  constructor(language, changed) {
    this.language=language;this.changed=changed;this.shell=1;this.slot=256;this.map='hulk_mid';this.dirty=false;this.itemSort=localStorage.getItem('starbreak-item-sort')==='name'?'name':'id';
    this.$=id=>document.getElementById(id);
    const tabs=[...document.querySelectorAll('#editor-settings-tabs [role="tab"]')];
    for(const [index,tab] of tabs.entries()){
      tab.onclick=()=>this.selectSettingsTab(tab.dataset.panel);
      tab.onkeydown=event=>{
        if(tab.disabled||!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
        event.preventDefault();
        const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:
          (index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
        this.selectSettingsTab(tabs[next].dataset.panel);tabs[next].focus();
      };
    }
    this.updateState=null;this.updateTimer=null;
    this.$('check-updates').onclick=()=>this.checkUpdates();
    this.$('open-editor').onclick=()=>{this.render();this.$('equipment-dialog').showModal();};
    this.$('close-editor').onclick=()=>this.$('equipment-dialog').close();
    this.$('editor-class').onchange=event=>{this.selectClass(+event.target.value);this.changed();};
    this.$('editor-mk').oninput=event=>{this.row().mk_=+event.target.value;this.touch();};
    this.$('editor-implants').oninput=event=>{this.row().impslots_=+event.target.value;this.touch();};
    this.$('item-search').oninput=()=>{this.itemOptions();this.remember();};
    this.$('sort-items').onclick=()=>{this.itemSort=this.itemSort==='id'?'name':'id';localStorage.setItem('starbreak-item-sort',this.itemSort);this.itemOptions();this.remember();};
    this.$('all-classes').onchange=()=>{this.itemOptions();this.remember();};
    this.$('reset-keys').onclick=()=>{this.keyDraft={...this.data.key_defaults};this.keyboard();};
    this.$('save-keys').onclick=()=>this.action(async()=>{this.preferences=await this.post('/api/preferences',{...this.preferenceDocument(),keys:this.keyDraft});this.keyDraft={...this.preferences.keys};this.changed();this.$('keyboard-notice').textContent=this.t('按键已保存，立即生效。','Keyboard settings saved and applied.');});
    this.$('editor-item').onchange=event=>{this.replaceItem(+event.target.value);this.render();};
    this.$('add-modifier').onclick=()=>{
      const item=this.current();if(!item)return;
      const description=this.catalogue.get(item.type_);
      const stat=description.suggested.find(id=>!item.statboosts_.some(b=>b.stat_===id))||description.suggested[0]||18;
      item.statboosts_.push({stat_:stat,val_:Number(description.defaults[stat]??0)});this.touch();this.modifiers();
    };
    this.$('reset-speeds').onclick=()=>{const item=this.current();if(item){delete this.profile.projectile_speeds[item.type_];this.touch();this.projectiles();}};
    this.$('save-profile').onclick=()=>this.action(async()=>{await this.save();this.notice(this.t('配装已保存，新一局生效。','Loadout saved for the next attempt.'));});
    this.$('reload-profile').onclick=()=>this.action(async()=>{await this.load(false);this.render();this.changed();this.notice(this.t('已载入保存的配装。','Saved loadout loaded.'));});
    this.$('restore-defaults').onclick=()=>this.action(async()=>{
      clearTimeout(this.draftTimer);
      this.$('restore-defaults').disabled=true;
      try{this.profile=await this.post('/api/reset',{revision:this.profile.revision});this.dirty=false;
        localStorage.removeItem('starbreak-web-draft');this.map=this.profile.settings.map;
        this.changed();this.render();this.notice(this.t('全部配装与战斗设置已恢复默认，原配置已备份；下一局生效。','All loadouts and encounter settings restored. Previous settings backed up; applies to the next attempt.'));
      }finally{this.$('restore-defaults').disabled=false;}
    });
    this.$('export-profile').onclick=()=>{
      const blob=new Blob([JSON.stringify(this.profile,null,2)+'\n'],{type:'application/json'}),link=document.createElement('a'),url=URL.createObjectURL(blob);
      link.href=url;link.download='starbreak-web-loadouts.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    };
    this.$('import-profile').onclick=()=>this.$('profile-file').click();
    this.$('profile-file').onchange=event=>this.action(async()=>{
      const file=event.target.files[0];if(!file)return;
      if(file.size>512*1024)throw new Error(this.t('配装文件不能超过 512 KB。','Loadout file exceeds 512 KB.'));
      const value=JSON.parse(await file.text());value.revision=this.profile.revision;
      this.profile=await this.post('/api/validate',value);this.map=this.profile.settings.map;this.touch();this.render();this.changed();event.target.value='';
      this.notice(this.t('已导入草稿，请检查后保存。','Draft imported. Review it, then save.'));
    });
  }
  t(zh,en){return this.language().startsWith('zh')?zh:en;}
  selectSettingsTab(panel){
    for(const tab of document.querySelectorAll('#editor-settings-tabs [role="tab"]')){
      const selected=tab.dataset.panel===panel;
      tab.setAttribute('aria-selected',String(selected));tab.tabIndex=selected?0:-1;
      this.$(tab.dataset.panel).hidden=!selected;
    }
  }
  row(){return this.profile.loadouts[this.shell];}
  current(){return this.row().items_.find(item=>item.slotpos_===this.slot);}
  touch(){this.dirty=true;localStorage.setItem('starbreak-web-draft',JSON.stringify(this.profile));this.changed();
    clearTimeout(this.draftTimer);this.draftTimer=setTimeout(()=>this.post('/api/draft',this.profile).catch(()=>{}),250);
  }
  flush(){clearTimeout(this.updateTimer);if(!this.preferences)return;clearTimeout(this.preferenceTimer);clearTimeout(this.draftTimer);navigator.sendBeacon('/api/preferences',JSON.stringify(this.preferenceDocument()));if(this.dirty)navigator.sendBeacon('/api/draft',JSON.stringify(this.profile));}
  renderUpdate(){
    const state=this.updateState||{},notice=this.$('update-notice');
    const message=state.messages?.[this.language().startsWith('zh')?'zh':'en']||this.t('启动时自动检查更新。','Updates are checked automatically at startup.');
    if(notice.textContent!==message)notice.textContent=message;notice.title=message;
    this.$('check-updates').disabled=['checking','installing'].includes(state.state);
    notice.closest('.update-row').dataset.state=state.state||'idle';
  }
  setUpdateStatus(state){
    clearTimeout(this.updateTimer);this.updateState=state;this.renderUpdate();
    if(['checking','installing'].includes(state.state))this.updateTimer=setTimeout(()=>this.pollUpdate(),1000);
  }
  updateFailure(){this.setUpdateStatus({state:'failed',messages:{zh:'检查更新失败，请稍后重试；继续使用当前版本。',en:'Could not check for updates. Try again later; the installed version remains in use.'}});}
  async pollUpdate(){
    try{const response=await fetch('/api/update');if(!response.ok)throw new Error('Update status unavailable');this.setUpdateStatus(await response.json());}
    catch(_){this.updateFailure();}
  }
  async checkUpdates(){
    if(['checking','installing'].includes(this.updateState?.state))return;
    this.setUpdateStatus({state:'checking',messages:{zh:'正在检查更新…',en:'Checking for updates…'}});
    try{this.setUpdateStatus(await this.post('/api/update',{}));}catch(_){this.updateFailure();}
  }
  preferenceDocument(){return {...this.preferences,shell:this.shell,slot:this.slot,item_sort:this.itemSort,query:this.$('item-search').value,all_classes:this.$('all-classes').checked,language:this.language()};}
  remember(){if(!this.preferences)return;clearTimeout(this.preferenceTimer);this.preferenceTimer=setTimeout(()=>this.post('/api/preferences',this.preferenceDocument()).catch(()=>{}),250);}
  keyboard(){
    if(!this.preferences)return;
    this.keyDraft||={...this.preferences.keys};
    const box=this.$('keyboard-settings');box.replaceChildren();
    const names={interact:['互动键','Interaction key'],editor_toggle:['显示/隐藏编辑器','Show / hide editor'],editor_toggle_alt:['备用编辑器快捷键','Alternate editor shortcut'],restart:['重开当前局','Restart current run'],wf:['切换 WF','Switch to WF'],dw:['切换 DW','Switch to DW'],fab:['切换 FAB','Switch to FAB'],ic:['切换 IC','Switch to IC'],zoom_in:['放大画面 / 缩小视野','Zoom in / narrower view'],zoom_out:['缩小画面 / 扩大视野','Zoom out / wider view']};
    for(const [key,label]of Object.entries(names)){
      const row=document.createElement('label'),text=document.createElement('span'),select=document.createElement('select');row.className='setting-row';text.textContent=this.t(...label);select.setAttribute('aria-label',text.textContent);
      const choices=[...(key==='interact'?['Auto']:['None']),...this.data.key_choices,...(key.startsWith('zoom_')?this.data.zoom_key_choices||[]:[])];
      select.append(...choices.map(name=>new Option(({Equal:'+',Minus:'−',NumpadAdd:'Numpad +',NumpadSubtract:'Numpad −'})[name]||name,name)));select.value=this.keyDraft[key];select.onchange=()=>{this.keyDraft[key]=select.value;};row.append(text,select);box.append(row);
    }
    const zoomRow=document.createElement('label'),zoomText=document.createElement('span'),zoomStep=document.createElement('input');zoomRow.className='setting-row';zoomText.textContent=this.t('每次缩放比例（%）','Zoom step (%)');zoomStep.type='number';zoomStep.min='5';zoomStep.max='50';zoomStep.step='1';zoomStep.value=this.keyDraft.zoom_percent??10;zoomStep.oninput=()=>this.keyDraft.zoom_percent=Number(zoomStep.value);zoomRow.append(zoomText,zoomStep);box.append(zoomRow);
    const row=document.createElement('label'),text=document.createElement('span'),input=document.createElement('input');row.className='setting-row';text.textContent=this.t('长按时间（秒）','Hold duration (seconds)');input.type='number';input.min='.5';input.max='5';input.step='.1';input.value=this.keyDraft.hold_seconds;input.setAttribute('aria-label',text.textContent);input.oninput=()=>{this.keyDraft.hold_seconds=Number(input.value);};row.append(text,input);box.append(row);
  }
  notice(text){this.$('editor-error').hidden=false;this.$('editor-error').textContent=text;}
  async action(run){this.$('editor-error').hidden=true;try{await run();}catch(error){this.notice(error.message);}}
  async post(url,value){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const data=await response.json();if(!response.ok)throw new Error(data.error);return data;}
  async load(restoreDraft=true){
    const response=await fetch('/api/editor'),data=await response.json();if(!response.ok)throw new Error(data.error);
    this.profile=data.profile;this.data=data;
    if(data.update)this.setUpdateStatus(data.update);
    if(!this.preferences){this.preferences=data.preferences;this.shell=this.preferences.shell;this.slot=this.preferences.slot;this.itemSort=this.preferences.item_sort;this.$('item-search').value=this.preferences.query;this.$('all-classes').checked=this.preferences.all_classes;}
    if(restoreDraft&&data.draft){this.profile=data.draft;this.dirty=true;}
    this.catalogue=new Map(data.items.map(item=>[item.type,item]));this.dirty=!!(restoreDraft&&data.draft);
    if(restoreDraft){try{const draft=JSON.parse(localStorage.getItem('starbreak-web-draft'));if(draft&&draft.revision===this.profile.revision){this.profile=await this.post('/api/validate',draft);this.dirty=true;}}catch(_){}}
    else {clearTimeout(this.draftTimer);await this.post('/api/draft',{discard:true,revision:this.profile.revision});localStorage.removeItem('starbreak-web-draft');this.map=this.profile.settings.map;}
  }
  async save(){clearTimeout(this.draftTimer);if(this.dirty){this.profile=await this.post('/api/editor',this.profile);this.dirty=false;localStorage.removeItem('starbreak-web-draft');this.changed();}}
  selectClass(shell){this.shell=shell;this.remember();this.render();}
  setMap(map){this.map=map;this.profile.settings.map=map;this.touch();this.settings();}
  replaceItem(type,slot=this.slot){
    if(this.row().items_.find(item=>item.slotpos_===slot)?.type_===type)return;
    this.row().items_=this.row().items_.filter(item=>item.slotpos_!==slot);
    if(type)this.row().items_.push({type_:type,slotpos_:slot,statboosts_:[]});this.touch();
  }
  mainOptions(){return [...this.catalogue.values()].filter(item=>item.slot===256&&(!item.usableby.length||item.usableby.includes(this.shell)));}
  main(){return this.row().items_.find(item=>item.slotpos_===256)?.type_;}
  render(){
    if(!this.profile)return;
    this.renderUpdate();
    this.$('editor-class').value=this.shell;this.$('editor-mk').value=this.row().mk_;this.$('editor-implants').value=this.row().impslots_;
    this.$('equipment-slots').replaceChildren(...this.data.slots.map(slot=>{
      const button=document.createElement('button'),item=this.row().items_.find(item=>item.slotpos_===slot.id),label=document.createElement('strong'),name=document.createElement('small');
      label.textContent=slot[this.language().startsWith('zh')?'zh':'en'];name.textContent=item?this.catalogue.get(item.type_)?.name||'#'+item.type_:this.t('空','Empty');
      button.append(label,name);button.classList.toggle('selected',slot.id===this.slot);button.onclick=()=>{this.slot=slot.id;this.$('item-search').value='';this.remember();this.render();};return button;
    }));this.itemOptions();this.modifiers();this.projectiles();this.settings();this.keyboard();
  }
  itemOptions(){
    const current=this.current(),query=this.$('item-search').value.toLowerCase().trim(),all=this.$('all-classes').checked;
    const values=[...this.catalogue.values()].filter(item=>(this.slot>=3840||item.slot===this.slot)&&
      (item.type===current?.type_||((!query||(item.type+' '+item.name).toLowerCase().includes(query))&&(all||this.slot>=3840||!item.usableby.length||item.usableby.includes(this.shell)))));
    values.sort(this.itemSort==='name'?(a,b)=>a.name.toLowerCase().localeCompare(b.name.toLowerCase(),'en')||a.type-b.type:(a,b)=>a.type-b.type);
    this.$('sort-items').textContent=this.itemSort==='name'?this.t('装备排序：首字母','Equipment sort: A–Z'):this.t('装备排序：ID','Equipment sort: ID');
    const options=values.map(item=>new Option(`${item.name} · #${item.type}`,item.type));if(this.slot!==256)options.unshift(new Option(this.t('— 空槽位 —','— Empty slot —'),0));
    this.$('editor-item').replaceChildren(...options);this.$('editor-item').value=current?.type_||0;
    const base=this.catalogue.get(current?.type_)?.base||[];this.$('base-stats').textContent=this.t('基础属性：','Base stats: ')+(base.map(boost=>this.statName(boost.stat_)+' '+boost.val_).join(' · ')||'—');
  }
  statName(id){return this.data.stats.find(stat=>stat.id===id)?.[this.language()==='zh-affixes'?'zh':'en']||'#'+id;}
  modifiers(){
    const current=this.current(),description=this.catalogue.get(current?.type_);this.$('add-modifier').disabled=!current||current.statboosts_.length>=64;
    this.$('modifier-rows').replaceChildren(...(current?.statboosts_||[]).map((boost,index)=>{
      const row=document.createElement('div'),select=document.createElement('select'),input=document.createElement('input'),remove=document.createElement('button');row.className='modifier-row';
      const stats=[...this.data.stats].sort((a,b)=>Number(!description.suggested.includes(a.id))-Number(!description.suggested.includes(b.id))||a.id-b.id);
      select.append(...stats.map(stat=>new Option(`${description.suggested.includes(stat.id)?'★ ':''}${this.statName(stat.id)} · ${stat.id}`,stat.id)));select.value=boost.stat_;
      select.setAttribute('aria-label',this.t('词缀属性','Modifier stat')+' '+(index+1));select.onchange=()=>{boost.stat_=+select.value;this.touch();};
      input.type='number';input.step='1';input.value=boost.val_;input.setAttribute('aria-label',this.t('词缀数值','Modifier value')+' '+(index+1));input.oninput=()=>{boost.val_=Number(input.value);this.touch();};
      remove.textContent=this.t('删除','Remove');remove.className='secondary';remove.onclick=()=>{current.statboosts_.splice(index,1);this.touch();this.modifiers();};row.append(select,input,remove);return row;
    }));
  }
  projectiles(){
    const current=this.current(),objects=this.catalogue.get(current?.type_)?.projectiles||[];this.$('projectile-panel').hidden=!objects.length;
    this.$('projectile-rows').replaceChildren(...objects.map(object=>{
      const row=document.createElement('label'),name=document.createElement('span'),input=document.createElement('input');row.className='projectile-row';name.textContent=object.vid;
      input.type='number';input.min='0';input.max='1000000';input.step='1';input.value=this.profile.projectile_speeds[current.type_]?.[object.index]??object.speed;
      input.setAttribute('aria-label',this.t('基础弹速 ','Projectile speed ')+object.index);input.oninput=()=>{const speeds=this.profile.projectile_speeds[current.type_]||={};speeds[object.index]=Number(input.value);this.touch();};row.append(name,input);return row;
    }));
  }
  settings(){
    if(!this.data)return;const container=this.$('encounter-settings'),meta=this.data.maps.find(map=>map.id===this.map);container.replaceChildren();if(!meta)return;
    this.$('settings-map-title').textContent=this.mapTitle||this.map;
    const field=(label,value,min,max,change,step='any')=>{
      const row=document.createElement('label'),text=document.createElement('span'),input=document.createElement('input');row.className='setting-row';text.textContent=label;input.type='number';input.value=value??'';input.min=min;input.max=max;input.step=step;
      input.setAttribute('aria-label',label);input.placeholder=this.t('随机','Random');input.oninput=()=>{change(input.value);this.touch();};row.append(text,input);container.append(row);
    };
    const drops=this.profile.settings.drops[this.map]||meta.drops;
    const dropLabels={health_percent:['血包 %','Health %'],ammo_percent:['弹药 %','Ammo %'],ec_percent:['EC %','EC %']};
    for(const [key,names]of Object.entries(dropLabels))field(this.t(...names),drops[key],0,100,value=>{this.profile.settings.drops[this.map]={...drops,...this.profile.settings.drops[this.map],[key]:Number(value)};});
    const labels={enemy_hp:['敌人血量倍率','Enemy HP multiplier'],enemy_damage:['敌人伤害倍率','Enemy damage multiplier'],enemy_speed:['敌人速度倍率','Enemy speed multiplier'],spawn_rate:['刷怪速率倍率','Spawn rate multiplier'],spawn_cap:['刷怪上限','Spawn limit'],boss_attack_rate:['首领攻击速率','Boss attack rate'],turret_attack_rate:['炮台攻击速率','Turret attack rate'],laser_rate:['激光速率','Laser rate'],bullet_rate:['子弹速率','Bullet rate'],saw_speed:['锯刃速度','Saw speed']};
    const balance=this.profile.settings.balance[this.map]||meta.balance_defaults;
    for(const [key,limits]of Object.entries(meta.balance_limits))field(this.t(...(labels[key]||[key,key])),balance[key]??meta.balance_defaults[key],...limits,value=>{this.profile.settings.balance[this.map]={...balance,...this.profile.settings.balance[this.map],[key]:Number(value)};},key==='spawn_cap'?'1':'any');
    if(['egy_hulk','hulk_normal'].includes(this.map)){
      const toggle=document.createElement('label'),check=document.createElement('input'),caption=document.createElement('span');
      toggle.className='checkbox';check.type='checkbox';check.id='skip-intro';
      check.checked=this.profile.settings.skip_intro?.[this.map]===true;
      caption.textContent=this.t('跳过开局','Skip opening');
      check.onchange=()=>{const rows=this.profile.settings.skip_intro||={};rows[this.map]=check.checked;this.touch();};
      toggle.append(check,caption);container.append(toggle);
      const hint=document.createElement('p');hint.className='hint';hint.textContent=this.t('新一局生效；直接从首领飞船停稳后开始','Applies to the next attempt; start with the boss ship in position');toggle.title=hint.textContent;
      field(this.t('追踪弹延迟 (ms)','Homing missile delay (ms)'),this.profile.settings.missile_delay_ms?.[this.map]??275,0,500,value=>{const delays=this.profile.settings.missile_delay_ms||={};delays[this.map]=value===''?275:Number(value);},'1');
      const delayHint=document.createElement('p');delayHint.className='hint';delayHint.textContent=this.t('首领与小怪共用；瞄准玩家多少毫秒前的位置；0 = 当前位置，默认 275ms','Shared by boss and enemy missiles; aim at the player’s position this many milliseconds ago; 0 = current position, default 275ms');container.lastElementChild.title=delayHint.textContent;
      const rows={front:['前面炮台','Front turrets'],upper:['上面炮台','Upper turrets'],lower:['下面炮台','Lower turrets']};
      for(const [key,names]of Object.entries(rows))field(this.t(...names),this.profile.turrets[key],0,key==='front'?10:20,value=>this.profile.turrets[key]=value===''?null:Number(value),'1');
      for(const key of ['upper','lower'])field(this.t(key==='upper'?'上方追踪炮台':'下方追踪炮台',key==='upper'?'Upper seeking turrets':'Lower seeking turrets'),this.profile.missiles[key],0,20,value=>this.profile.missiles[key]=value===''?null:Number(value),'1');
    }
  }
}
