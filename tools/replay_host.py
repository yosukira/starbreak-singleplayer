"""Verified continuation of an existing browser world, over a local socket.

The installed client keeps its own physics/actions and unread network queue.
This host reconstructs the matching real encounter controller and continues
the existing two cipher streams. It never loads code or memory from a replay.
"""
import copy
import json
import queue
import socket
from pathlib import Path
import struct
import threading
import time
from types import SimpleNamespace
import uuid

from localserver import game_settings, pb_layout
from localserver.battle_records import BattleRecorder, RecordStore
from localserver.live.bridge import PlayerBridge
from localserver.replay import (ServerReplay, ServerCapture, JournalWriter,
                                gameplay_settings, read_journal)
from localserver.run_replay import RunReplay,RunCapture,SCHEMA as RUN_SCHEMA
from tools.replay_stream import ReplayStream, framed


def state_player(state):
    if not isinstance(state, list) or len(state) != 14 or any(type(v) is not int for v in state):
        raise ValueError('Invalid player checkpoint')
    if state[8] <= 0 or state[13] != 1:
        raise ValueError('Takeover requires a living player')
    return dict(zip(('room_seed','total_input','objid','worldid','xmp','ymp','wmp','hmp',
                     'hp','maxhp','facing','ammo','input','alive'), state), vid='browser-player')


class PreparedReplay:
    def __init__(self, records, clients, request, client=None, server=None):
        self.source=records.get(str(uuid.UUID(request['record'])))
        self.attempt=str(uuid.UUID(request['id']))
        if self.source.get('replay',{}).get('id')!=self.attempt:raise ValueError('Replay does not belong to this battle')
        self.client=clients.read(self.attempt) if client is None else client
        self.server=read_journal(Path(records.directory)/(self.source['id']+'.server.jsonl.gz')) if server is None else server
        if self.server.get('attempt')!=self.attempt:raise ValueError('Server recording does not belong to this replay')
        if self.client.get('schema')=='starbreak-native-journal-v1':
            from tools.native_replay_stream import NativeReplayStream
            self.stream=NativeReplayStream(self.client,self.server)
        else:self.stream=ReplayStream(self.client,self.server)
        self.rebuilt=None
        self.clients=clients;self.branch=None;self.branch_cursor=0;self.writer=None;self.adopted=False

    def at(self, request, progress=None):
        cursor=request.get('cursor');ops=self.client['ops']
        if type(cursor)is int and self.branch and cursor<self.branch_cursor:
            self.close_staging();self.rebuilt=None
        if type(cursor)is not int or not 0<cursor<=len(ops) or ops[cursor-1]!=['loop',1]:raise ValueError('Take over between complete game iterations')
        checkpoint=next((row[1] for row in reversed(ops[:cursor]) if row[0]=='state'),None)
        if checkpoint!=request.get('player'):raise ValueError('Client player state has not been verified')
        player=state_player(checkpoint)
        received,sent=self.stream.progress(cursor)
        boundary,suffix=self.stream.catchup(received)
        if self.rebuilt is None or self.rebuilt.index>boundary.frame+1:
            self.rebuilt=RunReplay(self.server) if self.server.get('schema')==RUN_SCHEMA else ServerReplay(self.server,wire=self.stream)
        while self.rebuilt.index<=boundary.frame:
            if progress and self.rebuilt.index%100==0:progress(self.rebuilt.index,boundary.frame+1)
            self.rebuilt.advance()
        # Keep journal copying and cipher positioning off the input edge.
        self.stream.transmitter(received);self.stream.receiver(sent)
        if not self.branch:self.branch=self.clients.fork(self.attempt,cursor,source=self.client)
        elif cursor>=self.branch_cursor:self.clients.extend_fork(self.branch,self.client['ops'][self.branch_cursor:cursor])
        else:
            self.close_staging()
            self.branch=self.clients.fork(self.attempt,cursor,source=self.client)
        self.branch_cursor=cursor
        if self.writer is None:
            header=dict(self.server,attempt=self.branch['id']);header.pop('frames')
            path=self.clients.path.parent/('.takeover-'+self.branch['id']+'.pending.gz')
            self.writer=JournalWriter(path,header)
        for row in self.server['frames'][self.writer.count:boundary.frame+1]:self.writer.frame(row)
        return player,boundary,suffix,received,sent

    def close_staging(self):
        if self.adopted:return
        if self.writer:
            self.writer.close(complete=False);self.writer.path.unlink(missing_ok=True);self.writer=None
        if self.branch:
            with self.clients.connect() as db:
                db.execute('DELETE FROM chunks WHERE id=?',(self.branch['id'],))
                db.execute('DELETE FROM attempts WHERE id=?',(self.branch['id'],))
            self.branch=None;self.branch_cursor=0
        if self.rebuilt and self.server.get('schema')==RUN_SCHEMA:self.rebuilt.close()



