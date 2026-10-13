from pathlib import Path
from ..dumb_wave.scene import DumbWavePack

class DumbNormalWavePack(DumbWavePack):
    def __init__(self,root=None):
        super().__init__(root or Path(__file__).resolve().parents[3]/'maps/dumb_normal_wave')
