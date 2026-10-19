(() => {
  const $=id=>document.getElementById(id);
  let language=localStorage.getItem('starbreak-web-language')||(navigator.language.startsWith('zh')?'zh':'en');
  if(!['en','zh','zh-affixes'].includes(language))language='en';
  let catalogue=[],busy=false,liveMap=null,runMap=null,replayRecord=null,replayPaused=false,lastReplayState=null,battleEnded=false;
  const t=(zh,en)=>language.startsWith('zh')?zh:en;
  let loading=null,loadingPercent=0,loadingHide;
  function finishLoading(){if(!loading)return;loadingPercent=100;renderLoading();clearTimeout(loadingHide);loadingHide=setTimeout(()=>showLoading(null),220);}
  function showLoading(phase,current=null,total=null){
    clearTimeout(loadingHide);if(!loading)loadingPercent=0;
    const defaults={environment:0,starting:0,assets:5,client:70,replay:0};
    const computed=Number.isFinite(current)&&total>0?(phase==='assets'?5+60*current/total:current/total*100):defaults[phase]||0;
    loadingPercent=Math.max(loadingPercent,computed);
    loading=phase?{phase,current,total}:null;
    renderLoading();
  }
  function renderLoading(){
    $('loading-overlay').hidden=!loading;
    $('stage').setAttribute('aria-busy',String(!!loading));
    $('status').hidden=!!loading;
    if(!loading)return;
    const labels={
      environment:[t('正在准备游戏环境','Preparing game environment'),t('首次加载需要下载运行资源，之后将缓存在本机。','The first visit downloads runtime resources, which are cached on this device.')],
      starting:[t('正在启动战斗','Starting battle'),t('准备完成后将自动进入游戏。','The game will start as soon as it is ready.')],
      assets:[t('正在准备游戏资源','Preparing game assets'),t('资源将缓存在本机，下次启动更快。','Assets are cached locally for faster starts next time.')],
      client:[t('正在加载游戏','Loading game'),t('准备完成后将自动进入游戏。','The game will start as soon as it is ready.')],
      replay:[t('正在加载录像','Loading replay'),t('正在准备这一场的战斗数据。','Preparing this battle’s recording.')]
    };
    const [heading,detail]=labels[loading.phase];
    $('loading-title').textContent=heading;$('loading-detail').textContent=detail;
    const known=Number.isFinite(loading.current)&&Number.isFinite(loading.total)&&loading.total>0;
    const percent=Math.floor(Math.max(0,Math.min(100,loadingPercent)));
    const stages={download:t('正在下载运行资源','Downloading runtime resources'),storage:t('正在读取本地存档','Reading local saves'),initialize:t('正在初始化游戏','Initializing game'),ready:t('准备完成','Ready'),assets:t('正在下载地图资源','Downloading map assets'),engine:t('正在启动游戏引擎','Starting game engine'),world:t('正在载入地图','Loading map')};
    $('loading-count').textContent=known&&loading.phase==='assets'?`${stages.assets} · ${loading.current} / ${loading.total}`:(stages[loading.step]||heading);
    $('loading-percent').textContent=`${percent}%`;
    const bar=$('loading-progress');
    bar.setAttribute('aria-label',heading);
    bar.classList.remove('indeterminate');bar.setAttribute('aria-valuenow',String(percent));
    $('loading-meter').style.width=`${percent}%`;
  }
  addEventListener('starbreak-loading',event=>{const p=event.detail;if(!loading||p.phase==='environment'&&loading.phase!=='environment')return;loadingPercent=Math.max(loadingPercent,p.percent||0);loading={...loading,...p};renderLoading();});
  const editor=new WebEquipment(()=>language,()=>sync());
  const records=new BattleRecords($('records-page'),()=>language,()=>editor.data?.items||[],startReplay);
  function playbackControl(action,extra={}){document.querySelector('iframe')?.contentWindow.postMessage({type:'replay-control',action,...extra},location.origin);}
  function updatePause(){ $('replay-pause').textContent=replayPaused?t('继续','Resume'):t('暂停','Pause'); }
  function replayStatus(data){
    lastReplayState=data;
    const state=data.state;
    $('replay-takeover').disabled=state==='failed'||state==='ended'||state==='taking-over'||!data.takeover;
    if(state==='taking-over'){
      showLoading(null);$('replay-pause').disabled=$('replay-speed').disabled=true;
      $('status').textContent='';
      $('replay-progress').textContent=t('正在接管…','Taking over…')+(Number.isFinite(data.progress)?` ${data.progress}%`:'');return;
    }
    if(state==='takeover-failed'){showLoading(null);$('replay-pause').disabled=$('replay-speed').disabled=false;$('status').textContent=t('暂时无法接管，可继续回放','Takeover unavailable; replay can continue');$('replay-progress').title=data.message||'';return;}
    if(state==='failed'||state==='ended')showLoading(null);else if(data.frame>0)finishLoading();
    $('replay-pause').disabled=$('replay-speed').disabled=state==='failed'||state==='ended';
    if(state==='failed'){
      $('status').textContent=t('录像停止','Replay stopped');
      $('replay-progress').textContent=t('录像校验失败，请确认与当前版本兼容。','Replay validation failed. Check compatibility with this version.');
      $('replay-progress').title=data.message||'';
    }else{
      $('replay-progress').title='';
      $('status').textContent=state==='ended'?t('录像播放结束','Replay completed'):state==='paused'?t('已暂停','Paused'):'';
      const format=seconds=>Math.floor(seconds/60)+':'+String(Math.floor(seconds%60)).padStart(2,'0');
      if(Number.isFinite(data.seconds))$('replay-progress').textContent=format(data.seconds)+' / '+format(data.duration);
    }
  }
  $('replay-pause').onclick=()=>{replayPaused=!replayPaused;playbackControl('pause',{paused:replayPaused});updatePause();};
  $('replay-speed').onchange=event=>playbackControl('speed',{speed:Number(event.target.value)});
  $('replay-back').onclick=()=>{replayPaused=true;playbackControl('pause',{paused:true});updatePause();setTab(true);};
  $('replay-restart').onclick=()=>{if(replayRecord)startReplay(replayRecord);};
  $('replay-takeover').onclick=()=>playbackControl('takeover');
  function setTab(history){
    if(history&&replayRecord&&!replayPaused){replayPaused=true;playbackControl('pause',{paused:true});updatePause();}
    document.querySelector('.game-layout').hidden=history;
    $('tab-battle').classList.toggle('selected',!history);$('tab-records').classList.toggle('selected',history);
    if(history)records.show();else{records.hide();document.querySelector('iframe')?.contentWindow.focus();}
  }
  $('tab-records').onclick=()=>setTab(true);$('tab-battle').onclick=()=>setTab(false);
  addEventListener('pagehide',()=>editor.flush());
  const title=map=>map?.names?.[language]||map?.title||'StarBreak';
  function mapOptions(){
    if(!catalogue.length)return;
    const selected=$('map').value||editor.map;
    $('map').replaceChildren(...catalogue.map(map=>{const option=new Option(title(map)+(map.ready?'':t('（适配中）',' (in progress)')),map.id);option.disabled=!map.ready;return option;}));
    $('map').value=selected;
  }
  function translate(){
    document.documentElement.lang=language.startsWith('zh')?'zh':'en';$('language').value=language;
    document.querySelectorAll('[data-en]').forEach(element=>element.textContent=element.dataset[language.startsWith('zh')?'zh':'en']);
    records.render(true);
    mapOptions();editor.mapTitle=title(catalogue.find(map=>map.id===editor.map));editor.render();sync();
    $('arena-title').textContent=replayRecord?.map_title||title(catalogue.find(map=>map.id===(liveMap||editor.map)));updatePause();
    if(replayRecord&&lastReplayState)replayStatus(lastReplayState);
    else if(battleEnded)$('status').textContent=t('本场已结束','Battle ended');
    renderLoading();
  }
  let shortcutFingerprint='';
  const shortcuts=new PracticeShortcuts(window,{},shortcut,()=>busy);
  function shortcut(action){
    if(action.startsWith('zoom_')){if(!records.visible&&!$('equipment-dialog').open)document.querySelector('iframe')?.contentWindow.Module?.webZoom?.step(action==='zoom_in'?1:-1);return;}
    if(action==='editor'){if($('equipment-dialog').open)$('equipment-dialog').close();else $('open-editor').click();return;}
    if(busy||records.visible||$('equipment-dialog').open||!liveMap)return;
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
    if(busy)return;battleEnded=false;setTab(false);setBusy(true);$('error').hidden=true;$('editor-error').hidden=true;$('status').textContent=t('正在启动…','Starting…');
    showLoading('starting');
    try{
      if(saveDraft)await editor.save();
      await document.querySelector('iframe')?.contentWindow.Module?.gamePreferences?.flush();
      await document.querySelector('iframe')?.contentWindow.Module?.clientReplay?.finish();
      replayRecord=null;lastReplayState=null;$('replay-controls').hidden=true;
      $('equipment-dialog').close();$('stage').replaceChildren();
      let assets=await editor.post('/api/prepare',{map});
      while(!assets.assets_ready){
        if(assets.asset_error)throw new Error(assets.asset_error);
        $('status').textContent=t('准备游戏资源 ','Preparing assets ')+`${assets.assets_cached}/${assets.assets_total}`;
        showLoading('assets',assets.assets_cached,assets.assets_total);
        await new Promise(resolve=>setTimeout(resolve,750));assets=await(await fetch('/api/info')).json();
      }
      await editor.post('/api/start',{shell,map});liveMap=map;runMap=map;$('arena-title').textContent=title(catalogue.find(row=>row.id===map));
      const frame=document.createElement('iframe');frame.src=(globalThis.StarBreakBrowser?.assetURL('/game.html')||'/game.html')+'?attempt='+Date.now();frame.title=t('StarBreak 游戏','StarBreak game');frame.allow='autoplay; fullscreen';
      $('stage').replaceChildren(frame);$('status').textContent=t('加载客户端…','Loading client…');
      showLoading('client');
    }catch(error){showLoading(null);$('error').textContent=error.message;$('error').hidden=false;$('status').textContent=t('启动失败','Failed');if($('equipment-dialog').open)editor.notice(error.message);}
    finally{setBusy(false);}
  }
  async function startReplay(record){
    if(busy)return;
    battleEnded=false;
    setBusy(true);setTab(false);$('error').hidden=true;
    showLoading('replay');
    try{
      const check=await fetch('/api/replay/check?id='+encodeURIComponent(record.replay.id));
      if(!check.ok)throw Error(t('录像无法载入，请确认文件完整且与当前版本兼容。','Could not load replay. Check that it is complete and compatible with this version.'));
      await document.querySelector('iframe')?.contentWindow.Module?.gamePreferences?.flush();
      await document.querySelector('iframe')?.contentWindow.Module?.clientReplay?.finish();
      replayRecord=record;replayPaused=false;lastReplayState=null;liveMap=runMap=null;
      $('arena-title').textContent=record.map_title;$('status').textContent=t('加载录像…','Loading replay…');
      $('replay-controls').hidden=false;$('replay-speed').value='1';$('replay-progress').textContent='';$('replay-progress').title='';$('replay-pause').disabled=$('replay-speed').disabled=false;updatePause();
      const frame=document.createElement('iframe');frame.src=(globalThis.StarBreakBrowser?.assetURL('/game.html')||'/game.html')+'?replay='+encodeURIComponent(record.replay.id)+'&record='+encodeURIComponent(record.id);frame.title=t('StarBreak 录像回放','StarBreak replay');frame.allow='autoplay; fullscreen';
      $('stage').replaceChildren(frame);
    }catch(error){showLoading(null);$('error').textContent=error.message;$('error').hidden=false;}
    finally{setBusy(false);}
  }
  addEventListener('message',event=>{
    if(event.origin!==location.origin||event.source!==document.querySelector('iframe')?.contentWindow)return;
    if(event.data.type==='replay-status'&&event.data.state==='closed'&&!replayRecord&&!busy){
      battleEnded=true;
      showLoading(null);
      $('status').textContent=t('本场已结束','Battle ended');
      const frame=document.querySelector('iframe'),runtime=frame?.contentWindow.Module;
      void Promise.allSettled([runtime?.clientReplay?.finish(),runtime?.gamePreferences?.flush()]).then(()=>{
        if(document.querySelector('iframe')!==frame||replayRecord)return;
        const welcome=document.createElement('div');welcome.id='welcome';
        const mark=document.createElement('div');mark.className='cross';mark.textContent='✦';
        const heading=document.createElement('h2'),help=document.createElement('p');
        heading.dataset.zh='本场已结束';heading.dataset.en='Battle ended';heading.textContent=t(heading.dataset.zh,heading.dataset.en);
        help.dataset.zh='前往战斗记录查看回放，或打开“装备与设置”开始新一局。';
        help.dataset.en='View the replay in Battle records, or open Equipment & settings to start another battle.';
        help.textContent=t(help.dataset.zh,help.dataset.en);
        welcome.append(mark,heading,help);$('stage').replaceChildren(welcome);
      });return;
    }
    if(event.data.type==='loading-progress'){if(loading){loadingPercent=Math.max(loadingPercent,event.data.percent||0);loading.step=event.data.step;renderLoading();}return;}
    if(event.data.type==='practice-shortcut'){shortcut(event.data.action);return;}
    if(event.data.type==='replay-status'&&replayRecord){
      const state=event.data.state;
      if(state==='ready'){playbackControl('speed',{speed:Number($('replay-speed').value)});playbackControl('pause',{paused:replayPaused});return;}
      if(state==='taken-over'){
        showLoading(null);
        liveMap=runMap=replayRecord.map;editor.selectClass(replayRecord.shell);replayRecord=null;lastReplayState=null;
        $('replay-controls').hidden=true;$('status').textContent=t('已接管','You are in control');return;
      }
      replayStatus(event.data);
      return;
    }
    if(event.data.type==='game-preferences-status'){
      $('game-settings-notice').hidden=event.data.saved;
      $('game-settings-notice').textContent=event.data.saved?'':t('游戏设置暂未写入本地文件，已保留在此浏览器；请检查本地服务是否仍在运行。','Game settings could not be saved to the local folder. They remain in this browser; check that the local service is running.');return;
    }
    if(event.data.type==='player-state'){
      finishLoading();$('status').textContent='';
    }
    if(event.data.type==='bridge-ack'&&event.data.ack.map){liveMap=event.data.ack.map;$('arena-title').textContent=title(catalogue.find(map=>map.id===liveMap));}
    if(event.data.type==='game-error'){showLoading(null);$('error').textContent=event.data.message;$('error').hidden=false;}
  });
  async function init(){
    setBusy(true);if(globalThis.StarBreakBrowser){showLoading('environment');const p=StarBreakBrowser.progress;if(p){loadingPercent=p.percent;loading.step=p.step;}}translate();
    try{
      const [info]=await Promise.all([fetch('/api/info').then(response=>response.json()),editor.load()]);catalogue=info.maps;window.STARBREAK_RECORD_STAT_LABELS=Object.fromEntries((editor.data?.stats||[]).map(s=>[s.id,s]));
      if(editor.preferences.language){language=editor.preferences.language;localStorage.setItem('starbreak-web-language',language);}
      const saved=editor.profile.settings.map;editor.map=catalogue.some(map=>map.id===saved&&map.ready)?saved:'hulk_mid';
      mapOptions();$('map').value=editor.map;translate();
    }catch(error){showLoading(null);$('error').textContent=error.message;$('error').hidden=false;}
    finally{if(loading?.phase==='environment')finishLoading();setBusy(false);}
  }
  init();
})();
