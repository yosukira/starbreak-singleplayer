"""Create local settings from anonymous presets on first launch."""
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
USERDATA = ROOT / 'userdata'

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
