(() => {
  const $ = id => document.getElementById(id);
  let shell=1, catalogue=[], language=localStorage.getItem('starbreak-web-language') || (navigator.language.startsWith('zh')?'zh':'en');
  if(!['en','zh'].includes(language))language='en';
  function translate(){document.documentElement.lang=language;$('language').value=language;document.querySelectorAll('[data-en]').forEach(e=>e.textContent=e.dataset[language]);}
  $('language').onchange=e=>{language=e.target.value;localStorage.setItem('starbreak-web-language',language);translate();};
  function weapons(){const items=catalogue.filter(v=>v.usableby.includes(shell));$('weapon').replaceChildren(...items.map(v=>new Option(v.name,v.id)));}
  document.querySelectorAll('[data-shell]').forEach(b=>b.onclick=()=>{shell=+b.dataset.shell;document.querySelectorAll('[data-shell]').forEach(v=>v.classList.toggle('selected',v===b));weapons();});
  $('start').onclick=async()=>{
    $('start').disabled=true;$('error').hidden=true;$('status').textContent=language==='zh'?'正在启动…':'Starting…';
    $('stage').replaceChildren();
    $('telemetry').textContent=language==='zh'?'等待新一局…':'Waiting for a new attempt…';
    try{
      const response=await fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({shell,map:$('map').value,weapon:+$('weapon').value})});
      const data=await response.json();if(!response.ok)throw new Error(data.error);
      const frame=document.createElement('iframe');frame.src='/game.html?attempt='+Date.now();frame.title='StarBreak battle';frame.allow='autoplay; fullscreen';$('stage').replaceChildren(frame);
      $('status').textContent=language==='zh'?'加载客户端…':'Loading client…';
    }catch(e){$('error').textContent=e.message;$('error').hidden=false;$('status').textContent=language==='zh'?'启动失败':'Failed';}
    finally{$('start').disabled=false;}
  };
  $('fullscreen').onclick=()=>document.querySelector('iframe')?.requestFullscreen().catch(()=>{});
  addEventListener('message',e=>{if(e.origin!==location.origin)return;if(e.data.type==='player-state')$('status').textContent=e.data.player.alive?(language==='zh'?'战斗中':'Live'):(language==='zh'?'本局结束，可重新进入':'Attempt ended — start again');if(e.data.type==='game-error'){$('error').textContent=e.data.message;$('error').hidden=false;}});
  async function status(){try{const data=await(await fetch('/api/status')).json();if(data.feedback&&document.querySelector('iframe')){const p=data.player;$('telemetry').textContent=`HP ${p.hp}/${p.maxhp}  ·  ${language==='zh'?'命中反馈':'Hit feedback'} ${data.damage}  ·  ${language==='zh'?'受伤':'Damage taken'} ${data.received_damage}`;if(!p.alive)$('status').textContent=language==='zh'?'本局结束，可重新进入':'Attempt ended — start again';}}catch(_){}setTimeout(status,1000);}
  translate();fetch('/api/info').then(r=>r.json()).then(data=>{catalogue=data.items;data.maps.forEach(v=>$('map').add(new Option(v.title,v.id)));$('arena-title').textContent=data.maps[0].title;weapons();status();}).catch(e=>{$('error').textContent=e.message;$('error').hidden=false;});
})();
