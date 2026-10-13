"""The normal encounter retains the complete Hulk activation/gen/ship flow."""
from ...live.session import LiveSession
from .rules import HulkNormalRules

class HulkNormalLiveSession(LiveSession):
    MAP_ID = 'hulk_normal'
    RULES = HulkNormalRules()
