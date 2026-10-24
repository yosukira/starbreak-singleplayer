"""Assassin departure, full native MOVE_TO and arrival through actual wire clocks."""
import base64
import unittest

from test_sc_ghost_volley_fidelity import actor_host
from localserver.maps.sc_run.abilities import center
from localserver.maps.sc_run.physics import clear, rect


def assassin(target_x=416):
    host, snapshot, sent, _births = actor_host()
    oid = host._create('sc.assassin', (0, 0), changes=dict(hitpoints=392))
    host.enemies.add_actor(oid)
    player = dict(xmp=target_x*256, ymp=0, wmp=32*256, hmp=64*256, alive=True)
    host.players_provider = {7: player}
    state = host.enemies.units[oid]
    state.update(active=True, target=7, target_point=center(player))
    host.enemies._assassin_ghost(oid, state)
    return host, snapshot, sent, oid, state


def updates(sent, oid):
    return [update for message in sent
            for update in message.get('worldupdate_', {}).get('objectupdates_', [])
            if update['objid_'] == oid]


def moves(host, sent, oid):
    return [(update, host.layouts.decode('Linear', base64.b64decode(
        update['modifyprops_']['messageval_']['raw'])))
        for update in updates(sent, oid)
        if update.get('modifyprops_', {}).get('property_') == 42]


class ScAssassinVisibleTransitionTests(unittest.TestCase):
    def assert_snapshot(self, host, snapshot, oid):
        for actual, expected in zip(snapshot.position((1, oid)), host.actors[oid]['pos']):
            self.assertAlmostEqual(actual, expected, delta=1.)

    def test_four_hundred_pixel_leg_has_native_intermediate_positions(self):
        host, snapshot, sent, oid, state = assassin()
        host.step(.05)
        initial = snapshot.properties_at((1, oid))
        self.assertEqual(initial['vid_'], 'sc.eassassin')
        self.assertEqual((initial['animprops_']['animid_'], initial['animprops_']['time_']), (1, 264))
        self.assertNotIn('hitpoints_', initial)
        for _ in range(5):
            host.step(.05)
        self.assertEqual(host.actors[oid]['pos'], [0, 0])
        self.assertEqual(moves(host, sent, oid), [])
        host.step(.05)
        command, linear = moves(host, sent, oid)[0]
        self.assertEqual(command['offset_'], sent[-1]['worldupdate_']['dt_'])
        self.assertEqual((linear['xmp_'], linear['ymp_']), (400*256, 0))
        self.assertGreaterEqual(linear['dt_'], 264)
        self.assertEqual(snapshot.position((1, oid)), (0., 0.))
        positions = []
        for _ in range(5):
            host.step(.05)
            positions.append(snapshot.position((1, oid))[0])
            self.assertEqual(snapshot.properties_at((1, oid))['vid_'], 'sc.eassassin')
            self.assert_snapshot(host, snapshot, oid)
        self.assertTrue(all(0 < value < 400*256 for value in positions))
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(moves(host, sent, oid)), 1)
        self.assertFalse(any('movetoxmp_' in update for update in updates(sent, oid)),
                         'Per-frame position corrections must not replace the full native trajectory')
        # The 264ms leg ends inside the next frame, not on a 50ms tick.
        host.step(.013)
        self.assertLess(snapshot.position((1, oid))[0], 400*256)
        self.assert_snapshot(host, snapshot, oid)
        host.step(.002)
        self.assertEqual(snapshot.position((1, oid)), (400*256., 0.))
        self.assertEqual(host.actors[oid]['pos'], [400*256., 0.])
        self.assertEqual(snapshot.properties_at((1, oid))['vid_'], 'sc.eassassin')
        host.step(.05)
        self.assertEqual(snapshot.position((1, oid)), (400*256., 0.))
        self.assert_snapshot(host, snapshot, oid)
        self.assertEqual(len(moves(host, sent, oid)), 1)

    def test_native_path_crosses_wall_then_arrives_with_injury_and_burst_animation(self):
        host, snapshot, sent, oid, state = assassin(850)
        host._create('sc.wall', (300*256, -256*256), changes=dict(
            wmp=100*256, hmp=768*256, terraintype=1))
        inside = False
        for _ in range(80):
            host.step(.05)
            self.assert_snapshot(host, snapshot, oid)
            if not clear(host, rect(host.actors[oid]), (oid,)):
                inside = True
                props = snapshot.properties_at((1, oid))
                self.assertEqual(props['vid_'], 'sc.eassassin')
                self.assertEqual(props['blocktype_'], 0)
                self.assertNotIn('hitpoints_', props)
            if host.enemies.counts['bursts']:
                break
        self.assertTrue(inside, 'This actual native MOVE_TO must visibly pass through the wall')
        self.assertEqual(host.enemies.counts['bursts'], 1)
        props = snapshot.properties_at((1, oid))
        self.assertEqual(props['vid_'], 'sc.assassin')
        self.assertEqual(props['hitpoints_'], 392)
        self.assertEqual(props['blocktype_'], 1)
        self.assertEqual((props['animprops_']['animid_'], props['animprops_']['time_']), (2, 396))
        self.assertTrue(clear(host, rect(host.actors[oid]), (oid,)))
        self.assertEqual(len(host.enemies.projectiles.shots), 8)
        sounds = [update['playsound_']['soundname_'] for update in updates(sent, oid) if 'playsound_' in update]
        self.assertEqual(sounds, ['sc.ghost', 'sc.burst'])

    def test_move_started_during_real_tick_shares_its_wire_end_boundary(self):
        host, snapshot, sent, _births = actor_host()
        oid = host._create('sc.assassin', (0, 0), changes=dict(hitpoints=392))
        host.enemies.add_actor(oid)
        player = dict(xmp=416*256, ymp=0, wmp=32*256, hmp=64*256, alive=True)
        host.players_provider = {7: player}
        state = host.enemies.units[oid]
        state.update(active=True, target=7, target_point=center(player), next=0.)
        for _ in range(20):
            host.step(.05)
            if moves(host, sent, oid):
                break
        self.assertEqual(len(moves(host, sent, oid)), 1)
        self.assertEqual(snapshot.position((1, oid)), (0., 0.))
        self.assert_snapshot(host, snapshot, oid)
        start = state['ghost_motion']['start']
        self.assertGreaterEqual(start, .3)
        self.assertLessEqual(start, .355)
        host.step(.05)
        self.assertGreater(snapshot.position((1, oid))[0], 0.)
        self.assertLess(snapshot.position((1, oid))[0], 400*256)
        self.assert_snapshot(host, snapshot, oid)


if __name__ == '__main__':
    unittest.main()