class WarmTakeovers:
    def __init__(self):
        self.lock=threading.RLock();self.key=None;self.prepared=None;self.closed=False

    def prime(self,records,clients,request,client,server):
        with self.lock:
            if self.closed:return
            key=(request.get('id'),request.get('record'))
            if self.prepared is None:
                self.prepared=PreparedReplay(records,clients,request,client,server);self.key=key

    def get(self, records, clients, request):
        if self.closed:raise ValueError('Replay preparation has closed')
        key=(request.get('id'),request.get('record'))
        if key!=self.key or self.prepared is None:
            if self.prepared:self.prepared.close_staging()
            value=PreparedReplay(records,clients,request)
            self.key,self.prepared=key,value
        return self.prepared

    def warm(self, records, clients, request):
        with self.lock:
            prepared=self.get(records,clients,request)
            prepared.at(request)
            return dict(ready=True)

    def close(self):
        with self.lock:
            self.closed=True
            if self.prepared:self.prepared.close_staging()
            self.prepared=None;self.key=None

    def take(self, records, clients, request, **options):
        with self.lock:
            prepared=self.get(records,clients,request)
            result=Takeover(records,clients,request,prepared=prepared,**options)
            self.prepared=None
            return result


class Takeover:
    def __init__(self, records, clients, request, progress=None, manual=False, prepared=None):
        self.manual=manual
        if progress:progress(0,0)
        self.directory=Path(records.directory)
        prepared=prepared or PreparedReplay(records,clients,request)
        self.source=prepared.source
        client,server=prepared.client,prepared.server
        attempt=prepared.attempt;cursor=request['cursor']
        player,boundary,self.suffix,received,sent=prepared.at(request,progress)
        self.stream=prepared.stream
        self.full_run=server.get('schema')==RUN_SCHEMA
        rebuilt=self.rebuilt=prepared.rebuilt
        if progress is not None:progress(boundary.frame + 1, boundary.frame + 1)
        if getattr(rebuilt.session,'phase',None) in ('death','done','failed','defeated'):
            raise ValueError('This battle has already ended')
        self.settings = gameplay_settings(rebuilt.settings)
        self.bridge = PlayerBridge(0)
        self.bridge._absorb(dict(seq=0, player=player, keys=dict(interact=False), events=[]))
        self.transport, self.thread = None, None
        self.tx, self.rx = self.stream.transmitter(received), self.stream.receiver(sent)
        self.send_lock, self.boundary = threading.RLock(), threading.RLock()
        self.stopped, self.committed = threading.Event(), False
        self.record_error = None
        self.incoming = bytearray()
        self.created = time.monotonic()
        params = self.source['parameters']
        descriptions = {}
        for item in params.get('items', []):
            objects = [{} for _ in range(max((p['index'] for p in item['projectiles']), default=-1)+1)]
            for p in item['projectiles']: objects[p['index']] = dict(speed_=p['speed'])
            descriptions[item['type']] = dict(name_=item['name'],statboosts_=item['base'],obj_=objects)
        if self.full_run:
            self.server=rebuilt.server
            self.server.item_descriptions=descriptions
            self.channel=RunChannel(self)
            self.session=rebuilt.continue_live(self.bridge,self.channel)
            self.session.owns_bridge=False
            self.server._start_live=self.start_room
            self.read_thread=None
        else:
            self.server = SimpleNamespace(layouts=pb_layout.default_layouts(), _worldid=lambda: rebuilt.session.worldid,
                _send_raw=self.send, item_types=set(server['item_types']), item_descriptions=descriptions,
                class_loadouts={server['shell']:copy.deepcopy(params['loadout'])},
                _live_world_sequence=server.get('world_sequence',1))
            self.session = rebuilt.continue_live(self.bridge, self.server, None)
        self.session.options.web_client = client.get('schema')!='starbreak-native-journal-v1'
        self.recorder = BattleRecorder(self.session, records)
        self.session.battle_recorder = self.recorder
        self.token = str(uuid.uuid4())
        self.branch = prepared.branch
        elapsed_kind='step' if client.get('schema')=='starbreak-native-journal-v1' else 'frame'
        self.parent_seconds = sum(row[1] for row in client['ops'][:cursor] if row[0]==elapsed_kind)/1000.
        with game_settings.event_snapshot(self.settings):
            self.capture = (RunCapture if self.full_run else ServerCapture)(self.session, keep_frames=False)
        self.capture.header = dict(server, attempt=self.branch['id'])
        self.capture.header.pop('frames')
        self.capture.frame_count = boundary.frame+1
        if not self.full_run:
            wall = server['frames'][boundary.frame]['wall']
            self.capture.started = time.monotonic()-wall
            since = getattr(self.session,'_player_missing_since',None)
            if since is not None:self.session._player_missing_since = wall-(time.time()-since)
        self.writer=prepared.writer
        self.journal_path=self.directory/(self.recorder.identifier+'.server.jsonl.gz')
        prepared.adopted=True
        self.capture.sink = self.writer.frame
        self.capture_closed = False
        if self.full_run:self.capture.enter(self.session,record=False)

    def description(self):
        return dict(token=self.token, id=self.branch['id'], sequence=self.branch['sequence'], record=self.recorder.identifier,
                    size=self.branch.get('bytes'))

    def bind(self, transport):
        with self.boundary:
            if self.transport is not None or self.stopped.is_set():
                raise ValueError('Takeover connection already used')
            self.transport = transport
            # The suffix starts at the existing client's exact stream offset.
            # No second Hello, CONNECT, CreateWorld or CreatePlayer is sent.
            if self.suffix:
                transport.send(self.tx.crypt(self.suffix))
            transport.send(json.dumps(dict(type='takeover-ready',**self.description())))

    def commit(self):
        with self.boundary:
            if self.committed:return
            if self.transport is None or self.stopped.is_set():raise ValueError('Takeover connection is not ready')
            self.committed = True
            with game_settings.event_snapshot(self.settings):
                try:
                    self.recorder.tick(0)
                    self.recorder.document['parent'] = dict(id=self.source['id'],at=self.parent_seconds)
                    self.recorder.document['replay'] = dict(id=self.branch['id'])
                    if getattr(self,'portable_runner',False):
                        self.recorder.document['platform']='web'
                        self.recorder.document['replay']['format']='native'
                    if self.recorder.player_acted:
                        self.recorder.store.save(self.recorder.document)
                except Exception as error:
                    self.record_error=type(error).__name__
                    self.close_capture()
            if self.manual:
                if self.full_run:
                    self.room_messages = self.server.messages(self.channel)
                    next(self.room_messages)
                return
            self.thread = threading.Thread(target=self.run,daemon=True)
            if self.full_run:
                self.read_thread=threading.Thread(target=self.read_rooms,daemon=True)
                self.read_thread.start()
            self.thread.start()

    def start_room(self,channel):
        """Use the real level constructor after the normal WorldCreated ACK."""
        from localserver.server import _map_session_class
        with self.boundary,game_settings.event_snapshot(self.settings):
            if self.stopped.is_set():return
            if self.server.live_session is not None:return
            if self.server.options.map!=self.capture.header['map']:
                self.recorder.finish()
                self.close_capture()
                raise ValueError('This recorded level has ended')
            bridge=self.server._restart_bridge or self.bridge
            bridge.native_context=None
            self.server._live_world_sequence+=1
            session=_map_session_class(self.server.options.map)(self.server,channel,self.server.options,bridge=bridge)
            session.owns_bridge=False
            self.server.live_session=self.session=session
            self.server._restart_bridge=None;self.server._restart_in_progress=False;self.server._restart_player_type=None
            self.recorder.bind(session);session.battle_recorder=self.recorder
            if not self.capture_closed:
                try:self.capture.enter(session)
                except (OSError,ValueError) as error:
                    self.capture.failed=type(error).__name__;self.close_capture()

    def read_rooms(self):
        try:self.server._read_loop(self.channel)
        except Exception as error:
            if not self.stopped.is_set():
                self.error=type(error).__name__+': '+str(error);self.stop()

    def send(self, channel, kind, payload, *args, **kwargs):
        if self.stopped.is_set():return
        with self.send_lock:
            self.transport.send(self.tx.crypt(framed(kind,payload)))

    def telemetry(self, document):
        if not isinstance(document,dict) or not isinstance(document.get('player'),dict):
            raise ValueError('Invalid takeover feedback')
        self.bridge._absorb(document)
        return self.bridge.acknowledgement(document.get('seq'))

    def binary(self, data):
        if len(data)>1024*1024:raise ValueError('Takeover input exceeds 1 MB')
        self.incoming.extend(self.rx.crypt(data))
        while len(self.incoming)>=5:
            length=struct.unpack('>i',self.incoming[1:5])[0]
            if not 0<=length<=1024*1024:raise ValueError('Invalid takeover input frame')
            if len(self.incoming)<length+5:break
            if self.full_run:
                message = (self.incoming[0], bytes(self.incoming[5:length+5]))
                if self.manual:
                    self.room_messages.send(message)
                else:
                    self.channel.enqueue(message)
            # As in the standalone live host, real player physics/damage are
            # supplied by the bridge; TimePasses and ACKs are simply drained.
            del self.incoming[:length+5]

    def step(self, dt):
        with self.boundary, game_settings.event_snapshot(self.settings):
            if self.stopped.is_set():return
            if self.full_run:
                if self.server.live_session is None:return
                if self.capture_closed:self.session.step(dt)
                else:self.capture.step(dt)
            elif self.capture_closed:self.capture.step(dt)
            else:self.capture.run('step',dt)
            if self.record_error is None:
                try:self.recorder.tick(dt)
                except Exception as error:self.record_error=type(error).__name__
            if self.recorder.closed or self.capture.failed or self.record_error:self.close_capture()

    def run(self):
        previous=time.monotonic();deadline=previous
        try:
            while not self.stopped.is_set():
                now=time.monotonic();dt=min(.05,max(1/60,now-previous));previous=now
                self.step(dt)
                deadline=max(deadline+1/60,now)
                self.stopped.wait(max(0,deadline-time.monotonic()))
        except Exception as error:
            self.error=type(error).__name__+': '+str(error)
            self.stop()

    def close_capture(self):
        if self.capture_closed:return
        try:
            self.writer.close(complete=not (self.capture.failed or self.record_error))
            self.writer.path.replace(self.journal_path)
        except Exception as error:self.record_error=type(error).__name__
        finally:
            if self.full_run:self.capture.detach()
            else:self.capture.detach(restore_methods=False)
            self.capture_closed=True

    def stop(self):
        self.stopped.set()
        if self.thread is not None and self.thread is not threading.current_thread():self.thread.join(timeout=2)
        reader=getattr(self,'read_thread',None)
        if reader is not None and reader is not threading.current_thread():reader.join(timeout=1)
        with self.boundary:
            try:self.recorder.finish()
            except Exception as error:self.record_error=type(error).__name__
            finally:
                self.close_capture()
                self.session.stop()
                if self.full_run:self.rebuilt.close()


class RunChannel:
    """The existing server handshake consumes plaintext frames from this queue."""
    def __init__(self,host):self.host=host;self.messages=queue.Queue(maxsize=128)
    def send_frame(self,kind,payload):return self.host.send(self,kind,payload)
    def enqueue(self,message):
        try:self.messages.put_nowait(message)
        except queue.Full:raise ValueError('Too many pending room messages')
    def read_frame(self):
        if self.host.stopped.is_set():raise ConnectionError('Takeover closed')
        try:return self.messages.get(timeout=.2)
        except queue.Empty:raise socket.timeout()
