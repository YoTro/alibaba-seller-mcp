"""Confine local filesystem access to an allowlist of roots.

Tools that accept a path (media, price files, manifests, ``save_to``) must route
it through :func:`ensure_allowed` so an untrusted MCP client cannot read or write
arbitrary files. Paths are resolved (which collapses ``..`` and symlinks), then
checked to be inside one of the configured roots.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


class PathNotAllowedError(PermissionError):
    """A path resolved outside every allowed root."""


def ensure_allowed(path: str, roots: Iterable[Path], *, for_write: bool = False) -> Path:
    """Resolve ``path`` and confirm it lies within one of ``roots``.

    Returns the resolved :class:`~pathlib.Path`. Raises :class:`PathNotAllowedError`
    otherwise. For writes the target file need not exist yet, so its parent is
    resolved; for reads the path itself is resolved (``strict=False`` either way so
    a clear error is raised rather than an OSError).
    """
    p = Path(path).expanduser()
    resolved = (p.parent.resolve() / p.name) if for_write else p.resolve()
    roots = [Path(r).expanduser().resolve() for r in roots]
    for root in roots:
        if resolved == root or root in resolved.parents:
            return resolved
    allowed = ", ".join(str(r) for r in roots) or "(none)"
    raise PathNotAllowedError(
        f"Path {path!r} resolves to {resolved}, which is outside the allowed "
        f"director(ies): {allowed}. Set ALIBABA_MCP_ALLOWED_DIRS to permit it."
    )
