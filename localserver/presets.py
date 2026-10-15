"""Shipped defaults, independent of the user's edited equipment."""
import json
from pathlib import Path
import shutil
import tempfile

from localserver import paths

FILES = ('class_loadout_overrides.json', 'practice_loadout.json',
         'game_settings.json', 'class_loadouts.json')


def defaults(name):
    if name not in FILES:
        raise ValueError('Unknown default configuration')
    return json.loads((paths.ROOT / 'data/defaults' / name).read_text(encoding='utf-8-sig'))


def restore(targets):
    """Back up all affected files before replacing them; write watched gear last."""
    documents = {name: defaults(name) for name in FILES}
    targets = {name: Path(targets[name]) for name in FILES}
    directory = targets['class_loadouts.json'].parent
    directory.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='before-restore-defaults-', dir=directory))
    for name, target in targets.items():
        if target.exists():
            shutil.copyfile(target, backup / name)
    for name in FILES:
        target = targets[name]
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf8', dir=target.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump(documents[name], output, ensure_ascii=False, indent=2)
            output.write('\n')
        try:
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return backup
