"""Pure, bounded inventory refresh negotiation; no transport or run mutation."""
import copy
import math
from .player_inventory import merge_player_inventory

SCOPE_FIELDS=('map','room_token','room_seed','worldid','objid','charid','player_type')

def _number(value,label):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('invalid '+label)
    return float(value)

def _integer(value,label,minimum=1,maximum=9223372036854775807):
    if type(value) is not int or not minimum<=value<=maximum:raise ValueError('invalid '+label)
    return value

def _scope(scope):
    if not isinstance(scope,dict) or not isinstance(scope.get('map'),str) or not scope['map']:
        raise ValueError('invalid inventory room scope')
    value={k:copy.deepcopy(scope.get(k)) for k in SCOPE_FIELDS}
    _integer(value['room_token'],'room token');_integer(value['room_seed'],'native room seed',0,4294967295)
    for k in ('worldid','objid'):_integer(value[k],k,1,4294967295)
    _integer(value['charid'],'character ID',0,4294967295);_integer(value['player_type'],'inner character type',0,2147483647)
    return value

def _field(player,key,default=None):
    return player.get(key,default) if isinstance(player,dict) else getattr(player,key,default)

class InventoryHandoff:
    """One door intent. Repeated held input must reuse this same instance.

    All times are the local wall clock used by native sampled_at. Retry cadence,
    freshness and the25ms pre-request tolerance are local SIM protection rules.
    The owner commits ready synchronously with the same newest PlayerState;
    this object never moves a room, sends a packet or changes issued state.
    """
    def __init__(self,request_id,link,scope,now,timeout=1.5,max_attempts=4,retry_interval=.25):
        self.request_id=_integer(request_id,'inventory request ID');self.scope=_scope(scope)
        if not isinstance(link,dict):raise ValueError('invalid door link')
        self.link=copy.deepcopy(link);self.created_at=_number(now,'handoff start');self.last_now=self.created_at
        timeout=_number(timeout,'handoff timeout');self.retry_interval=_number(retry_interval,'retry interval')
        if timeout<=0 or self.retry_interval<=0:raise ValueError('handoff timing must be positive')
        self.max_attempts=_integer(max_attempts,'maximum attempts',1,64)
        self.expires_at=self.created_at+timeout;self.attempt=1;self.attempt_started=self.created_at
        self.next_attempt_at=self.created_at+self.retry_interval;self.state='pending';self.reason=None
        self.candidate=None;self.report=None;self.last_failure=None;self.sampled_at=None

    def control(self):
        if self.state!='pending':return None
        return dict(id=self.request_id,attempt=self.attempt,expires_at=self.expires_at,
            issued_at=self.attempt_started,scope=copy.deepcopy(self.scope))

    def _result(self):
        value=dict(status=self.state,id=self.request_id,attempt=self.attempt,expires_at=self.expires_at,
            reason=self.reason,last_failure=copy.deepcopy(self.last_failure))
        if self.state=='ready':value.update(link=copy.deepcopy(self.link),create_player=copy.deepcopy(self.candidate),report=copy.deepcopy(self.report))
        return value

    def cancel(self,reason='cancelled'):
        self.state='rejected';self.reason=str(reason);self.candidate=None;self.report=None
        return self._result()

    def _advance(self,now):
        if now>=self.next_attempt_at:
            # The final request remains valid until its deadline. A client
            # still applying the TCP boundary may answer after the last retry;
            # exhausting retries must not discard that outstanding response.
            if self.attempt>=self.max_attempts:return self._result()

            self.attempt+=1;self.attempt_started=now;self.next_attempt_at=now+self.retry_interval
        return self._result()

    def restart_after_effect(self,now,reason):
        """Invalidate an uncommitted sample after a new TCP loot/update effect.

        The owner republishes a new fixed native_t_min for the new attempt.
        Deadline and maximum attempts stay unchanged, including rapid effects.
        """
        try:now=_number(now,'inventory effect time')
        except ValueError as error:return self.cancel(str(error))
        if self.state=='rejected':return self._result()
        if now<self.last_now:return self.cancel('handoff clock moved backwards')
        self.last_now=now
        if now>=self.expires_at:return self.cancel('inventory refresh timed out')
        if self.attempt>=self.max_attempts:return self.cancel('inventory effects exhausted attempts')
        self.attempt+=1;self.attempt_started=now;self.next_attempt_at=now+self.retry_interval
        self.state='pending';self.reason=None;self.candidate=None;self.report=None;self.sampled_at=None
        self.last_failure=dict(errors=[str(reason)])
        return self._result()

    def poll(self,player,issued_create,now):
        try:now=_number(now,'handoff poll time')
        except ValueError as error:return self.cancel(str(error))
        if self.state=='rejected':return self._result()
        if now<self.last_now:return self.cancel('handoff clock moved backwards')
        self.last_now=now
        if now>=self.expires_at:return self.cancel('inventory refresh timed out')
        character=issued_create.get('charprops_') if isinstance(issued_create,dict) else None
        if not isinstance(character,dict) or type(character.get('charid_')) is not int or type(character.get('type_')) is not int or character.get('charid_')!=self.scope['charid'] or character.get('type_')!=self.scope['player_type']:
            return self.cancel('issued character identity changed')
        if player is not None:
            hp=_field(player,'hp');alive=_field(player,'alive')
            if alive is not True or type(hp) not in (int,float) or not math.isfinite(hp) or hp<=0:
                return self.cancel('player is dead or invalid')
            if any(type(_field(player,k)) is not int or _field(player,k)!=self.scope[k] for k in ('objid','worldid')):
                return self.cancel('player identity changed')
        if self.state=='ready':
            if player is None:return self.cancel('player missing before commit')
            if now-self.sampled_at>.75:return self.cancel('ready inventory became stale before commit')
            return self._result()
        raw=_field(player,'raw',{}) if player is not None else {}
        if isinstance(player,dict) and not raw:raw=player
        snapshot=raw.get('inventory') if isinstance(raw,dict) else None
        matching=isinstance(snapshot,dict) and type(snapshot.get('request_id')) is int and snapshot['request_id']==self.request_id and type(snapshot.get('request_attempt')) is int and snapshot['request_attempt']==self.attempt
        if matching:
            actual=snapshot.get('scope') or {}
            if isinstance(actual,dict) and any(actual.get(k) is not None and actual[k]!=v for k,v in self.scope.items()):
                return self.cancel('native inventory identity changed')
            sampled=snapshot.get('sampled_at')
            if type(sampled) in (int,float) and math.isfinite(sampled) and sampled>=self.attempt_started-.025:
                candidate,report=merge_player_inventory(issued_create,snapshot,now=now,expected_scope=self.scope,
                    expected_request_id=self.request_id)
                if report['ready_for_handoff']:
                    self.state='ready';self.candidate=candidate;self.report=report;self.sampled_at=sampled
                    return self._result()
                self.last_failure=copy.deepcopy(report)
            else:self.last_failure=dict(errors=['inventory sample predates current attempt or has invalid time'])
        return self._advance(now)
