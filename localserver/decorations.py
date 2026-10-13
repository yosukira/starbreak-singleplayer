"""Per-zone ``DecorationDescription`` builders for the local server.

The retail client builds its whole room rendering from
``WorldProperties.decoration_``:

    WorldView::WorldView(WorldView*, int, int, ClientWorld*)   WorldView.cc:56
        ClientWorld * world = ...;
        DecorationDescription const & deco = world->props().decoration();
        if (&deco == NULL) return;                     // <- no tile map at all
        tileMap_ = new TileMap(this, deco, world->props().gdv());
        recalculateWorldBounds();
        generateDecoration(deco, world->props().seed());

Without a decoration the client never allocates ``TileMap``/``Tiles``, so a
room made of ``terraintype_ == TERRAIN_PLATFORM`` objects renders as an empty
black screen even though the sprites are all there.

The three tile lists are resolved through the client's own texture metadata
(``TileMap::getTileSetId(vid)`` -> ``TextureManager::findTileSet``), which is
why the vids below are the ``tileviddesc_`` entries of ``<pvid>.m.bpb``:

    hulk.m.bpb -> hulk.external(0) hulk.external2(1) hulk.internal(2)
                  hulk.ledge(3) hulk.wall(4) hulk.wallcp(5)

``TilesDescription`` only needs ``vid_``.  ``TileMap::TileMap`` (0x4cca60)
resolves a set through ``TileMap::getTileSetId(vid)`` only when the entry's
``z_`` is ``-1`` (the field's default, i.e. omitted) or equals the world's
``gdv``; the retail forest decoration ships ``externaltiles_`` as bare
``[{vid_: "forest.external"}]`` and resolves the hard/normal variation from
``WorldProperties.gdv_`` instead.  Writing an explicit ``z_`` of 60 here made
the loop skip every entry (60 != -1 and 60 != gdv), leaving ``has60_`` false
and the whole tile map black.
"""

import os
import re

try:
    from . import pb_layout
except (ImportError, ValueError):
    import pb_layout

DEFAULT_DATA_DIR = (
    os.path.join(os.environ.get('HULK_GAME', 'StarBreak'), 'data', 'texture'))

ZONE_TILES = {
    'hulk': {'external': 'hulk.external', 'transition': 'hulk.wall',
             'internal': 'hulk.internal', 'ledge': 'hulk.ledge'},
    'hulk-s': {'external': 'hulk.external', 'transition': 'hulk.wall',
               'internal': 'hulk.internal', 'ledge': 'hulk.ledge'},
    'cave': {'external': 'cave.external', 'transition': 'cave.transition',
             'internal': 'cave.wall', 'ledge': 'cave.ledge'},
    'city': {'external': 'city.lava', 'transition': 'city.track',
             'internal': 'city.stilts', 'ledge': 'city.ledge'},
    'forest': {'external': 'forest.external', 'transition': 'forest.transition',
               'internal': 'forest.wall', 'ledge': 'forest.ledge'},
    'lab': {'external': 'lab.external', 'transition': 'lab.transition',
            'internal': 'lab.wall', 'ledge': 'lab.ledge'},
    'ice': {'external': 'ice.cnest', 'transition': 'ice.ledge',
            'internal': 'ice.wall', 'ledge': 'ice.ledge'},
    'sc': {'external': 'sc.external', 'transition': 'sc.tledge',
           'internal': 'sc.wall', 'ledge': 'sc.ledge'},
    'lobby': {'external': 'lobby.external', 'transition': 'lobby.transition',
              'internal': 'lobby.floor', 'ledge': 'lobby.ledge'},
}

DEFAULT_BACKGROUND = 0xff182022

_SEPARATOR = re.compile(r'[\s_\-]+$')

def _client_version(value):
    """Normalise a zone name to its texture pack prefix.

    ``hulkHard``, ``hulk_boss`` and ``hulk`` all use the ``hulk`` texture
    pack, while ``hulk-s`` is a pack of its own and has to survive unchanged.
    """
    text = str(value or '').strip().lower()
    while True:
        for suffix in ('hard', 'boss'):
            if text.endswith(suffix) and len(text) > len(suffix):
                text = _SEPARATOR.sub('', text[:-len(suffix)])
                break
        else:
            return text

def tiles_for(zone, data_dir=None):
    """Return the tile vid mapping for *zone*, derived from texture metadata."""
    key = _client_version(zone)
    mapping = ZONE_TILES.get(key)
    if mapping:
        return dict(mapping)

    return {
        'external': key + '.external',
        'transition': key + '.transition',
        'internal': key + '.internal',
        'ledge': key + '.ledge',
    }

def tile_set_vids(zone, data_dir=None):
    """Every vid the client can resolve to a tile set for *zone*."""
    mapping = tiles_for(zone, data_dir)
    vids = set(mapping.values())
    vids.update(read_tile_vids(_texture_path(zone, data_dir)))
    vids.discard('')
    return vids

def _texture_path(zone, data_dir=None):
    pack = _client_version(zone)
    if not pack:
        return None
    directory = data_dir or DEFAULT_DATA_DIR
    path = os.path.join(directory, pack + '.m.bpb')
    return path if os.path.exists(path) else None

def read_tile_vids(path):
    """Read ``tileviddesc_`` from one client ``.m.bpb`` metadata file."""
    if not path:
        return []
    try:
        decoded = pb_layout.default_layouts().decode_file(
            'ImageFileDescription', path)
    except Exception:
        return []
    return [entry.get('vid_') for entry in (decoded.get('tileviddesc_') or [])
            if entry.get('vid_')]

def build(zone, gdv=0, z=60, background=None, data_dir=None, tiles=None):
    'Build a stand-in ``DecorationDescription`` for *zone*.'
    mapping = tiles_for(zone, data_dir)
    if tiles:
        mapping = dict(mapping, **tiles)
    external = mapping.get('external') or ''
    if gdv:
        external = _hard_variant(external, data_dir)
    external_tile = {'vid_': external}
    transition = {'vid_': mapping.get('transition') or external}
    internal = {'vid_': mapping.get('internal') or external}
    return {
        'name_': 'local-' + str(zone),
        'backgroundcolor_': int(
            DEFAULT_BACKGROUND if background is None else background),
        'externaltiles_': [external_tile],
        'transitiontiles_': [transition],
        'internaltiles_': [internal],
    }

def _hard_variant(vid, data_dir=None):
    """``hulk.external`` -> ``hulk.external2`` when the pack ships it."""
    if not vid:
        return vid
    pack = vid.split('.', 1)[0]
    candidate = pack + '.external2'
    if candidate in read_tile_vids(
            os.path.join(data_dir or DEFAULT_DATA_DIR, pack + '.m.bpb')):
        return candidate
    return vid
