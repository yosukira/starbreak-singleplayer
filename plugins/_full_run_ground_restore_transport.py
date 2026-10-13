"""Pure candidate restore mailbox/packets. No socket/native/runtime hooks.

Providers and monotonic clock are required; no synthetic-success defaults.
Ordinary telemetry is copied, never cleared to drive restore state.
"""
import copy
import json
import math
try:
    from ._full_run_ground_restore import ascii_bytes,sha,scope,SCOPE,GUARDS,MAX_MESSAGE
except (ImportError,ValueError,SystemError):
    from _full_run_ground_restore import ascii_bytes,sha,scope,SCOPE,GUARDS,MAX_MESSAGE

SCHEMA='full-run-ground-restore-transport-v1'
ARRIVAL='full-run-ground-restore-arrival-v1'
DURABLE='full-run-ground-restore-durable-v1'
ABORT='full-run-ground-restore-transport-abort-v1'
MAX_OUTER=60000
CHANNELS=('control','ack','arrival','arrival_ack','durable','durable_received','abort')
IDENTITY_FIELDS={'restore_nonce','checkpoint_sha256','manifest_sha256','destination_scope'}
DURABLE_FIELDS=IDENTITY_FIELDS|{'schema','kind','receipts_sha256','evidence_sha256'}
ABORT_FIELDS=IDENTITY_FIELDS|{'schema','kind','reason'}
ARRIVAL_COMMON={'schema','kind','restore_nonce','checkpoint_sha256','cw_sha256','room'}

def integer(value,low=0,high=4294967295):
    if type(value) is not int or not low<=value<=high:raise ValueError('invalid transport integer')
    return value

def hash_value(value):
    if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):raise ValueError('invalid SHA')
    return value

def finite(value):
    if type(value) not in (float,int) or not math.isfinite(value):raise ValueError('invalid finite time')
    return value

def nonce(value):
    if not isinstance(value,str) or not 16<=len(value)<=96 or any(not(ord(c)<128 and (c.isalnum() or c in '_-')) for c in value):raise ValueError('invalid arrival nonce')
    return value

def room(value):
    if not isinstance(value,dict) or set(value)!={'map','room_token','room_seed'}:raise ValueError('arrival needs exact room triple')
    if not isinstance(value['map'],str) or not value['map'] or len(value['map'])>64:raise ValueError('invalid map')
    integer(value['room_token'],1);integer(value['room_seed']);return copy.deepcopy(value)

def same_scope(a,b):
    try:return scope(a)==scope(b)
    except (ValueError,TypeError,KeyError):return False

def identity(message):
    return {k:copy.deepcopy(message[k]) for k in ('restore_nonce','checkpoint_sha256','manifest_sha256','destination_scope')}

def match_identity(message,expected):
    try:return identity(message)==expected and same_scope(message['destination_scope'],expected['destination_scope'])
    except (ValueError,TypeError,KeyError):return False

def visit_matches(observations,prepare):
    try:
        life=observations['lifecycle']
        return room(life['room'])==room(prepare['room']) and life['cw_sha256']==prepare['cw_sha256']
    except (ValueError,TypeError,KeyError):return False

def discard_target(failed,identity_value,prepare):
    """Name the OLD visit explicitly; a caller must never discard current blindly."""
    destination=copy.deepcopy((identity_value or {}).get('destination_scope'))
    visit={k:destination[k] for k in ('map','room_token','room_seed')} if destination else copy.deepcopy((prepare or {}).get('room'))
    return dict(discard_scope=destination if failed else None,discard_visit=visit if failed else None)

def strict_dict(pairs):
    out={}
    for k,v in pairs:
        if k in out:raise ValueError('duplicate JSON field')
        out[k]=v
    return out

def envelope(channel,packet_id,message):
    if channel not in CHANNELS:raise ValueError('unknown restore channel')
    integer(packet_id,1)
    if not isinstance(message,dict) or len(ascii_bytes(message))>MAX_MESSAGE:raise ValueError('restore message exceeds6144')
    return dict(schema=SCHEMA,channel=channel,packet_id=packet_id,message_sha256=sha(message),message=copy.deepcopy(message))

