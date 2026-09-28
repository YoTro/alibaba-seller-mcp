"""Domain model: the ads on one alibaba.com search results page.

Five ad kinds share one placement shape. Three buyout kinds hold a slot for the
keyword for a year; P4P is bid per click and rotates.

    TOP_BOOTH        _wending.topBoothModel      type "wending"                      buyout
    DUXIU            _wending.topBoothModel      type "duxiu" (campaign type 14)     buyout
    STAR_BRAND       _wending.starBrandModel     type "starbrand"                    buyout
    BOTTOM_BOOTH     _wending.bottomBoothModel   type "creative" → creativeList[]    buyout
    TOP_AD_CLASSIC   an offer in the product list carrying adInfo / product_type
                     "newad" (the card's data-spm is "topad_classic", campaign type 32)   buyout
    P4P_LIST         offerResultData.offers[] with isShowAd                          bid
    P4P_BOTTOM       p4p-enmatch …/b2bad.do (loaded async)                           bid

Every booth — whatever its kind — shows products from ONE company, so a booth is
one advertiser. Bottom P4P carries only an encrypted member id, never a company id,
so it can be counted but not joined to the other slots.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import StrEnum
from typing import Any


class AdKind(StrEnum):
    TOP_BOOTH = "top_booth"
    DUXIU = "duxiu"
    STAR_BRAND = "star_brand"
    TOP_AD_CLASSIC = "topad_classic"
    BOTTOM_BOOTH = "bottom_booth"
    P4P_LIST = "p4p_list"
    P4P_BOTTOM = "p4p_bottom"
    ORGANIC = "organic"          # not an ad; only scored when organic results are included

    @property
    def is_buyout(self) -> bool:
        return self in _BUYOUT

    @property
    def is_ad(self) -> bool:
        return self is not AdKind.ORGANIC


_BUYOUT = frozenset({AdKind.TOP_BOOTH, AdKind.DUXIU, AdKind.STAR_BRAND, AdKind.TOP_AD_CLASSIC,
                     AdKind.BOTTOM_BOOTH})


@dataclass(frozen=True)
class AdPlacement:
    """One slot on page 1 and who holds it.

    ``advertiser`` is the join key: ``company_id`` when the page gives one, else
    ``member:<encrypted id>`` (bottom P4P). ``rank`` is the 1-based position in the
    product list for list slots (topad_classic included), and ``None`` for booths.
    """

    kind: AdKind
    advertiser: str
    company_id: str | None = None
    company_name: str | None = None
    rank: int | None = None
    product_ids: tuple[str, ...] = ()
    campaign_id: str | None = None
    campaign_type: str | None = None
    resource_lock_id: str | None = None
    match_type: str | None = None
    is_exact: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["product_ids"] = list(self.product_ids)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> AdPlacement:
        return cls(**{**d, "kind": AdKind(d["kind"]), "product_ids": tuple(d.get("product_ids") or ())})


@dataclass(frozen=True)
class KeywordAdSnapshot:
    """Everything scored for one keyword from one fetch of page 1."""

    keyword: str
    country: str
    fetched_at: float
    source: str                                   # http | file
    placements: tuple[AdPlacement, ...] = ()      # ads AND organic results, in page order
    list_length: int = 0                          # products in the page-1 list (ads included)
    total_results: int | None = None
    warnings: tuple[str, ...] = field(default=())

    def with_company_names(self) -> KeywordAdSnapshot:
        """Fill a placement's missing company name from another slot of the same company
        (a duxiu booth carries only the company id)."""
        names = {p.company_id: p.company_name for p in self.placements if p.company_id and p.company_name}
        return replace(self, placements=tuple(
            replace(p, company_name=names[p.company_id]) if not p.company_name and p.company_id in names else p
            for p in self.placements))

    @property
    def ads(self) -> tuple[AdPlacement, ...]:
        return tuple(p for p in self.placements if p.kind.is_ad)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword,
            "country": self.country,
            "fetched_at": self.fetched_at,
            "source": self.source,
            "placements": [p.to_dict() for p in self.placements],
            "list_length": self.list_length,
            "total_results": self.total_results,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> KeywordAdSnapshot:
        return cls(
            keyword=d["keyword"],
            country=d["country"],
            fetched_at=float(d["fetched_at"]),
            source=d.get("source", "http"),
            placements=tuple(AdPlacement.from_dict(p) for p in d.get("placements") or ()),
            list_length=int(d.get("list_length") or 0),
            total_results=d.get("total_results"),
            warnings=tuple(d.get("warnings") or ()),
        )
