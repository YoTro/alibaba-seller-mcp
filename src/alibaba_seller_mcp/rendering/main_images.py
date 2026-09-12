"""Deterministic main-image preparation from the seller's photos.

Alibaba main images must be 4–6, > 640×640, aspect 3:4–4:3 (square best). This
module turns white-background renders into padded squares (cropped to the product's
bounding box) and lifestyle photos into 3:4 / 4:3 crops, without any AI.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..pathsafe import ensure_asset_allowed
from .image_ops import open_rgb, product_bbox
from .spec import Assets

MAIN_MAX = 6
SQUARE = 1000


def render_to_square(path: Path, out: Path, *, size: int = SQUARE, pad: float = 0.08) -> Path:
    """Crop a white-bg render to its product and centre it on a white square."""
    im = open_rgb(path)
    im = im.crop(product_bbox(im))
    w, h = im.size
    s = int(max(w, h) * (1 + 2 * pad))
    canvas = Image.new("RGB", (s, s), (255, 255, 255))
    canvas.paste(im, ((s - w) // 2, (s - h) // 2))
    canvas.resize((size, size), Image.LANCZOS).save(out, "JPEG", quality=92, optimize=True)
    return out


def photo_to_main_ratio(path: Path, out: Path, *, focus: float = 0.35, max_side: int = 1600) -> Path:
    """Crop a lifestyle photo to 3:4 (portrait) / 4:3 (landscape) / 1:1 (near-square).

    ``focus`` (0 = top/left, 1 = bottom/right) picks which part of the long side to
    keep — product shots usually sit in the upper-middle of portrait photos.
    """
    im = open_rgb(path)
    w, h = im.size
    ratio = w / h
    if 0.9 <= ratio <= 1.1:
        target_w, target_h = min(w, h), min(w, h)
    elif ratio < 1:  # portrait → 3:4
        target_w, target_h = w, min(h, int(w * 4 / 3))
    else:            # landscape → 4:3
        target_w, target_h = min(w, int(h * 4 / 3)), h
    x0 = int((w - target_w) * 0.5)
    y0 = int((h - target_h) * focus)
    im = im.crop((x0, y0, x0 + target_w, y0 + target_h))
    if max(im.size) > max_side:
        im.thumbnail((max_side, max_side), Image.LANCZOS)
    im.save(out, "JPEG", quality=92, optimize=True)
    return out


def prepare_main_images(assets: Assets, out_dir: Path, *, base_dir: Path | None = None,
                        allowed_roots=None) -> list[Path]:
    """Build up to 6 main images: hero + side squares first, then scene crops."""

    def src(p: str) -> Path:
        return ensure_asset_allowed(p, base_dir, allowed_roots)

    # Resolve every source before creating anything: a refused or missing photo
    # then fails without leaving a half-built output folder behind.
    hero, side = src(assets.hero), src(assets.side) if assets.side else None
    scenes = [src(s.path) for s in assets.scenes]

    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = [render_to_square(hero, out_dir / "01_hero.jpg")]
    if side is not None:
        outputs.append(render_to_square(side, out_dir / "02_side.jpg"))
    for i, path in enumerate(scenes):
        if len(outputs) >= MAIN_MAX:
            break
        outputs.append(photo_to_main_ratio(path, out_dir / f"{len(outputs) + 1:02d}_scene{i + 1}.jpg"))
    return outputs
