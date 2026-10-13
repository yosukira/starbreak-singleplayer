"""Build an MT_CREATE_WORLD payload for the local server.

CreateWorld is *not* a plain protobuf on the wire: the client first runs
``compressUtil::decompress`` on it (see ``GameClient::handleCreateWorld``),
which expects ``[uint32 big-endian uncompressed size][LZ4 block]``.

This tool serialises a CreateWorld message from the DWARF-driven layout
table, compresses it with a minimal (literals-only) LZ4 encoder and prints
the hex blob that can be fed to the local server's ``rawsend 10 <hex>``
console command.

Usage:
    python tools/make_create_world.py --pvid hulk
    python tools/make_create_world.py --objects objects.json
    python tools/make_create_world.py --player-stub  # one visible object
"""

import argparse
import json
import os
import struct
import sys

TOOL_DIR = os.path.dirname(os.path.abspath(__file__))
LAB_ROOT = os.path.dirname(TOOL_DIR)
if LAB_ROOT not in sys.path:
    sys.path.insert(0, LAB_ROOT)

from localserver import pb_layout

def lz4_literal_block(data):
    """Encode ``data`` as a legal LZ4 block made of a single literal run.

    The LZ4 frame format allows the last sequence to contain literals only,
    so this is a fully valid (if unoptimised) block that
    ``LZ4_decompress_safe`` decodes byte for byte.
    """
    out = bytearray()
    length = len(data)
    out.append((min(length, 15) << 4) & 0xF0)
    if length >= 15:
        rest = length - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    out += data
    return bytes(out)

def wrap_compress(raw):
    """``[u32 network-order uncompressed size][LZ4 block]``.

    ``compressUtil::decompress`` (shared/util/compressUtil.cc:51) reads the
    size with ``ntohl`` before calling ``LZ4_decompress_safe``, so the prefix
    is big-endian even though every other integer on the wire is little-endian.
    A little-endian prefix makes the client parse a ~900 MB length and abort
    the world load (``basic_string::resize`` when it turns into a parse of an
    empty buffer), which is what kept the room free of terrain.
    """
    return struct.pack('>I', len(raw)) + lz4_literal_block(raw)

def decode_literals(block, size):
    """Decode the literals-only LZ4 blocks we emit ourselves."""
    out = bytearray()
    offset = 0
    while offset < len(block) and len(out) < size:
        token = block[offset]
        offset += 1
        length = token >> 4
        if length == 15:
            while True:
                extra = block[offset]
                offset += 1
                length += extra
                if extra != 255:
                    break
        out += block[offset:offset + length]
        offset += length
        if offset >= len(block):
            break
        distance = block[offset] | (block[offset + 1] << 8)
        offset += 2
        match = token & 15
        if match == 15:
            while True:
                extra = block[offset]
                offset += 1
                match += extra
                if extra != 255:
                    break
        match += 4
        start = len(out) - distance
        for index in range(match):
            out.append(out[start + index])
    return bytes(out)

def unwrap_decompress(blob):
    """Inverse of :func:`wrap_compress` for blobs we re-wrap locally."""
    size = struct.unpack('>I', bytes(blob[:4]))[0]
    body = bytes(blob[4:])
    try:
        import lz4.block
        return lz4.block.decompress(body, uncompressed_size=size)
    except ImportError:
        return decode_literals(body, size)

def stub_props():
    """A harmless visible object (the retail ``assassin`` sprite)."""
    return {
        'xmp_': 4 * 256 * 256,
        'ymp_': 4 * 256 * 256,
        'wmp_': 40 * 256,
        'hmp_': 40 * 256,
        'terraintype_': 1,
        'blocktype_': 1,
        'ownertype_': 1,
        'draw_': True,
        'hitpoints_': 100,
        'maxhitpoints_': 100,
        'vid_': 'assassin',
    }

