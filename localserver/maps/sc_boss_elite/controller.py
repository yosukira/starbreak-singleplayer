"""The same five SC gates, with 1.5x effective stage health."""
from ..sc_boss.controller import ScBossController
from .scene import load_rules

class EliteScBossController(ScBossController):
    name = 'sc_boss_elite'

    def __init__(self, rules=None):
        super().__init__(rules if rules is not None else load_rules())
