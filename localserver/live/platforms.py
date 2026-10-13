"""Ship ledges: per-row sweep removal and whole-deck generator removal."""
from collections import defaultdict
from . import constants

class Platforms(object):
    def __init__(self, session):
        self.session = session
        self.enabled = False
        self.holds = {}
        self.extended = {i:False for i in constants.LEDGE_IDS}
        self.terrain_due = {}
        self.pending = []
        rows = defaultdict(list)
        for objid in constants.LEDGE_IDS:
            item = session.room.object(objid)
            movement = item.get('move_', {}) or {}
            rows[(movement.get('to_') or {})['ymp_']].append(objid)
        self.rows = [ids for _,ids in sorted(rows.items())]

    def start(self):
        self.enabled = True
        self._reconcile()

    def stop(self):
        self.enabled = False
        self.holds.clear()
        self.terrain_due.clear()
        self.pending.clear()

    def retract(self, ids, seconds, reason, delay=0):
        if not self.enabled:
            return
        if delay:
            self.pending.append((self.session.room_time+delay, set(ids), seconds, reason))
            return
        self._retract_now(ids, seconds, reason, self.session.room_time)

    def _retract_now(self, ids, seconds, reason, at):
        self.holds[reason] = (at+seconds, set(ids))

        self._reconcile(restart=set(ids), at=at)
        self.session.event('ledges-retracted', seconds=seconds, reason=reason)

    def retract_two_rows(self, rng, reason):
        rows = rng.sample(self.rows, min(2,len(self.rows)))
        ids = [objid for row in rows for objid in row]
        self.retract(ids, constants.LEDGE_RETRACT_SECONDS, reason)
        self.session.event('ship-rows-retracted', rows=rows, reason=reason)

    def _reconcile(self, restart=(), at=None):
        at = self.session.room_time if at is None else at
        suppressed = set()
        for _,ids in self.holds.values():
            suppressed.update(ids)
        for objid in constants.LEDGE_IDS:
            wanted = objid not in suppressed
            changed = self.extended[objid] != wanted
            if not changed and (wanted or objid not in restart):
                continue
            self.extended[objid] = wanted
            if changed:
                vid='hulk.bossledgeshort' if wanted else 'hulk.bossledgeshorttelegraph'
                self.session.batch.modify(objid, 'VID', stringval=vid)
                self.session.room.object(objid).set(vid_=vid)
            self.session.batch.animprops(objid, 1, constants.LEDGE_EXTEND_ANIM_MS)
            self.session.batch.sound(objid, 'hulk.ledgeextend' if wanted else 'hulk.ledgeretract')

            self.terrain_due[objid] = (at+constants.LEDGE_TERRAIN_DELAY,
                                       constants.LEDGE_TERRAIN_TYPE if wanted else 0)

    def step(self):
        if not self.enabled:
            return
        due = sorted((p for p in self.pending if self.session.room_time+1e-8 >= p[0]),
                     key=lambda p:p[0])
        self.pending = [p for p in self.pending if self.session.room_time+1e-8 < p[0]]
        for at,ids,seconds,reason in due:
            self._retract_now(ids, seconds, reason, at)
        expired = [reason for reason,(until,_) in self.holds.items()
                   if self.session.room_time >= until]
        for reason in expired:
            del self.holds[reason]
        if expired:
            self._reconcile()
        for objid,(at,value) in list(self.terrain_due.items()):
            if self.session.room_time+1e-8 < at:
                continue
            item = self.session.room.object(objid)
            if item.get('terraintype_') != value:
                self.session.batch.modify(objid, 'TERRAINTYPE', intval=value)
                item.set(terraintype_=value)
            del self.terrain_due[objid]
            self.session.event('ledge-terrain', objid=objid, value=value)
