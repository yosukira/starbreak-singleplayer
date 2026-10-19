"""Whole-level journals rebuild real paused room runtimes and door handoffs.

Only inputs to the existing simulator are recorded. Room snapshots, actors,
random generators and switches are reconstructed by running the installed map
code; neither imported Python objects nor client memory snapshots are loaded.
"""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

from . import game_settings, paths
from .battle_records import parameters, snapshot
from .live.bridge import PlayerBridge
from .replay import (FULL_RUN_MAPS, MAX_CALLS, MAX_FRAMES, ObservedBridge,
    RecordedBridge, ReplayMismatch, ServerCapture, OPTIONS, METHODS, call_arguments, call_value,
    engine_fingerprint, compatible_engine, finite, gameplay_settings, integer, packet_digest, refresh_settings)

SCHEMA='starbreak-run-journal-v1'


def observation(value):
    method=value['method']
    if method not in ('datagrams','snapshot','consume_interactions','connected','peek_events','poll_events'):
        raise ValueError('Unknown run observation')
    args=call_arguments(method,value['args'])
    restored=call_value(method,value['value'],reading=True)
    clean=call_value(method,restored)
    if method=='snapshot' and clean[0] is not None:clean[0]['age']=value['value'][0]['age']
    if method=='consume_interactions':
        for original,current in zip(value['value'],clean):
            if current is not None:current['age']=original['age']
    return dict(method=method,args=args,value=clean)


def validate(document):
    if document.get('schema')!=SCHEMA or document.get('map') not in FULL_RUN_MAPS:
        raise ValueError('Unsupported full-run recording')
    digest=document['engine']
    if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid run engine identity')
    result=dict(schema=SCHEMA,engine=digest,map=document['map'],seed=str(integer(int(document['seed']),0,2**64-1)),
        shell=integer(document['shell'],1,5),web=document.get('web') is True,
        world_sequence=integer(document['world_sequence'],1,10000),
        input_serial=integer(document['input_serial'],1,10000),
        teleport_serial=integer(document.get('teleport_serial',0),0,2**31-1),
        parameters=parameters(document['parameters']),settings=gameplay_settings(document['settings']),options={},frames=[])
    for key,value in document.get('options',{}).items():
        if key not in OPTIONS:continue
        if key in ('live_demo_wait','live_player_gap'):value=finite(value,0,3600)
        elif type(value) is not bool:raise ValueError('Invalid run replay option')
        result['options'][key]=value
    if 'attempt' in document:
        import uuid
        result['attempt']=str(uuid.UUID(document['attempt']))
    frames=document['frames']
    if not isinstance(frames,list) or not 1<=len(frames)<=MAX_FRAMES or frames[0].get('action')!='enter':
        raise ValueError('Invalid full-run lifecycle')
    previous=0
    for row in frames:
        wall=finite(row['wall'],previous,1e12);previous=wall
        action=row['action']
        if action not in ('enter','step'):raise ValueError('Invalid full-run action')
        clean=dict(action=action,wall=wall,dt=finite(row['dt'],0,1),packets=integer(row['packets'],0,10000),sha256=row['sha256'],calls=[])
        if not isinstance(clean['sha256'],str) or len(clean['sha256'])!=64 or any(c not in '0123456789abcdef' for c in clean['sha256']):
            raise ValueError('Invalid run packet digest')
        calls=row['calls']
        if not isinstance(calls,list) or len(calls)>MAX_CALLS:raise ValueError('Too many run observations')
        clean['calls']=[observation(call) for call in calls]
        if action=='enter':
            if clean['dt'] or clean['calls'] or clean['packets']:
                raise ValueError('Invalid full-run room entry')
            for key in ('role','entry'):
                value=row[key]
                if not isinstance(value,str) or not 1<=len(value)<=80 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in value):
                    raise ValueError('Invalid recorded room reference')
                clean[key]=value
            clean['world_sequence']=integer(row['world_sequence'],1,10000)
        if 'settings' in row:clean['settings']=gameplay_settings(row['settings'])
        result['frames'].append(clean)
    return result


