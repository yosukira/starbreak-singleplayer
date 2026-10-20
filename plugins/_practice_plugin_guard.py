"""Keep legacy capture hooks out of one simulator process, not SBPE settings."""
import logging
import os

BLOCKED = ('combat_recorder', 'design_recorder', 'map_export',
           'sb_hardlab_probe', 'hardlab_delta', 'hardlab_server', 'hardlab_spawn')
LOG = logging.getLogger('hardlab_live')


def install(manager):
    """Called only by the live-session plugin, before installing its hooks."""
    if manager is None:
        return ()
    existing = getattr(manager, '_practice_plugin_guard', None)
    if existing is not None:
        return existing
    reload_plugin = getattr(manager, '_reload', None)
    active = getattr(manager, '_active', None)
    if not callable(reload_plugin) or not isinstance(active, dict):
        return ()

    def session_reload(filename):
        name, extension = os.path.splitext(os.path.basename(filename))
        if extension.lower() == '.py' and name.lower() in BLOCKED:
            return False
        return reload_plugin(filename)

    manager._reload = session_reload
    paused = []
    for name in BLOCKED:
        key = 'plugin_' + name
        plugin = active.pop(key, None)
        if plugin is None:
            continue
        cleanup = getattr(plugin, 'onUnload', None)
        if callable(cleanup):
            cleanup()
        paused.append(name)
        LOG.info('Paused legacy capture plugin for this practice process: %s', name)
    manager._practice_plugin_guard = tuple(paused)
    return tuple(paused)
