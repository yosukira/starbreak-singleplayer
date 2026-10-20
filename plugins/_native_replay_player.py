"""Drive the checked native engine on its owner thread, without memory loads.

Transport/control work runs separately. Replays supply typed game data only;
all calls go through the locally verified engine adapter. Python 3.5 syntax.
"""
import bisect
import time

try:
    from ._native_replay import NativeTape,validate_operations,VerifiedOperations,CALLS
except (ImportError,ValueError,SystemError):  # Embedded PyPy 3.5 top-level load
    from _native_replay import NativeTape,validate_operations,VerifiedOperations,CALLS


class PreparedReplay:
    """Local-only indexes; never accepted from a JSON document."""
    def __init__(self,document,progress=lambda loaded,total:None):
        ops=document['ops']
        if not isinstance(ops,VerifiedOperations) or not ops.complete:ops=validate_operations(ops)
        self.document=dict(document,ops=ops)
        self.preload=[];self.descriptions=set();seen=set();bootstrap=False
        self.times=[];self.ends=[];self.clocks=[];self.clock_positions=[];elapsed=0.
        end=int((document.get('view') or {}).get('end',len(ops)))
        import base64
        for index,row in enumerate(ops):
            if row[0]=='message':
                if row[1]==10:bootstrap=True
                if row[1]in(16,17):self.descriptions.add((row[1],base64.b64decode(row[2])))
                if row[1]==21 and index<end and row[2]not in seen:self.preload.append(row);seen.add(row[2])
            if row[0]=='clock':self.clock_positions.append(index);self.clocks.append(row[1])
            if row[0]=='step':elapsed+=row[1]/1000.
            if row==['loop',1]:self.ends.append(index+1);self.times.append(elapsed)
            if index%4096==0:progress(index,len(ops));time.sleep(0)
        if not bootstrap:raise ValueError('This recording predates native world bootstrap capture')
        if not self.ends:raise ValueError('Native recording has no completed game frames')
        self.duration=elapsed;progress(len(ops),len(ops))


