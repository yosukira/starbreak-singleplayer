"""EST fixed encounters use the existing measured Jungle controllers."""
import copy

def create(wrapper, options):
    role, instance = wrapper.instance.role, wrapper.instance
    pack = copy.deepcopy(wrapper.run.pack.encounter_pack(role))
    if role == 'mid':
        from ..jungle_mid.session import JungleMidLiveSession
        options.map = 'jungle_mid'
        runtime = JungleMidLiveSession(wrapper.proxy,wrapper.channel,options,
            log=wrapper.log,bridge=wrapper.bridge,pack=pack,seed=instance.seed)
    elif role == 'boss':
        from ..jungle_boss.session import JungleBossLiveSession
        options.map = 'jungle_boss'
        runtime = JungleBossLiveSession(wrapper.proxy,wrapper.channel,options,
            log=wrapper.log,bridge=wrapper.bridge,pack=pack,seed=instance.seed)
    else:
        raise ValueError('EST encounter role unsupported: '+role)

    if hasattr(runtime,'_next_id'):
        runtime._next_id=max(runtime._next_id,max(instance.recipe['objects'])+1000)
    return runtime

def finished(runtime, role):
    if runtime.phase != 'defeated':
        return False
    if role == 'mid':
        return True

    return int(runtime.boss['head']) not in runtime.actors
