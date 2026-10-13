"""Elite SC uses the normal encounter flow and its own difficulty pack."""
import os

from ..sc_boss.session import ScBossLiveSession
from .scene import EliteScBossPack
from .controller import EliteScBossController

class EliteScBossLiveSession(ScBossLiveSession):
    MAP_ID = 'sc_boss_elite'
    CONTROLLER_CLASS = EliteScBossController

    def __init__(self, server, channel, options, room=None, log=None, bridge=None, pack=None):
        root = os.path.dirname(getattr(options, 'create_world_hex', '') or '')
        super().__init__(server, channel, options, room=room, log=log, bridge=bridge,
                         pack=pack or EliteScBossPack(root or None))
