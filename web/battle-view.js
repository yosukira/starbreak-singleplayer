/* Fullscreen belongs to the persistent arena, so replacing a game iframe
 * for a restart or class change does not remove the fullscreen element. */
(function(scope){
  function create({document,arena,button,enabled=true}){
    function enter(){
      if(!enabled||document.fullscreenElement||!arena.requestFullscreen)return;
      try{return Promise.resolve(arena.requestFullscreen()).catch(()=>{});}catch(_){}
    }
    function leave(){
      if(document.fullscreenElement===arena)try{return Promise.resolve(document.exitFullscreen()).catch(()=>{});}catch(_){}
    }
    if(button){button.hidden=!enabled||!arena.requestFullscreen;button.onclick=()=>document.fullscreenElement===arena?leave():enter();}
    document.addEventListener('fullscreenchange',()=>{
      button?.setAttribute('aria-pressed',String(document.fullscreenElement===arena));
      // The iframe's resize handler owns the actual engine viewport update.
      arena.querySelector('iframe')?.contentWindow.dispatchEvent(new Event('resize'));
    });
    return {enter,leave};
  }
  if(typeof module!=='undefined')module.exports=create;else scope.StarBreakBattleView=create;
})(typeof window==='undefined'?{}:window);
