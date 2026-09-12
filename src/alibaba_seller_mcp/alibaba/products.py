"""The publish API adapter: schema in, filled schema out.

Flow (confirmed against the open.alibaba.com docs):

  1. ``schema.get``  -> a category-specific ``<itemSchema>`` XML form.
  2. Fill values (title, images, prices, attributes) into that form.
  3. ``schema.add`` / ``schema.add.draft`` / ``schema.update`` -> submit
     ``{publish_type, cat_id, language, version, xml}`` where ``xml`` is the
     filled itemSchema. The response carries ``product_id`` and ``biz_success``.

This module is steps 1 and 3 only — the HTTP calls and their request/response
shapes. Step 2 lives in :mod:`.fields` (assembly) and :mod:`.values` (the pure
value rules); images go through :mod:`.photobank`; the input document is a
:class:`~.manifest.ProductManifest`. Business field names/values are
category-specific — inspect them with ``get_publish_schema`` first.
"""

from __future__ import annotations

import json
from typing import Any

from ..config import Config
from .client import AlibabaClient
from .errors import AlibabaError
from .fields import FieldAssembler
from .manifest import ProductManifest
from .photobank import PhotoBank
from .schema import build_value_xml, describe_schema, parse_item_schema
from .values import (
    DEFAULT_LEAD_TIME, LADDER_PERIOD_MAX, MAX_ATTR_NAME_LEN, MAX_ATTR_VALUE_LEN,
    MAX_COMPANY_DESC_LEN, MAX_FAQ_ANSWER_LEN, MAX_FAQ_COUNT, MAX_FAQ_QUESTION_LEN,
    MAX_HIGHLIGHTS_LEN, _id_str, build_ladder_period, clip_body, clip_text,
    missing_required, missing_required_cat_props, missing_required_sale_props,
    option_code,
)

DEFAULT_VERSION = "trade.1.1"

# Re-exported: callers keep importing product publishing from one place, and the
# split below stays an internal detail.
__all__ = [
    "DEFAULT_LEAD_TIME", "DEFAULT_VERSION", "LADDER_PERIOD_MAX", "MAX_ATTR_NAME_LEN",
    "MAX_ATTR_VALUE_LEN", "MAX_COMPANY_DESC_LEN", "MAX_FAQ_ANSWER_LEN", "MAX_FAQ_COUNT",
    "MAX_FAQ_QUESTION_LEN", "MAX_HIGHLIGHTS_LEN", "FieldAssembler", "PhotoBank",
    "ProductManifest", "ProductService", "build_ladder_period", "clip_body", "clip_text",
    "missing_required", "missing_required_cat_props", "missing_required_sale_props",
    "option_code",
]


