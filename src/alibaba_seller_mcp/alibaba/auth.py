"""Seller authorization: OAuth 2.0 authorization-code flow + token lifecycle.

Flow:
  1. ``build_authorize_url()`` -> seller opens it, logs in, grants access, and is
     redirected to your ``redirect_uri`` with ``?code=...``.
  2. ``exchange_code(code)`` -> swaps the code for an access/refresh token pair
     (System API ``/auth/token/create``) and persists it.
  3. ``get_valid_token()`` -> returns a live access token, refreshing via
     ``/auth/token/refresh`` when it is near expiry.
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from ..config import Config
from ..storage import TokenStore, is_expired
from .client import AlibabaClient
from .errors import AlibabaAuthError

TOKEN_CREATE_METHOD = "/auth/token/create"
TOKEN_REFRESH_METHOD = "/auth/token/refresh"


class SellerAuth:
    def __init__(self, config: Config, client: AlibabaClient, store: TokenStore):
        self.config = config
        self.client = client
        self.store = store

    # ── step 1: authorize URL ────────────────────────────────────────────
    def build_authorize_url(self, state: str | None = None, **extra: str) -> str:
        self.config.require_alibaba()
        if not self.config.redirect_uri:
            raise AlibabaAuthError(
                "ALIBABA_REDIRECT_URI is not set; register a callback URL in the "
                "app console and set it in the environment."
            )
        params = {
            "response_type": "code",
            "force_auth": self.config.auth_force_auth,
            "client_id": self.config.app_key,
            "redirect_uri": self.config.redirect_uri,
        }
        if state:
            params["state"] = state
        params.update(extra)
        return f"{self.config.authorize_url}?{urlencode(params)}"

    @staticmethod
    def parse_code_from_url(url: str) -> str:
        """Extract the ``code`` query param from a redirected callback URL."""
        query = parse_qs(urlparse(url.strip()).query)
        code = (query.get("code") or [None])[0]
        if not code:
            raise AlibabaAuthError(
                f"No ?code= found in the URL. Paste the full address you were "
                f"redirected to. Got: {url[:200]}"
            )
        return code

    # ── step 2: exchange code ─────────────────────────────────────────────
    def exchange_code(self, code: str) -> dict[str, Any]:
        # Per docs, /auth/token/create takes only `code` (a system API; no token).
        body = self.client.call(TOKEN_CREATE_METHOD, {"code": code})
        token = self._normalize_token(body)
        self.store.save(token["account_key"], token)
        return token

    # ── step 3: valid token (auto-refresh) ────────────────────────────────
    def get_valid_token(self, account_key: str | None = None) -> str:
        token = self.store.get(account_key)
        if not token:
            raise AlibabaAuthError(
                "No stored authorization. Run the authorize flow first "
                "(build the authorize URL, then complete it with the code)."
            )
        if is_expired(token):
            token = self.refresh(token)
        return token["access_token"]

    def refresh(self, token: dict[str, Any]) -> dict[str, Any]:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            raise AlibabaAuthError(
                "Stored token is expired and has no refresh_token; re-authorize."
            )
        body = self.client.call(TOKEN_REFRESH_METHOD, {"refresh_token": refresh_token})
        new_token = self._normalize_token(body, fallback=token)
        self.store.save(new_token["account_key"], new_token)
        return new_token

    def status(self, account_key: str | None = None) -> dict[str, Any]:
        token = self.store.get(account_key)
        if not token:
            return {"authorized": False, "accounts": self.store.list_accounts()}
        return {
            "authorized": True,
            "account_key": token.get("account_key"),
            "expires_at": token.get("expires_at"),
            "expired": is_expired(token),
            "has_refresh_token": bool(token.get("refresh_token")),
            "accounts": self.store.list_accounts(),
        }

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _normalize_token(body: dict[str, Any], fallback: dict[str, Any] | None = None) -> dict[str, Any]:
        fallback = fallback or {}
        access_token = body.get("access_token") or body.get("accessToken")
        if not access_token:
            raise AlibabaAuthError(f"Token response missing access_token: {body}")

        expires_in = body.get("expires_in") or body.get("expire_time")
        try:
            expires_at = time.time() + float(expires_in) if expires_in else None
        except (TypeError, ValueError):
            expires_at = None

        # Identify the account so multiple sellers can be stored side by side.
        account_key = (
            str(
                body.get("account")
                or body.get("havana_id")
                or body.get("seller_id")
                or body.get("user_id")
                or body.get("aliId")
                or fallback.get("account_key")
                or "default"
            )
        )
        return {
            "account_key": account_key,
            "access_token": access_token,
            "refresh_token": body.get("refresh_token") or fallback.get("refresh_token"),
            "expires_at": expires_at,
            "raw": body,
        }
