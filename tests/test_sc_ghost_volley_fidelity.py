"""Ghost volleys through the shared actor clock and encoded native snapshots."""
import math
import unittest
from types import SimpleNamespace

from localserver.pb_layout import default_layouts
from localserver.world_snapshot import WorldSnapshot, decode_delta
from localserver.maps.run_common.actors import ActorSession
from localserver.maps.sc_run.abilities import center
from localserver.maps.sc_run.enemies import ScEnemies
from localserver.maps.sc_run.presentation import ScPresentation
from localserver.maps.sc_run.scene import ScRunPack


def actor_host(seed=0):
    layouts = default_layouts()
    snapshot = WorldSnapshot(layouts, dict(worldstate_=dict(
        serverworld_=dict(ownerid_=1, obj_=[]), clientworld_=[])))
    sent, births = [], {}

    def send(_channel, kind, payload, label='', record=False):
        if kind != 12:
            return
        message = decode_delta(layouts, payload)
        snapshot.apply(message)
        sent.append(message)
        for update in message.get('worldupdate_', {}).get('generalupdates_', []):
            if 'create_' in update:
                oid = update['create_']['objid_']
                births[oid] = snapshot.properties_at((1, oid))

    instance = SimpleNamespace(seed=seed, recipe=dict(metadata={}, creators={}),
                               snapshot=snapshot, flags={})
    bridge = SimpleNamespace(snapshot=lambda: (None, {}), poll_events=lambda: [])
    mechanics = lambda _host: SimpleNamespace(prepare=lambda: None, step=lambda _dt: None)
    host = ActorSession(SimpleNamespace(layouts=layouts, _send_raw=send), None,
        SimpleNamespace(), instance, bridge, ScRunPack(), map_id='sc_run',
        enemies_factory=ScEnemies, mechanics_factory=mechanics,
        presentation_factory=ScPresentation, players={})
    return host, snapshot, sent, births


def creates(sent, vid):
    return [update['create_'] for message in sent
            for update in message.get('worldupdate_', {}).get('generalupdates_', [])
            if update.get('create_', {}).get('props_', {}).get('vid_') == vid]


def start_volley(host, vid):
    parent = host._create(vid, (0, 0), changes=dict(hitpoints=750, damage=35))
    host.enemies.add_actor(parent)
    player = dict(xmp=400*256, ymp=0, wmp=32*256, hmp=64*256, alive=True)
    host.players_provider = {7: player}
    state = host.enemies.units[parent]
    state.update(active=True, phase='chase', target=7, target_point=center(player),
                 next=0., scan=100.)
    host.enemies._weapons(parent, state, player)
    return parent


class ScGhostVolleyFidelityTests(unittest.TestCase):
    def test_bomber_triple_alternates_birth_images_in_delta_and_snapshot(self):
        phases = set()
        for seed in (0, 1):
            with self.subTest(seed=seed):
                host, snapshot, sent, births = actor_host(seed)
                parent = start_volley(host, 'sc.bomber')
                for _ in range(20):
                    host.step(.05)
                shots = creates(sent, 'sc.shard')
                self.assertEqual(len(shots), 3)
                variants = [shot['props_']['animprops_']['animid_'] for shot in shots]
                phases.add(variants[0])
                self.assertEqual(variants, [variants[0], 3-variants[0], variants[0]])
                for shot, variant in zip(shots, variants):
                    oid = shot['objid_']
                    self.assertEqual(shot['creatorid_'], parent)
                    self.assertEqual(shot['props_']['animprops_']['time_'], 198)
                    self.assertEqual(births[oid]['animprops_']['animid_'], variant)
                    self.assertIn((1, oid), snapshot.objects)
                    self.assertEqual(sum(update.get('playsound_', {}).get('soundname_') == 'sc.bombdrop'
                        for message in sent for update in message.get('worldupdate_', {}).get('objectupdates_', [])
                        if update['objid_'] == oid), 1)
        self.assertEqual(phases, {1, 2})

    def test_sniper_keeps_integer_launch_facing_after_velocity_quantization(self):
        host, snapshot, sent, _births = actor_host()
        parent = host._create('sc.sniper', (0, 0), changes=dict(facing=180))
        # Native integer facing161 produces (-93000,31000)mp/s. Rebuilding
        # facing from that quantized path would change its image to162.
        host.enemies._emit(dict(owner=parent, kind='sliver', angle=math.radians(161)))
        oid, shot = next(iter(host.enemies.projectiles.shots.items()))
        self.assertEqual((shot['vx'], shot['vy']), (-93000, 31000))
        host.step(.05)
        wire = creates(sent, 'sc.sliver')[0]
        self.assertEqual(wire['props_']['facing_'], 161)
        self.assertEqual(snapshot.properties_at((1, oid))['facing_'], 161)
        self.assertEqual(host.actors[oid]['props']['facing'], 161)
        sounds = [update['playsound_']['soundname_'] for message in sent
                  for update in message.get('worldupdate_', {}).get('objectupdates_', [])
                  if update['objid_'] == oid and 'playsound_' in update]
        self.assertEqual(sounds, ['sc.sliver'])

    def test_explicit_zero_facing_is_preserved_by_generic_projectile_factory(self):
        host, snapshot, sent, _births = actor_host()
        oid = host.enemies.projectiles.spawn('sc.sliver', (0, 0), (0., 1000.), 0,
                                             changes=dict(facing=0))
        host.step(.05)
        self.assertEqual(creates(sent, 'sc.sliver')[0]['props_']['facing_'], 0)
        self.assertEqual(snapshot.properties_at((1, oid))['facing_'], 0)

    def test_death_after_first_release_cancels_unreleased_volley(self):
        for vid, child, steps in (('sc.bomber', 'sc.shard', 7),
                                  ('sc.sniper', 'sc.sliver', 9)):
            with self.subTest(vid=vid):
                host, snapshot, sent, _births = actor_host()
                parent = start_volley(host, vid)
                for _ in range(steps):
                    host.step(.05)
                self.assertEqual(len(creates(sent, child)), 1)
                host.feedback(dict(kind='damage', ownerid=1, victim=parent,
                                   value=1000000, event_id='kill'))
                self.assertFalse([event for event in host.enemies.pending if event['owner'] == parent])
                for _ in range(25):
                    host.step(.05)
                self.assertEqual(len(creates(sent, child)), 1)
                self.assertNotIn((1, parent), snapshot.objects)
                deaths = [update['destroy_'] for message in sent
                          for update in message.get('worldupdate_', {}).get('generalupdates_', [])
                          if update.get('destroy_', {}).get('objid_') == parent]
                self.assertEqual([death['reason_'] for death in deaths], [2])


if __name__ == '__main__':
    unittest.main()
