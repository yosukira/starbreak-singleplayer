"""SC display timer, four unique Gen flags and automatic collapse."""
import math
import time
from ..run_common.run import LevelRun

class ScLevelRun(LevelRun):
    INITIAL_SECONDS=24*60
    GEN_BONUS_SECONDS=120
    # Oct23 second recording: Gen4 Offline + Firing Sequence Cancelled at
    # 595.640/595.643s; Boss CreateWorld at 619.573s (23.930s after notice).
    # Round to 24s as a local transition fit, including network/load latency.
    COLLAPSE_SECONDS=24.

    def __init__(self,pack,seed=None,clock=None):
        super().__init__(pack,seed)
        self.pack.configure(self.seed)
        # Prepare the entire chosen level before entry. Later door visits use
        # these instances and cached encounter data rather than re-reading files.
        self.pack.preload_encounters()
        for role in self.pack.room_names:
            self.room(role)
        self._clock=clock or time.monotonic
        self.started_at=self._clock()
        self.gens=set();self.chests_rewarded=set()
        self.collapse_started=None
        self._room_seen={}
        self._timer_notice_boundary=0

    @property
    def boss_entered(self):
        # The host rolls current back if preparing CreateWorld fails. Derive
        # entry from the committed destination, rather than a second flag
        # which could suppress all later attempts after such a rollback.
        return self.current=='boss' or self.completed

    def remaining(self,now=None):
        # Local singleplayer keeps the original clock display without a
        # deadline that closes rooms or forces the character out of the run.
        now=self._clock() if now is None else float(now)
        return max(0.,self.INITIAL_SECONDS+self.GEN_BONUS_SECONDS*len(self.gens)-(now-self.started_at))

    def timer_notice(self,now=None):
        """Consume the latest global minute boundary, including across rooms."""
        now=self._clock() if now is None else float(now)
        boundary=int(max(0.,now-self.started_at)//60)
        if boundary<=self._timer_notice_boundary:return None
        # A delayed room load/tick reports the current minute once. It must
        # not replay old notices, and a Gen bonus never resets this phase.
        self._timer_notice_boundary=boundary
        if self.collapse_started is not None or self.boss_entered:return None
        minutes=math.ceil(self.remaining(now)/60)
        # The recorded template covers positive remaining minutes. Reaching
        # zero is only a display state in local singleplayer, with no exit.
        return minutes if minutes>0 else None

    def mark_gen(self,index,now=None):
        index=int(index)
        if index not in range(4):raise ValueError('SC Gen index out of range')
        if index in self.gens:return False
        now=self._clock() if now is None else float(now)
        self.gens.add(index)
        self.events.append(dict(kind='gen_destroyed',index=index,bonus_seconds=self.GEN_BONUS_SECONDS))
        if len(self.gens)==4:self.collapse_started=now
        return True

    def observe(self,instance,now=None):
        now=self._clock() if now is None else float(now)
        if instance.flags.get('gen_destroyed'):
            self.mark_gen(instance.recipe['metadata']['gen_index'],now)
        self._room_seen[instance.role]=now

    def collapse_age(self,now=None):
        if self.collapse_started is None:return None
        return max(0.,(self._clock() if now is None else now)-self.collapse_started)

    def boss_due(self,now=None):
        age=self.collapse_age(now)
        return not self.boss_entered and age is not None and age>=self.COLLAPSE_SECONDS

    def allows(self,link,role=None):
        if self.boss_due() and link.get('destination') not in ('boss','home','lobby'):
            return False
        if link.get('destination')=='boss':return len(self.gens)==4
        if link.get('requires')=='mid_cleared':
            return bool(self.room(role).flags.get('mid_cleared'))
        return super().allows(link,role)

    def move(self,link,player=None):
        # A last-hit feedback may share the handoff tick with a door press.
        # Commit room-local Gen progress before changing the current room.
        self.observe(self.room())
        moved=super().move(link,player)
        return moved

    def status(self):
        result=super().status()
        result.update(generators=sorted(self.gens),remaining_seconds=self.remaining(),
                      collapse_age=self.collapse_age(),boss_entered=self.boss_entered)
        return result

