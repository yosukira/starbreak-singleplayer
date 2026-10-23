"""Separate authored edge triggers from their wider visible door sprites."""

from ..efb_run.physics import surface, swept_shape


def bind_triggers(recipe):
    objects = recipe['objects']
    ids = recipe['metadata'].setdefault('door_trigger_ids', [])
    for port in recipe['metadata'].get('ports', []):
        if port['kind'] == 'core':
            continue
        door = objects[int(port['id'])]
        x, y, w, h = (door.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))
        matches = [oid for oid in ids if oid in objects
                   and objects[oid].get('xmp', 0) >= x
                   and objects[oid].get('xmp', 0)+objects[oid].get('wmp', 0) <= x+w
                   and objects[oid].get('ymp') == y and objects[oid].get('hmp') == h]
        if len(matches) > 1:
            raise ValueError('Ambiguous SC door trigger: '+str(port['id']))
        if matches:
            oid = matches[0]
        else:
            # Standalone encounter templates omitted the level trigger. Edge
            # doors use the outer half of the 160px sprite; the inner half is
            # where the original arrival marker stands. Core doors do not.
            oid = max(objects)+1
            objects[oid] = dict(xmp=x if port['side'] == 'left' else x+w//2,
                                ymp=y, wmp=w//2, hmp=h, draw=False)
            ids.append(oid)
        # No foreign teleport reaction is restored. The SC server owns the
        # destination; the native trigger flag preserves its visible overlay.
        objects[oid].update(trigger=255, draw=False)
        objects[oid].pop('treaction', None)
        port['trigger_id'] = oid


def overlaps(player_box, trigger):
    x, y, w, h = player_box
    tx, ty, tw, th = (trigger.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp'))
    return x < tx+tw and x+w > tx and y < ty+th and y+h > ty


def arrival(recipe, entry, wmp, hmp):
    """Keep the native feet anchor, adjusted for the actual shell footprint."""
    port = next((p for p in recipe['metadata'].get('ports', []) if p.get('entry') == entry), None)
    if not port or port['kind'] == 'core':
        return None
    x, y = recipe['entries'][entry]
    width, height = recipe['metadata'].get('spawn_footprint_mp', (7680, 17920))
    x, y = round(x+(width-wmp)/2), round(y+height-hmp)
    trigger = recipe['objects'][port['trigger_id']]
    if overlaps((x, y, wmp, hmp), trigger):
        # Wide shells still stay outside the purple area, without being
        # unnecessarily pushed beyond the entire decorative door frame.
        x = trigger['xmp']+trigger['wmp']+256 if port['side'] == 'left' else trigger['xmp']-wmp-256
    terrain = [(tuple(row.get(k, 0) for k in ('xmp', 'ymp', 'wmp', 'hmp')),
                row.get('shapetype', 0)) for row in recipe['objects'].values()
               if row.get('terraintype') in (1, 2) and row.get('wmp', 0)>0 and row.get('hmp', 0)>0]
    triggers = [recipe['objects'][p['trigger_id']]
                for p in recipe['metadata'].get('ports', [])
                if p['kind'] != 'core' and p.get('trigger_id') in recipe['objects']]

    def free(px, py):
        if any(overlaps((px, py, wmp, hmp), row) for row in triggers):
            return False
        return not any(swept_shape(bounds, shape, (px, py), (px, py),
                                  (wmp, hmp), penetrating=True, tolerance=1.) is not None
                       for bounds, shape in terrain)

    if free(x, y):
        return (x, y)
    # Native markers remain the first choice. Only a resized shell or changed
    # terrain needs a nearby landing on the room side of the purple trigger.
    # Use the true convex surface: an empty half of a slope is not a wall.
    inward = 1 if port['side'] == 'left' else -1
    choices = []
    for depth in (0, 8, 16, 32, 48, 64, 96, 128, 192, 256):
        px = x+inward*depth*256
        tops = [top for bounds, shape in terrain
                if (top := surface(bounds, shape, px, wmp)) is not None]
        ys = {y}
        ys.update(round(top-hmp) for top in tops if abs(top-y-hmp)<=192*256)
        for py in ys:
            if free(px, py):
                supported = any(abs(py+hmp-top)<=1 for top in tops)
                choices.append((not supported, (px-x)**2+(py-y)**2, abs(py-y), px, py))
    if not choices:
        raise ValueError('SC door has no clear arrival for shell: '+str(entry))
    return min(choices)[-2:]
