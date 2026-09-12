"""The AI structured content -> publish-field mapping.

`FieldAssembler` needs no API client: the network work it does is image uploads,
and the uploader is injected, so these tests stub the photo bank and nothing else.
"""

from pathlib import Path

from alibaba_seller_mcp.alibaba.fields import FieldAssembler
from alibaba_seller_mcp.alibaba.manifest import ProductManifest
from alibaba_seller_mcp.config import Config


class _StubPhotoBank:
    """Hands back a canned photo-bank ref for any upload."""

    def __init__(self):
        self.uploaded: list[str] = []

    def upload(self, local_path, access_token, *, group_id=None):
        self.uploaded.append(str(local_path))
        n = len(self.uploaded)
        return {"file_id": f"fid-{n}", "url": f"https://img.example/{n}.jpg", "cached": False}


def _assembler(tmp_path):
    return FieldAssembler(Config(app_key="k", app_secret="s", state_dir=tmp_path), _StubPhotoBank())


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
    fields = _assembler(tmp_path).assemble(manifest, "tok")

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


def test_custom_attribute_values_clipped_to_platform_limits(tmp_path):
    from alibaba_seller_mcp.alibaba.values import MAX_ATTR_NAME_LEN, MAX_ATTR_VALUE_LEN, clip_text

    long_value = "Fires ordinary dry table salt at about 0.04 g per shot with kinetic energy above 0.08 J at 20-30 cm"
    content = {"title": "T", "attributes": [{"name": "A very long attribute name that runs on", "value": long_value},
                                             {"name": "Short", "value": "ok"}]}
    manifest = ProductManifest({"category_id": 1, "content": content}, tmp_path)
    fields = _assembler(tmp_path).assemble(manifest, "tok")
    a0 = fields["customMoreProperty"]["customMoreProperty_0"]
    v = a0["valueName"]
    assert len(v) <= MAX_ATTR_VALUE_LEN
    assert long_value.startswith(v) and long_value[len(v)] == " "   # cut on a word boundary, no ellipsis
    assert len(a0["propName"]) <= MAX_ATTR_NAME_LEN
    assert fields["customMoreProperty"]["customMoreProperty_1"] == {"propName": "Short", "valueName": "ok"}
    assert clip_text("x" * 100, 70) == "x" * 70                     # no space → hard cut
    assert clip_text("  spaced   out  ", 70) == "spaced out"


def test_highlights_intro_and_faqs_clipped_to_platform_limits(tmp_path):
    from alibaba_seller_mcp.alibaba.values import (
        MAX_COMPANY_DESC_LEN, MAX_FAQ_ANSWER_LEN, MAX_FAQ_COUNT, MAX_FAQ_QUESTION_LEN,
        MAX_HIGHLIGHTS_LEN,
    )

    content = {
        "title": "T",
        "highlights": "word " * 600,                    # 3000 chars
        "company_intro": "para one.\n\n" + "word " * 600,
        "faqs": [{"question": f"Q{i} " + "q" * 200, "answer": "word " * 200} for i in range(12)],
    }
    manifest = ProductManifest({"category_id": 1, "content": content}, tmp_path)
    fields = _assembler(tmp_path).assemble(manifest, "tok")

    assert len(fields["textDesc"]) <= MAX_HIGHLIGHTS_LEN
    assert len(fields["companyDesc"]) <= MAX_COMPANY_DESC_LEN
    assert len(fields["companyFaqDesc"]) == MAX_FAQ_COUNT       # 12 offered, 8 kept
    for faq in fields["companyFaqDesc"]:
        assert len(faq["question"]) <= MAX_FAQ_QUESTION_LEN
        assert len(faq["answers"]) <= MAX_FAQ_ANSWER_LEN


def test_bodies_within_limits_keep_their_line_breaks(tmp_path):
    body = "First point\nSecond point\n\nThird point"
    content = {"title": "T", "highlights": body, "company_intro": body,
               "faqs": [{"question": "MOQ?", "answer": body}]}
    manifest = ProductManifest({"category_id": 1, "content": content}, tmp_path)
    fields = _assembler(tmp_path).assemble(manifest, "tok")
    assert fields["textDesc"] == body
    assert fields["companyDesc"] == body
    assert fields["companyFaqDesc"][0]["answers"] == body


def test_manifest_group_assignment(tmp_path):
    fb = _assembler(tmp_path)
    # explicit product_group dict
    m1 = ProductManifest({"category_id": 1, "product_group": {"first_group_id": "971189506"}}, tmp_path)
    assert fb.assemble(m1, "tok")["productGroup"] == {"first_group_id": "971189506"}
    # shortcut group_id -> first_group_id
    m2 = ProductManifest({"category_id": 1, "group_id": "908532704"}, tmp_path)
    assert fb.assemble(m2, "tok")["productGroup"] == {"first_group_id": "908532704"}
