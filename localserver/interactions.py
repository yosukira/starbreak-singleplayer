"""Gameplay interaction eligibility, shared by solo and cooperative maps."""

DW_BLOCKED_ANIMATIONS = frozenset((10, 11, 12, 13, 14, 19, 24, 29))
DW_BLOCKED_ACTIONS = frozenset(('FlailSpinAction', 'DashAction', 'DashDownAction'))

def gen_press_document(value):
    """Bounded same-frame player/panel geometry; no native pointers."""
    def number(value):
        if type(value) is not int or not -2**31 <= value < 2**32:
            raise ValueError('Invalid Gen press coordinate')
        return value
    def rect(value):
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError('Invalid Gen press rectangle')
        result = [number(v) for v in value]
        if not 0 < result[2] <= 65536 or not 0 < result[3] <= 65536:
            raise ValueError('Invalid Gen press size')
        return result
    if not isinstance(value, dict) or not isinstance(value.get('panels'), list) or len(value['panels']) > 5:
        raise ValueError('Invalid Gen press sample')
    return dict(seed=number(value['seed']), objid=number(value['objid']),
                worldid=number(value['worldid']), rect=rect(value['rect']),
                panels=[dict(objid=number(row['objid']), rect=rect(row['rect'])) for row in value['panels']])

def can_interact(player):
    """Reject UI-consumed input and DW actions; never infer from velocity."""
    if player is None:
        return False
    raw = player if isinstance(player, dict) else getattr(player, 'raw', {})
    alive = raw.get('alive', True) if isinstance(player, dict) else getattr(player, 'alive', True)
    if not alive or raw.get('spectator') or raw.get('loaded', True) is False or raw.get('interaction_blocked'):
        return False
    if raw.get('vid') != 'assassin' and raw.get('player_type') != 2:
        return True

    if 'action_type' in raw:
        return raw['action_type'] not in DW_BLOCKED_ACTIONS
    animation = raw.get('animprops') or {}
    return animation.get('animid_', animation.get('animid')) not in DW_BLOCKED_ANIMATIONS
