"""Parse the ICBU product-publish schema (an ``<itemSchema>`` XML form).

`alibaba.icbu.product.schema.get` returns a category-specific XML form describing
every publishable field: its id, label, type, validation rules, and (for
check-type fields) the allowed options. This module turns that XML into plain
Python structures so callers — a human, or Claude — can see what a category
requires, and so the publish layer can build a value document field-by-field.

Field types seen in the wild: input, multiInput, singleCheck, multiCheck,
complex (fixed nested fields), multiComplex (repeated nested groups), label
(read-only info). Required fields carry ``<rule name="requiredRule" value="true"/>``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any


@dataclass
class SchemaField:
    id: str
    name: str
    type: str
    required: bool = False
    value_type: str | None = None          # long | double | url | text | ...
    max_length: int | None = None
    options: list[dict[str, str]] = dc_field(default_factory=list)  # {value, displayName}
    rules: dict[str, str] = dc_field(default_factory=dict)
    children: list[SchemaField] = dc_field(default_factory=list)

    def to_dict(self, *, prune: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.name:
            out["name"] = self.name
        if self.required:
            out["required"] = True
        if self.value_type:
            out["value_type"] = self.value_type
        if self.max_length is not None:
            out["max_length"] = self.max_length
        if self.options:
            out["options"] = self.options
        if self.children:
            out["children"] = [c.to_dict(prune=prune) for c in self.children]
        return out


def _parse_field(el: ET.Element) -> SchemaField:
    rules: dict[str, str] = {}
    required = False
    value_type = None
    max_length = None
    for rule in el.findall("./rules/rule"):
        rname = rule.get("name", "")
        rval = rule.get("value", "")
        # keep the last value for repeated rule names (e.g. tipRule); enough for a summary
        rules[rname] = rval
        if rname == "requiredRule" and rval == "true":
            required = True
        elif rname == "valueTypeRule":
            value_type = rval
        elif rname == "maxLengthRule":
            try:
                max_length = int(rval)
            except ValueError:
                pass

    options = [
        {"value": o.get("value", ""), "displayName": o.get("displayName", "")}
        for o in el.findall("./options/option")
    ]

    children = [_parse_field(c) for c in el.findall("./fields/field")]

    return SchemaField(
        id=el.get("id", ""),
        name=el.get("name", ""),
        type=el.get("type", ""),
        required=required,
        value_type=value_type,
        max_length=max_length,
        options=options,
        rules=rules,
        children=children,
    )


def parse_item_schema(xml_text: str) -> list[SchemaField]:
    """Parse an ``<itemSchema>`` document into top-level :class:`SchemaField`\\ s."""
    root = ET.fromstring(xml_text)
    return [_parse_field(f) for f in root.findall("./field")]


def required_field_ids(fields: list[SchemaField]) -> list[str]:
    return [f.id for f in fields if f.required]


# ── building the filled value document for schema.add / schema.update ─────────
#
# The `xml` submitted to schema.add mirrors the itemSchema, with values attached:
#   input / singleCheck   -> <field id type><value>X</value></field>
#   multiCheck / multiInput -> <field ...><values><value>A</value>...</values></field>
#   complex               -> <field ...><complex-value> nested <field>… </complex-value></field>
#   multiComplex          -> <field ...> repeated <complex-value>…</complex-value> </field>
#
# The caller supplies a plain dict keyed by field id. Value shapes:
#   scalar (input/singleCheck):      "text" or 123
#   attributed scalar:               {"__value__": "text", "__attrs__": {"fileId": "…"}}
#                                      -> <value fileId="…">text</value>
#                                      (image fields require a fileId attribute)
#   list  (multiCheck/multiInput):   ["A", "B"]
#   dict  (complex):                 {"childId": <value>, ...}
#   list-of-dicts (multiComplex):    [{"childId": <value>}, {...}]

_ATTR_VALUE_KEYS = ("__value__", "__attrs__")


def _is_attr_value(v: Any) -> bool:
    return isinstance(v, dict) and any(k in v for k in _ATTR_VALUE_KEYS)


def _set_value_el(parent: ET.Element, value: Any) -> None:
    """Append a <value> (optionally with attributes) to ``parent``."""
    ve = ET.SubElement(parent, "value")
    if _is_attr_value(value):
        for ak, av in (value.get("__attrs__") or {}).items():
            ve.set(ak, "" if av is None else str(av))
        text = value.get("__value__")
        ve.text = "" if text is None else str(text)
    else:
        ve.text = "" if value is None else str(value)

_MULTI_VALUE_TYPES = {"multiCheck", "multiInput"}
_COMPLEX_TYPES = {"complex"}
_MULTI_COMPLEX_TYPES = {"multiComplex"}


def _field_type(sf: SchemaField | None, fallback: str = "input") -> str:
    return (sf.type if sf and sf.type else fallback)


def _build_field_el(sf: SchemaField | None, fid: str, value: Any) -> ET.Element:
    ftype = _field_type(sf)
    el = ET.Element("field", {"id": fid, "type": ftype})
    child_by_id = {c.id: c for c in (sf.children if sf else [])}

    if ftype in _COMPLEX_TYPES:
        # complex -> a single <complex-value>
        cv = ET.SubElement(el, "complex-value")
        for cid, cval in (value or {}).items():
            cv.append(_build_field_el(child_by_id.get(cid), cid, cval))
    elif ftype in _MULTI_COMPLEX_TYPES:
        # multiComplex -> one <complex-values> (PLURAL) per item
        for item in value or []:
            cv = ET.SubElement(el, "complex-values")
            for cid, cval in item.items():
                cv.append(_build_field_el(child_by_id.get(cid), cid, cval))
    elif ftype in _MULTI_VALUE_TYPES:
        vals = ET.SubElement(el, "values")
        for v in value if isinstance(value, (list, tuple)) else [value]:
            _set_value_el(vals, v)
    else:  # input, singleCheck, or anything scalar
        _set_value_el(el, value)
    return el


def build_value_xml(schema_fields: list[SchemaField], values: dict[str, Any]) -> str:
    """Build the filled ``<itemSchema>`` XML for schema.add / schema.update.

    ``schema_fields`` is the parsed schema (drives per-field type/nesting);
    ``values`` maps field ids to values (shapes documented above). Unknown ids
    are emitted as plain input fields.
    """
    by_id = {f.id: f for f in schema_fields}
    root = ET.Element("itemSchema")
    for fid, value in values.items():
        root.append(_build_field_el(by_id.get(fid), fid, value))
    return ET.tostring(root, encoding="unicode")


def describe_schema(xml_text: str, *, include_all: bool = False) -> dict[str, Any]:
    """A compact, JSON-serialisable summary of a publish schema.

    By default returns only fillable (non-label) fields; set ``include_all`` for
    everything. Read-only ``label`` info fields are always dropped.
    """
    fields = parse_item_schema(xml_text)
    fillable = [f for f in fields if f.type != "label"]
    shown = fillable if include_all else [f for f in fillable if f.type != "label"]
    return {
        "field_count": len(fields),
        "required": required_field_ids(fields),
        "fields": [f.to_dict() for f in shown],
    }
