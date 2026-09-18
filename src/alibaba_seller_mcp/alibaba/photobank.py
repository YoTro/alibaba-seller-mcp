"""Photo-bank uploads and their content-hash cache.

``photobank.upload`` is the only way an image reaches a listing: it returns a
``file_id`` / ``photobank_url`` pair that ``scImages`` and ``detailImage`` refer
to. Uploads are cached by content hash so republishing a product does not
re-upload photos that have not changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..config import Config
from ..files.readers import read_image
from ..storage import atomic_write
from .client import AlibabaClient
from .errors import AlibabaError
from .values import str_or_none


class _UploadCache:
    """content-hash -> {file_id, url}, persisted as JSON in the state dir."""

    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._read().get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        data = self._read()
        data[key] = value
        atomic_write(self._path, json.dumps(data, ensure_ascii=False, indent=2))


def _safe_file_name(name: str) -> str:
    """Photo-bank file names allow only letters, digits, and Chinese — strip the
    rest (spaces, parentheses, …) from the stem, keep a lowercase extension."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "jpg"
    cleaned = re.sub(r"[^0-9A-Za-z一-鿿]", "", stem) or "image"
    return f"{cleaned}.{ext.lower()}"



class PhotoBank:
    """Uploads images to the seller's photo bank (cached by content hash)."""

    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client
        self._cache = _UploadCache(config.state_dir / "photobank_cache.json")

    # ── media upload ────────────────────────────────────────────────────────
    def upload(
        self, local_path: str, access_token: str, *, group_id: str | int | None = None
    ) -> dict[str, Any]:
        """Upload one local image to the photo bank; return {file_id, url, cached}.

        Sends ``image_bytes`` as multipart (excluded from the signature) plus
        ``file_name`` / ``extra_context`` / ``group_id``. Caches by content hash.
        """
        asset = read_image(local_path, load_bytes=True)
        cache_key = f"{group_id}:{hashlib.sha256(asset.data).hexdigest()}"
        hit = self._cache.get(cache_key)
        if hit:
            return {**hit, "cached": True}

        # Filenames allow only letters/digits/Chinese — strip spaces/()/etc.
        safe_name = _safe_file_name(asset.filename)
        params: dict[str, Any] = {"file_name": safe_name, "extra_context": "{}"}
        if group_id is not None:
            params["group_id"] = str(group_id)
        files = {"image_bytes": (safe_name, asset.data, asset.content_type)}
        # photobank.upload lives on the legacy TOP gateway (/sync + `session`).
        body = self.client.call(
            self.config.method_photo_upload, params, access_token=access_token,
            files=files, protocol="sync",
        )
        resp = body.get("upload_image_response") or {}
        result = {"file_id": str_or_none(resp.get("file_id")), "url": resp.get("photobank_url")}
        if not result["url"] and not result["file_id"]:
            raise AlibabaError(f"photobank.upload returned no file_id/url: {body}")
        self._cache.put(cache_key, result)
        return {**result, "cached": False}

    # ── publish ───────────────────────────────────────────────────────────
