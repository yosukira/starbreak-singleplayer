from ..fireforest.session import ForestLiveSession
from ..fireforest.scene import roll_world

class ForestMidLiveSession(ForestLiveSession):
    KIND='mid'

def roll_create_world(blob, seed):
    return roll_world(blob,seed,'mid')
