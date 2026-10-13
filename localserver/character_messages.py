'Character description helpers for the local test server.'

import json

SCALAR_FIELDS = (
    ('type', 'type_'),
    ('w', 'w_'),
    ('h', 'h_'),
    ('walkaccel', 'walkaccel_'),
    ('airaccel', 'airaccel_'),
    ('climbspeed', 'climbspeed_'),
    ('hasspecialslot', 'hasspecialslot_'),
    ('hasbodyslot', 'hasbodyslot_'),
    ('hasmobslot', 'hasmobslot_'),
    ('canhome', 'canhome_'),
    ('lockfacingincrouch', 'lockfacingincrouch_'),
    ('usecharstats', 'usecharstats_'),
)

REPEATED_FIELDS = (
    ('maxhitpoints', 'maxhitpoints_'),
    ('maxammo', 'maxammo_'),
    ('damagebonus', 'damagebonus_'),
    ('armor', 'armor_'),
    ('walkspeed', 'walkspeed_'),
    ('jumpspeed', 'jumpspeed_'),
    ('critchance', 'critchance_'),
    ('critmult', 'critmult_'),
    ('mainslots', 'mainslots_'),
    ('secslots', 'secslots_'),
    ('impslots', 'impslots_'),
    ('invslots', 'invslots_'),
)

def build_description(data):
    'Turn one runtime catalog entry into a ``CharacterDescription``.'
    message = {}
    for name, target in SCALAR_FIELDS:
        value = data.get(name)
        if value is not None:
            message[target] = value
    for name, target in REPEATED_FIELDS:
        value = data.get(name)
        if value is not None:
            message[target] = list(value)
    for name, target in (('vid', 'vid_'), ('auravid', 'auravid_'),
                         ('portraitvid', 'portraitvid_'),
                         ('jumpsound', 'jumpsound_'),
                         ('hitsound', 'hitsound_'),
                         ('deathsound', 'deathsound_')):
        value = data.get(name)
        if value:
            message[target] = value
    return message

def load_library(path):
    'Load the runtime catalog file -> ``{type: description}``.'
    with open(path, 'r', encoding='utf-8') as handle:
        catalog = json.load(handle)
    library = {}
    for key, entry in catalog.items():
        data = entry.get('data', entry) if isinstance(entry, dict) else entry
        char_type = int(data.get('type', key))
        library[char_type] = build_description(data)
    return library

def value_for_level(values, level=0):
    """Mirror the client's level-indexed lookup for a stat array."""
    if not values:
        return 0
    if not isinstance(values, (list, tuple)):
        return values
    index = min(max(int(level), 0), len(values) - 1)
    return values[index]

def character_properties(description, level=0, hitpoints=None, ammo=None,
                         charid=1):
    """Build ``CharacterProperties`` consistent with a description.

    The client copies these values into the live player, so keeping them in
    sync with the description avoids the "stats jump" the retail UI shows
    when the server sends mismatched numbers.
    """
    maxhitpoints = value_for_level(description.get('maxhitpoints_'), level)
    maxammo = value_for_level(description.get('maxammo_'), level)
    props = {
        'charid_': int(charid),
        'type_': int(description.get('type_', 0)),
        'charslotid_': 0,
        'hitpoints_': int(hitpoints if hitpoints is not None
                          else maxhitpoints),
        'maxhitpoints_': int(maxhitpoints),
        'ammo_': int(ammo if ammo is not None else maxammo),
        'maxammo_': int(maxammo),
        'damagebonus_': value_for_level(
            description.get('damagebonus_'), level),
        'armor_': value_for_level(description.get('armor_'), level),
        'walkspeed_': value_for_level(description.get('walkspeed_'), level),
        'jumpspeed_': value_for_level(description.get('jumpspeed_'), level),
        'critchance_': value_for_level(
            description.get('critchance_'), level),
        'critmult_': value_for_level(description.get('critmult_'), level),
        'mainslots_': value_for_level(
            description.get('mainslots_'), level),
        'secslots_': value_for_level(description.get('secslots_'), level),
        'impslots_': value_for_level(description.get('impslots_'), level),
        'invslots_': value_for_level(description.get('invslots_'), level),
    }
    return props
