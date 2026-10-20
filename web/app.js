(() => {
  const $=id=>document.getElementById(id);
  const desktop=new URLSearchParams(location.search).get('desktop')==='1';
  if(desktop){
    document.body.classList.add('desktop-workspace');
    document.querySelector('.main-tabs').prepend($('tab-equipment'));
    $('tab-visuals').hidden=true;$('tab-equipment').hidden=false;$('tab-guide').hidden=false;$('tab-battle').hidden=true;$('open-editor').hidden=true;
    $('editor-keys-tab').dataset.en='Keys';$('editor-keys-tab').dataset.zh='按键设置';
    $('tab-equipment').onclick=()=>setTab('equipment');
    $('close-editor').onclick=()=>void window.pywebview?.api?.hide_window();
  }
  let language=localStorage.getItem('starbreak-web-language')||(navigator.language.startsWith('zh')?'zh':'en');
  if(!['en','zh','zh-affixes'].includes(language))language='en';
  let catalogue=[],busy=false,liveMap=null,runMap=null,replayRecord=null,replayPaused=false,lastReplayState=null,battleEnded=false,seekDragging=false,pendingSeek=0;
  let activeTab='battle';
  const t=(zh,en)=>language.startsWith('zh')?zh:en;
  if(desktop){
    async function health(){
      const box=$('native-connection-notice');
      try{
        const response=await fetch('/api/native/health',{cache:'no-store'});if(!response.ok)throw Error('unavailable');
        const value=await response.json();
        const labels={
          connected:t('游戏反馈已连接','Game feedback connected'),
          starting:t('正在连接游戏反馈…','Connecting game feedback…'),
          waiting:t('尚未收到游戏反馈。若怪物不受伤或 Gen 无响应，请重新运行 start.cmd。','No game feedback yet. If enemies cannot be damaged or panels do not respond, restart start.cmd.'),
          missing:t('游戏插件未启动，请重新运行 start.cmd，并查看本次启动日志。','The game plugin did not start. Restart start.cmd and check this session’s logs.'),
          stale:t('游戏反馈已中断，请重新运行 start.cmd。','Game feedback stopped. Restart start.cmd.'),
          disabled:t('游戏反馈已关闭，请重新运行 start.cmd。','Game feedback is disabled. Restart start.cmd.'),
          error:t('游戏反馈启动失败，请查看本次启动日志。','Game feedback failed to start. Check this session’s logs.'),
          degraded:t('伤害反馈不可用，可能有插件冲突；请查看本次启动日志。','Damage feedback is unavailable; another plugin may conflict. Check this session’s logs.')
        };
        box.textContent=labels[value.state]||labels.waiting;
        if(value.state==='connected'&&!value.replay_ready)box.textContent+=t('；原生录像组件未能启动。','; native replay is unavailable.')+(value.recording_error?' '+value.recording_error:'');
        if(value.state==='connected'&&value.replay_ready){
          box.textContent+=value.recording_error?t('；录制失败：','; recording failed: ')+value.recording_error:
            value.recording===false?t('；当前未录制（开启后需保存并开始新局）。','; not recording this attempt (enable recording, then Save and start).'):
            t('；正在录制，单场结束后保存录像。','; recording; replay is saved when this battle ends.');
        }
        box.hidden=false;
      }catch(_){box.textContent=t('尚未连接原生游戏，请从 start.cmd 启动。','Native game not connected. Launch it with start.cmd.');box.hidden=false;}
      setTimeout(health,2000);
    }
    void health();
  }
  let loading=null,loadingPercent=0,loadingHide;
  function finishLoading(){if(!loading||loadingHide)return;loadingPercent=100;renderLoading();loadingHide=setTimeout(()=>showLoading(null),220);}
  function showLoading(phase,current=null,total=null){
    clearTimeout(loadingHide);loadingHide=null;if(!loading)loadingPercent=0;
    const defaults={environment:0,starting:0,assets:5,client:70,replay:0};
    const computed=Number.isFinite(current)&&total>0?(phase==='assets'?5+60*current/total:current/total*100):defaults[phase]||0;
    loadingPercent=Math.max(loadingPercent,computed);
    loading=phase?{phase,current,total}:null;
    updateLocks();
    renderLoading();
  }
  function renderLoading(){
    $('loading-overlay').hidden=!loading;
    $('stage').setAttribute('aria-busy',String(!!loading));
    $('status').hidden=!!loading;
    if(!loading)return;
    const labels={
      environment:loading.updating?[t('正在更新网页版','Updating web version'),t('仅下载发生变化的运行文件，已有资源继续复用。','Only changed runtime files are downloaded; existing resources are reused.')]:[t('正在准备游戏环境','Preparing game environment'),loading.step==='download'?t('正在下载首次使用所需的运行资源。','Downloading resources needed for first use.'):t('正在读取本机资源并初始化游戏。','Reading local resources and initializing the game.')],
      starting:[t('正在启动战斗','Starting battle'),t('准备完成后将自动进入游戏。','The game will start as soon as it is ready.')],
      assets:[t('正在准备游戏资源','Preparing game assets'),t('资源将缓存在本机，下次启动更快。','Assets are cached locally for faster starts next time.')],
      client:[t('正在加载游戏','Loading game'),t('准备完成后将自动进入游戏。','The game will start as soon as it is ready.')],
      replay:[t('正在加载录像','Loading replay'),t('正在准备这一场的战斗数据。','Preparing this battle’s recording.')]
    };
    const [heading,detail]=labels[loading.phase];
    $('loading-title').textContent=heading;$('loading-detail').textContent=detail;
    const known=Number.isFinite(loading.current)&&Number.isFinite(loading.total)&&loading.total>0;
    const percent=Math.floor(Math.max(0,Math.min(100,loadingPercent)));
    const stages={checking:t('正在检查网页版本','Checking web version'),cached:t('正在读取本地缓存','Reading cached resources'),update:t('正在下载更新文件','Downloading update files'),download:t('正在下载运行资源','Downloading runtime resources'),'reading-replay':t('正在读取并校验录像','Reading and validating replay'),reconstructing:t('正在还原录像起点','Restoring replay start'),storage:t('正在读取本地存档','Reading local saves'),initialize:t('正在初始化游戏','Initializing game'),ready:t('准备完成','Ready'),assets:t('正在准备地图资源','Preparing map assets'),engine:t('正在启动游戏引擎','Starting game engine'),world:t('正在载入地图','Loading map')};
    $('loading-count').textContent=known&&loading.phase==='assets'?`${stages.assets} · ${loading.current} / ${loading.total}`:(stages[loading.step]||heading);
    $('loading-percent').textContent=`${percent}%`;
    const bar=$('loading-progress');
    bar.setAttribute('aria-label',heading);
    bar.classList.remove('indeterminate');bar.setAttribute('aria-valuenow',String(percent));
    $('loading-meter').style.width=`${percent}%`;
  }
  addEventListener('starbreak-loading',event=>{const p=event.detail;if(!loading||p.phase==='environment'&&loading.phase!=='environment')return;loadingPercent=Math.max(loadingPercent,p.percent||0);loading={...loading,...p};renderLoading();});
  const editor=new WebEquipment(()=>language,()=>sync());
  const records=new BattleRecords($('records-page'),()=>language,()=>editor.data?.items||[],startReplay,()=>catalogue);
  records.identity=()=>editor.preferences?.character_name||'';
  const visuals=desktop?null:new StarBreakVisuals.Page($('visuals-page'),()=>language);
  const guide=new BattleGuide($('guide-page'),()=>language,{
    desktop,configure:()=>desktop?setTab('equipment'):$('open-editor').click(),records:()=>setTab(true),battle:()=>setTab(false),language:setLanguage
  });
  function playbackControl(action,extra={}){
    if(replayRecord?.replay?.engine==='native'){
      void fetch('/api/native/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,...extra})}).then(async response=>{if(!response.ok)throw Error((await response.json()).error);}).catch(error=>{$('replay-progress').textContent=error.message;});return;
    }
    document.querySelector('iframe')?.contentWindow.postMessage({type:'replay-control',action,...extra},location.origin);
  }
  let nativePoll=0;
  async function pollNativeReplay(){
    clearTimeout(nativePoll);if(replayRecord?.replay?.engine!=='native')return;
    try{
      const response=await fetch('/api/native/status',{cache:'no-store'});const value=await response.json();
      if(!response.ok)throw Error(value.error);
      if(value.state==='live'){
        replayRecord=null;$('replay-controls').hidden=true;$('status').textContent=t('已在 Windows 客户端接管','You are in control in the Windows client');
        const notice=$('stage').querySelector('.record-native-playback');if(notice)notice.textContent=t('已接管，正在游戏客户端中战斗。按 F8 或 ~ 隐藏/显示此窗口。','You are in control in the game client. Press F8 or ~ to hide/show this window.');return;
      }
      replayStatus({...value,state:value.paused&&value.state==='playing'?'paused':value.state,message:value.message||value.error,frame:value.seconds>0?1:0});
    }catch(error){$('replay-progress').textContent=error.message;}
    if(replayRecord?.replay?.engine==='native')nativePoll=setTimeout(pollNativeReplay,400);
  }
  function updatePause(){ $('replay-pause').textContent=replayPaused?t('继续','Resume'):t('暂停','Pause'); }
  function replayStatus(data){
    lastReplayState=data;
    const state=data.state;
    if(state==='preparing'&&data.stage==='reconstructing'&&replayRecord?.replay?.engine!=='native'){
      if(loading){loading.step='reconstructing';loadingPercent=Math.max(loadingPercent,70+25*(data.progress||0));renderLoading();}
      $('replay-progress').textContent=t('正在还原录像起点','Restoring replay start')+` · ${Math.floor(100*(data.progress||0))}%`;
      $('replay-takeover').disabled=true;return;
    }
    if(replayRecord?.replay?.engine==='native'&&state==='starting'){
      const labels={reading:t('正在读取录像','Reading replay'),download:t('正在传输录像到客户端','Transferring replay to client'),indexing:t('正在准备回放索引','Preparing replay index'),reconstructing:t('正在还原录像起点','Restoring replay start')};
      const text=labels[data.stage]||t('正在准备客户端回放','Preparing native replay');
      const percent=Number.isFinite(data.progress)?Math.floor(100*Math.max(0,Math.min(1,data.progress))):null;
      $('status').textContent=text;$('replay-progress').textContent=text+(percent!==null?` · ${percent}%`:'');
      $('replay-pause').disabled=$('replay-speed').disabled=$('replay-seek').disabled=$('replay-takeover').disabled=true;
      const notice=$('stage').querySelector('.record-native-playback');if(notice)notice.textContent=$('replay-progress').textContent;
      return;
    }
    if(replayRecord?.replay?.engine==='native'&&state==='preparing'){
      const restoring=data.stage==='reconstructing';
      $('status').textContent=restoring?t('正在还原录像起点','Restoring replay start'):t('正在准备客户端资源','Preparing native-client assets');
      $('replay-progress').textContent=restoring?`${Math.floor(100*Math.max(0,Math.min(1,data.progress||0)))}%`:`${data.assets_loaded||0} / ${data.assets_total||0}`;
      const notice=$('stage').querySelector('.record-native-playback');if(notice)notice.textContent=$('status').textContent+' · '+$('replay-progress').textContent;
      $('replay-takeover').disabled=true;return;
    }
    const seek=$('replay-seek');
    seek.disabled=state==='taking-over'||state==='failed';
    if(Number.isFinite(data.duration))seek.max=String(data.duration);
    if(!seekDragging&&Number.isFinite(data.seconds))seek.value=String(data.seconds);
    $('replay-takeover').disabled=state==='failed'||state==='ended'||state==='taking-over'||!data.takeover;
    if(state==='seeking'){
      $('replay-pause').disabled=$('replay-speed').disabled=false;
      $('replay-progress').textContent=t('正在跳转…','Seeking…')+' '+Math.floor(data.seconds||0)+' / '+Math.floor(data.target||0)+'s';
      if(data.frame>0)finishLoading();return;
    }
    if(state==='taking-over'){
      showLoading(null);$('replay-pause').disabled=$('replay-speed').disabled=true;
      $('status').textContent='';
      $('replay-progress').textContent=t('正在接管…','Taking over…')+(Number.isFinite(data.progress)?` ${data.progress}%`:'');return;
    }
    if(state==='takeover-failed'){showLoading(null);$('replay-pause').disabled=$('replay-speed').disabled=false;$('status').textContent=t('暂时无法接管，可继续回放','Takeover unavailable; replay can continue');$('replay-progress').title=data.message||'';$('replay-progress').textContent=data.message||t('接管失败','Takeover failed');return;}
    if(state==='failed'||state==='ended')showLoading(null);else if(data.frame>0)finishLoading();
    $('replay-pause').disabled=$('replay-speed').disabled=state==='failed'||state==='ended';
    if(state==='failed'){
      $('status').textContent=t('录像停止','Replay stopped');
      $('replay-progress').textContent=t('回放中断：','Replay interrupted: ')+(data.message||data.error||t('请查看本次启动日志','See this session’s log'));
      $('replay-progress').title=data.message||'';
    }else{
      $('replay-progress').title='';
      $('status').textContent=state==='ended'?t('录像播放结束','Replay completed'):state==='paused'?t('已暂停','Paused'):'';
      const format=seconds=>Math.floor(seconds/60)+':'+String(Math.floor(seconds%60)).padStart(2,'0');
      if(Number.isFinite(data.seconds))$('replay-progress').textContent=format(data.seconds)+' / '+format(data.duration);
    }
  }
  $('replay-seek').oninput=event=>{seekDragging=true;$('replay-progress').textContent=Math.floor(Number(event.target.value)/60)+':'+String(Math.floor(Number(event.target.value)%60)).padStart(2,'0');};
  $('replay-seek').onchange=event=>{
    seekDragging=false;if(!replayRecord||busy)return;
    const target=Number(event.target.value),current=lastReplayState?.seconds||0;
    if(replayRecord.replay?.engine==='native')playbackControl('seek',{seconds:target,paused:replayPaused});
    else if(target<current||lastReplayState?.state==='ended')void startReplay(replayRecord,target,replayPaused,Number($('replay-speed').value));
    else playbackControl('seek',{seconds:target,paused:replayPaused});
  };
  $('replay-pause').onclick=()=>{replayPaused=!replayPaused;playbackControl('pause',{paused:replayPaused});updatePause();};
  $('replay-speed').onchange=event=>playbackControl('speed',{speed:Number(event.target.value)});
  $('replay-back').onclick=()=>{replayPaused=true;playbackControl('pause',{paused:true});updatePause();setTab(true);};
  $('replay-restart').onclick=()=>{if(replayRecord)startReplay(replayRecord);};
  $('replay-takeover').onclick=()=>playbackControl('takeover');
  function setTab(view){
    if(loading)return;
    activeTab=view==='equipment'&&desktop?'equipment':view===true?'records':view==='guide'?'guide':view==='visuals'&&!desktop?'visuals':'battle';
    if(activeTab!=='battle'&&replayRecord&&!replayPaused){replayPaused=true;playbackControl('pause',{paused:true});updatePause();}
    document.querySelector('.game-layout').hidden=activeTab!=='battle';
    $('guide-page').hidden=activeTab!=='guide';$('visuals-page').hidden=activeTab!=='visuals';
    if(desktop){
      const panel=$('equipment-dialog');
      if(activeTab==='equipment'){editor.render();if(!panel.open)panel.show();}
      else if(panel.open)panel.close();
      $('tab-equipment').classList.toggle('selected',activeTab==='equipment');
      $('tab-equipment').setAttribute('aria-pressed',String(activeTab==='equipment'));
    }
    for(const name of ['guide','battle','records','visuals']){
      $('tab-'+name).classList.toggle('selected',activeTab===name);
      $('tab-'+name).setAttribute('aria-pressed',String(activeTab===name));
    }
    if(activeTab==='records')records.show();else records.hide();
    if(activeTab==='battle')document.querySelector('iframe')?.contentWindow.focus();
    if(activeTab==='guide')guide.render();if(activeTab==='visuals')void visuals.show();
    if(window.history?.replaceState){
      const url=new URL(location.href);url.hash='';
      if(activeTab==='battle')url.searchParams.delete('view');else url.searchParams.set('view',activeTab);
      window.history.replaceState(null,'',url);
    }
  }
  $('tab-visuals').onclick=()=>setTab('visuals');$('tab-guide').onclick=()=>setTab('guide');$('tab-records').onclick=()=>setTab(true);$('tab-battle').onclick=()=>setTab(false);
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
    guide.render();if(activeTab==='visuals')visuals?.render();
    records.render(true);
    mapOptions();editor.mapTitle=title(catalogue.find(map=>map.id===editor.map));editor.render();sync();
    $('arena-title').textContent=replayRecord?.map_title||title(catalogue.find(map=>map.id===(liveMap||editor.map)));updatePause();
    if(replayRecord&&lastReplayState)replayStatus(lastReplayState);
    else if(battleEnded)$('status').textContent=t('本场已结束','Battle ended');
    renderLoading();
  }
  let shortcutFingerprint='';
  const shortcuts=new PracticeShortcuts(window,{},shortcut,()=>busy||!!loading);
  function shortcut(action){
    if(desktop)return; // Native shortcuts are owned by the shared host/game.
    if(action.startsWith('zoom_')){if(activeTab==='battle'&&!$('equipment-dialog').open)document.querySelector('iframe')?.contentWindow.Module?.webZoom?.step(action==='zoom_in'?1:-1);return;}
    if(action==='editor'){if($('equipment-dialog').open)$('equipment-dialog').close();else $('open-editor').click();return;}
    if(busy||loading||activeTab!=='battle'||$('equipment-dialog').open||!liveMap)return;
    const shell={wf:1,dw:2,fab:5,ic:3}[action]||editor.shell;
    editor.selectClass(shell);startEncounter(runMap||editor.map,shell,false);
  }
  function sync(){
    if(!editor.profile)return;
    if(editor.preferences){const signature=JSON.stringify(editor.preferences.keys);if(signature!==shortcutFingerprint){shortcutFingerprint=signature;shortcuts.update(editor.preferences.keys);document.querySelector('iframe')?.contentWindow.postMessage({type:'practice-keys',keys:editor.preferences.keys},location.origin);}}
    if(catalogue.length){$('map').value=editor.map;editor.mapTitle=title(catalogue.find(map=>map.id===editor.map));}
    $('draft-state').textContent=editor.dirty?t('修改尚未保存','Unsaved changes'):t('配装已保存','Loadout saved');
  }
  function setLanguage(value){
    if(!['en','zh','zh-affixes'].includes(value))return;
    language=value;localStorage.setItem('starbreak-web-language',language);editor.remember();translate();
  }
  $('language').onchange=event=>setLanguage(event.target.value);
  $('map').onchange=()=>{const row=catalogue.find(map=>map.id===$('map').value);editor.mapTitle=title(row);editor.setMap(row.id);if(!liveMap)$('arena-title').textContent=title(row);};
  function setBusy(value){
    busy=value;for(const element of document.querySelectorAll('dialog input,dialog select,dialog button'))if(element.id!=='close-editor')element.disabled=value;
    updateLocks();
    if(!value)editor.render();
  }
  function updateLocks(){
    const locked=busy||!!loading;
    for(const id of ['tab-equipment','open-editor','tab-guide','tab-battle','tab-records','language','start','close-editor','replay-back','replay-restart'])$(id).disabled=locked;
    document.querySelector('header')?.setAttribute('aria-busy',String(locked));
    for(const id of ['equipment-dialog','replay-controls','stage'])$(id).inert=locked;
  }
  $('start').onclick=()=>startEncounter($('map').value,editor.shell,true);
  async function startEncounter(map,shell,saveDraft){
    if(desktop){
      if(busy)return;setBusy(true);
      try{
        if(saveDraft)await editor.save();
        await editor.post('/api/start',{map,shell});
        replayRecord=null;lastReplayState=null;$('replay-controls').hidden=true;
        editor.notice(t('已在游戏客户端开始。','Started in the game client.'));
        window.pywebview?.api?.show_native_game?.();
      }catch(error){editor.notice(error.message);}
      finally{setBusy(false);}
      return;
    }
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
  async function startReplay(record,seek=0,paused=false,speed=1){
    if(busy)return;
    battleEnded=false;
    if(desktop)$('tab-battle').hidden=false;
    setBusy(true);setTab(false);$('error').hidden=true;
    showLoading('replay');
    try{
      if(record.replay?.engine==='native'){
        const response=await fetch('/api/native/command',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'watch',record:record.id})});
        if(!response.ok)throw Error((await response.json()).error);
        replayRecord=record;replayPaused=false;lastReplayState=null;
        $('arena-title').textContent=record.map_title;$('replay-controls').hidden=false;
        $('replay-speed').value='1';$('replay-seek').value='0';updatePause();
        const notice=document.createElement('p');notice.className='record-native-playback';notice.textContent=t('正在 Windows 游戏客户端中回放。可在这里暂停、调整倍速、拖动进度或接管；有效的键盘操作也能直接接管。','Playing in the Windows game client. Pause, change speed, seek or take over here. A valid gameplay keyboard press also takes over.');
        $('stage').classList.add('native-stage');$('stage').replaceChildren(notice);finishLoading();
        void pollNativeReplay();window.pywebview?.api?.show_native_game?.();return;
      }
      const check=await fetch('/api/replay/check?id='+encodeURIComponent(record.replay.id)+(globalThis.StarBreakBrowser?'&paged=1':''));
      if(!check.ok)throw Error(t('录像无法载入，请确认文件完整且与当前版本兼容。','Could not load replay. Check that it is complete and compatible with this version.'));
      await document.querySelector('iframe')?.contentWindow.Module?.gamePreferences?.flush();
      await document.querySelector('iframe')?.contentWindow.Module?.clientReplay?.finish();
      replayRecord=record;replayPaused=paused;pendingSeek=seek;seekDragging=false;lastReplayState=null;liveMap=runMap=null;
      $('arena-title').textContent=record.map_title;$('status').textContent=t('加载录像…','Loading replay…');
      $('replay-controls').hidden=false;$('replay-speed').value=String(speed);$('replay-seek').value='0';$('replay-progress').textContent='';$('replay-progress').title='';$('replay-pause').disabled=$('replay-speed').disabled=false;updatePause();
      const frame=document.createElement('iframe');frame.src=(globalThis.StarBreakBrowser?.assetURL('/game.html')||'/game.html')+'?replay='+encodeURIComponent(record.replay.id)+'&record='+encodeURIComponent(record.id);frame.title=t('StarBreak 录像回放','StarBreak replay');frame.allow='autoplay; fullscreen';
      $('stage').classList.remove('native-stage');
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
      if(state==='ready'){playbackControl('speed',{speed:Number($('replay-speed').value)});if(pendingSeek>0){playbackControl('seek',{seconds:pendingSeek,paused:replayPaused});pendingSeek=0;}else playbackControl('pause',{paused:replayPaused});return;}
      if(state==='taken-over'){
        showLoading(null);
        liveMap=runMap=replayRecord.map;editor.selectClass(replayRecord.shell);replayRecord=null;lastReplayState=null;
        $('replay-controls').hidden=true;$('status').textContent=t('已接管','You are in control');document.querySelector('iframe')?.contentWindow.focus();return;
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
  if(desktop)$('close-editor').onclick=()=>void window.pywebview?.api?.hide_window();
  void init().then(()=>{const view=new URLSearchParams(location.search).get('view');showLoading(null);if(desktop)setTab(view==='records'?true:view==='guide'?'guide':'equipment');else if(['records','guide','visuals'].includes(view))setTab(view==='records'?true:view);});
})();
