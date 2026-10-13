"""Hub-specific target and unopened gate properties."""

DUMMY_VID = 'lobby.tdummy'
DUMMY_HP = 1

DUMMY_HIT_ANIM = 1
DUMMY_HIT_MS = 264

def closed_portal_vid(vid):
    """Use the retail unused/unopened (nu) gate, preserving its destination."""
    prefix, dot, name = str(vid).rpartition('.')
    if not name.endswith('portal') or name.startswith('nu'):
        return vid
    return (prefix + dot if dot else '') + 'nu' + name
