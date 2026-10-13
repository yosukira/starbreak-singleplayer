"""Patch ``mvmmoclient_local.exe`` so an empty world bounds is not fatal.

``WorldView::recalculateWorldBounds`` (0x4ce720) bails out through
0x4cecc0 when the collected object list produced empty bounds; the retail
build logs ``F WorldView.cc:460] Empty ClientWorld!`` from there.  For the
offline lab a client that joins a still-empty world should survive the
first frames instead of terminating, so the four ``js 0x4cecc0`` guards
are redirected to the function epilogue (0x4cecaa), retaining the condition.
Valid bounds MUST fall through to TileMap::generate. The previous unconditional
jump patch prevented every floor/wall tile from ever being generated.

The input is the already handshake-patched ``mvmmoclient_local.exe``;
the result is ``mvmmoclient_local2.exe``.

Usage:
    python tools/patch_client_bounds.py [--game-dir DIR]
"""

import argparse
import os
import shutil
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_local_client import parse_pe_sections, va_to_offset

DEFAULT_GAME_DIR = os.environ.get('HULK_GAME', 'StarBreak')
SOURCE_NAME = 'mvmmoclient_local.exe'
TARGET_NAME = 'mvmmoclient_local2.exe'

EPILOGUE_VA = 0x4cecaa

def jump_patch(va):
    """Encode ``js 0x4cecaa`` as a six-byte conditional branch."""
    rel = EPILOGUE_VA - (va + 6)
    return b'\x0f\x88' + struct.pack('<i', rel)

GUARDS = [0x4CE8DB, 0x4CE8EC, 0x4CE8FD, 0x4CE90E]

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--game-dir', default=DEFAULT_GAME_DIR)
    options = parser.parse_args(argv)

    source = os.path.join(options.game_dir, SOURCE_NAME)
    target = os.path.join(options.game_dir, TARGET_NAME)
    if not os.path.exists(source):
        print('local client not found: {0}'.format(source))
        return 1
    with open(source, 'rb') as handle:
        data = bytearray(handle.read())
    image_base, sections = parse_pe_sections(data)
    for guard in GUARDS:
        offset = va_to_offset(sections, image_base, guard)
        before = bytes(data[offset:offset + 6])
        if before == jump_patch(guard):
            print('0x{0:x} already patched'.format(guard))
            continue
        if not (before.startswith(b'\x0f\x88') or
                before == bytes([0xE9])+struct.pack('<i',EPILOGUE_VA-(guard+5))+b'\x90'):
            raise ValueError(
                'unexpected bytes at 0x{0:x}: {1}'.format(guard,
                                                          before.hex()))
        after = jump_patch(guard)
        data[offset:offset + 6] = after
        print('0x{0:x}: {1} -> {2}'.format(guard, before.hex(), after.hex()))
    with open(target, 'wb') as handle:
        handle.write(bytes(data))
    shutil.copystat(source, target)
    print('wrote {0}'.format(target))
    return 0

if __name__ == '__main__':
    sys.exit(main())
