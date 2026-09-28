"""Application service: keyword → page → placements → monopoly report.

Owns the request policy. A keyword is served from the snapshot cache when it has
a recent one, and a batch stops making requests at the first slider page: every
request after it would be blocked too, and each one makes the block last longer.
Keywords after that point are still answered from the cache when possible.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .ads import KeywordAdSnapshot
from .errors import MarketBlockedError, MarketError, MarketParseError
from .monopoly import MonopolyReport, MonopolyScorer
from .page_data import extract_page_data
from .parsers import (
    bottom_p4p_params,
    dedupe,
    offer_list,
    page_keyword,
    parse_booths,
    parse_bottom_p4p,
    parse_offer_list,
    total_results,
)
from .snapshots import SnapshotStore
from .sources import HttpSearchSource, SavedPageSource, SearchPageSource


@dataclass
class SnapshotOutcome:
    snapshot: KeywordAdSnapshot
    cached: bool


@dataclass
class MonopolyBatch:
    reports: list[MonopolyReport] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)        # keywords answered from the cache
    blocked: list[str] = field(default_factory=list)       # keywords not analysed: slider page
    blocked_url: str | None = None
    blocked_notes: list[str] = field(default_factory=list)  # the solver's account of the block
    failed: dict[str, str] = field(default_factory=dict)   # keyword → error


class KeywordAdsAnalysis:
    def __init__(
        self,
        http: HttpSearchSource,
        store: SnapshotStore | None = None,
        scorer: MonopolyScorer | None = None,
        *,
        cache_ttl_seconds: float = 24 * 3600,
        unparsed_dir: Path | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.http = http
        self.store = store
        self.scorer = scorer or MonopolyScorer()
        self.cache_ttl_seconds = cache_ttl_seconds
        self.unparsed_dir = unparsed_dir
        self._clock = clock

    # ── building a snapshot ─────────────────────────────────────────────────
    def parse_page(
        self, html: str, *, keyword: str | None, country: str, source: str, bottom_p4p: bool = True,
    ) -> KeywordAdSnapshot:
        blocks, warnings = extract_page_data(html)
        if not offer_list(blocks) and "_wending" not in blocks:
            raise MarketParseError("no search results data on this page (is it an alibaba.com search page?)")
        keyword = keyword or page_keyword(blocks) or ""

        booths, booth_warnings = parse_booths(blocks)
        listed, list_length = parse_offer_list(blocks)
        bottom = []
        params = bottom_p4p_params(blocks)
        if bottom_p4p and params:
            try:
                bottom = parse_bottom_p4p(self.http.bottom_p4p(params))
            except MarketError as exc:
                warnings.append(f"bottom P4P not loaded: {exc}")

        return KeywordAdSnapshot(
            keyword=keyword,
            country=country.upper(),
            fetched_at=self._clock(),
            source=source,
            placements=tuple(dedupe([*booths, *listed, *bottom])),
            list_length=list_length,
            total_results=total_results(blocks),
            warnings=tuple(warnings + booth_warnings),
        ).with_company_names()

    def _fresh(self, keyword: str, country: str) -> KeywordAdSnapshot | None:
        if not self.store or self.cache_ttl_seconds <= 0:
            return None
        return self.store.latest(keyword, country, newer_than=self._clock() - self.cache_ttl_seconds)

    def _fetch(self, source: SearchPageSource, keyword: str | None, country: str,
               bottom_p4p: bool) -> KeywordAdSnapshot:
        html = source.search_page(keyword or "", country)
        try:
            snap = self.parse_page(html, keyword=keyword, country=country, source=source.name,
                                   bottom_p4p=bottom_p4p)
        except MarketParseError as exc:
            if self.unparsed_dir is None or source.name != "http":
                raise
            # Keep what the site actually sent: a new page variant can't be diagnosed
            # after the fact, and fetching it again may only earn a slider check.
            self.unparsed_dir.mkdir(parents=True, exist_ok=True)
            saved = self.unparsed_dir / f"{'-'.join((keyword or 'page').split())}-{int(self._clock())}.html"
            saved.write_text(html, encoding="utf-8")
            raise MarketParseError(f"{exc} — response saved to {saved}") from exc
        if self.store:
            self.store.append(snap)
        return snap

    def snapshot(self, keyword: str, country: str, *, refresh: bool = False,
                 bottom_p4p: bool = True) -> SnapshotOutcome:
        if not keyword.strip():
            raise ValueError("keyword is required (or pass a saved page)")
        if not refresh and (hit := self._fresh(keyword, country)):
            return SnapshotOutcome(hit, cached=True)
        return SnapshotOutcome(self._fetch(self.http, keyword.strip(), country, bottom_p4p), cached=False)

    def snapshot_from_file(self, path: Path, country: str, *, keyword: str | None = None,
                           bottom_p4p: bool = True) -> KeywordAdSnapshot:
        return self._fetch(SavedPageSource(path), keyword, country, bottom_p4p)

    # ── scoring ─────────────────────────────────────────────────────────────
    def report(self, snapshot: KeywordAdSnapshot, *, include_organic: bool = False) -> MonopolyReport:
        history = self.store.history(snapshot.keyword, snapshot.country) if self.store else []
        return self.scorer.score(snapshot, include_organic=include_organic, history=history)

    def monopoly(
        self,
        keywords: list[str],
        country: str,
        *,
        include_organic: bool = False,
        refresh: bool = False,
        bottom_p4p: bool = True,
    ) -> MonopolyBatch:
        batch = MonopolyBatch()
        for kw in dict.fromkeys(k.strip() for k in keywords if k.strip()):
            if batch.blocked_url is not None:          # stop requesting; serve the cache only
                hit = self._fresh(kw, country)
                if hit:
                    batch.cached.append(kw)
                    batch.reports.append(self.report(hit, include_organic=include_organic))
                else:
                    batch.blocked.append(kw)
                continue
            try:
                got = self.snapshot(kw, country, refresh=refresh, bottom_p4p=bottom_p4p)
            except MarketBlockedError as exc:
                batch.blocked_url = exc.url
                batch.blocked_notes = exc.notes
                batch.blocked.append(kw)
                continue
            except MarketError as exc:
                batch.failed[kw] = str(exc)
                continue
            if got.cached:
                batch.cached.append(kw)
            batch.reports.append(self.report(got.snapshot, include_organic=include_organic))
        return batch

    def monopoly_from_files(self, paths: list[Path], country: str, *,
                            include_organic: bool = False, bottom_p4p: bool = True) -> MonopolyBatch:
        batch = MonopolyBatch()
        for path in paths:
            try:
                snap = self.snapshot_from_file(path, country, bottom_p4p=bottom_p4p)
            except MarketError as exc:
                batch.failed[path.name] = str(exc)
                continue
            batch.reports.append(self.report(snap, include_organic=include_organic))
        return batch
