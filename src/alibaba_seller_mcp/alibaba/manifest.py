"""The product manifest: a ``product.json`` plus the assets it references.

A plain DTO — it resolves and confines its own paths, and knows nothing about the
publish API. ``ProductService`` and the brief flow both consume one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from ..pathsafe import ensure_allowed
from .errors import AlibabaError


class ProductManifest:
    """A product definition loaded from a ``product.json`` file (or a dict).

    Recognised keys: ``category_id`` (required), ``language`` (default en_US),
    ``publish_type`` (default "default"), ``version``, ``photobank_group_id``,
    ``fields`` (dict of schema field id -> value), ``main_images`` (list of paths),
    ``detail_images`` (list of paths), ``detail_gallery`` (option code, default
    "300"), ``video`` (path), ``price_file`` (path), ``product_group`` /
    ``group_id`` (group assignment), and ``product_attributes`` (dict keyed by
    category-attribute name or id -> value(s); auto-filled into ``icbuCatProp``
    using the schema's options/required rules). Relative paths resolve against the
    manifest's directory.
    """

    def __init__(
        self, data: dict[str, Any], base_dir: Path, *, allowed_roots: Iterable[Path] | None = None
    ):
        if "category_id" not in data:
            raise AlibabaError("Manifest is missing required key 'category_id'.")
        self.data = data
        self.base_dir = base_dir
        # If set, every asset path (images/video/price/content) must resolve here.
        self.allowed_roots = list(allowed_roots) if allowed_roots is not None else None

    @classmethod
    def load(cls, path: str, *, allowed_roots: Iterable[Path] | None = None) -> "ProductManifest":
        roots = list(allowed_roots) if allowed_roots is not None else None
        p = Path(ensure_allowed(path, roots)) if roots is not None else Path(path).expanduser()
        if not p.exists():
            raise AlibabaError(f"Manifest not found: {path}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data, p.parent, allowed_roots=roots)

    def resolve(self, rel: str) -> str:
        candidate = Path(rel).expanduser()
        if not candidate.is_absolute():
            candidate = self.base_dir / rel
        if self.allowed_roots is not None:
            return str(ensure_allowed(str(candidate), self.allowed_roots))
        return str(candidate)

    def content(self) -> dict[str, Any] | None:
        """The AI-generated structured content, from inline ``content`` or a
        ``content_file`` (e.g. ``content/detail.json``), or None."""
        if isinstance(self.data.get("content"), dict):
            return self.data["content"]
        cf = self.data.get("content_file")
        if cf:
            p = Path(self.resolve(cf))
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None
