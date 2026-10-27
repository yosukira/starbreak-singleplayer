/* Hold actions fire once per press; typing, blur and hidden tabs cancel timers. */
(function(scope){
  class PracticeShortcuts {
    constructor(target,bindings,action,blocked=()=>false,gameplayKey=()=>false){
      this.target=target;this.action=action;this.blocked=blocked;this.gameplayKey=gameplayKey;this.held=new Map();this.update(bindings);
      target.addEventListener('keydown',event=>this.down(event));
      target.addEventListener('keyup',event=>this.up(event.code));
      target.addEventListener('blur',()=>this.clear());
      target.document?.addEventListener('visibilitychange',()=>{if(target.document.hidden)this.clear();});
    }
    update(bindings){this.clear();this.bindings={...bindings};}
    clear(){for(const timer of this.held?.values()||[])clearTimeout(timer);this.held?.clear();}
    code(name){if(!name||name==='None'||name==='Auto')return null;if(/^[A-Z]$/.test(name))return 'Key'+name;if(/^\d$/.test(name))return 'Digit'+name;return name;}
    down(event){
      if(event.repeat||this.held.has(event.code)||this.blocked()||event.ctrlKey||event.metaKey||event.altKey)return;
      if(event.target?.closest?.('input,textarea,select,[contenteditable="true"]'))return;
      for(const name of ['editor_toggle','editor_toggle_alt','restart','wf','dw','fab','ic','zoom_in','zoom_out']){
        const key=this.code(this.bindings[name]);
        if(!key||!(key===event.code||key.startsWith('Digit')&&event.code==='Numpad'+key.slice(5)||key==='Equal'&&event.code==='NumpadAdd'||key==='Minus'&&event.code==='NumpadSubtract'))continue;
        // The original Controls bindings own gameplay keys. In particular,
        // KP7/KP8/KP9 attacks must not alias the practice class-change holds.
        if(this.gameplayKey(event))return;
        event.preventDefault();
        if(name.startsWith('zoom_')){this.held.set(event.code,null);this.action(name);}
        else if(name.startsWith('editor_toggle')){this.held.set(event.code,null);this.action('editor');}
        else this.held.set(event.code,setTimeout(()=>{if(!this.blocked()&&!this.gameplayKey(event))this.action(name);this.held.set(event.code,null);},this.bindings.hold_seconds*1000));
        break;
      }
    }
    up(code){if(this.held.has(code)){clearTimeout(this.held.get(code));this.held.delete(code);}}
  }
  if(typeof module!=='undefined')module.exports=PracticeShortcuts;
  else scope.PracticeShortcuts=PracticeShortcuts;
})(typeof window==='undefined'?{}:window);
