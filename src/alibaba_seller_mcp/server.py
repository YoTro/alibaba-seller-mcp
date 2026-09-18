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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from .ai import DetailSpecGenerator, ProductDetailGenerator
from .alibaba.auth import SellerAuth
from .alibaba.categories import CategoryService
from .alibaba.client import AlibabaClient
from .alibaba.errors import AlibabaError
from .alibaba.groups import GroupService
from .alibaba.manifest import ProductManifest
from .alibaba.products import ProductService
from .alibaba.videos import VideoService
from .config import Config, load_config, load_dotenv
from .files.readers import FileIngestError, read_image, read_video
from .listing import PublishFromBrief, prepare_brief_media
from .models import (
    AuthStatusResult,
    AuthUrlResult,
    BriefPublishResult,
    CategoryAttributesResult,
    CompleteAuthResult,
    DetailImagesResult,
    GroupChild,
    GroupResult,
    MediaInfoResult,
    PriceFileResult,
    ProductDetailResult,
    PublishResult,
    RawCallResult,
    RenderDraftField,
    RenderDraftResult,
    RenderedImage,
    Result,
    SchemaResult,
    UploadImageResult,
    Usage,
    UsageStatsResult,
    VideoListRelatedResult,
    VideoQueryResult,
    VideoRelateResult,
)
from .pathsafe import PathNotAllowedError, ensure_allowed
from .rendering import DetailSpec, render_spec
from .storage import TokenStore, UsageStore
from .usage.tracker import UsageTracker

# Built-in workflow guidance, delivered to every MCP client as server instructions.
INSTRUCTIONS = """\
Alibaba.com Global B2B seller tools. Recommended listing workflow:

1. Authorize: alibaba_auth_status → (if needed) alibaba_get_authorize_url, then
   alibaba_complete_authorization_from_url with the redirected URL.
2. Prepare a product folder with brief.json (facts only — never invent specs,
   patents or certifications) plus the seller's photos: a white-background hero
   render, a side render, and lifestyle scenes. Put them under brief.photos.
3. Media is code-rendered, not AI-drawn: listing_media_prepare builds 4-6 main
   images (square renders, 3:4 scene crops) and detail pages from detail_spec.json.
   If the spec is missing the AI writes it once (~1-2k tokens); afterwards edit the
   JSON and call detail_images_render — no tokens. Prefer this over image models:
   text-heavy pages (specs, steps, OEM/ODM terms) must be pixel-exact.
4. Publish with product_publish_from_brief(draft=True). It auto-runs step 3 when
   images.main / images.detail are empty and photos are present, uploads everything
   to the photo bank, and returns warnings — read them; an empty images warning means
   the upload failed.
5. Verify with product_render_draft (or alibaba_raw_call schema.render.draft) that
   scImages has 4-6 fileIds and detailImage lists every detail page. Each publish
   creates a NEW draft — tell the seller to delete superseded drafts in the console.
"""

mcp = MCPServer("alibaba-seller-mcp", instructions=INSTRUCTIONS)

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
        self._detail: ProductDetailGenerator | None = None
        self._detail_spec: DetailSpecGenerator | None = None
        self._publishing: PublishFromBrief | None = None

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
    def detail(self) -> ProductDetailGenerator:
        if self._detail is None:
            self._detail = ProductDetailGenerator(self.config, self.tracker)
        return self._detail

    @property
    def detail_spec(self) -> DetailSpecGenerator:
        if self._detail_spec is None:
            self._detail_spec = DetailSpecGenerator(self.config, self.tracker)
        return self._detail_spec

    @property
    def publishing(self) -> PublishFromBrief:
        """The brief → listing application service, wired to this context."""
        if self._publishing is None:
            self._publishing = PublishFromBrief(
                self.products, detail=self.detail, detail_spec=self.detail_spec,
                allowed_roots=self.config.allowed_paths,
            )
        return self._publishing


ctx = Context()


