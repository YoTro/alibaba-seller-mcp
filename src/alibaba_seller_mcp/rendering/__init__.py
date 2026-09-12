"""The rendering engine: detail-page images drawn in code — deterministic,
no AI, no knowledge of briefs or of the Alibaba platform.

    spec.py           DTOs: the page templates a spec may use (the AI/seller contract)
    image_ops.py      pixel work: cut-outs, transparency, colour variants
    fonts.py          font lookup with system fallbacks
    painter.py        drawing primitives on a fixed-width canvas
    pages.py          one renderer per page type (Strategy registry)
    render_assets.py  loading the seller's photos referenced by a spec
    render.py         entry point: render_spec(spec, out_dir)
    main_images.py    main-image crops (square renders, 3:4 / 4:3 scenes)

Why not an image model: detail pages are text-dense (specs, steps, OEM terms); an
image model gets numbers and spelling wrong, bills per attempt, and must be fully
regenerated for every copy change. The seller's photos are the only pixels not drawn.
"""

from .main_images import prepare_main_images
from .render import render_spec
from .spec import PAGE_TYPES, Assets, DetailSpec, PagesOnly, SceneAsset, Theme

__all__ = [
    "PAGE_TYPES", "Assets", "DetailSpec", "PagesOnly", "SceneAsset", "Theme",
    "prepare_main_images", "render_spec",
]
