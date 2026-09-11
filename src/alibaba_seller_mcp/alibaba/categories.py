"""Category system attributes (商品属性).

`alibaba.icbu.category.attribute.get` returns the system-defined attributes for a
publish category — including which are **required** — so the caller can fill the
schema's ``icbuCatProp`` field (each attribute maps to a schema field id
``p-<attr_id>``). This is a public API (no access token needed).

An attribute with ``car_model=true`` is a vehicle-fitment attribute whose next
level comes from the hierarchical-attribute API.
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from .client import AlibabaClient

_BOOL_FIELDS = ("required", "sku_attribute", "customize_image", "customize_value", "car_model")


def _to_bool(v: Any) -> Any:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return v


class CategoryService:
    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client

    def get_attributes(self, cat_id: str | int, access_token: str | None = None) -> dict[str, Any]:
        """Return the category's attributes with string booleans normalised.

        ``required`` lists the en_names of the top-level required attributes
        (``parent_attr_id == -1``) — the ones a publish must fill in ``icbuCatProp``.
        """
        body = self.client.call(
            self.config.method_category_attribute_get,
            {"cat_id": str(cat_id)},
            access_token=access_token,
        )
        attrs = [self._normalize(a) for a in (body.get("attributes") or [])]
        required = [
            a.get("en_name")
            for a in attrs
            if a.get("required") and str(a.get("parent_attr_id")) in ("-1", "None")
        ]
        return {
            "cat_id": str(cat_id),
            "count": len(attrs),
            "required": [r for r in required if r],
            "attributes": attrs,
        }

    @staticmethod
    def _normalize(attr: dict[str, Any]) -> dict[str, Any]:
        out = dict(attr)
        for f in _BOOL_FIELDS:
            if f in out:
                out[f] = _to_bool(out[f])
        values = []
        for v in attr.get("attribute_values") or []:
            vv = dict(v)
            if "sku_value" in vv:
                vv["sku_value"] = _to_bool(vv["sku_value"])
            values.append(vv)
        out["attribute_values"] = values
        return out
