/* User-owned PNG overrides. No file content is sent to the game server. */
(function(scope){
  'use strict';
  const LIMIT=128*1024*1024,KEY='starbreak-visual-options-v1';
  const DEFAULTS={textures:true,enemyHP:false,minimap:false,playerHUD:false,
    hideEffects:false,hideDamageNumbers:false,hideNames:false,hideFactions:false,hideHealthbars:false,
    hideFriendlyObjects:false,hideShell:false,hideChat:false,noShake:false,noFlash:false,
    damageFlash:1,camera:0,showHUD:true,showTiles:true,showEdges:true,showScatter:true,showUniform:true,
    showHitboxes:false,showBoxes:false,customBackground:false,background:'#080e17',brightness:1,cursorHide:0,
    miniHP:false,miniAmmo:false,ecCounter:false,fps:false,roomInfo:false,hideExtras:false,
    enemyFontSize:16,hudFontSize:40,minimapScale:.26,minimapPosition:'bottom-right'};
  const GROUPS=[
    ['HUD / 信息显示','HUD / Information',[
      ['enemyHP','敌人血量与护甲数字','Enemy health & armor numbers'],['enemyFontSize','敌人数字字号','Enemy number size','number',10,48,1],
      ['playerHUD','替换左上角生命与弹药显示','Replace upper-left health & ammo HUD'],['hudFontSize','左上角生命字号','Player health size','number',24,160,1],
      ['miniHP','角色上方显示生命','Health above your character'],['miniAmmo','角色上方显示弹药','Ammo above your character'],
      ['ecCounter','本房间与本次游玩的 EC 增量','Room & session EC gained'],['fps','显示 FPS 与帧耗时','Show FPS & frame time'],['roomInfo','显示当前房间信息','Show current room information']]],
    ['Noise / 干扰控制','Noise / Clutter',[
      ['hideEffects','隐藏动画特效','Hide animation effects'],['hideDamageNumbers','隐藏伤害数字','Hide damage numbers'],
      ['noShake','关闭震屏','Disable camera shake'],['noFlash','关闭全屏闪光','Disable screen flashes'],['damageFlash','受击闪白强度','Damage flash intensity','range',0,1,.1],
      ['hideNames','隐藏角色名字及公会','Hide character names & guilds'],['hideFactions','仅隐藏公会名','Hide guild names only'],['hideHealthbars','隐藏原生角色血条','Hide original character health bars'],
      ['hideFriendlyObjects','隐藏己方弹体与召唤物（保留平台）','Hide friendly projectiles & summons (keep platforms)'],['hideShell','隐藏角色外观','Hide your character sprite'],
      ['hideChat','隐藏聊天显示','Hide chat display'],['hideExtras','隐藏 VQ 假弹体（city.hbshot）','Hide VQ decoy projectiles (city.hbshot)']]],
    ['Misc / 镜头与场景','Misc / Camera & scenery',[
      ['camera','镜头跟随','Camera follow','select',[[0,'原版','Original'],[1,'居中','Centered'],[2,'居中且不限制房间边界','Centered, outside room bounds']]],
      ['showHUD','显示原生 HUD','Show original HUD'],['showTiles','显示地形贴图','Show terrain textures'],['showEdges','显示边缘装饰','Show edge decorations'],['showScatter','显示散布装饰','Show scattered decorations'],['showUniform','显示背景层','Show background layers'],
      ['customBackground','使用纯色背景（隐藏背景装饰）','Use a solid background (hide background decorations)'],['background','背景颜色','Background color','color'],['brightness','背景最大亮度','Maximum background brightness','range',0,1,.05],['cursorHide','鼠标静止后隐藏（秒，0 关闭）','Hide idle cursor (seconds; 0 off)','number',0,10,.5],
      ['showHitboxes','显示攻击判定区域','Show attack hitboxes'],['showBoxes','显示对象边框','Show object boxes']]],
    ['Map / 小地图','Map / Minimap',[
      ['minimap','显示小地图（默认关闭）','Show minimap (off by default)'],['minimapScale','地图占画面的比例','Map size relative to game','range',.1,.9,.05],
      ['minimapPosition','地图位置','Map position','select',[['bottom-right','右下','Bottom right'],['top-right','右上','Top right'],['center','中央','Center']]]]]
  ];
  function path(value){return decodeURIComponent(new URL(value,'https://local.invalid/').pathname).replace(/^.*\/assets\//,'/').replace(/^\/singleweb\//,'/');}
  const basename=value=>path(value).split('/').pop().toLowerCase();
  function match(name,entries){
    const key=String(name).replaceAll('\\','/').replace(/^\/+/,''),exact=entries.filter(row=>row.path.replace(/^\/+/,'' )===key);
    const found=exact.length?exact:entries.filter(row=>basename(row.path)===basename(key));
    if(found.length!==1)throw Error(found.length?'Ambiguous filename / 文件名匹配不唯一':'No matching game texture / 未匹配到游戏贴图');
    return found[0];
  }
  function png(buffer){
    const bytes=new Uint8Array(buffer),view=new DataView(buffer);
    if(bytes.length<33||[137,80,78,71,13,10,26,10].some((v,i)=>bytes[i]!==v)||view.getUint32(8)!==13||view.getUint32(12)!==0x49484452)throw Error('Use a PNG texture / 请选择 PNG 贴图');
    const width=view.getUint32(16),height=view.getUint32(20);
    if(!width||!height||width>8192||height>8192||width*height>33554432)throw Error('Texture dimensions exceed limit / 贴图尺寸超限');
    return {width,height};
  }
  function options(storage=scope.localStorage){
    let value;try{value=JSON.parse(storage.getItem(KEY)||'{}');}catch(_){}if(!value||typeof value!=='object')value={};
    const result={...DEFAULTS};for(const [key,fallback]of Object.entries(DEFAULTS))if(typeof fallback==='boolean')result[key]=typeof value[key]==='boolean'?value[key]:fallback;
    for(const [key,min,max]of [['damageFlash',0,1],['brightness',0,1],['cursorHide',0,10],['enemyFontSize',10,48],['hudFontSize',24,160],['minimapScale',.1,.9]])if(Number.isFinite(value[key]))result[key]=Math.max(min,Math.min(max,value[key]));
    if([0,1,2].includes(value.camera))result.camera=value.camera;if(/^#[a-f\d]{6}$/i.test(value.background))result.background=value.background;
    if(['bottom-right','top-right','center'].includes(value.minimapPosition))result.minimapPosition=value.minimapPosition;
    return result;
  }
  class Store{
    constructor(){this.ready=new Promise((resolve,reject)=>{const request=indexedDB.open('starbreak-user-textures-v1',1);request.onupgradeneeded=()=>request.result.createObjectStore('textures',{keyPath:'path'});request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);});}
    async transaction(mode,operation){const db=await this.ready;return new Promise((resolve,reject)=>{const tx=db.transaction('textures',mode);let value;tx.oncomplete=()=>resolve(value);tx.onabort=tx.onerror=()=>reject(tx.error||Error('Browser storage failed'));const request=operation(tx.objectStore('textures'));request.onsuccess=()=>{value=request.result;};});}
    list(){return this.transaction('readonly',store=>store.getAll());}
    remove(key){return this.transaction('readwrite',store=>store.delete(key));}
    clear(){return this.transaction('readwrite',store=>store.clear());}
    async put(file,catalogue){
      if(file.size>16*1024*1024)throw Error('Maximum 16 MB per PNG / 单张 PNG 上限 16 MB');
      const original=match(file.webkitRelativePath||file.name,catalogue),bytes=await file.arrayBuffer(),size=png(bytes);
      if(size.width!==original.width||size.height!==original.height)throw Error(`Expected ${original.width} × ${original.height} / 需要保持原图尺寸`);
      const image=await createImageBitmap(file);image.close();
      const entries=await this.list();
      if(entries.filter(row=>row.path!==original.path).reduce((n,row)=>n+row.blob.size,0)+file.size>LIMIT||entries.length>=256&&!entries.some(row=>row.path===original.path))throw Error('Local texture limit: 256 files / 128 MB / 本地贴图已达上限');
      return this.transaction('readwrite',store=>store.put({path:original.path,name:file.name,...size,blob:new Blob([bytes],{type:'image/png'}),updated:Date.now()}));
    }
  }
  class Page{
    constructor(root,language){this.root=root;this.language=language;this.store=new Store();this.rows=[];this.catalogue=[];this.notice='';this.pending=false;this.initialized=false;}
    t(zh,en){return this.language().startsWith('zh')?zh:en;}
    async show(){
      if(!this.initialized){
        try{const response=await fetch('texture-catalogue.json');if(!response.ok)throw Error(this.t('贴图目录暂不可用','Texture catalogue is unavailable'));this.catalogue=await response.json();this.rows=await this.store.list();this.initialized=true;}catch(error){this.notice=error.message;}
      }
      this.render();
    }
    changed(){scope.dispatchEvent(new Event('starbreak-visuals'));document.querySelector('iframe')?.contentWindow.postMessage({type:'visual-options',options:options()},location.origin);}
    render(){
      const root=this.root;root.replaceChildren();const el=(tag,text,cls)=>{const node=document.createElement(tag);if(text)node.textContent=text;if(cls)node.className=cls;return node;};
      root.append(el('h1',this.t('贴图与增强','Textures & enhancements')),el('p',this.t('贴图仅保存在当前浏览器，不上传服务器。按原文件名匹配 PNG，尺寸必须与原图一致。修改贴图后，下一次开始战斗或回放时生效。','Textures stay in this browser and are never uploaded. PNG filenames and dimensions must match the originals. Texture changes apply the next time you start a battle or replay.'),'hint'));
      const value=options(),save=(key,next)=>{const data=options();data[key]=next;localStorage.setItem(KEY,JSON.stringify(data));this.changed();};
      const presets=el('div',null,'visual-actions');
      for(const [zh,en,action]of [['低干扰预设','Low clutter preset',()=>({...options(),hideEffects:true,noShake:true,noFlash:true,showEdges:false,showScatter:false,damageFlash:.2})],['恢复增强默认设置','Reset enhancement settings',()=>({...DEFAULTS,textures:options().textures})]]){const button=el('button',this.t(zh,en),'secondary');button.onclick=()=>{localStorage.setItem(KEY,JSON.stringify(action()));this.changed();this.render();};presets.append(button);}root.append(presets);
      const settings=el('div',null,'visual-groups');
      for(const [zh,en,controls]of GROUPS){const group=el('section',null,'visual-group');group.append(el('h2',this.t(zh,en)));
        for(const [key,labelZH,labelEN,type='checkbox',min,max,step]of controls){
          const label=el('label',null,type==='checkbox'?'checkbox':'visual-control'),input=el(type==='select'?'select':'input'),name=el('span',this.t(labelZH,labelEN));input.dataset.visual=key;
          if(type==='select'){for(const [v,z,e]of min)input.append(new Option(this.t(z,e),v));input.value=value[key];}
          else{input.type=type;if(type==='checkbox')input.checked=value[key];else{input.value=value[key];if(type==='range'||type==='number'){input.min=min;input.max=max;input.step=step;}}}
          const number=type==='range'?el('output',String(value[key])):null;
          input.oninput=()=>{if(number)number.value=input.value;};input.onchange=()=>save(key,type==='checkbox'?input.checked:type==='range'||type==='number'||key==='camera'?Number(input.value):input.value);
          if(type==='checkbox')label.append(input,name);else label.append(name,input);if(number)label.append(number);group.append(label);
        }settings.append(group);
      }
      root.append(settings,el('p',this.t('开关即时生效并自动保存。隐藏地形贴图不改变地形碰撞；隐藏特效不会删除攻击。血量与护甲按 SBPE 显示在敌人身体中心，护甲为正数时另起一行。','Switches apply immediately and save automatically. Hiding terrain textures leaves collisions intact; hiding effects keeps attacks active. Enemy health and positive armor appear on two lines at the body center, as in SBPE.'),'hint'));
      root.append(el('h2',this.t('自定义贴图','Custom textures')));const use=el('label',null,'checkbox'),enabled=el('input');enabled.type='checkbox';enabled.checked=value.textures;enabled.dataset.visual='textures';enabled.onchange=()=>save('textures',enabled.checked);use.append(enabled,el('span',this.t('使用自定义贴图','Use custom textures')));root.append(use);
      const actions=el('div',null,'visual-actions'),input=el('input');input.type='file';input.multiple=true;input.accept='.png,image/png';input.id='visual-files';input.disabled=this.pending;
      input.onchange=async()=>{
        this.pending=true;this.render();const messages=[];let saved=0;
        for(const file of input.files){try{await this.store.put(file,this.catalogue);saved++;}catch(error){messages.push(file.name+': '+error.message);}}
        try{this.rows=await this.store.list();}catch(error){messages.push(error.message);}this.pending=false;this.notice=this.t(`已保存 ${saved} 张贴图。`,`Saved ${saved} texture(s).`)+(messages.length?'\n'+messages.join('\n'):'');this.changed();this.render();
      };
      const clear=el('button',this.t('清除全部贴图','Remove all textures'),'secondary');clear.disabled=this.pending||!this.rows.length;clear.onclick=async()=>{if(!confirm(this.t('删除当前浏览器保存的全部自定义贴图？','Remove all custom textures saved in this browser?')))return;await this.store.clear();this.rows=[];this.changed();this.render();};actions.append(input,clear);root.append(actions);
      root.append(el('p',this.t(`已保存 ${this.rows.length} 张 / ${(this.rows.reduce((n,r)=>n+r.blob.size,0)/1048576).toFixed(1)} MB`,`Saved ${this.rows.length} / ${(this.rows.reduce((n,r)=>n+r.blob.size,0)/1048576).toFixed(1)} MB`),'hint'));
      const status=el('p',this.notice,'visual-notice');status.role='status';root.append(status);
      const list=el('div',null,'visual-list');
      for(const row of this.rows.sort((a,b)=>a.path.localeCompare(b.path))){const line=el('div',null,'visual-row'),remove=el('button',this.t('删除','Delete'),'secondary');remove.onclick=async()=>{await this.store.remove(row.path);this.rows=this.rows.filter(r=>r.path!==row.path);this.changed();this.render();};line.append(el('span',row.path),el('small',`${row.width} × ${row.height}`),remove);list.append(line);}
      root.append(list);const details=el('details'),summary=el('summary',this.t('可替换的原始贴图名称','Original texture filenames'));details.append(summary);const names=el('div',null,'visual-catalogue');for(const row of this.catalogue)names.append(el('div',`${row.path} · ${row.width} × ${row.height}`));details.append(names);root.append(details);
    }
  }
  const api={path,basename,match,png,options,Store,Page,DEFAULTS,GROUPS};if(typeof module!=='undefined')module.exports=api;else scope.StarBreakVisuals=api;
})(typeof window==='undefined'?globalThis:window);
