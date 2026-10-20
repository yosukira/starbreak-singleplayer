"""Execute Win32 adapters in an x86 CPU, without a game or Windows host."""
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

from plugins._native_replay_hooks import thiscall_callback,thiscall_original,clock_callback,NativeHook,NativeHookGroup
from tools import native_symbols

try:
    from unicorn import Uc,UC_ARCH_X86,UC_MODE_32
    from unicorn.x86_const import (UC_X86_REG_ESP,UC_X86_REG_ECX,UC_X86_REG_EAX,
        UC_X86_REG_EDX,UC_X86_REG_EBX,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP)
except ImportError:Uc=None


def executable():
    image=bytearray(2048);image[:2]=b'MZ';struct.pack_into('<I',image,0x3c,128)
    image[128:132]=b'PE\0\0';struct.pack_into('<HH',image,132,0x14c,1)
    struct.pack_into('<H',image,148,224);struct.pack_into('<H',image,152,0x10b)
    struct.pack_into('<I',image,180,0x400000)
    image[376:384]=b'.text\0\0\0'
    struct.pack_into('<IIII',image,384,1024,0x1000,1024,512)
    struct.pack_into('<I',image,412,0x60000020)
    symbols={}
    for index,name in enumerate(native_symbols.HOOKS):
        offset=512+index*32
        image[offset:offset+16]=b'\x55\x89\xe5\x83\xec\x48\xb8'+bytes([index])+b'\x12\x34\x56\x78\x90\xc9\xc3\x90'
        symbols[name]=0x401000+index*32
    return bytes(image),symbols


