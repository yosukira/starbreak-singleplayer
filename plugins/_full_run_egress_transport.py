"""Dedicated final-egress UDP documents; no native hooks until explicitly armed.

Called on the native update thread. Cached receipts are independent of the
current World/player and never pass through ordinary player telemetry filters.
The caller sends the returned documents using compact ASCII JSON.
"""
import copy
import json
import os
import time

RUN_MAPS=frozenset(('egy_run','eub_run','etl_run','est_run','efb_run','eff_run'))

def encoded(value):
    return json.dumps(value,ensure_ascii=True,separators=(',',':'),allow_nan=False).encode('ascii')

def generation(value):
    if not isinstance(value,dict):return None
    keys=('request_id','request_attempt','issued_at','scope')
    if not all(key in value for key in keys):return None
    return encoded({key:value[key] for key in keys})

class EgressTransport(object):
    def __init__(self,bridge,factory=None):
        self.bridge=bridge
        self.factory=factory
        self.coordinator=None
        self.completed=[]
        self.error=None

    def _local(self):
        return (str(os.environ.get('HARDLAB_LIVE_SESSION','')).strip().lower() in ('1','yes','true','on')
            and getattr(self.bridge,'enabled',False) is True
            and getattr(self.bridge,'host',None) in ('127.0.0.1','localhost','::1'))

    def _create(self):
        if self.factory:return self.factory()
        try:from ._full_run_egress import EgressCoordinator
        except (ImportError,ValueError,SystemError):from _full_run_egress import EgressCoordinator
        return EgressCoordinator(self.bridge.ffi,self.bridge.lib,self.bridge.util,self.bridge)

    def absorb_ack(self,document):
        """ACK first; a stale combined ACK/request cannot re-arm its old nonce."""
        if not isinstance(document,dict):return
        try:
            acknowledgement=document.get('egress_ack')
            if self.coordinator is not None and acknowledgement is not None:
                if self.coordinator.ack(acknowledgement):
                    key=generation(acknowledgement)
                    if key is not None and key not in self.completed:
                        self.completed.append(key);self.completed=self.completed[-32:]
            control=document.get('egress_request')
            cancel=document.get('egress_cancel')

            if control is not None:
                if not self._local() or not isinstance(control,dict):return
                if (control.get('scope') or {}).get('map') not in RUN_MAPS:return
                key=generation(control)
                if key is not None and key in self.completed:return
                if self.coordinator is None:self.coordinator=self._create()
                self.coordinator.arm(copy.deepcopy(control))
            elif cancel is not None and self.coordinator is not None:
                active=getattr(self.coordinator,'control',None) or getattr(self.coordinator,'cancel_identity',None)

                if isinstance(cancel,dict) and active is not None and encoded(cancel)==encoded(active):
                    self.coordinator.arm(None)
                    ready=self.coordinator.ready_document()

                    fields=('request_id','request_attempt','issued_at','expires_at','scope','expected_pvid','expected_cw_sha256')
                    complete=(isinstance(ready,dict) and ready.get('status')=='cancelled'
                        and all(type(ready.get(k)) is type(cancel.get(k)) and ready.get(k)==cancel.get(k) for k in fields)
                        and self.coordinator.outgoing_document() is None)
                    if complete:
                        key=generation(cancel)
                        if key is not None and key not in self.completed:
                            self.completed.append(key);self.completed=self.completed[-32:]
        except Exception as error:
            self.error=type(error).__name__+': '+str(error)

    def packets(self,now=None):
        if self.coordinator is None:return []
        result=[];now=time.time() if now is None else now

        for field,value in (('egress_ready',self.coordinator.ready_document()),
                            ('egress',self.coordinator.outgoing_document())):
            if value is None:continue
            document=dict(seq=self.bridge.seq+1,t=now)
            document[field]=value
            try:
                raw=encoded(document)
                limit=4096 if field=='egress_ready' else 64000
                if len(raw)>limit:raise ValueError('dedicated '+field+' exceeds wire budget')
            except (ValueError,TypeError,UnicodeError) as error:
                self.error=type(error).__name__+': '+str(error)
                continue
            self.bridge.seq=document['seq'];result.append(document)
        return result

    def close(self):
        if self.coordinator is not None:
            self.coordinator.close()
        self.coordinator=None
