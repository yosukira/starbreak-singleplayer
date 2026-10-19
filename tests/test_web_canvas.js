'use strict';
const assert=require('node:assert/strict');
const fit=require('../web/canvas.js');
// Exercise the original client's rect -> logical coordinate calculation.
// Insets come from the element itself, with no hidden object-fit letterbox.
for(const [w,h,cw,ch] of [[1920,1080,2560,1600],[800,1200,2560,1600],[3840,2160,2560,1600],[390,660,1000,600],[1365.5,768.25,2000,1200],[1200,750,600,375]]){
  const canvas={width:cw,height:ch,style:{}};
  const r=fit(canvas,w,h);
  assert.ok(r.width<=w+.00001&&r.height<=h+.00001);
  assert.ok(Math.abs(r.width/r.height-cw/ch)<1e-9);
  assert.ok(Math.abs(r.left*2+r.width-w)<1e-9);
  assert.ok(Math.abs(r.top*2+r.height-h)<1e-9);
  for(const [x,y]of [[0,0],[.1,.2],[.5,.5],[.9,.8],[1,1]]){
    const scroll={x:30,y:82},page={x:r.left+x*r.width+scroll.x,y:r.top+y*r.height+scroll.y};
    const mapped=[(page.x-(scroll.x+r.left))*cw/r.width,(page.y-(scroll.y+r.top))*ch/r.height];
    assert.ok(Math.abs(mapped[0]-x*cw)<1e-8);
    assert.ok(Math.abs(mapped[1]-y*ch)<1e-8);
  }
  // A replay/zoom changes logical size independently of the outer window.
  canvas.width*=2;canvas.height*=2;
  assert.deepEqual(fit(canvas,w,h),r);
}
const canvas={width:0,height:0,style:{}};
assert.equal(fit(canvas,0,0),undefined);
assert.deepEqual(canvas.style,{});
console.log('Game canvas: letterbox/pillarbox, window resize, browser scaling, zoom and coordinate alignment passed.');