class ObservedChannel:
    def __init__(self,source,capture):self.source,self.capture=source,capture
    def __getattr__(self,name):return getattr(self.source,name)
    def send_frame(self,kind,payload):
        result=self.source.send_frame(kind,payload)
        self.capture.packet(kind,payload)
        return result


class RunCapture:
    def __init__(self,session,sink=None,keep_frames=True):
        if session.options.map not in FULL_RUN_MAPS or getattr(session.bridge,'shared_room',False):
            raise ValueError('Unsupported full-run recorder')
        if session.server.GROUND_EGRESS_REQUIRED:
            raise ValueError('Native ground egress replay is not available')
        self.session,self.server=session,session.server
        self.sink,self.keep_frames=sink,keep_frames
        self.frames,self.frame_count=[],0
        self.active,self.active_thread,self.failed=None,None,None
        self.settings=gameplay_settings(game_settings.load())
        self.header=dict(schema=SCHEMA,engine=engine_fingerprint(),map=session.options.map,
            seed=str(session.run.seed),shell=session.options.player_type,web=getattr(session.options,'web_client',False),
            world_sequence=int(getattr(session.server,'_live_world_sequence',1)),input_serial=session._input_serial,
            teleport_serial=int(getattr(session.bridge,'_teleport_serial',0)),
            settings=copy.deepcopy(self.settings),parameters=snapshot(session))
        self.header['options']={key:getattr(session.options,key) for key in OPTIONS if hasattr(session.options,key)}
        import os,uuid
        if os.environ.get('SB_REPLAY_ATTEMPT'):self.header['attempt']=str(uuid.UUID(os.environ['SB_REPLAY_ATTEMPT']))

    def emit(self,row):
        self.frame_count+=1
        if self.keep_frames:self.frames.append(row)
        if self.sink and not self.failed:self.sink(row)

    def enter(self,session,record=True):
        if self.active is not None:raise ValueError('Overlapping full-run room entry')
        self.session=session
        bridge,channel=session.bridge,session.channel
        while isinstance(bridge,ObservedBridge):bridge=bridge.source
        while isinstance(channel,ObservedChannel):channel=channel.source
        self.source_bridge,self.source_channel=bridge,channel
        session.bridge=ObservedBridge(bridge,self);session.channel=ObservedChannel(channel,self)
        session.current.bridge,session.current.channel=session.bridge,session.channel
        self.original_step=session.step
        if record:
            self.emit(dict(action='enter',wall=time.time(),dt=0,role=session.instance.role,entry=session.run.entry,
                world_sequence=int(getattr(session.server,'_live_world_sequence',1)),calls=[],packets=0,sha256=hashlib.sha256().hexdigest()))

    def observe(self,name,args,value):
        if self.active is None or self.failed or threading.get_ident()!=self.active_thread:return
        try:
            if len(self.active['calls'])>=MAX_CALLS:raise ValueError('Too many run observations')
            clean=call_value(name,value)
            if name=='snapshot' and value[0] is not None:
                clean[0]['entry_ready']=value[0].received_at>=self.session._ready_after
            if name=='consume_interactions':
                for player,row in zip(value,clean):
                    if row is not None:row['entry_ready']=player.received_at>=self.session._ready_after
            self.active['calls'].append(dict(method=name,args=args,value=clean))
        except (ValueError,TypeError,KeyError):self.failed='Unsupported full-run observation'

    def packet(self,kind,payload):
        if self.active is not None and threading.get_ident()==self.active_thread:
            packet_digest(self.digest,kind,payload);self.active['packets']+=1

    def step(self,dt):
        if self.active is not None:raise ValueError('Nested full-run replay frame')
        finite(dt,0,1)
        if self.failed:
            self.release_clock();return self.original_step(dt)
        if self.frame_count>=MAX_FRAMES:
            self.failed='Replay duration limit exceeded';self.release_clock();return self.original_step(dt)
        self.active=dict(action='step',wall=time.time(),dt=dt,calls=[],packets=0)
        self.active_thread=threading.get_ident();self.digest=hashlib.sha256()
        settings=gameplay_settings(game_settings.load())
        if settings!=self.settings:self.active['settings']=settings
        refresh_settings(self.session.current,self.settings,settings);self.settings=settings
        self.session._recorded_wall_time=self.active['wall']
        self.session.current._replay_wall_time=self.active['wall']
        try:
            with game_settings.event_snapshot(settings):result=self.original_step(dt)
            self.active['sha256']=self.digest.hexdigest()
            try:self.emit(self.active)
            except (OSError,ValueError):
                self.failed='Full-run journal could not be saved';self.release_clock()
            return result
        except Exception:
            self.failed='Full-run replay frame did not complete';raise
        finally:self.active=self.active_thread=None

    def document(self):return dict(self.header,frames=copy.deepcopy(self.frames))

    def detach(self):
        self.session.bridge,self.session.channel=self.source_bridge,self.source_channel
        self.session.current.bridge,self.session.current.channel=self.source_bridge,self.source_channel
        self.release_clock()

    def release_clock(self):
        self.session.__dict__.pop('_recorded_wall_time',None)
        ServerCapture._restore_clock(self.session.current)