class ProductService:
    """Talks to the publish API. Assembly and uploads are delegated."""

    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client
        self.photos = PhotoBank(config, client)
        self.assembler = FieldAssembler(config, self.photos)

    # ── schema ────────────────────────────────────────────────────────────
    def get_publish_schema(
        self,
        cat_id: str | int,
        access_token: str,
        *,
        language: str = "en_US",
        publish_type: str = "default",
        include_all: bool = False,
    ) -> dict[str, Any]:
        """Fetch and summarise the publish schema for a category."""
        xml = self._fetch_schema_xml(cat_id, access_token, language, publish_type)
        return describe_schema(xml, include_all=include_all)

    def get_schema_fields(
        self, cat_id: str | int, access_token: str, *, language: str = "en_US",
        publish_type: str = "default",
    ) -> list:
        """The category's publish schema, parsed into fields.

        The public way for callers that assemble their own values (the brief flow)
        to read a category's field types, options and required rules.
        """
        return parse_item_schema(self._fetch_schema_xml(cat_id, access_token, language, publish_type))

    def _fetch_schema_xml(
        self, cat_id: str | int, access_token: str, language: str, publish_type: str
    ) -> str:
        req = {
            "publish_type": publish_type,
            "cat_id": str(cat_id),
            "language": language,
            "version": DEFAULT_VERSION,
        }
        body = self.client.call(
            self.config.method_schema_get,
            {"param_product_top_publish_request": json.dumps(req)},
            access_token=access_token,
        )
        data = body.get("data")
        if not data:
            raise AlibabaError(f"schema.get returned no schema: {body}")
        return data

    def render_draft(
        self,
        product_id: str | int,
        cat_id: str | int,
        access_token: str,
        *,
        language: str = "en_US",
        parsed: bool = True,
    ) -> Any:
        """Read back a DRAFT product's filled schema (schema.render.draft).

        ``product_id`` is the plaintext (numeric) draft id. Returns parsed fields
        (``parsed=True``) or the raw itemSchema XML string."""
        req = {
            "product_id": str(product_id),
            "cat_id": str(cat_id),
            "language": language,
            "version": DEFAULT_VERSION,
        }
        body = self.client.call(
            self.config.method_schema_render_draft,
            {"param_product_top_publish_request": json.dumps(req)},
            access_token=access_token,
        )
        data = body.get("data")
        if not data:
            raise AlibabaError(f"schema.render.draft returned no data: {body}")
        return parse_item_schema(data) if parsed else data

    # ── media ─────────────────────────────────────────────────────────────
    def upload_image(
        self, local_path: str, access_token: str, *, group_id: str | int | None = None
    ) -> dict[str, Any]:
        """Upload one local image to the photo bank; return {file_id, url, cached}."""
        return self.photos.upload(local_path, access_token, group_id=group_id)

    # ── publish / update ──────────────────────────────────────────────────
    def publish_product(
        self, manifest: ProductManifest, access_token: str, *, draft: bool = False,
        schema_fields: list | None = None,
    ) -> dict[str, Any]:
        """Publish a product (schema.add), or create a draft (schema.add.draft).

        Drafts are not listed live and are ideal for verifying a manifest. Every
        call creates a NEW product — see :meth:`update_product` to change one.

        Pass ``schema_fields`` if you already fetched the category's schema (the
        brief flow does, to map option codes) — see :meth:`_submit`.
        """
        method = self.config.method_schema_add_draft if draft else self.config.method_schema_add
        return self._submit(manifest, access_token, method, schema_fields=schema_fields)

    def update_product(
        self, product_id: str, manifest: ProductManifest, access_token: str,
        *, schema_fields: list | None = None,
    ) -> dict[str, Any]:
        """Change an existing product in place (schema.update).

        NOT a partial update, despite the API name: the platform re-validates the
        whole document, so a manifest carrying only the fields you want to change
        is rejected for every required field it leaves out. Send a complete one.
        """
        return self._submit(
            manifest, access_token, self.config.method_schema_update,
            product_id=product_id, schema_fields=schema_fields,
        )

    def _submit(
        self, manifest: ProductManifest, access_token: str, method: str,
        *, product_id: str | None = None, schema_fields: list | None = None,
    ) -> dict[str, Any]:
        """Fetch the schema, assemble the manifest into it, and post the result.

        The single path add / add.draft / update all take, so the three cannot
        drift apart in what they fill or how they report the outcome.

        ``schema_fields`` lets a caller that has already fetched this category's
        schema hand it over instead of paying for a second ``schema.get`` (the
        payload is ~100 KB for a busy category). It must be the schema for this
        manifest's own category, language and publish_type — anything else would
        resolve option codes against the wrong form.
        """
        cat_id = manifest.data["category_id"]
        language = manifest.data.get("language", "en_US")
        publish_type = manifest.data.get("publish_type", "default")

        if schema_fields is None:
            schema_fields = parse_item_schema(
                self._fetch_schema_xml(cat_id, access_token, language, publish_type)
            )
        fields = self.assembler.assemble(
            manifest, access_token, manifest.data.get("photobank_group_id"), schema_fields
        )
        req = {
            "publish_type": publish_type,
            "xml": build_value_xml(schema_fields, fields),
            "cat_id": str(cat_id),
            "language": language,
            "version": manifest.data.get("version", DEFAULT_VERSION),
        }
        if product_id is not None:
            req["productId"] = str(product_id)
        body = self.client.call(
            method,
            {"param_product_top_publish_request": json.dumps(req, ensure_ascii=False)},
            access_token=access_token,
        )
        return {
            "filled_fields": sorted(fields),
            "product_id": _id_str(body.get("product_id") or product_id),
            "biz_success": body.get("biz_success"),
            "missing_required": missing_required(schema_fields, fields),
            "response": body,
        }
