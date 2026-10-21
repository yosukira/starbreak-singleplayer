"""Render-only SBPE-style display hooks for the pinned 1.14.6 browser client."""
import re


def patch(text):
    def replace(before,after):
        nonlocal text
        if text.count(before)!=1:raise ValueError('Browser visual signature mismatch: '+before[:65])
        text=text.replace(before,after)

    replace('var webVisualContext=env.webVisualContext;',
        'var webVisualSetting=env.webVisualSetting;var webVisualView=env.webVisualView;var webVisualEntity=env.webVisualEntity;var webVisualSprite=env.webVisualSprite;var webVisualDamageAge=env.webVisualDamageAge;var webVisualContext=env.webVisualContext;')
    replace('Module.asmLibraryArg={','Module.asmLibraryArg={'
        '"webVisualSetting":function(k){return Module.visualSetting?Module.visualSetting(k):0},'
        '"webVisualView":function(p,s){if(Module.visualView)Module.visualView(p,s)},'
        '"webVisualEntity":function(w,p){return Module.visualEntity?Module.visualEntity(w,p):1},'
        '"webVisualSprite":function(w,p){if(Module.visualSprite)Module.visualSprite(w,p)},'
        '"webVisualDamageAge":function(t){return Module.visualDamageAge?Module.visualDamageAge(t):t},')
    for name,call in (
        ('qaa','if(webVisualSetting(4)|0)return;'),
        ('Laa','if(webVisualSetting(5)|0)return;'),
        ('Rba','if(!(webVisualEntity(b|0,d|0)|0))return;'),
        ('Sba','if(!(webVisualEntity(b|0,d|0)|0))return;'),
        ('Pba','if(!(webVisualEntity(a|0,b|0)|0))return;'),
        ('Hba','if(webVisualSetting(3)|0)e=0;')):
        signature=re.search(r'function '+name+r'\([^)]*\)\{[^{}]*?var [^;]+;',text).group(0)
        replace(signature,signature+call)
    # Associate labels with actual sprite submissions, after sprite/animation
    # lookup has succeeded. A live WorldObject alone is not a visible enemy.
    for name,call,expected in (('Rba','MN(e,f,i,l,j,m,k,1);',2),('Sba','AN(o,m,p,C,k,D,B,1,i);',1)):
        start=text.index('function '+name+'(');end=text.index('function ',start+10);original=text[start:end]
        if original.count(call)!=expected:raise ValueError('Visible sprite signature mismatch')
        replace(original,original.replace(call,'webVisualSprite(b|0,d|0);'+call))
    replace('if(!(s&3145728))return;s=Iba(b,d)|0;',
            'if(!(s&3145728))return;if(webVisualSetting(1)|0)return;s=Iba(b,d)|0;')
    replace('aO(s,t,u);v=c[(c[(c[d+96>>2]|0?g:c[40702]|0)+84>>2]|0)+8>>2]|0;',
            'aO(s,t,u);if(webVisualSetting(2)|0)return;v=c[(c[(c[d+96>>2]|0?g:c[40702]|0)+84>>2]|0)+8>>2]|0;')
    start=text.index('function wba(');end=text.index('function ',start+10);original=text[start:end]
    signature=re.match(r'function [^{]+\{[^{}]*?var [^;]+;',original).group(0)
    changed=original.replace(signature,signature+'webVisualView(d|0,0);',1)
    changed=changed.replace('yba(d,n,e);','yba(d,n,e);webVisualView(d|0,1);',1)
    changed=re.sub(r'\breturn;', 'webVisualView(d|0,2);return;',changed)
    replace(original,changed)
    # The elapsed-damage getter is used only by these drawing paths.
    for name,expression in (('Pba','_x(b)|0'),('Rba','_x(d)|0'),('Sba','_x(d)|0')):
        start=text.index('function '+name+'(');end=text.index('function ',start+10);original=text[start:end]
        if original.count(expression)!=1:raise ValueError('Damage flash draw signature mismatch')
        replace(original,original.replace(expression,'webVisualDamageAge('+expression+')|0'))
    replace('re._Z9XDL_Clearj=(function(color){loadColorToArray(color,clearColorArray);',
            're._Z9XDL_Clearj=(function(color){if(Module.visualBackground)color=Module.visualBackground(color);loadColorToArray(color,clearColorArray);')
    return text