def build_packet(base,document):
    """Return actual compact ASCII wire bytes; never edits base player/keys."""
    if not isinstance(base,dict) or 'ground_restore' in base:raise ValueError('base already owns restore field')
    checked=envelope(document['channel'],document['packet_id'],document['message'])
    if checked!=document:raise ValueError('invalid envelope')
    outer=copy.deepcopy(base);outer['ground_restore']=checked;raw=ascii_bytes(outer)
    if len(raw)>MAX_OUTER:raise ValueError('outer compact ASCII exceeds60000')
    return raw

def decode_packet(raw):
    if not isinstance(raw,bytes) or not 0<len(raw)<=MAX_OUTER:raise ValueError('outer wire budget')
    outer=json.loads(raw.decode('ascii'),object_pairs_hook=strict_dict)
    if not isinstance(outer,dict) or len(ascii_bytes(outer))>MAX_OUTER:raise ValueError('outer decoded budget')
    doc=outer.get('ground_restore')
    if not isinstance(doc,dict) or set(doc)!={'schema','channel','packet_id','message_sha256','message'}:raise ValueError('restore envelope fields')
    if doc!=envelope(doc['channel'],doc['packet_id'],doc['message']):raise ValueError('restore envelope SHA/schema')
    return doc

class StableOutbox(object):
    """Keep message identity stable; each send uses latest supplied telemetry."""
    def __init__(self):self.document=None;self.counter=0
    def set(self,channel,message):
        if self.document is None or self.document['channel']!=channel or self.document['message']!=message:
            self.counter+=1;self.document=envelope(channel,self.counter,message)
        return copy.deepcopy(self.document)
    def packet(self,base):return build_packet(base,self.document) if self.document is not None else None

class BoundedMailbox(object):
    """SIM budgets, checked between callbacks; one callback is not preempted."""
    def __init__(self,clock,max_packets=4,max_bytes=60000,max_seconds=.004,max_queue=64,max_queue_bytes=256000,max_replays=2048):
        if not callable(clock):raise ValueError('monotonic clock provider required')
        self.clock=clock;self.max_packets=integer(max_packets,1,64);self.max_bytes=integer(max_bytes,1,MAX_OUTER)
        if type(max_seconds) not in (float,int) or not 0<max_seconds<=1:raise ValueError('frame time budget')
        self.max_seconds=max_seconds;self.max_queue=integer(max_queue,1,1024);self.max_queue_bytes=integer(max_queue_bytes,1,1048576)
        self.max_replays=integer(max_replays,1,4096);self.queue=[];self.queued_bytes=0;self.seen={};self.rejections=[]
    def reject(self,reason):
        self.rejections.append(str(reason)[:256]);self.rejections=self.rejections[-32:];return False
    def enqueue(self,raw):
        if not isinstance(raw,bytes) or not 0<len(raw)<=min(MAX_OUTER,self.max_bytes):return self.reject('packet exceeds wire/frame byte budget')
        if len(self.queue)>=self.max_queue or self.queued_bytes+len(raw)>self.max_queue_bytes:return self.reject('bounded mailbox full; evidence not evicted')
        self.queue.append(raw);self.queued_bytes+=len(raw);return True
    def drain(self,handler,started=None):
        start=self.clock() if started is None else started;count=0;used=0;duplicates=0;deferred=0;invalid=0
        available=len(self.queue)
        while self.queue and count<self.max_packets and count<available:
            now=self.clock()
            if not math.isfinite(now) or now<start:raise ValueError('invalid monotonic clock')
            if now-start>=self.max_seconds or used+len(self.queue[0])>self.max_bytes:break
            raw=self.queue.pop(0);self.queued_bytes-=len(raw);count+=1;used+=len(raw)
            try:
                doc=decode_packet(raw);m=doc['message']

                visit=m.get('destination_scope',m.get('room'))
                replay_key=(doc['channel'],m.get('restore_nonce'),sha(visit),doc['packet_id'])
                previous=self.seen.get(replay_key)
                if previous is not None:
                    if previous!=doc['message_sha256']:raise ValueError('conflicting replay')
                    duplicates+=1;continue
                if len(self.seen)>=self.max_replays:raise ValueError('replay journal full; no eviction')
                result=handler(doc)
                if result=='defer':
                    self.queue.append(raw);self.queued_bytes+=len(raw);deferred+=1
                elif result is True:self.seen[replay_key]=doc['message_sha256']
            except (ValueError,TypeError,KeyError,AttributeError,UnicodeError,RecursionError,OverflowError) as error:
                invalid+=1;self.reject(error)
        elapsed=self.clock()-start
        return dict(packets=count,bytes=used,duplicates=duplicates,deferred=deferred,invalid=invalid,elapsed_s=elapsed,
            time_budget_exceeded=elapsed>self.max_seconds,overrun_s=max(0.,elapsed-self.max_seconds),remaining=len(self.queue),
            budgets_are_SIM=True,single_handler_preempted=False)

