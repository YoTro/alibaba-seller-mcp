"""publish / draft / update share one submit path.

They used to be two near-identical bodies, which is how they drift: the same
fields must be assembled, the same outcome reported, and only the method name
and `productId` may differ.
"""


from alibaba_seller_mcp.alibaba.manifest import ProductManifest
from alibaba_seller_mcp.alibaba.products import ProductService
from alibaba_seller_mcp.config import Config

SCHEMA = """
<itemSchema>
  <field id="productTitle" type="input"><rules><rule name="requiredRule" value="true"/></rules></field>
  <field id="scPrice" type="singleCheck"/>
</itemSchema>
"""


class _RecordingClient:
    """Captures every call so the request shape can be asserted."""

    def __init__(self):
        self.calls = []

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.calls.append((method, params))
        if "schema.get" in method:
            return {"code": "0", "data": SCHEMA}
        return {"code": "0", "product_id": 777, "biz_success": True}


def _svc(tmp_path):
    cfg = Config(app_key="k", app_secret="s", state_dir=tmp_path)
    return ProductService(cfg, _RecordingClient()), cfg


def _manifest(tmp_path):
    return ProductManifest({"category_id": 1, "fields": {"productTitle": "T", "scPrice": "1"}}, tmp_path)


def _request(client):
    import json
    return json.loads(client.calls[-1][1]["param_product_top_publish_request"])


def test_publish_draft_and_update_differ_only_where_they_must(tmp_path):
    svc, cfg = _svc(tmp_path)
    m = _manifest(tmp_path)

    svc.publish_product(m, "tok")
    live_method, live_req = svc.client.calls[-1][0], _request(svc.client)

    svc.publish_product(m, "tok", draft=True)
    draft_method, draft_req = svc.client.calls[-1][0], _request(svc.client)

    svc.update_product("555", m, "tok")
    upd_method, upd_req = svc.client.calls[-1][0], _request(svc.client)

    assert live_method == cfg.method_schema_add
    assert draft_method == cfg.method_schema_add_draft
    assert upd_method == cfg.method_schema_update

    # identical documents: the same xml, category, language and version
    assert live_req == draft_req
    assert {k: v for k, v in upd_req.items() if k != "productId"} == live_req
    assert upd_req["productId"] == "555"


def test_every_path_reports_the_same_outcome_shape(tmp_path):
    svc, _ = _svc(tmp_path)
    m = _manifest(tmp_path)
    results = [svc.publish_product(m, "tok"),
               svc.publish_product(m, "tok", draft=True),
               svc.update_product("555", m, "tok")]
    for r in results:
        assert set(r) == {"filled_fields", "product_id", "biz_success", "missing_required", "response"}
        assert r["product_id"] == "777"
        assert r["biz_success"] is True
        assert r["filled_fields"] == ["productTitle", "scPrice"]


def test_update_falls_back_to_the_given_id_when_the_api_omits_it(tmp_path):
    svc, _ = _svc(tmp_path)

    def no_id(method, params=None, **kw):
        svc.client.calls.append((method, params))
        return {"code": "0", "data": SCHEMA} if "schema.get" in method else {"code": "0", "biz_success": True}

    svc.client.call = no_id
    assert svc.update_product("555", _manifest(tmp_path), "tok")["product_id"] == "555"
