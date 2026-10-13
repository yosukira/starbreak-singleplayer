import json
from pathlib import Path
from ..dumb_wave.scene import DumbWavePack

class DumbMidPack(DumbWavePack):
    def __init__(self,root=None):
        super().__init__(root or Path(__file__).resolve().parents[3]/'maps/dumb_mid')
        self.betty=json.loads((self.root/'betty.json').read_text(encoding='utf8'))