class ArrivalLatch(object):
    """No-player room latch. Never invents a player/owner/character scope."""
    def __init__(self):self.prepare=None;self.bound_scope=None;self.error=None;self.reply=None;self.complete=False
    def fail(self,reason):self.error=self.error or str(reason)[:256]
    def accept(self,message,observations,now):
        try:
            finite(now)
            required={'schema','kind','restore_nonce','checkpoint_sha256','cw_sha256','room','expires_at'}
            if not isinstance(message,dict) or set(message)!=required or message['schema']!=ARRIVAL or message['kind']!='prepare':return False
            wanted=room(message['room']);life=observations.get('lifecycle')
            if not isinstance(life,dict) or room(life.get('room'))!=wanted:return False
            hash_value(message['checkpoint_sha256']);hash_value(message['cw_sha256'])
            nonce(message['restore_nonce'])
            if self.prepare is not None:
                if message!=self.prepare:return False
                self.poll(observations,now);return not self.error
            if not now<finite(message['expires_at'])<=now+30:raise ValueError('arrival deadline')
            self.prepare=copy.deepcopy(message)
            if life.get('cw_sha256')!=message['cw_sha256']:raise ValueError('actual CW SHA differs')
            for field in ('latch_installed','native_updates_paused','input_paused'):
                if life.get(field) is not True:raise ValueError('missing CP-before lifecycle/input/physics latch')
            if life.get('cp_seen') is not False or life.get('player_present') is not False or observations.get('actual_scope') is not None:
                raise ValueError('CP/player existed before arrival latch')
            self.reply=self.message('prepared');return True
        except (ValueError,TypeError,KeyError,AttributeError) as error:self.fail(error);return False
    def message(self,kind,**values):
        out={k:copy.deepcopy(self.prepare[k]) for k in ('restore_nonce','checkpoint_sha256','cw_sha256','room')}
        out.update(schema=ARRIVAL,kind=kind);out.update(values);return out
    def poll(self,observations,now):
        if self.prepare is None or self.error or self.complete:return
        finite(now)
        if now>=self.prepare['expires_at']:self.fail('arrival deadline; discard target visit');return
        life=observations.get('lifecycle') or {}
        if not visit_matches(observations,self.prepare):self.fail('arrival target changed');return
        if any(life.get(f) is not True for f in ('latch_installed','native_updates_paused','input_paused')):self.fail('arrival latch lost');return
        actual=observations.get('actual_scope')
        if actual is None:return
        try:
            actual=scope(actual)
            if any(actual[k]!=self.prepare['room'][k] for k in ('map','room_token','room_seed')):raise ValueError('CP actual scope room mismatch')
            if life.get('cp_seen') is not True or life.get('player_present') is not True or life.get('latch_preceded_cp') is not True:raise ValueError('CP-before latch order not proven')
            if self.bound_scope is not None and self.bound_scope!=actual:raise ValueError('CP identity changed in same visit')
            self.bound_scope=actual;self.reply=self.message('scope_ready',actual_scope=actual)
        except (ValueError,TypeError,KeyError) as error:self.fail(error)

