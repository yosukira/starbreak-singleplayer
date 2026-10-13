"""Bounded dedicated egress mailbox, separate from live player telemetry.

The owning bridge protects this object with its lock. It never commits room
state or acknowledges an uncertain receipt on behalf of the room host.
"""
import copy
import json

class EgressMailbox:
    def __init__(self):
        self.control=None;self.ack=None;self.cancel=None;self.finishing=None
        self.ready=None;self.receipts=[]

    def arm(self,control):
        if control is None and self.control is not None and self.ack is not None:
            if all(self.ack.get(k)==self.control.get(k) for k in ('request_id','request_attempt','issued_at','scope')):
                self.finishing=copy.deepcopy(self.control)
        if self.control!=control:
            self.ready=None;self.receipts=[]
        self.control=copy.deepcopy(control)
        self.cancel=None

    def acknowledge(self,ack):self.ack=copy.deepcopy(ack)

    def request_cancel(self,control):
        if control!=self.control:raise ValueError('cancel must name active egress control')
        self.cancel=copy.deepcopy(control)
        self.control=None

    def fields(self):
        result={}
        for name,value in (('egress_request',self.control),('egress_ack',self.ack),('egress_cancel',self.cancel)):
            if value is not None:result[name]=copy.deepcopy(value)
        return result

    def absorb(self,document):
        field='egress' if 'egress' in document else 'egress_ready' if 'egress_ready' in document else None
        if field is None:return False

        if set(document)!=set(('seq','t',field)):return True
        try:
            if len(json.dumps(document,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii'))>64000:return True
        except (TypeError,ValueError,UnicodeError):return True
        value=document[field];expected=self.control or self.cancel or self.finishing
        if not isinstance(value,dict) or expected is None:return True
        keys=('request_id','request_attempt','issued_at','scope','expected_pvid','expected_cw_sha256')
        if any(type(value.get(k)) is not type(expected.get(k)) or value.get(k)!=expected.get(k) for k in keys):return True
        scope=value['scope'];expected_scope=expected['scope']
        if not isinstance(scope,dict) or set(scope)!=set(expected_scope):return True
        if any(type(scope[k]) is not type(expected_scope[k]) for k in scope):return True
        if field=='egress_ready':self.ready=copy.deepcopy(value)
        else:

            identity=(value.get('serial'),value.get('checkpoint_sha256'))
            self.receipts=[r for r in self.receipts if (r.get('serial'),r.get('checkpoint_sha256'))!=identity]
            self.receipts.append(copy.deepcopy(value));self.receipts=self.receipts[-8:]
        return True

    def drain(self):
        result=copy.deepcopy(self.receipts);self.receipts=[]
        return copy.deepcopy(self.ready),result
