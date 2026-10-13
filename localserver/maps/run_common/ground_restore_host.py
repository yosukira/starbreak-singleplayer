"""Actual bridge/TCP/persistence coordinator for an opted-in target visit.

The default server feature remains off. This host requires a committed native
final-egress boundary and a real native endpoint; loopback/provider tests do
not establish the physical pause, materializer or feedback APPLIED boundary.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import uuid

from plugins._hardlab_delta import load_layouts
from plugins._full_run_ground_restore import (RestoreServer, RestoreRegistry, sha, scope,
                                              manifest, foreign_set)
from plugins._full_run_ground_restore_transport import ServerRestoreMailbox, ARRIVAL

def wire(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('ascii')

class RestoreProofStore:
    'Local battle simulator runtime.'
    def __init__(self, directory):
        self.directory = Path(directory)
        self.last_path = None

    def persist(self, document):
        raw = wire(document)
        if len(raw) > 1048576:
            raise ValueError('restore evidence exceeds1MiB SIM bound')
        identity = document['identity']
        name = sha(identity)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory/(name+'.json')
        if path.exists():
            if path.read_bytes() != raw:
                raise ValueError('different evidence for the same restore identity')
        else:
            temporary = self.directory/(name+'.'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(str(temporary), str(path))
            finally:
                if temporary.exists():temporary.unlink()
        saved = path.read_bytes()
        if saved != raw:
            raise ValueError('restore persistence readback differs')
        self.last_path = path
        return hashlib.sha256(saved).hexdigest()

def final_boundary(checkpoint, evidence):
    if not isinstance(evidence, dict) or evidence.get('kind') != 'native-final-egress-v1':
        raise ValueError('saved ground lacks committed native final-egress boundary')
    if evidence.get('checkpoint_sha256') != sha(checkpoint) or evidence.get('scope') != checkpoint['scope']:
        raise ValueError('saved checkpoint/boundary identity differs')
    digest = evidence.get('expected_cw_sha256')
    if (type(digest) is not str or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest) or
            evidence.get('actual_cw_sha256') != digest or
            type(evidence.get('native_thread')) is not int or evidence['native_thread'] <= 0 or
            type(evidence.get('createworld_thread')) is not int or
            evidence.get('createworld_thread') != evidence['native_thread']):
        raise ValueError('saved ground native boundary is incomplete')
    return copy.deepcopy(evidence)

class GroundRestoreHost:
    def __init__(self, server, channel, bridge, checkpoint, boundary, target_room,
                 target_world, proof_store, *, now=None, timeout=15.):
        if not isinstance(proof_store, RestoreProofStore):
            raise ValueError('actual RestoreProofStore required')
        if type(timeout) not in (int, float) or not .1 <= timeout <= 30:
            raise ValueError('restore visit timeout must be bounded')
        self.server, self.channel, self.bridge = server, channel, bridge
        self.checkpoint = copy.deepcopy(checkpoint)
        self.boundary = final_boundary(self.checkpoint, boundary)
        self.room = copy.deepcopy(target_room)
        if set(self.room) != {'map', 'room_token', 'room_seed'}:
            raise ValueError('exact no-player target room required')
        self.world = bytes(target_world)
        self.world_document = server.layouts.decode('CreateWorld',
            server._import_world_tool().unwrap_decompress(self.world))
        if self.world_document['worldprops_']['seed_'] != self.room['room_seed']:
            raise ValueError('target room/CW seed differs')
        for name in ('map', 'room_seed'):
            if self.room[name] != self.checkpoint['scope'][name]:
                raise ValueError('checkpoint belongs to a different room')
        if self.room['room_token'] <= self.checkpoint['scope']['room_token']:
            raise ValueError('restore needs a later visit')
        self.proof_store = proof_store
        self.table = load_layouts(str(Path(__file__).resolve().parents[3]/'plugins/runtime_pb_layouts.json'))

        self.preflight = manifest(self.checkpoint['objects'], scope(self.checkpoint['scope']),
            self.checkpoint['revision'], self.table, foreign_set(self._foreign_keys()))
        self.lock = threading.RLock()
        self.stop, self.prepared, self.ready = threading.Event(), threading.Event(), threading.Event()
        self.thread = None
        self.error = None
        self.state = 'staged'
        self.world_sent = self.source_committed = self.player_claimed = self.player_sent = False
        self.cp = self.player_blob = None
        self.cp_sent_at = None
        self.ledger = None
        self.wire_claim = None
        self.constructor_sends = 0
        self.persisted = None
        start = time.time() if now is None else now
        self.prepare = dict(schema=ARRIVAL, kind='prepare', restore_nonce=uuid.uuid4().hex,
            checkpoint_sha256=sha(self.checkpoint), cw_sha256=hashlib.sha256(self.world).hexdigest(),
            room=self.room, expires_at=start+timeout)
        self.mailbox = ServerRestoreMailbox(None, self._persist, time.monotonic,
            arrival_prepare=self.prepare, scope_provider=self.actual_scope)
        if not hasattr(server, '_ground_restore_registry'):
            server._ground_restore_registry = RestoreRegistry()

    def start(self):
        self.bridge.attach_ground_restore(self.mailbox)
        self.bridge.advance_ground_restore(self.mailbox, time.time())
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.wait(.01):
            try:self.poll(time.time())
            except Exception as error:self.fail(type(error).__name__+': '+str(error))

    def fail(self, reason):
        with self.lock:
            self.error = self.error or str(reason)[:512]
            self.state = 'recovery-required'
            self.mailbox.error = self.mailbox.error or self.error
            if self.ledger is not None:self.ledger.fail(self.error)

            self.stop.set()

    def target_sent(self):
        with self.lock:
            self.world_sent = True
            self.state = 'await-prepared'

    def source_preserved(self, reason):
        """Only after the egress owner proved no target World was created.

        The native owner adapter must cancel its matching pending pause before
        publishing that exact egress cancellation/untouched abort receipt.
        """
        with self.lock:
            if self.world_sent or self.source_committed or self.player_claimed or self.constructor_sends:
                raise ValueError('cannot abandon a possibly created restore target')
            self.stop.set()
            self.state = 'cancelled-source-preserved'
            self.error = str(reason)[:512]
            self.mailbox.error = self.error
            if self.bridge._restore.endpoint is self.mailbox:
                self.bridge.detach_ground_restore(self.mailbox)
            archive = getattr(self.server, '_ground_restore_archive', None)
            if archive is None:self.server._ground_restore_archive = archive = []
            if self not in archive:archive.append(self)
            if getattr(self.server, '_ground_restore_host', None) is self:
                self.server._ground_restore_host = None

    def source_commit(self, player_blob):
        with self.lock:
            if not self.world_sent:raise ValueError('source commit cannot precede target CW send')
            self.player_blob = bytes(player_blob)
            self.cp = self.server.layouts.decode('Delta', self.player_blob)['createplayer_']
            character = self.cp['charprops_']
            if (character['charid_'] != self.checkpoint['scope']['charid'] or
                    character['type_'] != self.checkpoint['scope']['player_type']):
                raise ValueError('different character/type target restore')
            self.source_committed = True

    def send_player(self):
        with self.lock:
            if not self.world_sent or not self.source_committed or not self.prepared.is_set() or self.error:
                raise ValueError('CP cannot precede real prepared pause and source commit')
            if self.player_claimed:raise ValueError('restore CP already claimed')
            self.player_claimed = True
            try:
                self.server._send_raw(self.channel, 12, self.player_blob, 'ground restore create player')
            except Exception as error:
                self.fail('uncertain CP send: '+str(error));raise
            self.player_sent = True
            self.cp_sent_at = time.time()
            self.state = 'await-scope'

    def actual_scope(self):
        if not self.player_sent or self.error:return None
        player, _ = self.bridge.snapshot()
        if (player is None or player.received_at < self.cp_sent_at or player.age() > .5 or
                not player.alive or player.worldid != self.world_document['worldid_']):return None

        character = self.cp['charprops_']
        return scope(dict(self.room, worldid=player.worldid, objid=player.objid,
            charid=character['charid_'], player_type=character['type_']))

    def _foreign_keys(self):
        state = self.world_document['worldstate_']
        worlds = [state['serverworld_']]+list(state.get('clientworld_', []))
        return {(w['ownerid_'], obj['objid_']) for w in worlds for obj in w.get('obj_', [])}

    def _persist(self, identity, receipts):
        if self.error or self.ledger is None or self.ledger.phase != 'ready':return None
        if identity != self.ledger.identity or receipts != self.ledger.receipts:
            raise ValueError('restore persistence identity/receipts differ')
        if self.persisted is None:
            document = dict(schema='full-run-ground-restore-evidence-v1', identity=identity,
                checkpoint=self.checkpoint, boundary=self.boundary, receipts=receipts,
                target_cw_sha256=self.prepare['cw_sha256'], constructor_sends=self.constructor_sends,
                feedback_applied_proven=False)
            digest = self.proof_store.persist(document)
            self.persisted = dict(identity, persisted=True, receipts_sha256=sha(receipts), evidence_sha256=digest)
        return copy.deepcopy(self.persisted)

    def allow_createloot(self, payload):
        with self.lock:
            claim = self.wire_claim
            if (self.error or claim is None or claim[0] != threading.get_ident() or
                    claim[1] != hashlib.sha256(payload).hexdigest()):
                self.fail('unexpected/late CreateLoot during exclusive target restore')
                raise ValueError(self.error)

    def poll(self, now):
        with self.lock:
            if self.error or self.ready.is_set():return
            self.bridge.advance_ground_restore(self.mailbox, now)
            status = self.mailbox.status()
            if status['discard_destination']:
                self.fail(status['error'] or 'restore ledger rejected target');return
            reply = self.mailbox.arrival_reply or {}
            if reply.get('kind') == 'prepared' and self.world_sent:
                self.prepared.set()
            if self.ledger is None and reply.get('kind') == 'scope_ready':
                actual = self.actual_scope()
                if actual is None:return
                if actual != reply['actual_scope']:raise ValueError('actual CP and arrival scope differ')
                left = self.prepare['expires_at']-now
                proposed = RestoreServer(self.checkpoint, actual, self.prepare['restore_nonce'], now,
                    self.table, self._foreign_keys(), timeout_s=left, source_sealed=True)
                self.ledger = self.server._ground_restore_registry.claim(proposed)
                self.mailbox.bind_server(self.ledger)
                self.state = 'restoring'
            if self.ledger is not None and self.ledger.phase == 'constructors':
                batch = self.ledger.claim_tcp_batch(now)
                for index, message in enumerate(batch):
                    payload = self.server.layouts.encode('Delta', message)
                    self.wire_claim = (threading.get_ident(), hashlib.sha256(payload).hexdigest())
                    try:
                        self.server._send_raw(self.channel, 12, payload,
                            'ground restore constructor '+str(index))
                        self.constructor_sends += 1
                    except Exception as error:
                        self.fail('uncertain restore constructor send: '+str(error));raise
                    finally:self.wire_claim = None
            if self.mailbox.status()['handoff_allowed']:
                archive = getattr(self.server, '_ground_restore_archive', None)
                if archive is None:self.server._ground_restore_archive = archive = []
                archive.append(self)
                self.bridge.detach_ground_restore(self.mailbox)
                self.state = 'ready'
                self.ready.set()
                self.stop.set()

    def status(self):
        with self.lock:
            return dict(state=self.state, error=self.error, target_room=self.room,
                prepared=self.prepared.is_set(), player_sent=self.player_sent,
                constructor_sends=self.constructor_sends, ready=self.ready.is_set(),
                evidence=str(self.proof_store.last_path) if self.proof_store.last_path else None,
                endpoint=self.mailbox.status(), production_default_enabled=False)