class NativeSymbolTests(unittest.TestCase):
    def coff_image(self,extra=()):
        image,symbols=executable();image=bytearray(image);table=len(image)
        names=list(native_symbols.HOOKS)+list(extra);strings=bytearray(b'\0'*4);rows=bytearray()
        for index,name in enumerate(names):
            offset=len(strings);strings.extend(native_symbols.LINKER_NAMES[name].encode()+b'\0')
            value=symbols[name]-0x401000+(32 if index>=len(native_symbols.HOOKS) else 0)
            rows.extend(struct.pack('<IIIhHBB',0,offset,value,1,0x20,2,0))
        struct.pack_into('<I',strings,0,len(strings))
        struct.pack_into('<II',image,140,table,len(names))
        return bytes(image+rows+strings),symbols

    def test_direct_coff_resolution_uses_only_known_symbols_and_does_not_execute_a_program(self):
        image,symbols=self.coff_image()
        self.assertEqual(native_symbols.coff_symbols(image),symbols)
        with tempfile.TemporaryDirectory() as directory:
            game=Path(directory)/'client.exe';game.write_bytes(image)
            with mock.patch('tools.native_symbols.subprocess.run',side_effect=AssertionError('No executable may run')):
                self.assertEqual(native_symbols.resolve(game),native_symbols.manifest(image,symbols))

    def test_corrupt_coff_names_counts_auxiliary_records_and_duplicate_addresses_are_rejected(self):
        image,_=self.coff_image();table=2048;count=len(native_symbols.HOOKS)
        for changed in ('count','string-size','name','terminator','aux'):
            bad=bytearray(image)
            if changed=='count':struct.pack_into('<I',bad,144,2000001)
            elif changed=='string-size':struct.pack_into('<I',bad,table+count*18,len(image))
            elif changed=='name':struct.pack_into('<I',bad,table+4,3)
            elif changed=='terminator':bad[-1]=65
            else:bad[table+(count-1)*18+17]=1
            with self.subTest(changed=changed),self.assertRaises(ValueError):native_symbols.coff_symbols(bad)
        duplicate,_=self.coff_image((native_symbols.HOOKS[0],))
        with self.assertRaisesRegex(ValueError,'Ambiguous'):native_symbols.coff_symbols(duplicate)

    def test_manifest_has_only_typed_code_identities_and_offsets(self):
        image,symbols=executable();doc=native_symbols.manifest(image,symbols)
        self.assertEqual(doc['pointer_bytes'],4)
        self.assertEqual(len(doc['client']),64)
        self.assertEqual(set(doc['hooks']),set(native_symbols.HOOKS))
        self.assertNotIn('path',json.dumps(doc))
        self.assertEqual(set(doc['hooks']['WorldClient::updateInput']),{'rva','signature'})
        with self.assertRaises(ValueError):native_symbols.manifest(image,{})
        invalid=bytearray(image);struct.pack_into('<H',invalid,132,0x8664)
        with self.assertRaisesRegex(ValueError,'Win32'):native_symbols.manifest(bytes(invalid),symbols)
        wrong=dict(symbols);wrong['TimeUtil::NowMS']=0x402000
        with self.assertRaises(ValueError):native_symbols.manifest(image,wrong)
        noncode=bytearray(image);struct.pack_into('<I',noncode,412,0x40000040)
        with self.assertRaisesRegex(ValueError,'executable code'):native_symbols.manifest(bytes(noncode),symbols)

    def test_sbpe_symbol_resolution_checks_missing_and_ambiguous_entries(self):
        image,symbols=executable()
        listing='\n'.join(name+' '+format(address-0x400000,'x')+'-20' for name,address in symbols.items()).encode()
        with tempfile.TemporaryDirectory() as d:
            game=Path(d)/'client.exe';game.write_bytes(image)
            with mock.patch('tools.native_symbols.subprocess.run',return_value=mock.Mock(stdout=listing))as run:
                output=Path(d)/'code.json';doc=native_symbols.save(game,Path(d)/'symquery.exe',output)
                self.assertEqual(json.loads(output.read_text()),doc)
                self.assertEqual(run.call_args.args[0], [str(Path(d)/'symquery.exe'),'-e',str(game),'--list'])
            with mock.patch('tools.native_symbols.subprocess.run',return_value=mock.Mock(stdout=listing+b'\nTimeUtil::NowMS 1100-20')):
                with self.assertRaisesRegex(ValueError,'Ambiguous'):native_symbols.resolve(game,'symquery')

    def test_native_hook_refuses_the_host_64_bit_abi_before_allocating(self):
        ffi=mock.Mock();ffi.sizeof.return_value=8
        with self.assertRaisesRegex(ValueError,'32-bit'):NativeHook(ffi,mock.Mock(),0x401000,b'12345678')
        for bad in (-1,True,7):
            with self.assertRaises(ValueError):thiscall_original(0x401000,bad)

    def test_recording_callback_failures_pass_through_exactly_once(self):
        original=mock.Mock(return_value=77)
        ffi=mock.Mock();ffi.sizeof.return_value=4;ffi.buffer.return_value=b'12345678'
        ffi.NULL=None;ffi.callback.side_effect=lambda signature,callback:callback
        def cast(signature,value):
            if signature=='uintptr_t':return 0x101000
            if '(*)'in signature:return original
            return value
        ffi.cast.side_effect=cast
        lib=mock.Mock();lib.subhook_new.return_value=1;lib.subhook_get_trampoline.return_value=0x102000
        lib.subhook_is_installed.return_value=True
        for when in ('before','after','twice','remove'):
            with self.subTest(when=when),mock.patch.dict('os.environ',{'HARDLAB_LIVE_SESSION':'1'}):
                def replacement(call,*args):
                    if when=='remove':hook.remove()
                    if when!='before':call(*args)
                    if when=='twice':call(*args)
                    raise OSError('private path is not retained')
                hook=NativeHook(ffi,lib,0x401000,b'12345678',result='int',arguments=('unsigned int',),replace=replacement)
                hook.allocate=mock.Mock(return_value=0x103000)
                hook.install();original.reset_mock()
                self.assertEqual(hook.callback(0x778899aa,3),77)
                original.assert_called_once_with(0x778899aa,3)
                self.assertEqual(hook.error,'RuntimeError'if when in ('twice','remove')else'OSError')
                self.assertEqual(hook.active_calls,0)
                lib.subhook_is_installed.return_value=False;hook.remove()
                self.assertTrue(hook.retired)
                self.assertIsNotNone(hook.callback)
                self.assertIsNotNone(hook.original)
                with self.assertRaisesRegex(RuntimeError,'new native hook'):hook.install()
                lib.subhook_is_installed.return_value=True
        with mock.patch.dict('os.environ',{'HARDLAB_LIVE_SESSION':'0'}):
            hook=NativeHook(ffi,lib,0x401000,b'12345678')
            with self.assertRaisesRegex(ValueError,'offline'):hook.install()

    def test_boundary_setup_preflights_all_targets_and_rolls_back_partial_install(self):
        first,second=mock.Mock(target=0x401000),mock.Mock(target=0x402000)
        group=NativeHookGroup((first,second))
        second.check.side_effect=ValueError('different executable')
        with self.assertRaises(ValueError):group.install()
        first.install.assert_not_called();second.install.assert_not_called()
        second.check.side_effect=None;second.install.side_effect=RuntimeError('hook failed')
        with self.assertRaisesRegex(RuntimeError,'hook failed'):group.install()
        first.remove.assert_called_once();second.remove.assert_called_once()


