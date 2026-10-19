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
import uuid

from plugins._native_replay_capture import CLIENTS
from tools.native_replay import NativeJournals,adapter_digest,document
from tools import native_symbols


class NativeRecording:
    def __init__(self,directory,executable,viewport=(1366,768),registry=None):
        manifest=native_symbols.resolve(executable)
        if manifest['client'] not in CLIENTS:raise ValueError('The native replay adapter does not match this executable')
        self.identifier=str(uuid.uuid4());self.token=secrets.token_hex(32)
        self.journals=NativeJournals(directory);self.lock=threading.RLock();self.closed=False
        self.restart_request=None
        from tools.native_playback import NativePlayback
        self.playback=NativePlayback(directory,manifest['client']);self.ready=threading.Event();self.proxy=None
        self.registry=Path(registry) if registry else None
        self.journals.begin(self.identifier,manifest['client'],adapter_digest(),dict(content=None),viewport)
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
                if url.path=='/status':return self.result(dict(owner.playback.status,serial=owner.playback.serial,error=owner.playback.error or owner.playback.status.get('error')))
                if url.path!='/control':return self.send_error(404)
                try:
                    serial=int(parse_qs(url.query).get('serial',['0'])[0]);owner.ready.set()
                    return self.result(owner.playback.poll(serial))
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
                            with owner.lock:
                                if owner.closed:raise ValueError('Native launcher has closed')
                                owner.restart_request=dict(map=map_id,shell=shell)
                            return self.result(dict(starting=True,map=map_id,shell=shell))
                        return self.result(owner.playback.action(value))
                    if self.path=='/feedback':owner.playback.feedback(value);return self.result(dict(accepted=True))
                    if self.path=='/availability':
                        identifiers=value['ids']
                        if not isinstance(identifiers,list) or len(identifiers)>500:raise ValueError('Invalid native recording list')
                        identifiers=[str(uuid.UUID(v)) for v in identifiers]
                        return self.result(owner.playback.availability(identifiers))
                    with owner.lock:
                        if owner.closed:raise ValueError('Native recording has ended')
                        identifier=value.get('attempt',owner.identifier)
                        allowed=[owner.identifier]+list(owner.playback.branches)
                        if identifier not in allowed:raise ValueError('Native recording is not owned by this launcher')
                        result=owner.journals.append(identifier,value)
                    return self.result(result)
                except (ValueError,TypeError,KeyError):self.send_error(400,'Invalid native recording chunk')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads=True
        self.config=dict(url='http://127.0.0.1:'+str(self.server.server_address[1])+'/chunks',token=self.token,manifest=manifest)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        if self.registry:
            from tools.web_workspace import atomic_json
            atomic_json(self.registry,dict(url=self.config['url'],token=self.token,pid=os.getpid()))
            self.registry.chmod(0o600)

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
                self.ready.wait(15)
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
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=5)
        if self.registry:
            try:
                if json.loads(self.registry.read_text()).get('token')==self.token:self.registry.unlink()
            except (OSError,ValueError):pass
        # A game process can close without running a plugin destructor. Retain
        # only a fully delivered and validated frame boundary, never a half
        # native call. Pending/in-flight chunks are not guessed or synthesized.
        for identifier in [self.identifier]+list(self.playback.branches):
            self.seal(identifier)

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
