"""Existing ETL encounters bound to the exact compiled room geometry."""
import copy
from ...live.room import BossRoom
from ..hulk_mid.scene import normalize

def _bind_templates(pack, instance):
    objects = copy.deepcopy(instance.recipe['objects'])
    pack.templates = {str(oid):props for oid,props in objects.items()}
    inventory = {}
    for oid,props in objects.items():
        inventory.setdefault(props.get('vid',''),[]).append(oid)
    pack.scene['inventory'] = inventory
    return objects

def create(wrapper, options):
    role, instance = wrapper.instance.role, wrapper.instance
    rows = instance.snapshot.document(2)['worldstate_']['serverworld_']['obj_']
    room = BossRoom(copy.deepcopy(rows), {}, source='etl_run:'+role)
    if role == 'mid':
        from ..lab_mid.session import LabMidLiveSession
        options.map = 'lab_mid'
        runtime = LabMidLiveSession(wrapper.proxy,wrapper.channel,options,room,
            log=wrapper.log,bridge=wrapper.bridge)
        _bind_templates(runtime.pack, instance)

        if runtime.pack.boss_objid != 3 or runtime.pack.block_ids != list(range(4,12)):
            raise ValueError('ETL compiled Mid no longer matches the measured controller')
        runtime.prepare()
    elif role == 'boss':
        from ..lab_hard.session import LabHardLiveSession
        options.map = 'lab_hard'
        runtime = LabHardLiveSession(wrapper.proxy,wrapper.channel,options,room,
            log=wrapper.log,bridge=wrapper.bridge)
        objects = _bind_templates(runtime.pack, instance)
        runtime.pack.scene['walls'] = [dict(props,objid=oid) for oid,props in objects.items()
                                      if props.get('vid') == 'lab.crushwall']
        if runtime.pack.boss_objid != 2:
            raise ValueError('ETL compiled Boss no longer matches zomputerc2')
        runtime.prepare()
    else:
        raise ValueError('ETL encounter role unsupported: '+role)
    runtime._next_objid = max(runtime._next_objid, max(instance.recipe['objects'])+1000)
    return runtime

def finished(runtime, role):
    return runtime.phase == 'done'
