from alibaba_seller_mcp.alibaba.categories import CategoryService
from alibaba_seller_mcp.config import Config


class _StubClient:
    def __init__(self, body):
        self.body = body
        self.last = None

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.last = (method, params, access_token)
        return self.body


def test_get_attributes_normalizes_and_flags_required():
    body = {
        "code": "0",
        "attributes": [
            {"attr_id": "1", "en_name": "Place of Origin", "required": "true",
             "parent_attr_id": "-1", "car_model": "false", "input_type": "single_select",
             "attribute_values": [{"attr_value_id": "9", "en_name": "China", "sku_value": "false"}]},
            {"attr_id": "2", "en_name": "Brand", "required": "false", "parent_attr_id": "-1"},
            {"attr_id": "50", "en_name": "Sub", "required": "true", "parent_attr_id": "1"},
        ],
    }
    svc = CategoryService(Config(app_key="k", app_secret="s"), _StubClient(body))
    r = svc.get_attributes("201335115")

    assert r["count"] == 3
    # string booleans -> real booleans
    assert r["attributes"][0]["required"] is True
    assert r["attributes"][0]["car_model"] is False
    assert r["attributes"][0]["attribute_values"][0]["sku_value"] is False
    # only top-level (parent_attr_id == -1) required attrs are surfaced
    assert r["required"] == ["Place of Origin"]
    # public API — no token passed
    assert svc.client.last[2] is None
    assert svc.client.last[1] == {"cat_id": "201335115"}
