"""EUB fixed Betty Mid and Zomputer encounter adapters."""

def create(wrapper, options):
    role, instance = wrapper.instance.role, wrapper.instance
    kind=instance.recipe['metadata'].get('encounter_kind') or role
    pack = wrapper.run.pack.encounter_pack(role, instance.seed)
    if kind == 'mid':
        from ..dumb_mid.session import DumbMidLiveSession
        options.map = 'dumb_mid'
        cls = DumbMidLiveSession
    elif kind == 'boss':
        from ..dumb_boss.session import DumbBossLiveSession
        options.map = 'dumb_boss'
        cls = DumbBossLiveSession
    else:
        raise ValueError('EUB encounter role unsupported: '+role)
    runtime = cls(wrapper.proxy, wrapper.channel, options, log=wrapper.log,
                  bridge=wrapper.bridge, pack=pack, seed=instance.seed)
    runtime._next_id = max(runtime._next_id, max(instance.recipe['objects'])+1000)
    return runtime

def finished(runtime, role):
    return runtime.phase == 'done'