class RunBridge(RecordedBridge):
    def __init__(self):
        self.__dict__.update({key:value for key,value in vars(PlayerBridge(0)).items()if key!='datagrams'})
        super().__init__()

    def __getattr__(self,name):
        if name not in ('datagrams',*METHODS):
            method=getattr(PlayerBridge,name,None)
            if callable(method):return method.__get__(self,type(self))
        return super().__getattr__(name)

    def read(self,name,args):
        result=super().read(name,args)
        if name=='poll_events':
            for event in result:
                if event.get('kind')=='player_teleport_result':
                    serial=event.get('command_id',0)
                    self._teleport_serial=max(self._teleport_serial,serial)
                    if (self._teleport_command or {}).get('command_id')==serial:self._teleport_command=None
        return result


class RunRecording:
    """One writer and tick boundary shared by every visited room in a battle."""
    def __init__(self,session,recorder):
        from .replay import JournalWriter
        self.recorder=recorder;self.closed=False
        self.capture=RunCapture(session,keep_frames=False)
        self.writer=JournalWriter(recorder.store.directory/(recorder.identifier+'.server.jsonl.gz'),self.capture.header)
        self.capture.sink=self.writer.frame

    def seal(self):
        if self.closed or self.capture.active is not None:return
        if not (self.recorder.closed or self.capture.failed):return
        try:self.writer.close(complete=not self.capture.failed)
        finally:
            self.capture.detach();self.closed=True

    def attach(self,session):
        capture,recorder=self.capture,self.recorder
        original_step,original_stop=session.step,session.stop
        with recorder.boundary:
            if not self.closed:
                try:capture.enter(session)
                except (OSError,ValueError):
                    capture.failed='Full-run room entry could not be saved'
                    self.seal()

        def safely(action,*args):
            try:action(*args)
            except Exception as error:
                recorder.closed=True
                session.log('Battle recording unavailable: '+type(error).__name__)

        def step(dt,*args,**kwargs):
            with recorder.boundary:
                if session._stop.is_set():return
                try:
                    result=(capture.step(dt) if not self.closed and capture.session is session
                            else original_step(dt,*args,**kwargs))
                    if recorder.session is session:safely(recorder.tick,dt)
                    return result
                finally:self.seal()

        def stop(*args,**kwargs):
            result=original_stop(*args,**kwargs)
            with recorder.boundary:
                transition=getattr(session,'_battle_room_transition',False)
                session._battle_room_transition=False
                if not transition and recorder.session is session:safely(recorder.finish)
                self.seal()
            return result
        session.step,session.stop=step,stop


