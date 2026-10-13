"""Basic small-enemy supplies shared by map hosts, with no map AI."""
import os
import random

from . import game_settings

def is_hp_death(reason):
    return reason in (2, 'HITPOINTS', 'DESTROYREASON_HITPOINTS')

class SupplyDrops:
    """One exclusive health/ammo/EC roll per kill; flush separate loot deltas.

    The host owns eligibility and deduplication. A missing item description
    leaves that outcome empty rather than increasing other item probabilities.
    Each map reads its own editor row. Difficulty does not multiply it.
    """

    def __init__(self, session, map_id, rng=None):
        self.session = session
        self.map_id = map_id
        self.rng = rng if rng is not None else random.Random(None)
        self.pending = []
        self._cache_key = None
        self._rates = None

    def rates(self):
        path = game_settings.PATH
        try:
            stamp = os.stat(path).st_mtime_ns
        except OSError:
            stamp = None
        key = (path, stamp)
        if self._rates is None or key != self._cache_key:
            self._rates = game_settings.supply_rates(game_settings.load(path), self.map_id)
            self._cache_key = key
        return self._rates

    def outcomes(self, extra_table=()):
        chance, table = self.rates()
        total = sum(weight for _item, weight in table)
        rows = [(item, chance * weight / total) for item, weight in table] if total > 0 else []
        extra_total = sum(probability for _item, probability in extra_table)
        scale = min(1., max(0., 1. - chance) / extra_total) if extra_total > 0 else 0.
        return rows + [(item, probability * scale) for item, probability in extra_table]

    def killed(self, x, y, extra_table=()):
        table = self.outcomes(extra_table)
        if not any(probability > 0 for _item, probability in table):
            return None
        roll, cumulative = self.rng.random(), 0.
        for item, probability in table:
            cumulative += probability
            if roll >= cumulative:
                continue
            known = getattr(self.session.server, 'item_types', None)
            if known is not None and item not in known:
                return None
            self.pending.append({'props_': {'xmp_': round(x), 'ymp_': round(y)},
                                 'itemprops_': [{'type_': item}]})
            return item
        return None

    def flush(self):
        session = self.session
        while self.pending:
            payload = session.layouts.encode('Delta', {'createloot_': self.pending[0]})
            session.server._send_raw(session.channel, 12, payload,
                                     'small-enemy-supply', record=False)
            self.pending.pop(0)
