"""Gameplay interaction eligibility, shared by solo and cooperative maps."""

DW_BLOCKED_ANIMATIONS = frozenset((10, 11, 12, 13, 14, 19, 24, 29))
DW_BLOCKED_ACTIONS = frozenset(('FlailSpinAction', 'DashAction', 'DashDownAction'))

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
