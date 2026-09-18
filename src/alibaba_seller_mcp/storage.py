"""Local, file-backed persistence for OAuth tokens and usage records.

State lives under the configured state dir (default ``~/.alibaba_seller_mcp``).
This is deliberately dependency-free (plain JSON / JSONL) so the server has no
database requirement. Tokens are secrets — the state dir is gitignored.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any


def atomic_write(path: Path, text: str) -> None:
    """Write via a temp file + rename so a crash never leaves a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


class TokenStore:
    """Stores OAuth tokens per seller account in a single JSON file.

    Shape: ``{"accounts": {"<account_key>": {access_token, refresh_token,
    expires_at, ...}}, "default": "<account_key>"}``.
    """

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {"accounts": {}, "default": None}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"accounts": {}, "default": None}

    def save(self, account_key: str, token: dict[str, Any], *, make_default: bool = True) -> None:
        with self._lock:
            data = self._read()
            data.setdefault("accounts", {})[account_key] = token
            if make_default or not data.get("default"):
                data["default"] = account_key
            atomic_write(self._path, json.dumps(data, ensure_ascii=False, indent=2))

    def get(self, account_key: str | None = None) -> dict[str, Any] | None:
        data = self._read()
        key = account_key or data.get("default")
        if not key:
            return None
        return data.get("accounts", {}).get(key)

    def list_accounts(self) -> dict[str, Any]:
        data = self._read()
        return {
            "default": data.get("default"),
            "accounts": {
                k: {
                    "expires_at": v.get("expires_at"),
                    "expired": is_expired(v),
                    "has_refresh_token": bool(v.get("refresh_token")),
                }
                for k, v in data.get("accounts", {}).items()
            },
        }


def is_expired(token: dict[str, Any], *, skew: int = 60) -> bool:
    """True when ``token`` is past (or within ``skew`` seconds of) its expiry."""
    exp = token.get("expires_at")
    if not exp:
        return False
    return time.time() >= (float(exp) - skew)


class UsageStore:
    """Append-only JSONL log of AI token-usage records."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict[str, Any]]:
        if not self._path.exists():
            return []
        records: list[dict[str, Any]] = []
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return records
