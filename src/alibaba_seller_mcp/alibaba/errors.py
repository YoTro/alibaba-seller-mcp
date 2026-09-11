"""Exceptions for the Alibaba open-platform client."""

from __future__ import annotations


class AlibabaError(Exception):
    """Base class for all Alibaba client errors."""


class AlibabaConfigError(AlibabaError):
    """Missing or invalid local configuration (credentials, etc.)."""


class AlibabaAuthError(AlibabaError):
    """The seller is not authorized, or the token is expired/invalid."""


class AlibabaAPIError(AlibabaError):
    """The gateway returned a business or system error.

    The gateway reports errors in a couple of shapes; the parsed fields are
    normalised here so callers get a consistent object.
    """

    def __init__(
        self,
        code: str | None,
        message: str | None,
        *,
        request_id: str | None = None,
        sub_code: str | None = None,
        sub_message: str | None = None,
        raw: dict | None = None,
    ):
        self.code = code
        self.message = message
        self.request_id = request_id
        self.sub_code = sub_code
        self.sub_message = sub_message
        self.raw = raw or {}
        parts = [f"code={code}", f"message={message!r}"]
        if sub_code:
            parts.append(f"sub_code={sub_code}")
        if sub_message:
            parts.append(f"sub_message={sub_message!r}")
        if request_id:
            parts.append(f"request_id={request_id}")
        super().__init__(", ".join(parts))
