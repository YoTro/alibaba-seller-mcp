"""brief flow helpers: image count/quality checks and fact+AI attribute resolution."""

from alibaba_seller_mcp import brief as bf
from alibaba_seller_mcp.alibaba.schema import parse_item_schema
from alibaba_seller_mcp.files.readers import image_quality_issues, read_image

ICBU = """
<itemSchema><field id="icbuCatProp" type="complex"><fields>
  <field id="p-1" name="place of origin" type="singleCheck"><rules><rule name="requiredRule" value="true"/></rules>
    <options><option displayName="China" value="100"/></options></field>
  <field id="p-9" name="Feature" type="multiCheck"><rules><rule name="requiredRule" value="true"/></rules>
    <options><option displayName="Durable" value="7"/><option displayName="Eco-Friendly" value="8"/></options></field>
  <field id="p-5" name="Material" type="singleCheck"><rules><rule name="requiredRule" value="true"/></rules>
    <options><option displayName="ABS" value="3"/></options></field>
</fields></field></itemSchema>
"""


class _StubDetail:
    def __init__(self, chosen):
        self.chosen = chosen
        self.asked = None

    def select_attributes(self, brief_text, attrs, **kw):
        self.asked = [a["name"] for a in attrs]
        return self.chosen


def _icbu():
    return next(f for f in parse_item_schema(ICBU) if f.id == "icbuCatProp")


def test_main_image_count_too_few_hints():
    refs, need_more, warns = bf._prep_main_images(
        [{"file_id": "1", "url": "u"}, {"file_id": "2", "url": "u"}], products=None, token="t"
    )
    assert need_more is True
    assert any("4–6" in w for w in warns)


def test_main_image_count_capped_at_six():
    entries = [{"file_id": str(i), "url": "u"} for i in range(8)]
    refs, need_more, warns = bf._prep_main_images(entries, products=None, token="t")
    assert len(refs) == 6 and need_more is False
    assert any("first 6" in w for w in warns)


def test_resolve_attributes_facts_plus_ai():
    icbu = _icbu()
    detail = _StubDetail({"Feature": ["Eco-Friendly"]})
    attrs, ai_names = bf._resolve_attributes(
        icbu, {"place_of_origin": "China", "material": "ABS"}, "a salt gun", detail
    )
    # facts cover origin + material; AI is asked only for the uncovered required 'Feature'
    assert detail.asked == ["Feature"]
    assert attrs["place of origin"] == "China"
    assert attrs["Material"] == "ABS"
    assert attrs["Feature"] == ["Eco-Friendly"]
    assert ai_names == ["Feature"]


def test_detail_images_capped_at_30():
    urls = [f"https://img/{i}.jpg" for i in range(35)]
    out, warns = bf._prep_detail_images(urls, products=None, token="t")
    assert len(out) == 30
    assert any("30" in w for w in warns)


def test_detail_max_is_30():
    assert bf.DETAIL_MAX == 30


def test_image_quality_issues_main(tmp_path):
    from PIL import Image

    p = tmp_path / "small.jpg"
    Image.new("RGB", (300, 500), (200, 100, 50)).save(p)  # < 640 and ratio 3:5
    issues = image_quality_issues(read_image(str(p), load_bytes=False), kind="main")
    assert any("640" in i for i in issues)
    assert any("aspect ratio" in i for i in issues)


def test_image_quality_issues_detail_allows_long_flags_narrow(tmp_path):
    from PIL import Image

    # a tall long detail image: width < 1200 should warn; the long height must NOT
    # trigger an aspect-ratio warning (long detail images are allowed).
    p = tmp_path / "long.jpg"
    Image.new("RGB", (900, 9000), (50, 120, 200)).save(p)
    issues = image_quality_issues(read_image(str(p), load_bytes=False), kind="detail")
    assert any("1200" in i for i in issues)
    assert not any("aspect ratio" in i for i in issues)

    # a wide-enough detail image passes width; long height still fine
    p2 = tmp_path / "wide.jpg"
    Image.new("RGB", (1400, 6000), (50, 120, 200)).save(p2)
    assert image_quality_issues(read_image(str(p2), load_bytes=False), kind="detail") == []
