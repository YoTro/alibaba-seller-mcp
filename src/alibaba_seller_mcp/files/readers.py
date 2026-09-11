"""Read product assets (images, videos) and price data from local files.

Images:  validated + measured with Pillow, returned with bytes for upload.
Videos:  validated by extension/size (the platform ingests the uploaded bytes;
         we do not transcode).
Prices:  CSV or JSON. CSV expects a header row; JSON expects a list of objects
         or a single object. Recognised fields are normalised.
"""

from __future__ import annotations

import csv
import io
import json
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm"}

# Main-image guidance: <= 5 MB; recommended > 640x640; aspect ratio 3:4–4:3
# (square / 1000x1000 best).
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MIN_IMAGE_SIDE = 640
_MIN_RATIO = 3 / 4    # 0.75
_MAX_RATIO = 4 / 3    # 1.333…

# Detail-image guidance: <= 3 MB; recommended width >= 1200 px; height may be very
# long (long/stitched images are fine — no aspect-ratio limit).
MAX_DETAIL_BYTES = 3 * 1024 * 1024
MIN_DETAIL_WIDTH = 1200


class FileIngestError(Exception):
    """A local file could not be read or failed validation."""


@dataclass
class ImageAsset:
    path: str
    filename: str
    content_type: str
    size_bytes: int
    width: int | None
    height: int | None
    data: bytes = field(repr=False, default=b"")

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
            "width": self.width,
            "height": self.height,
        }


@dataclass
class VideoAsset:
    path: str
    filename: str
    content_type: str
    size_bytes: int
    data: bytes = field(repr=False, default=b"")

    def summary(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "filename": self.filename,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        }


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise FileIngestError(f"File not found: {path}")
    if not p.is_file():
        raise FileIngestError(f"Not a file: {path}")
    return p


def read_image(path: str, *, load_bytes: bool = True) -> ImageAsset:
    p = _resolve(path)
    if p.suffix.lower() not in IMAGE_EXTS:
        raise FileIngestError(
            f"Unsupported image extension {p.suffix!r}; supported: {sorted(IMAGE_EXTS)}"
        )
    data = p.read_bytes()
    width = height = None
    content_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
    try:
        from PIL import Image  # local import so the module loads without Pillow

        with Image.open(io.BytesIO(data)) as img:
            width, height = img.size
            fmt = (img.format or "").lower()
            if fmt:
                content_type = f"image/{'jpeg' if fmt == 'jpg' else fmt}"
    except Exception as exc:  # noqa: BLE001 — surface a clean validation error
        raise FileIngestError(f"Not a valid image ({path}): {exc}") from exc

    return ImageAsset(
        path=str(p),
        filename=p.name,
        content_type=content_type,
        size_bytes=len(data),
        width=width,
        height=height,
        data=data if load_bytes else b"",
    )


def image_quality_issues(asset: "ImageAsset", kind: str = "main") -> list[str]:
    """Advisory checks against the product-image guidance (does not raise).

    ``kind="main"`` — ≤5 MB, >640×640, aspect ratio 3:4–4:3.
    ``kind="detail"`` — ≤3 MB, width ≥1200 px; height may be arbitrarily long
    (long/stitched detail images are allowed, so no aspect-ratio check)."""
    issues: list[str] = []
    if kind == "detail":
        if asset.size_bytes > MAX_DETAIL_BYTES:
            issues.append(f"{asset.filename}: {asset.size_bytes / 1_048_576:.1f} MB exceeds the 3 MB detail-image limit")
        if asset.width and asset.width < MIN_DETAIL_WIDTH:
            issues.append(f"{asset.filename}: width {asset.width}px is below the recommended 1200px")
        return issues
    # main image
    if asset.size_bytes > MAX_IMAGE_BYTES:
        issues.append(f"{asset.filename}: {asset.size_bytes / 1_048_576:.1f} MB exceeds the 5 MB limit")
    if asset.width and asset.height:
        if asset.width < MIN_IMAGE_SIDE or asset.height < MIN_IMAGE_SIDE:
            issues.append(f"{asset.filename}: {asset.width}x{asset.height} is below the recommended 640x640")
        ratio = asset.width / asset.height
        if not (_MIN_RATIO <= ratio <= _MAX_RATIO):
            issues.append(
                f"{asset.filename}: aspect ratio {asset.width}:{asset.height} is outside 3:4–4:3 (square is best)"
            )
    return issues


def read_video(path: str, *, load_bytes: bool = True) -> VideoAsset:
    p = _resolve(path)
    if p.suffix.lower() not in VIDEO_EXTS:
        raise FileIngestError(
            f"Unsupported video extension {p.suffix!r}; supported: {sorted(VIDEO_EXTS)}"
        )
    data = p.read_bytes() if load_bytes else b""
    content_type = mimetypes.guess_type(p.name)[0] or "video/mp4"
    return VideoAsset(
        path=str(p),
        filename=p.name,
        content_type=content_type,
        size_bytes=p.stat().st_size,
        data=data,
    )


# Common price-field aliases -> canonical name.
_PRICE_ALIASES = {
    "sku": "sku",
    "sku_id": "sku",
    "skuid": "sku",
    "variant": "sku",
    "price": "price",
    "unit_price": "price",
    "amount": "price",
    "moq": "min_order_quantity",
    "min_order_quantity": "min_order_quantity",
    "minorder": "min_order_quantity",
    "currency": "currency",
    "cur": "currency",
    "min_price": "min_price",
    "max_price": "max_price",
    "quantity": "quantity",
    "qty": "quantity",
    "stock": "quantity",
}


def _normalize_price_row(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    for key, value in row.items():
        if value is None or value == "":
            continue
        canonical = _PRICE_ALIASES.get(str(key).strip().lower())
        if canonical:
            out[canonical] = value
        else:
            extra[key] = value
    if "price" in out:
        try:
            out["price"] = float(out["price"])
        except (TypeError, ValueError):
            pass
    if extra:
        out["extra"] = extra
    return out


def read_prices(path: str) -> list[dict[str, Any]]:
    """Read price rows from a CSV or JSON file into normalised dicts."""
    p = _resolve(path)
    suffix = p.suffix.lower()
    if suffix == ".json":
        raw = json.loads(p.read_text(encoding="utf-8"))
        rows = raw if isinstance(raw, list) else [raw]
    elif suffix == ".csv":
        with p.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
    else:
        raise FileIngestError(
            f"Unsupported price file {suffix!r}; use .csv or .json"
        )
    normalized = [_normalize_price_row(r) for r in rows if isinstance(r, dict)]
    if not normalized:
        raise FileIngestError(f"No price rows found in {path}")
    return normalized
