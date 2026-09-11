"""The AI structured content -> publish-field mapping (no network; upload stubbed)."""

from pathlib import Path

from alibaba_seller_mcp.alibaba.products import ProductManifest, ProductService
from alibaba_seller_mcp.config import Config


class _StubClient:
    """Returns a canned photobank response for any upload call."""

    def __init__(self):
        self.calls = []

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.calls.append(method)
        return {
            "code": "0",
            "upload_image_response": {
                "file_id": f"fid-{len(self.calls)}",
                "photobank_url": f"https://img.example/{len(self.calls)}.jpg",
            },
        }


def _service(tmp_path):
    cfg = Config(app_key="k", app_secret="s", state_dir=tmp_path)
    return ProductService(cfg, _StubClient())


def _make_png(path: Path):
    from PIL import Image

    Image.new("RGB", (2, 2), (200, 100, 50)).save(path)


def test_content_maps_to_fields(tmp_path):
    # two real (tiny) detail images on disk so image_slot 0/1 resolve
    _make_png(tmp_path / "d0.jpg")
    _make_png(tmp_path / "d1.jpg")
    content = {
        "title": "My Product",
        "highlights": "Great highlights",
        "detail_version": "premium",
        "modules": [
            {"type": "text", "text": "Intro text"},
            {"type": "image", "gallery": "300", "image_slot": 0, "caption": "Close-up"},
            {"type": "image", "gallery": "200", "image_slot": 1, "caption": "Scene"},
        ],
        "attributes": [{"name": "Material", "value": "ABS"}, {"name": "Power", "value": "USB"}],
        "company_intro": "We are a leading manufacturer.",
        "faqs": [{"question": "MOQ?", "answer": "10 units"}],
    }
    manifest = ProductManifest(
        {
            "category_id": 201335115,
            "content": content,
            "detail_images": ["d0.jpg", "d1.jpg"],
            "fields": {"scPrice": "1"},
        },
        tmp_path,
    )
    svc = _service(tmp_path)
    fields = svc._assemble_fields(manifest, "tok", None)

    assert fields["productTitle"] == "My Product"
    assert fields["textDesc"] == "Great highlights"
    assert fields["productDescType"] == "4"  # 智能编辑 (AI structured detail)
    # attributes -> customMoreProperty pairs
    assert fields["customMoreProperty"]["customMoreProperty_0"] == {"propName": "Material", "valueName": "ABS"}
    # detailImage grouped by gallery; the leading text folds into the 300 caption
    di = {entry["gallery"]: entry for entry in fields["detailImage"]}
    assert "300" in di and "200" in di
    assert di["300"]["images"][0]["generalText"].startswith("Intro text")
    # gallery 200 does not carry text
    assert "generalText" not in di["200"]["images"][0]
    # company intro + FAQs map to their dedicated fields
    assert fields["companyDesc"] == "We are a leading manufacturer."
    assert fields["companyFaqDesc"] == [{"question": "MOQ?", "answers": "10 units"}]


def test_manifest_group_assignment(tmp_path):
    svc = _service(tmp_path)
    # explicit product_group dict
    m1 = ProductManifest({"category_id": 1, "product_group": {"first_group_id": "971189506"}}, tmp_path)
    assert svc._assemble_fields(m1, "tok", None)["productGroup"] == {"first_group_id": "971189506"}
    # shortcut group_id -> first_group_id
    m2 = ProductManifest({"category_id": 1, "group_id": "908532704"}, tmp_path)
    assert svc._assemble_fields(m2, "tok", None)["productGroup"] == {"first_group_id": "908532704"}
