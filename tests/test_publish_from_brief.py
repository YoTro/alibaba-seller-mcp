"""The PublishFromBrief application service — in particular the media step, which
used to live in the MCP tool body where nothing could test it.
"""

import json

from PIL import Image

from alibaba_seller_mcp.alibaba.schema import parse_item_schema
from alibaba_seller_mcp.listing import PublishFromBrief

SCHEMA = """
<itemSchema><field id="icbuCatProp" type="complex"><fields>
  <field id="p-1" name="place of origin" type="singleCheck">
    <options><option displayName="China" value="100"/></options></field>
</fields></field></itemSchema>
"""

SPEC = {"pages": [{"type": "spec_table", "rows": [{"name": "a", "value": "1"},
                                                  {"name": "b", "value": "2"},
                                                  {"name": "c", "value": "3"}]}]}


class _StubProducts:
    def __init__(self):
        self.published = None
        self.uploaded: list[str] = []
        self.schema_fetches = 0
        self.published_with_schema = None

    def get_schema_fields(self, cat_id, token, *, language="en_US", publish_type="default"):
        self.schema_fetches += 1
        return parse_item_schema(SCHEMA)

    def upload_image(self, path, token, *, group_id=None):
        self.uploaded.append(str(path))
        n = len(self.uploaded)
        return {"file_id": str(n), "url": f"https://img/{n}.jpg"}

    def publish_product(self, manifest, token, *, draft, schema_fields=None):
        self.published = manifest.data
        self.published_with_schema = schema_fields
        return {"product_id": "1", "biz_success": True, "missing_required": [], "response": {}}


def _photo(path, size=(900, 900)):
    Image.new("RGB", size, (240, 240, 240)).save(path)


def _folder(tmp_path, **extra):
    _photo(tmp_path / "hero.png")
    _photo(tmp_path / "scene.jpg", (1200, 900))
    brief = {
        "brief": "a lamp", "category_id": 1, "ai": False, "content": {"title": "T"},
        "price": {"tiers": [[1, 9.9]]},
        "photos": {"hero": "hero.png", "scenes": [{"path": "scene.jpg", "caption": "Kitchen"}]},
        "detail_spec": SPEC,
        **extra,
    }
    (tmp_path / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
    return brief


def test_photos_are_rendered_and_uploaded_when_images_are_empty(tmp_path):
    products = _StubProducts()
    brief = _folder(tmp_path)
    out = PublishFromBrief(products).run(brief, token="t", base_dir=tmp_path, draft=True)

    # both media notes are reported back to the caller
    assert any("main images prepared from photos" in n for n in out.ai_filled)
    assert any("detail images rendered from inline spec" in n for n in out.ai_filled)
    # the rendered files were uploaded and their refs reached the manifest
    assert (tmp_path / "images" / "main" / "01_hero.jpg").exists()
    assert (tmp_path / "images" / "detail" / "01_spec_table.jpg").exists()
    assert len(products.published["main_image_refs"]) == 2      # hero square + scene crop
    assert len(products.published["detail_image_refs"]) == 1


def test_media_is_skipped_when_the_brief_already_names_its_images(tmp_path):
    products = _StubProducts()
    brief = _folder(tmp_path, images={"main": [{"file_id": "9", "url": "u"}] * 4,
                                      "detail": ["https://img/d.jpg"]})
    out = PublishFromBrief(products).run(brief, token="t", base_dir=tmp_path, draft=True)

    assert not any("prepared from photos" in n for n in out.ai_filled)
    assert not (tmp_path / "images").exists()
    assert products.uploaded == []                              # nothing re-uploaded
    assert len(products.published["main_image_refs"]) == 4


def test_the_callers_brief_is_not_mutated(tmp_path):
    """The tool body used to fill `brief["images"]` in place; a service that edits
    its input surprises any caller that reuses or re-runs the same brief."""
    products = _StubProducts()
    brief = _folder(tmp_path)
    before = json.dumps(brief, sort_keys=True)
    PublishFromBrief(products).run(brief, token="t", base_dir=tmp_path, draft=True)
    assert json.dumps(brief, sort_keys=True) == before


def test_load_returns_the_brief_and_its_folder(tmp_path):
    _folder(tmp_path)
    brief, base_dir = PublishFromBrief(_StubProducts(), allowed_roots=[tmp_path]).load(
        str(tmp_path / "brief.json"))
    assert brief["category_id"] == 1
    assert base_dir == tmp_path


def test_load_is_confined_to_the_allowlist(tmp_path):
    import pytest

    from alibaba_seller_mcp.pathsafe import PathNotAllowedError

    outside = tmp_path.parent / "elsewhere.json"
    outside.write_text("{}", encoding="utf-8")
    svc = PublishFromBrief(_StubProducts(), allowed_roots=[tmp_path])
    with pytest.raises(PathNotAllowedError):
        svc.load(str(outside))


def test_no_photos_means_no_media_step(tmp_path):
    products = _StubProducts()
    brief = {"brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"},
             "price": {"tiers": [[1, 1]]}, "images": {"main": [{"file_id": "1", "url": "u"}] * 4}}
    out = PublishFromBrief(products).run(brief, token="t", base_dir=tmp_path, draft=True)
    assert not any("photos" in n for n in out.ai_filled)
    assert out.needs_more_main_images is False


