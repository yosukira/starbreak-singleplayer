"""Pure pre-load/final-egress transaction. No socket, world move or hook.

The host must serialize intents, pause old AI before sending LoadPVid, retain
the old instance, and execute commit/abort actions in its own transport loop.
"""
import copy
import hashlib
import json
import math

from .ground_checkpoint import GroundCheckpointStore,integer,wire_size
from .inventory_handoff import SCOPE_FIELDS

def fingerprint(value):
    raw=json.dumps(value,sort_keys=True,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')
    return hashlib.sha256(raw).hexdigest()

def same_scope(a,b):
    return (isinstance(a,dict) and isinstance(b,dict) and set(a)==set(SCOPE_FIELDS) and
            set(b)==set(SCOPE_FIELDS) and all(type(a[k]) is type(b[k]) and a[k]==b[k] for k in SCOPE_FIELDS))

class GroundTransition:
    def __init__(self,request_id,attempt,scope,expected_pvid,cw_sha,issued,now,timeout=8.,arm_timeout=3.):
        integer(request_id,'request ID',1,9223372036854775807);integer(attempt,'attempt',1)
        if not same_scope(scope,scope):raise ValueError('incomplete source scope')
        for key in SCOPE_FIELDS:
            if key!='map':integer(scope[key],key,0 if key in ('room_seed','charid','player_type') else 1,4294967295)
        if not isinstance(scope['map'],str) or not scope['map']:raise ValueError('invalid source map')
        if not isinstance(expected_pvid,str) or not expected_pvid or len(expected_pvid.encode('utf8'))>128:raise ValueError('invalid PVID')
        if not isinstance(cw_sha,str) or len(cw_sha)!=64 or any(c not in '0123456789abcdef' for c in cw_sha):raise ValueError('invalid prepared world SHA')
        if type(now) not in (int,float) or not math.isfinite(now):raise ValueError('invalid transaction clock')
        if not 0<arm_timeout<=timeout<=10:raise ValueError('invalid bounded transaction deadlines')
        self.control=dict(request_id=request_id,request_attempt=attempt,scope=copy.deepcopy(scope),
            expected_pvid=expected_pvid,expected_cw_sha256=cw_sha,issued_at=now,expires_at=now+arm_timeout)
        self.deadline=now+timeout;self.issued=copy.deepcopy(issued)
        self.state='await-arm';self.load_sent=False;self.world_created=False
        self.checkpoint=None;self.vitals=None;self.ack=None;self.receipt_key=None
        self.final_joint=None
        self.native_boundary=None
        self.failure=None;self.committed=False

    def matches(self,value):
        return (isinstance(value,dict) and all(type(value.get(k)) is type(self.control[k]) and value.get(k)==self.control[k]
            for k in ('request_id','request_attempt','issued_at','expected_pvid','expected_cw_sha256')) and
            same_scope(value.get('scope'),self.control['scope']))

    def offer_arm(self,value,now):
        if self.state!='await-arm' or now>=self.control['expires_at']:return False
        if (not self.matches(value) or value.get('schema')!='full-run-egress-ready-v1' or
                value.get('status')!='armed' or value.get('ready') is not True):return False
        self.state='armed';return True

    def sent_load(self,now):
        if self.state!='armed' or now>=self.control['expires_at']:raise ValueError('load not armed or expired')
        self.load_sent=True;self.state='await-final'

    def offer(self,value,now):
        """A forged/mixed receipt never replaces the final native checkpoint."""
        if not self.matches(value) or value.get('schema')!='full-run-egress-v1' or value.get('kind')!='ground_egress':return False
        if not self.load_sent or now>=self.deadline:return False
        try:
            if wire_size(dict(seq=9223372036854775807,t=now,egress=value))>64000:raise ValueError('egress packet exceeds bounded wire size')
            integer(value.get('serial'),'egress serial',1,9223372036854775807)
            status=value.get('status')

            if self.state in ('committed','aborted','recovery-required'):
                if self.ack is None:return False
                incoming=self._ack(value)
                if self.state=='committed':
                    if status not in ('sealed','final'):return False
                    if status=='sealed' and incoming['actual_cw_sha256'] is None:
                        incoming['actual_cw_sha256']=self.ack['actual_cw_sha256']
                elif status not in ('aborted','interrupted','recovery-required'):return False
                return incoming==self.ack
            if status in ('aborted','interrupted','recovery-required'):
                report=value.get('abort')
                untouched=(status=='aborted' and value.get('checkpoint_kind')=='abort-report' and
                    isinstance(report,dict) and fingerprint(report)==value.get('checkpoint_sha256') and
                    report.get('source_untouched') is True and report.get('source_still_matches') is True and
                    report.get('original_load_allowed') is False and
                    same_scope(report.get('actual_scope'),self.control['scope']) and value.get('actual_cw_sha256') is None)
                self.state='abort-ready' if untouched else 'recovery-required'
                self.failure=copy.deepcopy(value);self.ack=self._ack(value) if untouched else None;return True
            if status not in ('sealed','final'):raise ValueError('unsupported egress status')
            if (value.get('checkpoint_kind')!='full-run-ground-joint-v1' or
                    value.get('original_load_allowed') is not True or value.get('source_untouched') is not False or
                    value.get('source_still_matches') is not True):
                raise ValueError('not a final pre-load capture')
            joint=value.get('joint')
            if not isinstance(joint,dict) or fingerprint(joint)!=value.get('checkpoint_sha256'):raise ValueError('checkpoint digest mismatch')
            store=GroundCheckpointStore()
            checkpoint,report=store.replace_joint(joint,self.issued,expected_scope=self.control['scope'],
                request_id=self.control['request_id'],attempt=self.control['request_attempt'],now=now,
                max_age_s=self.deadline-self.control['issued_at'])
            if not report['ready']:raise ValueError('final joint rejected: '+str(report['errors']))
            vitals=value.get('native_vitals')
            if not isinstance(vitals,dict):raise ValueError('missing final native vitals')
            integer(vitals.get('hp'),'final HP',1);integer(vitals.get('ammo'),'final ammo',0)
            for field in ('xmp','ymp','facing','pose'):integer(vitals.get(field),'final '+field)
            for field in ('wmp','hmp'):integer(vitals.get(field),'final '+field,1)
            integer(value.get('native_thread'),'source native thread',1,4294967295)
            identity=(value['serial'],value['checkpoint_sha256'])
            if self.receipt_key is not None and identity!=self.receipt_key:raise ValueError('second final checkpoint for one load')
            boundary=(value['native_thread'],fingerprint(vitals))
            if self.native_boundary is not None and boundary!=self.native_boundary:raise ValueError('final native boundary changed after sealing')
            if status=='final':
                if value.get('actual_cw_sha256')!=self.control['expected_cw_sha256']:raise ValueError('actual world SHA mismatch')
                integer(value.get('native_thread'),'source native thread',1,4294967295)
                if value.get('createworld_thread')!=value['native_thread']:raise ValueError('native lifecycle thread changed')
            elif value.get('actual_cw_sha256') is not None:raise ValueError('sealed receipt already has an unverified destination')
            if self.state=='final-ready' and status=='sealed':return True
            self.receipt_key=identity;self.checkpoint=checkpoint;self.vitals=copy.deepcopy(vitals)
            self.final_joint=copy.deepcopy(joint)
            self.native_boundary=boundary
            self.state='final-ready' if status=='final' else 'sealed'
            self.ack=self._ack(value) if status=='final' else None
            return True
        except (ValueError,TypeError,KeyError,AttributeError,UnicodeError):return False

    @staticmethod
    def _ack(value):
        return {k:copy.deepcopy(value.get(k)) for k in ('request_id','request_attempt','issued_at','scope',
            'serial','checkpoint_sha256','actual_cw_sha256')}

    def confirm_world_created(self,sha):
        if not self.load_sent or sha!=self.control['expected_cw_sha256']:return False
        self.world_created=True;return True

    def candidate(self):
        if self.state!='final-ready' or not self.world_created or self.committed:return None
        created=copy.deepcopy(self.checkpoint['create_player'])
        created['charprops_']['hitpoints_']=self.vitals['hp'];created['charprops_']['ammo_']=self.vitals['ammo']
        return dict(create_player=created,ground_checkpoint=copy.deepcopy(self.checkpoint),
            vitals=copy.deepcopy(self.vitals),ack=copy.deepcopy(self.ack))

    def mark_committed(self):
        if self.candidate() is None:raise ValueError('native final + WorldCreated required before commit')
        self.committed=True;self.state='committed'

    def expire(self,now):
        if now<self.deadline or self.state in ('committed','aborted'):return self.state

        self.state='recovery-required' if self.load_sent else 'abort-ready'
        self.failure=dict(reason='bounded ground transition deadline',source_untouched=not self.load_sent)
        return self.state

    def mark_aborted(self):
        if self.state!='abort-ready':raise ValueError('source preservation is unproven')
        self.state='aborted'
