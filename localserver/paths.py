"""Create local settings from anonymous presets on first launch."""
import os
import json
from pathlib import Path
import shutil
import tempfile

ROOT = Path(__file__).resolve().parents[1]
USERDATA = ROOT / 'userdata'

def _legacy_loadout(char_type):
    """The exact placeholder preset shipped by the first public release."""
    return {
        'type_': char_type, 'mk_': 6, 'impslots_': 1,
        'items_': [dict(type_=item_type, slotpos_=256 * (index + 1), statboosts_=[])
                   for index, item_type in enumerate((31, 1, 32, 14, 15, 73))],
    }


def _replace_legacy_file(path, original, document):
    """Back up an unchanged old file before installing the repaired preset."""
    if path.read_bytes() != original:
        return
    backup = path.with_name(path.stem + '.before-preset-repair.json')
    try:
        with backup.open('xb') as output:
            output.write(original)
    except FileExistsError:
        pass
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(path.parent),
                                     prefix=path.name + '.', suffix='.tmp', delete=False) as output:
        temporary = Path(output.name)
        json.dump(document, output, indent=2, ensure_ascii=False)
        output.write('\n')
    try:
        if path.read_bytes() == original:
            os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def _repair_legacy_presets():
    """Repair untouched public placeholders while retaining edited classes."""
    defaults = ROOT / 'data' / 'defaults'
    try:
        path = USERDATA / 'class_loadouts.json'
        original = path.read_bytes()
        current = json.loads(original.decode('utf-8-sig'))
        restored = json.loads((defaults / path.name).read_text(encoding='utf-8-sig'))
        if not isinstance(current, dict) or not isinstance(restored, dict):
            return
        untouched = [key for key in ('1', '2', '3', '5')
                     if current.get(key) == _legacy_loadout(int(key))]
        changed = False
        for key, value in restored.items():
            if key not in current or key in untouched:
                current[key] = value
                changed = True
        if changed:
            _replace_legacy_file(path, original, current)
        path = USERDATA / 'practice_loadout.json'
        original = path.read_bytes()
        if json.loads(original.decode('utf-8-sig')) == _legacy_loadout(1):
            restored = json.loads((defaults / path.name).read_text(encoding='utf-8-sig'))
            _replace_legacy_file(path, original, restored)
        if len(untouched) == 4:
            path = USERDATA / 'class_loadout_overrides.json'
            original = path.read_bytes()
            if json.loads(original.decode('utf-8-sig')) == {}:
                restored = json.loads((defaults / path.name).read_text(encoding='utf-8-sig'))
                _replace_legacy_file(path, original, restored)
    except (OSError, ValueError, TypeError):
        # Invalid or actively edited settings remain available for manual repair.
        return


def ensure_user_data():
    USERDATA.mkdir(parents=True, exist_ok=True)
    (USERDATA / 'logs').mkdir(exist_ok=True)
    for source in (ROOT / 'data' / 'defaults').glob('*.json'):
        target = USERDATA / source.name
        if not target.exists():

            try:
                with target.open('xb') as output, source.open('rb') as input_file:
                    shutil.copyfileobj(input_file, output)
            except FileExistsError:
                pass
    _repair_legacy_presets()