# ── the schema is fetched once, not once per layer ──────────────────────────
SUBMIT_SCHEMA = """<itemSchema>
  <field id="productTitle" type="input"/><field id="scPrice" type="singleCheck"/>
  <field id="priceUnit" type="singleCheck"><options><option displayName="Set/Sets" value="20"/></options></field>
</itemSchema>"""


class _CountingClient:
    """A gateway that records every method called."""

    def __init__(self):
        self.calls: list[str] = []

    def call(self, method, params=None, *, access_token=None, files=None, timeout=30.0, protocol="rest"):
        self.calls.append(method)
        if "schema.get" in method:
            return {"code": "0", "data": SUBMIT_SCHEMA}
        return {"code": "0", "product_id": 7, "biz_success": True}


def _real_service(tmp_path):
    from alibaba_seller_mcp.alibaba.products import ProductService
    from alibaba_seller_mcp.config import Config

    client = _CountingClient()
    return ProductService(Config(app_key="k", app_secret="s", state_dir=tmp_path), client), client


def test_a_brief_publish_fetches_the_schema_once(tmp_path):
    """The service needs the schema to map option codes, and _submit needs it to
    build the XML — but it is the same form, and it is ~100 KB for a real
    category, so it must not be fetched twice."""
    svc, client = _real_service(tmp_path)
    brief = {"brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"},
             "price": {"unit": "Set", "tiers": [[1, 9.9]]},
             "images": {"main": [{"file_id": "1", "url": "u"}] * 4}}

    PublishFromBrief(svc).run(brief, token="t", draft=True)

    assert client.calls.count("alibaba.icbu.product.schema.get") == 1
    assert client.calls.count("alibaba.icbu.product.schema.add.draft") == 1


def test_the_brief_service_hands_its_schema_to_the_publish_call(tmp_path):
    products = _StubProducts()
    brief = {"brief": "x", "category_id": 1, "ai": False, "content": {"title": "T"},
             "price": {"tiers": [[1, 1]]}, "images": {"main": [{"file_id": "1", "url": "u"}] * 4}}
    PublishFromBrief(products).run(brief, token="t", draft=True)
    assert products.schema_fetches == 1
    assert products.published_with_schema is not None, "publish_product got no schema to reuse"


