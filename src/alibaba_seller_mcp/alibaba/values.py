"""Pure builders for schema field values — no network, no filesystem, no state.

Everything here maps seller-facing data (an attribute name, a display value, a
lead-time ladder) onto what the itemSchema wants (option codes, ``inputValue``
attributes, ``ladderPeriod_0``…). Being pure, this is the layer worth testing
exhaustively: every rule the platform enforces lives here as a plain function.
"""

from __future__ import annotations

from typing import Any

# Custom parameters (customMoreProperty): the platform rejects attribute values longer
# than 70 characters; names are kept short as well.
MAX_ATTR_VALUE_LEN = 70
MAX_ATTR_NAME_LEN = 30
MAX_HIGHLIGHTS_LEN = 2000      # textDesc — 商品卖点
MAX_COMPANY_DESC_LEN = 2000    # companyDesc — 公司介绍
MAX_FAQ_QUESTION_LEN = 150
MAX_FAQ_ANSWER_LEN = 500
MAX_FAQ_COUNT = 8


def clip_text(text: str, limit: int) -> str:
    """Hard-cap ``text`` at ``limit`` characters, cutting at the last word boundary
    when one exists in the second half of the window (no ellipsis — platform fields
    are validated on raw length)."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return (cut[:space] if space > limit // 2 else cut).rstrip(" ,;:-")


def clip_body(text: str, limit: int) -> str:
    """Cap a multi-line body (highlights, company intro, FAQ answer) at ``limit``.

    Unlike :func:`clip_text` the line breaks are kept when the text already fits —
    these fields are paragraphs, and collapsing them would reflow the listing."""
    text = str(text)
    return text if len(text) <= limit else clip_text(text, limit)


def _id_str(value):
    """Product ids come back as JSON numbers; the result models expect strings."""
    return None if value is None else str(value)


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
            # Free-text attributes follow the same convention as custom saleProp
            # values: the text goes in ``inputValue`` and the element keeps a
            # negative placeholder. A plain ``<value>text</value>`` is silently
            # dropped by the platform (it comes back as ``-1``).
            first = val[0] if isinstance(val, (list, tuple)) else val
            text = str(first)
            if sf.max_length:
                text = clip_text(text, sf.max_length)
            out[sf.id] = {"__value__": "-1", "__attrs__": {"inputValue": text}}
    return out or None


def missing_required_cat_props(schema_fields: list, filled: dict[str, Any]) -> list[str]:
    """Names of required icbuCatProp attributes not present in ``filled`` — from the
    schema's own requiredRule. Useful to warn before a real (non-draft) publish."""
    icbu = next((f for f in schema_fields if f.id == "icbuCatProp"), None)
    if not icbu:
        return []
    have = set((filled.get("icbuCatProp") or {}).keys())
    return [c.name or c.id for c in icbu.children if c.required and c.id not in have]


# ── sales properties (saleProp: Color / Size … the SKU-defining attributes) ────
#
# saleProp is a complex field whose children (e.g. ``p-191288010`` "color") are
# multiCheck with a ``valueAttributeRule inputValue``: each value is serialized as
# ``<value inputValue="Orange">3558409</value>``. Values outside the option list are
# custom: any unique NEGATIVE number as the value, the text as inputValue.


def _build_sale_props(sale_field: Any, sale_props: dict[str, Any]) -> dict[str, Any] | None:
    """Build ``saleProp`` values from ``{name-or-id: value | [values]}``."""
    if not sale_field or not sale_props:
        return None
    provided = {str(k).strip().lower(): v for k, v in sale_props.items()}
    out: dict[str, Any] = {}
    for sf in sale_field.children:
        val = next((provided[k] for k in _cat_prop_keys(sf) if k in provided), None)
        if val is None:
            continue
        items = val if isinstance(val, (list, tuple)) else [val]
        options = {str(o.get("displayName", "")).strip().lower(): str(o.get("value")) for o in sf.options}
        taken = {str(o.get("value")) for o in sf.options}   # e.g. the "other" option is often -1
        values: list[dict[str, Any]] = []
        custom = 0
        for item in items:
            text = str(item).strip()
            code = options.get(text.lower())
            if code is not None:
                # enumerated option: send its code with the option's own display casing
                display = next(str(o["displayName"]) for o in sf.options if str(o.get("value")) == code)
            else:
                # custom value: a unique negative id that no option uses; the seller's text is the name
                custom -= 1
                while str(custom) in taken:
                    custom -= 1
                code, display = str(custom), text
            values.append({"__value__": code, "__attrs__": {"inputValue": display}})
        out[sf.id] = values
    return out or None


