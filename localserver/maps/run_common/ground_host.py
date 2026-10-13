"""Candidate host integration for the final native egress boundary.

The public default remains disabled until destination materialization and
recovery have passed. Nonempty destination checkpoints are explicitly refused
before loading; this host must never silently drop saved ground objects.
"""
import copy
import hashlib
import threading
import time

from ...live.bridge import PlayerState
from .ground_checkpoint import GroundCheckpointStore
from .ground_transition import GroundTransition, fingerprint

class GroundEgressHost:
    def __init__(self,server,channel,source,link,purpose,request_id,attempt,scope,issued,now=None):
        self.server,self.channel,self.source=server,channel,source
        self.link=copy.deepcopy(link);self.purpose=purpose;self.run=source.run
        self.source_role=self.run.current;self.source_entry=self.run.entry
        self.source_token=source._input_serial
        self.restore=None
        self.options=server.options if purpose=='room' else server._world_exit_options(link['destination'])
        if self.options is None:raise ValueError('unsupported external ground destination')
        from ... import game_settings
        if game_settings.map_info(self.options.map).get('preload_pvids'):
            raise ValueError('ground egress supports one expected LoadPVid only')
        if purpose=='room':
            destination=self.run.room(link['destination'])
            previous=getattr(destination,'ground_checkpoint',None)
            if previous and previous.get('objects') and not getattr(server,'GROUND_RESTORE_REQUIRED',False):
                raise ValueError('nonempty destination requires verified native ground restore coordinator')
            if source.map_id=='egy_run':
                from ..egy_run.session import world_blob
            else:world_blob=source.world_blob
            self.world=server._distinct_player_world_blob(world_blob(server,self.run,role=link['destination'],entry=link.get('entry','default')))
        else:self.world=server._load_create_world(self.options)
        self.bootstrap=server._load_bootstrap_delta(self.options)
        self.tx=GroundTransition(request_id,attempt,scope,self.options.pvid,hashlib.sha256(self.world).hexdigest(),
            issued,time.time() if now is None else now)
        self.lock=threading.RLock();self.stop=threading.Event();self.ready=threading.Event()
        self.thread=None;self.world_sent=False;self.paused=False;self.finished=False;self.error=None
        self.cancel_pending=False;self.source_commit=None;self.stats=dict(loads=0,worlds=0,commits=0,aborts=0)
        if purpose=='room' and previous and previous.get('objects'):
            maker=getattr(server,'_new_ground_restore_host',None)
            if not callable(maker):raise ValueError('native ground restore coordinator unavailable')
            self.restore=maker(self,destination,previous)

    def start(self):
        self.server._restart_in_progress=True
        try:
            if self.restore is not None:self.restore.start()
            self.source.bridge.arm_ground_egress(self.tx.control)
        except Exception:
            if self.restore is not None:self.restore.source_preserved('restore channel could not arm before Load')
            self.server._restart_in_progress=False
            self.server._ground_egress_host=None
            raise
        self.thread=threading.Thread(target=self._loop,daemon=True);self.thread.start()

    def _loop(self):
        while not self.stop.wait(.01):
            try:self.poll(time.time())
            except Exception as error:
                self.error=type(error).__name__+': '+str(error)

                if self.tx.load_sent:self.tx.state='recovery-required';self.stop.set()
                else:self._cancel()

    def _cancel(self):
        if not self.cancel_pending:
            self.source.bridge.cancel_ground_egress(self.tx.control)
            self.cancel_pending=True

    def poll(self,now):
        with self.lock:
            if self.finished or self.tx.committed:return
            small,receipts=self.source.bridge.ground_egress_snapshot()
            if self.cancel_pending:
                if self.tx.matches(small) and small.get('status')=='cancelled':self._resume('native egress arm cancelled')
                elif now>=self.tx.deadline:self.error='cancel ACK timeout; source remains running, new loads held';self.stop.set()
                return
            if self.tx.state=='await-arm':
                if self.tx.offer_arm(small,now):

                    self.source.stop(stop_bridge=False);self.paused=True
                    worker=getattr(self.source,'_thread',None)
                    if worker is not None and worker.is_alive():raise ValueError('old room worker has not stopped; Load forbidden')
                    observer=getattr(self.source,'commit_status',None)
                    self.source_commit=observer() if callable(observer) else None
                    if not isinstance(self.source_commit,dict) or self.source_commit.get('local_committed') is not True:
                        self.error='source snapshot/transport/outboxes not committed; Load forbidden'
                        self._cancel()
                        return
                    self.source.suppress_send=True
                    self.tx.sent_load(time.time())
                    self.server._pending_pvids=[]
                    payload=self.server.layouts.encode('LoadPVid',dict(pvid_=self.options.pvid))
                    self.server._send_raw(self.channel,21,payload,'ground final-egress load')
                    self.stats['loads']+=1
                elif now>=self.tx.control['expires_at'] or (self.tx.matches(small) and small.get('status') in ('rejected','error','expired')):
                    self._cancel()
            for receipt in receipts:self.tx.offer(receipt,now)
            if self.tx.state=='abort-ready':
                self.source.bridge.acknowledge_ground_egress(self.tx.ack)
                self.tx.mark_aborted();self.stats['aborts']+=1;self._resume('native final ground read rejected; source preserved')
            elif self.tx.candidate() is not None:self._commit(now)
            elif self.tx.load_sent and now>=self.tx.deadline:
                self.tx.expire(now);self.error='final egress receipt/WorldCreated deadline; destination held for recovery';self.stop.set()

    def _resume(self,reason):
        worker=getattr(self.source,'_thread',None)
        if self.paused and worker is not None and worker.is_alive():
            self.error='native source preserved; waiting for old Python worker to stop before resuming'
            return
        if self.restore is not None:
            try:self.restore.source_preserved(reason)
            except ValueError as error:
                self.error=str(error);self.tx.state='recovery-required';self.stop.set();return
        self.source.bridge.arm_ground_egress(None)
        if self.tx.ack is not None:
            self.server._ground_egress_ack_pending=dict(bridge=self.source.bridge,
                control=copy.deepcopy(self.tx.control),ack=copy.deepcopy(self.tx.ack))
        self.source._inventory_status=dict(status='rejected',reason=reason)
        self.server._restart_in_progress=False
        self.server._ground_egress_host=None
        self.finished=True;self.stop.set()
        if self.paused:
            self.source.suppress_send=False;self.source.start()

    def send_world(self):
        with self.lock:
            if not self.tx.load_sent or self.finished or self.world_sent:return False
            self.server._send_raw(self.channel,10,self.world,'ground staged world')
            if self.bootstrap:self.server._send_raw(self.channel,12,self.bootstrap,'ground staged bootstrap')
            self.world_sent=True;self.server.pending_player_delta=True;self.stats['worlds']+=1
            if self.restore is not None:self.restore.target_sent()
            return True

    def world_created(self):
        with self.lock:
            if not self.world_sent:return False
            return self.tx.confirm_world_created(self.tx.control['expected_cw_sha256'])

    def wait_ready(self):
        return self.ready.wait(max(0.,self.tx.deadline-time.time()))

    def _commit(self,now):
        if (self.run.current!=self.source_role or self.run.entry!=self.source_entry or
                self.source._input_serial!=self.source_token):raise ValueError('source host generation changed before commit')
        candidate=self.tx.candidate()
        created=copy.deepcopy(candidate['create_player']);vitals=candidate['vitals']
        if self.purpose=='room':
            spawn=self.run.destination_spawn(self.link['destination'],self.link.get('entry','default'))
            width,height=self.run.room(self.link['destination']).recipe['metadata'].get('spawn_footprint_mp',(10240,20480))
            w,h=vitals['wmp'],vitals['hmp']
            created['pos_']=dict(xmp_=round(spawn[0]+(width-w)/2),ymp_=round(spawn[1]+height-h),wmp_=w,hmp_=h)
        else:created.pop('pos_',None)
        message=self.server.layouts.decode('Delta',self.server.player_delta_blob)
        message['createplayer_']=created
        player_blob=self.server.layouts.encode('Delta',message)
        store=copy.deepcopy(getattr(self.source.instance,'_ground_store',None)) or GroundCheckpointStore()
        checkpoint,report=store.replace_joint(self.tx.final_joint,self.tx.issued,expected_scope=self.tx.control['scope'],
            request_id=self.tx.control['request_id'],attempt=self.tx.control['request_attempt'],now=now,
            max_age_s=self.tx.deadline-self.tx.control['issued_at'])
        if not report['ready']:raise ValueError('final checkpoint commit rejected: '+str(report['errors']))
        if self.purpose=='room':
            raw=dict(vitals,objid=self.tx.control['scope']['objid'],worldid=self.tx.control['scope']['worldid'],alive=True,
                maxhp=vitals['hp'],received_at=now)
            if not self.run.move(self.link,PlayerState(**raw)):raise ValueError('destination progress changed before commit')
        else:
            world=self.server.layouts.decode('CreateWorld',self.server._import_world_tool().unwrap_decompress(self.world))
            self.server._world_exit_arrival=dict(map=self.options.map,room_token=self.source_token+1,
                room_seed=world['worldprops_'].get('seed_',0),room_ready=False,room_handoff=True)
            self.server.options=self.options
        self.source.instance._ground_store=store;self.source.instance.ground_checkpoint=checkpoint
        self.source.instance.ground_checkpoint_boundary=dict(kind='native-final-egress-v1',
            checkpoint_sha256=fingerprint(checkpoint),
            scope=copy.deepcopy(checkpoint['scope']),request_id=self.tx.control['request_id'],
            request_attempt=self.tx.control['request_attempt'],
            expected_cw_sha256=self.tx.control['expected_cw_sha256'],
            actual_cw_sha256=self.tx.ack['actual_cw_sha256'],
            native_thread=self.tx.native_boundary[0],createworld_thread=self.tx.native_boundary[0],
            feedback_applied_proven=False)
        self.server.create_world_blob=self.world;self.server.bootstrap_delta_blob=self.bootstrap
        self.server.player_delta_blob=player_blob
        self.server._restart_bridge=self.source.bridge;self.server._restart_player_type=self.source.player_type
        with self.source.bridge._lock:
            self.source.bridge.state=None;self.source.bridge.events=[];self.source.bridge.keys={}
            if self.purpose!='room':self.source.bridge.native_context=dict(self.server._world_exit_arrival)
            elif self.restore is not None:
                self.source.bridge.native_context=dict(self.restore.room,room_ready=False,room_handoff=True)
        if self.server.live_session is self.source:self.server.live_session=None
        self.tx.mark_committed();self.source.bridge.acknowledge_ground_egress(candidate['ack'])
        self.server._ground_egress_ack_pending=dict(bridge=self.source.bridge,
            control=copy.deepcopy(self.tx.control),ack=copy.deepcopy(candidate['ack']))
        self.source.bridge.arm_ground_egress(None)
        self.stats['commits']+=1;self.stop.set();self.ready.set()

    def player_sent(self):
        with self.lock:
            if not self.tx.committed:raise ValueError('player cannot precede final ground commit')
            self.finished=True;self.server._ground_egress_host=None
