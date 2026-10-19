"""Owned native playback controls and verified, asynchronous server handoff."""
import base64
import copy
import json
from pathlib import Path
import threading
import uuid

from localserver.battle_records import RecordStore
from tools.native_replay import NativeJournals,adapter_digest
from tools.replay_host import WarmTakeovers


class NativePlayback:
    def __init__(self,directory,client):
        self.records=RecordStore(directory);self.clients=NativeJournals(directory);self.client=client
        self.lock=threading.RLock();self.warm=WarmTakeovers();self.serial=0
        self.command=None;self.source=None;self.host=None;self.branch=None
        self.status=dict(state='idle');self.packets=bytearray();self.controls=[]
        self.preparing=False;self.warming=False;self.closed=False;self.error=None;self.branches=set()

    def availability(self,identifiers):return self.clients.statuses(identifiers,adapter_digest(),self.client)

    def action(self,value):
        old_host=old_warm=None
        with self.lock:
            if self.closed:raise ValueError('Native game has closed')
            action=value.get('action')
            if action=='watch':
                record=self.records.get(str(uuid.UUID(value['record'])))
                if record.get('replay',{}).get('format',record['platform'])!='native':raise ValueError('This recording uses the browser replay engine')
                document=self.clients.read(record['replay']['id'])
                from tools.portable_replay import supported_runners
                supported_runners(document)
                if document['client']!=self.client or document['adapter']!=adapter_digest():
                    raise ValueError('Native replay requires the matching checked client and replay adapter')
                if not any(row[0]=='message' and row[1]==10 for row in document['ops']):
                    raise ValueError('This older recording has no native world bootstrap; record a new battle')
                old_host,old_warm=self.host,self.warm
                self.host=None;self.warm=WarmTakeovers();self.source=record;self.serial+=1;self.error=None
                self.command=dict(serial=self.serial,record=record['id'],document=document)
                self.branch=None;self.packets.clear();self.controls=[]
                self.preparing=False;self.warming=False
                self.status=dict(state='starting');result=dict(state='starting',serial=self.serial)
            else:
                if not self.source:raise ValueError('Select a native replay first')
                if self.branch:raise ValueError('The native battle is already under live control')
                if action not in ('pause','speed','seek','takeover','stop'):raise ValueError('Unknown native replay action')
                if len(self.controls)>=16:raise ValueError('Native controls are busy')
                self.controls.append(value);result=dict(accepted=True)
        if old_host:old_host.stop()
        if old_warm:old_warm.close()
        return result

    def poll(self,serial):
        with self.lock:
            value=dict(serial=self.serial,controls=self.controls,error=self.error)
            self.controls=[]
            if self.command and serial!=self.serial:value['play']=self.command
            if self.branch:value['branch']=self.branch
            if self.host and self.host.committed:
                value['ack']=self.host.bridge.acknowledgement(0)
                restore=self.host.bridge._restore.outgoing()
                if restore:value['documents']=[json.loads(restore)]
            if self.packets:
                value['packets']=base64.b64encode(bytes(self.packets[:512*1024])).decode()
                del self.packets[:512*1024]
            return value

    def send(self,data):
        if isinstance(data,str):return  # The bridge has an explicit handoff field.
        with self.lock:
            if len(self.packets)+len(data)>16*1024*1024:raise ValueError('Native replay output queue is full')
            self.packets.extend(data)

    def feedback(self,value):
        with self.lock:
            if value.get('serial')!=self.serial or not self.source:return
            status=value.get('status') or {}
            if not isinstance(status,dict):raise ValueError('Invalid native player status')
            self.status={key:status.get(key) for key in ('state','seconds','duration','paused','speed','takeover','target','error','message','assets_loaded','assets_total')}
            host=self.host
            if host and value.get('committed')==self.branch['id'] and not host.committed:host.commit()
            if host and host.committed:
                documents=value.get('documents',[])
                if not isinstance(documents,list) or len(documents)>64 or len(json.dumps(documents,separators=(',',':')).encode('ascii'))>256*1024:
                    raise ValueError('Invalid native room feedback budget')
                for document in documents:
                    if not isinstance(document,dict) or sum(field in document for field in ('egress','egress_ready','ground_restore'))!=1:
                        raise ValueError('Invalid native room feedback document')
                    host.bridge._absorb(document)
                telemetry=value.get('telemetry')
                if telemetry:
                    host.telemetry(telemetry)
                from tools.replay_stream import framed
                for kind in value.get('acks',[]):
                    if kind not in (11,22):raise ValueError('Invalid native room acknowledgement')
                    host.binary(framed(kind,b''))
                return
            request=value.get('checkpoint')
            if not request or status.get('state')not in ('playing','paused','taking-over'):return
            request=dict(request,record=self.source['id'])
            serial=self.serial
            warm_host=self.warm
            if value.get('takeover'):
                if self.preparing:return
                self.preparing=True
                def prepare():
                    created=None
                    try:
                        created=warm_host.take(self.records,self.clients,request)
                        owner=self
                        class Transport:
                            def send(_self,data):
                                with owner.lock:
                                    if serial==owner.serial and not owner.closed:owner.send(data)
                        created.bind(Transport())
                        with self.lock:
                            stale=self.closed or serial!=self.serial
                            if not stale:
                                self.host=created;self.branch=created.description();self.branches.add(self.branch['id'])
                        if stale:created.stop()
                    except Exception as error:
                        if created:created.stop()
                        with self.lock:
                            if serial==self.serial:self.error=str(error)[:200]
                    finally:
                        with self.lock:
                            if serial==self.serial:self.preparing=False
                threading.Thread(target=prepare,daemon=True).start()
            elif not self.warming and not self.preparing:
                self.warming=True
                def warm():
                    try:warm_host.warm(self.records,self.clients,request)
                    except (OSError,ValueError):pass
                    finally:
                        with self.lock:
                            if serial==self.serial:self.warming=False
                threading.Thread(target=warm,daemon=True).start()

    def close(self):
        with self.lock:self.closed=True;host=self.host
        if host:host.stop()
        self.warm.close()


class NativeControlClient:
    def __init__(self,path=None):
        from urllib.parse import urlsplit
        path=Path(path) if path else Path(__file__).resolve().parents[1]/'userdata/native-control.json'
        value=json.loads(path.read_text())
        url=urlsplit(value['url']);token=value['token']
        if url.scheme!='http' or url.hostname!='127.0.0.1' or url.path!='/chunks' or url.username or url.password or url.query or url.fragment:
            raise ValueError('Invalid owned native endpoint')
        if len(token)!=64 or any(c not in '0123456789abcdef' for c in token):raise ValueError('Invalid native token')
        self.url=value['url'][:-len('/chunks')];self.token=token

    def call(self,path,value=None):
        from urllib.request import Request,urlopen
        request=Request(self.url+path,data=None if value is None else json.dumps(value).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with urlopen(request,timeout=10) as response:raw=response.read(128*1024+1)
        if len(raw)>128*1024:raise ValueError('Native control response is too large')
        return json.loads(raw)
