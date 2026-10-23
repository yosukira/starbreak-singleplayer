"""Existing SC encounters bound to the full run's persistent room instances."""
import copy
from functools import lru_cache

from ...live.room import BossRoom


_MAPS = dict(patriarch='sc_patriarch', bonesaw='sc_bonesaw', hive='sc_hive', boss='sc_boss')


@lru_cache(maxsize=4)
def _pack(kind):
    # Immutable disk-loaded source. Every encounter receives its own copy.
    if kind == 'patriarch':
        from ..sc_patriarch.scene import PatriarchPack
        return PatriarchPack()
    if kind == 'bonesaw':
        from ..sc_bonesaw.scene import BonesawPack
        return BonesawPack()
    if kind == 'hive':
        from ..sc_hive.scene import ScHivePack
        return ScHivePack()
    if kind == 'boss':
        from ..sc_boss.scene import ScBossPack
        return ScBossPack()
    raise ValueError('Unsupported SC encounter: ' + str(kind))


def bound_pack(level_pack, instance):
    kind = instance.recipe['metadata']['encounter_kind']
    cached = getattr(level_pack, 'encounter_packs', {}).get(kind)
    pack = copy.deepcopy(cached if cached is not None else _pack(kind))
    objects = copy.deepcopy(instance.recipe['objects'])
    pack.templates = {str(oid): props for oid, props in objects.items()}
    # Each dedicated controller keeps its configured projectile defaults.
    # The common catalogue supplies every additional SC body it may request.
    dynamic = getattr(level_pack, 'dynamic_templates', None)
    if dynamic is None:
        dynamic = level_pack.content.dynamic_templates
    merged = copy.deepcopy(dynamic)
    merged.update(copy.deepcopy(pack.dynamic_templates))
    pack.dynamic_templates = merged
    if hasattr(pack, 'solids'):
        pack.solids = [tuple(p.get(key, 0) / 256 for key in ('xmp', 'ymp', 'wmp', 'hmp'))
                       for p in objects.values() if p.get('terraintype') == 1 and p.get('vid') == 'sc.wall']
    pack.scene['spawn_id'] = instance.recipe['metadata']['spawn_marker_id']
    return pack


def create(wrapper, options):
    instance = wrapper.instance
    kind = instance.recipe['metadata']['encounter_kind']
    if kind not in _MAPS:
        raise ValueError('Unsupported SC encounter: ' + str(kind))
    pack = bound_pack(wrapper.run.pack, instance)
    rows = instance.snapshot.document(2)['worldstate_']['serverworld_']['obj_']
    room = BossRoom(copy.deepcopy(rows), {}, source='sc_run:' + instance.role)
    child = copy.copy(options)
    child.map = _MAPS[kind]
    child.worldid = 1
    child.live_seed = child.room_roll_seed = instance.seed
    kwargs = dict(room=room, log=wrapper.log, bridge=wrapper.bridge, pack=pack)
    if kind == 'patriarch':
        from ..sc_patriarch.session import PatriarchLiveSession
        cls = PatriarchLiveSession
        kwargs['seed'] = instance.seed
    elif kind == 'bonesaw':
        from ..sc_bonesaw.session import BonesawLiveSession
        cls = BonesawLiveSession
        kwargs['seed'] = instance.seed
    elif kind == 'hive':
        from ..sc_hive.session import ScHiveLiveSession
        cls = ScHiveLiveSession
    else:
        from ..sc_boss.session import ScBossLiveSession
        cls = ScBossLiveSession
    runtime = cls(wrapper.proxy, wrapper.channel, child, **kwargs)
    # Constructors prepare once; RunLiveSession advances this same runtime on
    # each visit, without invoking the standalone thread/start lifecycle.
    runtime.instance = instance
    runtime.map_id = 'sc_run'
    if hasattr(runtime, 'supplies'):
        runtime.supplies.map_id = 'sc_run'
    return runtime


def finished(runtime, kind):
    if kind not in _MAPS:
        return False
    controller = getattr(runtime, 'controller', None)
    return (getattr(runtime, 'phase', None) == 'done'
            and getattr(controller, 'state', None) == 'done')
