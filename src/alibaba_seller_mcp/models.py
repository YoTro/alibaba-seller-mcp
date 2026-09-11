"""Typed tool-output models (Pydantic v2).

Each MCP tool returns one of these instead of a bare ``dict[str, Any]`` so clients
get a real output schema (``additionalProperties: false``) with typed top-level
fields. Every field has a default so the error path (see ``server.tool_errors``)
can construct any model with just ``ok=False`` + ``error``. Deeply nested,
inherently-variable payloads (raw API responses, generated content) stay typed as
generic objects/arrays.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Result(BaseModel):
    """Base for every tool result: success flag + error details."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    error: str | None = None
    error_type: str | None = None


# ── auth ──────────────────────────────────────────────────────────────────────
class AuthUrlResult(Result):
    authorize_url: str | None = None


class CompleteAuthResult(Result):
    account_key: str | None = None
    expires_at: float | None = None
    has_refresh_token: bool | None = None


class AuthStatusResult(Result):
    authorized: bool | None = None
    account_key: str | None = None
    expires_at: float | None = None
    expired: bool | None = None
    has_refresh_token: bool | None = None
    accounts: dict[str, Any] = Field(default_factory=dict)


class RawCallResult(Result):
    response: dict[str, Any] = Field(default_factory=dict)


# ── local files ─────────────────────────────────────────────────────────────
class MediaInfoResult(Result):
    kind: str | None = None
    path: str | None = None
    filename: str | None = None
    content_type: str | None = None
    size_bytes: int | None = None
    width: int | None = None
    height: int | None = None


class PriceFileResult(Result):
    count: int | None = None
    rows: list[dict[str, Any]] = Field(default_factory=list)


# ── products ─────────────────────────────────────────────────────────────────
class UploadImageResult(Result):
    file_id: str | None = None
    url: str | None = None
    cached: bool | None = None


class SchemaFieldSummary(BaseModel):
    model_config = ConfigDict(extra="allow")  # field summaries carry variable keys
    id: str
    type: str


class SchemaResult(Result):
    field_count: int | None = None
    required: list[str] = Field(default_factory=list)
    fields: list[dict[str, Any]] = Field(default_factory=list)


class CategoryAttributesResult(Result):
    cat_id: str | None = None
    count: int | None = None
    required: list[str] = Field(default_factory=list)
    attributes: list[dict[str, Any]] = Field(default_factory=list)


class PublishResult(Result):
    product_id: str | None = None
    biz_success: Any = None
    filled_fields: list[str] = Field(default_factory=list)
    # Required category attributes (icbuCatProp) still unfilled — a draft tolerates
    # these, but a real (non-draft) publish will be rejected until they are provided.
    missing_required: list[str] = Field(default_factory=list)
    response: dict[str, Any] = Field(default_factory=dict)


class BriefPublishResult(PublishResult):
    """publish-from-brief adds enrichment/advisory info to a publish result."""

    warnings: list[str] = Field(default_factory=list)
    ai_filled: list[str] = Field(default_factory=list)
    needs_more_main_images: bool | None = None


class RenderDraftField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    filled: bool = True


class RenderDraftResult(Result):
    fields: list[RenderDraftField] = Field(default_factory=list)


class GroupChild(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_id: str | None = None
    group_name: str | None = None


class GroupResult(Result):
    group_id: str | None = None
    group_name: str | None = None
    parent_id: Any = None
    parent_id2: Any = None
    children_id_list: list[Any] | None = None
    children: list[GroupChild] = Field(default_factory=list)


# ── video ────────────────────────────────────────────────────────────────────
class VideoRelateResult(Result):
    success: bool | None = None
    video_id: str | None = None
    product_id: str | None = None
    msg_code: str | None = None
    msg_info: str | None = None


class VideoListRelatedResult(Result):
    product_ids: list[str] = Field(default_factory=list)
    msg_code: str | None = None
    msg_info: str | None = None


class VideoQueryResult(Result):
    raw: dict[str, Any] = Field(default_factory=dict)


# ── AI generation ─────────────────────────────────────────────────────────────
class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProductDetailResult(Result):
    model: str | None = None
    content: dict[str, Any] | None = None
    raw_text: str | None = None
    usage: Usage | None = None
    saved_to: str | None = None


class SocialContentResult(Result):
    model: str | None = None
    posts: list[dict[str, Any]] | None = None
    raw_text: str | None = None
    usage: Usage | None = None


# ── usage stats ────────────────────────────────────────────────────────────────
class UsageStatsResult(Result):
    call_count: int | None = None
    group_by: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    totals: dict[str, Any] = Field(default_factory=dict)
    groups: dict[str, Any] = Field(default_factory=dict)
