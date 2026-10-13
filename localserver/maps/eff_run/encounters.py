"""Bind existing Forest capabilities to the exact loaded EFF encounter room."""
import copy

def create(wrapper,options):
    role=wrapper.instance.role;recipe=wrapper.instance.recipe
    pack=wrapper.run.pack.encounter_pack(role,wrapper.instance.seed)
    pack.templates={str(oid):copy.deepcopy(p) for oid,p in recipe['objects'].items()}
    scene=copy.deepcopy(recipe['metadata']['encounter_scene'])
    expected='forest.egoblinmaster' if role=='mid' else 'forest.goblinmother'
    bosses=[oid for oid,p in recipe['objects'].items() if p.get('vid')==expected]
    gates=[oid for oid,p in recipe['objects'].items() if p.get('vid')=='forest.vgate288']
    if len(bosses)!=1 or len(gates)!=1:raise ValueError('EFF encounter actual boss/gate identity ambiguous')
    scene['boss_id']=bosses[0];scene['gate_id']=gates[0];scene['portal_id']=next(iter(recipe['links']))
    scene['spawn_id']=recipe['metadata']['spawn_marker_id'];pack.scene=scene

    pack.solids=[tuple(p.get(k,0)/256 for k in ('xmp','ymp','wmp','hmp')) for p in recipe['objects'].values()
                 if p.get('terraintype')==1 and p.get('wmp',0)>0 and p.get('hmp',0)>0]
    pack.surfaces=pack.solids
    options.map='forest_'+role;options.room_roll_seed=wrapper.instance.seed
    if role=='mid':from ..forest_mid.session import ForestMidLiveSession as Session
    elif role=='boss':from ..forest_boss.session import ForestBossLiveSession as Session
    else:raise ValueError('EFF unsupported encounter '+role)
    runtime=Session(wrapper.proxy,wrapper.channel,options,log=wrapper.log,bridge=wrapper.bridge,pack=pack,seed=wrapper.instance.seed)
    runtime.controller.next_id=max(runtime.controller.next_id,max(recipe['objects'])+1000)
    return runtime

def finished(runtime,role):

    return runtime.phase=='done' and runtime.controller.state=='done'
