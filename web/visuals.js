/* User-owned PNG overrides. No file content is sent to the game server. */
(function(scope){
  'use strict';
  const LIMIT=128*1024*1024,KEY='starbreak-visual-options-v1';
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
  function options(storage=scope.localStorage){try{const value=JSON.parse(storage.getItem(KEY)||'{}');return {textures:value.textures!==false,enemyHP:value.enemyHP===true,minimap:value.minimap===true,playerHUD:value.playerHUD===true};}catch(_){return {textures:true,enemyHP:false,minimap:false,playerHUD:false};}}
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
      const settings=el('div',null,'visual-options'),value=options();
      for(const [key,zh,en]of [['textures','使用自定义贴图','Use custom textures'],['enemyHP','显示敌人血量','Show enemy health'],['minimap','显示小地图','Show minimap'],['playerHUD','替换左上角生命与弹药显示','Replace upper-left health & ammo HUD']]){
        const label=el('label',null,'checkbox'),input=el('input');input.type='checkbox';input.checked=value[key];input.dataset.visual=key;input.onchange=()=>{const next=options();next[key]=input.checked;localStorage.setItem(KEY,JSON.stringify(next));this.changed();};label.append(input,el('span',this.t(zh,en)));settings.append(label);
      }
      root.append(settings,el('p',this.t('增强显示默认关闭，可分别开启。只改变显示，不修改战斗数值；小地图按当前房间显示地形、玩家和敌人。','Enhancements are off by default and can be enabled separately. They change display only; the minimap shows the current room, terrain, player and enemies.'),'hint'));
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
  const api={path,basename,match,png,options,Store,Page};if(typeof module!=='undefined')module.exports=api;else scope.StarBreakVisuals=api;
})(typeof window==='undefined'?globalThis:window);
