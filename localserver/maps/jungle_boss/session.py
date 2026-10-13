'Native jungle_boss host driven by live AI.'
import copy
import heapq
import math
import random
import threading
import time
from pathlib import Path

from ... import pb_layout
from ...live.bridge import PlayerBridge
from ...live.wire import DeltaBatch
from ...supply import SupplyDrops
from ..jungle_common.session import JungleRoomActors
from . import params
from .controller import JungleBossController
from .scene import JungleBossPack, normalize, motion_position

def roll_create_world(blob, seed):
    """Server hook: rebuild this run's CreateWorld with a rolled room."""
    pack = JungleBossPack()
    rolled = pack.rolled_world(seed)
    return bytes.fromhex(rolled) if rolled else None

DESTROY_INTERNAL = 1
DESTROY_HITPOINTS = 2
DESTROY_LIFETIME = 6

class JungleBossLiveSession(JungleRoomActors):
    fauna = params
    HZ = 60.
    NATIVE_BOSS_HP = params.SOLO_HP
    PART_TICK_S = 0.05
    CHAIN_TICK_S = 0.07

    def __init__(self, server, channel, options, room=None, log=None, bridge=None,
                 pack=None, seed=None, players=None):
        self.server, self.channel, self.options, self.room = server, channel, options, room
        self.log = log or (lambda message: None)
        self.layouts = getattr(server, 'layouts', None) or pb_layout.default_layouts()
        root = Path(getattr(options, 'create_world_hex', '') or '').parent
        self.pack = pack or JungleBossPack(root if (root / 'programs.json').exists() else None)

        roll_seed = getattr(options, 'room_roll_seed', None)
        if roll_seed is not None and not getattr(self.pack, 'run_objects', None):
            self.pack.rolled_world(int(roll_seed))
        self.worldid = int(getattr(options, 'worldid', 1) or 1)
        self.batch = DeltaBatch(self.layouts, self.worldid)
        self.bridge = bridge or PlayerBridge(int(getattr(options, 'bridge_port', 21550) or 21550),
                                             log=self.log)
        self.owns_bridge = bridge is None

        self.seed = (int(roll_seed) if seed is None and roll_seed is not None else
                     random.SystemRandom().randrange(1 << 30) if seed is None else int(seed))
        self.players = max(1, int(players or getattr(options, 'players', 1) or 1))
        self._stop, self._thread = threading.Event(), None
        self.prepare()

    def prepare(self):
        self.supplies = SupplyDrops(self, 'jungle_boss')
        self.phase = 'dormant'
        self.room_time = 0.
        self.controller, self.player = None, None
        self.actors, self.retired, self.queue = {}, set(), []
        self.sequence, self.frames = 0, 0
        self.events = []
        self.rng = random.Random(self.seed)
        self.counts = dict(spawns=0, npcs=0, shots=0, worms=0, pods=0, hoppers=0, hives=0,
                           native_damage=0, native_destroy=0, splits=0, cap_skips=0,
                           wall_hits=0, lifetime=0, pincer_slashes=0, boss_passes=0)
        self._next_id = max(map(int, self.pack.templates)) + 1000
        self._wire_ms, self._sent_ms = 0., 0
        self._frame_start = 0.
        self._part_at, self._pincer_at, self._pincer_last = 0., 0., 0.
        self._pass_platforms = None
        self.initial_worm_heads = []
        self.initial_worms = []
        self.initial_skitters = []
        self.boss = self.pack.boss()
        self.offsets = self.pack.chain_offsets()
        self.solids = self._solid_rects()
        self.platforms = self._platform_rects()
        self.bounds = tuple(self.pack.scene.get('bounds_mp') or (-262144, 90112, 1024000, 712704))
        floors = [p for p in self.pack.templates.values()
                  if p.get('terraintype') == 1 and p.get('damage', 0) >= 1000]
        self.pit_y = min((p['ymp'] for p in floors), default=self.bounds[3])
        markers=[p for p in self.pack.templates.values()
                 if p.get('terraintype')==4 and 'xmp'in p and 'ymp'in p]
        if len(markers)>=3:
            self.bounds=(min(p['xmp']for p in markers),min(p['ymp']for p in markers),
                         max(p['xmp']for p in markers),max(p['ymp']for p in markers))
        self.batch.clear()
        rolled = getattr(self.pack, 'run_objects', None)
        for identity, props in (rolled or self.pack.templates).items():
            self._add_actor(int(identity), props, 0., kind='static')
        self._promote_room()
        if rolled is None:

            self._configure_worms()
            self._configure_hives()
            self._configure_skitters()

        self.boss_hp = float(params.SOLO_HP) * float(params.COOP_HP_MULTIPLIER)
        self._boss_hp_at = 0.
        self._boss_hp_sent = int(round(self.boss_hp))
        for part in self.boss['parts']:
            self.batch.modify(int(part), 'HITPOINTS', intval=int(round(self.boss_hp)))
            self.batch.modify(int(part), 'MAXHITPOINTS', intval=int(round(self.boss_hp)))
        self.pincer_pairs = self._configure_pincers()
        self.pincer_timers = {}
        for pair in self.pincer_pairs:

            self.pincer_timers[int(pair['left'])] = dict(
                remain=self.rng.uniform(0.0, params.PINCER_CD_S),
                pending=None, armed=False)

    def _promote_room(self):
        head = int(self.boss['head'])
        for part in self.boss['parts']:
            row = self.actors.get(int(part))
            if row is None:
                continue
            row['kind'] = 'boss' if int(part) == head else 'part'
            row['parent'] = head
        rolled = getattr(self.pack, 'run_objects', None)
        if rolled is not None:

            self.initial_worm_heads = []
            self.initial_worms = []
            for worm in getattr(self.pack, 'run_worms', []):
                head = int(worm['head'])
                self._attach_worm(head, [int(v) for v in worm['body']], int(worm['tail']), at=0.)
                self.initial_worm_heads.append(head)
                self.initial_worms.append(dict(head=head, body=[int(v) for v in worm['body']],
                                               tail=int(worm['tail'])))
            for oid in getattr(self.pack, 'run_hives', []):
                self._activate_hive(int(oid), int(rolled[int(oid)].get('hitpoints')
                                                  or params.HIVE_HP))
            for oid in getattr(self.pack, 'run_skitters', []):
                self._activate_skitter(int(oid))
                self.initial_skitters.append(int(oid))
            return

        for oid in self.pack.scene.get('hives') or []:
            self._activate_hive(int(oid),
                                int(self.pack.templates[str(oid)].get('hitpoints') or params.HIVE_HP))
        for oid in self.pack.scene.get('skitters') or []:
            self._activate_skitter(int(oid))

    def _retire_chain(self, worm, reason, at):
        ids = [int(worm['head'])]
        ids += [int(v) for v in worm.get('body') or []]
        ids.append(int(worm['tail']))
        for oid in ids:
            if oid in self.actors:
                self._retire(oid, reason, at)

    def _configure_worms(self):
        'Roll the pre-fight eye-worm chains over the runtime slot pool.'
        for worm in self.pack.scene.get('initial_worms') or []:
            self._retire_chain(worm, DESTROY_INTERNAL, 0.)
        slots = list(params.SNAKE_SLOT_MP)
        self.rng.shuffle(slots)
        lo, hi = (int(v) for v in params.SNAKE_INIT_RANGE)
        count = self.rng.randint(lo, min(hi, len(slots)))
        self.initial_worm_heads = []
        self.initial_worms = []
        for x, y in slots[:count]:
            head = self._spawn_worm(0., position=(float(x), float(y)))
            if head is not None:
                head = int(head)
                self.initial_worm_heads.append(head)
                chain = list(self.actors[head]['chain'])
                self.initial_worms.append(dict(head=head, body=chain[1:-1], tail=chain[-1]))

    def _configure_hives(self):
        """Roll platforms, then each platform's hive count and positions."""
        recorded = [int(oid) for oid in (self.pack.scene.get('hives') or [])]
        template = {}
        for oid in recorded:
            row = self.actors.get(oid)
            if row is not None and not template:
                template = dict(row['props'])
        for oid in recorded:
            if oid in self.actors:
                self._retire(oid, DESTROY_INTERNAL, 0.)
        if not self.platforms:
            return
        lo, hi = (int(v) for v in params.HIVE_PLATFORM_RANGE)
        count = self.rng.randint(lo, min(hi, len(self.platforms)))
        for platform in self.rng.sample(self.platforms, count):
            for hx,hy in self.pack.hive_positions(platform,self.rng):
                hid = self._create('jungle.hopperhive',
                                   dict(template, xmp=hx, ymp=hy),
                                   0., kind='npc', hp=params.HIVE_HP)
                self._activate_hive(hid, params.HIVE_HP)

    def _configure_skitters(self):
        """Roll 4-10 platforms; every worm sits centred on its platform top."""
        recorded = [int(oid) for oid in (self.pack.scene.get('skitters') or [])]
        for oid in recorded:
            if oid in self.actors:
                self._retire(oid, DESTROY_INTERNAL, 0.)
        if not self.platforms:
            return
        lo, hi = (int(v) for v in params.SKITTER_INIT_RANGE)
        count = self.rng.randint(lo, min(hi, len(self.platforms)))
        props = self.spawn_props('jungle.skitterup')
        for platform in self.rng.sample(self.platforms, count):
            px, py, pw, ph = platform
            oid = self._create('jungle.skitterup',
                               dict(props, xmp=int(px + pw/2 - 7680), ymp=int(py - 12288),
                                    hitpoints=params.SKITTER_HP), 0., hp=params.SKITTER_HP)
            self._activate_skitter(oid)
            self.initial_skitters.append(int(oid))

    def _configure_pincers(self):
        """Roll 2-10 platforms and build a synchronized left/right pair on each."""
        if getattr(self.pack, 'run_pincers', None) is not None:

            return [dict(pair) for pair in self.pack.run_pincers]
        recorded = self.pack.scene.get('pincers') or []
        if not recorded or not self.platforms:
            return []
        template = dict(self.pack.templates.get(str(recorded[0]['left'])) or {})
        width = int(template.get('wmp') or params.PINCER_SPRITE_W_MP)
        height = int(template.get('hmp') or params.PINCER_SPRITE_H_MP)

        for pair in recorded:
            for oid in (int(pair['left']), int(pair['right'])):
                if oid in self.actors:
                    self._retire(oid, DESTROY_INTERNAL, 0.)
        count = self.rng.randint(int(params.PINCER_COUNT_RANGE[0]),
                                min(int(params.PINCER_COUNT_RANGE[1]), len(self.platforms)))
        pairs = []
        for platform in self.rng.sample(self.platforms, count):
            px, py, pw, _ph = platform
            left_x = int(px)
            right_x = int(px + pw - width)
            y = int(py - height)
            left = self._create('jungle.pincer',
                                dict(template, xmp=left_x, ymp=y,
                                     z=params.PINCER_Z,
                                     facing=params.PINCER_LEFT_FACING),
                                0., kind='static')
            right = self._create('jungle.pincer',
                                 dict(template, xmp=right_x, ymp=y,
                                      z=params.PINCER_Z,
                                      facing=params.PINCER_RIGHT_FACING),
                                 0., kind='static')
            pairs.append(dict(left=left, right=right,
                              center_x=(left_x + right_x + width) / 2.,
                              y=y, gap=right_x - left_x, platform=list(platform)))
        return pairs

    def _solid_rects(self):
        rows = []
        for _identity, props in self.pack.templates.items():
            if props.get('terraintype') not in (1, 2):
                continue
            if props.get('xmp') is None or props.get('ymp') is None:
                continue
            rows.append((props['xmp'], props['ymp'], props.get('wmp', 0), props.get('hmp', 0)))
        return rows

    def _platform_rects(self):
        rows = []
        for _identity, props in self.pack.templates.items():
            if props.get('vid') != 'jungle.ledge':
                continue
            rows.append((props.get('xmp', 0), props.get('ymp', 0),
                         props.get('wmp', 0), props.get('hmp', 0)))
        return sorted(rows, key=lambda r: (r[1], r[0]))

    def start(self):
        if self.owns_bridge:
            self.bridge.start()
        self.prepare()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self.log('jungle_boss live session started (live AI, seeded spawns)')

    def stop(self, stop_bridge=True):
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.)
        if stop_bridge and self.owns_bridge:
            self.bridge.stop()

    def _run(self):
        interval=1./self.HZ
        deadline=time.monotonic()
        previous=deadline-interval
        while not self._stop.is_set():
            now=time.monotonic()
            dt=min(.1,max(0.,now-previous))
            previous=now
            deadline += interval
            try:
                self.step(dt)
            except Exception:
                import traceback
                self.log('jungle_boss tick failed: ' + traceback.format_exc())
                self._stop.set()
                break
            self._stop.wait(max(0., deadline - time.monotonic()))
            if deadline < time.monotonic() - interval:
                deadline = time.monotonic()

    def _enqueue(self, at, kind, **values):
        self.sequence += 1
        heapq.heappush(self.queue, (at, self.sequence, dict(kind=kind, **values)))

    def position(self, objid, at=None):
        row = self.actors.get(objid)
        if row is None:
            return (0., 0.)
        at = self.room_time if at is None else at
        if row['kind'] in ('npc', 'projectile', 'fx', 'chain', 'part'):
            return tuple(row['pos'])
        move = row['move']
        if 'relativeto' in move:
            parent = move['relativeto'].get('objid')
            if parent in self.actors and parent != objid:
                x, y = self.position(parent, at)
                offset = move.get('to') or {}
                return x + offset.get('xmp', 0), y + offset.get('ymp', 0)
        return motion_position(row['start'], move, max(0., at - row['epoch']))

    def _move(self, objid, move, at):
        row = self.actors[objid]
        row['start'] = self.position(objid, at)
        row['move'], row['epoch'] = copy.deepcopy(move or {}), at

    def _command(self, objid, command, at, dt_ms=None):
        row = self.actors.get(objid)
        if row is None:
            return
        offset = self._offset(at)
        change = command.get('modifyprops')
        if change:
            prop = change['property']
            if prop in (19, 20):
                return
            values = {k: change[k] for k in ('intval', 'boolval', 'stringval') if k in change}
            if prop in (41, 49) and 'decoded' in change:
                message_type = 'Properties_Move' if prop == 41 else 'AnimationProperties'
                decoded = change['decoded']
                if prop == 41:
                    self._move(objid, decoded, at)
                values['message'] = (message_type, normalize(message_type, decoded, self.layouts))
            self.batch.modify(objid, prop, offset=offset, **values)
            if prop == 43:
                row['vid'] = change.get('stringval', '')
        if command.get('playsound'):
            self.batch.sound(objid, command['playsound']['soundname'], offset=offset)
        if 'movetoxmp' in command or 'movetoymp' in command:
            x, y = self.position(objid, at)
            target = (command.get('movetoxmp', x), command.get('movetoymp', y))
            duration = max(1, dt_ms or command.get('movetodt') or 50)
            self.batch.move_to(objid, int(target[0]), int(target[1]), dt=duration, offset=offset)
            self._move(objid, dict(to=dict(xmp=target[0], ymp=target[1], dt=duration)), at)

    def _attach_worm(self, head, body, tail, at=None):
        row = self.actors.get(head)
        if row is None:
            return
        row['kind'] = 'npc'
        row['npc'] = True
        row['hp'] = row['hp'] or params.SNAKE_HEAD_HP
        row['ai'] = dict(species='snake', vel=[0., 0.])
        links = [head] + list(body) + [tail]

        vx, vy = row['ai'].get('vel') or (0., 0.)
        if math.hypot(vx, vy) < 1:
            angle = math.radians(row.get('facing', 0))
            vx, vy = math.cos(angle), math.sin(angle)
        mag = math.hypot(vx, vy) or 1.
        ux, uy = vx/mag, vy/mag
        for index, oid in enumerate(links):
            link = self.actors.get(oid)
            if link is None:
                continue
            link['kind'] = 'npc' if index == 0 else 'chain'
            link['parent'] = head
            link['link_index'] = index
            link['tick_s'] = 0.
            if index:
                offset = params.SNAKE_LINK_MP*index
                link['pos'] = [row['pos'][0]-ux*offset, row['pos'][1]-uy*offset]

                self.batch.modify(
                    oid, 'MOVE',
                    message=('Properties_Move', {
                        'relativeto_': {'objid_': int(links[index-1])},
                        'follow_': {'maxdist_': int(round(params.SNAKE_LINK_MP/256.))},
                    }),
                    offset=self._offset(self.room_time if at is None else at))
        row['chain'] = links

    def _sync_actor(self,row,at,interval=None):
        if (row.get('ai')or{}).get('species')!='hopper':
            return super()._sync_actor(row,at,interval)
        ai=row['ai']
        state=(bool(ai.get('grounded')),bool(ai.get('birth_arc')),ai.get('dir'))
        if at<row['send_at'] and row.get('motion_state')==state:
            return
        x,y=row['pos']
        vx,vy=ai['vel']
        points=[{'xmp_':round(x),'ymp_':round(y),'dt_':1,'pos_':0,
                 'start_':{'xmp_':round(x),'ymp_':round(y)}}]

        for index in range(1,6):
            t=index*.02
            delay=min(t,ai.get('birth_delay',0))
            active=t-delay
            acceleration=min(active,max(0,(params.HOPPER_FALL_CAP_MP_S-vy)/params.HOPPER_GRAVITY_MP_S2))
            nx=x+vx*t
            ny=y+vy*(delay+acceleration)+.5*params.HOPPER_GRAVITY_MP_S2*acceleration**2
            ny+=params.HOPPER_FALL_CAP_MP_S*(active-acceleration)
            if ai.get('grounded'):
                nx,ny=x,y
            elif vy+params.HOPPER_GRAVITY_MP_S2*active>0:
                for px,py,pw,_ph in self.platforms:
                    if px<=nx+row['props']['wmp']/2<=px+pw and y+row['props']['hmp']<=py+1 and ny+row['props']['hmp']>=py:
                        ny=py-row['props']['hmp']
            points.append({'xmp_':round(nx),'ymp_':round(ny),'dt_':20})
        self.batch.modify(row['objid'],'MOVE',message=('Properties_Move',{'waypoint_':{'points_':points}}),offset=self._offset(at))
        row['motion_state']=state
        row['sent']=list(row['pos'])
        row['sent_at']=at
        row['send_at']=at+.08

    def _snake_cap(self):
        phase = self.controller.phase if self.controller else 1
        return int(params.SNAKE_ALIVE_CAP[params.phase_index(phase)])

    def _spawn_worm(self, at, position=None):
        alive = sum(1 for r in self.actors.values()
                    if r['ai'] and r['ai'].get('species') == 'snake')
        if alive >= self._snake_cap():
            self.counts['cap_skips'] += 1
            return None
        base = self.spawn_props('jungle.snakehead')
        body_props = self.spawn_props('jungle.snakebody')
        tail_props = self.spawn_props('jungle.snaketail')
        for props in (base, body_props, tail_props):
            props.pop('move', None)
            props.pop('lifetime', None)
        x, y = position if position else self._worm_spawn_point()
        head = self._create('jungle.snakehead', dict(base, xmp=int(x), ymp=int(y)), at,
                            kind='npc', hp=params.SNAKE_HEAD_HP)
        self.counts['worms'] += 1
        chain_ids = [head]
        for _ in range(params.SNAKE_BODY_COUNT):
            oid = self._create('jungle.snakebody', dict(body_props, xmp=int(x), ymp=int(y)), at,
                               kind='chain')
            chain_ids.append(oid)
        tail = self._create('jungle.snaketail', dict(tail_props, xmp=int(x), ymp=int(y)), at,
                            kind='chain')
        chain_ids.append(tail)
        self._attach_worm(head, chain_ids[1:-1], tail, at=at)
        cx = (self.bounds[0]+self.bounds[2])/2
        cy = (self.bounds[1]+self.bounds[3])/2
        dx, dy = cx-x, cy-y
        dist = math.hypot(dx, dy)
        if dist > 1:
            self.actors[head]['ai']['vel'] = [dx/dist*params.SNAKE_SPEED_MP_S,
                                              dy/dist*params.SNAKE_SPEED_MP_S]
        self.counts['npcs'] += 1
        return head

    def _worm_spawn_point(self, edge=None):
        """Wave worms crawl in from just outside one of the four room edges."""
        x0, y0, x1, y1 = self.bounds
        margin = self.rng.uniform(*params.SNAKE_SPAWN_MARGIN_RANGE_MP)
        edge = self.rng.randrange(4) if edge is None else edge
        if edge == 0:
            return (x0-margin,y0+self.rng.random()*(y1-y0))
        if edge == 1:
            return (x1+margin,y0+self.rng.random()*(y1-y0))
        if edge == 2:
            return (x0+self.rng.random()*(x1-x0),y0-margin)
        return (x0+self.rng.random()*(x1-x0),y1-self.rng.uniform(*params.SNAKE_BOTTOM_MARGIN_MP))

    def _platform_has_skitter(self, platform):
        for row in self.actors.values():
            ai = row.get('ai') or {}
            if (ai.get('species') == 'skitter' and ai.get('platform')
                    and tuple(ai['platform']) == tuple(platform)):
                return True
        return False

    def _spawn_skitter(self, at, platform=None):
        """One red worm at a platform centre (boss-pass roll, never random)."""
        alive = sum(r['npc'] for r in self.actors.values())
        if alive >= params.NPC_CAP or not self.platforms:
            self.counts['cap_skips'] += 1
            return None
        if platform is None:
            return None
        px, py, pw, ph = platform
        props = self.spawn_props('jungle.skitterup')
        props = dict(props, xmp=int(px + pw/2 - 7680), ymp=int(py - 12288),
                     hitpoints=params.SKITTER_HP)
        oid = self._create('jungle.skitterup', props, at, hp=params.SKITTER_HP)
        self._activate_skitter(oid)
        self.actors[oid]['ai']['platform'] = tuple(platform)
        self.actors[oid]['pos'] = [float(props['xmp']),float(props['ymp'])]
        self.counts['npcs'] += 1
        return oid

    def _roll_pass_skitters(self, at, head_x, head_y, direction, head_w=172032.):
        """Roll one red worm per real platform the sweep reaches.

        Keep the original per-pass roll, but only permit a platform and spawn
        rect inside a current body part's middle height band. A grazing slab
        edge or a distant passed head must never seed a worm. Each platform
        is rolled once per pass.
        """
        if self._pass_platforms is None:
            self._pass_platforms = set()
        phase = params.phase_index(self.controller.phase if self.controller else 1)
        chance = float(params.SKITTER_PASS_CHANCE[phase])
        for platform in self.platforms:
            key = tuple(platform)
            if key in self._pass_platforms:
                continue
            px, py, pw, ph = platform
            inset = params.SKITTER_PASS_Y_INSET_MP
            if py-12288 < head_y+inset or py+ph > head_y+head_w-inset:
                continue
            passed = ((head_x+head_w/2 >= px+pw/2) if direction>0
                      else (head_x+head_w/2 <= px+pw/2))
            if not passed:
                continue
            overlapping = any(
                row['pos'][0] <= px+pw/2-7680
                and row['pos'][0]+row['props'].get('wmp',head_w) >= px+pw/2+7680
                and row['pos'][1]+inset <= py-12288
                and row['pos'][1]+row['props'].get('hmp',head_w)-inset >= py+ph
                for oid in self.boss['parts']
                if (row:=self.actors.get(int(oid))) is not None)
            if not overlapping:
                continue
            self._pass_platforms.add(key)
            if self._platform_has_skitter(platform):
                continue
            if self.rng.random() < chance:
                self._spawn_skitter(at, platform=platform)

    def _controller_event(self, event):
        kind, at = event['kind'], event.get('at', self.room_time)
        if self.phase == 'defeated':
            return
        if kind == 'boss_activate':
            self.phase = 'sweep'

            for part in self.boss['parts']:
                self.batch.modify(int(part), 'HITPOINTS', intval=int(round(self.boss_hp)),
                                  offset=self._offset(at))
                self.batch.modify(int(part), 'MAXHITPOINTS', intval=int(round(self.boss_hp)),
                                  offset=self._offset(at))
            self._boss_hp_at = at
            self._boss_hp_sent = int(round(self.boss_hp))
            self._record('boss_activate', at, lane=event['lane'])
        elif kind == 'boss_pass':
            self._boss_pass(event, at)
        elif kind == 'boss_hold':
            self.phase = 'dwell'
        elif kind == 'phase':

            self._record('phase', at, phase=event['phase'])
            self.log('jungle_boss phase %d at %.1fs' % (int(event['phase']), at))
            self.batch.world_sound(params.PHASE_SOUND, offset=self._offset(at))
            self.batch.shake(int(params.PHASE_SHAKE[0]), int(params.PHASE_SHAKE[1]),
                             offset=self._offset(at))
        elif kind == 'snake_wave':
            edge=self.rng.randrange(4)
            for _ in range(event.get('count',1)):
                self._spawn_worm(at,position=self._worm_spawn_point(edge))
        elif kind == 'skitter_wave':
            self._spawn_skitter(at)
        elif kind == 'fireshot_volley':
            self._fireshot_volley(at,count=event.get('count'),aimed=event.get('aimed'))
        elif kind == 'boss_defeated':
            self._boss_defeated(at)
        self._record('controller', at, event=kind)

    def _boss_pass(self, event, at):
        head = int(self.boss['head'])
        (x0, y0), (x1, y1) = event['start'], event['end']
        duration_ms = max(1, int(round(event['duration_s'] * 1000.)))
        row = self.actors[head]
        row['start'] = (float(x0), float(y0))
        row['move'] = dict(to=dict(xmp=int(x1), ymp=int(y1), dt=duration_ms))
        row['epoch'] = at
        row['dir'] = int(event.get('dir') or (1 if x1 > x0 else -1))
        row['pos'] = [float(x0), float(y0)]
        row['pos_end'] = (float(x1), float(y1))
        row['pass_at'] = at
        row['pass_dur'] = max(1e-3, float(event['duration_s']))

        self._pass_platforms = set()

        first_pass = self.counts['boss_passes'] == 0
        speed = abs(x1-x0)/row['pass_dur']
        for part in self.boss['segments']+[self.boss['tail']]:
            link = self.actors.get(int(part))
            if link is None:
                continue
            maxgap = float(self.offsets.get(int(part), (params.PART_SPACING, 0))[0])
            if first_pass:
                link['pos'] = [float(x0),float(y0)]
                self.batch.modify(int(part), 'MOVE', message=('Properties_Move', {
                    'to_': {'xmp_': int(x0),'ymp_':int(y0),'dt_':1,'pos_':0,
                            'start_':{'xmp_':int(x0),'ymp_':int(y0)}}}),offset=self._offset(at))
            self._enqueue(at+maxgap/speed, 'boss_part_pass', objid=int(part),
                          start=(float(x0),float(y0)), end=(float(x1),float(y1)),
                          duration=row['pass_dur'], direction=row['dir'])
        self.batch.modify(head, 'MOVE', message=('Properties_Move', {
            'to_': {'xmp_': int(x1), 'ymp_': int(y1), 'dt_': duration_ms, 'pos_': 0,
                    'start_': {'xmp_': int(x0), 'ymp_': int(y0)}}}), offset=self._offset(at))
        self.batch.modify(head, 'FACING', intval=180 if row['dir'] < 0 else 0,
                          offset=self._offset(at))
        self.counts['boss_passes'] += 1
        self._record('boss_pass', at, lane=event['start'][1], dir=row['dir'],
                     duration=event['duration_s'])

    def _boss_part_pass(self, action, at):
        row = self.actors.get(action['objid'])
        if row is None:
            return
        x,y = action['start']
        ex,ey = action['end']
        row['part_motion'] = dict(start=(x,y),end=(ex,ey),at=at,duration=action['duration'])
        row['pos'] = [x,y]
        row['dir'] = action['direction']
        self.batch.modify(row['objid'],'MOVE',message=('Properties_Move',{
            'to_':{'xmp_':int(ex),'ymp_':int(ey),'dt_':max(1,round(action['duration']*1000)),
                   'pos_':0,'start_':{'xmp_':int(x),'ymp_':int(y)}}}),offset=self._offset(at))
        self.batch.modify(row['objid'],'FACING',intval=180 if row['dir']<0 else 0,
                          offset=self._offset(at))
        self._record('boss_part_pass',at,objid=row['objid'],direction=row['dir'],lane=y)

    @staticmethod
    def _part_position(row, at):
        move = row.get('part_motion')
        if not move:
            return tuple(row['pos'])
        fraction = max(0.,min(1.,(at-move['at'])/move['duration']))
        return tuple(a+(b-a)*fraction for a,b in zip(move['start'],move['end']))

    def _boss_head_position(self, at):
        head = int(self.boss['head'])
        row = self.actors[head]
        if 'pass_at' not in row:
            return tuple(row['pos'])
        elapsed = max(0., at - row['pass_at'])
        if row['pass_dur'] <= 0:
            return row['pos_end']
        frac = min(1., elapsed / row['pass_dur'])
        x0, y0 = row['start']
        x1, y1 = row['pos_end']
        return (x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac)

    def _boss_defeated(self, at):
        self.phase = 'defeated'
        if self.controller is not None:
            self.controller.state = 'defeated'

        if self.queue:
            self.queue = [item for item in self.queue
                          if (item[2] or {}).get('kind') not in ('controller', 'fireshot')]
            heapq.heapify(self.queue)

        for oid in self._head_hitbox_ids():
            self._retire(oid, DESTROY_INTERNAL, at)
        for part in self.boss['parts']:
            if int(part) in self.actors:
                self.batch.modify(int(part), 'HITBOXDAMAGE', intval=0,
                                  offset=self._offset(at))
        self.batch.shake(int(params.DEATH_SHAKE[0]), int(params.DEATH_SHAKE[1]),
                         offset=self._offset(at))
        x, y = self._boss_head_position(at)

        live_parts = [oid for oid in self.boss['parts'] if int(oid) in self.actors]
        for index in range(int(params.BOSSX_COUNT)):
            when = at + params.BOSS_DEATH_S * (index + self.rng.random()) / params.BOSSX_COUNT
            part = self.actors[self.rng.choice(live_parts)] if live_parts else None
            if part is not None:
                base = (part['pos'][0], part['pos'][1])
                spread = 96 * 256
                dx = self.rng.uniform(-spread, spread)
                dy = self.rng.uniform(-spread, spread)
            else:
                base, dx, dy = (x, y), 0., 0.
            self._enqueue(when, 'bossx', x=base[0], y=base[1], dx=dx, dy=dy,
                          part=part['objid'] if part is not None else None)
        head = self.actors.get(int(self.boss['head']))
        self._death_dir = int((head or {}).get('dir') or -1)
        self._death_speed = float(params.phase_speed(self.controller.phase if self.controller else 4))
        self._death_stopped = False
        for part in self.boss['parts']:
            row = self.actors.get(int(part))
            if row is None:
                continue
            self.batch.modify(int(part), 'HITPOINTS', intval=0,
                              offset=self._offset(at))
            self.batch.modify(int(part), 'INVULNERABLE', boolval=True,
                              offset=self._offset(at))
        self._enqueue(at + params.BOSS_DEATH_S, 'boss_vanish')
        self._record('boss_defeated', at)

    def _head_hitbox_ids(self):
        ids = []
        for oid, row in self.actors.items():
            props = row.get('props') or {}
            if props.get('draw') is False and float(props.get('damage') or 0) >= 1000000:
                ids.append(int(oid))
        return ids

    def _boss_vanish(self, at):

        self.batch.shake(0, offset=self._offset(at))
        for part in self.boss['parts']:
            if int(part) in self.actors:
                self._retire(int(part), DESTROY_LIFETIME, at)
        self.queue=[q for q in self.queue if q[2]['kind']!='boss_part_pass']
        heapq.heapify(self.queue)

    def _fireshot_volley(self, at, count=None, aimed=None):
        'Schedule a run of flowers, one out of the middle of a body part.'
        if self.actors.get(int(self.boss['head'])) is None:
            return
        phase = params.phase_index(self.controller.phase if self.controller else 1)
        if self.rng.random() < float(params.FIRESHOT_EMPTY_CHANCE[phase]):
            self.counts['empty_volleys'] = self.counts.get('empty_volleys', 0) + 1
            self._record('fireshot_empty', at, phase=phase + 1)
            return
        parts = [int(v) for v in self.boss['parts']]
        if not parts:
            return
        if aimed is None:
            aimed=self.rng.random()<params.FIRESHOT_AIM_CHANCE[phase]
        run=int(count) if count is not None else (1 if aimed else self.rng.randint(*params.FIRESHOT_BURST_SIZE[phase]))
        target=self._player_center()

        part=min(parts,key=lambda i:math.hypot(
            self.actors[i]['pos'][0]+self.actors[i]['props'].get('wmp',172032)/2-target[0],
            self.actors[i]['pos'][1]+self.actors[i]['props'].get('hmp',172032)/2-target[1]))
        for index in range(run):
            when = at + index * float(params.FIRESHOT_RUN_GAP_S)
            self._enqueue(when, 'fireshot', part=part,aimed=aimed)

    def _fireshot_shot(self, part, at, aimed=True):
        row = self.actors.get(int(part))
        head = self.actors.get(int(self.boss['head']))
        if row is None or head is None:
            return
        direction = int(row.get('dir') or head.get('dir') or -1)
        w = float(row['props'].get('wmp', params.BOSS_HEAD_WMP))
        h = float(row['props'].get('hmp', params.BOSS_HEAD_WMP))

        position=self._boss_head_position(at) if int(part)==int(self.boss['head']) else self._part_position(row,at)
        mx = position[0] + w / 2.-params.FIRESHOT_WMP[0]/2
        my = position[1] + h / 2.-params.FIRESHOT_WMP[0]/2
        target = self._player_center((mx+8192, my+8192))
        angle = (math.atan2(target[1]-my-8192,target[0]-mx-8192) if aimed else
                 math.radians((180 if direction<0 else 0)+self.rng.randint(-90,90)))
        speed = params.FIRESHOT_SPEED_MP_S[0]
        oid=self._spawn_shot('fireshot1', int(part), at, (mx, my),
                         (math.cos(angle) * speed, math.sin(angle) * speed),
                         facing=math.degrees(angle))

        if oid is not None:
            self.actors[oid]['split_facing'] = 180 if direction < 0 else 0
        self._record('flower_shot',at,objid=oid,part=part,aimed=aimed,
                     heading=math.degrees(angle),position=(mx,my))

    def _split_fireshot(self, row, at):
        spec = params.SHOTS.get(row['shot'])
        if not spec or not spec.get('children'):
            return
        index = spec['tier']
        speed = params.FIRESHOT_SPEED_MP_S[min(2, index + 1)]
        count = params.FIRESHOT_CHILDREN[index]
        spacing = params.FIRESHOT_SPACING_DEG[index]
        base = row.get('split_facing', row['facing'])

        shift = (row['props']['wmp']-params.FIRESHOT_WMP[index+1])/2
        position = (row['pos'][0]+shift, row['pos'][1]+shift)
        for slot in range(count):
            degrees = base + spacing * slot
            angle = math.radians(degrees)
            spawned = self._spawn_shot('fireshot%d' % (index + 2), row['objid'], at, position,
                                       (math.cos(angle) * speed, math.sin(angle) * speed),
                                       facing=degrees)
            if spawned is None:
                break
            if slot == 0:
                self.batch.sound(spawned, 'jungle.shotsplit', offset=self._offset(at))
        self.counts['splits'] += 1

    def _npc_tick(self, row, dt, at):
        ai = row['ai'] or {}
        species = ai.get('species')
        if species == 'snake':
            self._snake_tick(row, dt, at)
        elif species == 'skitter':
            self._skitter_tick(row, dt, at)
        elif species == 'pod':
            self._pod_tick(row, at)
        elif species == 'hive':
            self._hive_tick(row, at)
        elif species == 'hopper':
            self._hopper_tick(row, dt, at)

    def _snake_tick(self, row, dt, at):
        """Lock a direction for 1–2 s; crossing the target never ends a leg."""
        ai = row['ai']
        player = self._target_player((row['pos'][0]+row['props'].get('wmp',12288)/2,
                                      row['pos'][1]+row['props'].get('hmp',12288)/2))
        center = player.center() if player is not None else None
        if at >= ai.get('next_lock', 0):
            if center is not None:
                dx = center[0] - row['pos'][0] - row['props'].get('wmp', 12288)/2
                dy = center[1] - row['pos'][1] - row['props'].get('hmp', 12288)/2
                distance = math.hypot(dx, dy)
                if distance > 1:
                    ai['vel'] = [dx/distance*params.SNAKE_SPEED_MP_S,
                                 dy/distance*params.SNAKE_SPEED_MP_S]
            if math.hypot(*ai['vel']) < 1:
                angle = math.radians(row['facing'])
                ai['vel'] = [math.cos(angle)*params.SNAKE_SPEED_MP_S,
                             math.sin(angle)*params.SNAKE_SPEED_MP_S]
            ai['next_lock'] = at + self.rng.uniform(*params.SNAKE_LEG_S)
            heading = math.degrees(math.atan2(ai['vel'][1], ai['vel'][0]))
            row['facing'] = heading
            self.batch.modify(row['objid'], 'FACING', intval=int(round(heading)),
                              offset=self._offset(at))
            self._record('snake_lock', at, objid=row['objid'], heading=heading,
                         next_lock=ai['next_lock'])
        row['pos'][0] += ai['vel'][0]*dt
        row['pos'][1] += ai['vel'][1]*dt
        if at >= ai.get('next_pose', 0):
            ai['next_pose'] = at + params.SNAKE_POSE_S
            ai['pose'] = 3 if ai.get('pose') == 2 else 2
            self.batch.modify(row['objid'], 'POSE', intval=ai['pose'], offset=self._offset(at))
        self._chain_tick(row, at)

    def _chain_tick(self, row, at):
        '''Internal mirror of the client's native ``follow maxdist=48px``.

        The retail client owns the visible interpolation after receiving the
        one-shot MOVE follow constraint in ``_attach_worm``.  We still keep
        server-side positions in lockstep for collision bookkeeping and test
        assertions, but intentionally emit no per-link MOVETO/FACING updates.
        '''
        chain = row.get('chain') or []
        if not chain:
            return
        need = float(params.SNAKE_LINK_MP)
        for order, oid in enumerate(chain[1:], start=1):
            link = self.actors.get(oid)
            parent = self.actors.get(chain[order-1])
            if link is None or parent is None:
                continue
            dx = link['pos'][0]-parent['pos'][0]
            dy = link['pos'][1]-parent['pos'][1]
            dist = math.hypot(dx, dy)
            if dist > 1:
                link['pos'] = [parent['pos'][0]+dx/dist*need,
                               parent['pos'][1]+dy/dist*need]

    def _blocked(self,x,y,props,shot):

        if shot in ('fireshot1','fireshot2','fireshot3'):
            return False
        return super()._blocked(x,y,props,shot)

    def _step_projectiles(self, start, end):
        dt = max(0., end - start)
        for objid, row in list(self.actors.items()):
            if row['kind'] == 'fx':
                if row['expire'] is not None and end >= row['expire']:
                    self._retire(objid, DESTROY_LIFETIME, end)
                continue
            if row['kind'] != 'projectile' or objid in self.retired:
                continue
            vx, vy = row['vel']
            ax, ay = row['accel']
            nx = row['pos'][0] + (vx + ax * dt / 2) * dt
            ny = row['pos'][1] + (vy + ay * dt / 2) * dt
            row['vel'] = [vx + ax * dt, vy + ay * dt]
            row['pos'] = [nx, ny]
            if row['shot'] in ('fireshot1', 'fireshot2') and end >= row['expire']:
                row['send_at'] = 0.
                self._sync_actor(row, end)
                self._split_fireshot(row, end)
                self._retire(objid, DESTROY_LIFETIME, end)
                continue
            expire = row['expire'] is not None and end >= row['expire']
            blocked = self._blocked(nx, ny, row['props'], row['shot'])
            if expire or blocked:
                row['send_at'] = 0.
                self._sync_actor(row, end)
                if expire:
                    self.counts['lifetime'] += 1
                    self._retire(objid, DESTROY_LIFETIME, end)
                else:
                    self.counts['wall_hits'] += 1
                    self._retire(objid, DESTROY_INTERNAL, end)
                continue
            self._sync_actor(row, end)

    def _feedback(self, event):
        if not isinstance(event, dict):
            return
        if 'ownerid' in event and int(event.get('ownerid') or 0) not in (0, self.worldid):
            return
        victim = int(event.get('victim') or 0)
        row = self.actors.get(victim)
        if row is None:
            return
        if event.get('kind') == 'damage':
            value = int(event.get('value') or 0)
            if value <= 0:
                return
            self.counts['native_damage'] += 1
            if victim in self.boss['parts'] and self.controller is not None:
                self.controller.add_damage(value, victim)
                remaining = max(1,int(math.ceil(self.controller.remaining_hp())))
                if remaining <= 1 or self.room_time >= self._boss_hp_at:
                    for part in self.boss['parts']:
                        self.batch.modify(part, 'HITPOINTS', intval=remaining,
                                          offset=self._offset(self.room_time))
                        self.batch.modify(part, 'MAXHITPOINTS',
                                          intval=int(round(self.boss_hp)),
                                          offset=self._offset(self.room_time))
                    self._boss_hp_at = self.room_time + 0.05
                    self._boss_hp_sent = remaining
                self._record('boss_damage', self.room_time, victim=victim, value=value,
                             remaining=remaining)
                if self.controller.defeated() and self.phase != 'defeated':
                    self._boss_defeated(self.room_time)
            elif row['hp'] is not None:
                row['hp'] -= value
                if row['hp'] <= 0:
                    self._npc_died(victim, row, self.room_time)
                else:
                    self.batch.modify(victim, 'HITPOINTS', intval=row['hp'],
                                      offset=self._offset(self.room_time))
        elif event.get('kind') == 'destroy':
            value = int(event.get('reason') or 1)
            if not self.native_retirement_allowed(victim,value):
                self.counts['rejected_shared_destroy']=self.counts.get('rejected_shared_destroy',0)+1
                return
            self.counts['native_destroy'] += 1
            if row['kind'] in ('npc', 'projectile', 'chain', 'fx'):
                if (row['ai'] or {}).get('species') == 'pod' and value == DESTROY_LIFETIME:
                    self._pod_fire(row, self.room_time)
                    return
                if row['kind'] == 'projectile' and value == DESTROY_LIFETIME:
                    self._split_fireshot(row, self.room_time)
                if (row['ai'] or {}).get('species') == 'snake':
                    for oid in row.get('chain') or []:
                        if oid != victim:
                            self._retire(oid, value, self.room_time)
                    if self.controller is not None:
                        for action in self.controller.npc_died(victim):
                            self._enqueue(self.room_time, 'controller',
                                          event=dict(action, at=self.room_time))
                self._retire(victim, value, self.room_time, native=True)

    def _npc_died(self, objid, row, at):
        species = (row['ai'] or {}).get('species')
        if species == 'hopper':
            self._hopper_burst(row, at)
        elif species == 'snake':
            for oid in row.get('chain') or []:
                if oid != objid and oid in self.actors:
                    self._retire(oid, DESTROY_HITPOINTS, at)
        self._retire(objid, DESTROY_HITPOINTS, at)
        if self.controller is not None:
            for event in self.controller.npc_died(objid):
                event = dict(event)
                event['at'] = at
                self._enqueue(at, 'controller', event=event)

    def step(self, dt):
        if not math.isfinite(dt) or dt < 0:
            raise ValueError('dt must be finite and non-negative')
        state, keys = self.bridge.snapshot()
        if state is not None:
            self.player = state
        self._frame_start = self.room_time
        if self.controller is None and state is not None and state.alive and state.age() < 2.:
            self.controller = JungleBossController(self.pack, seed=self.seed,
                                                   players=self.players)

            if getattr(self, 'initial_worm_heads', None):
                self.controller.worms = set(int(v) for v in self.initial_worm_heads)
            self.room_time = self._frame_start = 0.
            for event in self.controller.begin():
                self._controller_event(event)
            self._record('fight_start', 0.)

        for feedback in self.bridge.poll_events(limit=4096):
            self._feedback(feedback)
        if self.controller:
            start, end = self.room_time, self.room_time + dt
            player_ys = []
            for player in self.combat_players():
                if player.age() >= 2.:
                    continue

                head_height = self.pack.templates[str(self.boss['head'])].get('hmp', 172032)
                player_ys.append(player.ymp + (player.hmp-head_height)/2)
            for event in self.controller.step(dt, player_ys=player_ys):
                event = dict(event)
                event['at'] = end
                self._enqueue(end, 'controller', event=event)
            while self.queue and self.queue[0][0] <= end + 1e-9:
                at, _seq, action = heapq.heappop(self.queue)
                if action['kind'] == 'controller':
                    self._controller_event(action['event'])
                elif action['kind'] == 'fireshot':
                    self._fireshot_shot(action['part'], at,aimed=action.get('aimed',True))
                elif action['kind'] == 'boss_part_pass':
                    self._boss_part_pass(action, at)
                elif action['kind'] == 'bossx':
                    part=self.actors.get(action.get('part'))
                    base=(self._boss_head_position(at) if part and part['objid']==self.boss['head'] else
                          self._part_position(part,at) if part else (action['x'],action['y']))
                    self._spawn_bossx(base[0], base[1], at,
                                      action.get('dx', 0.), action.get('dy', 0.))
                elif action['kind'] == 'boss_vanish':
                    self._boss_vanish(at)
            self._step_npcs(start, end)
            self._step_projectiles(start, end)
            self._step_pincers(start, end)
            self._step_boss(start, end)
            self.room_time = end
            if self.phase != 'defeated' and self.controller.state != 'dormant':
                self.phase = self.controller.state
        self._wire_ms += dt * 1000.
        target = round(self._wire_ms)
        self.batch.dt = target - self._sent_ms
        self._sent_ms = target
        self.server._send_raw(self.channel, 12, self.batch.encode(), 'jungle-boss-live',
                              record=False)
        self.frames += 1
        self.batch.clear()
        self.supplies.flush()

    def _step_boss(self, start, end):
        """Horizontal parts; offsets are cumulative distances from the head."""
        head = self.actors.get(int(self.boss['head']))
        if head is None or 'pass_at' not in head:
            return
        if self.phase == 'defeated':
            self._step_boss_death(start, end, head)
            return
        position = self._boss_head_position(end)
        head['pos'] = [float(position[0]), float(position[1])]
        direction = int(head.get('dir') or -1)
        holding = bool(self.controller and self.controller.state == 'dwell')

        for part in self.boss['segments'] + [self.boss['tail']]:
            prow = self.actors.get(int(part))
            if prow is None:
                continue
            prow['pos'] = list(self._part_position(prow,end))
        if not holding:
            self._roll_pass_skitters(end, position[0], position[1], direction)
        tell_id = int(self.boss.get('tell') or 0)
        tell = self.actors.get(tell_id)
        if tell is not None:
            offset = abs(float((self.pack.templates.get(str(tell_id), {}).get('move') or {}).get('to', {}).get('xmp')
                               or params.BOSS_TELL_OFFSET_MP))
            tell['pos'] = [position[0]+direction*offset, position[1]]
            self._sync_actor(tell, end, interval=self.PART_TICK_S)

    def _step_boss_death(self, start, end, head):
        """Keep flying through the 9.4 s death segment, stop at the edge."""
        head['pos'] = list(self._boss_head_position(end))
        self._death_stopped = end >= head['pass_at']+head['pass_dur']
        for part in self.boss['segments'] + [self.boss['tail']]:
            prow = self.actors.get(int(part))
            if prow is None:
                continue
            prow['pos'] = list(self._part_position(prow,end))

    def _sample_bossx_offset(self, quantiles):
        """Piecewise-linear sample from a measured px quantile table."""
        if not quantiles:
            return 0.
        pick = self.rng.random()*(len(quantiles)-1)
        index = min(int(pick), len(quantiles)-2)
        fraction = pick-index
        return quantiles[index]*(1.-fraction)+quantiles[index+1]*fraction

    def _spawn_bossx(self, x, y, at, dx=0., dy=0.):
        props = self.spawn_props('jungle.bossx')
        props = dict(props, xmp=int(x + dx), ymp=int(y + dy),
                     lifetime=params.BOSSX_LIFETIME_MS)
        oid = self._create('jungle.bossx', props, at, kind='fx')
        self.actors[oid]['expire'] = at + params.BOSSX_LIFETIME_MS/1000.
        self.batch.sound(oid, 'jungle.bossx', offset=self._offset(at))

    def status(self):
        return dict(map='jungle_boss', phase=self.phase, time=round(self.room_time, 3),
                    frames=self.frames,
                    controller=self.controller.status() if self.controller else None,
                    npcs=sum(row['kind'] == 'npc' for row in self.actors.values()),
                    projectiles=sum(row['kind'] == 'projectile' for row in self.actors.values()),
                    actors=len(self.actors), pending=len(self.queue),
                    counts=dict(self.counts))
