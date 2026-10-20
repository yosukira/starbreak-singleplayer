"""Typed native execution events shared with the offline recorder/importer.

No object pointers, native addresses, executable snapshots, arbitrary metadata
or outgoing login payloads are accepted. Incoming world deltas must come from
the owned offline transport. Python 3.5 compatible; importing this module does
not read files, change code or attach to the game.
"""
import base64
import json

SCHEMA='starbreak-native-journal-v1'
CALLS=('input','delta','frame','read','player-input','engine-frame','message','ack')
MESSAGES=(10,12,15,16,17,21,37,38)
MAX_OPERATIONS=3000000
MAX_PACKET=262144


def integer(value,low=-(2**31),high=2**32-1):
    if type(value)is not int or not low<=value<=high:raise ValueError('Invalid native replay integer')
    return value


def wire_size(value):
    """Exact compact JSON size for validated ASCII operation rows, without serialization."""
    if isinstance(value,list):return 2+max(0,len(value)-1)+sum(wire_size(part) for part in value)
    if isinstance(value,str):return len(value)+2
    return len(str(value))


def operation(row):
    if not isinstance(row,list) or not row:raise ValueError('Invalid native replay operation')
    kind=row[0]
    if kind=='loop' and len(row)==2:return [kind,integer(row[1],0,1)]
    if kind in ('frame','read') and len(row)==1:return [kind]
    if kind=='engine-frame' and len(row)==2:return [kind,integer(row[1],0,10000)]
    if kind=='player-input' and len(row)==5:return [kind]+[integer(value)for value in row[1:]]
    if kind=='return' and len(row)==2 and row[1]in CALLS:return list(row)
    if kind=='input' and len(row)==3:
        return [kind,integer(row[1],0,16383),integer(row[2],0,1)]
    if kind=='clock' and len(row)==2:return [kind,integer(row[1],-(2**31),2**31-1)]
    if kind=='random32' and len(row)==2:return [kind,integer(row[1],0,2**32-1)]
    if kind=='random64' and len(row)==3:
        return [kind,integer(row[1],0,2**32-1),integer(row[2],0,2**32-1)]
    if kind=='step' and len(row)==2:return [kind,integer(row[1],0,10000)]
    if kind in ('state','engine-state') and len(row)==2 and isinstance(row[1],list)and len(row[1])in(2,14):
        state=[integer(value)for value in row[1]]
        for index in(0,1):integer(state[index],0,2**32-1)
        if len(state)==14:
            integer(state[13],0,1)
            for index in(2,3,6,7,9):integer(state[index],0,2**31-1)
        return [kind,state]
    if kind=='ack' and len(row)==2:return [kind,integer(row[1],1,255)] if row[1]in(11,22) else _invalid_ack()
    if kind in ('delta','message') and len(row)==3 and isinstance(row[2],str):
        message=integer(row[1],1,255)
        if message not in ((12,37,38) if kind=='delta' else MESSAGES):raise ValueError('Only offline game messages belong in native replay')
        if len(row[2])>350000:raise ValueError('Native replay packet is too large')
        packet=base64.b64decode(row[2],validate=True)
        if not packet or len(packet)>MAX_PACKET:raise ValueError('Invalid native replay packet')
        return [kind,message,base64.b64encode(packet).decode('ascii')]
    raise ValueError('Unsupported native replay operation')


def _invalid_ack():raise ValueError('Unsupported native replay acknowledgement')


def validate_operations(rows,complete=True):
    if not isinstance(rows,list) or len(rows)>MAX_OPERATIONS:
        raise ValueError('Native replay is too long')
    output=[];stack=[];in_loop=False;checked=False
    for row in rows:
        row=operation(row);kind=row[0]
        if kind=='loop':
            if stack or bool(row[1])!=in_loop:raise ValueError('Invalid native replay loop boundary')
            if row[1]and not checked:raise ValueError('Native replay frame has no player check')
            in_loop=not bool(row[1]);checked=False
        elif kind in CALLS:
            if kind=='frame'and not in_loop:raise ValueError('Native frame outside the game loop')
            stack.append(kind)
            if len(stack)>32:raise ValueError('Native replay call nesting is too deep')
        elif kind=='return':
            if not stack or stack.pop()!=row[1]:raise ValueError('Native replay call order changed')
        elif kind=='engine-state':
            if not in_loop or not output or output[-1]!=['return','engine-frame']:
                raise ValueError('Native engine checkpoint must follow its own completed frame')
        elif kind=='state':
            if not in_loop or stack or checked:raise ValueError('Invalid native player check boundary')
            checked=True
        elif not stack:raise ValueError('Native clock/random value outside a game call')
        if checked and kind not in ('state','loop'):
            raise ValueError('Native frame continued after its final player check')
        output.append(row)
    if complete and (in_loop or stack):raise ValueError('Native replay ends inside a game call')
    return output


