"""Page data → :class:`AdPlacement` s. One strategy per ad source.

Booths are dispatched by their ``type`` field through :data:`BOOTH_PARSERS`, so a
new booth format is one registry entry. A ``*Model`` block with an unregistered
type is reported as a warning, never dropped silently — that is how a new ad
product on the page gets noticed.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any, Protocol

from .ads import AdKind, AdPlacement
from .page_data import parse_track_info


def _s(v: Any) -> str | None:
    """Ids arrive as JSON numbers or strings; normalise to a non-empty string."""
    if v is None or v == "":
        return None
    return str(v)


def _track(model: dict[str, Any]) -> dict[str, str]:
    """trackInfo + extendLogs merged (trackInfo wins; it is the superset)."""
    return {**parse_track_info(model.get("extendLogs")), **parse_track_info(model.get("trackInfo"))}


def _company(model: dict[str, Any], track: dict[str, str]) -> tuple[str | None, str | None]:
    info = model.get("companyInfo") or {}
    products = model.get("productList") or []
    first = products[0] if products and isinstance(products[0], dict) else {}
    cid = _s(model.get("companyId")) or _s(info.get("companyId")) or _s(track.get("company_id")) \
        or _s(first.get("companyId"))
    name = model.get("companyName") or info.get("companyName") or first.get("companyName")
    return cid, name


def _product_ids(model: dict[str, Any]) -> tuple[str, ...]:
    ids = [_s(p.get("productId") or p.get("id")) for p in model.get("productList") or [] if isinstance(p, dict)]
    ad_info = model.get("adInfo") or {}
    if not any(ids):
        ids = [_s(p.get("id")) for p in (ad_info.get("creativeInfo") or {}).get("products") or []]
    if not any(ids):
        ids = [s.strip() for s in str(ad_info.get("trSecondProducts") or model.get("trSecondProducts") or "").split(",")]
    return tuple(i for i in ids if i)


class BoothParser(Protocol):
    def parse(self, model: dict[str, Any], kind: AdKind) -> list[AdPlacement]: ...


class SingleCompanyBoothParser:
    """A booth that shows one company's products: wending, duxiu, starbrand — and the
    topad_classic card in the product list, which has the same shape."""

    def parse(self, model: dict[str, Any], kind: AdKind) -> list[AdPlacement]:
        track = _track(model)
        cid, name = _company(model, track)
        if not cid:
            return []
        ad_info = model.get("adInfo") or {}
        exact = track.get("is_exact")
        return [AdPlacement(
            kind=kind,
            advertiser=cid,
            company_id=cid,
            company_name=name,
            product_ids=_product_ids(model),
            campaign_id=_s(model.get("campaignId") or ad_info.get("campaignId") or track.get("campaign_id")),
            campaign_type=_s(model.get("campaignType") or ad_info.get("campaignType") or track.get("campaign_type")),
            resource_lock_id=track.get("resource_lock_id") or None,
            match_type=track.get("match_type") or None,
            is_exact=None if exact is None else exact == "1",
        )]


class CreativeListParser:
    """A container (``type: "creative"``) of several single-company booths."""

    def parse(self, model: dict[str, Any], kind: AdKind) -> list[AdPlacement]:
        single = SingleCompanyBoothParser()
        return [p for c in model.get("creativeList") or [] if isinstance(c, dict) for p in single.parse(c, kind)]


# Booth `type` → (parser, kind when the block name does not decide it).
BOOTH_PARSERS: dict[str, tuple[BoothParser, AdKind]] = {
    "wending": (SingleCompanyBoothParser(), AdKind.TOP_BOOTH),
    "duxiu": (SingleCompanyBoothParser(), AdKind.DUXIU),
    "starbrand": (SingleCompanyBoothParser(), AdKind.STAR_BRAND),
    "creative": (CreativeListParser(), AdKind.BOTTOM_BOOTH),
}

# The block name wins over the type: the bottom booth's creatives are `wending`
# too (campaign type 12, like the top booth) but sit at the foot of the page.
_KIND_BY_BLOCK = {"bottomBoothModel": AdKind.BOTTOM_BOOTH}


def parse_booths(blocks: dict[str, Any]) -> tuple[list[AdPlacement], list[str]]:
    """Every ``*Model`` booth in any page block (they live under ``_wending`` today)."""
    placements: list[AdPlacement] = []
    warnings: list[str] = []
    for block_name, block in blocks.items():
        if not isinstance(block, dict):
            continue
        for key, model in block.items():
            if not (key.endswith("Model") and isinstance(model, dict) and "type" in model):
                continue
            entry = BOOTH_PARSERS.get(str(model["type"]))
            if entry is None:
                warnings.append(f"unknown ad block {block_name}.{key} (type {model['type']!r}) — not scored")
                continue
            parser, kind = entry
            found = parser.parse(model, _KIND_BY_BLOCK.get(key, kind))
            if not found:
                warnings.append(f"{block_name}.{key} (type {model['type']!r}) has no company id — not scored")
            placements.extend(found)
    return placements, warnings


def offer_list(blocks: dict[str, Any]) -> dict[str, Any]:
    return ((blocks.get("_offer_list") or {}).get("offerResultData")) or {}


def is_topad_classic(offer: dict[str, Any], track: dict[str, str]) -> bool:
    """The buyout card inside the list. Its ``isShowAd`` is false, so it would pass
    for organic; it is the only offer carrying ``adInfo``, and its product type is
    ``newad`` (organic ``e``, P4P ``g``)."""
    return bool(offer.get("adInfo")) or track.get("product_type") == "newad" or track.get("item_type") == "newad"


def parse_offer_list(blocks: dict[str, Any]) -> tuple[list[AdPlacement], int]:
    """The page-1 product list: the topad_classic card, ``isShowAd`` offers as P4P,
    the rest as organic.

    Returns ``(placements, list_length)``; ranks are 1-based over the whole list.
    """
    offers = [o for o in offer_list(blocks).get("offers") or [] if isinstance(o, dict)]
    out = []
    classic = SingleCompanyBoothParser()
    for rank, o in enumerate(offers, start=1):
        cid = _s(o.get("companyId"))
        if not cid:
            continue
        track = parse_track_info(o.get("trackInfo"))
        if is_topad_classic(o, track):
            out.extend(replace(p, rank=rank) for p in classic.parse(o, AdKind.TOP_AD_CLASSIC))
            continue
        out.append(AdPlacement(
            kind=AdKind.P4P_LIST if o.get("isShowAd") else AdKind.ORGANIC,
            advertiser=cid,
            company_id=cid,
            company_name=o.get("companyName"),
            rank=rank,
            product_ids=tuple(filter(None, [_s(o.get("productId") or o.get("id"))])),
            campaign_id=track.get("campaign_id") or None,
        ))
    return out, len(offers)


_DETAIL_ID = re.compile(r"_(\d{6,})\.html")


def parse_bottom_p4p(payload: dict[str, Any] | None) -> list[AdPlacement]:
    """The async bottom-of-page P4P response (``{"PRODUCTS": [...]}``)."""
    out = []
    for item in (payload or {}).get("PRODUCTS") or []:
        member = _s(item.get("MEMBERID"))
        if not member:
            continue
        m = _DETAIL_ID.search(str(item.get("DETAILURL") or ""))
        out.append(AdPlacement(
            kind=AdKind.P4P_BOTTOM,
            advertiser=f"member:{member}",
            product_ids=(m.group(1),) if m else (),
        ))
    return out


def bottom_p4p_params(blocks: dict[str, Any]) -> dict[str, Any] | None:
    """The request parameters the page uses to load its bottom P4P, if any."""
    footer = blocks.get("_content_footer") or {}
    params = (footer.get("p4pData") or {}).get("bottomP4P")
    return params if isinstance(params, dict) and params.get("urlPrefix") else None


def page_keyword(blocks: dict[str, Any]) -> str | None:
    for name in ("_offer_list", "_header", "_content_footer"):
        kw = (blocks.get(name) or {}).get("keyword")
        if isinstance(kw, str) and kw.strip():
            return kw.strip()
    return None


def total_results(blocks: dict[str, Any]) -> int | None:
    total = offer_list(blocks).get("totalCount")
    return int(total) if isinstance(total, int | float) else None


def dedupe(placements: Iterable[AdPlacement]) -> list[AdPlacement]:
    """Drop exact repeats (same kind, advertiser and rank) — defensive, cheap."""
    seen: set[tuple] = set()
    out = []
    for p in placements:
        key = (p.kind, p.advertiser, p.rank, p.resource_lock_id)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out
