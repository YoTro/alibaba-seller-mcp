"""Typed tool-output models (Pydantic v2).

Each MCP tool returns one of these instead of a bare ``dict[str, Any]`` so clients
get a real output schema (``additionalProperties: false``) with typed top-level
fields. Every field has a default so the error path (see ``server.tool_errors``)
can construct any model with just ``ok=False`` + ``error``; that model travels as
the structured content of an ``isError: true`` result, so a failure is visible
both at the protocol level and in the typed body. Deeply nested,
inherently-variable payloads (raw API responses, generated content) stay typed as
generic objects/arrays.
"""

from __future__ import annotations

from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, Field


class Result(BaseModel):
    """Base for every tool result: success flag + error details."""

    model_config = ConfigDict(extra="forbid")

    ok: bool = True
    error: str | None = None
    error_type: str | None = None

    # Which field carries the platform's verdict on an action this tool performed
    # — ``biz_success`` on a publish, ``success`` on a video relation, and so on.
    # ``server.tool_errors`` turns a ``False`` there into an ``isError`` result,
    # because an API that accepts the call and refuses the work is still a
    # failure the caller must see.
    #
    # Declared per model rather than sniffed by name on purpose: a tool that
    # merely REPORTS state has look-alike fields that are answers, not failures
    # (``AuthStatusResult.authorized`` is the obvious one). Only set this on a
    # result whose field judges something the tool just did.
    OUTCOME_FIELD: ClassVar[str | None] = None

    def failure_message(self) -> str:
        """Why the platform refused. Overridden where the detail is model-specific."""
        return "the platform rejected the request"


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
    OUTCOME_FIELD: ClassVar[str | None] = "biz_success"

    product_id: str | None = None
    biz_success: Any = None
    filled_fields: list[str] = Field(default_factory=list)
    # Required category attributes (icbuCatProp) still unfilled — a draft tolerates
    # these, but a real (non-draft) publish will be rejected until they are provided.
    missing_required: list[str] = Field(default_factory=list)
    response: dict[str, Any] = Field(default_factory=dict)

    def failure_message(self) -> str:
        # schema.add/update put the per-field reasons in `message`, with the
        # short form in `msg_code` (e.g. "isv.missing-parameter:productTitle").
        return str(
            self.response.get("message")
            or self.response.get("msg_code")
            or super().failure_message()
        )


class BriefPublishResult(PublishResult):
    """publish-from-brief adds enrichment/advisory info to a publish result."""

    warnings: list[str] = Field(default_factory=list)
    ai_filled: list[str] = Field(default_factory=list)
    needs_more_main_images: bool | None = None


class RenderedImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    width: int
    height: int
    size_bytes: int


class DetailImagesResult(Result):
    """Code-rendered listing media (main images + detail pages) from a spec/brief."""

    spec_path: str | None = None
    spec_source: str | None = None          # existing | ai | none
    main_images: list[RenderedImage] = Field(default_factory=list)
    detail_images: list[RenderedImage] = Field(default_factory=list)
    usage: dict[str, int] | None = None
    notes: list[str] = Field(default_factory=list)


class RenderDraftField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    filled: bool = True


class RenderDraftResult(Result):
    fields: list[RenderDraftField] = Field(default_factory=list)


class GroupChild(BaseModel):
    # group ids come back from the API as JSON numbers, but ids are strings
    # everywhere else in this server — coerce rather than reject.
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)
    group_id: str | None = None
    group_name: str | None = None


class GroupResult(Result):
    model_config = ConfigDict(extra="forbid", coerce_numbers_to_str=True)
    group_id: str | None = None
    group_name: str | None = None
    parent_id: Any = None
    parent_id2: Any = None
    children_id_list: list[Any] | None = None
    children: list[GroupChild] = Field(default_factory=list)


# ── video ────────────────────────────────────────────────────────────────────
class VideoRelateResult(Result):
    OUTCOME_FIELD: ClassVar[str | None] = "success"

    success: bool | None = None
    video_id: str | None = None
    product_id: str | None = None
    msg_code: str | None = None
    msg_info: str | None = None

    def failure_message(self) -> str:
        return str(self.msg_info or self.msg_code or super().failure_message())


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