def build_create_world(objects, worldid=1, zone='hulk', client_world=False,
                       decoration=None, gdv=None, seed=None, gravity=None):
    'Serialise one ``CreateWorld`` snapshot.'
    world_objs = []
    for item in objects:
        world_objs.append({
            'objid_': int(item['objid']),
            'props_': item['props'],
        })
    world = {
        'ownerid_': worldid,
        'obj_': world_objs,
    }
    worldstate = {
        'serverworld_': world,
    }
    if client_world:
        worldstate['clientworld_'] = [world]
    worldprops = {
        'zoneid_': worldid,
        'gameid_': 1,
        'zone_': zone,
        'floor_': 0,

        'gdv_': int(gdv or 0),
        'seed_': int(seed or 0),
    }
    if gravity is not None:

        worldprops['gravity_'] = int(gravity)
    if decoration:

        worldprops['decoration_'] = decoration
    return {
        'worldid_': worldid,
        'worldprops_': worldprops,
        'worldstate_': worldstate,

        'spawnobj_': {'worldid_': worldid, 'objid_': 0},
    }

def load_objects(path):
    with open(path, 'r') as handle:
        return json.load(handle)

def build_subworld_delta(worldid=1):
    """``MT_SERVER_DELTA`` that only materialises the room's sub-world.

    The retail flow is ``CreateWorld`` (world properties + object snapshot)
    followed by a delta whose first entry is ``CreateSubWorld``.  The client
    builds its tile map from the *sub-world* object list, so without this
    delta the room renders sprites but has no terrain and no collision - the
    player and camera then fall through the floor.

    Objects are deliberately not repeated here: everything in the snapshot is
    already inserted by ``CreateWorld``, and a second insertion makes
    ``SubWorld::addObj`` abort with "Duplicate object insertion".
    """
    return {'createsubworld_': {'worldid_': int(worldid)}}

def build_bootstrap_delta(objects, worldid=1, creates=True):
    """A MT_SERVER_DELTA(12) carrying one GeneralUpdate create per object.

    Real servers follow CreateWorld with a burst of deltas; the client
    renders the world before the first frames and expects objects to be
    present by then.
    """
    message = {'createsubworld_': {'worldid_': int(worldid)}}
    if creates:
        general_updates = []
        for item in objects:
            general_updates.append({
                'offset_': 0,
                'create_': {
                    'objid_': int(item['objid']),
                    'props_': item['props'],
                },
            })
        message['worldupdate_'] = {
            'worldid_': worldid,
            'generalupdates_': general_updates,
        }
    return message

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--pvid', default='hulk')
    parser.add_argument('--worldid', type=int, default=1)
    parser.add_argument('--objects',
                        help='JSON list of {objid, props} objects')
    parser.add_argument('--player-stub', action='store_true',
                        help='include a single visible stub object')
    parser.add_argument('--delta', action='store_true',
                        help='emit the bootstrap delta instead of CreateWorld')
    parser.add_argument('--out', help='write hex blob to this file')
    options = parser.parse_args(argv)

    if options.objects:
        objects = load_objects(options.objects)
    elif options.player_stub:
        objects = [{'objid': 1, 'props': stub_props()}]
    else:
        objects = [{'objid': 1, 'props': stub_props()}]

    layouts = pb_layout.default_layouts()
    if options.delta:
        message = build_bootstrap_delta(objects, options.worldid)
        blob = layouts.encode('Delta', message)
        print('Delta raw={0} bytes'.format(len(blob)))
        hex_blob = blob.hex()
        if options.out:
            with open(options.out, 'w') as handle:
                handle.write(hex_blob)
            print('written to {0}'.format(options.out))
        print(hex_blob)
        return 0
    message = build_create_world(objects, options.worldid, options.pvid)
    raw = layouts.encode('CreateWorld', message)
    blob = wrap_compress(raw)
    print('CreateWorld raw={0} bytes compressed={1} bytes'.format(
        len(raw), len(blob)))
    hex_blob = blob.hex()
    if options.out:
        with open(options.out, 'w') as handle:
            handle.write(hex_blob)
        print('written to {0}'.format(options.out))
    print(hex_blob)
    return 0

if __name__ == '__main__':
    sys.exit(main())
