/* Read-only inventory ABI for the SHA-pinned 1.14.6 asm.js client.
 * ol/Ol/Xl use the libc++ table at PlayerCharacter+652: next/hash/key/value
 * at node+0/4/8/12. This differs from the Win32 GCC map node layout.
 */
const StarBreakInventory = (() => {
  const ABI='web-1.14.6-items-v1';
  let waiting=null;
  function snapshot(world,player,context,control) {
    const heap=Module.HEAP32,bytes=Module.HEAPU8;
    const guard=(pointer,size=4)=>{if(!Number.isInteger(pointer)||pointer<=0||pointer%4||pointer+size>heap.byteLength)throw new Error('Inventory pointer outside client heap');return pointer;};
    const read=(pointer,offset=0)=>heap[guard(pointer+offset)>>2]|0;
    const unsigned=(pointer,offset=0)=>read(pointer,offset)>>>0;
    const string=pointer=>{
      guard(pointer,12);const long=bytes[pointer+11]&128;
      const length=long?unsigned(pointer,4):bytes[pointer+11],data=long?unsigned(pointer):pointer;
      if(length>4096||data<=0||data+length>bytes.length)throw new Error('Invalid inventory string');
      return new TextDecoder('utf-8',{fatal:true}).decode(bytes.subarray(data,data+length));
    };
    const boost=pointer=>{
      guard(pointer,36);const flags=unsigned(pointer,32),value={};if(flags&~63)throw new Error('Unknown item modifier fields');
      for(const [mask,key,offset]of [[1,'stat',4],[2,'val',8],[4,'level',12],[16,'subtype',24]])if(flags&mask)value[key]=read(pointer,offset);
      if(flags&8)value.increment=!!bytes[pointer+16];if(flags&32)value.subtypestr=string(read(pointer,20));return value;
    };
    const scope=()=>({map:context.map,room_token:context.room_token,room_seed:unsigned(world,200),
      worldid:unsigned(read(world,664),4),objid:unsigned(player,8),charid:unsigned(player,468),player_type:read(read(player,472),4)});
    const identity=scope(),header=()=>[read(player,652),read(player,656),read(player,660),read(player,664)];
    const before=header(),ec=read(player,380),count=before[3];
    if(count<0||count>256||before[1]<0||before[1]>1048576||(count===0)!==(before[2]===0)||ec<0)throw new Error('Invalid inventory table or EC');
    const items=[],slots=[],visited=new Set(),keys=new Set(),positions=new Set(),records=[];let node=before[2],totalBoosts=0;
    while(node){
      guard(node,16);if(visited.has(node)||items.length>=count)throw new Error('Inventory node cycle or count mismatch');visited.add(node);
      const next=read(node),key=read(node,8),item=read(node,12);guard(item,68);
      if(read(item,4)!==player)throw new Error('Inventory item owner mismatch');
      const slot=read(item,64),props=item+12,flags=unsigned(props,32),type=read(props,4),serializedSlot=read(props,8);
      if(!(flags&1)||flags&~3||type<=0||slot<=0||slot>65535||positions.has(slot)||keys.has(key))throw new Error('Invalid inventory item/slot');
      const boostData=read(props,12),boostCount=read(props,16),values=[];
      if(boostCount<0||boostCount>64||(totalBoosts+=boostCount)>2048)throw new Error('Inventory modifier bound exceeded');
      for(let index=0;index<boostCount;index++)values.push(boost(read(boostData,index*4)));
      const value={type,slotpos:slot,statboosts:values};
      items.push(value);slots.push({map_key:key,native_slotpos:slot,serialized_slotpos:serializedSlot,type,owner_ok:true});
      records.push([node,next,key,item,slot]);keys.add(key);positions.add(slot);node=next;
    }
    if(items.length!==count||JSON.stringify(before)!==JSON.stringify(header())||read(player,380)!==ec||JSON.stringify(identity)!==JSON.stringify(scope()))throw new Error('Inventory changed during snapshot');
    for(const [address,next,key,item,slot]of records)if(read(address)!==next||read(address,8)!==key||read(address,12)!==item||read(item,64)!==slot)throw new Error('Inventory node changed during snapshot');
    const order=items.map((_,index)=>index).sort((a,b)=>items[a].slotpos-items[b].slotpos);
    const result={schema:'full-run-inventory-v1',sampled_at:Date.now()/1000,request_id:control?.id,request_attempt:control?.attempt,
      scope:identity,ok:true,items_ok:true,ec_ok:true,count,items:order.map(index=>items[index]),slots:order.map(index=>slots[index]),ec,
      quality:{stable:true,slot_source:'Item.slotPos',declared_count:count,traversed_count:count,abi:{name:ABI,pointer_size:4},errors:[]}};
    if(JSON.stringify(result).length>48000)throw new Error('Inventory exceeds transport bound');
    return result;
  }
  function refresh(world,player,context,control){
    waiting=null;
    if(!control||!context.room_ready)return null;
    const now=Date.now()/1000;
    if(now>=control.expires_at){waiting={reason:'expired',now,expires_at:control.expires_at};return null;}
    const heap=Module.HEAP32,server=heap[(world+628)>>2],nativeTime=server?heap[(server+8)>>2]:-1;
    if(control.native_t_min!==undefined&&nativeTime<control.native_t_min){waiting={reason:'native-time',native_server_t:nativeTime,required:control.native_t_min};return null;}
    const value=snapshot(world,player,context,control),target=control.scope;
    if(!target||Object.keys(target).some(key=>value.scope[key]!==target[key]))throw new Error('Inventory refresh scope mismatch');
    value.native_server_t=nativeTime;return value;
  }
  return {snapshot,refresh,ABI,get waiting(){return waiting;}};
})();
