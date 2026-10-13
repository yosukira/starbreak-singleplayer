"""Serialized bridge boundary for the explicit ground-restore endpoint.

The UDP thread only enqueues and sends a previously produced packet. The room
host alone advances the endpoint: native-scope/persistence providers must never
run under the UDP thread or the ordinary player-state lock.
"""
import copy
import json
import threading

class RestoreChannel:
    def __init__(self):
        self._lock = threading.RLock()
        self.endpoint = None
        self.cached_packet = None
        self.cached_status = None
        self.received = self.rejected = 0

    def attach(self, endpoint):
        from plugins._full_run_ground_restore_transport import ServerRestoreMailbox
        if not isinstance(endpoint, ServerRestoreMailbox):
            raise ValueError('explicit ServerRestoreMailbox required')
        with self._lock:
            if self.endpoint is not None and self.endpoint is not endpoint:
                raise ValueError('restore endpoint already owned; retain and detach it explicitly')
            self.endpoint = endpoint
        return endpoint

    def detach(self, endpoint):
        with self._lock:
            if self.endpoint is not endpoint:
                raise ValueError('restore endpoint ownership changed')
            previous = self.endpoint
            self.endpoint = self.cached_packet = self.cached_status = None
            return previous

    def absorb(self, document, raw=None):
        if 'ground_restore' not in document:
            return False

        with self._lock:
            self.received += 1
            if self.endpoint is None:
                self.rejected += 1
                return True
            try:
                if raw is None:
                    raw = json.dumps(document, ensure_ascii=True, separators=(',', ':'),
                                     allow_nan=False).encode('ascii')
                if not isinstance(raw, bytes) or not 0 < len(raw) <= 60000:
                    raise ValueError('restore whole-packet wire budget')
                if not self.endpoint.enqueue(raw):
                    self.rejected += 1
            except (ValueError, TypeError, UnicodeError):
                self.rejected += 1
            return True

    def advance(self, endpoint, now):
        """Host-thread only. This is the sole endpoint.frame call site."""
        with self._lock:
            if self.endpoint is not endpoint:
                raise ValueError('cannot advance a detached/replaced restore endpoint')
            raw = endpoint.frame({'t': round(now, 4)}, now)
            if raw is not None and (not isinstance(raw, bytes) or len(raw) > 60000):
                raise ValueError('restore endpoint returned invalid wire bytes')
            self.cached_packet = raw
            self.cached_status = copy.deepcopy(endpoint.status())
            return raw

    def outgoing(self):
        with self._lock:
            return self.cached_packet

    def status(self):
        with self._lock:
            return dict(attached=self.endpoint is not None, received=self.received,
                        rejected=self.rejected, endpoint=copy.deepcopy(self.cached_status))
