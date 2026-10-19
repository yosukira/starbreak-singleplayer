/* Keep the canvas element itself aligned with the visible game rectangle.
 * The original client's mouse adapter uses getBoundingClientRect(); scaling
 * only its contents with object-fit would include invisible letterbox space. */
(function(){
  'use strict';
  function fit(canvas,width,height){
    if(!(canvas.width>0&&canvas.height>0&&width>0&&height>0))return;
    const scale=Math.min(width/canvas.width,height/canvas.height);
    const w=canvas.width*scale,h=canvas.height*scale;
    canvas.style.width=w+'px';canvas.style.height=h+'px';
    canvas.style.left=(width-w)/2+'px';canvas.style.top=(height-h)/2+'px';
    return {width:w,height:h,left:(width-w)/2,top:(height-h)/2};
  }
  if(typeof module!=='undefined'){module.exports=fit;return;}
  const canvas=document.getElementById('canvas');
  const resize=()=>fit(canvas,document.documentElement.clientWidth,document.documentElement.clientHeight);
  new MutationObserver(resize).observe(canvas,{attributes:true,attributeFilter:['width','height']});
  if(typeof ResizeObserver!=='undefined')new ResizeObserver(resize).observe(document.documentElement);
  addEventListener('resize',resize);addEventListener('fullscreenchange',resize);
  resize();
})();
