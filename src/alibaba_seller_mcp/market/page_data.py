"""Pull the server-rendered data blocks out of an alibaba.com search page.

The page ships its state as a series of script assignments::

    window.__page__data_sse10._wending = { … };
    window.__page__data_sse10._offer_list = { … };

Each right-hand side is valid JSON (quoted keys, ``\\/`` and ``\\uXXXX`` escapes).
A regex cannot find where one ends — the blocks embed rendered HTML and
JSON-in-strings full of braces — so :func:`extract_page_data` scans to the
matching bracket while tracking string and escape state, then ``json.loads`` it.
"""

from __future__ import annotations

import json
import re
from typing import Any

_ASSIGN = re.compile(r"window\.__page__data_sse10\.([A-Za-z0-9_$]+)\s*=\s*")

# The anti-bot slider ("punish") page. Present only when the real page is withheld.
_BLOCK_MARKERS = ("_____tmd_____/verify", "/punish/", "punish-component")


def is_blocked(html: str) -> bool:
    """True when the response is the slider check, not a search page."""
    return any(m in html for m in _BLOCK_MARKERS) and "__page__data_sse10" not in html


def _json_span_end(text: str, start: int) -> int:
    """Index one past the bracket closing the JSON value that opens at ``start``."""
    depth = 0
    in_str = escaped = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def extract_page_data(html: str) -> tuple[dict[str, Any], list[str]]:
    """``({block_name: data}, warnings)`` for every JSON assignment on the page.

    A later assignment to the same name does not override an earlier object — the
    page follows each block with small property patches (``_wending.pageCommonData
    = …``) that this pattern does not match, and repeats none of the blocks.
    """
    blocks: dict[str, Any] = {}
    warnings: list[str] = []
    for m in _ASSIGN.finditer(html):
        start = m.end()
        if start >= len(html) or html[start] not in "{[":
            continue                          # a scalar flag like `_wending_is_hydrate = true`
        end = _json_span_end(html, start)
        if end < 0:
            warnings.append(f"block {m.group(1)} is truncated")
            continue
        try:
            blocks.setdefault(m.group(1), json.loads(html[start:end]))
        except json.JSONDecodeError as exc:
            warnings.append(f"block {m.group(1)} is not valid JSON ({exc.msg} at {exc.pos})")
    return blocks, warnings


def parse_track_info(raw: Any) -> dict[str, str]:
    """``"@@k1:v1@@k2:v2"`` (the page's trackInfo / extendLogs format) → dict.

    Values may contain ``:`` themselves, so each pair splits on the first one only.
    """
    out: dict[str, str] = {}
    for part in str(raw or "").split("@@"):
        key, sep, value = part.partition(":")
        if sep and key:
            out.setdefault(key.strip(), value)
    return out
