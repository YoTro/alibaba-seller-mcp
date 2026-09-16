"""Assembling one manifest into the filled ``{field id: value}`` map.

This is the seam between the seller's data (a manifest, the AI's structured
content, local files) and the itemSchema the publish API wants. It owns the
platform's content rules — length caps, title cleanup, how detail modules fold
into ``detailImage`` galleries.

**It has side effects, by design** — hence *assembler*, not *builder*: it reads
the files a manifest points at (price table, video) and uploads its images to
the photo bank as it goes, because which images need uploading is only known
while walking the AI's detail modules. The rules that are genuinely pure live
next door in :mod:`.values`; the uploader is injected so the assembler is still
testable without a network.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from ..files.readers import read_prices, read_video
from ..text_format import clean_title, enforce_length
from .manifest import ProductManifest
from .photobank import PhotoBank
from .values import (
    MAX_ATTR_NAME_LEN, MAX_ATTR_VALUE_LEN, MAX_COMPANY_DESC_LEN, MAX_FAQ_ANSWER_LEN,
    MAX_FAQ_COUNT, MAX_FAQ_QUESTION_LEN, MAX_HIGHLIGHTS_LEN, _build_cat_props,
    _build_sale_props, _image_value, build_ladder_period, build_skus, clip_body, clip_text,
)


class FieldAssembler:
    """Assembles publish-schema values from a manifest.

    Not a pure builder: assembling reads the manifest's referenced files and
    uploads its images through ``photos``. Inject a stub photo bank to exercise
    it offline.
    """

    def __init__(self, config: Config, photos: PhotoBank):
        self.config = config
        self.photos = photos

    # ── field assembly (local files + AI content -> schema values) ──────────
    def assemble(
        self, manifest: ProductManifest, access_token: str, group_id: Any = None,
        schema_fields: list | None = None,
    ) -> dict[str, Any]:
        """The filled ``{field id: value}`` map for one manifest.

        Uploads any local images it meets and reads any files the manifest names.
        """
        fields: dict[str, Any] = dict(manifest.data.get("fields", {}))
        content = manifest.content()

        # AI structured content -> title / highlights / attributes / desc type
        if content:
            if content.get("title") and "productTitle" not in fields:
                fields["productTitle"] = content["title"]
            if content.get("highlights") and "textDesc" not in fields:
                fields["textDesc"] = clip_body(content["highlights"], MAX_HIGHLIGHTS_LEN)
            attrs = content.get("attributes") or []
            if attrs and "customMoreProperty" not in fields:
                cmp: dict[str, Any] = {}
                for a in attrs[:30]:
                    name, value = a.get("name"), a.get("value")
                    if name and value:
                        cmp[f"customMoreProperty_{len(cmp)}"] = {
                            "propName": clip_text(str(name), MAX_ATTR_NAME_LEN),
                            "valueName": clip_text(str(value), MAX_ATTR_VALUE_LEN),
                        }
                if cmp:
                    fields["customMoreProperty"] = cmp
            if "productDescType" not in fields:
                fields["productDescType"] = str(
                    manifest.data.get("product_desc_type", self.config.product_desc_type_default)
                )
            # Company Introduction -> companyDesc
            if content.get("company_intro") and "companyDesc" not in fields:
                fields["companyDesc"] = clip_body(content["company_intro"], MAX_COMPANY_DESC_LEN)
            # FAQs -> companyFaqDesc (multiComplex: question + answers)
            faqs = content.get("faqs") or []
            if faqs and "companyFaqDesc" not in fields:
                fields["companyFaqDesc"] = [
                    {
                        "question": clip_text(f.get("question", ""), MAX_FAQ_QUESTION_LEN),
                        "answers": clip_body(f.get("answer") or f.get("answers", ""), MAX_FAQ_ANSWER_LEN),
                    }
                    for f in faqs[:MAX_FAQ_COUNT]
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

        # sales properties (Color/Size …) -> saleProp, values carry inputValue
        if "saleProp" not in fields and manifest.data.get("sale_props"):
            sale = next((f for f in (schema_fields or []) if f.id == "saleProp"), None)
            sale_props = _build_sale_props(sale, manifest.data["sale_props"])
            if sale_props:
                fields["saleProp"] = sale_props

        # per-SKU seller code (skuOuterId "Commodity code") = "<model>-<value>",
        # one sku row per sale-property value, linked to it via props.
        if "sku" not in fields and manifest.data.get("sale_props") and manifest.data.get("sku_code_prefix"):
            sale = next((f for f in (schema_fields or []) if f.id == "saleProp"), None)
            skus = build_skus(sale, manifest.data["sale_props"], str(manifest.data["sku_code_prefix"]))
            if skus:
                fields["sku"] = skus

        # shipping ladder -> ladderPeriod ("up to N units → M days"), required by the console
        if "ladderPeriod" not in fields and manifest.data.get("lead_time"):
            fields["ladderPeriod"] = build_ladder_period(manifest.data["lead_time"])

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
                    up = self.photos.upload(manifest.resolve(rel), access_token, group_id=group_id)
                    sc[f"scImages_{i}"] = _image_value(up)
            fields["scImages"] = sc

        # detail images -> detailImage (from AI modules if present, else the list);
        # existing detail_image_refs (URLs) are used directly without uploading.
        if "detailImage" not in fields:
            entries = self._detail_image(
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

    def _detail_image(
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
                uploaded[slot] = self.photos.upload(
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
            used: set[int] = set()
            for gallery, slot, caption in triples:
                url = url_for(slot)
                if not url:
                    continue
                used.add(slot)
                img: dict[str, Any] = {"imageURL": url}
                if caption and gallery == "300":  # only detail shots support text
                    img["generalText"] = caption[:500]
                by_gallery.setdefault(gallery, []).append(img)
            # Seller-supplied detail images the AI modules did not reference are
            # appended (uncaptioned) so nothing the seller provided is dropped.
            total = len(detail_refs) if detail_refs else len(detail_paths)
            for slot in range(total):
                if slot in used:
                    continue
                url = url_for(slot)
                if url:
                    by_gallery.setdefault("300", []).append({"imageURL": url})
            return [{"gallery": g, "images": imgs} for g, imgs in by_gallery.items() if imgs]

        n = len(detail_refs) if detail_refs else len(detail_paths)
        if n:
            imgs = [{"imageURL": url_for(i)} for i in range(n)]
            imgs = [im for im in imgs if im["imageURL"]]
            if imgs:
                return [{"gallery": str(manifest.data.get("detail_gallery", "300")), "images": imgs}]
        return []
