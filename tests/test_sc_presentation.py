"""Native SC cue boundaries, encoded through the real Delta schema."""
import base64
import copy
import unittest
from types import SimpleNamespace

from localserver.live.wire import DeltaBatch
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.maps.run_common.actors import ActorSession


class ScPresentationTests(unittest.TestCase):
    def host(self, vid, props=None):
        host = SimpleNamespace(actors={7: dict(vid=vid, props=copy.deepcopy(props or {}))},
                               batch=DeltaBatch(), _offset=lambda: 17)
        return host, ScPresentation(host)

    def decoded(self, host):
        layouts = host.batch.layouts
        delta = layouts.decode('Delta', host.batch.encode(), raw_strings=True)
        sounds, animations = [], []
        for event in delta['worldupdate_'].get('objectupdates_', []):
            self.assertEqual(event['objid_'], 7)
            self.assertEqual(event.get('offset_'), 17)
            if 'playsound_' in event:
                name = event['playsound_']['soundname_']
                sounds.append(base64.b64decode(name['raw']).decode('utf8'))
            change = event.get('modifyprops_', {})
            if change.get('property_') == 49:
                blob = base64.b64decode(change['messageval_']['raw'])
                animations.append(layouts.decode('AnimationProperties', blob))
        return sounds, animations

    def test_sliver_birth_has_sound_and_missing_create_animation_once(self):
        host, cues = self.host('sc.sliver')
        cues.projectile_created(7, 'sc.sliver')
        cues.projectile_created(7, 'sc.sliver')
        sounds, animations = self.decoded(host)
        self.assertEqual(sounds, ['sc.sliver'])
        self.assertEqual(animations, [dict(animid_=1, time_=198)])
        self.assertEqual(host.actors[7]['props']['animprops'], dict(animid=1, time=198))

    def test_monk_birth_is_silent_and_release_remains_separate(self):
        host, cues = self.host('sc.monkb')
        cues.projectile_created(7, 'sc.monkb')
        self.assertEqual(self.decoded(host), ([], [dict(animid_=1, time_=264)]))
        host.batch.clear()
        cues.sound(7, 'sc.monkshoot', token='release-at-3s')
        self.assertEqual(self.decoded(host), (['sc.monkshoot'], []))

    def test_caller_observed_shard_variant_is_preserved(self):
        host, cues = self.host('sc.shard', dict(animprops=dict(animid=2, time=198)))
        cues.projectile_created(7, 'sc.shard')
        self.assertEqual(self.decoded(host), (['sc.bombdrop'], [dict(animid_=2, time_=198)]))

    def test_existing_world_body_and_unknown_projectile_do_not_invent_cues(self):
        original = dict(animprops=dict(animid=0, pos=221, time=660), soundprefix='sc.monk',
                        vid='sc.skeleton', pose=5)
        host, cues = self.host('sc.skeleton', original)
        cues.body_created(7)
        cues.projectile_created(7, 'sc.unobserved')
        self.assertEqual(self.decoded(host), ([], []))
        self.assertEqual(host.actors[7]['props'], original)

    def test_sliverx_effect_and_projectile_callbacks_do_not_duplicate(self):
        host, cues = self.host('sc.sliverx')
        cues.effect_created(7, 'sc.sliverx')
        cues.projectile_created(7, 'sc.sliverx')
        self.assertEqual(self.decoded(host), (['sc.sliverx'], [dict(animid_=1, time_=264)]))

    def test_new_laser_segment_flashes_without_replaying_snapshot_or_endpoint(self):
        host, cues = self.host('sc.aim')
        cues.body_created(7)
        self.assertEqual(self.decoded(host), ([], []))
        host._pending_creates = {7: object()}
        cues.body_created(7)
        cues.body_created(7)
        self.assertEqual(self.decoded(host), ([], [dict(animid_=1, time_=264)]))
        host, cues = self.host('sc.aimend')
        host._pending_creates = {7: object()}
        cues.body_created(7)
        self.assertEqual(self.decoded(host), ([], []))

    def test_direct_weapon_create_reaches_laser_flash_in_actual_actor_host(self):
        class Idle:
            def __init__(self, host):
                pass
            def prepare(self):
                pass
        batch = DeltaBatch()
        instance = SimpleNamespace(seed=5, recipe=dict(metadata={}, creators={}),
                                   snapshot=SimpleNamespace(objects={}))
        pack = SimpleNamespace(props=lambda vid: dict(vid=vid, wmp=16384, hmp=12288))
        host = ActorSession(SimpleNamespace(layouts=batch.layouts),None,SimpleNamespace(),instance,
                            SimpleNamespace(),pack,map_id='sc_run',enemies_factory=Idle,
                            mechanics_factory=Idle,presentation_factory=ScPresentation)
        oid = host._create('sc.aim',(120,340))
        message = host.batch.layouts.decode('Delta',host.batch.encode(),raw_strings=True)
        world = message['worldupdate_']
        self.assertEqual(world['generalupdates_'][0]['create_']['objid_'],oid)
        updates = [row for row in world['objectupdates_'] if row['objid_']==oid]
        self.assertEqual(len(updates),1)
        self.assertEqual(updates[0]['modifyprops_']['property_'],49)
        value = updates[0]['modifyprops_']['messageval_']['raw']
        self.assertEqual(host.batch.layouts.decode('AnimationProperties',base64.b64decode(value)),
                         dict(animid_=1,time_=264))


if __name__ == '__main__':
    unittest.main()
