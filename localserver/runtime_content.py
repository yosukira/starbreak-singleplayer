"""Derived runtime tables needed in addition to map packs and handshake data."""
from pathlib import Path

RUNTIME_FILES=(
    'data/defaults/keybindings.json',
    'plugins/_practice_keybindings.py',
    'data/catalogue/class_players.json',
    'data/catalogue/character_descriptions.jsonl',
    'data/catalogue/characters.json',
    'data/catalogue/item_descriptions.jsonl',
    'data/defaults/class_loadouts.json',
    'data/defaults/practice_loadout.json',
    'data/defaults/class_loadout_overrides.json',
    'data/mechanics/item-affix-guide.json',
    'data/mechanics/enemy-cards.json',
    'data/mechanics/animation-catalogue.jsonl',
    'data/mechanics/enemy-triggers.json',
    'data/mechanics/attack-emit-vids.json',
    'data/mechanics/attack-emits.json',
    'data/mechanics/enemy-state-profiles.json',
    'data/mechanics/boss-entrance-timeline.json',
    'data/mechanics/eloader-flame-points.json',
    'data/mechanics/combat-cycles.json',
    'data/mechanics/death-presentation.json',
    'localserver/maps/efb_run/native_profiles.json',
)

def require_runtime_content(root):
    missing=[name for name in RUNTIME_FILES if not (Path(root)/name).is_file()]
    if missing:raise RuntimeError('Missing server runtime tables: '+', '.join(missing))
