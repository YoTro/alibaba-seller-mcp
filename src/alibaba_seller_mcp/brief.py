"""Minimal "brief → published draft" flow.

The seller supplies only the irreducible facts (a short brief, brand, images,
price, hard facts like origin/material, and a category). Everything textual — title,
highlights, image-text detail modules, FAQs, custom parameters — and the
descriptive category attributes are enriched by Claude; the schema/option-code
plumbing is handled by the product layer. This is the difference from a bulk table
upload: you give facts, not thirty columns of prose and codes.

Brief shape (all human-facing):
    {
      "brief": "short product description + key real features",   # required
      "brand": "C01B" | ["C01B", "ABS"],
      "category_id": 201335115,                                   # required (v1)
      "images": {"main": [{"file_id","url"} | path | url], "detail": [url | ...]},
      "price": {"unit": "Set" | code, "moq": 10, "tiers": [[qty, price], ...]},
      "facts": {"place_of_origin": "China", "material": "ABS"},   # factual attrs
      "version": "premium" | "lite" | "general",                  # optional
      "group": "<group_id>" | {"first_group_id": "..."},          # optional
      "keywords": [...], "features": [...], "company_intro": "...", "language": "en_US"
    }
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .alibaba.products import ProductManifest, option_code
from .alibaba.schema import parse_item_schema
from .files.readers import MIN_IMAGE_SIDE, image_quality_issues, read_image  # noqa: F401

MAIN_MIN, MAIN_MAX = 4, 6
DETAIL_MAX = 30          # detailImage images cap (schema maxItemsRule)

# Map friendly fact keys to category-attribute display names.
_FACT_ALIASES = {
    "place_of_origin": "place of origin",
    "origin": "place of origin",
    "material": "material",
    "brand": "brand name",
    "model": "model number",
}


def _prep_main_images(entries: list, products, token: str) -> tuple[list[dict], bool, list[str]]:
    refs: list[dict] = []
    warns: list[str] = []
    for e in entries:
        if isinstance(e, dict):
            refs.append({"file_id": e.get("file_id"), "url": e.get("url")})
        elif isinstance(e, str) and e.startswith(("http://", "https://", "//")):
            refs.append({"url": e})
            warns.append(f"main image {e[:48]}… has no photo-bank file_id; scImages needs one — use {{file_id, url}}")
        elif isinstance(e, str) and e.isdigit():
            refs.append({"file_id": e})
        else:  # local path — validate, then attempt upload
            try:
                asset = read_image(str(e), load_bytes=False)
                warns.extend(image_quality_issues(asset, kind="main"))
                up = products.upload_image(str(e), token)
                refs.append({"file_id": up.get("file_id"), "url": up.get("url")})
            except Exception as exc:  # noqa: BLE001 — surface as a warning, keep going
                warns.append(f"main image {e}: {exc}")
    need_more = len(refs) < MAIN_MIN
    if need_more:
        warns.append(
            f"only {len(refs)} main image(s) — Alibaba wants 4–6. AI cannot generate real "
            "product photos; add more from your photo bank or upload them in the console."
        )
    if len(refs) > MAIN_MAX:
        warns.append(f"{len(refs)} main images provided; using the first {MAIN_MAX}.")
        refs = refs[:MAIN_MAX]
    return refs, need_more, warns


def _prep_detail_images(entries: list, products, token: str) -> tuple[list[str], list[str]]:
    """Return (detail image URLs, warnings). Accepts photo-bank URLs/dicts or local
    file paths (uploaded to the photo bank)."""
    out: list[str] = []
    warns: list[str] = []
    if len(entries) > DETAIL_MAX:
        warns.append(f"{len(entries)} detail images provided; using the first {DETAIL_MAX} (max).")
        entries = entries[:DETAIL_MAX]
    for e in entries:
        if isinstance(e, dict):
            u = e.get("url") or e.get("file_id")
            if u:
                out.append(u)
        elif isinstance(e, str) and (e.startswith(("http://", "https://", "//"))):
            out.append(e)
        elif isinstance(e, str):  # local path -> upload
            try:
                asset = read_image(str(e), load_bytes=False)
                warns.extend(image_quality_issues(asset, kind="detail"))
                up = products.upload_image(str(e), token)
                if up.get("url") or up.get("file_id"):
                    out.append(up.get("url") or up.get("file_id"))
            except Exception as exc:  # noqa: BLE001
                warns.append(f"detail image {e}: {exc}")
    return out, warns


def _resolve_attributes(icbu, facts: dict, brief_text: str, detail) -> tuple[dict, list[str]]:
    """facts (human, factual) + AI-selected descriptive attributes, keyed by name."""
    product_attributes: dict[str, Any] = {}
    ai_names: list[str] = []
    if icbu is None:
        return product_attributes, ai_names

    fact_by_name = {
        _FACT_ALIASES.get(str(k).strip().lower(), str(k).strip().lower()): v for k, v in (facts or {}).items()
    }
    covered: set[str] = set()
    for sf in icbu.children:
        if (sf.name or "").lower() in fact_by_name:
            product_attributes[sf.name] = fact_by_name[(sf.name or "").lower()]
            covered.add(sf.id)

    todo = [sf for sf in icbu.children if sf.required and sf.id not in covered]
    if todo:
        attrs = [
            {"name": sf.name, "type": sf.type, "options": [o["displayName"] for o in sf.options]}
            for sf in todo
        ]
        chosen = detail.select_attributes(brief_text, attrs)
        for sf in todo:
            if sf.name in chosen and chosen[sf.name] not in (None, "", []):
                product_attributes[sf.name] = chosen[sf.name]
                ai_names.append(sf.name)
    return product_attributes, ai_names


def publish_from_brief(
    brief: dict[str, Any], *, products, detail, token: str, allowed_roots=None, draft: bool = True
) -> dict[str, Any]:
    warnings: list[str] = []
    ai_filled: list[str] = []

    cat_id = brief.get("category_id")
    if not cat_id:
        raise ValueError("brief.category_id is required (automatic category prediction is not enabled yet).")
    brief_text = brief.get("brief") or brief.get("description")
    if not brief_text:
        raise ValueError("brief.brief (a short product description) is required.")
    language = brief.get("language", "en_US")
    brand = brief.get("brand")
    brands = [brand] if isinstance(brand, str) else list(brand or [])
    version = brief.get("version", "premium")

    schema_fields = parse_item_schema(products._fetch_schema_xml(cat_id, token, language, "default"))
    icbu = next((f for f in schema_fields if f.id == "icbuCatProp"), None)

    images = brief.get("images") or {}
    main_refs, need_more, mwarn = _prep_main_images(images.get("main") or [], products, token)
    warnings.extend(mwarn)
    detail_refs, dwarn = _prep_detail_images(images.get("detail") or [], products, token)
    warnings.extend(dwarn)

    # AI content
    gen = detail.generate(
        product_name=brief_text,
        version=version,
        features=brief.get("features"),
        keywords=brief.get("keywords"),
        brands=brands,
        language="English" if language.startswith("en") else language,
        detail_image_count=len(detail_refs),
    )
    content = gen.get("content")
    if content:
        ai_filled += ["title", "highlights", "detail modules", "FAQs", "customMoreProperty"]
        if brief.get("company_intro"):
            content["company_intro"] = brief["company_intro"]

    # attributes: facts + AI selection
    product_attributes, aattr = _resolve_attributes(icbu, brief.get("facts") or {}, brief_text, detail)
    if aattr:
        ai_filled.append("category attributes: " + ", ".join(aattr))

    # price
    price = brief.get("price") or {}
    fields: dict[str, Any] = {"saleType": brief.get("sale_type", "normal"), "scPrice": "1"}
    if price.get("unit") is not None:
        fields["priceUnit"] = option_code(schema_fields, "priceUnit", price["unit"])
    if price.get("moq") is not None:
        fields["minOrderQuantity"] = price["moq"]
    tiers = price.get("tiers") or []
    if tiers:
        fields["ladderPrice"] = {
            f"ladderPrice_{i}": {"quantity": t[0], "price": t[1]} for i, t in enumerate(tiers[:4])
        }
    else:
        warnings.append("no price tiers provided (price.tiers) — ladderPrice left empty")

    data: dict[str, Any] = {
        "category_id": cat_id,
        "language": language,
        "fields": fields,
        "main_image_refs": main_refs,
        "detail_image_refs": detail_refs,
        "product_attributes": product_attributes,
    }
    if content:
        data["content"] = content
    group = brief.get("group")
    if isinstance(group, dict):
        data["product_group"] = group
    elif isinstance(group, (str, int)) and str(group).isdigit():
        data["product_group"] = {"first_group_id": str(group)}
    elif group is not None:
        warnings.append(f"group {group!r} is not a numeric id; use a group id or {{first_group_id}} — skipped")

    manifest = ProductManifest(data, base_dir=Path.cwd(), allowed_roots=allowed_roots)
    result = products.publish_product(manifest, token, draft=draft)
    result["warnings"] = warnings
    result["ai_filled"] = ai_filled
    result["needs_more_main_images"] = need_more
    return result
