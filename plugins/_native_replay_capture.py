"""Record checked 1.14.6 native calls without changing their live behavior.

The supplied executable's DWARF types and x86 epilogues were checked for each
entry below. Playback is a separate driver; this recorder does not claim that
a stored journal can already be played by another engine. Python 3.5 syntax.
"""
import base64
import json
import os
import queue
import threading
import time
import copy
import struct
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

try:
    from ._native_replay import NativeTape,MESSAGES
    from ._native_replay_hooks import NativeHook, NativeHookGroup
except (ImportError, ValueError, SystemError):  # PyPy 3.5 top-level relative import
    from _native_replay import NativeTape,MESSAGES
    from _native_replay_hooks import NativeHook, NativeHookGroup

CLIENTS=(
    '29b0d5d1f20862558233a4bc02d89f681211825a3fe3077ba1c7838c50398687',
    'bb446253ec9fbe585040db413d700e92ab9ae938d53893f179386b1f00bac150',
)
ABI=(
    ('TimeUtil::NowMS','int',(),'cdecl','clock'),
    ('randUtil::randomSeed','unsigned int',(),'cdecl','random32'),
    ('randUtil::randomSeed64','unsigned long long',(),'cdecl','random64'),
    ('WorldClient::updateInput','void',('unsigned int','unsigned int'),'thiscall','input'),
    ('WorldClient::doTimePasses','void',(),'thiscall','frame'),
    ('WorldClient::handleServerDelta','void',('int','const char *','int'),'thiscall','delta'),
    ('ServerConnection::doRead','_Bool',(),'thiscall','read'),
    ('ClientWorld::addPlayerInput','_Bool',('void *',),'thiscall','player-input'),
    ('ClientWorld::internalUpdate','void',('int',),'thiscall','engine-frame'),
    ('MessageHandlers::run','void',('int','const char *','int'),'thiscall','message'),
    ('ServerConnection::sendMessage','void',('int',),'thiscall','ack'),
    ('GameClient::handleKeyboardEvent','void',('int','int'),'thiscall','keyboard'),
)


def read_control(response,limit=272*1024*1024):
    # Embedded PyPy's HTTP read(n) reserves n bytes even for a tiny reply.
    # A 272 MB reservation on each idle poll can exhaust the 32-bit client.
    chunks=[];size=0
    while True:
        chunk=response.read(min(64*1024,limit+1-size))
        if not chunk:break
        size+=len(chunk)
        if size>limit:raise ValueError('Native playback document is too large')
        chunks.append(chunk)
    return b''.join(chunks)


