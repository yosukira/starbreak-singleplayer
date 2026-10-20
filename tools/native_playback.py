"""Owned native playback controls and verified, asynchronous server handoff."""
import base64
import copy
import json
from pathlib import Path
import threading
import time
import uuid

from localserver.battle_records import RecordStore
from tools.native_replay import NativeJournals,adapter_digest,compatible_adapter
from tools.replay_host import WarmTakeovers


def native_battle_view(operations,world_sequence):
    """Keep the verified prefix, but present only the selected practice world."""
    sequence=0;boundary=0;elapsed=0.;start=None;offset=0.;end=len(operations);duration=0.;transition=None
    for index,row in enumerate(operations):
        if row[0]=='message' and row[1]==21 and sequence and transition is None:transition=(boundary,elapsed)
        if row[0]=='message' and row[1]==10:
            sequence+=1
            if sequence==world_sequence:start=boundary;offset=elapsed
            elif sequence==world_sequence+1:
                end,end_time=transition if transition is not None else (boundary,elapsed)
                duration=end_time-offset;break
            transition=None
        if row[0]=='step':elapsed+=row[1]/1000.
        if row==['loop',1]:boundary=index+1
    else:duration=elapsed-offset
    if start is None or end<=start:raise ValueError('The selected battle is missing from this native recording')
    return dict(start=start,end=end,offset=offset,duration=duration)


