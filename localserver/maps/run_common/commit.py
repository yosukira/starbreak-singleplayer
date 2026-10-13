'Observe actual room sends and the entire wrapper tick.'
from functools import wraps

from ...live.wire import DeltaBatch
from ...supply import SupplyDrops

class RoomCommitState:
    def __init__(self):
        self.tick_serial = self.work_revision = self.snapshot_revision = 0
        self.completed_tick = 0
        self.active = False
        self.attempted = self.sent = self.suppressed = 0
        self.transport_uncertain = False
        self.error = None
        self._tick_sent = 0
        self.completed_tick_sent = 0

    def fail(self, reason, transport_uncertain=False):
        if self.error is None:
            self.error = str(reason)[:384]
        self.transport_uncertain |= bool(transport_uncertain)

    def begin(self):
        if self.active:
            self.fail('overlapping room wrapper ticks')
            raise RuntimeError(self.error)
        self.tick_serial += 1
        self.work_revision += 1
        self.active = True
        self._tick_sent = self.sent

    def finish(self, completed):
        if completed:
            self.completed_tick = self.tick_serial
            self.completed_tick_sent = self.sent - self._tick_sent
        self.active = False

    def status(self, wrapper):
        runtime = wrapper.current
        batch = getattr(runtime, 'batch', None)

        batch_empty = (isinstance(batch, DeltaBatch) and not batch.general and
                       not batch.objects and not batch.extra)

        batch_empty &= (not getattr(runtime, '_in_step', False) and
                        not getattr(runtime, '_pending_creates', {}))
        pending = getattr(runtime, 'pending_loot', [])
        pending_loot_empty = isinstance(pending, list) and not pending
        supplies = getattr(runtime, 'supplies', None)
        supplies_empty = (isinstance(supplies, SupplyDrops) and
                          isinstance(supplies.pending, list) and not supplies.pending)
        completed = (self.completed_tick > 0 and self.completed_tick == self.tick_serial and
                     not self.active and self.completed_tick_sent > 0)
        healthy = self.error is None and self.suppressed == 0
        flags = dict(
            snapshot_committed=bool(completed and healthy and batch_empty),
            transport_committed=bool(completed and healthy and not self.transport_uncertain),
            batch_empty=bool(batch_empty),
            pending_loot_empty=bool(pending_loot_empty),
            supplies_empty=bool(supplies_empty),
            wrapper_progress_committed=bool(completed and healthy and batch_empty and
                getattr(wrapper, '_ready', False) and not wrapper.suppress_send))
        return dict(schema='full-run-room-commit-v1',
            coverage='server-send-and-standard-room-outboxes-only',
            tick_serial=self.tick_serial, work_revision=self.work_revision,
            snapshot_revision=self.snapshot_revision, active=self.active,
            attempted=self.attempted, sent=self.sent, suppressed=self.suppressed,
            transport_uncertain=self.transport_uncertain, error=self.error,
            local_committed=all(flags.values()) and not self.transport_uncertain,
            **flags)

def tracked_tick(method):
    """Include post-child progress/door work, including early return paths."""
    @wraps(method)
    def run(wrapper, *args, **kwargs):
        state = wrapper.proxy.commit
        state.begin()
        completed = False
        try:
            result = method(wrapper, *args, **kwargs)
            completed = True
            return result
        except Exception as error:
            state.fail('wrapper tick failed: ' + type(error).__name__ + ': ' + str(error))
            raise
        finally:
            state.finish(completed)
    return run