def build_skus(sale_field: Any, sale_props: dict[str, Any], code_prefix: str) -> list[dict] | None:
    """Build the ``sku`` multiComplex — one row per sale-property value.

    Each row carries the seller ``skuOuterId`` (the "Commodity code", built as
    ``f"{code_prefix}-{value}"``) and the ``props`` that link it to the sale
    property value. ``props`` mirrors what the platform stores:
    ``<value propId propName propValueId propValueName>{propId}:{propValueId}</value>``
    where ``propId`` is the sale-property field id without its ``p-`` prefix and
    ``propValueId`` is the option code (a unique negative id for a custom value,
    matching :func:`_build_sale_props`). One sale property (e.g. Color) for now.
    """
    if not sale_field or not sale_props or not code_prefix:
        return None
    provided = {str(k).strip().lower(): v for k, v in sale_props.items()}
    for sf in sale_field.children:
        val = next((provided[k] for k in _cat_prop_keys(sf) if k in provided), None)
        if val is None:
            continue
        items = val if isinstance(val, (list, tuple)) else [val]
        options = {str(o.get("displayName", "")).strip().lower(): str(o.get("value")) for o in sf.options}
        taken = {str(o.get("value")) for o in sf.options}
        prop_name = sf.id                                   # e.g. "p-191288010"
        prop_id = prop_name[2:] if prop_name.startswith("p-") else prop_name
        rows: list[dict] = []
        custom = 0
        for item in items:
            text = str(item).strip()
            value_id = options.get(text.lower())
            if value_id is not None:
                display = next(str(o["displayName"]) for o in sf.options if str(o.get("value")) == value_id)
            else:
                custom -= 1
                while str(custom) in taken:
                    custom -= 1
                value_id, display = str(custom), text
            rows.append({
                "skuOuterId": clip_text(f"{code_prefix}-{display}", 64),
                "props": [{
                    "__value__": f"{prop_id}:{value_id}",
                    "__attrs__": {"propId": prop_id, "propName": prop_name,
                                  "propValueId": value_id, "propValueName": display},
                }],
            })
        return rows or None
    return None


def missing_required_sale_props(schema_fields: list, filled: dict[str, Any]) -> list[str]:
    """Names of required saleProp children (e.g. Color) not present in ``filled``."""
    sale = next((f for f in schema_fields if f.id == "saleProp"), None)
    if not sale:
        return []
    have = set((filled.get("saleProp") or {}).keys())
    return [f"saleProp: {c.name or c.id}" for c in sale.children if c.required and c.id not in have]


def missing_required(schema_fields: list, filled: dict[str, Any]) -> list[str]:
    """All required-but-unfilled items a real publish would reject: category
    attributes, sales properties, and the shipping ladder (ladderPeriod)."""
    out = missing_required_cat_props(schema_fields, filled) + missing_required_sale_props(schema_fields, filled)
    if any(f.id == "ladderPeriod" for f in schema_fields) and not filled.get("ladderPeriod"):
        out.append("ladderPeriod (lead_time)")
    return out


# ── shipping ladder (ladderPeriod: "up to N units → M days") ──────────────────
LADDER_PERIOD_MAX = 3
DEFAULT_LEAD_TIME: list[list[int]] = [[1000, 15]]   # ≤ 1000 units → 15 days


def build_ladder_period(lead_time: list | None) -> dict[str, Any]:
    """``[[quantity, days], …]`` (max 3, sorted by quantity) → ``ladderPeriod`` value.

    Each tier reads "orders up to ``quantity`` units ship in ``days`` days"."""
    tiers = sorted(([int(q), int(d)] for q, d in (lead_time or DEFAULT_LEAD_TIME)), key=lambda t: t[0])
    return {
        f"ladderPeriod_{i}": {"quantity": q, "day": d} for i, (q, d) in enumerate(tiers[:LADDER_PERIOD_MAX])
    }


def _image_value(upload: dict[str, Any]) -> Any:
    """scImages value: the photo-bank URL as text WITH a required fileId attribute
    (``<value fileId="…">url</value>``). Falls back to a plain value if no file_id."""
    file_id = upload.get("file_id")
    url = upload.get("url")
    if file_id:
        return {"__value__": url or "", "__attrs__": {"fileId": file_id}}
    return url or file_id
