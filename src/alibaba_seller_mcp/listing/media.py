"""Media flow (sibling of ``publishing``): a brief's ``photos`` → main images + code-rendered detail pages.

Flow (all paths relative to the brief's folder):

    photos.hero / photos.side / photos.scenes
        ├─ prepare_main_images()  → images/main/*.jpg          (deterministic)
        └─ detail_spec.json  ──exists──▶ render_spec()         (deterministic, free)
             └─ missing / regenerate ─▶ AI writes pages → save spec → render_spec()

The spec is saved next to the brief so the seller can hand-edit copy and re-render
without another AI call. Nothing here talks to Alibaba; upload happens in the brief
publish flow.
"""

from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from ..rendering import Assets, DetailSpec, SceneAsset, Theme, prepare_main_images, render_spec

SPEC_FILENAME = "detail_spec.json"


@dataclass
class MediaPrep:
    main_paths: list[str] = field(default_factory=list)
    detail_paths: list[str] = field(default_factory=list)
    spec_path: str | None = None
    spec_source: str = "none"            # inline | existing | ai | none
    usage: dict[str, int] | None = None
    notes: list[str] = field(default_factory=list)


def photos_to_assets(photos: dict[str, Any]) -> Assets:
    scenes = []
    for s in photos.get("scenes") or []:
        if isinstance(s, str):
            scenes.append(SceneAsset(path=s))
        else:
            scenes.append(SceneAsset(path=s["path"], caption=s.get("caption", "")))
    if not photos.get("hero"):
        raise ValueError("brief.photos.hero (a white-background product render) is required")
    return Assets(hero=photos["hero"], side=photos.get("side"), scenes=scenes)


def _identity(brief: dict[str, Any]) -> tuple[str, str, str]:
    """(brand, product_name, model) from a brief, with sensible fallbacks."""
    if "brand_name" in brief:            # explicit (may be "" to print no brand on the pages)
        brand_s = brief.get("brand_name") or ""
    else:                                # else the first `brand` casing token
        brand = brief.get("brand")
        brand_s = (brand[0] if isinstance(brand, list) and brand else brand) or ""
    name = brief.get("product_name") or ""
    if not name:
        text = brief.get("brief") or brief.get("description") or ""
        head = re.split(r"[:.\n]", text, maxsplit=1)[0].strip()
        name = head[:60] or "Product"
    return str(brand_s)[:40], str(name)[:60], str(brief.get("model") or "")[:30]


def _facts_text(brief: dict[str, Any]) -> str:
    """The fact sheet the AI may use: brief text plus optional structured lists.

    ``how_to_use`` (operation steps) and ``parts`` (named parts of the product) are
    the only sources the prompt allows for the steps / callouts pages, so listing
    them here keeps the AI from inventing mechanics.
    """
    text = brief.get("brief") or brief.get("description") or ""
    steps = brief.get("how_to_use") or []
    if steps:
        text += "\nHow to use (operation steps, in order):\n" + "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))
    parts = brief.get("parts") or []
    if parts:
        text += "\nNamed parts of the product: " + "; ".join(str(p) for p in parts)
    return text


def _side_image_bytes(path: Path, max_side: int = 800) -> bytes:
    im = Image.open(path).convert("RGB")
    im.thumbnail((max_side, max_side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def prepare_brief_media(
    brief: dict[str, Any],
    base_dir: Path,
    generator: Any = None,
    *,
    regenerate_spec: bool = False,
    extra_instructions: str = "",
    out_root: Path | None = None,
    language: str = "English",
    allowed_roots=None,
) -> MediaPrep:
    """Prepare main + detail images for a brief that carries ``photos``.

    ``generator`` (an ``ai.DetailSpecGenerator``) is only needed when no
    ``detail_spec.json`` exists yet (or ``regenerate_spec`` is set).
    """
    photos = brief.get("photos") or {}
    prep = MediaPrep()
    if not photos:
        return prep
    assets = photos_to_assets(photos)
    out_root = out_root or (base_dir / "images")

    prep.main_paths = [str(p) for p in prepare_main_images(assets, out_root / "main", base_dir=base_dir,
                                                           allowed_roots=allowed_roots)]

    spec_path = base_dir / SPEC_FILENAME
    brand, product_name, model = _identity(brief)
    theme = Theme(**(photos.get("theme") or brief.get("theme") or {}))
    if brief.get("ai", True) is False:
        generator = None  # manual mode: never call Claude

    inline = brief.get("detail_spec")
    if inline:
        # Spec inline in the brief (single-file, fully manual control) — always wins.
        spec = DetailSpec(
            brand=inline.get("brand") or brand, product_name=inline.get("product_name") or product_name,
            model=inline.get("model") or model, footer=inline.get("footer", ""),
            width=inline.get("width", 1200), theme=Theme(**inline["theme"]) if inline.get("theme") else theme,
            assets=assets, pages=inline["pages"],
        )
        prep.spec_source = "inline"
        prep.spec_path = None
        prep.detail_paths = [str(p) for p in render_spec(spec, out_root / "detail", base_dir=base_dir,
                                                         allowed_roots=allowed_roots)]
        return prep

    if spec_path.exists() and not regenerate_spec:
        spec = DetailSpec.model_validate(json.loads(spec_path.read_text(encoding="utf-8")))
        # keep the spec's own pages; refresh assets/theme from the brief so photo
        # swaps take effect without editing the spec by hand
        spec = spec.model_copy(update={"assets": assets, "theme": theme})
        prep.spec_source = "existing"
    else:
        if generator is None:
            raise ValueError(
                "no detail spec: add `detail_spec` to the brief (or a detail_spec.json beside it), "
                "or set `ai: true` to let Claude write one"
            )
        side_src = base_dir / (assets.side or assets.hero)
        gen = generator.generate(
            brand=brand, product_name=product_name, model=model,
            facts=_facts_text(brief),
            features=brief.get("features"),
            oem_odm=brief.get("oem_odm", ""),
            scene_captions=[s.caption or f"Scene {i + 1}" for i, s in enumerate(assets.scenes)],
            has_side_render=bool(assets.side),
            language=language,
            extra_instructions="\n".join(s for s in (brief.get("instructions", ""), extra_instructions) if s),
            product_image=_side_image_bytes(side_src) if side_src.exists() else None,
        )
        spec = DetailSpec(brand=brand, product_name=product_name, model=model,
                          theme=theme, assets=assets, pages=gen["pages"])
        spec_path.write_text(json.dumps(spec.model_dump(exclude_none=True), ensure_ascii=False, indent=2),
                             encoding="utf-8")
        prep.spec_source = "ai"
        prep.usage = gen.get("usage")
        prep.notes.append(f"detail spec written by AI → {spec_path.name}; edit it and re-render to change copy")

    prep.spec_path = str(spec_path)
    prep.detail_paths = [str(p) for p in render_spec(spec, out_root / "detail", base_dir=base_dir,
                                                     allowed_roots=allowed_roots)]
    return prep
