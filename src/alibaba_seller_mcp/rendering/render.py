"""Entry point: ``DetailSpec`` → JPEG detail pages (width 1200, each < 3 MB).

Text is drawn from the spec verbatim, so numbers and spelling are always exact.
Layout lives in :mod:`pages`, primitives in :mod:`painter`, pixel work in
:mod:`image_ops`."""

from __future__ import annotations

from pathlib import Path

from .pages import RENDERERS
from .render_assets import load_assets
from .spec import DetailSpec


def render_spec(spec: DetailSpec, out_dir: Path, *, base_dir: Path | None = None,
                allowed_roots=None) -> list[Path]:
    """Render every page of ``spec`` into ``out_dir`` as ``NN_<type>.jpg``.

    Relative asset paths resolve against ``base_dir`` (usually the spec's folder)
    and, when ``allowed_roots`` is given, must stay inside it.
    """
    assets = load_assets(spec, base_dir, allowed_roots)   # before mkdir: a refused
    out_dir.mkdir(parents=True, exist_ok=True)            # asset leaves no output folder
    outputs: list[Path] = []
    for i, page in enumerate(spec.pages, start=1):
        painter = RENDERERS[page.type](spec, page, assets)
        outputs.append(painter.save(out_dir / f"{i:02d}_{page.type}.jpg"))
    return outputs
