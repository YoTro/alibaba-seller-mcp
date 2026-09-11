"""Video ↔ product association (ICBU video relation APIs).

Documented shapes:
  relate main video   alibaba.icbu.video.relation.product.main
    params: video_id, product_id  ->  {code, model:"true", msg_code, msg_info}
  relate detail video alibaba.icbu.video.relation.product.detail  (same shape)
  list related        alibaba.icbu.video.relation.product.list
    params: video_id (opt), type ("videoId"=main / "detailVideoId"=detail)
    -> {result: {model:[{product_id}], msg_code, msg_info}}
  query videos        alibaba.icbu.video.query  (params vary — passed through)
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from .client import AlibabaClient
from .errors import AlibabaError

RELATION_TYPES = {"videoId", "detailVideoId"}


def _truthy(model: Any) -> bool:
    if isinstance(model, bool):
        return model
    return str(model).strip().lower() == "true"


def _is_numeric_id(value: str) -> bool:
    return value.isdigit()


class VideoService:
    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client

    # ── id resolution (relation APIs need ENCRYPTED ids) ──────────────────
    def encrypt_product_id(self, numeric_id: str, access_token: str, *, language: str = "ENGLISH") -> str:
        """Numeric product id -> encrypted product id (secret_id)."""
        body = self.client.call(
            self.config.method_product_id_encrypt,
            {"product_id": str(numeric_id), "language": language},
            access_token=access_token,
        )
        enc = body.get("secret_id") or body.get("secretId")
        if not enc:
            raise AlibabaError(f"product.id.encrypt returned no secret_id: {body}")
        return enc

    def resolve_video_id(self, video_id: str, access_token: str, *, max_pages: int = 10) -> str:
        """Numeric video id -> encrypted video_id (looked up via video.query).
        An already-encrypted id is returned unchanged."""
        if not _is_numeric_id(str(video_id)):
            return video_id
        for page in range(1, max_pages + 1):
            body = self.query_videos(access_token, {"current_page": str(page), "page_size": "50"})["raw"]
            model = (body.get("result") or {}).get("model") or {}
            items = model.get("list") or []
            for v in items:
                if str(v.get("id")) == str(video_id):
                    return v.get("video_id")
            if len(items) < 50:
                break
        raise AlibabaError(f"Video with numeric id {video_id} not found via video.query")

    def relate_to_product(
        self,
        video_id: str,
        product_id: str,
        access_token: str,
        *,
        target: str = "main",
        auto_encrypt: bool = True,
    ) -> dict[str, Any]:
        """Associate a video with a product's main image (``target="main"``) or
        detail (``target="detail"``). With ``auto_encrypt`` (default), numeric
        product/video ids are converted to the encrypted ids the API requires."""
        if target not in ("main", "detail"):
            raise ValueError("target must be 'main' or 'detail'")
        if auto_encrypt:
            if _is_numeric_id(str(product_id)):
                product_id = self.encrypt_product_id(product_id, access_token)
            video_id = self.resolve_video_id(video_id, access_token)
        method = (
            self.config.method_video_relate_main
            if target == "main"
            else self.config.method_video_relate_detail
        )
        body = self.client.call(
            method, {"video_id": video_id, "product_id": product_id}, access_token=access_token
        )
        return {
            "success": _truthy(body.get("model")),
            "video_id": video_id,
            "product_id": product_id,
            "msg_code": body.get("msg_code"),
            "msg_info": body.get("msg_info"),
            "raw": body,
        }

    def list_related_products(
        self, access_token: str, *, video_id: str | None = None, type: str = "videoId", auto_encrypt: bool = True
    ) -> dict[str, Any]:
        """List product ids related to a video. ``type``: videoId (main) or
        detailVideoId (detail)."""
        if type not in RELATION_TYPES:
            raise ValueError(f"type must be one of {sorted(RELATION_TYPES)}")
        params: dict[str, Any] = {"type": type}
        if video_id:
            params["video_id"] = self.resolve_video_id(video_id, access_token) if auto_encrypt else video_id
        body = self.client.call(
            self.config.method_video_relation_list, params, access_token=access_token
        )
        result = body.get("result") or {}
        model = result.get("model") or []
        product_ids = [m.get("product_id") for m in model if isinstance(m, dict) and m.get("product_id")]
        return {
            "product_ids": product_ids,
            "msg_code": result.get("msg_code"),
            "msg_info": result.get("msg_info"),
            "raw": body,
        }

    def query_videos(self, access_token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Query the seller's videos. Params are category/console-specific — pass
        what the API detail page lists (e.g. paging). Returns the raw response."""
        body = self.client.call(
            self.config.method_video_query, params or {}, access_token=access_token
        )
        return {"raw": body}
