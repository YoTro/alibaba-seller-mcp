"""Product groups (卖家展示分组).

A product group is a public collection of related products; a product can belong
to only one group at a time. Groups form a tree (up to three levels, mirrored by
the publish schema's ``productGroup`` field: first/second/third_group_id).

Documented:
  alibaba.icbu.product.group.get  params: group_id (required; -1 = all top-level),
    extra_context (opt)  ->  {product_group: {group_id, group_name, parent_id,
    parent_id2, children_id_list, children_group:[{group_id, group_name}]}}
"""

from __future__ import annotations

from typing import Any

from ..config import Config
from .client import AlibabaClient


class GroupService:
    def __init__(self, config: Config, client: AlibabaClient):
        self.config = config
        self.client = client

    def get_group(self, access_token: str, group_id: str | int = -1) -> dict[str, Any]:
        """Get one group's info (its name, parents, and child groups). Pass
        ``group_id=-1`` to list all top-level groups (as ``children``)."""
        body = self.client.call(
            self.config.method_product_group_get,
            {"group_id": str(group_id), "extra_context": "{}"},
            access_token=access_token,
        )
        pg = body.get("product_group") or {}
        children = [
            {"group_id": c.get("group_id"), "group_name": c.get("group_name")}
            for c in (pg.get("children_group") or [])
        ]
        return {
            "group_id": pg.get("group_id"),
            "group_name": pg.get("group_name"),
            "parent_id": pg.get("parent_id"),
            "parent_id2": pg.get("parent_id2"),
            "children_id_list": pg.get("children_id_list"),
            "children": children,
            "raw": body,
        }

    def list_top_groups(self, access_token: str) -> list[dict[str, Any]]:
        """Convenience: the top-level groups (``get_group(-1).children``)."""
        return self.get_group(access_token, group_id=-1)["children"]
