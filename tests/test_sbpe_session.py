import configparser
from types import SimpleNamespace
import unittest
from unittest import mock

from plugins._practice_plugin_guard import install


class PluginGuardTests(unittest.TestCase):
    def test_loaded_capture_is_unloaded_and_future_reload_is_blocked_in_this_process(self):
        config=configparser.ConfigParser()
        config.read_string('[plugin_combat_recorder]\nenabled = yes\n[plugin_noise]\nenabled = yes\n')
        capture=SimpleNamespace(onUnload=mock.Mock())
        visual=SimpleNamespace(onUnload=mock.Mock())
        reload_plugin=mock.Mock(return_value=True)
        manager=SimpleNamespace(config=config,_reload=reload_plugin,
            _active={'plugin_combat_recorder':capture,'plugin_noise':visual})
        self.assertEqual(install(manager),('combat_recorder',))
        capture.onUnload.assert_called_once_with();visual.onUnload.assert_not_called()
        self.assertIs(manager._active['plugin_noise'],visual)
        self.assertNotIn('plugin_combat_recorder',manager._active)
        self.assertFalse(manager._reload('combat_recorder.py'))
        self.assertTrue(manager._reload('noise.py'))
        reload_plugin.assert_called_once_with('noise.py')
        self.assertTrue(config.getboolean('plugin_combat_recorder','enabled'))
        self.assertTrue(config.getboolean('plugin_noise','enabled'))
        self.assertEqual(install(manager),('combat_recorder',))
        capture.onUnload.assert_called_once_with()

    def test_a_new_normal_sbpe_manager_keeps_the_original_plugins(self):
        first=SimpleNamespace(_active={},_reload=mock.Mock(return_value=True))
        normal=SimpleNamespace(_active={},_reload=mock.Mock(return_value=True))
        install(first)
        self.assertFalse(first._reload('combat_recorder.py'))
        self.assertTrue(normal._reload('combat_recorder.py'))


if __name__=='__main__':unittest.main()
