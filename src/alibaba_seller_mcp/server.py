"""MCP server exposing Alibaba Global B2B seller tools.

Run with:  python -m alibaba_seller_mcp   (or the `alibaba-seller-mcp` script)
Transport: stdio (works with Claude Desktop / Claude Code and any MCP client).

Every tool declares MCP annotations (read-only / destructive / idempotent /
open-world hints), returns a typed Pydantic model (structured output), and — for
tools that touch the filesystem — confines paths to the configured allowlist.
"""

# NOTE: intentionally NOT `from __future__ import annotations` — the MCP SDK infers
# each tool's output schema from its (real, resolved) return type, which postponed
# string annotations would hide.

import functools
import json
import os
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from .ai.product_detail import ProductDetailGenerator
from .ai.social import SocialContentGenerator
from .alibaba.auth import SellerAuth
from .alibaba.categories import CategoryService
from .alibaba.client import AlibabaClient
from .alibaba.errors import AlibabaError
from .alibaba.groups import GroupService
from .alibaba.products import ProductManifest, ProductService
from .alibaba.videos import VideoService
from .config import Config, load_config
from .files.readers import FileIngestError, read_image, read_video
from .models import (
    AuthStatusResult,
    AuthUrlResult,
    BriefPublishResult,
    CategoryAttributesResult,
    CompleteAuthResult,
    GroupChild,
    GroupResult,
    MediaInfoResult,
    PriceFileResult,
    ProductDetailResult,
    PublishResult,
    RawCallResult,
    RenderDraftField,
    RenderDraftResult,
    Result,
    SchemaResult,
    SocialContentResult,
    UploadImageResult,
    Usage,
    UsageStatsResult,
    VideoListRelatedResult,
    VideoQueryResult,
    VideoRelateResult,
)
from .pathsafe import PathNotAllowedError, ensure_allowed
from .storage import TokenStore, UsageStore
from .usage.tracker import UsageTracker

mcp = MCPServer("alibaba-seller-mcp")

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".webm"}


class Context:
    """Lazily-constructed shared services (built on first use)."""

    def __init__(self) -> None:
        self._config: Config | None = None
        self._client: AlibabaClient | None = None
        self._auth: SellerAuth | None = None
        self._products: ProductService | None = None
        self._videos: VideoService | None = None
        self._groups: GroupService | None = None
        self._categories: CategoryService | None = None
        self._tracker: UsageTracker | None = None
        self._social: SocialContentGenerator | None = None
        self._detail: ProductDetailGenerator | None = None

    @property
    def config(self) -> Config:
        if self._config is None:
            self._config = load_config()
        return self._config

    @property
    def client(self) -> AlibabaClient:
        if self._client is None:
            self._client = AlibabaClient(self.config)
        return self._client

    @property
    def auth(self) -> SellerAuth:
        if self._auth is None:
            self._auth = SellerAuth(self.config, self.client, TokenStore(self.config.token_store_path))
        return self._auth

    @property
    def products(self) -> ProductService:
        if self._products is None:
            self._products = ProductService(self.config, self.client)
        return self._products

    @property
    def videos(self) -> VideoService:
        if self._videos is None:
            self._videos = VideoService(self.config, self.client)
        return self._videos

    @property
    def groups(self) -> GroupService:
        if self._groups is None:
            self._groups = GroupService(self.config, self.client)
        return self._groups

    @property
    def categories(self) -> CategoryService:
        if self._categories is None:
            self._categories = CategoryService(self.config, self.client)
        return self._categories

    @property
    def tracker(self) -> UsageTracker:
        if self._tracker is None:
            self._tracker = UsageTracker(UsageStore(self.config.usage_log_path))
        return self._tracker

    @property
    def social(self) -> SocialContentGenerator:
        if self._social is None:
            self._social = SocialContentGenerator(self.config, self.tracker)
        return self._social

    @property
    def detail(self) -> ProductDetailGenerator:
        if self._detail is None:
            self._detail = ProductDetailGenerator(self.config, self.tracker)
        return self._detail


ctx = Context()


def tool_errors(model: type[Result]) -> Callable:
    """Decorator: on a known error, return the tool's result model with ok=False
    instead of raising a raw traceback. `model` is the tool's return type."""

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except (
                AlibabaError,
                FileIngestError,
                PathNotAllowedError,
                ValueError,
                RuntimeError,
            ) as exc:
                return model(ok=False, error=str(exc), error_type=type(exc).__name__)

        return wrapper

    return deco


