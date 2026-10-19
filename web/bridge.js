/* Local 1.14.6 browser feedback adapter. No account login or native injection. */
(() => {
  'use strict';
  let socket={readyState:0};
  let reconnectTelemetry=STARBREAK_WEB_BOOT.replay?.mode!=='play';
  const keys = new Set();
  let practiceKeys=STARBREAK_WEB_BOOT.keybindings;
  Module.webZoom?.setStep(practiceKeys.zoom_percent??10);
  const shortcuts=new PracticeShortcuts(window,practiceKeys,action=>{if(action.startsWith('zoom_'))Module.webZoom?.step(action==='zoom_in'?1:-1);else parent.postMessage({type:'practice-shortcut',action},location.origin);});
  addEventListener('message',event=>{if(event.origin===location.origin&&event.source===parent&&event.data.type==='practice-keys'){practiceKeys=event.data.keys;shortcuts.update(practiceKeys);Module.webZoom?.setStep(practiceKeys.zoom_percent??10);}});
  let maximumDependencies=0;
  Module.monitorRunDependencies=count=>{maximumDependencies=Math.max(maximumDependencies,count);parent.postMessage({type:'loading-progress',percent:75+(maximumDependencies?15*(1-count/maximumDependencies):0),step:'engine'},location.origin);};
  (Module.preRun||=[]).push(()=>parent.postMessage({type:'loading-progress',percent:92,step:'world'},location.origin));
  let frame = 0, seq = 0, last = 0, wasInteract = false, events = [], lastPlayer = null, context = {}, worldKey = '', gameplay = false;
  const read = (pointer, offset = 0) => Module.HEAP32[(pointer + offset) >> 2] | 0;
  const valid = p => p > 0 && p + 744 < Module.HEAP32.byteLength && !(p & 3);
  addEventListener('keydown', e => { keys.add(e.code); if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Space','Tab'].includes(e.code)) e.preventDefault(); });
  addEventListener('keyup', e => keys.delete(e.code));
  addEventListener('blur', () => keys.clear());
  const acknowledge = e => {
    try { const ack = JSON.parse(e.data); if(ack.map)context=ack; parent.postMessage({type:'bridge-ack',ack}, location.origin); }
    catch (_) {}
  };
  const connectTelemetry = () => {
    if(!reconnectTelemetry)return;
    const transport=new WebSocket('ws://'+location.hostname+':'+STARBREAK_WEB_BOOT.wsPort+'/telemetry', 'binary');
    socket=transport;transport.onmessage=acknowledge;
    transport.onclose=()=>{
      if(reconnectTelemetry&&socket===transport)setTimeout(connectTelemetry,500);
    };
  };
  connectTelemetry();
  Module.webStartLiveTelemetry = transport => {
    // A takeover owns a multiplexed transport. Never reconnect its feedback to
    // the ordinary session's UDP bridge or allow stale acknowledgements in.
    reconnectTelemetry=false;
    socket.onmessage=null;
    socket=transport;socket.onmessage=acknowledge;
    seq=0;last=0;wasInteract=false;events=[];lastPlayer=null;keys.clear();gameplay=false;
    pickupTotals={health_packs:0,ammo_packs:0,healing:0,damage_taken:0};pickups.clear();healthBefore.clear();
  };
  Module.webFrame = world => {
    try {
      frame++;
      const pointer = read(world, 672), subworld = read(world, 664);
      const key=world+'/'+(read(world,200)>>>0);
      if(key!==worldKey){worldKey=key;lastPlayer=null;events=[];pickups.clear();healthBefore.clear();activePlayer=0;gameplay=false;pickupTotals={health_packs:0,ammo_packs:0,healing:0,damage_taken:0};}
      activePlayer=valid(pointer)?pointer:0;
      // Engine input uses the game's own remapped keyboard/controller binds.
      // Sample every frame, before rate limiting; menu clicks do not set it.
      if(reconnectTelemetry||socket.readyState===WebSocket.OPEN){
        const customInteract=practiceKeys.interact!=='Auto'&&keys.has(shortcuts.code(practiceKeys.interact));
        if(valid(pointer)&&read(pointer,64)>0&&((read(world,668)&0x2ff)||customInteract))gameplay=true;
      }
      const now = performance.now();
      if (now - last < 16 || socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 60000) return;
      last = now;
      if ((!valid(pointer) || !valid(subworld)) && !lastPlayer) return;
      const player = valid(pointer) && valid(subworld) ? {objid:read(pointer,8),worldid:read(subworld,4),
        xmp:read(pointer,16),ymp:read(pointer,20),wmp:read(pointer,24),hmp:read(pointer,28),
        hp:read(pointer,64),maxhp:read(pointer,68),facing:read(pointer,40),
        alive:read(pointer,64)>0,vid:'browser-player',ammo:read(pointer,540),input:read(pointer,520)} : {...lastPlayer,hp:0,alive:false};
      lastPlayer = player;activePlayer=valid(pointer)?pointer:0;
      player.room_seed=read(world,200)>>>0;
      player.battle_metrics={protocol:'player-metrics-v1',...pickupTotals};
      // ClientWorld.totalInput already reflects the player's own game bindings.
      const interact = !!(read(world,668)&128) ||
        (practiceKeys.interact!=='Auto' && keys.has(shortcuts.code(practiceKeys.interact)));
      const documentState={};
      if(context.map){
        documentState.room_context={map:context.map,room_token:context.room_token,room_seed:read(world,200)>>>0};
        player.inventory_protocol='full-run-inventory-v1';
        if(player.alive && seq%120===0){
          try{player.inventory_preview=StarBreakInventory.snapshot(world,pointer,context);}
          catch(error){player.inventory_error=error.message;}
        }
        if(context.inventory_request && player.alive){
          try{const inventory=StarBreakInventory.refresh(world,pointer,context,context.inventory_request);if(inventory)player.inventory=inventory;else if(StarBreakInventory.waiting)player.inventory_wait=StarBreakInventory.waiting;}
          catch(error){player.inventory_error=error.message;}
        }
      }
      socket.send(JSON.stringify({seq:++seq,t:Date.now()/1000,player,
        keys:{interact,interact_edge:interact&&!wasInteract,interaction_blocked:false,attack:keys.has('KeyD'),gameplay},events,...documentState}));
      wasInteract = interact; events = [];
      document.getElementById('loading').hidden = true;
      if (seq % 30 === 0) parent.postMessage({type:'player-state',player,frame,world,pointer},location.origin);
    } catch (e) { console.error('Browser bridge:',e); }
  };
  const pickups = new Map();
  let pickupTotals = {health_packs:0,ammo_packs:0,healing:0,damage_taken:0}, activePlayer = 0;
  const healthBefore = new Map();
  Module.webHealth = (player, stage) => {
    if(player!==activePlayer||!valid(player))return;
    if(stage===0){const stack=healthBefore.get(player)||[];stack.push({hp:read(player,64),children:0});healthBefore.set(player,stack);}
    else{
      const stack=healthBefore.get(player),before=stack?.pop();if(!before)return;
      const change=Math.max(0,read(player,64))-Math.max(0,before.hp);
      if(stack.length)stack.at(-1).children+=change;else healthBefore.delete(player);
      if(before.hp>0){const own=change-before.children;pickupTotals.healing+=Math.max(0,own);pickupTotals.damage_taken+=Math.max(0,-own);}
    }
  };
  Module.webLoot = (loot, player, stage) => {
    try {
      if(!valid(loot)||!valid(player)||player!==activePlayer)return;
      if(stage===0){
        const description=read(loot,356);if(!valid(description))return;
        const list=read(description,128),count=read(description,132);
        if(count<0||count>64||!list||list+count*4>Module.HEAP32.byteLength)return;
        const stats=new Set();for(let i=0;i<count;i++){const boost=read(list,i*4);if(valid(boost))stats.add(read(boost,4));}
        pickups.set(loot,{player,hp:read(player,64),health:stats.has(1),ammo:stats.has(3)});
      } else {
        const before=pickups.get(loot);pickups.delete(loot);if(!before||before.player!==player)return;
        pickupTotals.health_packs+=Number(before.health);pickupTotals.ammo_packs+=Number(before.ammo);
        // Healing is counted by the central health adjustment observer.
      }
    }catch(e){console.error('Pickup observer:',e);}
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
