"""Version-checked local adapters for the original 1.14.6 web client.

The original client and assets stay outside the source repository. Patches are
applied in memory when served and never modify the user's source client.
"""
import base64
import hashlib
import re
from http.client import HTTPException
from pathlib import Path
import tempfile
from urllib.error import HTTPError
from urllib.request import urlopen

CLIENT_SHA256 = 'f6a285c504a5832a7a7f02d73769672a66bb46b7b1b60c1a136309aeb0727c8d'
CLIENT_FILENAME = 'VR1574197275.mvmmoclient.js'


def ensure_cached_client(path):
    """Reuse a verified cache, or replace it only after a complete download."""
    if path.is_file():
        try:
            patch_client(path.read_bytes())
            return path
        except ValueError:
            print('Cached web client is incomplete or unsupported; downloading it again.', flush=True)
    else:
        print('Downloading the original 1.14.6 web client from starbreak.com...', flush=True)
    for attempt in range(3):
        try:
            with urlopen('https://www.starbreak.com/' + CLIENT_FILENAME, timeout=60) as response:
                data = response.read()
            patch_client(data)
            break
        except HTTPError as error:
            if error.code < 500 or attempt == 2:
                raise
        except (OSError, HTTPException, ValueError):
            if attempt == 2:
                raise
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(data)
        except BaseException:
            output.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def patch_client(source):
    if hashlib.sha256(source).hexdigest() != CLIENT_SHA256:
        raise ValueError('Unsupported web client. Expected StarBreak VR1574197275 / 1.14.6.')
    text = source.decode('utf8')

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Web client patch signature mismatch: ' + before[:65])
        text = text.replace(before, after)

    # Preserve the game's own serialized options byte-for-byte. The adapter
    # mirrors only this key to the local data folder, independent of HTTP port.
    replace('var dataStr=localStorage.getItem(keyStr);',
            'var dataStr=Module.gamePreferences?Module.gamePreferences.load(keyStr):localStorage.getItem(keyStr);')
    replace('localStorage.setItem(keyStr,dataStr)}function __Z17XDL_SetWindowSize',
            'if(Module.gamePreferences)Module.gamePreferences.save(keyStr,dataStr);else localStorage.setItem(keyStr,dataStr)}function __Z17XDL_SetWindowSize')
    # Viewport affects camera culling and positional audio as well as drawing.
    # Replay the original logical size, while CSS scales it to today's window.
    replace('newHeight=Math.max(600,Math.min(XDL.cHeight,window.innerHeight,1600));if(XDL.canvas.width!=newWidth',
            'newHeight=Math.max(600,Math.min(XDL.cHeight,window.innerHeight,1600));if(Module.clientReplay){var size=Module.clientReplay.viewport(newWidth,newHeight);newWidth=size[0];newHeight=size[1]}if(XDL.canvas.width!=newWidth')
    replace('onWindowResize:(function(){var newWidth=XDL.canvas.width;',
            'onWindowResize:(function(event){if(event&&event.type==="resize"&&Module.clientReplay){Module.clientReplay.control("resize",[],function(){XDL.onWindowResize()});return}var newWidth=XDL.canvas.width;')

    # Add narrow, typed imports to the asm.js module; preserve the game logic.
    replace('"use asm";var a=new global.Int8Array(buffer);',
            '"use asm";var webFrame=env.webFrame;var webUpdates=env.webUpdates;var webLoot=env.webLoot;var webHealth=env.webHealth;var webReplayFrame=env.webReplayFrame;var webReplayEnd=env.webReplayEnd;var webReplayInput=env.webReplayInput;var webBinding=env.webBinding;var webBattleExit=env.webBattleExit;var a=new global.Int8Array(buffer);')
    replace('Module.asmLibraryArg={"abort":abort,',
            'Module.asmLibraryArg={"webBattleExit":function(){if(Module.clientReplay)Module.clientReplay.requestQuit()},'
            '"webBinding":function(p,kind,a,b,c,d){var rows=Module.replayBindings||(Module.replayBindings=new Map());if(kind)rows.set(p,[kind,a,b,c,d]);else rows.delete(p)},'
            '"webFrame":function(p){if(Module.webFrame)Module.webFrame(p)},'
            '"webUpdates":function(p,u){if(Module.webUpdates)Module.webUpdates(p,u)},'
            '"webLoot":function(l,p,s){if(Module.webLoot)Module.webLoot(l,p,s)},'
            '"webHealth":function(p,s){if(Module.webHealth)Module.webHealth(p,s)},'
            '"webReplayFrame":function(w,d){return Module.webReplayFrame?Module.webReplayFrame(w,d):d},'
            '"webReplayEnd":function(w){if(Module.webReplayEnd)Module.webReplayEnd(w)},'
            '"webReplayInput":function(w,p){return Module.webReplayInput?Module.webReplayInput(w,p):1},"abort":abort,')
    # CM serializes CONNECT without RSA, matching make_local_client.py. Both
    # directions still use the existing client's RC4 streams after CONNECT.
    replace('if((d|0)==3){c[i>>2]=0;c[i+4>>2]=0;c[i+8>>2]=0;EUa(e,i)|0;',
            'if((d|0)==255){c[i>>2]=0;c[i+4>>2]=0;c[i+8>>2]=0;EUa(e,i)|0;')
    # Browser -k omits the optional RC4 seed. Match the local server's explicit
    # RC4 handshake with a deterministic seed; this connection is localhost-only.
    replace('CM(c[j>>2]|0,3,g);if(c[x>>2]&16|0?',
            'c[x>>2]=c[x>>2]|16;c[g+16>>2]=0;CM(c[j>>2]|0,3,g);if(c[x>>2]&16|0?')
    replace('function rh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0,v=0,w=0,x=0;d=a+364|0;',
            'function rh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0,v=0,w=0,x=0;b=webReplayFrame(a|0,b|0)|0;webFrame(a|0);d=a+364|0;')
    start = text.index('function rh(a,b){')
    end = text.index('function ', start + 10)
    original = text[start:end]
    if original.count('KB(a);return}') != 2:
        raise ValueError('Web client frame boundary signature mismatch')
    replace(original, original.replace('KB(a);return}', 'KB(a);webReplayEnd(a|0);return}'))
    replace('function Xh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0;d=l;',
            'function Xh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0;if(!(webReplayInput(a|0,b|0)|0))return 0;d=l;')
    replace('return{__GLOBAL__I_000101:P_a,', 'return{replaySetInput:Xh,__GLOBAL__I_000101:P_a,')
    # Reload only named game options through the original protobuf/map/string
    # functions. Keep unrelated local-client state and the live world intact.
    # The temporary map contains only options, and every copied row is freed.
    from tools.game_preferences import SETTINGS
    memory = base64.b64decode(re.search(
        r'memoryInitializer="data:application/octet-stream;base64,([^"]+)"', text).group(1))
    keys = {}
    for key in sorted(SETTINGS):
        locations = list(re.finditer(b'\0'+key.encode()+b'\0', memory))
        if len(locations) != 1:
            raise ValueError('Game preference key signature mismatch: '+key)
        keys[key] = locations[0].start()+1+8  # original GLOBAL_BASE
    mapping = re.search(r'function rL\(b\)\{.*?(?=function sL\()', text).group(0)
    defaults = mapping[mapping.index('ndb(b+64|0,0,824)|0;'):mapping.index('f=q9()|0;')]
    if not defaults.endswith('sL(b);'):
        raise ValueError('Original input defaults signature mismatch')
    defaults = re.sub(r'\bb\b', 'm', defaults)
    copies = ''.join(
        'c[h>>2]=0;c[h+4>>2]=0;c[h+8>>2]=0;'
        'if(y9(e,%d,h)|0){C9(j,%d,h);if((a[h+11>>0]|0)<0)h8a(c[h>>2]|0)}else B9(j,%d,0);B9(e,%d,0);'
        % (address, address, address, address) for address in keys.values())
    helper = ('function replayPreferences(b,d){b=b|0;d=d|0;var e=0,f=0,h=0,i=0,j=0,k=0,m=0;'
        'f=l;l=l+48|0;e=f;h=f+32|0;ndb(e,0,20)|0;g[e+16>>2]=1.0;'
        'r9(e,b,d);j=q9()|0;'+copies+'u9(e,0);G9(F9()|0);m=qL()|0;'+defaults+
        'c[h>>2]=0;c[h+4>>2]=0;c[h+8>>2]=0;'
        'if(y9(j,%d,h)|0)tL(m,h);if((a[h+11>>0]|0)<0)h8a(c[h>>2]|0);'
        'Ba(J9(F9()|0)|0)|0;Ca(N9(F9()|0)|0)|0;l=f;return}' % keys['impbstr'])
    # Action hints cache their text when created. Track only original live UI
    # objects and refresh their existing text children after binding changes.
    # NF is the common widget destructor, including all action-hint overloads.
    for name, call in (('g3', 'webBinding(b|0,1,e|0,f|0,-1,0);'),
                       ('j3', 'webBinding(b|0,1,e|0,f|0,g|0,h|0);'),
                       ('A0', 'webBinding(a|0,2,0,0,0,0);'),
                       ('NF', 'webBinding(b|0,0,0,0,0,0);'),
                       ('OG', 'webBattleExit();')):
        signature = re.search(r'function '+name+r'\([^)]*\)\{[^{}]*?var [^;]+;', text).group(0)
        replace(signature, signature+call)
    refresh = ('function replayBoundLabel(b,d,e,f,h){b=b|0;d=d|0;e=e|0;f=f|0;h=h|0;'
        'var i=0,j=0,k=0,m=0,n=0,p=0;i=l;l=l+16|0;j=i;'
        'if((c[b>>2]|0)!=42960){l=i;return}m=p9(d)|0;n=e;'
        'if(!(m|0)&((f|0)>=0)){m=p9(f)|0;n=h}'
        'p=c[b+48>>2]|0;if(n){c[j>>2]=0;c[j+4>>2]=0;c[j+8>>2]=0;'
        'o9(j,m,p9(9)|0,uG()|0);bE(p,(a[j+11>>0]|0)<0?c[j>>2]|0:j);'
        'if((a[j+11>>0]|0)<0)h8a(c[j>>2]|0)}else bE(p,n9(m,uG()|0)|0);'
        'k=c[p+24>>2]|0;c[b+24>>2]=k;c[b+28>>2]=c[p+28>>2];p=c[b+52>>2]|0;'
        'if(p){c[p+16>>2]=k;c[b+24>>2]=k+(c[p+24>>2]|0)}l=i;return}')
    replace('return{replaySetInput:Xh,__GLOBAL__I_000101:P_a,',
            helper+refresh+'return{replayBoundLabel:replayBoundLabel,replayControlRow:A0,replayPreferences:replayPreferences,replaySetInput:Xh,__GLOBAL__I_000101:P_a,')
    replace('function __Z12XDL_DoEventsv(){',
        'Module.replayStop=function(){clearInterval(Module.replayTimerId);Module.pauseMainLoop()};function __Z12XDL_DoEventsv(){')
    replace('function __Z12XDL_DoEventsv(){',
        'Module.replayPreferences=function(content){var raw=atob(content);if(raw.length>65536)throw Error("Game options exceed limit");var p=Module._malloc(raw.length||1);try{for(var n=0;n<raw.length;n++)Module.HEAPU8[p+n]=raw.charCodeAt(n);Module.asm.replayPreferences(p,raw.length)}finally{Module._free(p)}for(var entry of Array.from(Module.replayBindings||[])){var object=entry[0],row=entry[1];if(row[0]===2)Module.asm.replayControlRow(object);else Module.asm.replayBoundLabel(object,row[1],row[2],row[3],row[4])}};function __Z12XDL_DoEventsv(){')
    replace('ws=new WebSocketConstructor(url,opts);ws.binaryType="arraybuffer"',
            'ws=Module.clientReplay?Module.clientReplay.createSocket(url,opts):new WebSocketConstructor(url,opts);ws.binaryType="arraybuffer"')
    replace('function _gettimeofday(ptr){var now=Date.now();',
            'function _gettimeofday(ptr){var now=Module.clientReplay?Module.clientReplay.value("clock",Date.now()):Date.now();')
    replace('function _time(ptr){var ret=Date.now()/1e3|0;',
            'function _time(ptr){var ret=(Module.clientReplay?Module.clientReplay.value("time",Date.now()):Date.now())/1e3|0;')
    # Record the engine's clock import, leaving the browser's scheduler on its
    # own real clock. RAF/timeout scheduling varies between machines and must
    # not consume simulation clock observations.
    replace('"_emscripten_get_now":_emscripten_get_now,',
            '"_emscripten_get_now":function(){var value=_emscripten_get_now();return Module.clientReplay?Module.clientReplay.value("tick",value):value},')
    replace('var ASM_CONSTS=[(function(){setInterval(__Z11loopCheckerv,1e3)})];',
            'Module.replayTimer=function(){__Z11loopCheckerv()};var ASM_CONSTS=[(function(){Module.replayTimerId=setInterval(function(){if(Module.clientReplay)Module.clientReplay.timer(__Z11loopCheckerv);else __Z11loopCheckerv()},1e3)})];')
    replace('function __exit(status){Module["exit"](status)}',
            'function __exit(status){if(Module.clientReplay)Module.clientReplay.quit(status);Module.replayStop();ABORT=true;Module["exit"](status)}')
    replace('runIter:(function(func){if(ABORT)return;',
            'runIter:(function(func){var iteration=function(){if(ABORT)return;')
    replace('if(Module["postMainLoop"])Module["postMainLoop"]()})},isFullscreen:false,',
            'if(Module["postMainLoop"])Module["postMainLoop"]()};if(Module.clientReplay)Module.clientReplay.iterate(iteration);else iteration()})},isFullscreen:false,')
    replace('crypto.getRandomValues(randomBuffer);return randomBuffer[0]',
            'crypto.getRandomValues(randomBuffer);return Module.clientReplay?Module.clientReplay.value("random",randomBuffer[0]):randomBuffer[0]')
    replace('return Math.random()*256|0',
            'return Module.clientReplay?Module.clientReplay.value("random",Math.random()*256|0):Math.random()*256|0')
    replace('image.onload=(function(){var textureId=XDL.renderEngine.addTexture(image);Runtime.dynCall("vii",doneCallback,[textureId,userData])});',
            'image.onload=(function(){var apply=function(){var textureId=XDL.renderEngine.addTexture(image);Runtime.dynCall("vii",doneCallback,[textureId,userData])};if(Module.clientReplay)Module.clientReplay.asset("texture",filename,apply);else apply()});')
    replace('http.onload=(function(){var byteArray=new Uint8Array(http.response);var buffer=_malloc(byteArray.length);HEAPU8.set(byteArray,buffer);Runtime.dynCall("viii",doneCallback,[buffer,byteArray.length,userData]);_free(buffer)});http.send()',
            'http.onload=(function(){var apply=function(){var byteArray=new Uint8Array(http.response);var buffer=_malloc(byteArray.length);HEAPU8.set(byteArray,buffer);Runtime.dynCall("viii",doneCallback,[buffer,byteArray.length,userData]);_free(buffer)};if(Module.clientReplay)Module.clientReplay.asset("data",textFilenameStr,apply);else apply()});http.send()')
    # Observe original input dispatch, including focus/resize events which can
    # read the game clock outside a simulation tick. Key codes are numeric;
    # text input, clipboard contents and account fields are never collected.
    dispatches = (
        ('key', 'viii', 'keyboardCallback', 'event.type==="keydown"?1:0,code'),
        ('button', 'viiiii', 'mouseButtonCallback', 'Browser.mouseX,Browser.mouseY,event.button,event.type==="mousedown"?1:0'),
        ('mouse', 'viii', 'mouseMoveCallback', 'Browser.mouseX,Browser.mouseY'),
        ('window', 'vii', 'windowEventCallback', '1'),
        ('window', 'vii', 'windowEventCallback', '3'),
        ('key', 'viii', 'keyboardCallback', 'gamepadState.buttons[b]?1:0,b|1<<29'),
        ('key', 'viii', 'keyboardCallback', 'false,key|1<<29'),
        ('key', 'viii', 'keyboardCallback', 'true,key|1<<29'),
    )
    for kind, signature, callback, arguments in dispatches:
        original = 'Runtime.dynCall("%s",XDL.%s,[%s,XDL.userData])' % (signature, callback, arguments)
        observed = arguments + (',XDL.canvas.width,XDL.canvas.height' if kind == 'window' else '')
        replace(original, '(Module.clientReplay?Module.clientReplay.control("%s",[%s],function(){%s}):%s)' % (kind, observed, original, original))
    replace('function __Z12XDL_DoEventsv(){',
        'Module.replayTranslateKey=function(code){return code>=65&&code<=90?code+32:(XDL.keyCodes[code]||code)};Module.replayControl=function(kind,values){if(kind==="resize"){XDL.onWindowResize();return}if(kind==="reset"){var w=Module.clientReplay.world;var p=Module._malloc(24);Module.HEAP32.fill(0,p>>2,(p+24)>>2);Module.HEAP32[(p+12)>>2]=Module.HEAP32[(w+668)>>2];Module.HEAP32[(p+20)>>2]=1;Module.asm.replaySetInput(w,p);Module._free(p);return}var table={key:["viii","keyboardCallback"],button:["viiiii","mouseButtonCallback"],mouse:["viii","mouseMoveCallback"],window:["vii","windowEventCallback"]};var row=table[kind];if(!row)throw Error("Invalid replay control");var args=values.slice();if(kind==="window"){XDL.canvas.width=args[1];XDL.canvas.height=args[2];args=args.slice(0,1)}Runtime.dynCall(row[0],XDL[row[1]],args.concat([XDL.userData]))};function __Z12XDL_DoEventsv(){')
    replace('function JB(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0;d=a+328|0;',
            'function JB(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0;webUpdates(a|0,b|0);d=a+328|0;')
    # Observe a successful boost pickup immediately around its effects. The
    # capacity/eligibility guard has already passed; failed pickups never count.
    replace('m=c[j>>2]|0;n=c[m+128>>2]|0;o=c[m+132>>2]|0;m=n+(o<<2)|0;',
            'webLoot(b|0,k|0,0);m=c[j>>2]|0;n=c[m+128>>2]|0;o=c[m+132>>2]|0;m=n+(o<<2)|0;')
    replace('Gc[o&63](q,r,2,b)}else{if(!k)',
            'Gc[o&63](q,r,2,b);webLoot(b|0,k|0,1)}else{if(!k)')
    # PlayerCharacter's health adjustment slot, shared by all four classes.
    # Observe the clamped result so overheal is not reported as healing.
    before = 'function dl(b,d,e,f){b=b|0;d=d|0;e=e|0;f=f|0;var g=0,h=0,i=0;'
    start = text.index(before)
    end = text.index('function ', start + len(before))
    original = text[start:end]
    observed = original.replace(before, before + 'webHealth(b|0,0);').replace(';return}', ';webHealth(b|0,1);return}')
    replace(original, observed)
    return text.encode('utf8')
