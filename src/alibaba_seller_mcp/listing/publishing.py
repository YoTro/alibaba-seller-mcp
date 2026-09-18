"""The application service for "a brief folder → a published listing".

:class:`PublishFromBrief` owns the whole flow in one place — code-render the media,
fetch the schema, ask Claude for the copy, resolve attributes, build the commercial
fields, publish — so the MCP tool above it stays an adapter and no step can be
forgotten by a caller assembling the flow itself.

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
      "keywords": [...], "features": [...], "company_intro": "...", "language": "en_US",
      "ai": true,                     # false = no Claude calls: everything comes from the brief
      "content": {"title": "...", "highlights": "...", "faqs": [...], ...},   # manual overrides
      "photos": {...}, "detail_spec": {"pages": [...]}, "how_to_use": [...], "parts": [...]
    }
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..alibaba.manifest import ProductManifest
from ..alibaba.values import DEFAULT_LEAD_TIME, build_ladder_period, option_code
from ..files.readers import MIN_IMAGE_SIDE, image_quality_issues, read_image  # noqa: F401
from ..pathsafe import ensure_allowed, ensure_asset_allowed
from .media import prepare_brief_media

MAIN_MIN, MAIN_MAX = 4, 6

# Every key the flow reads. A brief is a hand-written JSON file, so a typo here
# is the likeliest thing to go wrong — and the quietest: an unknown key is simply
# never read, the publish succeeds, and the listing is missing whatever it was
# meant to carry. These are warnings, not errors, so a brief written for a newer
# version of this server still publishes.
_KNOWN_BRIEF_KEYS = frozenset({
    "ai", "base_dir", "brand", "brand_name", "brief", "category_id", "company_intro",
    "content", "description", "detail_spec", "facts", "features", "group", "how_to_use",
    "images", "instructions", "keywords", "language", "lead_time", "model", "oem_odm",
    "parts", "photos", "price", "product_name", "sale_props", "sale_type",
    "sku_code_prefix", "theme", "version",
})


def _did_you_mean(name: str, candidates) -> str:
    """`" (did you mean 'Pest Control Type'?)"` — or nothing when nothing is close."""
    match = difflib.get_close_matches(str(name).lower(), [str(c).lower() for c in candidates], n=1, cutoff=0.7)
    if not match:
        return ""
    actual = next((c for c in candidates if str(c).lower() == match[0]), match[0])
    return f" (did you mean {actual!r}?)"


def _unknown_key_warnings(brief: dict[str, Any], schema_fields: list) -> list[str]:
    """Keys the flow will silently ignore: unknown top-level keys, and `facts`
    entries matching no category attribute or sales property."""
    warnings: list[str] = []

    unknown = sorted(set(brief) - _KNOWN_BRIEF_KEYS)
    if unknown:
        warnings.append(
            "ignored unknown brief key(s): "
            + ", ".join(f"{k}{_did_you_mean(k, _KNOWN_BRIEF_KEYS)}" for k in unknown)
        )

    facts = brief.get("facts") or {}
    if not facts:
        return warnings
    icbu = next((f for f in schema_fields if f.id == "icbuCatProp"), None)
    sale = next((f for f in schema_fields if f.id == "saleProp"), None)
    attr_names = [c.name for c in (icbu.children if icbu else []) if c.name]
    sale_names = [c.name for c in (sale.children if sale else []) if c.name]
    known = {n.lower() for n in attr_names + sale_names} | set(_FACT_ALIASES) | set(_FACT_ALIASES.values())
    stray = sorted(k for k in facts if str(k).strip().lower() not in known)
    if stray:
        warnings.append(
            "ignored facts key(s) matching no attribute of this category: "
            + ", ".join(f"{k}{_did_you_mean(k, attr_names + sale_names)}" for k in stray)
        )
    return warnings
DETAIL_MAX = 30          # detailImage images cap (schema maxItemsRule)

# Map friendly fact keys to category-attribute display names.
_FACT_ALIASES = {
    "place_of_origin": "place of origin",
    "origin": "place of origin",
    "material": "material",
    "brand": "brand name",
    "model": "model number",
}


def _local(e: str, base_dir: Path | None, allowed_roots=None) -> str:
    """Resolve a local image path named in the brief, confined to the allowlist.

    These paths are uploaded to the photo bank, so an unchecked one would publish
    an arbitrary local file (see pathsafe.ensure_asset_allowed).
    """
    p = Path(e).expanduser()
    if base_dir is not None and not p.is_absolute() and not p.exists():
        p = base_dir / p
    return str(ensure_asset_allowed(str(p), None, allowed_roots))


def _prep_main_images(
    entries: list, products, token: str, base_dir: Path | None = None, allowed_roots=None
) -> tuple[list[dict], bool, list[str]]:
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
                local = _local(str(e), base_dir, allowed_roots)
                asset = read_image(local, load_bytes=False)
                warns.extend(image_quality_issues(asset, kind="main"))
                up = products.upload_image(local, token)
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


def _prep_detail_images(
    entries: list, products, token: str, base_dir: Path | None = None, allowed_roots=None
) -> tuple[list[str], list[str]]:
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
                local = _local(str(e), base_dir, allowed_roots)
                asset = read_image(local, load_bytes=False)
                warns.extend(image_quality_issues(asset, kind="detail"))
                up = products.upload_image(local, token)
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
    if todo and detail is not None:  # detail=None → manual mode, no AI selection
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


@dataclass(frozen=True)
class BriefOutcome:
    """What one brief run produced. The MCP layer maps this onto its result model."""

    product_id: str | None = None
    biz_success: Any = None
    filled_fields: list[str] = field(default_factory=list)
    missing_required: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    ai_filled: list[str] = field(default_factory=list)
    needs_more_main_images: bool = False
    response: dict[str, Any] = field(default_factory=dict)


class PublishFromBrief:
    """Application service: one brief folder in, one listing out.

    Owns the whole flow — media, schema, AI copy, attributes, commercial fields,
    publish — so no caller has to know the order or that there is an order. Its
    collaborators are injected: ``products`` (the publish API), ``detail`` (the AI
    copywriter) and ``detail_spec`` (the AI detail-page spec writer). Pass
    ``detail=None`` / ``detail_spec=None`` for a run that may not call Claude; a
    brief with ``"ai": false`` never calls it either way.
    """

    def __init__(self, products, *, detail=None, detail_spec=None, allowed_roots=None):
        self.products = products
        self.detail = detail
        self.detail_spec = detail_spec
        self.allowed_roots = allowed_roots

    # ── entry points ───────────────────────────────────────────────────────
    def load(self, brief_path: str) -> tuple[dict[str, Any], Path]:
        """Read a ``brief.json``, confined to the allowlist.

        Returns the brief and its folder — relative photo/image paths in a brief
        resolve against the folder it lives in, not the process's cwd.
        """
        p = Path(brief_path).expanduser()
        if self.allowed_roots is not None:
            p = ensure_allowed(str(p), self.allowed_roots)
        return json.loads(p.read_text(encoding="utf-8")), p.parent

    def run(
        self, brief: dict[str, Any], *, token: str, base_dir: Path | None = None,
        draft: bool = True,
    ) -> BriefOutcome:
        """Publish (or draft) one brief. The brief is not mutated."""
        brief = dict(brief)
        base_dir = self._base_dir(brief, base_dir)
        warnings: list[str] = []
        ai_filled: list[str] = []

        cat_id = brief.get("category_id")
        if not cat_id:
            raise ValueError("brief.category_id is required (automatic category prediction is not enabled yet).")
        brief_text = brief.get("brief") or brief.get("description")
        if not brief_text:
            raise ValueError("brief.brief (a short product description) is required.")
        language = brief.get("language", "en_US")
        use_ai = brief.get("ai", True) is not False

        # 1. media — render main images / detail pages from photos when the brief
        #    has photos but no images yet. Must run first: the copywriter is told
        #    how many detail images it may reference.
        images, notes = self._prepare_media(brief, base_dir, use_ai)
        ai_filled += notes

        # 2. schema
        schema_fields = self.products.get_schema_fields(cat_id, token, language=language)
        icbu = next((f for f in schema_fields if f.id == "icbuCatProp"), None)
        warnings.extend(_unknown_key_warnings(brief, schema_fields))

        # 3. images -> photo-bank refs (uploading local files)
        main_refs, need_more, mwarn = _prep_main_images(
            images.get("main") or [], self.products, token, base_dir, self.allowed_roots)
        warnings.extend(mwarn)
        detail_refs, dwarn = _prep_detail_images(
            images.get("detail") or [], self.products, token, base_dir, self.allowed_roots)
        warnings.extend(dwarn)

        # 4. copy + attributes
        content, cwarn, cfilled = self._content(brief, brief_text, language, len(detail_refs), use_ai)
        warnings.extend(cwarn)
        ai_filled += cfilled
        product_attributes, aattr = _resolve_attributes(
            icbu, brief.get("facts") or {}, brief_text, self.detail if use_ai else None
        )
        if aattr:
            ai_filled.append("category attributes: " + ", ".join(aattr))

        # 5. commercial fields + sales properties
        fields_, fwarn = self._commercial_fields(brief, schema_fields)
        warnings.extend(fwarn)
        sale_props, swarn = self._sale_props(brief, schema_fields)
        warnings.extend(swarn)

        # 6. publish
        data: dict[str, Any] = {
            "category_id": cat_id,
            "language": language,
            "fields": fields_,
            "main_image_refs": main_refs,
            "detail_image_refs": detail_refs,
            "product_attributes": product_attributes,
            "sale_props": sale_props,
        }
        if content:
            data["content"] = content
        # per-SKU seller code "Commodity code" (skuOuterId) = "<prefix>-<value>",
        # prefix defaults to the model; only emitted when the product has sale_props.
        sku_prefix = brief.get("sku_code_prefix") or brief.get("model")
        if sku_prefix:
            data["sku_code_prefix"] = sku_prefix
        group, gwarn = _product_group(brief.get("group"))
        warnings.extend(gwarn)
        if group:
            data["product_group"] = group

        manifest = ProductManifest(data, base_dir=Path.cwd(), allowed_roots=self.allowed_roots)
        # reuse the schema fetched in step 2 — same category, language and
        # publish_type, so a second schema.get would fetch the identical form
        r = self.products.publish_product(manifest, token, draft=draft, schema_fields=schema_fields)
        return BriefOutcome(
            product_id=r.get("product_id"),
            biz_success=r.get("biz_success"),
            filled_fields=r.get("filled_fields", []),
            missing_required=r.get("missing_required", []),
            warnings=warnings,
            ai_filled=ai_filled,
            needs_more_main_images=need_more,
            response=r.get("response", {}),
        )

    # ── steps ──────────────────────────────────────────────────────────────
    def _base_dir(self, brief: dict[str, Any], base_dir: Path | None) -> Path | None:
        """The folder this brief belongs to, or None if it does not name one.

        * a brief read from a file uses its own folder (``load`` supplies it);
        * an inline brief may name one with ``base_dir``;
        * otherwise None — an inline brief that carries no paths needs no folder.

        There is deliberately no working-directory fallback. Rendering writes a
        whole ``images/`` tree, and defaulting that to wherever the process
        happens to be running drops generated files into unrelated directories
        (it littered this repo's root on every test run). Reading is different:
        a relative path with no folder to resolve against still falls back to the
        cwd at the point of use, the same as every other path argument here.
        """
        if base_dir is not None:
            return Path(base_dir)
        declared = brief.get("base_dir")
        if not declared:
            return None
        p = Path(declared).expanduser()
        return Path(ensure_allowed(str(p), self.allowed_roots)) if self.allowed_roots is not None else p

    def _prepare_media(
        self, brief: dict[str, Any], base_dir: Path | None, use_ai: bool
    ) -> tuple[dict[str, Any], list[str]]:
        """Code-render main images / detail pages from ``photos``, if needed.

        Only fills what the brief left empty, so a brief that already names its
        images (or photo-bank refs) is untouched.
        """
        images = dict(brief.get("images") or {})
        if not brief.get("photos"):
            return images, []
        if images.get("main") and images.get("detail"):
            return images, []
        if base_dir is None:
            # Rendering creates an images/ tree; it must go somewhere the caller
            # chose. Refusing is better than guessing the cwd, and better than
            # the old behaviour of ignoring `photos` without a word.
            raise ValueError(
                "this brief has `photos` but no folder to render into. Pass it as "
                "`brief_path` (the file's own folder is used), or add a `base_dir` "
                "key naming the folder the photos live in and images/ should be written to."
            )
        prep = prepare_brief_media(
            brief, base_dir, self.detail_spec if use_ai else None,
            allowed_roots=self.allowed_roots,
        )
        notes: list[str] = []
        if not images.get("main"):
            images["main"] = prep.main_paths
            notes.append(f"main images prepared from photos ({len(prep.main_paths)})")
        if not images.get("detail"):
            images["detail"] = prep.detail_paths
            notes.append(f"detail images rendered from {prep.spec_source} spec ({len(prep.detail_paths)})")
        return images, notes

    def _content(
        self, brief: dict[str, Any], brief_text: str, language: str,
        detail_image_count: int, use_ai: bool,
    ) -> tuple[dict[str, Any] | None, list[str], list[str]]:
        """AI copy, then the brief's own ``content`` keys on top (manual always wins)."""
        warnings: list[str] = []
        ai_filled: list[str] = []
        overrides = dict(brief.get("content") or {})
        if brief.get("company_intro"):
            overrides.setdefault("company_intro", brief["company_intro"])

        content: dict[str, Any] | None = None
        if use_ai and self.detail is not None:
            brand = brief.get("brand")
            gen = self.detail.generate(
                product_name=brief_text,
                version=brief.get("version", "premium"),
                features=brief.get("features"),
                keywords=brief.get("keywords"),
                brands=[brand] if isinstance(brand, str) else list(brand or []),
                language="English" if language.startswith("en") else language,
                detail_image_count=detail_image_count,
                extra_instructions=brief.get("instructions", ""),   # e.g. compliance wording rules
            )
            content = gen.get("content")
            if content:
                ai_filled += ["title", "highlights", "detail modules", "FAQs", "customMoreProperty"]

        manual = {k: v for k, v in overrides.items() if v not in (None, "", [])}
        if manual:
            content = {**(content or {}), **manual}
            ai_filled.append("manual content: " + ", ".join(sorted(manual)))
        if not use_ai and not (content or {}).get("title"):
            warnings.append("ai=false and no content.title — the draft will have no title")
        return content, warnings, ai_filled

    def _commercial_fields(
        self, brief: dict[str, Any], schema_fields: list
    ) -> tuple[dict[str, Any], list[str]]:
        """Price, MOQ, tiered price, sale type and the shipping ladder."""
        warnings: list[str] = []
        price = brief.get("price") or {}
        fields_: dict[str, Any] = {"saleType": brief.get("sale_type", "normal"), "scPrice": "1"}
        if price.get("unit") is not None:
            fields_["priceUnit"] = option_code(schema_fields, "priceUnit", price["unit"])
        if price.get("moq") is not None:
            fields_["minOrderQuantity"] = price["moq"]
        tiers = price.get("tiers") or []
        if tiers:
            fields_["ladderPrice"] = {
                f"ladderPrice_{i}": {"quantity": t[0], "price": t[1]} for i, t in enumerate(tiers[:4])
            }
        else:
            warnings.append("no price tiers provided (price.tiers) — ladderPrice left empty")

        lead_time = brief.get("lead_time")
        if not lead_time:
            lead_time = DEFAULT_LEAD_TIME
            warnings.append(
                f"lead_time not set — ladderPeriod defaulted to ≤{lead_time[0][0]} units → "
                f"{lead_time[0][1]} days; set brief.lead_time = [[quantity, days], …]"
            )
        fields_["ladderPeriod"] = build_ladder_period(lead_time)
        return fields_, warnings

    @staticmethod
    def _sale_props(brief: dict[str, Any], schema_fields: list) -> tuple[dict[str, Any], list[str]]:
        """Sales properties (Color, Size …) — the SKU-defining attributes.

        A ``Color`` left in ``facts`` is moved here: it is a sales property, not a
        category attribute, and sellers reliably put it in the wrong place.
        """
        warnings: list[str] = []
        sale_props: dict[str, Any] = dict(brief.get("sale_props") or {})
        sale_field = next((f for f in schema_fields if f.id == "saleProp"), None)
        if not sale_field:
            return sale_props, warnings
        sale_names = {(c.name or "").lower() for c in sale_field.children}
        for k, v in (brief.get("facts") or {}).items():
            key = str(k).strip().lower()
            if key in sale_names and key not in {s.lower() for s in sale_props}:
                sale_props[k] = v
        missing = [c.name or c.id for c in sale_field.children
                   if c.required and (c.name or "").lower() not in {s.lower() for s in sale_props}]
        if missing:
            warnings.append("required sales properties not set (brief.sale_props): " + ", ".join(missing))
        return sale_props, warnings


def _product_group(group: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """``group`` shortcut (an id or a {first_group_id} dict) -> the manifest value."""
    if isinstance(group, dict):
        return group, []
    if isinstance(group, (str, int)) and str(group).isdigit():
        return {"first_group_id": str(group)}, []
    if group is not None:
        return None, [f"group {group!r} is not a numeric id; use a group id or {{first_group_id}} — skipped"]
    return None, []
