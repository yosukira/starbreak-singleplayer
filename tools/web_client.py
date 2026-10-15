"""Version-checked local adapters for the original 1.14.6 web client.

The original client and assets stay outside the source repository. Patches are
applied in memory when served and never modify the user's source client.
"""
import hashlib

CLIENT_SHA256 = 'f6a285c504a5832a7a7f02d73769672a66bb46b7b1b60c1a136309aeb0727c8d'


def patch_client(source):
    if hashlib.sha256(source).hexdigest() != CLIENT_SHA256:
        raise ValueError('Unsupported web client. Expected StarBreak VR1574197275 / 1.14.6.')
    text = source.decode('utf8')

    def replace(before, after):
        nonlocal text
        if text.count(before) != 1:
            raise ValueError('Web client patch signature mismatch: ' + before[:65])
        text = text.replace(before, after)

    # Add narrow, typed imports to the asm.js module; preserve the game logic.
    replace('"use asm";var a=new global.Int8Array(buffer);',
            '"use asm";var webFrame=env.webFrame;var webUpdates=env.webUpdates;var a=new global.Int8Array(buffer);')
    replace('Module.asmLibraryArg={"abort":abort,',
            'Module.asmLibraryArg={"webFrame":function(p){if(Module.webFrame)Module.webFrame(p)},'
            '"webUpdates":function(p,u){if(Module.webUpdates)Module.webUpdates(p,u)},"abort":abort,')
    # CM serializes CONNECT without RSA, matching make_local_client.py. Both
    # directions still use the existing client's RC4 streams after CONNECT.
    replace('if((d|0)==3){c[i>>2]=0;c[i+4>>2]=0;c[i+8>>2]=0;EUa(e,i)|0;',
            'if((d|0)==255){c[i>>2]=0;c[i+4>>2]=0;c[i+8>>2]=0;EUa(e,i)|0;')
    # Browser -k omits the optional RC4 seed. Match the local server's explicit
    # RC4 handshake with a deterministic seed; this connection is localhost-only.
    replace('CM(c[j>>2]|0,3,g);if(c[x>>2]&16|0?',
            'c[x>>2]=c[x>>2]|16;c[g+16>>2]=0;CM(c[j>>2]|0,3,g);if(c[x>>2]&16|0?')
    replace('function rh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0,v=0,w=0,x=0;d=a+364|0;',
            'function rh(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0,n=0,o=0,p=0,q=0,r=0,s=0,t=0,u=0,v=0,w=0,x=0;webFrame(a|0);d=a+364|0;')
    replace('function JB(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0;d=a+328|0;',
            'function JB(a,b){a=a|0;b=b|0;var d=0,e=0,f=0,g=0,h=0,i=0,j=0,k=0,l=0,m=0;webUpdates(a|0,b|0);d=a+328|0;')
    return text.encode('utf8')
