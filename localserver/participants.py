"""Shared participant and target rules for one-player and cooperative rooms."""
import math
import collections

def identity(player):
    return (int(player.worldid), int(player.objid))

def life_identity(player):
    return identity(player) + (int(getattr(player, 'raw', {}).get('life_id', 0)),)

def active_players(players, max_age=None):
    if players is None:
        return ()
    if hasattr(players, 'values'):
        players = players.values()
    result = {}
    for player in players:
        if isinstance(player, dict):
            from .live.bridge import PlayerState
            player = PlayerState(**player)
        if player is None or not getattr(player, 'alive', False):
            continue
        raw = getattr(player, 'raw', {})
        if raw.get('spectator', False) or raw.get('loaded', True) is False:
            continue
        if getattr(player, 'maxhp', 0) > 0 and getattr(player, 'hp', 0) <= 0:
            continue
        if max_age is not None and player.age() > max_age:
            continue
        if not all(math.isfinite(v) for v in player.center()):
            continue
        result[identity(player)] = player
    return tuple(result[key] for key in sorted(result))

def from_bridge(bridge, fallback=None):
    """A roster is authoritative; an empty roster must not revive a fallback."""
    snapshot = getattr(bridge, 'snapshot_players', None)
    if callable(snapshot):
        return active_players(snapshot())
    peers = getattr(bridge, 'players', None)
    if peers is not None:
        if callable(peers):
            peers = peers()
        return active_players(peers, max_age=2.)
    return active_players((fallback,))

def nearest(players, origin, radius=None):
    candidates = active_players(players)
    if not candidates:
        return None
    def distance(player):
        x, y = player.center()
        return (x-origin[0])**2 + (y-origin[1])**2
    target = min(candidates, key=lambda p: (distance(p), identity(p)))
    if radius is not None and distance(target) > radius**2:
        return None
    return target

class ActiveTargetIndex:
    """Nearest-target geometry for one already validated simulation roster.

    Build after relocation, then discard at the end of this controller step.
    This is never a cache across telemetry, life changes or simulation frames.
    """
    def __init__(self,candidates):
        self.rows=tuple((player,*player.center(),identity(player)) for player in candidates)

    def nearest(self,origin,radius=None):
        target=None;best=None
        for player,x,y,key in self.rows:
            distance=(x-origin[0])**2+(y-origin[1])**2
            rank=(distance,key)
            if best is None or rank<best:target=player;best=rank
        if best is None or radius is not None and best[0]>radius**2:return None
        return target

class DamageTargetLock:
    """ETL: a ten-second lock, close contributors, and a 10% hand-range override."""
    def __init__(self, interval=10., override_chance=.1):
        self.interval = float(interval)
        self.override_chance = float(override_chance)
        self.key = None
        self.next_check = 0.
        self.damage = {}
        self.damage_events = collections.deque()

    def add_damage(self, player, value, at=0.):
        if player is not None and value > 0 and math.isfinite(value):
            key = life_identity(player)
            self.damage[key] = self.damage.get(key, 0.) + float(value)
            stamp=float(at);amount=float(value)

            if self.damage_events and self.damage_events[-1][:2]==(stamp,key):
                previous=self.damage_events.pop();amount+=previous[2]
            self.damage_events.append((stamp, key, amount))

    def select(self, players, origin, now, hand_reach, rng):
        candidates = active_players(players)
        live = {life_identity(p): p for p in candidates}
        if now < self.next_check and self.key in live:
            return live[self.key]
        self.next_check = now + self.interval
        while self.damage_events and self.damage_events[0][0] < now-self.interval:
            self.damage_events.popleft()
        self.damage = {}
        for _, key, value in self.damage_events:
            if key in live:
                self.damage[key] = self.damage.get(key, 0.) + value
        contributors = [p for p in candidates if self.damage.get(life_identity(p), 0.) > 0]
        target = nearest(contributors or candidates, origin)
        close = [p for p in contributors if math.dist(p.center(), origin) <= hand_reach]
        if close:
            target = min(close, key=lambda p: (-self.damage[life_identity(p)],
                         math.dist(p.center(), origin), identity(p)))
        others = [p for p in candidates if p is not target
                  and math.dist(p.center(), origin) <= hand_reach]

        if others and rng.random() < self.override_chance:
            target = rng.choice(others)
        self.key = life_identity(target) if target is not None else None

        self.damage = {key: value for key, value in self.damage.items() if key in live}
        return target
