"""Create ``mvmmoclient_local.exe``, a copy of the retail client that talks
plaintext MT_CONNECT so a local server can complete the handshake.

Retail flow (ServerConnection::sendMessage @0x472b30):

    cmp dword ptr [ebp+8], 3        ; MessageType == MT_CONNECT?
    je  0x472e30                    ; -> RSAEncrypt::multiEncrypt(payload)

The client holds only the server's *public* key, so a local server can
never read ``Connect.enckey_`` (the 64-byte RC4 seed) out of that blob.
The patch rewrites the compare immediate from 3 to 0xff, which makes the
branch dead and sends the Connect message through the normal plaintext
path.  Behaviour against the retail server is intentionally changed; this
copy exists purely for offline local sessions.

Usage:
    python tools/make_local_client.py [--game-dir DIR] [--force]
"""

import argparse
import os
import shutil
import struct
import sys

DEFAULT_GAME_DIR = os.environ.get('HULK_GAME', 'StarBreak')
SOURCE_NAME = 'mvmmoclient.exe'
TARGET_NAME = 'mvmmoclient_local.exe'

PATCH_VA = 0x472bb7
PATCH_BEFORE = b'\x83\x7d\x08\x03'
PATCH_AFTER = b'\x83\x7d\x08\xff'

def parse_pe_sections(data):
    pe_offset = struct.unpack_from('<I', data, 0x3C)[0]
    if data[pe_offset:pe_offset + 4] != b'PE\0\0':
        raise ValueError('not a PE image')
    coff = pe_offset + 4
    section_count = struct.unpack_from('<H', data, coff + 2)[0]
    optional_size = struct.unpack_from('<H', data, coff + 16)[0]
    optional = coff + 20
    image_base = struct.unpack_from('<I', data, optional + 28)[0]
    sections = []
    section_offset = optional + optional_size
    for index in range(section_count):
        offset = section_offset + index * 40
        name = data[offset:offset + 8].rstrip(b'\0').decode('ascii')
        virtual_size, virtual_address, raw_size, raw_offset = (
            struct.unpack_from('<IIII', data, offset + 8))
        sections.append((name, virtual_address, virtual_size,
                         raw_offset, raw_size))
    return image_base, sections

def va_to_offset(sections, image_base, va):
    rva = va - image_base
    for _name, section_va, vsize, raw, raw_size in sections:
        if section_va <= rva < section_va + max(vsize, raw_size):
            delta = rva - section_va
            if delta < raw_size:
                return raw + delta
    raise ValueError('VA 0x{0:x} is not mapped to file data'.format(va))

def patch_image(data):
    image_base, sections = parse_pe_sections(data)
    offset = va_to_offset(sections, image_base, PATCH_VA)
    current = data[offset:offset + len(PATCH_BEFORE)]
    if current == PATCH_AFTER:
        return offset, False
    if current != PATCH_BEFORE:
        raise ValueError(
            'unexpected bytes at 0x{0:x}: {1}'.format(PATCH_VA,
                                                      current.hex()))
    patched = bytearray(data)
    patched[offset:offset + len(PATCH_AFTER)] = PATCH_AFTER
    return offset, bytes(patched), True

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-dir', default=DEFAULT_GAME_DIR)
    parser.add_argument('--force', action='store_true',
                        help='overwrite an existing local client copy')
    options = parser.parse_args(argv)

    source = os.path.join(options.game_dir, SOURCE_NAME)
    target = os.path.join(options.game_dir, TARGET_NAME)
    if not os.path.exists(source):
        print('retail client not found: {0}'.format(source))
        return 1
    if os.path.exists(target) and not options.force:
        print('{0} already exists (use --force to rebuild)'.format(target))
        return 0

    with open(source, 'rb') as handle:
        data = handle.read()
    offset, patched, changed = patch_image(data)
    if not changed:
        print('source image already carries the patch; copying as-is')
        patched = data
    with open(target, 'wb') as handle:
        handle.write(patched)
    shutil.copystat(source, target)
    print('wrote {0}'.format(target))
    print('patched {0} bytes at file offset 0x{1:x} (VA 0x{2:x}): {3} -> {4}'
          .format(len(PATCH_AFTER),
                  offset,
                  PATCH_VA,
                  PATCH_BEFORE.hex(),
                  PATCH_AFTER.hex()))
    return 0

if __name__ == '__main__':
    sys.exit(main())
