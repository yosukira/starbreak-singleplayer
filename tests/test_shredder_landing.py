"""User-requested landing cooldown, using actual shared collision and AI paths."""
import random,unittest
from types import SimpleNamespace as NS
from localserver.live import constants
from localserver.live.terrain import GroundMotion
from localserver.live.enemies import Enemy,BEHAVIOUR,SHREDDER_VIDS
from localserver.live.room import BossRoom
from localserver.live.bridge import PlayerState
from localserver.maps.egy_run.enemies import ExplorationEnemies

def room(deck=False):
 rows=[dict(objid_=800,props_=dict(xmp_=-1000*256,ymp_=0,wmp_=3000*256,hmp_=20*256,terraintype_=2))]
 if deck:rows.append(dict(objid_=801,props_=dict(xmp_=-500*256,ymp_=-200*256,wmp_=1500*256,hmp_=20*256,terraintype_=2)))
 return BossRoom(rows,{})

def unit(vid=None,drop=0,deck=False):
 vid=vid or next(iter(sorted(SHREDDER_VIDS)));stats=BEHAVIOUR[vid];w,h=stats['size']
 r=room(deck);u=Enemy(999,vid,stats,0,-h-(200*256 if deck else 0)-drop,w,h,rng=random.Random(11),room=r)
 return r,u

def land(u,now=10):
 for _ in range(300):
  now+=1/120;u.step(1/120,None,now)
  if u.motion.grounded:return now
 raise AssertionError('Real collision solver never landed')

class LandingCooldownTests(unittest.TestCase):
 def test_initially_supported_melee_starts_cooldown(self):
  for vid in SHREDDER_VIDS:
   with self.subTest(vid=vid):
    _,u=unit(vid);self.assertTrue(u.motion.grounded)
    self.assertEqual(u.next_jump,constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_spawn_drop_lands_then_full_cooldown(self):
  for vid in SHREDDER_VIDS:
   with self.subTest(vid=vid):
    _,u=unit(vid,drop=100*256);self.assertFalse(u.motion.grounded)
    t=land(u);self.assertAlmostEqual(u.next_jump,t+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_initial_support_binding_uses_spawn_time_not_map_zero(self):
  _,u=unit();u.step(.01,None,120)
  self.assertAlmostEqual(u.next_jump,120+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_deck_retraction_and_floor_landing_restarts_cooldown(self):
  for vid in SHREDDER_VIDS:
   with self.subTest(vid=vid):
    r,u=unit(vid,deck=True);first=u.motion.landing_serial
    r.object(801).set(terraintype_=0);t=land(u)
    self.assertEqual(u.motion.support,800);self.assertGreater(u.motion.landing_serial,first)
    self.assertAlmostEqual(u.next_jump,t+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_landing_after_a_jump_restarts_instead_of_using_takeoff_time(self):
  _,u=unit();u.next_jump=0;u.motion.jump(60000,u);t=land(u)
  self.assertAlmostEqual(u.next_jump,t+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_ship_return_early_branch_also_restarts_on_landing(self):
  _,u=unit(drop=100*256);u.ship_jump_until=99;u.ship_jump_vx=0
  t=land(u);self.assertAlmostEqual(u.next_jump,t+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)

 def test_remaining_grounded_does_not_refresh_or_extend_longer_deadline(self):
  _,u=unit();u.step(1/120,None,1/120);deadline=u.next_jump;serial=u.motion.landing_serial
  for n in range(240):u.step(1/120,None,(n+1)/120)
  self.assertEqual(u.next_jump,deadline);self.assertEqual(u.motion.landing_serial,serial)
  _,u=unit(drop=100*256);u.next_jump=100;land(u);self.assertEqual(u.next_jump,100)

 def test_actual_pursuit_jump_waits_and_resumes_after_deadline(self):
  _,u=unit(drop=30*256);t=land(u);deadline=u.next_jump
  p=PlayerState(objid=1,worldid=2,xmp=int(u.x+100*256),ymp=int(u.y-100*256),wmp=7680,hmp=u.h,hp=80,alive=True)
  u.step(0,p,deadline-.01);self.assertFalse(u.motion.jumping)
  u.step(.01,p,deadline+.01);self.assertTrue(u.motion.jumping)

 def test_non_melee_monster_keeps_existing_jump_behavior(self):
  _,u=unit('hulk.scavengergunman',drop=100*256);land(u);self.assertEqual(u.next_jump,0)

 def test_exploration_controller_landing_uses_same_cooldown(self):
  # Run the actual wrapper/collision path for inactive, recovery and launch
  # stepping alike; target acquisition is outside this isolated fixture.
  r=room();b=NS(x=0.,y=-60*256.,w=10*256,h=20*256)
  m=GroundMotion(r);e=object.__new__(ExplorationEnemies);e.now=8.
  state=dict(motion=m,body=b,family='melee',jump_after=0.)
  e._advance_unit=lambda oid,s,dt:m.step(b,dt,0,e.now)
  for _ in range(200):
   e.now+=.01;e._step_unit(1,state,.01)
   if m.grounded:break
  self.assertTrue(m.grounded);self.assertAlmostEqual(state['jump_after'],e.now+constants.SHREDDER_PURSUIT_JUMP_INTERVAL)
  deadline=state['jump_after']
  for _ in range(20):e.now+=.01;e._step_unit(1,state,.01)
  self.assertEqual(state['jump_after'],deadline)

if __name__=='__main__':unittest.main(verbosity=2)
