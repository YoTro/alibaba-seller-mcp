"""brief flow helpers: image count/quality checks and fact+AI attribute resolution."""

from alibaba_seller_mcp.alibaba.schema import parse_item_schema
from alibaba_seller_mcp.files.readers import image_quality_issues, read_image
from alibaba_seller_mcp.listing import publishing as bf

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


def test_resolve_attributes_manual_mode_skips_ai():
    icbu = _icbu()
    attrs, ai_names = bf._resolve_attributes(icbu, {"place_of_origin": "China"}, "a salt gun", None)
    assert attrs == {"place of origin": "China"} and ai_names == []


class _StubProducts:
    def __init__(self, schema_xml=ICBU):
        self.published = None
        self.published_with_schema = None
        self.schema_xml = schema_xml
        self.schema_fetches = 0

    def get_schema_fields(self, cat_id, token, *, language="en_US", publish_type="default"):
        self.schema_fetches += 1
        return parse_item_schema(self.schema_xml)

    def publish_product(self, manifest, token, *, draft, schema_fields=None):
        self.published = manifest.data
        self.published_with_schema = schema_fields
        return {"product_id": "1", "biz_success": True, "missing_required": [], "response": {}}


class _NoAI:
    """Any call means the brief flow ignored ai=false."""

    def generate(self, **kw):
        raise AssertionError("AI content generation must not run with ai=false")

    def select_attributes(self, *a, **kw):
        raise AssertionError("AI attribute selection must not run with ai=false")


def test_publish_from_brief_manual_mode_uses_content_overrides():
    products = _StubProducts()
    brief = {
        "brief": "salt gun", "category_id": 1, "ai": False,
        "content": {"title": "My Exact Title", "highlights": "H", "faqs": [{"question": "q", "answer": "a"}]},
        "images": {"main": [{"file_id": str(i), "url": "u"} for i in range(4)], "detail": ["https://x/1.jpg"]},
        "price": {"unit": "20", "moq": 1, "tiers": [[1, 9.9]]},
        "facts": {"place_of_origin": "China", "material": "ABS"},
    }
    r = bf.PublishFromBrief(products, detail=_NoAI()).run(brief, token="t", draft=True)
    content = products.published["content"]
    assert content["title"] == "My Exact Title" and content["faqs"][0]["question"] == "q"
    assert r.ai_filled == ["manual content: faqs, highlights, title"]
    assert products.published["product_attributes"]["Material"] == "ABS"
    assert "Feature" not in products.published["product_attributes"]   # left for the seller, not guessed


ICBU_WITH_SALE = ICBU.replace(
    "</field></itemSchema>",
    '</field><field id="saleProp" name="Sales Property" type="complex"><fields>'
    '<field id="p-191288010" name="color" type="multiCheck"><rules><rule name="requiredRule" value="true"/></rules>'
    '<options><option displayName="Orange" value="3558409"/></options></field></fields></field>'
    '<field id="ladderPeriod" type="complex"><fields><field id="ladderPeriod_0" type="complex"><fields>'
    '<field id="quantity" type="input"/><field id="day" type="input"/></fields></field></fields></field>'
    "</itemSchema>",
)


def test_publish_from_brief_fills_sale_props_from_facts_and_defaults_lead_time():
    products = _StubProducts(ICBU_WITH_SALE)
    brief = {
        "brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"},
        "images": {"main": [{"file_id": "1", "url": "u"}] * 4},
        "price": {"tiers": [[1, 1]]},
        "facts": {"place_of_origin": "China", "material": "ABS", "Color": "Orange"},
    }
    r = bf.PublishFromBrief(products, detail=_NoAI()).run(brief, token="t", draft=True)
    data = products.published
    assert data["sale_props"] == {"Color": "Orange"}                 # Color moved out of facts into saleProp
    assert "Color" not in data["product_attributes"]
    assert data["fields"]["ladderPeriod"] == {"ladderPeriod_0": {"quantity": 1000, "day": 15}}
    assert any("lead_time not set" in w for w in r.warnings)
    assert not any("required sales properties" in w for w in r.warnings)


def test_publish_from_brief_explicit_lead_time_and_missing_color_warns():
    products = _StubProducts(ICBU_WITH_SALE)
    brief = {"brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"}, "lead_time": [[50, 5], [500, 12]],
             "images": {"main": [{"file_id": "1", "url": "u"}] * 4}, "price": {"tiers": [[1, 1]]}}
    r = bf.PublishFromBrief(products, detail=_NoAI()).run(brief, token="t", draft=True)
    assert products.published["fields"]["ladderPeriod"] == {
        "ladderPeriod_0": {"quantity": 50, "day": 5}, "ladderPeriod_1": {"quantity": 500, "day": 12}}
    assert any("required sales properties not set" in w and "color" in w for w in r.warnings)


def test_publish_from_brief_manual_mode_without_title_warns():
    products = _StubProducts()
    brief = {"brief": "x", "category_id": 1, "ai": False, "price": {"tiers": [[1, 1]]},
             "images": {"main": [{"file_id": "1", "url": "u"}] * 4}}
    r = bf.PublishFromBrief(products, detail=_NoAI()).run(brief, token="t", draft=True)
    assert any("no content.title" in w for w in r.warnings)


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
