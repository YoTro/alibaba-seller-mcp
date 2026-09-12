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


def ensure_asset_allowed(path: str, base_dir: Path | None, roots: Iterable[Path] | None) -> Path:
    """Resolve an asset path *named inside a file* (a brief's ``photos``, a spec's
    ``assets``, a manifest's images) and confine it to ``roots``.

    The tool's own argument is checked by :func:`ensure_allowed`, but the paths a
    brief or spec points at are content, and content is as untrusted as arguments:
    without this, a brief could name any file on disk and have it read, rendered
    into a listing image and uploaded to the photo bank. Relative paths resolve
    against ``base_dir`` (the file's own folder); ``roots=None`` disables the check
    for in-process callers that have already vetted the paths.
    """
    p = Path(path).expanduser()
    full = p if p.is_absolute() or base_dir is None else base_dir / p
    if roots is None:
        return full
    return ensure_allowed(str(full), roots)
