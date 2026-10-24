"""Scout retry identity, committed headings and inertia through native deltas."""
import math
import unittest

from test_sc_ghost_volley_fidelity import actor_host
from localserver.maps.sc_run.abilities import center


def player(x, y, alive=True):
    return dict(xmp=x*256, ymp=y*256, wmp=32*256, hmp=64*256, alive=alive)


def cues(sent, oid):
    return [update for message in sent
            for update in message.get('worldupdate_', {}).get('objectupdates_', [])
            if update['objid_'] == oid
            and update.get('playsound_', {}).get('soundname_') == 'sc.scoutattack']


def blocked_scout():
    host, snapshot, sent, _births = actor_host()
    host._create('sc.wall', (-2000*256, 160*256), changes=dict(
        wmp=4000*256, hmp=64*256, terraintype=1))
    oid = host._create('sc.scout', (0, 0), changes=dict(hitpoints=750, damage=35))
    host.enemies.add_actor(oid)
    chosen = player(-300, 400)
    host.players_provider = {7: chosen}
    state = host.enemies.units[oid]
    state.update(active=True, phase='chase', target=7,
                 target_point=center(chosen), next=0.)
    host.enemies._weapons(oid, state, chosen)
    for _ in range(20):
        host.step(.05)
        if state.get('scout_phase') == 'retry':
            break
    if state.get('scout_phase') != 'retry':
        raise AssertionError('Fixture must hit the early floor during its first dive')
    return host, snapshot, sent, oid, state


class ScScoutRetryIdentityTests(unittest.TestCase):
    def assert_snapshot(self, host, snapshot, oid):
        for actual, expected in zip(snapshot.position((1, oid)), host.actors[oid]['pos']):
            self.assertAlmostEqual(actual, expected, delta=1.)

    def next_cue(self, host, sent, oid):
        for _ in range(20):
            host.step(.025)
            if len(cues(sent, oid)) >= 2:
                return
        self.fail('The committed retry should issue its cue')

    def test_retry_refreshes_same_player_and_turns_facing_before_velocity(self):
        host, snapshot, sent, oid, state = blocked_scout()
        self.assertEqual(state['dive_target_key'], 7)
        first_heading = state['dive_heading']
        deadline = state['next']
        old_x = host.actors[oid]['pos'][0]
        self.assertLess(state['vx'], -50000.)
        chosen = player(600, 400)
        host.players_provider[7] = chosen
        host.step(.025)
        self.assertLess(host.room_time, state['retry_at'])
        self.assertLess(host.actors[oid]['pos'][0], old_x-1000.)
        self.assertEqual(len(cues(sent, oid)), 1)
        self.assert_snapshot(host, snapshot, oid)
        self.next_cue(host, sent, oid)
        self.assertNotEqual(state['dive_heading'], first_heading)
        self.assertGreater(state['dive_heading'][0], 0.)
        self.assertGreater(state['dive_heading'][1], 0.)
        self.assertEqual(state['dive_target'], center(chosen))
        self.assertEqual(snapshot.properties_at((1, oid))['facing_'], 0)
        self.assertLess(state['vx'], 0., 'New right-facing cue must not instantly reverse leftward inertia')
        self.assertEqual(state['next'], deadline)
        self.assertEqual(host.enemies.counts['attacks'], 1)
        self.assert_snapshot(host, snapshot, oid)

    def test_retry_keeps_selected_identity_outside_range_after_scan_picks_another(self):
        host, snapshot, sent, oid, state = blocked_scout()
        deadline = state['next']
        chosen = player(2500, 400)
        host.players_provider = {7: chosen, 9: player(-200, 350)}
        state['scan'] = 0.
        self.next_cue(host, sent, oid)
        self.assertEqual(state['target'], 9, 'The ordinary scanner may already have changed its own target')
        self.assertEqual(state['dive_target_key'], 7)
        self.assertEqual(state['dive_target'], center(chosen))
        self.assertGreater(state['dive_heading'][0], 0.)
        self.assertGreater(math.dist(center(host.actors[oid]), center(chosen)), 900*256)
        self.assertEqual(snapshot.properties_at((1, oid))['facing_'], 0)
        self.assertEqual(state['next'], deadline)
        self.assertEqual(host.enemies.counts['attacks'], 1)

    def test_missing_or_dead_selected_player_cancels_without_using_neighbour(self):
        for absent in ('left', 'dead'):
            with self.subTest(absent=absent):
                host, snapshot, sent, oid, state = blocked_scout()
                deadline = state['next']
                host.players_provider = {9: player(100, 400)}
                if absent == 'dead':
                    host.players_provider[7] = player(600, 400, alive=False)
                state['scan'] = 0.
                host.step(.05)
                self.assertEqual(state['scout_phase'], 'recover')
                self.assertEqual(len(cues(sent, oid)), 1)
                self.assertEqual(state['next'], deadline)
                self.assertEqual(state['dive_target_key'], 7)
                self.assert_snapshot(host, snapshot, oid)

    def test_selected_player_above_cancels_upward_retry_as_local_policy(self):
        host, snapshot, sent, oid, state = blocked_scout()
        host.players_provider[7] = player(600, -200)
        deadline = state['next']
        for _ in range(20):
            host.step(.025)
            if state['scout_phase'] == 'recover':
                break
        self.assertEqual(state['scout_phase'], 'recover')
        self.assertEqual(len(cues(sent, oid)), 1)
        self.assertEqual(state['next'], deadline)
        self.assert_snapshot(host, snapshot, oid)

    def test_retry_waiting_horizontal_motion_is_clipped_by_wall(self):
        host, snapshot, sent, oid, state = blocked_scout()
        wall_right = round(host.actors[oid]['pos'][0])-256
        host._create('sc.wall', (wall_right-8192, -100*256), changes=dict(
            wmp=8192, hmp=260*256, terraintype=1))
        host.step(.025)
        self.assertEqual(state['scout_phase'], 'retry')
        self.assertGreaterEqual(host.actors[oid]['pos'][0], wall_right-1.)
        self.assertEqual(state['vx'], 0.)
        self.assertEqual(len(cues(sent, oid)), 1)
        self.assert_snapshot(host, snapshot, oid)


if __name__ == '__main__':
    unittest.main()