class RunReplay:
    def __init__(self,document):
        from .server import LocalServer,parse_args
        document=validate(document)
        if not compatible_engine(document['engine']):raise ValueError('Replay requires the same simulator engine and map data')
        self.document=document;self.frames=document['frames'];self.index=0;self.failed=False
        self.settings=document['settings'];self.session=None;self.bridge=RunBridge()
        self.bridge._teleport_serial=document['teleport_serial']
        self.workspace=tempfile.TemporaryDirectory(prefix='starbreak-run-replay-')
        directory=Path(self.workspace.name)
        loadouts=json.loads((paths.ROOT/'data/defaults/class_loadouts.json').read_text())
        loadouts[str(document['shell'])]=document['parameters']['loadout']
        loadouts.update(_turret_counts=document['parameters']['turrets'],_turret_missiles=document['parameters']['missiles'])
        (directory/'loadouts.json').write_text(json.dumps(loadouts))
        (directory/'overrides.json').write_text(json.dumps({'_projectile_speeds':document['parameters']['projectile_speeds']}))
        info=game_settings.map_info(document['map'])
        options=parse_args(['--map',document['map'],'--live-seed',document['seed'],'--player-type',str(document['shell']),
            '--pvid',info['pvid'],'--zone',info['zone'],'--gdv',str(info.get('gdv',1)),
            '--class-loadouts',str(directory/'loadouts.json'),'--projectile-overrides',str(directory/'overrides.json')]+
            (['--web-client'] if document['web'] else []))
        options.live=True;options.time_echo=False;options.log_file=None
        for key,value in document['options'].items():setattr(options,key,value)
        self.digest,self.packets=hashlib.sha256(),0
        self.channel=SimpleNamespace(send_frame=self.packet)
        with game_settings.event_snapshot(self.settings):self.server=LocalServer(options)
        self.server._live_world_sequence=document['world_sequence']-1
        serial='_egy_input_serial' if document['map']=='egy_run' else '_level_input_serial'
        setattr(self.server,serial,document['input_serial']-1)

    def packet(self,kind,payload):
        packet_digest(self.digest,kind,payload);self.packets+=1

    def advance(self):
        if self.failed:raise ReplayMismatch('Full-run replay has already diverged')
        if self.index>=len(self.frames):return False
        row=self.frames[self.index];self.digest,self.packets=hashlib.sha256(),0
        self.bridge.begin(row['calls'])
        settings=row.get('settings',self.settings)
        try:
            with game_settings.event_snapshot(settings):
                if row['action']=='enter':
                    from .server import _map_session_class
                    options=self.server.options
                    if self.session is not None and not self.session.suppress_send:
                        raise ReplayMismatch('Room entry without a verified transition')
                    self.server._live_world_sequence+=1
                    if self.server._live_world_sequence!=row['world_sequence']:
                        raise ReplayMismatch('Full-run world sequence changed')
                    self.session=_map_session_class(options.map)(self.server,self.channel,options,bridge=self.bridge)
                    if (self.session.instance.role,self.session.run.entry)!=(row['role'],row['entry']):
                        raise ReplayMismatch('Full-run room route changed')
                    self.server.live_session=self.session
                    self.server._restart_in_progress=False;self.server._restart_bridge=None;self.server._restart_player_type=None
                    self.server._pending_pvids=[];self.server.pending_player_delta=False
                else:
                    refresh_settings(self.session.current,self.settings,settings)
                    self.session._recorded_wall_time=row['wall'];self.session.current._replay_wall_time=row['wall']
                    self.session.step(row['dt'])
            self.bridge.end()
            if self.packets!=row['packets'] or self.digest.hexdigest()!=row['sha256']:
                raise ReplayMismatch('Full-run output diverged at frame '+str(self.index))
        except Exception:self.failed=True;raise
        self.settings=settings;self.index+=1;return True

    def continue_live(self,bridge,channel):
        if self.failed or self.index<2 or self.session is None:
            raise ReplayMismatch('A verified full-run frame is required before continuation')
        session=self.session
        if (session.suppress_send or not session._ready or getattr(session,'_inventory_handoff',None) is not None
                or self.bridge._teleport_command is not None):
            raise ValueError('Wait until room loading and inventory transfer finish before taking over')
        if session.run.completed or not self.server.live_session:
            raise ValueError('This level has already ended')
        bridge.native_context=copy.deepcopy(self.bridge.native_context)
        bridge._teleport_serial=self.bridge._teleport_serial
        session.bridge,session.channel=bridge,channel
        session.current.bridge,session.current.channel=bridge,channel
        session.__dict__.pop('_recorded_wall_time',None)
        for room in session.run.rooms.values():
            if room.runtime is not None:ServerCapture._restore_clock(room.runtime)
        return session

    def close(self):self.workspace.cleanup()