class NativePlayer:
    def __init__(self,capture,document,keyboard=lambda:(),post_frame=lambda:None,clock=time.monotonic):
        prepared=document if isinstance(document,PreparedReplay) else PreparedReplay(document)
        document=prepared.document;ops=document['ops']
        descriptions=getattr(capture,'descriptions',set())
        if not prepared.descriptions.issubset(descriptions):
            raise ValueError('Native replay requires matching item and character descriptions')
        self.capture,self.document=capture,document
        self.legacy_sound_clocks=document.get('clock_scope')!='game-v1' or bool(document.get('runners'))
        self.keyboard,self.post_frame,self.clock=keyboard,post_frame,clock
        self.previous_keys=set(keyboard());self.requested=False;self.pending_keys=[]
        self.pending_keyups=[];self.keyup_after=0
        self.world_created=False;self.phase='playing';self.speed=1.;self.paused=False
        self.preload=prepared.preload
        self.preloaded=not self.preload;self.preloading=False
        self.target=None;self.seeking=False;self.cursor=0;self.seconds=0.;self.acks=[]
        self.times,self.ends=prepared.times,prepared.ends
        self.clocks,self.clock_positions=prepared.clocks,prepared.clock_positions
        self.duration=prepared.duration;self.anchor=clock();self.anchor_seconds=0.;self.reset()
        view=document.get('view') or {}
        self.view_start=int(view.get('start',0));self.view_end=int(view.get('end',len(ops)))
        self.view_offset=float(view.get('offset',0));self.view_duration=float(view.get('duration',self.duration))
        self.priming=bool(view)

    def blocked(self):return self.phase!='live'

    def reset(self):
        callback=getattr(self.capture,'replay_start',None)
        if callback:callback()
        self.capture.tape=NativeTape(mode='play',ops=self.document['ops'])
        self.capture.engine_elapsed=0;self.world_created=False
        self.cursor=0;self.seconds=0.;self.phase='playing' if self.preloaded else 'preparing';self.requested=False
        self.anchor=self.clock();self.anchor_seconds=0.
        self.priming=bool(self.document.get('view'))

    def read_body(self):
        tape=self.capture.tape
        while tape.cursor<len(tape.ops) and tape.ops[tape.cursor]!=['return','read']:
            row=tape.ops[tape.cursor]
            if row[0] in ('clock','random32','random64'):
                tape.take(row[0])  # Network bookkeeping has no game side effect.
            elif row[0] in CALLS:self.capture.invoke(row)
            else:raise ValueError('Unexpected native transport operation')
            if tape.error:raise ValueError('Native transport replay diverged')
        return True

    def uses_native_time_credit(self):
        engine='native'
        for row in self.document.get('runners',[]):
            if row['cursor']>self.capture.tape.cursor:break
            engine=row['engine']
        return engine=='native'

    def frame(self):
        tape=self.capture.tape
        clock_index=bisect.bisect_left(self.clock_positions,tape.cursor)
        if clock_index<len(self.clocks):self.capture.sync_audio_clock(self.clocks[clock_index])
        if not tape.loop(0):raise ValueError('Native frame could not start')
        while tape.cursor<len(tape.ops) and tape.ops[tape.cursor][0]!='state':
            row=tape.ops[tape.cursor]
            if row[0] not in CALLS:raise ValueError('Unexpected native driver operation')
            self.capture.invoke(row)
            if tape.error:raise ValueError('Native engine replay diverged')
        self.post_frame()
        if not tape.loop(1,self.capture.state()) or tape.error:
            raise ValueError('Native player checkpoint diverged')
        self.cursor=tape.cursor;self.seconds=tape.elapsed/1000.

    def update(self):
        if self.phase=='preparing':
            try:
                if not self.preloading:
                    self.preloading=True;self.capture.pending_assets=0
                    for row in self.preload:self.capture.invoke(row)
                if self.capture.pending_assets==0:
                    self.preloaded=True;self.preloading=False;self.phase='playing'
                    self.anchor=self.clock();self.anchor_seconds=0.
            except Exception as error:self.capture.tape.fail(error);self.phase='failed'
            return
        if self.phase!='playing' or self.capture.tape.error:return
        now=self.clock()
        if self.paused and not self.seeking and not self.priming:
            self.anchor=now;self.anchor_seconds=self.seconds;return
        target=self.target if self.seeking else self.anchor_seconds+(now-self.anchor)*self.speed
        deadline=now+(.035 if self.seeking or self.priming else .025);count=0
        try:
            self.capture.driving=True
            while self.capture.tape.cursor<self.view_end:
                index=bisect.bisect_right(self.ends,self.capture.tape.cursor)
                if index>=len(self.ends):break
                if not self.priming and self.times[index]>target+1e-7:break
                self.frame();count+=1
                if self.priming and self.cursor>self.view_start and self.world_created and len(self.capture.state())==14:
                    self.priming=False;self.anchor=self.clock();self.anchor_seconds=self.seconds;break
                if self.requested and self.can_takeover():break
                if count>=2048 or self.clock()>=deadline:break
            if self.seeking and (bisect.bisect_right(self.times,target)<=bisect.bisect_right(self.ends,self.cursor)):
                self.seeking=False;self.anchor=self.clock();self.anchor_seconds=self.seconds
            if self.cursor>=self.view_end:self.phase='ended'
        except Exception as error:
            self.capture.tape.fail(error);self.phase='failed'
        finally:self.capture.driving=False

    def can_takeover(self):
        return bool(self.phase=='playing' and not self.priming and not self.seeking and self.world_created and self.capture.tape.can_takeover())

    def keyboard_input(self,mask,pressed):
        # Called only inside the checked original keyboard-event dispatcher.
        # A mouse-generated WorldInput never reaches this path.
        if pressed and mask&0x2ff and self.accepts_keyboard():
            if self.seeking:self.seeking=False;self.target=None;self.anchor=self.clock();self.anchor_seconds=self.seconds
            self.requested=True;self.pending_keys.append([mask,1])
        elif not pressed and self.requested and mask&0x2ff:self.pending_keys.append([mask,0])

    def accepts_keyboard(self):
        state=self.capture.state()
        return bool(self.phase=='playing' and not self.priming and self.world_created and not self.capture.tape.error and len(state)==14 and state[8]>0)

    def ack(self,kind):
        if self.phase=='live':self.acks.append(kind)

    def control(self,value):
        action=value.get('action')
        if self.phase=='live':raise ValueError('The native battle is already under live control')
        if action=='pause':
            if type(value.get('paused')) is not bool:raise ValueError('Invalid native pause state')
            self.paused=value['paused']
        elif action=='speed':
            if value.get('speed') not in(.25,.5,1,2,4):raise ValueError('Unsupported native replay speed')
            self.speed=float(value['speed'])
        elif action=='seek':
            target=value.get('seconds')
            if type(target)not in(int,float) or not 0<=target<=self.view_duration:raise ValueError('Invalid native seek target')
            target+=self.view_offset
            if target<self.seconds or self.phase=='ended':self.reset()
            self.target=float(target);self.seeking=True
            if type(value.get('paused')) is bool:self.paused=value['paused']
        elif action=='takeover':
            if not self.can_takeover():raise ValueError('Native takeover is not at a verified living-player frame')
            self.requested=True
        elif action=='stop':self.phase='stopped'
        else:raise ValueError('Unsupported native replay control')
        self.anchor=self.clock();self.anchor_seconds=self.seconds

    def checkpoint(self):
        if not self.can_takeover():raise ValueError('Native player has not reached a verified takeover boundary')
        tape=self.capture.tape
        return dict(id=self.document['id'],cursor=tape.cursor,player=tape.last_state)

    def commit(self,save,sequence):
        checkpoint=self.checkpoint()
        tape=self.capture.tape
        clocks=[row[1] for row in tape.ops[:tape.cursor] if row[0]=='clock']
        real=int(self.capture.functions['clock'].original())
        self.capture.clock_offset=((clocks[-1]-real+2**31)%2**32-2**31) if clocks else 0
        self.capture.sync_audio_clock(real+self.capture.clock_offset)
        tape.continue_recording(save,sequence);self.phase='live'
        self.requested=False;self.paused=False;self.speed=1.
        return checkpoint

    def status(self):
        return dict(state='failed' if self.capture.tape.error and self.capture.tape.mode=='play' else 'preparing' if self.priming else 'seeking' if self.seeking else self.phase,
            cursor=self.cursor,seconds=max(0,self.seconds-self.view_offset),duration=self.view_duration,paused=self.paused,
            speed=self.speed,takeover=self.can_takeover(),error=self.capture.tape.error,
            message=getattr(self.capture.tape,'error_detail',None),
            target=max(0,self.target-self.view_offset) if self.seeking else None,
            stage='reconstructing' if self.priming else 'seeking' if self.seeking else 'assets' if self.phase=='preparing' else None,
            progress=min(1.,self.cursor/float(max(1,self.view_start))) if self.priming else
                min(1.,self.seconds/max(.001,self.target)) if self.seeking else None,
            assets_loaded=max(0,len(self.preload)-self.capture.pending_assets),assets_total=len(self.preload))
