/* A WebSocket-shaped in-memory link to the parent's player-device worker. */
(() => {
  const host=parent.StarBreakBrowser;
  if(!host)throw Error('Open the game from the main page');
  globalThis.fetch=host.fetch;
  navigator.sendBeacon=(url,body)=>{void host.fetch(url,{method:'POST',body});return true;};
  // Original game loaders can use origin-root URLs. Keep their assets inside
  // this game's mount point without changing the shared main website.
  const open=XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open=function(method,url,...args){return open.call(this,method,host.assetURL(url),...args);};
  const image=Object.getOwnPropertyDescriptor(HTMLImageElement.prototype,'src');
  if(image?.set)Object.defineProperty(HTMLImageElement.prototype,'src',{...image,set(value){image.set.call(this,host.assetURL(value));}});
  const active=new Set();
  addEventListener('pagehide',()=>{for(const socket of active)socket.close();});
  class LocalSocket extends EventTarget {
    static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;
    constructor(url,protocols){super();this.url=url;this.protocol=typeof protocols==='string'?protocols:(protocols?.[0]||'');this.readyState=0;this.binaryType='arraybuffer';this.bufferedAmount=0;this.chain=Promise.resolve();active.add(this);host.attach(this,url).catch(error=>this.fail(error));}
    dispatch(type,event){this['on'+type]?.(event);this.dispatchEvent(event);}
    opened(){if(this.readyState!==0)return;this.readyState=1;this.dispatch('open',new Event('open'));}
    receive(message){const data=message.binary?Uint8Array.from(atob(message.data),c=>c.charCodeAt(0)).buffer:message.data;this.dispatch('message',new MessageEvent('message',{data}));}
    send(data){if(this.readyState!==1)throw Error('Game channel is not open');const binary=typeof data!=='string';const value=binary?host.to64(new Uint8Array(data.buffer||data,data.byteOffset||0,data.byteLength)):data;const size=binary?data.byteLength:data.length;this.bufferedAmount+=size;this.chain=this.chain.then(()=>host.send(this,this.url,value,binary)).catch(error=>this.fail(error)).finally(()=>{this.bufferedAmount-=size;});}
    close(){if(this.readyState===3)return;this.readyState=3;active.delete(this);host.detach(this,this.url);this.dispatch('close',new Event('close'));}
    fail(error){console.error(error);this.dispatch('error',new Event('error'));this.close();}
  }
  for(const key of ['CONNECTING','OPEN','CLOSING','CLOSED'])LocalSocket.prototype[key]=LocalSocket[key];
  globalThis.WebSocket=LocalSocket;
})();
