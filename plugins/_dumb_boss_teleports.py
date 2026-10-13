'runtime dumb_boss teleporter volumes (client side, SBPE plugins folder).'

TELEPORTS = (

    (40960, 204032, 24576, 58368, 62, 58, 49152, 106496, 24576),

    (319488, 204032, 24576, 58368, 65, 67, 303104, 106496, 24576),

    (49152, 48384, 24576, 58368, 59, 61, 40960, 262144, 24576),

    (303104, 48384, 24576, 58368, 68, 64, 319488, 262144, 24576),
)

def match(x, y, w, h):
    """Return the teleport row whose volume overlaps the given rect."""
    for row in TELEPORTS:
        vx, vy, vw, vh = row[:4]
        if x + w <= vx or x >= vx + vw or y + h <= vy or y >= vy + vh:
            continue
        return row
    return None

def landing(row, w, h):
    """Put the player's horizontal centre on the destination mechanism."""
    dx,dy,dw=row[6:9]
    return round(dx+(dw-w)/2),dy-h