class ClientRestoreTransport(object):
    def __init__(self,client,provider,clock,**budgets):
        if not callable(provider):raise ValueError('actual native observation provider required')
        self.client=client;self.provider=provider;self.clock=clock;self.mailbox=BoundedMailbox(clock,**budgets)
        self.arrival=ArrivalLatch();self.outbox=StableOutbox();self.durable=None;self.error=None;self.last_observation=None;self.frame_report=None
    def enqueue(self,raw):return self.mailbox.enqueue(raw)
    def observations(self):
        value=self.provider()
        if not isinstance(value,dict):raise ValueError('actual provider unavailable')
        return copy.deepcopy(value)
    def handle(self,doc,observations,now):
        if self.error or self.arrival.error or self.client.phase=='failed':return False
        channel,m=doc['channel'],doc['message']
        if channel=='arrival':
            if m.get('kind')=='failed' and set(m)==ARRIVAL_COMMON|{'expires_at','reason'} and m.get('schema')==ARRIVAL and self.arrival.prepare is not None and visit_matches(observations,self.arrival.prepare) and all(m.get(k)==self.arrival.prepare[k] for k in ('restore_nonce','checkpoint_sha256','cw_sha256','room','expires_at')):
                self.error=self.error or 'host arrival abort: '+str(m.get('reason'));return True
            if not isinstance(observations.get('lifecycle'),dict) or observations['lifecycle'].get('room') is None:return 'defer'
            return self.arrival.accept(m,observations,now)
        if channel=='control':
            if self.arrival.prepare is None:return False
            if observations.get('actual_scope') is None:return 'defer'
            if self.arrival.error or self.arrival.bound_scope is None:return False
            if m.get('restore_nonce')!=self.arrival.prepare['restore_nonce'] or m.get('checkpoint_sha256')!=self.arrival.prepare['checkpoint_sha256']:return False
            if not same_scope(m.get('destination_scope'),self.arrival.bound_scope):return False
            guard=observations.get('guards');empty=observations.get('empty_observation')
            return self.client.consume(m,observations['actual_scope'],empty,guard,now,observations.get('foreign_keys',()))
        if channel=='durable':
            if self.client.identity is None or self.client.phase!='ready' or not match_identity(m,self.client.identity):return False
            if set(m)!=DURABLE_FIELDS or m.get('schema')!=DURABLE or m.get('kind')!='persisted' or m.get('receipts_sha256')!=sha(self.client.receipts):return False
            hash_value(m.get('evidence_sha256'))
            if not same_scope(observations.get('actual_scope'),self.client.identity['destination_scope']):return False
            if self.durable is not None:return m==self.durable
            guard=observations.get('guards')
            if not isinstance(guard,dict) or any(guard.get(k) is not True for k in GUARDS):
                self.error='exclusive guard lost before durable receipt';return False
            self.durable=copy.deepcopy(m);self.arrival.complete=True;return True
        if channel=='abort':
            if self.client.identity is not None and match_identity(m,self.client.identity) and set(m)==ABORT_FIELDS and m.get('schema')==ABORT and m.get('kind')=='discard' and same_scope(observations.get('actual_scope'),self.client.identity['destination_scope']):
                self.error='host aborted transport: '+str(m.get('reason'));return True
        return False
    def frame(self,base,now):
        start=self.clock()
        try:
            finite(now)
            observations=self.observations();self.last_observation=observations;provider_elapsed=self.clock()-start
            self.arrival.poll(observations,now)
            if self.client.identity is not None and self.durable is None and not self.error:
                guard=observations.get('guards')
                if not isinstance(guard,dict) or any(guard.get(k) is not True for k in GUARDS):self.error='exclusive guard lost before durable receipt'
            self.frame_report=self.mailbox.drain(lambda d:self.handle(d,observations,now),started=start)
            if self.arrival.error:self.error=self.error or self.arrival.error
            if self.error:
                if self.client.identity is not None:self.outbox.set('abort',dict(self.client.identity,schema=ABORT,kind='discard',reason=self.error))
                elif self.arrival.prepare is not None:self.outbox.set('arrival_ack',self.arrival.message('failed',reason=self.error))
            elif self.durable is not None:
                reply=copy.deepcopy(self.durable);reply['kind']='received';self.outbox.set('durable_received',reply)
            elif self.client.identity is not None:
                message=self.client.control(now)
                if message is not None:self.outbox.set('ack',message)
            elif self.arrival.reply is not None:self.outbox.set('arrival_ack',self.arrival.reply)
            raw=self.outbox.packet(base);elapsed=self.clock()-start
            self.frame_report.update(whole_frame_elapsed_s=elapsed,provider_elapsed_s=provider_elapsed,
                time_budget_exceeded=elapsed>self.mailbox.max_seconds,overrun_s=max(0.,elapsed-self.mailbox.max_seconds))
            return raw
        except Exception as error:
            self.error=self.error or str(error);return None
    def status(self):
        host_ready=(self.last_observation or {}).get('host_room_ready') is True
        current=same_scope((self.last_observation or {}).get('actual_scope'),(self.client.identity or {}).get('destination_scope'))
        failed=bool(self.error) or self.client.phase=='failed'
        return dict(discard_target(failed,self.client.identity,self.arrival.prepare),error=self.error,discard_destination=failed,
            handoff_allowed=self.durable is not None and host_ready and current and not self.error and self.client.phase=='ready',
            unique_evidence_retained=True,ledger_cache_cleared=False,ordinary_keys_mutated=False,
            waiting_player=self.arrival.prepare is not None and self.arrival.bound_scope is None,
            frame=copy.deepcopy(self.frame_report),native_io_performed=False,gate_release_performed=False)

