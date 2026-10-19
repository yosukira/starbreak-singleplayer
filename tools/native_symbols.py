"""Resolve replay entry points from the player's own Win32 game executable.

The result contains code identities and offsets only. No executable, paths,
machine identifiers or account data are included in the manifest.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess

from tools.make_local_client import parse_pe_sections,va_to_offset

SCHEMA='starbreak-native-code-v1'
# Symbol discovery proves locations, not a C++ call signature. The replay
# integration must independently verify each prototype before using NativeHook.
HOOKS=(
    'TimeUtil::NowMS','randUtil::randomSeed','randUtil::randomSeed64',
    'WorldClient::updateInput','WorldClient::doTimePasses',
    'WorldClient::handleServerDelta','ServerConnection::doRead',
)


def manifest(image,symbols):
    if image[:2]!=b'MZ' or len(image)<256:raise ValueError('A Win32 game executable is required')
    pe=struct.unpack_from('<I',image,0x3c)[0]
    if not 0<=pe<=len(image)-256 or image[pe:pe+4]!=b'PE\0\0':raise ValueError('Invalid PE header')
    if struct.unpack_from('<H',image,pe+4)[0]!=0x14c or struct.unpack_from('<H',image,pe+24)[0]!=0x10b:
        raise ValueError('Native replay currently requires the Win32 x86 client')
    base,sections=parse_pe_sections(image)
    rows={}
    for name in HOOKS:
        address=symbols.get(name)
        if type(address)is not int or not base<=address<=0xffffffff:raise ValueError('Missing native replay symbol: '+name)
        offset=va_to_offset(sections,base,address)
        section_table=pe+24+struct.unpack_from('<H',image,pe+20)[0]
        if not any(start<=address-base and address-base+16<=start+min(vsize,raw_size)
                   and struct.unpack_from('<I',image,section_table+index*40+36)[0]&0x20000000
                   for index,(_,start,vsize,raw,raw_size)in enumerate(sections)):
            raise ValueError('Native replay target is outside executable code')
        prefix=image[offset:offset+16]
        if len(prefix)!=16 or len(set(prefix))<3:raise ValueError('Invalid native code signature: '+name)
        rows[name]=dict(rva=address-base,signature=prefix.hex())
    return dict(schema=SCHEMA,client=hashlib.sha256(image).hexdigest(),image_base=base,pointer_bytes=4,hooks=rows)


def resolve(executable,symquery):
    executable,symquery=Path(executable),Path(symquery)
    image=executable.read_bytes()
    result=subprocess.run([str(symquery),'-e',str(executable),'--list'],check=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=45)
    if len(result.stdout)>64*1024*1024:raise ValueError('Native symbol table is too large')
    base,_=parse_pe_sections(image);symbols={}
    for line in result.stdout.decode('utf8','replace').splitlines():
        columns=line.split()
        if len(columns)<2 or columns[0]not in HOOKS:continue
        value=columns[1].split('-',1)[0]
        if not re.fullmatch(r'(?:0x)?[0-9a-fA-F]+',value):raise ValueError('Invalid native symbol offset')
        name=columns[0];address=base+int(value,16)
        if name in symbols and symbols[name]!=address:raise ValueError('Ambiguous native replay symbol: '+name)
        symbols[name]=address
    return manifest(image,symbols)


def save(executable,symquery,destination):
    from tools.update import atomic_json
    document=resolve(executable,symquery)
    atomic_json(Path(destination),document)
    return document


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--client',required=True,type=Path)
    parser.add_argument('--symquery',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    document=save(args.client,args.symquery,args.output)
    print('Native replay code verified: '+document['client']+' ('+str(len(document['hooks']))+' entry points)')


if __name__=='__main__':main()
