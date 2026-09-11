"""Signed REST client for the Alibaba.com Global B2B gateway.

Signing (confirmed against the open-platform docs):

    1. Collect all request params (system + business), excluding the ``sign``
       field itself and any byte-array/file params.
    2. Sort by parameter name in ASCII ascending order.
    3. Build the base string:  ``api_path + key1 value1 key2 value2 ...``
       where ``api_path`` is the path after the ``/rest`` gateway root
       (e.g. ``/icbu/product/create``).
    4. sign = HMAC-SHA256(base_string, app_secret).hexdigest().upper()

System params sent on every call: ``app_key``, ``timestamp`` (epoch millis),
``sign_method`` (``sha256``), ``sign``, and ``access_token`` for authorized calls.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, BinaryIO

import requests

from ..config import Config
from .errors import AlibabaAPIError, AlibabaError


def is_path_style(method: str) -> bool:
    """Which gateway protocol a method uses, matching the official iop SDK.

    * **Path style (GOP)** — the method is a leading-slash path such as
      ``/auth/token/create``. The request is POSTed to ``gateway + path`` and the
      signature base is ``path + sorted(key+value)``.
    * **Method style (TOP)** — the method is a dotted name such as
      ``alibaba.icbu.product.list``. The name travels as a ``method`` parameter,
      the request is POSTed to the gateway root, and the signature base is
      ``sorted(key+value)`` with **no** path prefix.
    """
    return method.startswith("/")


class AlibabaClient:
    def __init__(self, config: Config, *, session: requests.Session | None = None):
        self.config = config
        self._session = session or requests.Session()

    # ── signing ──────────────────────────────────────────────────────────
    def _sign(self, api: str, params: dict[str, str], *, path_style: bool | None = None) -> str:
        # Signature base is the api name (leading-slash path for GOP, dotted
        # method name for TOP) followed by the ASCII-sorted key+value pairs.
        # In the TOP case `method` is itself one of those params.
        base = api + "".join(f"{k}{params[k]}" for k in sorted(params) if k != "sign")
        digest = hmac.new(
            self.config.app_secret.encode("utf-8"),
            base.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return digest.upper()

    def _sign_sync(self, params: dict[str, str]) -> str:
        """TOP /sync signature: HMAC-SHA256 over sorted key+value, NO api prefix."""
        base = "".join(f"{k}{params[k]}" for k in sorted(params) if k != "sign")
        return hmac.new(
            self.config.app_secret.encode("utf-8"), base.encode("utf-8"), hashlib.sha256
        ).hexdigest().upper()

    @staticmethod
    def _stringify(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return str(value)

    # ── request ──────────────────────────────────────────────────────────
    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        access_token: str | None = None,
        files: dict[str, tuple[str, BinaryIO | bytes, str]] | None = None,
        timeout: float = 30.0,
        protocol: str = "rest",
    ) -> dict[str, Any]:
        """Make one signed call and return the parsed JSON body.

        ``protocol``:
          * ``"rest"`` (default) — IOP/GOP gateway (``/rest``). Sign base is the api
            name + sorted key+value; token param ``access_token``.
          * ``"sync"`` — legacy TOP gateway (``/sync``), used by some ICBU APIs such
            as ``photobank.upload``. Sign base is sorted key+value with NO api-name
            prefix; token param ``session``; params go on the query string; the
            response is unwrapped from its ``<method>_response`` envelope.

        ``files`` (name -> (filename, content, content_type)) are sent as multipart
        and excluded from the signature base. Raises :class:`AlibabaAPIError` on a
        gateway/business error.
        """
        self.config.require_alibaba()
        if protocol == "sync":
            return self._call_sync(method, params, access_token, files, timeout)

        path_style = is_path_style(method)
        signed: dict[str, str] = {
            "app_key": self.config.app_key,
            "sign_method": self.config.sign_method,
            "timestamp": str(int(time.time() * 1000)),
        }
        if not path_style:
            # TOP protocol: the dotted method name travels as a parameter.
            signed["method"] = method
        if access_token:
            signed["access_token"] = access_token
        for key, value in (params or {}).items():
            if value is None:
                continue
            signed[key] = self._stringify(value)

        signed["sign"] = self._sign(method, signed, path_style=path_style)

        url = f"{self.config.gateway}{method}" if path_style else self.config.gateway
        try:
            if files:
                resp = self._session.post(url, data=signed, files=files, timeout=timeout)
            else:
                resp = self._session.post(url, data=signed, timeout=timeout)
        except requests.RequestException as exc:
            raise AlibabaError(f"HTTP request to {url} failed: {exc}") from exc

        return self._parse(resp)

    def _call_sync(
        self,
        method: str,
        params: dict[str, Any] | None,
        access_token: str | None,
        files: dict | None,
        timeout: float,
    ) -> dict[str, Any]:
        signed: dict[str, str] = {
            "app_key": self.config.app_key,
            "sign_method": self.config.sign_method,
            "timestamp": str(int(time.time() * 1000)),
            "method": method,
        }
        if access_token:
            signed["session"] = access_token
        for key, value in (params or {}).items():
            if value is not None:
                signed[key] = self._stringify(value)
        signed["sign"] = self._sign_sync(signed)

        url = self.config.sync_gateway
        try:
            # params on the query string; file (if any) in the multipart body
            resp = self._session.post(url, params=signed, files=files, timeout=timeout)
        except requests.RequestException as exc:
            raise AlibabaError(f"HTTP request to {url} failed: {exc}") from exc

        body = self._parse(resp)
        # Unwrap the "<method>_response" envelope, e.g.
        # {"alibaba_icbu_photobank_upload_response": {...}} -> {...}
        env_key = method.replace(".", "_") + "_response"
        inner = body.get(env_key)
        if isinstance(inner, dict):
            return {"code": body.get("code", "0"), **inner}
        return body

    # ── response parsing ─────────────────────────────────────────────────
    @staticmethod
    def _parse(resp: requests.Response) -> dict[str, Any]:
        try:
            body = resp.json()
        except ValueError as exc:
            raise AlibabaError(
                f"Gateway returned non-JSON response (HTTP {resp.status_code}): "
                f"{resp.text[:500]}"
            ) from exc

        # IOP-style error: {"code": "...", "message": "...", "request_id": "..."}
        # (code "0" or absent means success). Business sub-errors may appear
        # under "sub_code"/"sub_message".
        code = body.get("code")
        if code not in (None, "0", 0):
            raise AlibabaAPIError(
                code=str(code),
                message=body.get("message") or body.get("msg"),
                request_id=body.get("request_id"),
                sub_code=body.get("sub_code"),
                sub_message=body.get("sub_message"),
                raw=body,
            )

        # TOP-style error: {"error_response": {"code", "msg", "sub_code", ...}}
        err = body.get("error_response")
        if isinstance(err, dict):
            raise AlibabaAPIError(
                code=str(err.get("code")),
                message=err.get("msg"),
                request_id=err.get("request_id"),
                sub_code=err.get("sub_code"),
                sub_message=err.get("sub_msg"),
                raw=body,
            )

        return body