def _anno(
    title: str,
    *,
    read_only: bool = False,
    destructive: bool | None = None,
    idempotent: bool | None = None,
    open_world: bool = False,
) -> ToolAnnotations:
    return ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


def _safe_read(path: str) -> str:
    return str(ensure_allowed(path, ctx.config.allowed_paths))


def _safe_write(path: str) -> str:
    return str(ensure_allowed(path, ctx.config.allowed_paths, for_write=True))


# ── seller authorization ────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Get seller authorize URL", read_only=True))
@tool_errors(AuthUrlResult)
def alibaba_get_authorize_url(state: str = "") -> AuthUrlResult:
    """Get the OAuth URL the seller opens to grant this app access.

    Send the returned URL to the seller. After they approve, the platform
    redirects to the app's configured callback with a `?code=...`; pass that code
    to `alibaba_complete_authorization`.
    """
    return AuthUrlResult(authorize_url=ctx.auth.build_authorize_url(state=state or None))


@mcp.tool(annotations=_anno("Complete seller authorization", idempotent=False, open_world=True))
@tool_errors(CompleteAuthResult)
def alibaba_complete_authorization(code: str) -> CompleteAuthResult:
    """Exchange an OAuth `code` (from the callback) for an access token and store it."""
    token = ctx.auth.exchange_code(code)
    return CompleteAuthResult(
        account_key=token["account_key"],
        expires_at=token["expires_at"],
        has_refresh_token=bool(token.get("refresh_token")),
    )


@mcp.tool(annotations=_anno("Complete authorization from URL", idempotent=False, open_world=True))
@tool_errors(CompleteAuthResult)
def alibaba_complete_authorization_from_url(redirected_url: str) -> CompleteAuthResult:
    """Complete authorization from the full callback URL the seller landed on.

    Handy when the callback is a page like https://www.alibaba.com — paste the
    whole redirected address (containing `?code=...`) and this extracts the code
    and exchanges it for a token.
    """
    token = ctx.auth.exchange_code(ctx.auth.parse_code_from_url(redirected_url))
    return CompleteAuthResult(
        account_key=token["account_key"],
        expires_at=token["expires_at"],
        has_refresh_token=bool(token.get("refresh_token")),
    )


@mcp.tool(annotations=_anno("Authorization status", read_only=True))
@tool_errors(AuthStatusResult)
def alibaba_auth_status(account_key: str = "") -> AuthStatusResult:
    """Show whether a seller is authorized and when the token expires."""
    return AuthStatusResult(**ctx.auth.status(account_key or None))


# ── generic escape hatch ────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Raw API call", destructive=True, open_world=True))
@tool_errors(RawCallResult)
def alibaba_raw_call(
    method: str, params: dict[str, Any] | None = None, authorized: bool = True, account_key: str = ""
) -> RawCallResult:
    """Call any granted API by method name (e.g. `alibaba.icbu.product.get`).

    Escape hatch for testing/verifying methods. It can invoke write APIs, so it is
    not read-only. Set `authorized=False` for system/public APIs that need no token.
    """
    token = ctx.auth.get_valid_token(account_key or None) if authorized else None
    return RawCallResult(response=ctx.client.call(method, params or {}, access_token=token))


# ── local file ingestion ────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Inspect local media", read_only=True))
@tool_errors(MediaInfoResult)
def read_local_media(path: str) -> MediaInfoResult:
    """Inspect a local image or video (type, size, and image dimensions).

    The path is confined to the server's allowed directories."""
    safe = _safe_read(path)
    if Path(safe).suffix.lower() in _VIDEO_EXTS:
        return MediaInfoResult(kind="video", **read_video(safe, load_bytes=False).summary())
    return MediaInfoResult(kind="image", **read_image(safe, load_bytes=False).summary())


@mcp.tool(annotations=_anno("Read price file", read_only=True))
@tool_errors(PriceFileResult)
def read_price_file(path: str) -> PriceFileResult:
    """Read prices from a local CSV or JSON file into normalized rows.

    The path is confined to the server's allowed directories."""
    from .files.readers import read_prices

    rows = read_prices(_safe_read(path))
    return PriceFileResult(count=len(rows), rows=rows)


