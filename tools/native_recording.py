"""Receive journals only from the owned, checked offline native process.

This loopback endpoint is private to one launcher. It shares the battle store,
never starts a browser game, and cannot execute anything supplied by a trace.
"""
import hmac
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import secrets
import os
from pathlib import Path
import socket
import select
import threading
import time
import uuid

from plugins._native_replay_capture import CLIENTS
from tools.native_replay import NativeJournals,adapter_digest,document
from tools import native_symbols


class NativeRecording:
    def __init__(self,directory,executable,viewport=(1366,768),registry=None,recording=True,health_path=None,practice_path=None):
        manifest=native_symbols.resolve(executable)
        if manifest['client'] not in CLIENTS:raise ValueError('The native replay adapter does not match this executable')
        self.identifier=str(uuid.uuid4());self.token=secrets.token_hex(32)
        self.journals=NativeJournals(directory);self.lock=threading.RLock();self.closed=False
        self.restart_request=None
        self.practice_path=Path(practice_path) if practice_path else None
        self.health_path=Path(health_path) if health_path else None
        self.started=time.monotonic()
        self.started_wall=time.time();self.finished_pending={};self.finalizer_stop=threading.Event()
        from tools.native_playback import NativePlayback
        self.playback=NativePlayback(directory,manifest['client']);self.ready=threading.Event();self.proxy=None
        self.registry=Path(registry) if registry else None
        self.recording=recording
        self.identifiers={self.identifier} if recording else set()
        self.viewport=viewport;self.manifest=manifest;self.resume=None
        if recording:self.journals.begin(self.identifier,manifest['client'],adapter_digest(),dict(content=None),viewport)
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def authorized(self):return hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+owner.token)
            def result(self,value):
                raw=json.dumps(value,separators=(',',':')).encode();self.send_response(200)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)))
                self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(raw)
            def do_GET(self):
                if not self.authorized():return self.send_error(403)
                from urllib.parse import urlsplit,parse_qs
                url=urlsplit(self.path)
                if url.path=='/health':return self.result(owner.health())
                if url.path=='/status':return self.result(dict(owner.playback.status,serial=owner.playback.serial,error=owner.playback.error or owner.playback.status.get('error')))
                if url.path!='/control':return self.send_error(404)
                try:
                    serial=int(parse_qs(url.query).get('serial',['0'])[0]);owner.ready.set()
                    result=owner.playback.poll(serial)
                    resume=owner.resume
                    if resume and serial!=resume['serial']:
                        result['resume']={k:resume[k] for k in ('serial','id','recording')}
                    return self.result(result)
                except (ValueError,TypeError,KeyError):return self.send_error(400)
            def do_POST(self):
                if self.path not in('/chunks','/command','/feedback','/availability'):return self.send_error(404)
                if not self.authorized():return self.send_error(403)
                self.connection.settimeout(5)
                try:
                    length=int(self.headers.get('Content-Length',0))
                    if not 0<length<=512*1024:raise ValueError('Invalid native recording body')
                    value=json.loads(self.rfile.read(length))
                    if not isinstance(value,dict):raise ValueError('Expected native request')
                    if self.path=='/command':
                        if value.get('action')=='practice':
                            from localserver import game_settings
                            map_id,shell=value.get('map'),value.get('shell')
                            if map_id not in game_settings.available_maps() or type(shell) is not int or shell not in (1,2,3,5):
                                raise ValueError('Invalid practice encounter')
                            owner.practice(map_id,shell)
                            return self.result(dict(starting=True,map=map_id,shell=shell))
                        return self.result(owner.playback.action(value))
                    if self.path=='/feedback':
                        resume=owner.resume
                        if resume and value.get('resume')==resume['id'] and value.get('serial')==resume['serial']:
                            from tools.web_workspace import atomic_json
                            with owner.lock:
                                if owner.resume is resume:
                                    atomic_json(owner.practice_path,resume['practice'])
                                    owner.resume=None
                        else:
                            practice=owner.playback.feedback(value)
                            # Reset outside the playback lock: stopping its host
                            # joins a simulation thread which takes that lock.
                            if practice:owner.practice(**practice)
                        return self.result(dict(accepted=True))
                    if self.path=='/availability':
                        identifiers=value['ids']
                        if not isinstance(identifiers,list) or len(identifiers)>500:raise ValueError('Invalid native recording list')
                        identifiers=[str(uuid.UUID(v)) for v in identifiers]
                        return self.result(owner.playback.availability(identifiers))
                    with owner.lock:
                        if owner.closed:raise ValueError('Native recording has ended')
                        identifier=value.get('attempt',owner.identifier)
                        allowed=list(owner.identifiers)+list(owner.playback.branches)
                        if identifier not in allowed:raise ValueError('Native recording is not owned by this launcher')
                        result=owner.journals.append(identifier,value)
                    return self.result(result)
                except (ValueError,TypeError,KeyError):self.send_error(400,'Invalid native recording chunk')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.config=dict(url='http://127.0.0.1:'+str(self.server.server_address[1])+'/chunks',token=self.token,manifest=manifest,recording=recording,attempt=self.identifier)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.finalizer=threading.Thread(target=self._finalize_loop,daemon=True);self.finalizer.start()
        if self.registry:
            from tools.web_workspace import atomic_json
            atomic_json(self.registry,dict(url=self.config['url'],token=self.token,pid=os.getpid()))
            self.registry.chmod(0o600)

    def practice(self,map_id,shell):
        from tools.web_workspace import atomic_json
        enabled=self.recording
        try:
            if self.registry:enabled=json.loads((self.registry.parent/'recording.json').read_text(encoding='utf8')).get('record_replays',True) is not False
        except (OSError,ValueError,TypeError):pass
        if self.closed:raise ValueError('Native launcher has closed')
        if not self.practice_path:
            self.restart_request=dict(map=map_id,shell=shell);return
        if not self.playback.source and not enabled and not self.recording:
            atomic_json(self.practice_path,dict(id=str(uuid.uuid4()),map=map_id,shell=shell));return
        self.playback.reset()
        with self.lock:
            identifier=str(uuid.uuid4())
            if enabled:
                self.journals.begin(identifier,self.manifest['client'],adapter_digest(),dict(content=None),self.viewport)
                self.identifiers.add(identifier)
            self.identifier=identifier;self.recording=enabled
            request=dict(id=str(uuid.uuid4()),map=map_id,shell=shell,attempt=identifier,recording=enabled)
            self.resume=dict(serial=self.playback.serial,id=identifier,recording=enabled,practice=request)

    def health(self):
        value={}
        try:
            if self.health_path:value=json.loads(self.health_path.read_text(encoding='utf8'))
        except (OSError,ValueError):pass
        if not isinstance(value,dict) or type(value.get('at')) not in (int,float):value={}
        if not value:return dict(state='starting' if time.monotonic()-self.started<20 else 'missing')
        if time.time()-value.get('at',0)>4:return dict(state='stale')
        if not value.get('active'):state='error' if value.get('error') else 'disabled'
        elif value.get('hook_error'):state='degraded'
        elif not value.get('connected'):state='waiting'
        else:state='connected'
        return dict(state=state,replay_ready=value.get('replay_ready',False),recording=self.recording,
                    recording_error=value.get('recording_error_detail') or value.get('recording_error') or getattr(self,'finalize_error',None))

    def _finalize_loop(self):
        while not self.finalizer_stop.wait(.5):
            try:self.finalize_battles()
            except (OSError,ValueError,EOFError):pass
            except Exception as error:self.finalize_error=type(error).__name__

    def finalize_battles(self,immediate=False):
        """Publish immutable per-battle tapes while the original game keeps running."""
        from localserver.battle_records import encoded
        from localserver.replay import read_journal,JournalWriter
        from tools.replay_bundle import client_chunks
        records=self.playback.records
        with records.connect() as db:
            rows=db.execute('SELECT id,document FROM records WHERE updated>=?',(self.started_wall,)).fetchall()
        owned={*self.identifiers,*self.playback.branches}
        for identifier,raw in rows:
            record=json.loads(raw);source=record.get('replay',{}).get('id')
            if source not in owned or record['result']=='active':continue
            first=self.finished_pending.setdefault(identifier,time.monotonic())
            if not immediate and time.monotonic()-first<1:continue
            path=records.directory/(identifier+'.server.jsonl.gz')
            if not path.is_file():continue
            journal=read_journal(path)  # A still-open server frame must finish first.
            target=str(uuid.uuid5(uuid.UUID(source),'battle:'+identifier))
            trace=self.journals.read(source,require_complete=False)
            end=next((i+1 for i in range(len(trace['ops'])-1,-1,-1) if trace['ops'][i]==['loop',1]),0)
            if not end:continue
            if record['map'] in __import__('localserver.replay',fromlist=['SUPPORTED_MAPS']).SUPPORTED_MAPS:
                from tools.native_playback import native_battle_view
                end=min(end,native_battle_view(trace['ops'],journal.get('world_sequence',1))['end'])
            if record['result']=='defeat':
                state=next((row[1] for row in reversed(trace['ops'][:end]) if row[0]=='state'),None)
                if not state or len(state)==14 and state[8]>0:continue
            trace=document(dict(trace,id=target,ops=trace['ops'][:end]));chunks=list(client_chunks(trace))
            header={key:value for key,value in trace.items() if key!='ops'}
            with self.journals.connect() as db:
                db.execute('INSERT OR IGNORE INTO attempts VALUES(?,?,?,?,0)',(target,json.dumps(header,separators=(',',':')),0,0))
            for start in range(0,len(chunks),4):
                with self.journals.connect() as db:
                    db.executemany('INSERT OR IGNORE INTO chunks VALUES(?,?,?,?)',[(target,s,d,c) for s,d,c,_ in chunks[start:start+4]])
            with self.journals.connect() as db:
                db.execute('UPDATE attempts SET sequence=?,size=?,complete=1 WHERE id=?',(len(chunks),sum(row[3] for row in chunks),target))
            # Rebind only the identity; every recorded server frame is preserved.
            journal['attempt']=target;frames=journal.pop('frames')
            temporary=path.with_suffix('.snapshot.gz');temporary.unlink(missing_ok=True)
            writer=JournalWriter(temporary,journal)
            try:
                for frame in frames:writer.frame(frame)
                writer.close()
                record['replay']['id']=target
                with records.connect() as db:
                    current=db.execute('SELECT document FROM records WHERE id=?',(identifier,)).fetchone()
                    if current and json.loads(current[0]).get('replay',{}).get('id')==source:
                        os.replace(temporary,path)
                        db.execute('UPDATE records SET document=? WHERE id=?',(encoded(record).decode(),identifier));self.finalize_error=None
            finally:
                writer.close(False);temporary.unlink(missing_ok=True)

    def gate(self,upstream):
        """Delay only this owned connection until the capture hooks are ready."""
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(1);listener.settimeout(.2)
        stopped=threading.Event();connections=[]
        def run():
            try:
                while not stopped.is_set():
                    try:client,_=listener.accept();break
                    except socket.timeout:continue
                else:return
                connections.append(client)
                target=socket.create_connection(('127.0.0.1',upstream),timeout=5);connections.append(target)
                # Missing/unsupported hooks must not prevent ordinary practice.
                deadline=time.monotonic()+15
                while not self.ready.wait(.05):
                    # A completed, failed recorder initialization is decisive;
                    # ordinary practice must not wait for a control poll that
                    # can never arrive.
                    try:
                        status=json.loads(self.health_path.read_text(encoding='utf8')) if self.health_path else {}
                        if status.get('replay_initialized') and not status.get('replay_ready'):break
                    except (OSError,ValueError):pass
                    if stopped.is_set() or time.monotonic()>=deadline:break
                for stream in connections:stream.setblocking(False)
                while not stopped.is_set():
                    ready,_,_=select.select(connections,[],[],.2)
                    for source in ready:
                        data=source.recv(65536)
                        if not data:return
                        other=target if source is client else client
                        other.setblocking(True);other.sendall(data);other.setblocking(False)
            except OSError:pass
            finally:
                for stream in connections:stream.close()
        thread=threading.Thread(target=run,daemon=True);thread.start()
        self.proxy=(listener,stopped,thread,connections)
        return listener.getsockname()[1]

    def close(self):
        self.finalizer_stop.set();self.finalizer.join(timeout=30)
        with self.lock:
            if self.closed:return
            self.closed=True
        self.ready.set()
        if self.proxy:
            listener,stopped,thread,connections=self.proxy;stopped.set();listener.close()
            for stream in connections:
                try:stream.shutdown(socket.SHUT_RDWR)
                except OSError:pass
            thread.join(timeout=2)
        self.playback.close()
        self.playback.records.finish_owner(os.getpid())
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.registry:
            try:
                if json.loads(self.registry.read_text()).get('token')==self.token:self.registry.unlink()
            except (OSError,ValueError):pass
        # A game process can close without running a plugin destructor. Retain
        # only a fully delivered and validated frame boundary, never a half
        # native call. Pending/in-flight chunks are not guessed or synthesized.
        for identifier in list(self.identifiers)+list(self.playback.branches):
            self.seal(identifier)
        self.finalize_battles(immediate=True)

    def seal(self,identifier):
        with self.lock:
            trace=self.journals.read(identifier,require_complete=False)
            end=next((i+1 for i in range(len(trace['ops'])-1,-1,-1) if trace['ops'][i]==['loop',1]),0)
            if not end:
                with self.journals.connect() as db:
                    db.execute('DELETE FROM chunks WHERE id=?',(identifier,))
                    db.execute('DELETE FROM attempts WHERE id=?',(identifier,))
                return
            trace['ops']=trace['ops'][:end];trace=document(trace)
            from tools.replay_bundle import client_chunks
            chunks=list(client_chunks(trace))
            with self.journals.connect() as db:
                db.execute('UPDATE attempts SET sequence=sequence WHERE id=?',(identifier,))
                current=db.execute('SELECT complete FROM attempts WHERE id=?',(identifier,)).fetchone()
                if current[0]:return
                db.execute('DELETE FROM chunks WHERE id=?',(identifier,))
                db.executemany('INSERT INTO chunks VALUES(?,?,?,?)',[(identifier,s,d,c) for s,d,c,_ in chunks])
                db.execute('UPDATE attempts SET sequence=?,size=?,complete=1 WHERE id=?',(len(chunks),sum(row[3] for row in chunks),identifier))
