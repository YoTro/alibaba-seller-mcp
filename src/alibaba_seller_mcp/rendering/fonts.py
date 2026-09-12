"""Font lookup for the detail-image renderer.

Search order: ``theme.font_dir`` → ``DETAIL_FONT_DIR`` env → well-known system
folders (macOS Supplemental, Linux DejaVu/Liberation, Windows). Falls back to
Pillow's built-in scalable font so rendering never fails on a bare system.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

Weight = str  # "regular" | "bold" | "black" | "symbol"

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


def _dirs(font_dir: str | None) -> list[Path]:
    out: list[Path] = []
    for d in (font_dir, os.environ.get("DETAIL_FONT_DIR"), *_SYSTEM_DIRS):
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


@lru_cache(maxsize=512)
def font(size: int, weight: Weight = "regular", font_dir: str | None = None) -> ImageFont.FreeTypeFont:
    path = _resolve(weight, font_dir)
    if path:
        return ImageFont.truetype(path, size)
    try:  # Pillow ≥ 10.1 ships a scalable default
        return ImageFont.load_default(size=size)
    except TypeError:  # very old Pillow: bitmap default only
        return ImageFont.load_default()