class ChunkDelivery:
    """Bounded FIFO on a worker thread; the game never waits for HTTP."""
    def __init__(self, config, control=True):
        endpoint=urlsplit(config['url'])
        if endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or endpoint.path!='/chunks':
            raise ValueError('Native recorder must use the owned loopback endpoint')
        token=config['token']
        if not isinstance(token,str) or len(token)!=64 or any(c not in '0123456789abcdef' for c in token):
            raise ValueError('Invalid native recorder token')
        self.url=config['url'];self.token=token;self.error=None;self.closed=False
        self.serial=0;self.incoming=queue.Queue(maxsize=4);self.control_error=None
        self.feedback_value=None;self.feedback_lock=threading.Lock();self.last_poll=0
        self.poll_interval=.1
        self.control_enabled=control
        self.queue=queue.Queue(maxsize=4)
        self.thread=threading.Thread(target=self.run);self.thread.daemon=True;self.thread.start()

    def put(self, value):
        if self.closed or self.error:raise IOError('Native recording delivery stopped')
        raw=json.dumps(value,separators=(',',':')).encode('utf8')
        if len(raw)>512*1024:raise ValueError('Native recording chunk is too large')
        try:self.queue.put_nowait(raw)
        except queue.Full:raise IOError('Native recording delivery queue is full')

    def run(self):
        while True:
            try:raw=self.queue.get(timeout=.01)
            except queue.Empty:raw=False
            try:
                if raw is None:return
                if raw is not False and not self.error:
                    request=Request(self.url,data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
                    for attempt in range(2):
                        try:
                            with urlopen(request,timeout=3) as response:response.read(4096)
                            break
                        except OSError:
                            if attempt:raise
            except Exception as error:self.error=type(error).__name__
            finally:
                if raw is not False:self.queue.task_done()
            try:
                if not self.control_enabled:continue
                with self.feedback_lock:
                    feedback=self.feedback_value;self.feedback_value=None
                if feedback is not None:
                    request=Request(self.url[:-len('/chunks')]+'/feedback',data=json.dumps(feedback,separators=(',',':')).encode('utf8'),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
                    with urlopen(request,timeout=3) as response:response.read(4096)
                if time.monotonic()-self.last_poll>=self.poll_interval and not self.incoming.full():
                    self.last_poll=time.monotonic()
                    request=Request(self.url[:-len('/chunks')]+'/control?serial='+str(self.serial),headers={'Authorization':'Bearer '+self.token})
                    with urlopen(request,timeout=3) as response:
                        raw=read_control(response)
                    value=json.loads(raw.decode('utf8'))
                    if value.get('play'):self.serial=value['serial']
                    self.incoming.put_nowait(value)
            except Exception as error:self.control_error=type(error).__name__

    def feedback(self,value):
        value=copy.deepcopy(value)
        self.poll_interval=.016 if value.get('committed') or value.get('takeover') else .1
        with self.feedback_lock:
            old=self.feedback_value
            if old and old.get('serial')==value.get('serial'):
                if old.get('telemetry') and not value.get('telemetry'):
                    value['telemetry']=old['telemetry'];old=dict(old,telemetry=None)
                before=(old.get('telemetry')or{}).get('events',[])
                if before:
                    try:
                        current=value.setdefault('telemetry',{}).setdefault('events',[])
                        if len(before)+len(current)>4096:raise ValueError('Native feedback queue is full')
                        value['telemetry']['events']=before+current
                    except Exception as error:self.control_error=type(error).__name__;return
                value['acks']=old.get('acks',[])+value.get('acks',[])
                value['documents']=old.get('documents',[])+value.get('documents',[])
                if len(json.dumps(value['documents'],separators=(',',':')).encode('ascii'))>256*1024:
                    raise ValueError('Native room feedback queue is full')
            self.feedback_value=value

    def close(self):
        self.closed=True
        # Called at shutdown, never from an update hook. A failed sender cannot
        # hold the game open indefinitely or silently drop a completed chunk.
        try:self.queue.put(None,timeout=3)
        except queue.Full:return
        self.thread.join(timeout=8)


class NativeCapture:
    def __init__(self,ffi,lib,refs,manifest,save,base,hook=NativeHook,notice=None):
        if manifest.get('client') not in CLIENTS or manifest.get('pointer_bytes')!=4:
            raise ValueError('Native recorder does not support this executable')
        self.ffi,self.refs=ffi,refs;self.frames=0;self.owner=threading.get_ident();self.engine_elapsed=0
        self.player=None;self.driving=False;self.clock_offset=0;self.functions={};self.keyboard_depth=0
        self.serial=0;self.branch=None;self.packet_buffer=bytearray();self.control_error=None
        self.descriptions=set()
        self.last_ack=None;self.bridge_documents=[]
        self.resource_depth=0;self.pending_assets=0
        self.tape=NativeTape(save=save,notice=notice)
        hooks=[]
        for name,result,arguments,convention,kind in ABI:
            row=manifest['hooks'][name]
            hooks.append(hook(ffi,lib,base+row['rva'],bytes.fromhex(row['signature']),
                result=result,arguments=arguments,convention=convention,replace=self.replacement(kind)))
            self.functions[kind]=hooks[-1]
        self.hooks=NativeHookGroup(hooks)

    def replacement(self,kind):
        def observe(original,*args):
            player=self.player
            if kind=='keyboard':
                self.keyboard_depth+=1
                try:return original(*args)
                finally:self.keyboard_depth-=1
            if kind=='ack':
                message=int(args[1])
                if message==22:self.pending_assets=max(0,self.pending_assets-1)
                self.resource_depth+=1
                try:result=original(*args)
                finally:self.resource_depth-=1
                if player is not None and message in(11,22):player.ack(message)
                return result
            if self.resource_depth:return original(*args)
            if player is not None and kind=='read' and not self.driving:return True
            if player is not None and player.blocked() and not self.driving and kind not in ('clock','random32','random64'):
                if kind=='input' and self.keyboard_depth:player.keyboard_input(int(args[1]),bool(args[2]))
                return True if kind=='read' else 0 if kind in ('clock','random32','random64','player-input') else None
            # The client's IO worker may run while the UI thread is updating.
            # Never mix its clocks/call stack into the simulation journal.
            if threading.get_ident()!=self.owner or not self.tape.in_loop or self.tape.error:return original(*args)
            if kind in ('clock','random32','random64'):
                def scalar():
                    value=original(*args)
                    return ((int(value)+self.clock_offset+2**31)%2**32-2**31) if kind=='clock' else value
                return self.tape.value(kind,scalar)
            if kind=='message':
                message=int(args[1])
                if message not in MESSAGES:return original(*args)
                size=int(args[3])
                if not 0<size<=262144:raise ValueError('Invalid native message size')
                payload=bytes(self.ffi.buffer(args[2],size))
                def receive():
                    if self.tape.mode=='play' and message in (16,17):
                        if (message,payload) not in self.descriptions:
                            raise ValueError('Native replay requires matching item and character descriptions')
                        # Retail libraries reject duplicate descriptions. The
                        # exact bytes are already installed by this client's
                        # original login; replay the boundary without adding
                        # the same global catalogue entry a second time.
                        return None
                    if message!=21:return original(*args)
                    self.pending_assets+=1;self.resource_depth+=1
                    try:return original(*args)
                    finally:self.resource_depth-=1
                result=self.tape.call(['message',message,base64.b64encode(payload).decode('ascii')],receive)
                if message in (16,17):self.descriptions.add((message,payload))
                if message==10:
                    self.sync_refs()
                    if player is not None:player.world_created=True
                return result
            if kind=='input':
                return self.tape.call(['input',int(args[1]),int(bool(args[2]))],lambda:original(*args))
            if kind=='read':return self.tape.call(['read'],lambda:original(*args))
            if kind=='player-input':
                value=self.ffi.cast('struct PlayerInput *',args[1])
                row=['player-input',int(value.total),int(value.hit),int(value.unhit),int(value._has_bits[0])]
                return self.tape.call(row,lambda:original(*args))
            if kind=='engine-frame':
                row=['engine-frame',int(args[1])]
                result=self.tape.call(row,lambda:original(*args))
                self.engine_elapsed+=row[1]
                world=self.ffi.cast('struct ClientWorld *',args[0])
                check=['engine-state',self.world_state(world)]
                if self.tape.mode=='record':self.tape.emit(check)
                elif self.tape.take('engine-state')!=check:raise ValueError('Native physics state changed')
                return result
            if kind=='delta':
                message,size=int(args[1]),int(args[3])
                if message not in (12,37,38):return original(*args)
                if not 0<size<=262144:raise ValueError('Invalid native world delta size')
                payload=bytes(self.ffi.buffer(args[2],size))
                row=['delta',message,base64.b64encode(payload).decode('ascii')]
                return self.tape.call(row,lambda:original(*args))
            before=self.engine_elapsed
            def advance():
                result=original(*args)
                # ClientWorld.total is an input bitmask, not a time counter.
                # Only completed internalUpdate(dt) calls advance game time.
                self.tape.value('step',lambda:self.engine_elapsed-before)
                return result
            return self.tape.call(['frame'],advance)
        def checked(original,*args):
            try:return observe(original,*args)
            except Exception as error:
                self.tape.fail(error)
                if self.player is not None and self.player.blocked():
                    return True if kind=='read' else 0 if kind in ('clock','random32','random64','player-input') else None
                raise
        return checked

    def state(self):
        game=getattr(self.refs,'GameClient',self.ffi.NULL)
        client=game.worldClient if game is not None and game!=self.ffi.NULL else self.refs.WorldClient
        if self.player is not None and not self.player.world_created:return [0,0]
        if client==self.ffi.NULL or client.clientWorld==self.ffi.NULL:return [0,0]
        world=client.clientWorld
        return self.world_state(world)

    def sync_refs(self):
        # A replay can create several worlds before SBPE's next draw callback.
        # Do not let the bridge dereference references to the deleted world.
        game=getattr(self.refs,'GameClient',self.ffi.NULL)
        if game is None or game==self.ffi.NULL:return
        client=game.worldClient;self.refs.WorldClient=client
        if client==self.ffi.NULL:
            self.refs.ClientWorld=self.refs.WorldView=self.ffi.NULL
        else:
            self.refs.ClientWorld=client.clientWorld
            self.refs.WorldView=getattr(client,'worldView',self.ffi.NULL)

    def world_state(self,world):
        seed,total=int(world.asWorld.props.seed),int(world.total)
        if world.player==self.ffi.NULL or world.mySubWorld==self.ffi.NULL:return [seed,total]
        obj=world.player.asClientObject.asNativeObject.asWorldObject
        props=obj.props;character=self.ffi.cast('struct PlayerCharacter *',world.player)
        return [seed,total,int(obj.objId),int(world.mySubWorld.asNativeSubWorld.asSubWorldImpl.asSubWorld.worldId),
            int(props.xmp),int(props.ymp),int(props.wmp),int(props.hmp),int(props.hitpoints),int(props.maxhitpoints),
            int(props.facing),int(character.ammo),int(character.total),int(props.hitpoints>0)]

    def poll(self,post_frame=lambda:None):
        delivery=getattr(self,'delivery',None)
        if delivery is None:return
        while True:
            try:value=delivery.incoming.get_nowait()
            except queue.Empty:break
            if value.get('serial',0)<self.serial:continue
            if value.get('play'):
                if self.tape.mode=='record':self.tape.finish()
                try:from ._native_replay_player import NativePlayer
                except (ImportError,ValueError,SystemError):from _native_replay_player import NativePlayer
                request=value['play'];self.serial=request['serial'];self.branch=None
                try:self.player=NativePlayer(self,request['document'],post_frame=post_frame)
                except Exception as error:
                    delivery.feedback(dict(serial=self.serial,status=dict(state='failed',error=type(error).__name__)))
                    raise
                self.packet_buffer=bytearray();self.control_error=None
            if self.player is None:continue
            if value.get('ack') is not None:self.last_ack=value['ack']
            self.bridge_documents.extend(value.get('documents',[]))
            if len(self.bridge_documents)>64:raise ValueError('Native room control queue is full')
            if value.get('error'):
                self.control_error=value['error'];self.player.requested=False;self.player.paused=True
            for control in value.get('controls',[]):
                try:self.player.control(control)
                except (ValueError,TypeError) as error:self.control_error=str(error)
            if value.get('packets'):self.packet_buffer.extend(base64.b64decode(value['packets'],validate=True))
            if len(self.packet_buffer)>16*1024*1024:raise ValueError('Native playback output is too large')
            branch=value.get('branch')
            if branch and self.branch is None:
                self.player.commit(lambda chunk:delivery.put(dict(chunk,attempt=branch['id'])),branch['sequence'])
                self.branch=branch;self.reset_inputs=True

    def before(self):
        if self.player is None:self.tape.loop(0);return
        if self.player.phase!='live':
            if not self.player.requested:self.player.update()
            return
        self.tape.loop(0)
        if getattr(self,'reset_inputs',False):
            total=self.state()[1]
            for bit in range(14):
                if total&(1<<bit):self.invoke(['input',1<<bit,0])
            self.reset_inputs=False
        while len(self.packet_buffer)>=5:
            kind=self.packet_buffer[0];size=struct.unpack('>i',self.packet_buffer[1:5])[0]
            if not 0<=size<=262144:raise ValueError('Invalid native replay output frame')
            if len(self.packet_buffer)<size+5:break
            data=bytes(self.packet_buffer[5:5+size]);del self.packet_buffer[:5+size]
            self.invoke(['message',kind,base64.b64encode(data).decode('ascii')])
        for mask,pressed in self.player.pending_keys:self.invoke(['input',mask,pressed])
        self.player.pending_keys=[]

    def after(self):
        if self.player is None or self.player.phase=='live':
            self.tape.loop(1,self.state());self.frames+=1
            if self.frames%30==0:self.tape.flush()
        self.feedback()

    def feedback(self,telemetry=None,documents=None):
        if self.player is None or not hasattr(self,'delivery'):return
        player=self.player;status=player.status()
        if player.requested and player.phase!='live':status['state']='taking-over'
        if self.control_error:status['message']=self.control_error
        value=dict(serial=self.serial,status=status,telemetry=telemetry,documents=documents or [],acks=player.acks)
        player.acks=[]
        if player.can_takeover():value['checkpoint']=player.checkpoint()
        if player.requested and player.phase!='live':value['takeover']=True
        if self.branch:value['committed']=self.branch['id']
        self.delivery.feedback(value)

    def divert_feedback(self,payload):
        if self.player is None:return False
        if self.player.phase=='live':
            if any(field in payload for field in ('egress','egress_ready','ground_restore')):
                self.feedback(documents=[payload])
            else:self.feedback(payload)
        return True

    def close(self):
        self.tape.finish();self.hooks.remove()

    def invoke(self,row):
        """Use only checked functions and current game-owned objects."""
        kind=row[0];ffi=self.ffi;game=self.refs.GameClient
        if game is None or game==ffi.NULL:raise ValueError('Native game is not connected')
        client=game.worldClient
        if kind=='message':
            data=base64.b64decode(row[2],validate=True);buffer=ffi.new('char[]',data)
            args=(ffi.addressof(game.serverConnection,'messageHandlers'),row[1],buffer,len(data))
        elif kind=='ack':args=(game.serverConnection,row[1])
        elif kind=='read':
            return self.tape.call(row,lambda:self.player.read_body())
        else:
            if client==ffi.NULL:raise ValueError('Native world is not ready')
            if kind=='input':args=(client,row[1],row[2])
            elif kind=='frame':args=(client,)
            elif kind=='engine-frame':args=(client.clientWorld,row[1])
            elif kind=='delta':
                data=base64.b64decode(row[2],validate=True);buffer=ffi.new('char[]',data)
                args=(client,row[1],buffer,len(data))
            elif kind=='player-input':
                value=ffi.new('struct PlayerInput *');value.total,value.hit,value.unhit=row[1:4];value._has_bits[0]=row[4]
                args=(client.clientWorld,value)
            else:raise ValueError('Unsupported native driver call')
        hook=self.functions[kind]
        if kind=='message' and row[1]==21 and self.player is not None and getattr(self.player,'preloading',False):
            self.pending_assets+=1;self.resource_depth+=1
            try:return hook.original(*args)
            finally:self.resource_depth-=1
        return self.replacement(kind)(hook.original,*args)


def create(ffi,lib,refs,environ=None):
    env=os.environ if environ is None else environ
    if env.get('HARDLAB_LIVE_SESSION')!='1' or not env.get('HARDLAB_NATIVE_REPLAY'):return None
    config=json.loads(env['HARDLAB_NATIVE_REPLAY'])
    global _ACTIVE_CAPTURE
    if _ACTIVE_CAPTURE is not None:
        if _ACTIVE_CAPTURE.delivery.token!=config['token']:raise ValueError('Another native recording is still attached')
        return _ACTIVE_CAPTURE
    import ctypes
    kernel=ctypes.windll.kernel32
    kernel.GetModuleHandleW.argtypes=[ctypes.c_wchar_p];kernel.GetModuleHandleW.restype=ctypes.c_void_p
    base=kernel.GetModuleHandleW(None)
    if not base:raise ValueError('Native client module is unavailable')
    delivery=ChunkDelivery(config,control=False)
    try:
        capture=NativeCapture(ffi,lib,refs,config['manifest'],delivery.put,int(base))
        capture.hooks.install();capture.delivery=delivery
        delivery.control_enabled=True
        _ACTIVE_CAPTURE=capture
        return capture
    except Exception:
        delivery.close();raise


_ACTIVE_CAPTURE=None
