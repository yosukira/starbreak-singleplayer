"""Known input-hook coexistence without executing native machine code."""
import hashlib
import struct
from types import SimpleNamespace as NS
import unittest
from unittest import mock

from plugins import _hardlab_livebridge as bridge


class InputDetourTests(unittest.TestCase):
    def fixture(self, hooked=True):
        address = bridge.WORLD_CLIENT_SET_INPUT
        destination, trampoline, callback = 0x510000, 0x520000, 0x530000
        original = bytes.fromhex('5589e583ec78')+bytes(range(220))
        digest = hashlib.sha256(original).hexdigest()
        patcher = mock.patch.object(bridge, 'WORLD_CLIENT_SET_INPUT_SHA256', digest)
        patcher.start()
        self.addCleanup(patcher.stop)
        stub = (bytes.fromhex('9c60ff742428ff74241cb8')+struct.pack('<I', callback)+
                bytes.fromhex('ffd083c40889442428619db8')+struct.pack('<I', trampoline)+bytes.fromhex('ffe0'))
        memory = {address: (b'\xe9'+struct.pack('<i', destination-address-5)+original[5:]) if hooked else original,
                  destination: stub,
                  trampoline: original[:6]+b'\xe9'+struct.pack('<i', address+6-trampoline-11)}
        reads = []

        def buffer(pointer, size):
            reads.append((pointer, size))
            for start, data in memory.items():
                if start <= pointer and pointer+size <= start+len(data):
                    return data[pointer-start:pointer-start+size]
            raise AssertionError('Attempted to read an unowned address')

        applied = []
        ffi = NS(NULL=None, buffer=buffer)
        ffi.cast = lambda kind, value: (lambda world, mask: applied.append((world, mask))) if '(*)' in kind else value
        b = object.__new__(bridge.LiveBridge)
        b.ffi = ffi
        b.lib = NS(subhook_is_installed=mock.Mock(return_value=True),
                   subhook_get_trampoline=mock.Mock(return_value=trampoline))
        b.refs = NS(WorldClient=0x540000)
        native = NS(refs=b.refs, ffi=ffi, lib=b.lib, input_hook=0x55, input_filter_version=1,
                    input_callback=callback, allocations=[destination],
                    abi=dict(set_input=dict(address=address, size=226, sha256=digest)))
        assistant = NS(native=native)
        native.owner = assistant
        plugin = NS(assistant=assistant, native=native)
        b.refs.manager = NS(_active={'plugin_combat_assist': plugin})
        written = []
        b.hook = NS(_kernel32=NS(VirtualAlloc=lambda *args: 0x560000,
                                 GetCurrentProcess=lambda: 1, FlushInstructionCache=lambda *args: True),
                    _ctypes=NS(memmove=lambda ptr, code, size: written.append(code[:size])))
        b._input_setter = None
        b._input_stub = 0
        return b, native, memory, reads, applied, written

    def test_unmodified_entry_still_works_without_an_assistant(self):
        b, _n, _memory, _reads, _applied, _written = self.fixture(hooked=False)
        b.refs.manager._active.clear()
        self.assertTrue(b._input_entry_supported())

    def test_recognized_hook_calls_the_entry_not_the_unfiltered_trampoline(self):
        b, _n, _memory, _reads, applied, written = self.fixture()
        b._native_set_input(1)
        self.assertEqual(applied, [(b.refs.WorldClient, 1)])
        self.assertEqual(len(written), 1)
        self.assertIn(b'\xb8'+struct.pack('<I', bridge.WORLD_CLIENT_SET_INPUT), written[0])
        self.assertNotIn(b'\xb8'+struct.pack('<I', 0x520000), written[0])

    def test_unknown_jump_target_is_rejected_without_dereferencing_it(self):
        b, _n, memory, reads, _applied, _written = self.fixture()
        address = bridge.WORLD_CLIENT_SET_INPUT
        memory[address] = b'\xe9'+struct.pack('<i', 0x610000-address-5)+memory[address][5:]
        self.assertFalse(b._input_entry_supported())
        self.assertNotIn(0x610000, [address for address, _size in reads])

    def test_stale_or_uninstalled_adapter_cannot_authorize_a_patch(self):
        for kind in ('owner', 'plugin', 'uninstalled'):
            with self.subTest(kind=kind):
                b, n, _memory, reads, _applied, _written = self.fixture()
                if kind == 'owner':
                    n.owner = object()
                elif kind == 'plugin':
                    b.refs.manager._active['plugin_combat_assist'].native = object()
                else:
                    b.lib.subhook_is_installed.return_value = False
                self.assertFalse(b._input_entry_supported())
                self.assertEqual(reads, [(bridge.WORLD_CLIENT_SET_INPUT, 6)])

    def test_changed_callback_or_original_body_is_rejected(self):
        for target in ('callback', 'body', 'entry_sixth_byte', 'trampoline', 'return_address'):
            with self.subTest(target=target):
                b, n, memory, _reads, _applied, _written = self.fixture()
                if target == 'callback':
                    n.input_callback += 4
                elif target == 'body':
                    address = bridge.WORLD_CLIENT_SET_INPUT
                    memory[address] = memory[address][:-1]+b'\xff'
                elif target == 'entry_sixth_byte':
                    address = bridge.WORLD_CLIENT_SET_INPUT
                    memory[address] = memory[address][:5]+b'\x90'+memory[address][6:]
                elif target == 'return_address':
                    memory[0x520000] = memory[0x520000][:-1]+b'\x00'
                else:
                    memory[0x520000] = b'\x90'*6
                self.assertFalse(b._input_entry_supported())
                with self.assertRaisesRegex(RuntimeError, 'unsupported WorldClient'):
                    b._native_set_input(1)
                self.assertIsNone(b._input_setter)

    def test_cached_caller_does_not_authorize_a_replaced_hook(self):
        b, _n, _memory, _reads, applied, written = self.fixture()
        b._native_set_input(1)
        b.refs.manager._active.clear()
        with self.assertRaisesRegex(RuntimeError, 'unsupported WorldClient'):
            b._native_set_input(2)
        self.assertEqual(applied, [(b.refs.WorldClient, 1)])
        self.assertEqual(len(written), 1)


if __name__ == '__main__':
    unittest.main()
