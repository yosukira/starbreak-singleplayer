"""Typed 1.14.6 engine entry points used by portable native journals."""
import re


def patch(text):
    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Portable replay engine signature mismatch: ' + before[:60])
        text = text.replace(before, after)

    replace('var webFrame=env.webFrame;',
        'var webNativeContext=env.webNativeContext;var webNativeEnter=env.webNativeEnter;var webNativeExit=env.webNativeExit;var webNativeClock=env.webNativeClock;var webNativeSeed=env.webNativeSeed;var webFrame=env.webFrame;')
    replace('"_usleep":_usleep,',
        '"_usleep":function(useconds){return Module.nativeJournal?Module.nativeJournal.wallSleep(useconds,_usleep):_usleep(useconds)},')
    replace('Module.asmLibraryArg={', 'Module.asmLibraryArg={'
        '"webNativeEnter":function(k,o,a,b,c){return Module.nativeJournal?Module.nativeJournal.enter(k,o,a,b,c):1},'
        '"webNativeExit":function(k,o){if(Module.nativeJournal)Module.nativeJournal.leave(k,o)},'
        '"webNativeClock":function(v){return Module.nativeJournal?Module.nativeJournal.clock(v):v},'
        '"webNativeSeed":function(v){return Module.nativeJournal?Module.nativeJournal.seed(v):v},')
    replace('Module.asmLibraryArg={','Module.asmLibraryArg={"webNativeContext":function(s){if(Module.nativeJournal)Module.nativeJournal.nativeServer=s},')
    replace('"webVisualContext":function(p){if(Module.visualContext)Module.visualContext(p)}',
        '"webVisualContext":function(p){if(Module.nativeJournal)Module.nativeJournal.clientContext(p);if(Module.visualContext)Module.visualContext(p)}')
    signature=re.search(r'function HM\([^)]*\)\{.*?var [^;]+;',text).group(0)
    replace(signature,signature+'webNativeContext(d|0);')
    # Objects and pointers come only from this installed engine. A recording
    # supplies typed arguments, never an address, callback or executable state.
    for name, kind, obj, arguments, returns in (
        ('Bja',1,'a',('b','d'),False), ('Cja',2,'a',(),False),
        ('rh',3,'a',('b',),False), ('Xh',4,'a',('b',),True),
        ('td',5,'a',('b','d','e'),False), ('KM',6,'b',(),True),
        ('Yh',7,'a',('b',),True)):
        start = text.index('function '+name+'(')
        end = text.index('function ',start+10)
        original = text[start:end]
        signature = re.match(r'function [^{]+\{.*?var [^;]+;',original).group(0)
        args = ','.join((*arguments,*('0' for _ in range(3-len(arguments)))))
        changed = re.sub(r'\breturn\b([^;{}]*)(;|(?=\}))',
            lambda match:'{'+f'webNativeExit({kind},{obj}|0);return'+match[1]+';}', original)
        changed = changed.replace(signature,signature+f'if(!(webNativeEnter({kind},{obj}|0,{args})|0))return'+(' 1;' if returns else ';'),1)
        replace(original,changed)
    replace('return ~~(a-+h[19826])|0}', 'return webNativeClock(~~(a-+h[19826])|0)|0}')
    replace('return c[b>>2]<<24^d<<16^c[b+4>>2]|0}', 'return webNativeSeed(c[b>>2]<<24^d<<16^c[b+4>>2]|0)|0}')
    replace('return{replayBoundLabel:',
        'return{nativeReplayInventory:Yh,nativeReplayClock:af,nativeReplayMessage:td,nativeReplayInput:Bja,nativeReplayFrame:Cja,nativeReplayEngine:rh,nativeReplayPlayerInput:Xh,replayBoundLabel:')
    return text
