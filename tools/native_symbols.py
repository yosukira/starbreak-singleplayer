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
    'ClientWorld::addPlayerInput','ClientWorld::internalUpdate',
    'MessageHandlers::run','ServerConnection::sendMessage',
    'GameClient::handleKeyboardEvent',
    'WorldClient::handleModifyInv','ModifyInv::ModifyInv','ModifyInv::~ModifyInv',
    'SoundMixer::play',
)

# Exact linker names in the 1.14.6 Win32 executable. Reading its own COFF
# table also works on macOS; no Windows program or supplied code is executed.
LINKER_NAMES=dict(zip(HOOKS,(
    '__ZN8TimeUtil5NowMSEv','__ZN8randUtil10randomSeedEv',
    '__ZN8randUtil12randomSeed64Ev','__ZN11WorldClient11updateInputEjb',
    '__ZN11WorldClient12doTimePassesEv',
    '__ZN11WorldClient17handleServerDeltaE11MessageTypePKci',
    '__ZN16ServerConnection6doReadEv',
    '__ZN11ClientWorld14addPlayerInputERK11PlayerInput',
    '__ZN11ClientWorld14internalUpdateEi',
    '__ZN15MessageHandlers3runE11MessageTypePKci',
    '__ZN16ServerConnection11sendMessageE11MessageType',
    '__ZN10GameClient19handleKeyboardEventEii',
    '__ZN11WorldClient15handleModifyInvERK9ModifyInv','__ZN9ModifyInvC2Ev','__ZN9ModifyInvD2Ev',
    '__ZN10SoundMixer4playEPKc9SoundType6ObjKeydd',
)))


def coff_symbols(image):
    """Read only the known replay symbols, rejecting truncated/aliased tables."""
    if len(image)<256 or image[:2]!=b'MZ':raise ValueError('A Win32 game executable is required')
    pe=struct.unpack_from('<I',image,0x3c)[0]
    if pe>len(image)-256 or image[pe:pe+4]!=b'PE\0\0':raise ValueError('Invalid PE header')
    if struct.unpack_from('<H',image,pe+4)[0]!=0x14c or struct.unpack_from('<H',image,pe+24)[0]!=0x10b:
        raise ValueError('Native replay currently requires the Win32 x86 client')
    table,count=struct.unpack_from('<II',image,pe+12)
    strings=table+count*18
    if not table or not 1<=count<=2000000 or table<pe+24 or strings>len(image)-4:
        raise ValueError('Missing or invalid native COFF symbol table')
    size=struct.unpack_from('<I',image,strings)[0]
    if size<4 or size>64*1024*1024 or strings+size>len(image):raise ValueError('Invalid native COFF string table')
    base,sections=parse_pe_sections(image)
    wanted={value:key for key,value in LINKER_NAMES.items()}
    wanted['__ZZN10SoundMixer3getEvE2sm']='SoundMixer::instance'
    wanted['__ZN10SoundMixer4playEPKc9SoundType6ObjKeydd']='SoundMixer::play'
    result={};index=0
    while index<count:
        offset=table+index*18;raw=image[offset:offset+8]
        if raw[:4]==b'\0'*4:
            start=struct.unpack_from('<I',raw,4)[0]
            if not 4<=start<size:raise ValueError('Invalid native COFF symbol name')
            end=image.find(b'\0',strings+start,strings+size)
            if end<0:raise ValueError('Unterminated native COFF symbol name')
            name=image[strings+start:end]
        else:name=raw.rstrip(b'\0')
        value,section,_kind,_storage,aux=struct.unpack_from('<IhHBB',image,offset+8)
        if index+aux>=count:raise ValueError('Truncated native COFF auxiliary record')
        name=wanted.get(name.decode('ascii','replace'))
        if name and section>0:
            if section>len(sections):raise ValueError('Invalid native COFF code section')
            address=base+sections[section-1][1]+value
            if name in result and result[name]!=address:raise ValueError('Ambiguous native replay symbol: '+name)
            result[name]=address
        index+=1+aux
    return result


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
    result=dict(schema=SCHEMA,client=hashlib.sha256(image).hexdigest(),image_base=base,pointer_bytes=4,hooks=rows)
    mixer=symbols.get('SoundMixer::instance')
    if mixer is not None:
        if not any(start<=mixer-base and mixer-base+12<=start+vsize and
                   not struct.unpack_from('<I',image,section_table+index*40+36)[0]&0x20000000
                   for index,(_,start,vsize,raw,raw_size)in enumerate(sections)):
            raise ValueError('Native sound mixer is outside data')
        result['sound_mixer']=dict(rva=mixer-base)
    sound=symbols.get('SoundMixer::play')
    if sound is not None:
        # 1.14.6: this clock selects an SDL sound channel and stamps its age.
        # It is reached only for audible camera-relative coordinates, so its
        # call count cannot be part of deterministic battle execution.
        call=sound+0x79;offset=va_to_offset(sections,base,call)
        if image[offset:offset+1]!=b'\xe8' or call+5+struct.unpack_from('<i',image,offset+1)[0]!=symbols['TimeUtil::NowMS']:
            raise ValueError('Native sound clock call site changed')
        result['sound_clock']=dict(rva=call+5-base)
    return result


def resolve(executable,symquery=None):
    executable=Path(executable)
    image=executable.read_bytes()
    if symquery is None:return manifest(image,coff_symbols(image))
    symquery=Path(symquery)
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
    parser.add_argument('--symquery',type=Path,help='Optional SBPE fallback; otherwise read the executable symbol table directly')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    document=save(args.client,args.symquery,args.output)
    print('Native replay code verified: '+document['client']+' ('+str(len(document['hooks']))+' entry points)')


if __name__=='__main__':main()
