import pytest

from alibaba_seller_mcp.alibaba.videos import VideoService
from alibaba_seller_mcp.config import Config


class _StubClient:
    def __init__(self, response):
        self.response = response
        self.last = None

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.last = (method, params)
        return self.response


def _svc(response):
    return VideoService(Config(app_key="k", app_secret="s"), _StubClient(response))


def test_relate_success():
    svc = _svc({"code": "0", "model": "true", "msg_code": "00000", "msg_info": "success"})
    out = svc.relate_to_product("vid1", "pid1", "tok", target="main")
    assert out["success"] is True
    assert svc.client.last[0] == "alibaba.icbu.video.relation.product.main"
    assert svc.client.last[1] == {"video_id": "vid1", "product_id": "pid1"}


def test_relate_detail_uses_detail_method():
    svc = _svc({"code": "0", "model": False})
    out = svc.relate_to_product("v", "p", "tok", target="detail")
    assert out["success"] is False
    assert svc.client.last[0] == "alibaba.icbu.video.relation.product.detail"


def test_relate_bad_target():
    with pytest.raises(ValueError):
        _svc({}).relate_to_product("v", "p", "tok", target="thumbnail")


def test_list_related_products():
    svc = _svc({"code": "0", "result": {"model": [{"product_id": "p1"}, {"product_id": "p2"}], "msg_code": "0"}})
    out = svc.list_related_products("tok", video_id="v1", type="videoId")
    assert out["product_ids"] == ["p1", "p2"]
    assert svc.client.last[1] == {"type": "videoId", "video_id": "v1"}


def test_list_bad_type():
    with pytest.raises(ValueError):
        _svc({}).list_related_products("tok", type="bogus")


class _SeqClient:
    """Returns queued responses in order and records calls."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.calls.append((method, params))
        return self.responses.pop(0)


def test_relate_auto_encrypts_numeric_ids():
    svc = VideoService(Config(app_key="k", app_secret="s"), _SeqClient([
        {"code": "0", "secret_id": "ENC_PID"},                              # product.id.encrypt
        {"code": "0", "result": {"model": {"list": [{"id": "6000343176038", "video_id": "ENC_VID"}]}}},  # video.query
        {"code": "0", "model": "true"},                                     # relate.main
    ]))
    out = svc.relate_to_product("6000343176038", "1601944558882", "tok", target="main")
    assert out["success"] is True
    assert out["product_id"] == "ENC_PID"
    assert out["video_id"] == "ENC_VID"
    # the relation call used the encrypted ids
    relate_method, relate_params = svc.client.calls[-1]
    assert relate_method == "alibaba.icbu.video.relation.product.main"
    assert relate_params == {"video_id": "ENC_VID", "product_id": "ENC_PID"}


def test_encrypted_ids_pass_through_unchanged():
    # non-numeric ids are not re-encrypted (only the relate call happens)
    svc = VideoService(Config(app_key="k", app_secret="s"), _SeqClient([{"code": "0", "model": True}]))
    out = svc.relate_to_product("pbUJxEnc", "orMJxEnc", "tok", target="detail")
    assert out["success"] is True
    assert len(svc.client.calls) == 1  # no encrypt/query calls