def tool_errors(model: type[Result]) -> Callable:
    """Decorator: turn an anticipated failure into a proper MCP **tool execution
    error** instead of a raw traceback. `model` is the tool's return type.

    The spec (MCP 2025-11-25, Tools § Error Handling) wants API, validation and
    business failures reported as `isError: true` on the `CallToolResult`, so a
    client can tell "the tool ran and failed" from "the tool succeeded". Returning
    only a body with `ok=False` leaves `isError` false and the failure invisible at
    the protocol level. Returning the result explicitly lets us set the flag and
    still hand back the tool's typed model (`ok=False`, `error`, `error_type`) as
    structured content — the SDK passes a `CallToolResult` through untouched and
    skips output-schema validation when `is_error` is set.
    """

    def deco(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = fn(*args, **kwargs)
            except (
                AlibabaError,
                FileIngestError,
                PathNotAllowedError,
                ValueError,
                RuntimeError,
                OSError,          # a missing/unreadable photo is the seller's typo, not a crash
            ) as exc:
                return _as_tool_error(model(), type(exc).__name__, str(exc))
            rejected = _business_failure(result)
            return result if rejected is None else _as_tool_error(result, "BusinessFailure", rejected)

        return wrapper

    return deco


def _as_tool_error(payload: Result, error_type: str, message: str) -> CallToolResult:
    """The tool's own result model, marked failed, inside an `isError` result."""
    failed = payload.model_copy(update={"ok": False, "error": message, "error_type": error_type})
    return CallToolResult(
        content=[TextContent(type="text", text=f"{error_type}: {message}")],
        structured_content=failed.model_dump(mode="json", by_alias=True),
        is_error=True,
    )


def _business_failure(result: Any) -> str | None:
    """Message for a call the gateway accepted but the business layer rejected.

    A clean gateway ``code: "0"`` with a false verdict (``biz_success`` on a
    publish, ``success`` on a video relation) is the platform saying "your
    request was well-formed and I still refused it". No exception is raised for
    it, so without this check the tool reports success for work that never
    happened.

    Which field holds the verdict is declared by the result model
    (``Result.OUTCOME_FIELD``), not guessed from its name — a tool that reports
    state has look-alike fields whose ``False`` is an answer, not a failure.
    """
    field_name = getattr(type(result), "OUTCOME_FIELD", None)
    if not field_name:
        return None
    verdict = getattr(result, field_name, None)
    if isinstance(verdict, str):
        verdict = {"true": True, "false": False}.get(verdict.strip().lower(), verdict)
    if verdict is not False:        # True, None (not reported) or an unexpected shape
        return None
    return result.failure_message()


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
    Both forms support every key, `photos` included.
    The brief needs: `brief` (short description), `category_id`, `price`
    ({unit, moq, tiers}), `facts` (place_of_origin, material, …) and either
    `images` (main 4–6 + detail — local paths, photo-bank refs or URLs) or `photos`
    ({hero, side, scenes:[{path, caption}]}). With `photos` and empty `images`, the
    main images are cropped and the detail pages are code-rendered from the brief's
    `detail_spec` (or a `detail_spec.json` in its folder, written once by AI if
    missing) before upload.
    Relative paths and the rendered-image output folder resolve against the
    brief's **base directory**: the file's own folder for `brief_path`, or the
    `base_dir` key for an inline brief. An inline brief with `photos` must set
    it — rendering writes an `images/` tree, and the server will not guess where
    that belongs. Claude writes the title, highlights, detail modules, FAQs, custom
    parameters, and picks the descriptive category attributes from the schema's
    allowed options. Returns the draft `product_id`, `missing_required`, `ai_filled`
    (what was auto-generated), and `warnings` (read them: an image upload failure
    surfaces here). Defaults to `draft=True`; every call creates a new draft.
    """
    service = ctx.publishing
    base_dir = None
    if brief_path:
        brief, base_dir = service.load(brief_path)   # relative paths resolve against the brief's folder
    if not brief:
        raise ValueError("Provide `brief` (inline) or `brief_path`.")
    out = service.run(
        brief, token=ctx.auth.get_valid_token(account_key or None), base_dir=base_dir, draft=draft
    )
    return BriefPublishResult(
        product_id=out.product_id,
        biz_success=out.biz_success,
        filled_fields=out.filled_fields,
        missing_required=out.missing_required,
        warnings=out.warnings,
        ai_filled=out.ai_filled,
        needs_more_main_images=out.needs_more_main_images,
        response=out.response,
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


@mcp.tool(annotations=_anno("Update product", destructive=True, idempotent=True, open_world=True))
@tool_errors(PublishResult)
def product_update(product_id: str, manifest_path: str, account_key: str = "") -> PublishResult:
    """Overwrite an existing product (schema.update) with a **complete** manifest.

    Despite the API name this is NOT a partial update: the platform re-validates
    the whole document and rejects a manifest that omits a required field (you
    get `isError` with `isv.missing-parameter:...` naming each one). So send the
    product's full manifest with your change applied, not just the changed
    fields — anything you leave out is not preserved, it is refused. Read
    `missing_required` in the result before treating an update as complete.
    Re-applying the same complete manifest yields the same state (idempotent).
    Paths are confined to the server's allowed directories.
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


# ── code-rendered listing media ───────────────────────────────────────────────
def _rendered(paths: list[str]) -> list[RenderedImage]:
    out = []
    for p in paths:
        a = read_image(p, load_bytes=False)
        out.append(RenderedImage(path=p, width=a.width or 0, height=a.height or 0, size_bytes=a.size_bytes))
    return out


@mcp.tool(annotations=_anno("Prepare listing media (code-rendered)", idempotent=True, open_world=True))
@tool_errors(DetailImagesResult)
def listing_media_prepare(
    brief_path: str, regenerate_spec: bool = False, extra_instructions: str = "", language: str = "English"
) -> DetailImagesResult:
    """Build main images and detail pages for a brief from its `photos`, without an
    image model.

    Main images: hero/side renders are cropped to the product and padded to
    1000×1000; scenes are cropped to 3:4 / 4:3. Detail pages: rendered with Pillow
    from `detail_spec.json` next to the brief. If that spec does not exist (or
    `regenerate_spec=True`), Claude writes it once from the brief's facts — a compact
    JSON of page templates (hero, features, steps, levels, callouts, chips, scenes,
    spec_table, oem_odm, trust) — and it is saved for hand-editing. Outputs go to
    `images/main` and `images/detail` beside the brief. Token usage is recorded
    (label `detail_image_spec`).
    """
    brief_file = Path(_safe_read(brief_path))
    brief = json.loads(brief_file.read_text(encoding="utf-8"))
    if not brief.get("photos"):
        raise ValueError("brief.photos is required ({hero, side?, scenes?})")
    prep = prepare_brief_media(
        brief, brief_file.parent, ctx.detail_spec, regenerate_spec=regenerate_spec,
        extra_instructions=extra_instructions, language=language,
        allowed_roots=ctx.config.allowed_paths,
    )
    return DetailImagesResult(
        spec_path=prep.spec_path, spec_source=prep.spec_source, usage=prep.usage, notes=prep.notes,
        main_images=_rendered(prep.main_paths), detail_images=_rendered(prep.detail_paths),
    )


@mcp.tool(annotations=_anno("Render detail images from spec", idempotent=True))
@tool_errors(DetailImagesResult)
def detail_images_render(spec_path: str, out_dir: str = "") -> DetailImagesResult:
    """Re-render detail pages from a `detail_spec.json` — deterministic, no AI, no
    tokens. Edit the spec's text and call this to refresh the images. Relative asset
    paths resolve against the spec's folder; output defaults to `images/detail`
    beside it. Pages come out as `NN_<type>.jpg`, width 1200, each < 3 MB.
    """
    spec_file = Path(_safe_read(spec_path))
    spec = DetailSpec.model_validate(json.loads(spec_file.read_text(encoding="utf-8")))
    out = Path(_safe_write(out_dir)) if out_dir else spec_file.parent / "images" / "detail"
    paths = [str(p) for p in render_spec(spec, out, base_dir=spec_file.parent,
                                         allowed_roots=ctx.config.allowed_paths)]
    return DetailImagesResult(spec_path=str(spec_file), spec_source="existing", detail_images=_rendered(paths))


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
@mcp.tool(annotations=_anno("Relate video to product", destructive=True, idempotent=True, open_world=True))
@tool_errors(VideoRelateResult)
def video_relate_product(
    video_id: str, product_id: str, target: str = "main", account_key: str = ""
) -> VideoRelateResult:
    """Set a product's video. `target`: "main" (main-image video) or "detail"
    (detail video). Each is a single slot, so relating a video to a product that
    already has one for that target replaces it. Numeric ids are auto-converted
    to the encrypted ids the API needs. Re-relating the same pair is idempotent."""
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


def main() -> None:
    load_dotenv()
    mcp.run()


if __name__ == "__main__":
    main()
