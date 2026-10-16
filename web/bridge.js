/* Local 1.14.6 browser feedback adapter. No account login or native injection. */
(() => {
  'use strict';
  let socket;
  const keys = new Set();
  let practiceKeys=STARBREAK_WEB_BOOT.keybindings;
  const shortcuts=new PracticeShortcuts(window,practiceKeys,action=>parent.postMessage({type:'practice-shortcut',action},location.origin));
  addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data.type==='practice-keys'){practiceKeys=event.data.keys;shortcuts.update(practiceKeys);}});
  let frame = 0, seq = 0, last = 0, wasInteract = false, events = [], lastPlayer = null, context = {}, worldKey = '';
  const read = (pointer, offset = 0) => Module.HEAP32[(pointer + offset) >> 2] | 0;
  const valid = p => p > 0 && p + 744 < Module.HEAP32.byteLength && !(p & 3);
  addEventListener('keydown', e => { keys.add(e.code); if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Space','Tab'].includes(e.code)) e.preventDefault(); });
  addEventListener('keyup', e => keys.delete(e.code));
  addEventListener('blur', () => keys.clear());
  const receive = e => {
    try { const ack = JSON.parse(e.data); if(ack.map)context=ack; parent.postMessage({type:'bridge-ack',ack}, location.origin); }
    catch (_) {}
  };
  const connect = () => {
    socket = new WebSocket('ws://'+location.hostname+':'+STARBREAK_WEB_BOOT.wsPort+'/telemetry', 'binary');
    socket.onmessage = receive;
    socket.onclose = () => setTimeout(connect, 500);
  };
  connect();
  Module.webFrame = world => {
    try {
      frame++;
      const pointer = read(world, 672), subworld = read(world, 664);
      const key=world+'/'+(read(world,200)>>>0);
      if(key!==worldKey){worldKey=key;lastPlayer=null;events=[];}
      const now = performance.now();
      if (now - last < 16 || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 60000) return;
      last = now;
      if ((!valid(pointer) || !valid(subworld)) && !lastPlayer) return;
      const player = valid(pointer) && valid(subworld) ? {objid:read(pointer,8),worldid:read(subworld,4),
        xmp:read(pointer,16),ymp:read(pointer,20),wmp:read(pointer,24),hmp:read(pointer,28),
        hp:read(pointer,64),maxhp:read(pointer,68),facing:read(pointer,40),
        alive:read(pointer,64)>0,vid:'browser-player',ammo:read(pointer,540),input:read(pointer,520)} : {...lastPlayer,hp:0,alive:false};
      lastPlayer = player;
      // ClientWorld.totalInput already reflects the player's own game bindings.
      const interact = practiceKeys.interact==='Auto'?!!(read(world,668)&128):keys.has(shortcuts.code(practiceKeys.interact));
      const documentState={};
      if(context.map){
        documentState.room_context={map:context.map,room_token:context.room_token,room_seed:read(world,200)>>>0};
        player.inventory_protocol='full-run-inventory-v1';
        if(player.alive && seq%120===0){
          try{player.inventory_preview=StarBreakInventory.snapshot(world,pointer,context);}
          catch(error){player.inventory_error=error.message;}
        }
        if(context.inventory_request && player.alive){
          try{const inventory=StarBreakInventory.refresh(world,pointer,context,context.inventory_request);if(inventory)player.inventory=inventory;}
          catch(error){player.inventory_error=error.message;}
        }
      }
      socket.send(JSON.stringify({seq:++seq,t:Date.now()/1000,player,
        keys:{interact,interact_edge:interact&&!wasInteract,interaction_blocked:false,attack:keys.has('KeyD')},events,...documentState}));
      wasInteract = interact; events = [];
      document.getElementById('loading').hidden = true;
      if (seq % 30 === 0) parent.postMessage({type:'player-state',player,frame,world,pointer},location.origin);
    } catch (e) { console.error('Browser bridge:',e); }
  };
  Module.webUpdates = (world, update) => {
    try {
        if(!valid(update) || events.length>=256) return;
        const flags=read(update,52),seed=read(world,200)>>>0;
        if(flags&16) {
          const destroy=read(update,20);
          if(valid(destroy)) events.push({kind:'destroy',victim:read(destroy,8),ownerid:read(destroy,4),
            reason:read(destroy,12),native_seed:seed,at:Date.now()/1000});
        }
        if (!(flags&8)) return;
        const damage=read(update,16);
        if (!valid(damage)) return;
        const value=read(damage,12), victim=read(damage,8);
        if(value>0 && victim>0 && events.length<256) events.push({kind:'damage',victim,value,
          ownerid:read(damage,36)&1 ? read(damage,4) : read(read(world,664),4),owner_explicit:!!(read(damage,36)&1),native_seed:seed,crit:!!Module.HEAPU8[damage+16],at:Date.now()/1000});
    } catch(e) {console.error('Browser damage bridge:',e);}
  };
  document.getElementById('canvas').focus();
})();