class NativePlayback:
    def __init__(self,directory,client):
        self.records=RecordStore(directory);self.clients=NativeJournals(directory);self.client=client
        self.lock=threading.RLock();self.warm=WarmTakeovers();self.serial=0
        self.command=None;self.source=None;self.host=None;self.branch=None
        self.status=dict(state='idle');self.packets=bytearray();self.controls=[]
        self.preparing=False;self.warming=False;self.closed=False;self.error=None;self.branches=set()
        self.practice_pending=False
        self.load_thread=None

    def availability(self,identifiers):return self.clients.statuses(identifiers,adapter_digest(),self.client)

    def reset(self):
        with self.lock:
            host,warm=self.host,self.warm
            self.serial+=1;self.host=None;self.branch=None;self.source=None;self.command=None
            self.warm=WarmTakeovers();self.controls=[];self.packets.clear();self.error=None
            self.preparing=self.warming=False;self.warm_cursor=None;self.status=dict(state='idle')
            self.practice_pending=False
        if host:host.stop()
        warm.close()

    def action(self,value):
        old_host=old_warm=None
        with self.lock:
            if self.closed:raise ValueError('Native game has closed')
            action=value.get('action')
            if action=='watch':
                record=self.records.get(str(uuid.UUID(value['record'])))
                if record.get('replay',{}).get('format',record['platform'])!='native':raise ValueError('This recording uses the browser replay engine')
                old_host,old_warm=self.host,self.warm
                self.host=None;self.warm=WarmTakeovers();self.source=record;self.serial+=1;self.error=None
                serial=self.serial;self.command=None;warm_host=self.warm
                self.branch=None;self.packets.clear();self.controls=[]
                self.preparing=False;self.warming=False;self.warm_cursor=None
                self.practice_pending=False
                self.status=dict(state='starting',stage='reading',progress=0)
                result=dict(state='starting',serial=serial)
                def load():
                    try:
                        document=self.clients.read(record['replay']['id'])
                        from tools.portable_replay import supported_runners
                        supported_runners(document)
                        if document['client']!=self.client or not compatible_adapter(document['adapter']):
                            raise ValueError('Native replay requires the matching checked client and replay adapter')
                        if not any(row[0]=='message' and row[1]==10 for row in document['ops']):
                            raise ValueError('This older recording has no native world bootstrap; record a new battle')
                        from localserver.replay import read_journal,SUPPORTED_MAPS
                        journal_path=self.records.directory/(record['id']+'.server.jsonl.gz')
                        journal=read_journal(journal_path) if record['map'] in SUPPORTED_MAPS or journal_path.is_file() else None
                        if record['map'] in SUPPORTED_MAPS:
                            document=dict(document,view=native_battle_view(document['ops'],journal.get('world_sequence',1)))
                        with self.lock:
                            if self.closed or self.serial!=serial:return
                            self.command=dict(serial=serial,record=record['id'],document=document)
                            self.status=dict(state='starting',stage='download',progress=0,loaded=0,total=len(document['ops']))
                        # Reuse the verified document while the native client
                        # is downloading/reconstructing it, off its input edge.
                        if journal is not None:
                            try:warm_host.prime(self.records,self.clients,dict(id=record['replay']['id'],record=record['id']),document,journal)
                            except (OSError,ValueError):pass  # Takeover reports its own preparation error.
                    except Exception as error:
                        with self.lock:
                            if self.serial==serial:
                                self.error=str(error)[:400]
                                self.status=dict(state='failed',stage='reading',error=type(error).__name__,message=self.error)
                self.load_thread=threading.Thread(target=load,daemon=True);self.load_thread.start()
            else:
                if not self.source:raise ValueError('Select a native replay first')
                if self.branch:raise ValueError('The native battle is already under live control')
                if action not in ('pause','speed','seek','takeover','stop'):raise ValueError('Unknown native replay action')
                if action=='stop':
                    self.serial+=1;self.command=None;self.source=None;self.error=None;self.controls=[value];self.status=dict(state='stopped')
                    return dict(accepted=True)
                if action=='seek':self.controls=[row for row in self.controls if row.get('action')!='seek']
                if len(self.controls)>=16:raise ValueError('Native controls are busy')
                self.controls.append(value);result=dict(accepted=True)
        if old_host:old_host.stop()
        if old_warm:old_warm.close()
        return result

    def poll(self,serial):
        with self.lock:
            value=dict(serial=self.serial,controls=[],error=self.error)
            if self.status.get('state')=='stopped':
                value['controls']=self.controls;self.controls=[]
            if self.command:
                value['controls']=self.controls;self.controls=[]
                if serial!=self.serial:
                    document=self.command['document']
                    value['play']=dict(self.command,document={k:v for k,v in document.items() if k!='ops'},
                                       operations=len(document['ops']),transport='pages-v1')
            if self.branch:value['branch']=self.branch
            if self.host and self.host.committed:
                value['ack']=self.host.bridge.acknowledgement(0)
                restore=self.host.bridge._restore.outgoing()
                if restore:value['documents']=[json.loads(restore)]
            if self.packets:
                value['packets']=base64.b64encode(bytes(self.packets[:512*1024])).decode()
                del self.packets[:512*1024]
            return value

    def page(self,serial,offset):
        with self.lock:
            if serial!=self.serial or not self.command:raise ValueError('Native replay selection changed')
            rows=self.command['document']['ops']
            if not 0<=offset<len(rows):raise ValueError('Invalid native replay page offset')
            # Bound each JSON parse/allocation on the embedded 32-bit client.
            end=offset;size=0
            from plugins._native_replay import wire_size
            while end<len(rows) and end-offset<1024:
                addition=wire_size(rows[end])+1
                if end>offset and size+addition>384*1024:break
                size+=addition;end+=1
            return dict(serial=serial,offset=offset,total=len(rows),ops=rows[offset:end])

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
            self.status={key:status.get(key) for key in ('state','seconds','duration','paused','speed','takeover','target','error','message','assets_loaded','assets_total','stage','progress','loaded','total')}
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
                if telemetry and not self.practice_pending:
                    for event in telemetry.get('events',[]):
                        kind=event.get('kind') if isinstance(event,dict) else None
                        if kind not in ('restart','switch_character'):continue
                        shell=getattr(host.session,'player_type',None) or host.session.options.player_type
                        if kind=='switch_character':shell=event.get('type')
                        if type(shell) is not int or shell not in (1,2,3,5):continue
                        self.practice_pending=True
                        return dict(map_id=self.source['map'],shell=shell)
                return
            # The client may send several takeover requests before it receives
            # our branch offer. Keep that one host until the offer is committed;
            # replacing it strands the first simulation and drops all feedback.
            if self.branch is not None:
                return
            request=value.get('checkpoint')
            if not request or status.get('state')not in ('playing','paused','taking-over'):return
            request=dict(request,record=self.source['id'])
            serial=self.serial
            warm_host=self.warm
            if value.get('takeover'):
                if self.preparing:return
                self.preparing=True
                source_document=self.command['document']
                def prepare():
                    created=None
                    try:
                        created=warm_host.take(self.records,self.clients,request)
                        # Mark the actual handoff boundary, after warm staging
                        # has advanced to this requested cursor.
                        self.clients.mark_runner(created.branch['id'],source_document,request['cursor'],
                            dict(client=self.client,adapter=adapter_digest()),engine='native')
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
            elif (not self.warming and not self.preparing and getattr(self,'warm_cursor',None)!=request.get('cursor')
                    and time.monotonic()-getattr(self,'last_warm',0)>=.5):
                self.warm_cursor=request.get('cursor')
                self.last_warm=time.monotonic()
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
        from urllib.request import Request,build_opener,ProxyHandler
        request=Request(self.url+path,data=None if value is None else json.dumps(value).encode(),
            headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with build_opener(ProxyHandler({})).open(request,timeout=10) as response:raw=response.read(128*1024+1)
        if len(raw)>128*1024:raise ValueError('Native control response is too large')
        return json.loads(raw)
