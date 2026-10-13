from ..fireforest.session import ForestLiveSession
from ..fireforest.scene import roll_world

class ForestBossLiveSession(ForestLiveSession):
    KIND='boss'

def roll_create_world(blob, seed):
    return roll_world(blob,seed,'boss')