# ── products ─────────────────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Upload image to photo bank", idempotent=False, open_world=True))
@tool_errors(UploadImageResult)
def product_upload_image(path: str, group_id: str = "", account_key: str = "") -> UploadImageResult:
    """Upload one local image to the photo bank; returns {file_id, url}. The path
    is confined to the server's allowed directories."""
    token = ctx.auth.get_valid_token(account_key or None)
    result = ctx.products.upload_image(_safe_read(path), token, group_id=group_id or None)
    return UploadImageResult(file_id=result.get("file_id"), url=result.get("url"), cached=result.get("cached"))


@mcp.tool(annotations=_anno("Get product publish schema", read_only=True, open_world=True))
@tool_errors(SchemaResult)
def product_get_schema(
    category_id: str, language: str = "en_US", include_all: bool = False, account_key: str = ""
) -> SchemaResult:
    """Get the publish schema for a category: fillable fields, types, required
    flags, and allowed options. Inspect this before publishing."""
    token = ctx.auth.get_valid_token(account_key or None)
    desc = ctx.products.get_publish_schema(category_id, token, language=language, include_all=include_all)
    return SchemaResult(field_count=desc["field_count"], required=desc["required"], fields=desc["fields"])


@mcp.tool(annotations=_anno("Get category attributes", read_only=True, open_world=True))
@tool_errors(CategoryAttributesResult)
def category_get_attributes(category_id: str) -> CategoryAttributesResult:
    """Get a category's system-defined attributes (for `icbuCatProp`), including
    which are **required**. Public API — no authorization needed. Each attribute's
    `attr_id` maps to the publish schema field `p-<attr_id>`; `car_model` attributes
    need the hierarchical-attribute API for their next level."""
    a = ctx.categories.get_attributes(category_id)
    return CategoryAttributesResult(
        cat_id=a["cat_id"], count=a["count"], required=a["required"], attributes=a["attributes"]
    )


@mcp.tool(annotations=_anno("Publish product", idempotent=False, open_world=True))
@tool_errors(PublishResult)
def product_publish(manifest_path: str, draft: bool = False, account_key: str = "") -> PublishResult:
    """Publish a product from a manifest file (product.json), creating a new product.

    The manifest gives `category_id`, `language`, `fields`, and asset references
    (`main_images`/`detail_images`, `price_file`, `video`). Required category
    attributes (`icbuCatProp`) are auto-filled from the manifest's
    `product_attributes` (keyed by attribute name or id) using the schema's own
    options/required rules. Set `draft=True` to create a draft (not listed live) —
    recommended for verifying a manifest. `missing_required` in the result lists
    required attributes still unfilled (a real publish needs them). The manifest and
    its assets are confined to the server's allowed directories.
    """
    token = ctx.auth.get_valid_token(account_key or None)
    manifest = ProductManifest.load(manifest_path, allowed_roots=ctx.config.allowed_paths)
    r = ctx.products.publish_product(manifest, token, draft=draft)
    return PublishResult(
        product_id=r.get("product_id"),
        biz_success=r.get("biz_success"),
        filled_fields=r.get("filled_fields", []),
        missing_required=r.get("missing_required", []),
        response=r.get("response", {}),
    )


@mcp.tool(annotations=_anno("Publish product from brief (AI)", idempotent=False, open_world=True))
@tool_errors(BriefPublishResult)
def product_publish_from_brief(
    brief: dict[str, Any] | None = None,
    brief_path: str = "",
    draft: bool = True,
    account_key: str = "",
) -> BriefPublishResult:
    """Publish a product from a minimal **brief** — the seller provides only facts;
    Claude enriches the rest.

    Provide `brief` inline or a `brief_path` (JSON file, confined to allowed dirs).
    The brief needs: `brief` (short description), `category_id`, `images`
    (main 4–6 + detail), `price` ({unit, moq, tiers}), and `facts` (place_of_origin,
    material). Claude writes the title, highlights, detail modules, FAQs, custom
    parameters, and picks the descriptive category attributes from the schema's
    allowed options. Returns the draft `product_id`, `missing_required`, `ai_filled`
    (what was auto-generated), and `warnings` (e.g. fewer than 4 main images —
    AI cannot generate real product photos). Defaults to `draft=True`.
    """
    from . import brief as brief_flow

    if brief_path:
        brief = json.loads(Path(_safe_read(brief_path)).read_text(encoding="utf-8"))
    if not brief:
        raise ValueError("Provide `brief` (inline) or `brief_path`.")
    token = ctx.auth.get_valid_token(account_key or None)
    r = brief_flow.publish_from_brief(
        brief, products=ctx.products, detail=ctx.detail, token=token,
        allowed_roots=ctx.config.allowed_paths, draft=draft,
    )
    return BriefPublishResult(
        product_id=r.get("product_id"),
        biz_success=r.get("biz_success"),
        filled_fields=r.get("filled_fields", []),
        missing_required=r.get("missing_required", []),
        warnings=r.get("warnings", []),
        ai_filled=r.get("ai_filled", []),
        needs_more_main_images=r.get("needs_more_main_images"),
        response=r.get("response", {}),
    )


