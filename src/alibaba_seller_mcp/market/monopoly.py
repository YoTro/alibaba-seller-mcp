"""Keyword monopoly rate — how much of page 1's ad exposure one buyer owns.

Pure domain service: snapshots in, a report out, no I/O.

**Exposure weights.** A slot's weight is how much attention its position gets,
not a flat count — otherwise 24 rotating P4P slots drown out the one buyout booth
that sits above all of them.

* list rank *r* (topad_classic, P4P, and organic when included): ``1 / log2(r + 1)``
  — the usual position-discount curve; rank 1 = 1.0, rank 8 ≈ 0.32
* top-slot booth (top booth / duxiu / star brand): the sum of the first
  ``top_slot_positions`` list ranks — it is a full-width block above them
* bottom booth: the weight of the last list rank — it sits below the list
* bottom P4P: ``bottom_p4p_factor`` × the bottom-booth weight

**Metrics** (over ad exposure; organic joins the pool only when asked):

* ``locked_share``   buyout-slot weight / total — what bidding cannot win
* ``monopoly_rate``  weight held by buyout holders, their P4P slots included / total
* ``hhi``            Σ share² over advertisers — concentration whether or not a buyout exists

Verdict: ``locked`` (a buyout, monopoly_rate ≥ threshold), ``anchored`` (a buyout,
below it), ``contested`` (no buyout, hhi ≥ threshold), ``open`` (bid for it).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from .ads import AdKind, AdPlacement, KeywordAdSnapshot

_TOP_BOOTHS = frozenset({AdKind.TOP_BOOTH, AdKind.DUXIU, AdKind.STAR_BRAND})


@dataclass(frozen=True)
class ExposureWeights:
    top_slot_positions: int = 8
    bottom_p4p_factor: float = 0.5
    default_list_length: int = 48          # page-1 size when a snapshot does not say

    @staticmethod
    def rank(r: int) -> float:
        return 1.0 / math.log2(r + 1)

    def top_slot(self) -> float:
        return sum(self.rank(r) for r in range(1, self.top_slot_positions + 1))

    def bottom_slot(self, list_length: int) -> float:
        return self.rank(max(list_length or self.default_list_length, 1))

    def of(self, p: AdPlacement, list_length: int) -> float:
        if p.kind in _TOP_BOOTHS:
            return self.top_slot()
        if p.kind is AdKind.BOTTOM_BOOTH:
            return self.bottom_slot(list_length)
        if p.kind is AdKind.P4P_BOTTOM:
            return self.bottom_p4p_factor * self.bottom_slot(list_length)
        return self.rank(p.rank or list_length or self.default_list_length)


@dataclass(frozen=True)
class Thresholds:
    locked_monopoly_rate: float = 0.5
    contested_hhi: float = 0.25


@dataclass(frozen=True)
class HolderShare:
    advertiser: str
    company_name: str | None
    kinds: tuple[str, ...]                 # every kind of slot this holder has on the page
    share: float                           # of the scored exposure
    first_seen: float | None = None        # earliest snapshot holding a buyout for this keyword


@dataclass(frozen=True)
class MonopolyReport:
    keyword: str
    country: str
    verdict: str
    monopoly_rate: float
    locked_share: float
    hhi: float
    ad_slots: dict[str, int]
    holders: tuple[HolderShare, ...]       # buyout holders, largest share first
    top_advertisers: tuple[HolderShare, ...]
    fetched_at: float
    source: str
    include_organic: bool
    notes: tuple[str, ...] = field(default=())


class MonopolyScorer:
    def __init__(self, weights: ExposureWeights | None = None, thresholds: Thresholds | None = None):
        self.weights = weights or ExposureWeights()
        self.thresholds = thresholds or Thresholds()

    def score(
        self,
        snapshot: KeywordAdSnapshot,
        *,
        include_organic: bool = False,
        history: list[KeywordAdSnapshot] | None = None,
    ) -> MonopolyReport:
        pool = [p for p in snapshot.placements if p.kind.is_ad or include_organic]
        n = snapshot.list_length
        weight_of = {id(p): self.weights.of(p, n) for p in pool}
        total = sum(weight_of.values())

        by_adv: dict[str, float] = defaultdict(float)
        kinds: dict[str, set[str]] = defaultdict(set)
        names: dict[str, str | None] = {}
        for p in pool:
            by_adv[p.advertiser] += weight_of[id(p)]
            kinds[p.advertiser].add(p.kind.value)
            names.setdefault(p.advertiser, p.company_name)
            if p.company_name and not names[p.advertiser]:
                names[p.advertiser] = p.company_name

        buyout_holders = {p.advertiser for p in pool if p.kind.is_buyout}
        locked = sum(weight_of[id(p)] for p in pool if p.kind.is_buyout)
        held = sum(by_adv[a] for a in buyout_holders)

        share = (lambda w: w / total) if total else (lambda w: 0.0)
        monopoly_rate = share(held)
        hhi = sum(share(w) ** 2 for w in by_adv.values())
        first_seen = _first_seen(history or [], snapshot)

        def holder(a: str) -> HolderShare:
            return HolderShare(a, names.get(a), tuple(sorted(kinds[a])), round(share(by_adv[a]), 4),
                               first_seen.get(a))

        holders = tuple(sorted((holder(a) for a in buyout_holders), key=lambda h: -h.share))
        top = tuple(sorted((holder(a) for a in by_adv), key=lambda h: -h.share)[:5])

        ad_slots: dict[str, int] = defaultdict(int)
        for p in snapshot.placements:
            if p.kind.is_ad:
                ad_slots[p.kind.value] += 1

        notes = list(snapshot.warnings)
        if not snapshot.ads:
            notes.append("no ads on page 1")
        if any(p.kind is AdKind.P4P_BOTTOM for p in pool):
            notes.append("bottom P4P advertisers are identified by an encrypted member id only; "
                         "they count toward hhi but never toward a buyout holder")

        return MonopolyReport(
            keyword=snapshot.keyword,
            country=snapshot.country,
            verdict=self._verdict(bool(buyout_holders), monopoly_rate, hhi),
            monopoly_rate=round(monopoly_rate, 4),
            locked_share=round(share(locked), 4),
            hhi=round(hhi, 4),
            ad_slots=dict(ad_slots),
            holders=holders,
            top_advertisers=top,
            fetched_at=snapshot.fetched_at,
            source=snapshot.source,
            include_organic=include_organic,
            notes=tuple(notes),
        )

    def _verdict(self, has_buyout: bool, monopoly_rate: float, hhi: float) -> str:
        if has_buyout:
            return "locked" if monopoly_rate >= self.thresholds.locked_monopoly_rate else "anchored"
        return "contested" if hhi >= self.thresholds.contested_hhi else "open"


def _first_seen(history: list[KeywordAdSnapshot], current: KeywordAdSnapshot) -> dict[str, float]:
    """Earliest snapshot in which each advertiser held a buyout slot for this keyword."""
    seen: dict[str, float] = {}
    for snap in [*history, current]:
        for p in snap.placements:
            if p.kind.is_buyout:
                seen[p.advertiser] = min(seen.get(p.advertiser, snap.fetched_at), snap.fetched_at)
    return seen
