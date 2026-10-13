'Turn exported room geometry into objects the retail client can render.'

try:
    from . import decorations
except (ImportError, ValueError):
    import decorations

TERRAIN_PLATFORM = 1
TERRAIN_LEDGE = 2

def normalize(objects, zone=None, tile_vids=None, fallback_wall=None,
              data_dir=None):
    """Return a copy of *objects* with the render flags the client needs.

    * ``draw_``       - set on every object that has a graphic to show.
    * ``isinternal_`` - set on terrain rectangles, which are rasterised into
      the tile map instead of being drawn as sprites.
    * ``vid_``        - terrain with an unknown vid (a boss wall such as
      ``hulk.bwall`` is solid but has no tile set of its own) is redirected to
      the zone wall so it does not silently disappear.
    """
    if tile_vids is None:
        tile_vids = decorations.tile_set_vids(zone, data_dir)
    if fallback_wall is None:
        fallback_wall = decorations.tiles_for(zone, data_dir).get('transition')

    result = []
    for item in objects:
        props = dict(item.get('props') or {})
        vid = props.get('vid_') or ''
        terrain = int(props.get('terraintype_') or 0)
        if terrain in (TERRAIN_PLATFORM, TERRAIN_LEDGE):
            if vid not in tile_vids:
                if fallback_wall:
                    props['vid_'] = fallback_wall
            props.setdefault('draw_', True)
            if terrain == TERRAIN_PLATFORM:
                props['isinternal_'] = True
        elif vid:
            props.setdefault('draw_', True)
        result.append({'objid': item.get('objid'), 'props': props})
    return result
