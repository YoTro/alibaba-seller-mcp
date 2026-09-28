"""Append-only JSONL history of keyword ad snapshots.

Two jobs: a cache (buyout slots change once a year, so re-checking a keyword within
the TTL costs no request) and a history (when a holder was first seen — a rough
renewal date for an annual buyout). Parsed placements only; never raw HTML.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from .ads import KeywordAdSnapshot


def keyword_key(keyword: str, country: str) -> tuple[str, str]:
    return " ".join(keyword.lower().split()), country.upper()


class SnapshotStore:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def append(self, snapshot: KeywordAdSnapshot) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot.to_dict(), ensure_ascii=False) + "\n")

    def history(self, keyword: str, country: str) -> list[KeywordAdSnapshot]:
        """Every stored snapshot for this keyword + country, oldest first."""
        if not self._path.exists():
            return []
        want = keyword_key(keyword, country)
        out = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if keyword_key(d.get("keyword", ""), d.get("country", "")) == want:
                    out.append(KeywordAdSnapshot.from_dict(d))
        return sorted(out, key=lambda s: s.fetched_at)

    def latest(self, keyword: str, country: str, *, newer_than: float) -> KeywordAdSnapshot | None:
        hist = [s for s in self.history(keyword, country) if s.fetched_at >= newer_than]
        return hist[-1] if hist else None
