"""Runtime configuration, loaded from environment variables.

Values come from the process environment. For local development, export them or
use a `.env` (see `.env.example`); this module does not itself parse `.env`, so
either export the vars or load them with your process manager / a dotenv shim.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _state_dir() -> Path:
    override = os.environ.get("ALIBABA_MCP_STATE_DIR")
    base = Path(override) if override else Path.home() / ".alibaba_seller_mcp"
    base.mkdir(parents=True, exist_ok=True)
    return base


@dataclass(frozen=True)
class Config:
    # ── Alibaba open platform ────────────────────────────────────────────
    app_key: str = ""
    app_secret: str = ""
    redirect_uri: str = ""
    # New unified platform (open.alibaba.com console). Gateway and authorize host
    # are both open-api.alibaba.com per the seller-authorization docs.
    gateway: str = "https://open-api.alibaba.com/rest"
    # Legacy TOP gateway (used by some ICBU APIs, e.g. photobank.upload). Different
    # signing (no api-name prefix) and token param name (`session`).
    sync_gateway: str = "https://open-api.alibaba.com/sync"
    authorize_url: str = "https://open-api.alibaba.com/oauth/authorize"
    # force_auth=true forces the login/consent screen each time.
    auth_force_auth: str = "true"
    sign_method: str = "sha256"

    # Schema-based publish flow (Global B2B).
    method_schema_get: str = "alibaba.icbu.product.schema.get"
    method_schema_add: str = "alibaba.icbu.product.schema.add"
    method_schema_add_draft: str = "alibaba.icbu.product.schema.add.draft"
    method_schema_render: str = "alibaba.icbu.product.schema.render"
    method_schema_render_draft: str = "alibaba.icbu.product.schema.render.draft"
    method_schema_update: str = "alibaba.icbu.product.schema.update"
    method_product_update: str = "alibaba.icbu.product.update"
    method_photo_upload: str = "alibaba.icbu.photobank.upload"
    # Category system attributes (for required icbuCatProp fields).
    method_category_attribute_get: str = "alibaba.icbu.category.attribute.get"
    method_category_level_attr_get: str = "alibaba.icbu.category.level.attr.get"
    # Product groups.
    method_product_group_get: str = "alibaba.icbu.product.group.get"
    method_product_group_add: str = "alibaba.icbu.product.group.add"
    # Relation APIs take ENCRYPTED product/video ids; encrypt numeric ids with these.
    method_product_id_encrypt: str = "alibaba.icbu.product.id.encrypt"
    method_product_id_decrypt: str = "alibaba.icbu.product.id.decrypt"
    # Video ↔ product relation.
    method_video_relate_main: str = "alibaba.icbu.video.relation.product.main"
    method_video_relate_detail: str = "alibaba.icbu.video.relation.product.detail"
    method_video_relation_list: str = "alibaba.icbu.video.relation.product.list"
    method_video_query: str = "alibaba.icbu.video.query"
    # productDescType option code. 2=普通编辑; the console's 智能编辑 (AI structured
    # detail) mode uses 4 (confirmed from the backend publish payload), which is why
    # it is the default here for AI-generated structured details.
    product_desc_type_default: str = "4"

    # ── Anthropic / Claude ───────────────────────────────────────────────
    anthropic_api_key: str = ""
    social_model: str = "claude-opus-5"

    # ── Local state ──────────────────────────────────────────────────────
    state_dir: Path = field(default_factory=_state_dir)
    # Filesystem roots the server is allowed to read from / write to. Tools that
    # take a local path (media, price files, manifests, save_to) are confined to
    # these subtrees. Defaults to the current working directory.
    allowed_paths: tuple[Path, ...] = field(default_factory=lambda: (Path.cwd().resolve(),))
    # Soft ceiling on Claude tokens for ONE server session (0 = no limit). Guards
    # against a runaway loop of AI calls, which is the only thing here that spends
    # real money per attempt; the platform's own rate limits cover the rest.
    ai_token_budget: int = 0

    # ── alibaba.com buyer site (keyword ads / monopoly rate) ─────────────
    # A browser's Cookie header for www.alibaba.com (no login needed). Optional,
    # but after the anti-bot slider has been passed once in a browser, its cookies
    # let this client through too. Defaults to <state_dir>/market_cookie.txt.
    market_cookie_file: Path | None = None
    # Seconds between live search requests, and how long a keyword's snapshot is
    # reused before it is fetched again (buyout slots change once a year).
    market_min_interval: float = 10.0
    market_cache_ttl_hours: float = 24.0
    market_country: str = "US"
    # When the search page comes back as the anti-bot slider, solve it (see market/slider.py)
    # cheapest route first and retry with the cookies that clears it. Off by default; the
    # browser tier needs `pip install camoufox playwright` + `python -m camoufox fetch`.
    market_solve_slider: bool = False
    market_slider_headless: bool = True          # run the browser tier headless
    market_slider_os: str = "macos"              # Camoufox OS fingerprint
    # Template replay (see market/slider.py): re-sign blocks captured from a real drag and
    # pass the slider over plain HTTP. On by default — with no template yet it is skipped,
    # and the browser tier saves one every time it passes. Each template is used at most
    # `market_slider_template_uses` times (the reverse-engineering archive validated ≤5).
    market_slider_algo: bool = True
    market_slider_template_uses: int = 5
    # Headless Node signer: passes, but still needs a browser and is heavier than it. Dev-only.
    market_slider_headless_sign: bool = False
    market_headless_signer_dir: Path | None = None

    @property
    def token_store_path(self) -> Path:
        return self.state_dir / "tokens.json"

    @property
    def usage_log_path(self) -> Path:
        return self.state_dir / "usage.jsonl"

    @property
    def market_snapshot_path(self) -> Path:
        return self.state_dir / "market_snapshots.jsonl"

    @property
    def market_slider_template_dir(self) -> Path:
        return self.state_dir / "slider_templates"

    @property
    def market_cookie_path(self) -> Path:
        return self.market_cookie_file or self.state_dir / "market_cookie.txt"

    def require_alibaba(self) -> None:
        """Raise if the Alibaba credentials needed for signed calls are missing."""
        missing = [
            name
            for name, val in (
                ("ALIBABA_APP_KEY", self.app_key),
                ("ALIBABA_APP_SECRET", self.app_secret),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "See .env.example."
            )


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a whole number of tokens, got {raw!r}") from exc


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _allowed_paths() -> tuple[Path, ...]:
    raw = os.environ.get("ALIBABA_MCP_ALLOWED_DIRS", "")
    if not raw.strip():
        return (Path.cwd().resolve(),)
    roots = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    return tuple(roots) or (Path.cwd().resolve(),)


def load_config() -> Config:
    return Config(
        allowed_paths=_allowed_paths(),
        ai_token_budget=_int_env("ALIBABA_MCP_AI_TOKEN_BUDGET", 0),
        market_cookie_file=(
            Path(os.environ["ALIBABA_MARKET_COOKIE_FILE"]).expanduser()
            if os.environ.get("ALIBABA_MARKET_COOKIE_FILE", "").strip() else None
        ),
        market_min_interval=_float_env("ALIBABA_MARKET_MIN_INTERVAL", 10.0),
        market_cache_ttl_hours=_float_env("ALIBABA_MARKET_CACHE_TTL_HOURS", 24.0),
        market_country=os.environ.get("ALIBABA_MARKET_COUNTRY", "US").strip().upper() or "US",
        market_solve_slider=_bool_env("ALIBABA_MARKET_SOLVE_SLIDER", False),
        market_slider_headless=_bool_env("ALIBABA_MARKET_SLIDER_HEADLESS", True),
        market_slider_os=os.environ.get("ALIBABA_MARKET_SLIDER_OS", "macos").strip() or "macos",
        market_slider_algo=_bool_env("ALIBABA_MARKET_SLIDER_ALGO", True),
        market_slider_template_uses=_int_env("ALIBABA_MARKET_SLIDER_TEMPLATE_USES", 5),
        market_slider_headless_sign=_bool_env("ALIBABA_MARKET_SLIDER_HEADLESS_SIGN", False),
        market_headless_signer_dir=(
            Path(os.environ["ALIBABA_MARKET_HEADLESS_SIGNER_DIR"]).expanduser()
            if os.environ.get("ALIBABA_MARKET_HEADLESS_SIGNER_DIR", "").strip() else None
        ),
        app_key=os.environ.get("ALIBABA_APP_KEY", ""),
        app_secret=os.environ.get("ALIBABA_APP_SECRET", ""),
        redirect_uri=os.environ.get("ALIBABA_REDIRECT_URI", ""),
        gateway=os.environ.get("ALIBABA_GATEWAY", "https://open-api.alibaba.com/rest").rstrip("/"),
        sync_gateway=os.environ.get("ALIBABA_SYNC_GATEWAY", "https://open-api.alibaba.com/sync").rstrip("/"),
        authorize_url=os.environ.get(
            "ALIBABA_AUTHORIZE_URL", "https://open-api.alibaba.com/oauth/authorize"
        ),
        auth_force_auth=os.environ.get("ALIBABA_FORCE_AUTH", "true"),
        sign_method=os.environ.get("ALIBABA_SIGN_METHOD", "sha256"),
        method_schema_get=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_GET", "alibaba.icbu.product.schema.get"
        ),  # (product create is the schema.add flow; no single product.create method)
        method_schema_add=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_ADD", "alibaba.icbu.product.schema.add"
        ),
        method_schema_add_draft=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_ADD_DRAFT", "alibaba.icbu.product.schema.add.draft"
        ),
        method_schema_render=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_RENDER", "alibaba.icbu.product.schema.render"
        ),
        method_schema_render_draft=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_RENDER_DRAFT", "alibaba.icbu.product.schema.render.draft"
        ),
        method_schema_update=os.environ.get(
            "ALIBABA_METHOD_SCHEMA_UPDATE", "alibaba.icbu.product.schema.update"
        ),
        method_product_update=os.environ.get(
            "ALIBABA_METHOD_PRODUCT_UPDATE", "alibaba.icbu.product.update"
        ),
        method_photo_upload=os.environ.get(
            "ALIBABA_METHOD_PHOTO_UPLOAD", "alibaba.icbu.photobank.upload"
        ),
        method_category_attribute_get=os.environ.get(
            "ALIBABA_METHOD_CATEGORY_ATTRIBUTE_GET", "alibaba.icbu.category.attribute.get"
        ),
        method_category_level_attr_get=os.environ.get(
            "ALIBABA_METHOD_CATEGORY_LEVEL_ATTR_GET", "alibaba.icbu.category.level.attr.get"
        ),
        method_product_group_get=os.environ.get(
            "ALIBABA_METHOD_PRODUCT_GROUP_GET", "alibaba.icbu.product.group.get"
        ),
        method_product_group_add=os.environ.get(
            "ALIBABA_METHOD_PRODUCT_GROUP_ADD", "alibaba.icbu.product.group.add"
        ),
        method_product_id_encrypt=os.environ.get(
            "ALIBABA_METHOD_PRODUCT_ID_ENCRYPT", "alibaba.icbu.product.id.encrypt"
        ),
        method_product_id_decrypt=os.environ.get(
            "ALIBABA_METHOD_PRODUCT_ID_DECRYPT", "alibaba.icbu.product.id.decrypt"
        ),
        method_video_relate_main=os.environ.get(
            "ALIBABA_METHOD_VIDEO_RELATE_MAIN", "alibaba.icbu.video.relation.product.main"
        ),
        method_video_relate_detail=os.environ.get(
            "ALIBABA_METHOD_VIDEO_RELATE_DETAIL", "alibaba.icbu.video.relation.product.detail"
        ),
        method_video_relation_list=os.environ.get(
            "ALIBABA_METHOD_VIDEO_RELATION_LIST", "alibaba.icbu.video.relation.product.list"
        ),
        method_video_query=os.environ.get(
            "ALIBABA_METHOD_VIDEO_QUERY", "alibaba.icbu.video.query"
        ),
        product_desc_type_default=os.environ.get("ALIBABA_PRODUCT_DESC_TYPE", "4"),
        anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        social_model=os.environ.get("SOCIAL_MODEL", "claude-opus-5"),
    )


def load_dotenv() -> None:
    """Minimal ``.env`` loader (no dependency): set vars not already in the env.

    Reads the first of ``./.env`` or the repo-root ``.env``. Public because every
    entry point needs it before :func:`load_config` — the MCP server and the CLI
    OAuth helper alike — and the helper should not have to import the server (and
    its whole tool registry) to get at it.
    """
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
