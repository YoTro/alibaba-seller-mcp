"""Font lookup for the detail-image renderer.

Search order: ``theme.font_dir`` → ``DETAIL_FONT_DIR`` env → well-known system
folders (macOS Supplemental, Linux DejaVu/Liberation, Windows). Falls back to
Pillow's built-in scalable font so rendering never fails on a bare system.

The ``inter`` family (what the page renderer uses) is bundled under ``typefaces/``
(SIL OFL) so pages look the same on every host; Inter Display is used from 32px up,
mirroring the SF Text / SF Display split. It falls back to Helvetica Neue, then to
the ``sans`` family.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

Weight = str  # "regular" | "medium" | "semibold" | "bold" | "black" | "symbol"
Family = str  # "sans" | "inter"

_BUNDLED = Path(__file__).with_name("typefaces")
_DISPLAY_FROM = 32  # px; Inter Display below this size looks too tight

_CANDIDATES: dict[Weight, list[str]] = {
    "regular": ["Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf", "arial.ttf"],
    "bold": ["Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "arialbd.ttf"],
    "black": ["Arial Black.ttf", "Arial Bold.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf", "ariblk.ttf"],
    "symbol": ["Arial Unicode.ttf", "DejaVuSans.ttf", "Apple Symbols.ttf", "seguisym.ttf"],
}

_SYSTEM_DIRS = [
    "/System/Library/Fonts/Supplemental",
    "/Library/Fonts",
    os.path.expanduser("~/Library/Fonts"),
    "/usr/share/fonts/truetype/dejavu",
    "/usr/share/fonts/truetype/liberation",
    "/usr/share/fonts/dejavu",
    "C:/Windows/Fonts",
]


# (file, face index) — index matters for .ttc collections
_INTER: dict[Weight, list[tuple[str, int]]] = {
    "regular": [("Inter-Regular.ttf", 0), ("HelveticaNeue.ttc", 0)],
    "medium": [("Inter-Medium.ttf", 0), ("HelveticaNeue.ttc", 10)],
    "semibold": [("Inter-SemiBold.ttf", 0), ("HelveticaNeue.ttc", 1)],
    "bold": [("Inter-SemiBold.ttf", 0), ("HelveticaNeue.ttc", 1)],
}
_INTER_DISPLAY: dict[Weight, list[tuple[str, int]]] = {
    "semibold": [("InterDisplay-SemiBold.ttf", 0)],
    "bold": [("InterDisplay-Bold.ttf", 0)],
}


def _dirs(font_dir: str | None, bundled: bool = False) -> list[Path]:
    out: list[Path] = []
    for d in (font_dir, os.environ.get("DETAIL_FONT_DIR"), _BUNDLED if bundled else None,
              *_SYSTEM_DIRS, "/System/Library/Fonts"):
        if d and Path(d).is_dir():
            out.append(Path(d))
    return out


@lru_cache(maxsize=256)
def _resolve(weight: Weight, font_dir: str | None) -> str | None:
    for d in _dirs(font_dir):
        for name in _CANDIDATES.get(weight, _CANDIDATES["regular"]):
            p = d / name
            if p.is_file():
                return str(p)
    return None


@lru_cache(maxsize=256)
def _resolve_inter(weight: Weight, display: bool, font_dir: str | None) -> tuple[str, int] | None:
    cands = (_INTER_DISPLAY.get(weight, []) if display else []) + _INTER.get(weight, _INTER["regular"])
    for name, index in cands:
        for d in _dirs(font_dir, bundled=True):
            if (d / name).is_file():
                return str(d / name), index
    return None


@lru_cache(maxsize=512)
def font(size: int, weight: Weight = "regular", font_dir: str | None = None,
         family: Family = "sans") -> ImageFont.FreeTypeFont:
    if family == "inter" and weight != "symbol":
        hit = _resolve_inter(weight, size >= _DISPLAY_FROM, font_dir)
        if hit:
            return ImageFont.truetype(hit[0], size, index=hit[1])
    path = _resolve("bold" if weight in ("medium", "semibold") else weight, font_dir)
    if path:
        return ImageFont.truetype(path, size)
    try:  # Pillow ≥ 10.1 ships a scalable default
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow: bitmap default only
        return ImageFont.load_default()