def test_a_standalone_publish_still_fetches_its_own_schema(tmp_path):
    """product_publish (a manifest, no brief) has no schema of its own — the
    parameter is an optimisation, not a new requirement."""
    from alibaba_seller_mcp.alibaba.manifest import ProductManifest

    svc, client = _real_service(tmp_path)
    m = ProductManifest({"category_id": 1, "fields": {"productTitle": "T"}}, tmp_path)
    r = svc.publish_product(m, "t", draft=True)
    assert client.calls.count("alibaba.icbu.product.schema.get") == 1
    assert r["product_id"] == "7"


# ── inline briefs must behave like file briefs ──────────────────────────────
def _inline(tmp_path, **extra):
    _photo(tmp_path / "hero.png")
    return {"brief": "a lamp", "category_id": 1, "ai": False, "content": {"title": "T"},
            "price": {"tiers": [[1, 9.9]]}, "detail_spec": SPEC, **extra}


def test_inline_brief_with_photos_must_name_a_folder(tmp_path):
    """Rendering writes a whole images/ tree. An inline brief that names no
    folder used to fall back to the process's cwd, which dropped generated files
    into unrelated directories (it littered this repo on every test run).
    Refusing with a fix is better than guessing — and far better than the
    original behaviour of ignoring `photos` silently."""
    import pytest

    products = _StubProducts()
    brief = _inline(tmp_path, photos={"hero": str(tmp_path / "hero.png")})
    with pytest.raises(ValueError, match="no folder to render into"):
        PublishFromBrief(products).run(brief, token="t", draft=True)


def test_rendering_never_writes_outside_the_brief_folder(tmp_path, monkeypatch):
    """Whatever the process's cwd is, the images/ tree lands beside the brief."""
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    product = tmp_path / "product"
    product.mkdir()
    _photo(product / "hero.png")
    brief = {"brief": "a lamp", "category_id": 1, "ai": False, "content": {"title": "T"},
             "price": {"tiers": [[1, 9.9]]}, "detail_spec": SPEC,
             "photos": {"hero": "hero.png"}, "base_dir": str(product)}

    PublishFromBrief(_StubProducts(), allowed_roots=[tmp_path]).run(brief, token="t", draft=True)

    assert (product / "images" / "main" / "01_hero.jpg").exists()
    assert not (elsewhere / "images").exists(), "rendered into the cwd instead of the brief folder"


def test_inline_brief_can_declare_its_own_base_dir(tmp_path):
    products = _StubProducts()
    brief = _inline(tmp_path, photos={"hero": "hero.png"}, base_dir=str(tmp_path))
    out = PublishFromBrief(products, allowed_roots=[tmp_path]).run(brief, token="t", draft=True)

    assert any("main images prepared from photos" in n for n in out.ai_filled)
    assert (tmp_path / "images" / "main" / "01_hero.jpg").exists()


def test_inline_and_file_briefs_produce_the_same_manifest(tmp_path):
    """The capability must not depend on how the brief was delivered."""
    from_file = _StubProducts()
    file_brief = _folder(tmp_path)
    PublishFromBrief(from_file).run(file_brief, token="t", base_dir=tmp_path, draft=True)

    inline = _StubProducts()
    inline_brief = dict(file_brief, base_dir=str(tmp_path))
    PublishFromBrief(inline, allowed_roots=[tmp_path]).run(inline_brief, token="t", draft=True)

    assert len(inline.published["main_image_refs"]) == len(from_file.published["main_image_refs"])
    assert len(inline.published["detail_image_refs"]) == len(from_file.published["detail_image_refs"])


def test_a_declared_base_dir_is_confined_to_the_allowlist(tmp_path):
    import pytest

    from alibaba_seller_mcp.pathsafe import PathNotAllowedError

    inside = tmp_path / "product"
    inside.mkdir()
    brief = _inline(tmp_path, photos={"hero": "hero.png"}, base_dir=str(tmp_path.parent))
    svc = PublishFromBrief(_StubProducts(), allowed_roots=[inside])
    with pytest.raises(PathNotAllowedError):
        svc.run(brief, token="t", draft=True)
