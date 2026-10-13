"""Run progression and paused room instances; independent of transport/UI."""
from ..run_common.run import LevelRun, RoomInstance, room_seed

COLOURS = ('green', 'purple', 'yellow')

class EgyRun(LevelRun):
    """SIM: unoccupied rooms pause, including AI/RNG/cooldowns/lifetimes."""
    def __init__(self, pack, seed=None):
        super().__init__(pack, seed)
        self.activated = {colour: False for colour in COLOURS}
        self.mid_defeated = False

    @property
    def boss_open(self):
        return all(self.activated.values())

    def activate(self, colour):
        if colour not in self.activated:
            raise ValueError('Unknown Graveyard switch: ' + str(colour))
        if colour == 'yellow' and not self.mid_defeated:
            return False
        if self.activated[colour]:
            return False
        self.activated[colour] = True
        self.events.append(dict(kind='switch', colour=colour, bossOpen=self.boss_open))
        return True

    def allows(self, link):
        required = link.get('requires')
        if required in self.activated and not self.activated[required]:
            return False
        if required == 'boss' and not self.boss_open:
            return False
        if required == 'mid' and not self.mid_defeated:
            return False
        if link.get('destination') == 'boss' and not self.boss_open:
            return False
        return True

    def status(self):
        return dict(seed=self.seed, room=self.current, entry=self.entry,
                    switches=self.activated.copy(), bossGateOpen=self.boss_open,
                    midDefeated=self.mid_defeated, completed=self.completed,
                    transitions=self.transitions,
                    visited={role: room.visits for role, room in self.rooms.items()})