class ServerRestoreMailbox(object):
    'Local battle simulator runtime.'
    def __init__(self,server,persistence_provider,clock,arrival_prepare=None,scope_provider=None,**budgets):
        if not callable(persistence_provider):raise ValueError('persistence evidence provider required')
        if not callable(scope_provider):raise ValueError('actual server-side scope provider required')
        if server is not None:raise ValueError('start pre-player with server=None; bind_server only after actual scope_ready')
        if not isinstance(arrival_prepare,dict) or set(arrival_prepare)!=ARRIVAL_COMMON|{'expires_at'} or arrival_prepare.get('schema')!=ARRIVAL or arrival_prepare.get('kind')!='prepare':raise ValueError('pre-player arrival metadata required')
        room(arrival_prepare['room']);nonce(arrival_prepare['restore_nonce']);hash_value(arrival_prepare['checkpoint_sha256']);hash_value(arrival_prepare['cw_sha256']);finite(arrival_prepare['expires_at'])
        self.server=server;self.persistence_provider=persistence_provider;self.clock=clock;self.scope_provider=scope_provider
        self.arrival_prepare=copy.deepcopy(arrival_prepare);self.arrival_reply=None
        self.mailbox=BoundedMailbox(clock,**budgets);self.outbox=StableOutbox();self.durable=None;self.durable_received=False
        self.error=None;self.frame_report=None
        self.scope_error=None
    def actual_scope(self):
        try:
            value=self.scope_provider();value=scope(value) if value is not None else None;self.scope_error=None;return value
        except Exception as error:self.scope_error=str(error)[:256];return None
    def enqueue(self,raw):return self.mailbox.enqueue(raw)
    def bind_server(self,server):
        if self.server is not None:raise ValueError('server already bound; no ledger replacement')
        r=self.arrival_reply or {};p=self.arrival_prepare
        if r.get('kind')!='scope_ready' or not same_scope(r.get('actual_scope'),server.destination):raise ValueError('actual CP scope not received')
        if not same_scope(self.actual_scope(),server.destination):raise ValueError('current actual CP scope provider mismatch')
        if server.identity['restore_nonce']!=p['restore_nonce'] or server.identity['checkpoint_sha256']!=p['checkpoint_sha256']:raise ValueError('arrival/restore identity mismatch')
        if server.deadline>p['expires_at']+1e-9:raise ValueError('post-CP ledger must not extend arrival deadline')
        self.server=server
    def handle(self,doc,now):
        if self.error or (self.server is not None and self.server.phase=='failed'):return False
        channel,m=doc['channel'],doc['message']
        if channel=='arrival_ack' and self.arrival_prepare is not None:
            p=self.arrival_prepare
            if m.get('schema')!=ARRIVAL or room(m.get('room'))!=room(p['room']) or any(type(m.get(k)) is not type(p[k]) or m.get(k)!=p[k] for k in ('restore_nonce','checkpoint_sha256','cw_sha256')):return False
            extra={'reason'} if m.get('kind')=='failed' else {'actual_scope'} if m.get('kind')=='scope_ready' else set()
            if set(m)!=ARRIVAL_COMMON|extra:return False
            if m.get('kind')=='failed':self.error=self.error or 'arrival rejected: '+str(m.get('reason'));return True
            if m.get('kind') not in ('prepared','scope_ready'):return False
            if m['kind']=='scope_ready':
                actual=scope(m.get('actual_scope'))
                if any(actual[k]!=p['room'][k] for k in ('map','room_token','room_seed')):return False
            if self.arrival_reply and self.arrival_reply.get('kind')=='scope_ready' and m['kind']=='prepared':return True
            if self.arrival_reply and self.arrival_reply.get('kind')=='scope_ready' and not same_scope(m.get('actual_scope'),self.arrival_reply['actual_scope']):return False
            self.arrival_reply=copy.deepcopy(m);return True
        if self.server is None:return False
        actual=self.actual_scope()
        if actual is None:return 'defer'
        if not same_scope(actual,self.server.destination):return False
        if channel=='ack':return self.server.accept(m,now)
        if channel=='abort' and match_identity(m,self.server.identity) and set(m)==ABORT_FIELDS and m.get('schema')==ABORT and m.get('kind')=='discard':
            self.error=self.error or 'client aborted transport: '+str(m.get('reason'));return True
        if channel=='durable_received' and self.durable is not None:
            wanted=copy.deepcopy(self.durable);wanted['kind']='received'
            if m!=wanted:return False
            self.durable_received=True;return True
        return False
    def persistence(self):
        if self.server is None or self.server.phase!='ready' or self.durable is not None:return
        proof=self.persistence_provider(copy.deepcopy(self.server.identity),copy.deepcopy(self.server.receipts))
        if not isinstance(proof,dict) or proof.get('persisted') is not True:return
        if not match_identity(proof,self.server.identity) or proof.get('receipts_sha256')!=sha(self.server.receipts):return
        hash_value(proof.get('evidence_sha256'))
        self.durable=dict(self.server.identity,schema=DURABLE,kind='persisted',receipts_sha256=sha(self.server.receipts),evidence_sha256=proof['evidence_sha256'])
    def frame(self,base,now):
        start=self.clock()
        try:
            finite(now)
            deadline=self.server.deadline if self.server is not None else self.arrival_prepare['expires_at']
            if now>=deadline and not self.durable_received:self.error=self.error or 'transport deadline; discard target World, retain evidence'
            self.frame_report=self.mailbox.drain(lambda d:self.handle(d,now),started=start)
            if not self.error and self.clock()-start<self.mailbox.max_seconds:self.persistence()
            if self.error:
                if self.server is not None:self.outbox.set('abort',dict(self.server.identity,schema=ABORT,kind='discard',reason=self.error))
                elif self.arrival_prepare is not None:
                    failure=copy.deepcopy(self.arrival_prepare);failure.update(kind='failed',reason=self.error);self.outbox.set('arrival',failure)
            elif self.durable is not None:self.outbox.set('durable',self.durable)
            elif self.server is not None:
                message=self.server.control(now)
                if message is not None:self.outbox.set('control',message)
            elif self.arrival_prepare is not None:self.outbox.set('arrival',self.arrival_prepare)
            raw=self.outbox.packet(base);elapsed=self.clock()-start
            self.frame_report.update(whole_frame_elapsed_s=elapsed,time_budget_exceeded=elapsed>self.mailbox.max_seconds,
                overrun_s=max(0.,elapsed-self.mailbox.max_seconds))
            return raw
        except Exception as error:
            self.error=self.error or str(error);return None
    def status(self):
        actual=self.actual_scope()
        failed=bool(self.error) or (self.server is not None and self.server.phase=='failed')
        return dict(discard_target(failed,self.server.identity if self.server else None,self.arrival_prepare),error=self.error,discard_destination=failed,
            handoff_allowed=self.server is not None and same_scope(actual,self.server.destination) and self.server.phase=='ready' and self.durable_received and not self.error,
            unique_evidence_retained=True,actual_scope_error=self.scope_error,frame=copy.deepcopy(self.frame_report),socket_io_performed=False,gate_release_performed=False)