@mcp.tool(annotations=_anno("Read back draft product", read_only=True, open_world=True))
@tool_errors(RenderDraftResult)
def product_render_draft(
    product_id: str, category_id: str, language: str = "en_US", account_key: str = ""
) -> RenderDraftResult:
    """Read back a DRAFT product's saved fields (schema.render.draft). `product_id`
    is the plaintext numeric draft id. Useful to verify what a publish stored."""
    token = ctx.auth.get_valid_token(account_key or None)
    fields = ctx.products.render_draft(product_id, category_id, token, language=language, parsed=True)
    return RenderDraftResult(
        fields=[RenderDraftField(id=f.id, type=f.type) for f in fields if f.type != "label"]
    )


@mcp.tool(annotations=_anno("Update product", destructive=False, idempotent=True, open_world=True))
@tool_errors(PublishResult)
def product_update(product_id: str, manifest_path: str, account_key: str = "") -> PublishResult:
    """Incrementally update an existing product (schema.update) from a manifest.

    Only the fields present in the manifest are changed. Re-applying the same
    manifest yields the same state (idempotent). Paths are confined to the
    server's allowed directories.
    """
    token = ctx.auth.get_valid_token(account_key or None)
    manifest = ProductManifest.load(manifest_path, allowed_roots=ctx.config.allowed_paths)
    r = ctx.products.update_product(product_id, manifest, token)
    return PublishResult(
        product_id=r.get("product_id"),
        biz_success=r.get("biz_success"),
        filled_fields=r.get("filled_fields", []),
        missing_required=r.get("missing_required", []),
        response=r.get("response", {}),
    )


# ── product groups ────────────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Get product group", read_only=True, open_world=True))
@tool_errors(GroupResult)
def product_group_get(group_id: str = "-1", account_key: str = "") -> GroupResult:
    """Get a product group's info (name, parents, child groups). Pass group_id=-1
    (default) to list all top-level groups (returned under `children`)."""
    token = ctx.auth.get_valid_token(account_key or None)
    g = ctx.groups.get_group(token, group_id=group_id)
    return GroupResult(
        group_id=g.get("group_id"),
        group_name=g.get("group_name"),
        parent_id=g.get("parent_id"),
        parent_id2=g.get("parent_id2"),
        children_id_list=g.get("children_id_list"),
        children=[GroupChild(**c) for c in g.get("children", [])],
    )


# ── video ↔ product relation ──────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Relate video to product", destructive=False, idempotent=True, open_world=True))
@tool_errors(VideoRelateResult)
def video_relate_product(
    video_id: str, product_id: str, target: str = "main", account_key: str = ""
) -> VideoRelateResult:
    """Associate a video with a product. `target`: "main" (main-image video) or
    "detail" (detail video). Numeric ids are auto-converted to the encrypted ids
    the API needs. Re-relating the same pair is idempotent."""
    token = ctx.auth.get_valid_token(account_key or None)
    r = ctx.videos.relate_to_product(video_id, product_id, token, target=target)
    return VideoRelateResult(
        success=r.get("success"),
        video_id=r.get("video_id"),
        product_id=r.get("product_id"),
        msg_code=r.get("msg_code"),
        msg_info=r.get("msg_info"),
    )


@mcp.tool(annotations=_anno("List products related to video", read_only=True, open_world=True))
@tool_errors(VideoListRelatedResult)
def video_list_related(video_id: str = "", type: str = "videoId", account_key: str = "") -> VideoListRelatedResult:
    """List product ids related to a video. `type`: "videoId" (main) or
    "detailVideoId" (detail)."""
    token = ctx.auth.get_valid_token(account_key or None)
    r = ctx.videos.list_related_products(token, video_id=video_id or None, type=type)
    return VideoListRelatedResult(
        product_ids=r.get("product_ids", []), msg_code=r.get("msg_code"), msg_info=r.get("msg_info")
    )


