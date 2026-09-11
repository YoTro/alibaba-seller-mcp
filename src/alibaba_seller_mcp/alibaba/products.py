"""Schema-based product publishing for the Global B2B.

Flow (confirmed against the open.alibaba.com docs):

  1. ``schema.get``  -> a category-specific ``<itemSchema>`` XML form.
  2. Fill values (title, images, prices, attributes) into that form.
  3. ``schema.add`` -> submit ``{publish_type, cat_id, language, version, xml}``
     where ``xml`` is the filled itemSchema. Response carries ``product_id`` and
     ``biz_success``.

Local assets are handled here per the user's requirement: images are uploaded to
the **photo bank** (``photobank.upload`` -> ``file_id`` / ``photobank_url``) and the
resulting URLs are placed into ``scImages`` / ``detailImage``; prices come from a
CSV/JSON file and fill ``ladderPrice`` (tiered) or ``sku`` (per-SKU). Uploaded
images are cached by content hash so re-publishing does not re-upload.

A product is described by a manifest (see ``ProductManifest``): a ``product.json``
plus asset files referenced by relative path. Business field names/values are
category-specific — inspect them with ``get_publish_schema`` first.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

from ..config import Config
from ..files.readers import read_image, read_prices, read_video
from ..pathsafe import ensure_allowed
from ..text_format import clean_title, enforce_length
from ..storage import _atomic_write
from .client import AlibabaClient
from .errors import AlibabaError
from .schema import build_value_xml, describe_schema, parse_item_schema

DEFAULT_VERSION = "trade.1.1"


class ProductManifest:
    """A product definition loaded from a ``product.json`` file (or a dict).

    Recognised keys: ``category_id`` (required), ``language`` (default en_US),
    ``publish_type`` (default "default"), ``version``, ``photobank_group_id``,
    ``fields`` (dict of schema field id -> value), ``main_images`` (list of paths),
    ``detail_images`` (list of paths), ``detail_gallery`` (option code, default
    "300"), ``video`` (path), ``price_file`` (path), ``product_group`` /
    ``group_id`` (group assignment), and ``product_attributes`` (dict keyed by
    category-attribute name or id -> value(s); auto-filled into ``icbuCatProp``
    using the schema's options/required rules). Relative paths resolve against the
    manifest's directory.
    """

    def __init__(
        self, data: dict[str, Any], base_dir: Path, *, allowed_roots: Iterable[Path] | None = None
    ):
        if "category_id" not in data:
            raise AlibabaError("Manifest is missing required key 'category_id'.")
        self.data = data
        self.base_dir = base_dir
        # If set, every asset path (images/video/price/content) must resolve here.
        self.allowed_roots = list(allowed_roots) if allowed_roots is not None else None

    @classmethod
    def load(cls, path: str, *, allowed_roots: Iterable[Path] | None = None) -> "ProductManifest":
        roots = list(allowed_roots) if allowed_roots is not None else None
        p = Path(ensure_allowed(path, roots)) if roots is not None else Path(path).expanduser()
        if not p.exists():
            raise AlibabaError(f"Manifest not found: {path}")
        data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data, p.parent, allowed_roots=roots)

    def resolve(self, rel: str) -> str:
        candidate = Path(rel).expanduser()
        if not candidate.is_absolute():
            candidate = self.base_dir / rel
        if self.allowed_roots is not None:
            return str(ensure_allowed(str(candidate), self.allowed_roots))
        return str(candidate)

    def content(self) -> dict[str, Any] | None:
        """The AI-generated structured content, from inline ``content`` or a
        ``content_file`` (e.g. ``content/detail.json``), or None."""
        if isinstance(self.data.get("content"), dict):
            return self.data["content"]
        cf = self.data.get("content_file")
        if cf:
            p = Path(self.resolve(cf))
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        return None


class _UploadCache:
    """content-hash -> {file_id, url}, persisted as JSON in the state dir."""

    def __init__(self, path: Path):
        self._path = path

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self._read().get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        data = self._read()
        data[key] = value
        _atomic_write(self._path, json.dumps(data, ensure_ascii=False, indent=2))


def _safe_file_name(name: str) -> str:
    """Photo-bank file names allow only letters, digits, and Chinese — strip the
    rest (spaces, parentheses, …) from the stem, keep a lowercase extension."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, "jpg"
    cleaned = re.sub(r"[^0-9A-Za-z一-鿿]", "", stem) or "image"
    return f"{cleaned}.{ext.lower()}"


def _str_or_none(value: Any) -> str | None:
    return None if value is None else str(value)


def _resolve_option(sf: Any, value: Any) -> str:
    """Map a user-supplied option (displayName OR value/id) to the option value.

    Also matches the singular part of a "Singular/Plural" display name (e.g. the
    unit "Set" matches the option "Set/Sets")."""
    s = str(value).strip()
    for opt in getattr(sf, "options", []) or []:
        if opt.get("value") == s:
            return s
        dn = str(opt.get("displayName", "")).strip()
        if dn.lower() == s.lower() or dn.split("/", 1)[0].strip().lower() == s.lower():
            return opt.get("value")
    return s  # not an enumerated option — pass through (e.g. free-text inputValue)


def option_code(schema_fields: list, field_id: str, value: Any) -> str:
    """Resolve a display value to its option code for a given schema field id."""
    sf = next((f for f in schema_fields if f.id == field_id), None)
    return _resolve_option(sf, value) if sf is not None else str(value)


def _cat_prop_keys(sf: Any) -> set[str]:
    """The manifest keys that may address a category-attribute field: its field id
    (``p-20662``), the bare attr id (``20662``), and its display name."""
    fid = sf.id.lower()
    keys = {fid, sf.id[2:].lower() if sf.id.startswith("p-") else fid}
    if sf.name:
        keys.add(sf.name.strip().lower())
    return keys


def _build_cat_props(icbu_field: Any, product_attributes: dict[str, Any]) -> dict[str, Any] | None:
    """Build ``icbuCatProp`` values from a manifest ``product_attributes`` dict
    (keyed by attribute name or id), using each child's schema type/options."""
    if not icbu_field or not product_attributes:
        return None
    provided = {str(k).strip().lower(): v for k, v in product_attributes.items()}
    out: dict[str, Any] = {}
    for sf in icbu_field.children:
        val = next((provided[k] for k in _cat_prop_keys(sf) if k in provided), None)
        if val is None:
            continue
        if sf.type == "multiCheck":
            items = val if isinstance(val, (list, tuple)) else [val]
            out[sf.id] = [_resolve_option(sf, v) for v in items]
        elif sf.type == "singleCheck":
            first = val[0] if isinstance(val, (list, tuple)) else val
            out[sf.id] = _resolve_option(sf, first)
        else:  # input / free text
            first = val[0] if isinstance(val, (list, tuple)) else val
            out[sf.id] = str(first)
    return out or None


def missing_required_cat_props(schema_fields: list, filled: dict[str, Any]) -> list[str]:
    """Names of required icbuCatProp attributes not present in ``filled`` — from the
    schema's own requiredRule. Useful to warn before a real (non-draft) publish."""
    icbu = next((f for f in schema_fields if f.id == "icbuCatProp"), None)
    if not icbu:
        return []
    have = set((filled.get("icbuCatProp") or {}).keys())
    return [c.name or c.id for c in icbu.children if c.required and c.id not in have]


def _image_value(upload: dict[str, Any]) -> Any:
    """scImages value: the photo-bank URL as text WITH a required fileId attribute
    (``<value fileId="…">url</value>``). Falls back to a plain value if no file_id."""
    file_id = upload.get("file_id")
    url = upload.get("url")
    if file_id:
        return {"__value__": url or "", "__attrs__": {"fileId": file_id}}
    return url or file_id


class ProductService:
    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client
        self._cache = _UploadCache(config.state_dir / "photobank_cache.json")

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

    # ── media upload ────────────────────────────────────────────────────────
    def upload_image(
        self, local_path: str, access_token: str, *, group_id: str | int | None = None
    ) -> dict[str, Any]:
        """Upload one local image to the photo bank; return {file_id, url, cached}.

        Sends ``image_bytes`` as multipart (excluded from the signature) plus
        ``file_name`` / ``extra_context`` / ``group_id``. Caches by content hash.
        """
        asset = read_image(local_path, load_bytes=True)
        cache_key = f"{group_id}:{hashlib.sha256(asset.data).hexdigest()}"
        hit = self._cache.get(cache_key)
        if hit:
            return {**hit, "cached": True}

        # Filenames allow only letters/digits/Chinese — strip spaces/()/etc.
        safe_name = _safe_file_name(asset.filename)
        params: dict[str, Any] = {"file_name": safe_name, "extra_context": "{}"}
        if group_id is not None:
            params["group_id"] = str(group_id)
        files = {"image_bytes": (safe_name, asset.data, asset.content_type)}
        # photobank.upload lives on the legacy TOP gateway (/sync + `session`).
        body = self.client.call(
            self.config.method_photo_upload, params, access_token=access_token,
            files=files, protocol="sync",
        )
        resp = body.get("upload_image_response") or {}
        result = {"file_id": _str_or_none(resp.get("file_id")), "url": resp.get("photobank_url")}
        if not result["url"] and not result["file_id"]:
            raise AlibabaError(f"photobank.upload returned no file_id/url: {body}")
        self._cache.put(cache_key, result)
        return {**result, "cached": False}

    # ── publish ───────────────────────────────────────────────────────────
    def publish_product(
        self, manifest: ProductManifest, access_token: str, *, draft: bool = False
    ) -> dict[str, Any]:
        """Publish a product (schema.add), or create a draft (schema.add.draft).

        Drafts are not listed live and are ideal for verifying a manifest.
        """
        cat_id = manifest.data["category_id"]
        language = manifest.data.get("language", "en_US")
        publish_type = manifest.data.get("publish_type", "default")
        version = manifest.data.get("version", DEFAULT_VERSION)
        group_id = manifest.data.get("photobank_group_id")

        schema_fields = parse_item_schema(
            self._fetch_schema_xml(cat_id, access_token, language, publish_type)
        )
        fields = self._assemble_fields(manifest, access_token, group_id, schema_fields)

        xml = build_value_xml(schema_fields, fields)
        req = {
            "publish_type": publish_type,
            "xml": xml,
            "cat_id": str(cat_id),
            "language": language,
            "version": version,
        }
        method = self.config.method_schema_add_draft if draft else self.config.method_schema_add
        body = self.client.call(
            method,
            {"param_product_top_publish_request": json.dumps(req, ensure_ascii=False)},
            access_token=access_token,
        )
        return {
            "filled_fields": sorted(fields),
            "product_id": body.get("product_id"),
            "biz_success": body.get("biz_success"),
            "missing_required": missing_required_cat_props(schema_fields, fields),
            "response": body,
        }

    def update_product(
        self, product_id: str, manifest: ProductManifest, access_token: str
    ) -> dict[str, Any]:
        """Incremental update via schema.update — only the fields in the manifest."""
        cat_id = manifest.data["category_id"]
        language = manifest.data.get("language", "en_US")
        publish_type = manifest.data.get("publish_type", "default")
        version = manifest.data.get("version", DEFAULT_VERSION)
        group_id = manifest.data.get("photobank_group_id")

        schema_fields = parse_item_schema(
            self._fetch_schema_xml(cat_id, access_token, language, publish_type)
        )
        fields = self._assemble_fields(manifest, access_token, group_id, schema_fields)
        xml = build_value_xml(schema_fields, fields)
        req = {
            "publish_type": publish_type,
            "xml": xml,
            "cat_id": str(cat_id),
            "language": language,
            "version": version,
            "productId": str(product_id),
        }
        body = self.client.call(
            self.config.method_schema_update,
            {"param_product_top_publish_request": json.dumps(req, ensure_ascii=False)},
            access_token=access_token,
        )
        return {
            "filled_fields": sorted(fields),
            "product_id": body.get("product_id") or product_id,
            "biz_success": body.get("biz_success"),
            "missing_required": missing_required_cat_props(schema_fields, fields),
            "response": body,
        }

    # ── field assembly (local files + AI content -> schema values) ──────────
    def _assemble_fields(
        self, manifest: ProductManifest, access_token: str, group_id: Any,
        schema_fields: list | None = None,
    ) -> dict[str, Any]:
        fields: dict[str, Any] = dict(manifest.data.get("fields", {}))
        content = manifest.content()

        # AI structured content -> title / highlights / attributes / desc type
        if content:
            if content.get("title") and "productTitle" not in fields:
                fields["productTitle"] = content["title"]
            if content.get("highlights") and "textDesc" not in fields:
                fields["textDesc"] = content["highlights"]
            attrs = content.get("attributes") or []
            if attrs and "customMoreProperty" not in fields:
                cmp: dict[str, Any] = {}
                for a in attrs[:30]:
                    name, value = a.get("name"), a.get("value")
                    if name and value:
                        cmp[f"customMoreProperty_{len(cmp)}"] = {"propName": name, "valueName": value}
                if cmp:
                    fields["customMoreProperty"] = cmp
            if "productDescType" not in fields:
                fields["productDescType"] = str(
                    manifest.data.get("product_desc_type", self.config.product_desc_type_default)
                )
            # Company Introduction -> companyDesc
            if content.get("company_intro") and "companyDesc" not in fields:
                fields["companyDesc"] = content["company_intro"]
            # FAQs -> companyFaqDesc (multiComplex: question + answers)
            faqs = content.get("faqs") or []
            if faqs and "companyFaqDesc" not in fields:
                fields["companyFaqDesc"] = [
                    {"question": f.get("question", ""), "answers": f.get("answer") or f.get("answers", "")}
                    for f in faqs
                    if f.get("question")
                ]

        # category attributes -> icbuCatProp (p-<attr_id>). Driven by the schema's
        # own icbuCatProp children (their type/options/requiredRule), so required
        # detection and option codes come straight from schema.get.
        if "icbuCatProp" not in fields and manifest.data.get("product_attributes"):
            icbu = next((f for f in (schema_fields or []) if f.id == "icbuCatProp"), None)
            cat_props = _build_cat_props(icbu, manifest.data["product_attributes"])
            if cat_props:
                fields["icbuCatProp"] = cat_props

        # product group assignment -> productGroup (first/second/third_group_id).
        # Accept a dict {first_group_id,...} or a shortcut `group_id` (leaf level).
        if "productGroup" not in fields:
            pg = manifest.data.get("product_group")
            if isinstance(pg, dict):
                fields["productGroup"] = {k: v for k, v in pg.items() if v is not None}
            elif manifest.data.get("group_id") is not None:
                fields["productGroup"] = {"first_group_id": manifest.data["group_id"]}

        # main images -> scImages_0..N (photo-bank URLs)
        # scImages: existing photo-bank refs ({file_id, url}) take precedence over
        # local images (which are uploaded first).
        main_refs = manifest.data.get("main_image_refs")
        main = manifest.data.get("main_images", [])
        if "scImages" not in fields and (main_refs or main):
            sc: dict[str, Any] = {}
            if main_refs:
                for i, ref in enumerate(main_refs[:6]):
                    sc[f"scImages_{i}"] = _image_value(ref)
            else:
                for i, rel in enumerate(main[:6]):
                    up = self.upload_image(manifest.resolve(rel), access_token, group_id=group_id)
                    sc[f"scImages_{i}"] = _image_value(up)
            fields["scImages"] = sc

        # detail images -> detailImage (from AI modules if present, else the list);
        # existing detail_image_refs (URLs) are used directly without uploading.
        if "detailImage" not in fields:
            entries = self._build_detail_image(
                content,
                manifest.data.get("detail_images", []),
                manifest.data.get("detail_image_refs"),
                manifest,
                access_token,
                group_id,
            )
            if entries:
                fields["detailImage"] = entries

        # video -> validated locally; the video subsystem (alibaba.icbu.video.*)
        # owns the actual upload/association, so we surface metadata for now.
        video = manifest.data.get("video")
        if video:
            fields.setdefault("_video", read_video(manifest.resolve(video), load_bytes=False).summary())

        # prices -> ladderPrice (tiered) when scPrice == "1"
        price_file = manifest.data.get("price_file")
        if price_file:
            rows = read_prices(manifest.resolve(price_file))
            if fields.get("scPrice") == "1" and "ladderPrice" not in fields:
                ladder: dict[str, Any] = {}
                for i, r in enumerate(rows[:4]):
                    qty = r.get("min_order_quantity") or r.get("quantity") or 1
                    ladder[f"ladderPrice_{i}"] = {"quantity": qty, "price": r.get("price")}
                fields["ladderPrice"] = ladder
            if "minOrderQuantity" not in fields and rows:
                moq = rows[0].get("min_order_quantity")
                if moq is not None:
                    fields["minOrderQuantity"] = moq

        # Publishing title rules (safety net for manually-set titles too): strip
        # disallowed special characters and cap at 128 characters. Casing is left
        # as-is here (AI content is already title-cased by the generator).
        if fields.get("productTitle"):
            fields["productTitle"] = enforce_length(clean_title(str(fields["productTitle"])))

        return fields

    def _build_detail_image(
        self,
        content: dict[str, Any] | None,
        detail_paths: list[str],
        detail_refs: list[str] | None,
        manifest: ProductManifest,
        access_token: str,
        group_id: Any,
    ) -> list[dict[str, Any]]:
        """Build the ``detailImage`` value (list of {gallery, images:[{imageURL,
        generalText}]}), uploading each referenced image once.

        With AI ``content.modules``: image modules reference detail images by
        ``image_slot`` and carry captions; standalone text modules are folded into
        the caption of the adjacent image (gallery 300 supports text). Without
        content: each detail image becomes one entry under ``detail_gallery``.
        """
        uploaded: dict[int, dict[str, Any]] = {}

        def url_for(slot: int) -> str | None:
            if slot is None or slot < 0:
                return None
            if detail_refs:  # existing photo-bank URLs, no upload
                return detail_refs[slot] if slot < len(detail_refs) else None
            if slot >= len(detail_paths):
                return None
            if slot not in uploaded:
                uploaded[slot] = self.upload_image(
                    manifest.resolve(detail_paths[slot]), access_token, group_id=group_id
                )
            return uploaded[slot]["url"] or uploaded[slot]["file_id"]

        if content and content.get("modules"):
            # (gallery, slot, caption) triples, folding text modules into captions
            triples: list[list[Any]] = []
            pending: list[str] = []
            for m in content["modules"]:
                if m.get("type") == "text" and m.get("text"):
                    if triples:
                        triples[-1][2] = (triples[-1][2] + "\n" + m["text"]).strip()
                    else:
                        pending.append(m["text"])
                elif m.get("type") == "image":
                    cap = m.get("caption", "") or ""
                    if pending:
                        cap = ("\n".join(pending) + "\n" + cap).strip()
                        pending = []
                    triples.append([str(m.get("gallery", "300")), m.get("image_slot", 0), cap])

            by_gallery: dict[str, list[dict[str, Any]]] = {}
            for gallery, slot, caption in triples:
                url = url_for(slot)
                if not url:
                    continue
                img: dict[str, Any] = {"imageURL": url}
                if caption and gallery == "300":  # only detail shots support text
                    img["generalText"] = caption[:500]
                by_gallery.setdefault(gallery, []).append(img)
            return [{"gallery": g, "images": imgs} for g, imgs in by_gallery.items() if imgs]

        n = len(detail_refs) if detail_refs else len(detail_paths)
        if n:
            imgs = [{"imageURL": url_for(i)} for i in range(n)]
            imgs = [im for im in imgs if im["imageURL"]]
            if imgs:
                return [{"gallery": str(manifest.data.get("detail_gallery", "300")), "images": imgs}]
        return []