class NativeTape:
    """Observe and verify the original native call sequence without memory loads.

    Concrete game hooks provide `invoke` and state reads. This adapter never
    supplies native addresses or selects an executable from recording data.
    A recorder failure stops recording while allowing the live call to finish.
    """
    def __init__(self,mode='record',ops=None,save=None,notice=None,keep_ops=False):
        if mode not in('record','play'):raise ValueError('Invalid native tape mode')
        self.mode=mode;self.ops=validate_operations(ops or [])if mode=='play'else[]
        self.save=save;self.notice=notice;self.keep_ops=keep_ops
        self.cursor=self.sequence=self.size=self.pending_size=0
        self.pending=[];self.stack=[];self.in_loop=False
        self.error=None;self.finished=False;self.last_state=None;self.elapsed=0

    def fail(self,error):
        if self.error is None:
            # Never copy arbitrary paths or native exception text into exports.
            self.error=type(error).__name__
            if self.notice:
                try:self.notice(self.error)
                except Exception:pass

    def emit(self,row):
        if self.error or self.finished:return
        row=operation(row)
        size=wire_size(row)+1
        if self.size+size>128*1024*1024:raise ValueError('Native replay exceeds 128 MB')
        if self.pending_size+size>450000 or len(self.pending)>=1000:self.flush()
        if self.error:return
        self.pending.append(row);self.pending_size+=size;self.size+=size
        if self.keep_ops:self.ops.append(row)

    def flush(self,end=False):
        if self.mode!='record'or self.error or (not self.pending and not end):return
        chunk=dict(sequence=self.sequence,ops=self.pending,end=end)
        try:
            if self.save:self.save(chunk)
        except Exception as error:
            self.fail(error);return
        self.sequence+=1;self.pending=[];self.pending_size=0

    def take(self,kind):
        if self.error or self.finished:raise ValueError('Native playback is stopped')
        if self.cursor>=len(self.ops)or self.ops[self.cursor][0]!=kind:
            raise ValueError('Native replay call sequence changed')
        row=self.ops[self.cursor];self.cursor+=1
        return row

    def loop(self,stage,state=None):
        if self.error or self.finished:return self.mode=='record'
        try:
            marker=operation(['loop',stage])
            if self.stack or bool(stage)!=self.in_loop:raise ValueError('Overlapping native loop')
            if stage==1:
                check=operation(['state',state])
                if self.mode=='record':self.emit(check)
                elif self.take('state')!=check:raise ValueError('Native player state changed')
                self.last_state=check[1]
            if self.mode=='record':self.emit(marker)
            elif self.take('loop')!=marker:raise ValueError('Native loop stage changed')
            self.in_loop=not bool(stage)
            return True
        except Exception as error:self.fail(error);return self.mode=='record'

    def call(self,row,invoke):
        """Called by the installed input/frame/delta hook around its original."""
        if self.mode=='record'and(self.error or self.finished):return invoke()
        try:
            row=operation(row)
            if row[0]not in CALLS:raise ValueError('Unknown native replay call')
        except Exception as error:
            self.fail(error)
            if self.mode=='record':return invoke()
            raise
        entered=False
        try:
            if self.mode=='record':
                try:self.emit(row)
                except Exception as error:self.fail(error)
            elif self.take(row[0])!=row:raise ValueError('Native call arguments changed')
            self.stack.append(row[0]);entered=True
            result=invoke()
            end=['return',row[0]]
            if self.mode=='record':
                try:self.emit(end)
                except Exception as error:self.fail(error)
            elif self.take('return')!=end:raise ValueError('Native return boundary changed')
            return result
        except Exception as error:
            self.fail(error)
            if self.mode=='play':raise
            # A real game exception propagates; never invoke it again.
            raise
        finally:
            if entered:self.stack.pop()

    def value(self,kind,invoke):
        if kind not in('clock','random32','random64','step'):raise ValueError('Unknown native scalar')
        if not self.stack or self.error or self.finished:return invoke()
        if self.mode=='play':
            try:
                row=self.take(kind)
                value=row[1]|row[2]<<32 if kind=='random64'else row[1]
                if kind=='step':self.elapsed+=value
                return value
            except Exception as error:self.fail(error);raise
        value=invoke()
        try:
            if kind=='random64':
                if type(value)is not int or not 0<=value<2**64:raise ValueError('Invalid native random seed')
                self.emit([kind,value&0xffffffff,value>>32])
            else:self.emit([kind,value])
        except Exception as error:self.fail(error)
        if kind=='step':self.elapsed+=value
        return value

    def can_takeover(self):
        return bool(self.mode=='play'and not self.error and not self.in_loop and not self.stack
                    and self.cursor and self.ops[self.cursor-1]==['loop',1]
                    and self.last_state and len(self.last_state)==14 and self.last_state[8]>0
                    and self.last_state[13]==1)

    def continue_recording(self,save,sequence):
        """Called only after the host verifies and forks the server checkpoint."""
        if not self.can_takeover():raise ValueError('Native takeover needs a verified living-player frame boundary')
        integer(sequence,0,10**7)
        if not callable(save):raise ValueError('A separate native branch writer is required')
        self.ops=self.ops[:self.cursor]
        self.size=sum(wire_size(row)+1 for row in self.ops)
        self.mode='record';self.save=save;self.sequence=sequence
        self.pending=[];self.pending_size=0

    def finish(self):
        if self.finished:return
        if self.in_loop or self.stack:
            self.fail(ValueError('Native recording ended inside a frame'))
        self.flush(end=True);self.finished=True
