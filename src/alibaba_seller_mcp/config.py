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

    @property
    def token_store_path(self) -> Path:
        return self.state_dir / "tokens.json"

    @property
    def usage_log_path(self) -> Path:
        return self.state_dir / "usage.jsonl"

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


def _allowed_paths() -> tuple[Path, ...]:
    raw = os.environ.get("ALIBABA_MCP_ALLOWED_DIRS", "")
    if not raw.strip():
        return (Path.cwd().resolve(),)
    roots = [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]
    return tuple(roots) or (Path.cwd().resolve(),)


def load_config() -> Config:
    return Config(
        allowed_paths=_allowed_paths(),
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
