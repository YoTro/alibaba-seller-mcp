"""Record and aggregate Anthropic token usage, with USD cost estimates.

Every AI call records a row (model, token counts, cost, timestamp, label) to the
usage log. Costs use the published per-1M-token rates below; cache reads bill at
~0.1x input and cache writes at ~1.25x input. Rates are cached constants — update
`PRICING` when Anthropic's price list changes.
"""

from __future__ import annotations

import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ..storage import UsageStore

# USD per 1,000,000 tokens: (input, output).
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float | None:
    """USD cost for one call, or None if the model's price is unknown."""
    rate = PRICING.get(model)
    if rate is None:
        return None
    in_rate, out_rate = rate
    cost = (
        input_tokens * in_rate
        + output_tokens * out_rate
        + cache_read_tokens * in_rate * CACHE_READ_MULTIPLIER
        + cache_write_tokens * in_rate * CACHE_WRITE_MULTIPLIER
    ) / 1_000_000
    return round(cost, 6)


class UsageTracker:
    def __init__(self, store: UsageStore):
        self.store = store
        # Tokens recorded by THIS process. The on-disk log is the full history;
        # a budget is about stopping a loop in the session that is running now,
        # so it is counted in memory rather than re-read from the log per call.
        self.session_tokens = 0

    def record(self, model: str, usage: Any, *, label: str = "", metadata: dict | None = None) -> dict[str, Any]:
        """Record one call from an Anthropic ``response.usage`` object (or dict)."""
        input_tokens = _get(usage, "input_tokens", 0)
        output_tokens = _get(usage, "output_tokens", 0)
        cache_read = _get(usage, "cache_read_input_tokens", 0)
        cache_write = _get(usage, "cache_creation_input_tokens", 0)
        cost = estimate_cost(model, input_tokens, output_tokens, cache_read, cache_write)
        record = {
            "ts": time.time(),
            "iso": datetime.now(UTC).isoformat(),
            "model": model,
            "label": label,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "total_tokens": input_tokens + output_tokens,
            "estimated_cost_usd": cost,
            "metadata": metadata or {},
        }
        self.store.append(record)
        self.session_tokens += record["total_tokens"]
        return record

    def stats(
        self,
        *,
        model: str | None = None,
        label: str | None = None,
        since_iso: str | None = None,
        group_by: str = "day",
    ) -> dict[str, Any]:
        """Aggregate recorded usage, optionally filtered, grouped by day/model/label."""
        records = self.store.read_all()
        since_ts = _parse_since(since_iso)

        filtered = [
            r
            for r in records
            if (model is None or r.get("model") == model)
            and (label is None or r.get("label") == label)
            and (since_ts is None or r.get("ts", 0) >= since_ts)
        ]

        totals = _zero_totals()
        groups: dict[str, dict[str, Any]] = defaultdict(_zero_totals)
        for r in filtered:
            _accumulate(totals, r)
            key = _group_key(r, group_by)
            _accumulate(groups[key], r)

        return {
            "filters": {"model": model, "label": label, "since_iso": since_iso},
            "call_count": len(filtered),
            "totals": _finalize(totals),
            "group_by": group_by,
            "groups": {k: _finalize(v) for k, v in sorted(groups.items())},
        }


def _get(usage: Any, name: str, default: int) -> int:
    if isinstance(usage, dict):
        val = usage.get(name, default)
    else:
        val = getattr(usage, name, default)
    return int(val or 0)


def _zero_totals() -> dict[str, Any]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "cost_known": True,
    }


def _accumulate(acc: dict[str, Any], r: dict[str, Any]) -> None:
    acc["calls"] += 1
    for f in (
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "total_tokens",
    ):
        acc[f] += int(r.get(f, 0) or 0)
    cost = r.get("estimated_cost_usd")
    if cost is None:
        acc["cost_known"] = False
    else:
        acc["estimated_cost_usd"] += cost


def _finalize(acc: dict[str, Any]) -> dict[str, Any]:
    acc["estimated_cost_usd"] = round(acc["estimated_cost_usd"], 6)
    return acc


def _group_key(r: dict[str, Any], group_by: str) -> str:
    if group_by == "model":
        return r.get("model", "unknown")
    if group_by == "label":
        return r.get("label") or "(none)"
    # default: day (UTC)
    iso = r.get("iso", "")
    return iso[:10] if iso else "unknown"


def _parse_since(since_iso: str | None) -> float | None:
    if not since_iso:
        return None
    try:
        dt = datetime.fromisoformat(since_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except ValueError:
        return None
