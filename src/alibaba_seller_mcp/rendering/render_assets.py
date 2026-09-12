"""Loading the seller's photos referenced by a spec (renders cut out, scenes as-is)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from ..pathsafe import ensure_asset_allowed
from .image_ops import open_rgb, product_cutout
from .spec import DetailSpec


@dataclass
class LoadedAssets:
    hero: Image.Image
    side: Image.Image
    scenes: list[tuple[str, Image.Image]] = field(default_factory=list)


def load_assets(spec: DetailSpec, base_dir: Path | None, allowed_roots=None) -> LoadedAssets:
    def src(p: str) -> Path:
        # the spec's asset paths are file content, so they get the same allowlist
        # treatment as a tool argument (see pathsafe.ensure_asset_allowed)
        return ensure_asset_allowed(p, base_dir, allowed_roots)

    hero = product_cutout(src(spec.assets.hero))
    side = product_cutout(src(spec.assets.side)) if spec.assets.side else hero
    scenes = [(s.caption, open_rgb(src(s.path))) for s in spec.assets.scenes]
    return LoadedAssets(hero=hero, side=side, scenes=scenes)
