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
from urllib.request import Request, build_opener, ProxyHandler
from urllib.parse import urlsplit

try:
    from ._native_replay import NativeTape,MESSAGES
    from ._native_replay_hooks import NativeHook, NativeHookGroup
except (ImportError, ValueError, SystemError):  # PyPy 3.5 top-level relative import
    from _native_replay import NativeTape,MESSAGES
    from _native_replay_hooks import NativeHook, NativeHookGroup

def urlopen(request,timeout=3):
    return build_opener(ProxyHandler({})).open(request,timeout=timeout)


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
    ('WorldClient::handleModifyInv','void',('void *',),'thiscall','inventory'),
    ('ModifyInv::ModifyInv','void',(),'thiscall','inventory-ctor'),
    ('ModifyInv::~ModifyInv','void',(),'thiscall','inventory-dtor'),
    # ObjKey and the two doubles are forwarded as their original stack words.
    ('SoundMixer::play','void',('const char *','int','unsigned int','unsigned int',
        'unsigned int','unsigned int','unsigned int','unsigned int'),'thiscall','sound'),
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
        self.url=config['url'];self.token=token;self.error=None;self.closed=False;self.attempt=config.get('attempt')
        self.serial=0;self.incoming=queue.Queue(maxsize=4);self.control_error=None
        self.feedback_value=None;self.feedback_lock=threading.Lock();self.last_poll=0
        self.poll_interval=.1
        self.control_enabled=control
        self.queue=queue.Queue(maxsize=256)
        self.queued_bytes=0;self.queue_lock=threading.Lock()
        self.pending_feedback=None;self.failed_attempts=set()
        self.loading_serial=None;self.progress_at=0
        self.thread=threading.Thread(target=self.run);self.thread.daemon=True;self.thread.start()
        self.control_thread=threading.Thread(target=self.control_run);self.control_thread.daemon=True;self.control_thread.start()

    def put(self, value):
        if self.closed or self.error:raise IOError('Native recording delivery stopped')
        # NativeTape transfers a detached immutable chunk. Encoding belongs to
        # this delivery worker, never the original game's update thread.
        if self.attempt and 'attempt' not in value:value=dict(value,attempt=self.attempt)
        try:from ._native_replay import wire_size
        except (ImportError,ValueError,SystemError):from _native_replay import wire_size
        size=sum(wire_size(row)+1 for row in value.get('ops',[]))+256
        with self.queue_lock:
            if self.queued_bytes+size>8*1024*1024:raise IOError('Native recording delivery buffer is full')
            self.queued_bytes+=size
        try:self.queue.put_nowait((value,size))
        except queue.Full:
            with self.queue_lock:self.queued_bytes-=size
            raise IOError('Native recording delivery queue is full')

    def run(self):
        while True:
            try:entry=self.queue.get(timeout=.1)
            except queue.Empty:continue
            value,size=(None,0) if entry is None else entry
            try:
                if value is None:return
                if value.get('attempt') in self.failed_attempts:continue
                raw=json.dumps(value,separators=(',',':')).encode('utf8')
                if len(raw)>512*1024:raise ValueError('Native recording chunk is too large')
                request=Request(self.url,data=raw,headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
                for attempt in range(5):
                    try:
                        with urlopen(request,timeout=5) as response:response.read(4096)
                        break
                    except OSError:
                        if attempt==4:raise
                        time.sleep(.05*(attempt+1))
            except Exception as error:
                self.failed_attempts.add(value.get('attempt'))
                if value.get('attempt')==self.attempt:self.error=type(error).__name__;self.error_detail=str(error)[:400]
                import logging
                logging.getLogger('hardlab_live').exception('Native replay delivery failed')
            finally:
                with self.queue_lock:self.queued_bytes-=size
                self.queue.task_done()

    def poll_control(self):
        if not self.control_enabled:return
        if self.pending_feedback is None:
            with self.feedback_lock:
                self.pending_feedback=self.feedback_value;self.feedback_value=None
        if self.pending_feedback is not None:
            request=Request(self.url[:-len('/chunks')]+'/feedback',data=json.dumps(self.pending_feedback,separators=(',',':')).encode('utf8'),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
            with urlopen(request,timeout=3) as response:response.read(4096)
            self.pending_feedback=None
        if time.monotonic()-self.last_poll>=self.poll_interval and not self.incoming.full():
            self.last_poll=time.monotonic()
            request=Request(self.url[:-len('/chunks')]+'/control?serial='+str(self.serial),headers={'Authorization':'Bearer '+self.token})
            with urlopen(request,timeout=3) as response:raw=read_control(response,1024*1024)
            value=json.loads(raw.decode('utf8'))
            if value.get('play'):
                request=value['play'];self.loading_serial=request['serial']
                try:
                    request['document']=self.load_replay(request)
                except Exception as error:
                    # A failed load is terminal for this selection. Retrying
                    # an oversized allocation forever freezes the UI at 0:00.
                    self.serial=request['serial']
                    # Keep the terminal report pending until acknowledged,
                    # even if the connection that failed is still down.
                    self.pending_feedback=dict(serial=self.serial,status=dict(state='failed',stage='download',
                        error=type(error).__name__,message=str(error)[:400] or type(error).__name__))
                    self.loading_serial=None
                    return
                self.loading_serial=None
            if value.get('play') or value.get('resume'):self.serial=value['serial']
            self.incoming.put_nowait(value)
        self.control_error=None

    def report_load(self,stage,loaded,total,error=None):
        self.loading_status=(stage,loaded,total)
        now=time.monotonic()
        if error is None and loaded!=total and now-self.progress_at<.15:return
        self.progress_at=now
        status=dict(state='failed' if error else 'starting',stage=stage,loaded=loaded,total=total,
                    progress=loaded/float(total) if total else 0)
        if error:status.update(error=type(error).__name__,message=str(error)[:400] or type(error).__name__)
        request=Request(self.url[:-len('/chunks')]+'/feedback',
            data=json.dumps(dict(serial=self.loading_serial,status=status),separators=(',',':')).encode('utf8'),
            headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with urlopen(request,timeout=3) as response:response.read(4096)

    def load_replay(self,request):
        try:
            from ._native_replay import OperationValidator,MAX_OPERATIONS
            from ._native_replay_player import PreparedReplay
        except (ImportError,ValueError,SystemError):
            from _native_replay import OperationValidator,MAX_OPERATIONS
            from _native_replay_player import PreparedReplay
        total=request.get('operations')
        if request.get('transport')!='pages-v1' or type(total)is not int or not 0<total<=MAX_OPERATIONS:
            raise ValueError('Unsupported native replay transfer')
        validator=OperationValidator();offset=0
        while offset<total:
            if self.closed:raise IOError('Native replay transfer closed')
            url=self.url[:-len('/chunks')]+'/replay?serial='+str(request['serial'])+'&offset='+str(offset)
            page_request=Request(url,headers={'Authorization':'Bearer '+self.token})
            with urlopen(page_request,timeout=3) as response:raw=read_control(response,512*1024)
            page=json.loads(raw.decode('utf8'));del raw
            if page.get('serial')!=request['serial'] or page.get('offset')!=offset or page.get('total')!=total:
                raise ValueError('Native replay page changed')
            rows=page.get('ops')
            if not isinstance(rows,list) or not rows or len(rows)>1024 or offset+len(rows)>total:
                raise ValueError('Invalid native replay page')
            validator.append(rows);offset+=len(rows);del page,rows
            self.report_load('download',offset,total)
            time.sleep(.001)
        document=dict(request['document'],ops=validator.finish())
        return PreparedReplay(document,lambda loaded,count:self.report_load('indexing',loaded,count))

    def control_run(self):
        while not self.closed:
            try:self.poll_control()
            except Exception as error:
                self.control_error=type(error).__name__+': '+str(error)[:200]
            time.sleep(.01 if self.control_error is None else .1)

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
        self.control_thread.join(timeout=4)


class NativeCapture:
    def __init__(self,ffi,lib,refs,manifest,save,base,hook=NativeHook,notice=None):
        if manifest.get('client') not in CLIENTS or manifest.get('pointer_bytes')!=4:
            raise ValueError('Native recorder does not support this executable')
        self.ffi,self.refs=ffi,refs;self.frames=0;self.owner=threading.get_ident();self.engine_elapsed=0
        self.player=None;self.driving=False;self.clock_offset=0;self.functions={};self.keyboard_depth=0
        self.serial=0;self.branch=None;self.packet_buffer=bytearray();self.control_error=None
        self.live_call_depth=0;self.draining=False
        self.descriptions=set()
        self.recording=True
        self.input_observer=None
        self.last_ack=None;self.bridge_documents=[]
        self.resource_depth=0;self.pending_assets=0
        self.tape=NativeTape(save=save,notice=notice)
        self.sound_mixer=None
        self.sound_clock_caller=base+manifest['sound_clock']['rva'] if manifest.get('sound_clock') else None
        self.playback_clock=0
        if manifest.get('sound_mixer') and hook is NativeHook:
            if ffi.sizeof('struct SoundMixer')!=12 or ffi.sizeof('struct SoundMixer__ChannelStatus')!=32:
                raise ValueError('Native sound mixer layout changed')
            self.sound_mixer=ffi.cast('struct SoundMixer *',base+manifest['sound_mixer']['rva'])
        hooks=[]
        self.shared_hook_owners=[]
        for name,result,arguments,convention,kind in ABI:
            row=manifest['hooks'][name]
            target,signature=base+row['rva'],bytes.fromhex(row['signature'])
            if hook is NativeHook:
                try:from ._native_replay_hooks import shared_hook_target
                except (ImportError,ValueError,SystemError):from _native_replay_hooks import shared_hook_target
                target,signature,owner=shared_hook_target(ffi,lib,refs,name,target,signature)
                if owner is not None:self.shared_hook_owners.append(owner)
            hooks.append(hook(ffi,lib,target,signature,
                result=result,arguments=arguments,convention=convention,replace=self.replacement(kind),
                **({'capture_caller':True} if kind=='clock' and hook is NativeHook else {})))
            self.functions[kind]=hooks[-1]
        self.hooks=NativeHookGroup(hooks)

    def replacement(self,kind):
        def observe(original,*args):
            if kind in ('frame','engine-frame','message','delta','input','player-input','inventory'):
                native_original=original
                def original(*forwarded):
                    self.live_call_depth+=1
                    try:return native_original(*forwarded)
                    finally:self.live_call_depth-=1
            if kind in ('inventory-ctor','inventory-dtor'):return original(*args)
            player=self.player
            if self.draining and kind=='message' and int(args[1]) in MESSAGES:return None
            if kind=='sound':
                cursor=self.tape.cursor
                result=original(*args)
                # Legacy tapes included the camera-dependent SoundMixer clock.
                # A culled sound reaches this verified boundary but never calls
                # NowMS. Retire its scalar only at the enclosing call's return.
                if (player is not None and player.legacy_sound_clocks and self.driving
                        and self.sound_clock_caller and self.tape.cursor==cursor
                        and self.tape.stack and cursor+1<len(self.tape.ops)
                        and self.tape.ops[cursor][0]=='clock'
                        and self.tape.ops[cursor+1]==['return',self.tape.stack[-1]]):
                    self.tape.take('clock')
                return result
            if kind=='keyboard':
                mapping=getattr(self,'keyboard_mask',None)
                if player is not None and player.blocked() and mapping is not None:
                    mask=mapping(int(args[2]));pressed=bool(args[1])
                    if mask and (player.requested or pressed and player.accepts_keyboard()):
                        player.keyboard_input(mask,pressed);return None
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
            if player is None and self.recording and getattr(self,'awaiting_world',False):
                if kind=='message' and int(args[1]) in (21,10):
                    self.awaiting_world=False;self.tape.loop(0)
                else:return original(*args)
            if player is None and getattr(self,'awaiting_created',False):
                if kind=='message' and int(args[1])==10:self.awaiting_created=False
                elif kind not in ('read','message','clock','random32','random64'):return original(*args)
            if player is not None and kind=='read' and not self.driving:
                # Keep the original connection's cipher/socket drained, but
                # never apply its old practice packets to the replay world.
                self.draining=True
                try:original(*args)
                finally:self.draining=False
                return True
            if player is not None and player.blocked() and not self.driving and kind not in ('clock','random32','random64'):
                if kind=='input' and self.keyboard_depth:player.keyboard_input(int(args[1]),bool(args[2]))
                return True if kind=='read' else 0 if kind in ('clock','random32','random64','player-input') else None
            if (kind=='input' and self.input_observer is not None and threading.get_ident()==self.owner
                    and (player is None or player.phase=='live')):
                self.input_observer(int(args[1]),bool(args[2]),self.keyboard_depth>0)
            if player is None and not self.recording:
                # Keep bootstrap descriptions for playback, but no per-frame
                # clocks, input, world checks or journal writes in live play.
                if kind=='message' and threading.get_ident()==self.owner and int(args[1]) in (16,17):
                    size=int(args[3])
                    if not 0<size<=262144:raise ValueError('Invalid native message size')
                    self.descriptions.add((int(args[1]),bytes(self.ffi.buffer(args[2],size))))
                return original(*args)
            if (threading.get_ident()==self.owner and not self.tape.in_loop and not self.tape.finished
                    and not self.tape.error and kind in ('input','inventory')
                    and not getattr(self,'awaiting_world',False)):
                # SBPE macros also change equipment/input after our draw
                # callback. Preserve those real calls as their own complete
                # iteration instead of silently omitting their side effects.
                self.tape.loop(0)
                try:return observe(original,*args)
                finally:self.after()
            # The client's IO worker may run while the UI thread is updating.
            # Never mix its clocks/call stack into the simulation journal.
            if threading.get_ident()!=self.owner or not self.tape.in_loop or self.tape.error:
                result=original(*args)
                if player is not None and player.phase=='live' and threading.get_ident()==self.owner:
                    if kind=='clock' and self.live_call_depth:
                        return (int(result)+self.clock_offset+2**31)%2**32-2**31
                    if kind=='engine-frame':self.engine_elapsed+=int(args[1])
                return result
            if kind in ('clock','random32','random64'):
                def scalar():
                    value=original(*args)
                    return ((int(value)+self.clock_offset+2**31)%2**32-2**31) if kind=='clock' else value
                if kind=='clock' and self.sound_clock_caller and self.functions[kind].caller==self.sound_clock_caller:
                    if self.tape.mode!='play':return scalar()
                    if (player.legacy_sound_clocks and self.tape.cursor<len(self.tape.ops)
                            and self.tape.ops[self.tape.cursor][0]=='clock'):
                        return self.tape.value('clock',scalar)
                    return self.playback_clock
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
            if kind=='inventory':
                value=self.ffi.cast('struct ModifyInv *',args[1])
                row=['inventory',int(value.command),int(value.slotpos),int(value.targetslotpos),int(value._has_bits[0])]
                return self.tape.call(row,lambda:original(*args))
            if kind=='engine-frame':
                row=['engine-frame',int(args[1])]
                def update():
                    result=original(*args)
                    # Older adapters recorded the camera-dependent audio
                    # clocks at the end of internalUpdate. Retire only that
                    # legacy scalar tail; never skip calls, RNG or physics.
                    if self.tape.mode=='play' and player.legacy_sound_clocks and self.sound_clock_caller:
                        while self.tape.cursor<len(self.tape.ops) and self.tape.ops[self.tape.cursor][0]=='clock':
                            self.tape.take('clock')
                    return result
                result=self.tape.call(row,update)
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
            if value.get('resume'):
                resume=value['resume']
                if self.player is not None:
                    game=self.refs.GameClient
                    if game is not None and game!=self.ffi.NULL:
                        self.draining=True
                        try:self.functions['read'].original(game.serverConnection)
                        finally:self.draining=False
                if (self.recording or self.player is not None) and not self.tape.finished:self.tape.finish()
                self.player=None;self.branch=None;self.serial=resume['serial'];self.recording=resume['recording']
                self.delivery.attempt=resume['id'];self.tape=NativeTape(save=self.delivery.put)
                self.delivery.error=None;self.delivery.error_detail=None
                self.packet_buffer=bytearray();self.frames=self.engine_elapsed=0;self.clock_offset=0
                self.sync_audio_clock(int(self.functions['clock'].original()))
                self.awaiting_world=True;self.awaiting_created=True;self.control_error=None
                callback=getattr(self,'replay_start',None)
                if callback:callback()
                self.delivery.feedback(dict(serial=self.serial,status=dict(state='idle'),resume=resume['id']))
                continue
            if value.get('play'):
                if self.tape.mode=='record' and (self.recording or self.player is not None):self.tape.finish()
                try:from ._native_replay_player import NativePlayer
                except (ImportError,ValueError,SystemError):from _native_replay_player import NativePlayer
                request=value['play'];self.serial=request['serial'];self.branch=None
                try:
                    callback=getattr(self,'replay_start',None)
                    if callback:callback()
                    self.player=NativePlayer(self,request['document'],post_frame=post_frame)
                except Exception as error:
                    delivery.feedback(dict(serial=self.serial,status=dict(state='failed',error=type(error).__name__,message=str(error)[:400])))
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
                self.player.commit(lambda chunk:delivery.put(dict(chunk,attempt=branch['id'])),branch['sequence'],branch.get('size'))
                self.branch=branch;self.reset_inputs=True

    def sync_audio_clock(self,logical):
        """Rebase presentation-only channel ages after a replay time jump.

        SoundMixer persists across worlds. Future lastPlayMS values otherwise
        make its original getBestChannel reject every positional sound.
        """
        self.playback_clock=logical
        mixer=self.sound_mixer
        if mixer is None:return
        ffi=self.ffi
        def rows(vector,kind,size,limit):
            start=int(ffi.cast('uintptr_t',vector.start));end=int(ffi.cast('uintptr_t',vector.finish))
            if not start and not end:return ()
            if not start or end<start or (end-start)%size or (end-start)//size>limit:
                raise ValueError('Invalid native sound channel vector')
            pointer=ffi.cast(kind+' *',vector.start)
            return (pointer[index] for index in range((end-start)//size))
        for vector in rows(mixer.channels,'struct STDVector',12,32):
            for channel in rows(vector,'struct SoundMixer__ChannelStatus',32,128):
                if ((int(logical)-int(channel.lastPlayMS)+2**31)%2**32-2**31)<0:
                    channel.lastPlayMS=(int(logical)-1+2**31)%2**32-2**31

    def before(self):
        if self.player is None:
            if self.recording and not getattr(self,'awaiting_world',False):self.tape.loop(0)
            return
        if self.player.phase!='live':
            if not self.player.requested:self.player.update()
            return
        self.sync_audio_clock(int(self.functions['clock'].original())+self.clock_offset)
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
        if self.player.pending_keys:
            pressed_masks=set();deferred=[]
            for mask,pressed in self.player.pending_keys:
                if pressed:
                    self.invoke(['input',mask,1]);pressed_masks.add(mask)
                    if mask in deferred:deferred.remove(mask)
                elif mask in pressed_masks:
                    if mask not in deferred:deferred.append(mask)
                else:self.invoke(['input',mask,0])
            # A tap released while handoff was preparing must reach one real
            # physics step; draining both edges before it loses the action.
            self.player.pending_keyups=deferred;self.player.keyup_after=self.engine_elapsed
            self.player.pending_keys=[]

    def after(self):
        if (self.player is not None and self.player.phase=='live' and self.player.pending_keyups
                and self.engine_elapsed>self.player.keyup_after):
            for mask in self.player.pending_keyups:self.invoke(['input',mask,0])
            self.player.pending_keyups=[]
        if ((self.player is None and self.recording and not getattr(self,'awaiting_world',False)) or (self.player is not None and self.player.phase=='live')):
            previous=self.tape.last_state;state=[0,0] if getattr(self,'awaiting_created',False) else self.state()
            self.tape.loop(1,state);self.frames+=1
            ended=previous and len(previous)==14 and previous[8]>0 and (len(state)!=14 or state[8]<=0)
            if ended or self.frames%30==0:self.tape.flush()
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
        if self.recording or self.player is not None:self.tape.finish()
        self.hooks.remove()
        if hasattr(self,'delivery'):self.delivery.close()
        global _ACTIVE_CAPTURE
        if _ACTIVE_CAPTURE is self:_ACTIVE_CAPTURE=None

    def invoke(self,row):
        """Use only checked functions and current game-owned objects."""
        kind=row[0];ffi=self.ffi;game=self.refs.GameClient
        if kind=='frame' and self.tape.mode=='play':
            cursor=self.tape.cursor;ops=self.tape.ops
            # doTimePasses sleeps when less than 10 ms have accrued. An
            # observed empty call has no physics or input side effect; replay
            # its exact scalar boundaries without repeating that wall sleep.
            if (cursor+4<=len(ops) and ops[cursor]==['frame'] and ops[cursor+1][0]=='clock'
                    and ops[cursor+2]==['step',0] and ops[cursor+3]==['return','frame']):
                def empty():
                    self.tape.value('clock',lambda:0);self.tape.value('step',lambda:0)
                return self.tape.call(row,empty)
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
            elif kind=='inventory':
                value=ffi.new('struct ModifyInv *')
                self.functions['inventory-ctor'].original(value)
                try:
                    value.command,value.slotpos,value.targetslotpos=row[1:4];value._has_bits[0]=row[4]
                    hook=self.functions[kind]
                    return self.replacement(kind)(hook.original,client,value)
                finally:self.functions['inventory-dtor'].original(value)
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
        capture.recording=config.get('recording',True)
        capture.hooks.install();capture.delivery=delivery
        delivery.control_enabled=True
        _ACTIVE_CAPTURE=capture
        return capture
    except Exception:
        delivery.close();raise


_ACTIVE_CAPTURE=None
