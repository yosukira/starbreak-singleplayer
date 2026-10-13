"""Helpers for constructing the local test player.

The retail server creates the player with a ``Delta.createplayer`` message
after ``CreateWorld`` has been accepted.  Keeping the construction here (and
not in the generic protocol codec) makes it easy to replace the hard-coded
local test account with a real character/inventory provider later.
"""

try:
    from . import character_messages
except (ImportError, ValueError):
    import character_messages

CHARACTER_WILDFIRE = 1

def build_player(accountid='local-account-0001',
                 username='Localplayer',
                 playertype=CHARACTER_WILDFIRE,
                 description=None,
                 level=0,
                 hitpoints=None,
                 ammo=None,
                 position=None, loadout=None,
                 charid=None, charslotid=None, slottedcharprops=None):
    """Return a ``CreatePlayer`` protobuf value.

    ``description`` is a ``CharacterDescription`` built by
    :mod:`localserver.character_messages`; the character properties are
    derived from it so the client's live player matches the description it
    received (the retail client copies these numbers straight into the
    player object).

    ``position`` is an optional ``(xmp, ymp, wmp, hmp)`` tuple.  When it is
    omitted the client uses its normal ``spawnObj()`` ground search, which is
    what the retail server does for a fresh zone entry.
    """
    if description is None:
        description = {
            'type_': int(playertype),
            'maxhitpoints_': [100],
            'maxammo_': [6],
            'walkspeed_': [100],
            'jumpspeed_': [100],
            'mainslots_': [1],
            'secslots_': [1],
            'impslots_': [1],
            'invslots_': [8],
        }
    resolved_charid = int(charid if charid is not None else playertype)
    resolved_level = int(level)
    if loadout is not None and loadout.get('mk_') is not None:
        try:
            mk = int(loadout.get('mk_'))
        except (TypeError, ValueError):
            mk = None
        if mk is not None:
            resolved_level = max(0, min(5, mk - 1))
    charprops = character_messages.character_properties(
        description, level=resolved_level, hitpoints=hitpoints, ammo=ammo,
        charid=resolved_charid)
    charprops['charslotid_'] = int(
        charslotid if charslotid is not None else max(0, resolved_charid - 1))
    if loadout is not None:
        if int(loadout.get('type_', playertype)) != int(playertype):
            raise ValueError('practice loadout character type does not match player')
        import copy
        for key in ('hitpoints_', 'maxhitpoints_', 'ammo_', 'maxammo_',
                    'damagebonus_', 'armor_', 'walkspeed_', 'jumpspeed_',
                    'critchance_', 'critmult_', 'mainslots_', 'secslots_',
                    'impslots_', 'invslots_', 'items_'):
            if key in loadout:
                charprops[key] = copy.deepcopy(loadout[key])
    player = {
        'playertype_': int(playertype),
        'accountid_': accountid,
        'username_': username,
        'accountprops_': {
            'ec_': 0,
            'progress_': {},
        },
        'charprops_': charprops,
        'slottedcharprops_': list(slottedcharprops or []),
    }
    if position is not None:
        xmp, ymp, wmp, hmp = position
        player['pos_'] = {
            'xmp_': int(xmp),
            'ymp_': int(ymp),
            'wmp_': int(wmp),
            'hmp_': int(hmp),
        }
    return player

def build_player_delta(**kwargs):
    """Wrap :func:`build_player` in the gameplay ``Delta`` message."""
    return {'createplayer_': build_player(**kwargs)}