@unittest.skipIf(Uc is None,'install unicorn to execute the Win32 CPU adapter checks')
class NativeAdapterCPUTests(unittest.TestCase):
    def machine(self,code,arguments,thiscall=True):
        uc=Uc(UC_ARCH_X86,UC_MODE_32)
        uc.mem_map(0x100000,0x20000);uc.mem_map(0x200000,0x10000)
        uc.mem_write(0x100000,code)
        stack=0x208000
        uc.mem_write(stack,struct.pack('<I',0x110000)+b''.join(struct.pack('<I',v)for v in arguments))
        uc.reg_write(UC_X86_REG_ESP,stack);uc.reg_write(UC_X86_REG_ECX,0x778899aa)
        for reg in (UC_X86_REG_EBX,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP):uc.reg_write(reg,0x12345678)
        return uc,stack

    def assert_preserved(self,uc):
        for reg in (UC_X86_REG_EBX,UC_X86_REG_ESI,UC_X86_REG_EDI,UC_X86_REG_EBP):
            self.assertEqual(uc.reg_read(reg),0x12345678)
        self.assertEqual(uc.reg_read(UC_X86_REG_EAX),0x89abcdef)
        self.assertEqual(uc.reg_read(UC_X86_REG_EDX),0x01234567)

    def observer(self,count,thiscall):
        code=bytearray(b'\xff\x05'+struct.pack('<I',0x111100))
        if thiscall:code.extend(b'\x89\x0d'+struct.pack('<I',0x111104))
        for index in range(count+(not thiscall)):
            code.extend(b'\x8b\x44\x24'+bytes([4+index*4]))
            code.extend(b'\xa3'+struct.pack('<I',0x111108+index*4))
        code.extend(b'\xb8\xef\xcd\xab\x89\xba\x67\x45\x23\x01')
        code.extend(b'\xc2'+struct.pack('<H',count*4)if thiscall and count else b'\xc3')
        return bytes(code)

    def test_thiscall_callback_preserves_arguments_return_registers_and_callee_pop(self):
        for count in range(7):
            with self.subTest(arguments=count):
                arguments=list(range(100,100+count))
                uc,stack=self.machine(thiscall_callback(0x101000,count),arguments)
                uc.mem_write(0x101000,self.observer(count,False))
                uc.emu_start(0x100000,0x110000,count=1000)
                actual=struct.unpack('<'+'I'*(count+1),bytes(uc.mem_read(0x111108,(count+1)*4)))
                self.assertEqual(actual,(0x778899aa,*arguments))
                self.assertEqual(uc.reg_read(UC_X86_REG_ESP),stack+4+count*4)
                self.assert_preserved(uc)

    def test_clock_callback_passes_return_site_without_changing_cdecl_stack_or_result(self):
        uc,stack=self.machine(clock_callback(0x101000),[])
        # Save the sole callback argument, then return the expected 64-bit
        # register pair used by assert_preserved.
        uc.mem_write(0x101000,b'\x8b\x44\x24\x04\xa3\x08\x11\x11\x00\xb8\xef\xcd\xab\x89\xba\x67\x45\x23\x01\xc3')
        uc.emu_start(0x100000,0x110000,count=1000)
        self.assertEqual(struct.unpack('<I',bytes(uc.mem_read(0x111108,4)))[0],0x110000)
        self.assertEqual(uc.reg_read(UC_X86_REG_ESP),stack+4)
        self.assert_preserved(uc)

    def test_callback_can_call_original_once_with_both_call_conventions_balanced(self):
        for count in range(7):
            with self.subTest(arguments=count):
                arguments=list(range(100,100+count))
                uc,stack=self.machine(thiscall_callback(0x101000,count),arguments)
                callback=bytearray(b'\x55\x89\xe5')
                for i in reversed(range(count+1)):callback.extend(b'\xff\x75'+bytes([8+i*4]))
                callback.extend(b'\xb8'+struct.pack('<I',0x102000)+b'\xff\xd0\x83\xc4'+bytes([(count+1)*4])+b'\xc9\xc3')
                uc.mem_write(0x101000,bytes(callback))
                uc.mem_write(0x102000,thiscall_original(0x103000,count))
                uc.mem_write(0x103000,self.observer(count,True))
                uc.emu_start(0x100000,0x110000,count=1000)
                self.assertEqual(struct.unpack('<II',bytes(uc.mem_read(0x111100,8))),(1,0x778899aa))
                self.assertEqual(list(struct.unpack('<'+'I'*count,bytes(uc.mem_read(0x111108,count*4)))),arguments)
                self.assertEqual(uc.reg_read(UC_X86_REG_ESP),stack+4+count*4)
                self.assert_preserved(uc)
