(() => {
  const $=id=>document.getElementById(id);
  let language=localStorage.getItem('starbreak-web-language')||(navigator.language.startsWith('zh')?'zh':'en');
  if(!['en','zh'].includes(language))language='en';
  let catalogue=[],busy=false,liveMap=null,runMap=null;
  const t=(zh,en)=>language==='zh'?zh:en;
  const editor=new WebEquipment(()=>language,()=>sync());
  addEventListener('pagehide',()=>editor.flush());
  const title=map=>map?.names?.[language]||map?.title||'StarBreak';
  function mapOptions(){
    if(!catalogue.length)return;
    const selected=$('map').value||editor.map;
    $('map').replaceChildren(...catalogue.map(map=>{const option=new Option(title(map)+(map.ready?'':t('（适配中）',' (in progress)')),map.id);option.disabled=!map.ready;return option;}));
    $('map').value=selected;
  }
  function translate(){
    document.documentElement.lang=language;$('language').value=language;
    document.querySelectorAll('[data-en]').forEach(element=>element.textContent=element.dataset[language]);
    mapOptions();editor.mapTitle=title(catalogue.find(map=>map.id===editor.map));editor.render();sync();
    $('arena-title').textContent=title(catalogue.find(map=>map.id===(liveMap||editor.map)));
  }
  let shortcutFingerprint='';
  const shortcuts=new PracticeShortcuts(window,{},shortcut,()=>busy);
  function shortcut(action){
    if(action==='editor'){if($('equipment-dialog').open)$('equipment-dialog').close();else $('open-editor').click();return;}
    if(busy||$('equipment-dialog').open||!liveMap)return;
    const shell={wf:1,dw:2,fab:5,ic:3}[action]||editor.shell;
    editor.selectClass(shell);startEncounter(runMap||editor.map,shell,false);
  }
  function sync(){
    if(!editor.profile)return;
    if(editor.preferences){const signature=JSON.stringify(editor.preferences.keys);if(signature!==shortcutFingerprint){shortcutFingerprint=signature;shortcuts.update(editor.preferences.keys);document.querySelector('iframe')?.contentWindow.postMessage({type:'practice-keys',keys:editor.preferences.keys},location.origin);}}
    if(catalogue.length){$('map').value=editor.map;editor.mapTitle=title(catalogue.find(map=>map.id===editor.map));}
    $('draft-state').textContent=editor.dirty?t('修改尚未保存','Unsaved changes'):t('网页配装已保存','Browser loadout saved');
  }
  $('language').onchange=event=>{language=event.target.value;localStorage.setItem('starbreak-web-language',language);editor.remember();translate();};
  $('map').onchange=()=>{const row=catalogue.find(map=>map.id===$('map').value);editor.mapTitle=title(row);editor.setMap(row.id);if(!liveMap)$('arena-title').textContent=title(row);};
  function setBusy(value){
    busy=value;for(const element of document.querySelectorAll('dialog input,dialog select,dialog button'))if(element.id!=='close-editor')element.disabled=value;
    $('open-editor').disabled=value;
    if(!value)editor.render();
  }
  $('start').onclick=()=>startEncounter($('map').value,editor.shell,true);
  async function startEncounter(map,shell,saveDraft){
    if(busy)return;setBusy(true);$('error').hidden=true;$('editor-error').hidden=true;$('status').textContent=t('正在启动…','Starting…');
    try{
      if(saveDraft)await editor.save();$('equipment-dialog').close();$('stage').replaceChildren();
      let assets=await editor.post('/api/prepare',{map});
      while(!assets.assets_ready){
        if(assets.asset_error)throw new Error(assets.asset_error);
        $('status').textContent=t('准备游戏资源 ','Preparing assets ')+`${assets.assets_cached}/${assets.assets_total}`;
        await new Promise(resolve=>setTimeout(resolve,750));assets=await(await fetch('/api/info')).json();
      }
      await editor.post('/api/start',{shell,map});liveMap=map;runMap=map;$('arena-title').textContent=title(catalogue.find(row=>row.id===map));
      const frame=document.createElement('iframe');frame.src='/game.html?attempt='+Date.now();frame.title=t('StarBreak 游戏','StarBreak game');frame.allow='autoplay; fullscreen';
      $('stage').replaceChildren(frame);$('status').textContent=t('加载客户端…','Loading client…');
    }catch(error){$('error').textContent=error.message;$('error').hidden=false;$('status').textContent=t('启动失败','Failed');if($('equipment-dialog').open)editor.notice(error.message);}
    finally{setBusy(false);}
  }
  addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==document.querySelector('iframe')?.contentWindow)return;
    if(event.data.type==='practice-shortcut'){shortcut(event.data.action);return;}
    if(event.data.type==='player-state')$('status').textContent='';
    if(event.data.type==='bridge-ack'&&event.data.ack.map){liveMap=event.data.ack.map;$('arena-title').textContent=title(catalogue.find(map=>map.id===liveMap));}
    if(event.data.type==='game-error'){$('error').textContent=event.data.message;$('error').hidden=false;}
  });
  async function init(){
    setBusy(true);translate();
    try{
      const [info]=await Promise.all([fetch('/api/info').then(response=>response.json()),editor.load()]);catalogue=info.maps;
      if(editor.preferences.language){language=editor.preferences.language;localStorage.setItem('starbreak-web-language',language);}
      const saved=editor.profile.settings.map;editor.map=catalogue.some(map=>map.id===saved&&map.ready)?saved:'hulk_mid';
      mapOptions();$('map').value=editor.map;translate();
    }catch(error){$('error').textContent=error.message;$('error').hidden=false;}
    finally{setBusy(false);}
  }
  init();
})();