@mcp.tool(annotations=_anno("Query seller videos", read_only=True, open_world=True))
@tool_errors(VideoQueryResult)
def video_query(params: dict[str, Any] | None = None, account_key: str = "") -> VideoQueryResult:
    """Query the seller's videos (to find video ids). Requires paging params, e.g.
    params={"current_page": "1", "page_size": "20"}; each item has a `video_id`."""
    token = ctx.auth.get_valid_token(account_key or None)
    return VideoQueryResult(raw=ctx.videos.query_videos(token, params or None)["raw"])


# ── AI structured product detail (AI+结构化商详) ──────────────────────────────
@mcp.tool(annotations=_anno("Generate product detail (AI)", idempotent=False, open_world=True))
@tool_errors(ProductDetailResult)
def generate_product_detail(
    product_name: str,
    version: str = "premium",
    features: list[str] | None = None,
    keywords: list[str] | None = None,
    target_market: str = "global B2B buyers",
    tone: str = "professional",
    language: str = "English",
    detail_image_count: int = 0,
    brands: list[str] | None = None,
    extra_instructions: str = "",
    save_to: str = "",
) -> ProductDetailResult:
    """Generate a structured product detail with Claude (title, highlights, ordered
    image+text modules, attributes, keywords, FAQs).

    The title is normalized to Alibaba title case; pass `brands` (e.g. ["C01B"]) to
    preserve casing. `version` ∈ premium (全能精装版) | lite (经济简装版) | general
    (通用排版). If `save_to` is given, the content is written there as JSON (confined
    to the server's allowed directories). Token usage is recorded (see `usage_stats`).
    """
    safe_save = _safe_write(save_to) if save_to else ""
    result = ctx.detail.generate(
        product_name=product_name,
        version=version,
        features=features,
        keywords=keywords,
        target_market=target_market,
        tone=tone,
        language=language,
        detail_image_count=detail_image_count,
        brands=brands,
        extra_instructions=extra_instructions,
    )
    saved_to = None
    if safe_save and result.get("content"):
        p = Path(safe_save)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result["content"], ensure_ascii=False, indent=2), encoding="utf-8")
        saved_to = str(p)
    usage = result.get("usage")
    return ProductDetailResult(
        model=result.get("model"),
        content=result.get("content"),
        raw_text=result.get("raw_text"),
        usage=Usage(**usage) if usage else None,
        saved_to=saved_to,
    )


# ── AI social content ────────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("Generate social content (AI)", idempotent=False, open_world=True))
@tool_errors(SocialContentResult)
def generate_social_content(
    product_name: str,
    platforms: list[str],
    features: list[str] | None = None,
    keywords: list[str] | None = None,
    tone: str = "professional",
    language: str = "English",
    variants: int = 1,
    extra_instructions: str = "",
) -> SocialContentResult:
    """Generate platform-tailored social posts for a product with Claude.

    Token usage is recorded automatically (see `usage_stats`). `platforms` e.g.
    ["linkedin", "instagram", "x", "facebook", "tiktok", "pinterest"].
    """
    result = ctx.social.generate(
        product_name=product_name,
        platforms=platforms,
        features=features,
        keywords=keywords,
        tone=tone,
        language=language,
        variants=variants,
        extra_instructions=extra_instructions,
    )
    usage = result.get("usage")
    return SocialContentResult(
        model=result.get("model"),
        posts=result.get("posts"),
        raw_text=result.get("raw_text"),
        usage=Usage(**usage) if usage else None,
    )


# ── token usage stats ────────────────────────────────────────────────────────
@mcp.tool(annotations=_anno("AI token usage stats", read_only=True))
@tool_errors(UsageStatsResult)
def usage_stats(
    model: str = "",
    label: str = "",
    since_iso: str = "",
    group_by: str = "day",
) -> UsageStatsResult:
    """Report AI token usage and estimated USD cost (from the local usage log).

    Filter by `model`/`label`/`since_iso` (ISO-8601). `group_by` ∈ day|model|label.
    """
    s = ctx.tracker.stats(
        model=model or None, label=label or None, since_iso=since_iso or None, group_by=group_by
    )
    return UsageStatsResult(
        call_count=s["call_count"],
        group_by=s["group_by"],
        filters=s["filters"],
        totals=s["totals"],
        groups=s["groups"],
    )


# ── usage log as an MCP resource ──────────────────────────────────────────────
@mcp.resource("usage://summary")
def usage_summary_resource() -> str:
    """A quick all-time usage summary, grouped by model."""
    return json.dumps(ctx.tracker.stats(group_by="model"), ensure_ascii=False, indent=2)


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency): set vars not already in the env."""
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        break


def main() -> None:
    _load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
