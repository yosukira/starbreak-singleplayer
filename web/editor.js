/* Equipment drafts are validated by the Python model before being saved. */
class WebEquipment {
  constructor(language, changed) {
    this.language=language;this.changed=changed;this.shell=1;this.slot=256;this.map='hulk_mid';this.dirty=false;
    this.$=id=>document.getElementById(id);
    this.$('open-editor').onclick=()=>{this.render();this.$('equipment-dialog').showModal();};
    this.$('close-editor').onclick=()=>this.$('equipment-dialog').close();
    this.$('editor-class').onchange=event=>{this.selectClass(+event.target.value);this.changed();};
    this.$('editor-mk').oninput=event=>{this.row().mk_=+event.target.value;this.touch();};
    this.$('editor-implants').oninput=event=>{this.row().impslots_=+event.target.value;this.touch();};
    this.$('item-search').oninput=()=>this.itemOptions();
    this.$('all-classes').onchange=()=>this.itemOptions();
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
  t(zh,en){return this.language()==='zh'?zh:en;}
  row(){return this.profile.loadouts[this.shell];}
  current(){return this.row().items_.find(item=>item.slotpos_===this.slot);}
  touch(){this.dirty=true;localStorage.setItem('starbreak-web-draft',JSON.stringify(this.profile));this.changed();}
  notice(text){this.$('editor-error').hidden=false;this.$('editor-error').textContent=text;}
  async action(run){this.$('editor-error').hidden=true;try{await run();}catch(error){this.notice(error.message);}}
  async post(url,value){const response=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(value)});const data=await response.json();if(!response.ok)throw new Error(data.error);return data;}
  async load(restoreDraft=true){
    const response=await fetch('/api/editor'),data=await response.json();if(!response.ok)throw new Error(data.error);
    this.profile=data.profile;this.data=data;this.catalogue=new Map(data.items.map(item=>[item.type,item]));this.dirty=false;
    if(restoreDraft){try{const draft=JSON.parse(localStorage.getItem('starbreak-web-draft'));if(draft&&draft.revision===this.profile.revision){this.profile=await this.post('/api/validate',draft);this.dirty=true;}}catch(_){}}
    else {localStorage.removeItem('starbreak-web-draft');this.map=this.profile.settings.map;}
  }
  async save(){if(this.dirty){this.profile=await this.post('/api/editor',this.profile);this.dirty=false;localStorage.removeItem('starbreak-web-draft');this.changed();}}
  selectClass(shell){this.shell=shell;this.render();}
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
    this.$('editor-class').value=this.shell;this.$('editor-mk').value=this.row().mk_;this.$('editor-implants').value=this.row().impslots_;
    this.$('equipment-slots').replaceChildren(...this.data.slots.map(slot=>{
      const button=document.createElement('button'),item=this.row().items_.find(item=>item.slotpos_===slot.id),label=document.createElement('strong'),name=document.createElement('small');
      label.textContent=slot[this.language()];name.textContent=item?this.catalogue.get(item.type_)?.name||'#'+item.type_:this.t('空','Empty');
      button.append(label,name);button.classList.toggle('selected',slot.id===this.slot);button.onclick=()=>{this.slot=slot.id;this.$('item-search').value='';this.render();};return button;
    }));this.itemOptions();this.modifiers();this.projectiles();this.settings();
  }
  itemOptions(){
    const current=this.current(),query=this.$('item-search').value.toLowerCase().trim(),all=this.$('all-classes').checked;
    const values=[...this.catalogue.values()].filter(item=>(this.slot>=3840||item.slot===this.slot)&&
      (item.type===current?.type_||((!query||(item.type+' '+item.name).toLowerCase().includes(query))&&(all||this.slot>=3840||!item.usableby.length||item.usableby.includes(this.shell)))));
    values.sort((a,b)=>Number(!a.usableby.includes(this.shell))-Number(!b.usableby.includes(this.shell))||a.type-b.type);
    const options=values.map(item=>new Option(`${item.name} · #${item.type}`,item.type));if(this.slot!==256)options.unshift(new Option(this.t('— 空槽位 —','— Empty slot —'),0));
    this.$('editor-item').replaceChildren(...options);this.$('editor-item').value=current?.type_||0;
    const base=this.catalogue.get(current?.type_)?.base||[];this.$('base-stats').textContent=this.t('基础属性：','Base stats: ')+(base.map(boost=>this.statName(boost.stat_)+' '+boost.val_).join(' · ')||'—');
  }
  statName(id){return this.data.stats.find(stat=>stat.id===id)?.[this.language()]||'#'+id;}
  modifiers(){
    const current=this.current(),description=this.catalogue.get(current?.type_);this.$('add-modifier').disabled=!current||current.statboosts_.length>=64;
    this.$('modifier-rows').replaceChildren(...(current?.statboosts_||[]).map((boost,index)=>{
      const row=document.createElement('div'),select=document.createElement('select'),input=document.createElement('input'),remove=document.createElement('button');row.className='modifier-row';
      const stats=[...this.data.stats].sort((a,b)=>Number(!description.suggested.includes(a.id))-Number(!description.suggested.includes(b.id))||a.id-b.id);
      select.append(...stats.map(stat=>new Option(`${description.suggested.includes(stat.id)?'★ ':''}${stat[this.language()]} · ${stat.id}`,stat.id)));select.value=boost.stat_;
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
      const hint=document.createElement('p');hint.className='hint';hint.textContent=this.t('新一局生效；直接从首领飞船停稳后开始','Applies to the next attempt; start with the boss ship in position');container.append(hint);
      const rows={front:['前面炮台','Front turrets'],upper:['上面炮台','Upper turrets'],lower:['下面炮台','Lower turrets']};
      for(const [key,names]of Object.entries(rows))field(this.t(...names),this.profile.turrets[key],0,key==='front'?10:20,value=>this.profile.turrets[key]=value===''?null:Number(value),'1');
      for(const key of ['upper','lower'])field(this.t(key==='upper'?'上方追踪炮台':'下方追踪炮台',key==='upper'?'Upper seeking turrets':'Lower seeking turrets'),this.profile.missiles[key],0,20,value=>this.profile.missiles[key]=value===''?null:Number(value),'1');
    }
  }
}
