"""The allowlist must cover paths named *inside* a brief or spec, not just tool
arguments. A brief's `photos` are read, rendered into listing images and uploaded
to the photo bank, so an unchecked one publishes an arbitrary local file.
"""

import asyncio
import json

import pytest
from PIL import Image

from alibaba_seller_mcp import server
from alibaba_seller_mcp.config import Config


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """An allowlist of exactly one product folder, plus a file outside it."""
    product = tmp_path / "product"
    product.mkdir()
    Image.new("RGB", (800, 800), (200, 120, 40)).save(product / "hero.png")
    outside = tmp_path / "outside_secret.png"
    Image.new("RGB", (800, 800), (10, 200, 10)).save(outside)
    monkeypatch.setattr(
        server.ctx, "_config",
        Config(app_key="k", app_secret="s", state_dir=tmp_path / "state", allowed_paths=(product,)),
    )
    return product, outside


def _call(name, args):
    return asyncio.run(server.mcp.call_tool(name, args))


SPEC_PAGES = [{"type": "spec_table", "rows": [{"name": "a", "value": "1"},
                                              {"name": "b", "value": "2"},
                                              {"name": "c", "value": "3"}]}]


def _write_brief(folder, hero):
    brief = {"brief": "probe", "category_id": 1520, "ai": False,
             "photos": {"hero": hero}, "detail_spec": {"pages": SPEC_PAGES}}
    (folder / "brief.json").write_text(json.dumps(brief), encoding="utf-8")
    return str(folder / "brief.json")


def test_brief_photo_outside_the_allowlist_is_refused(sandbox):
    product, outside = sandbox
    res = _call("listing_media_prepare", {"brief_path": _write_brief(product, str(outside))})
    assert res.is_error is True
    assert res.structured_content["error_type"] == "PathNotAllowedError"
    assert not (product / "images").exists(), "no image may be produced from a refused path"


def test_brief_photo_traversal_is_refused(sandbox):
    product, outside = sandbox
    res = _call("listing_media_prepare", {"brief_path": _write_brief(product, "../outside_secret.png")})
    assert res.is_error is True
    assert res.structured_content["error_type"] == "PathNotAllowedError"


def test_brief_photo_inside_the_allowlist_still_works(sandbox):
    product, _ = sandbox
    res = _call("listing_media_prepare", {"brief_path": _write_brief(product, "hero.png")})
    assert res.is_error is False, res.structured_content.get("error")
    assert len(res.structured_content["main_images"]) == 1
    assert len(res.structured_content["detail_images"]) == 1


def test_spec_asset_outside_the_allowlist_is_refused(sandbox):
    product, outside = sandbox
    spec = {"brand": "ACME", "product_name": "Widget",
            "assets": {"hero": str(outside)}, "pages": SPEC_PAGES}
    (product / "detail_spec.json").write_text(json.dumps(spec), encoding="utf-8")
    res = _call("detail_images_render", {"spec_path": str(product / "detail_spec.json")})
    assert res.is_error is True
    assert res.structured_content["error_type"] == "PathNotAllowedError"


def test_missing_photo_reports_the_file_not_a_crash(sandbox):
    """An OSError from a seller typo is an anticipated failure: the client should
    see which file is missing, not a bare 'Error executing tool'."""
    product, _ = sandbox
    res = _call("listing_media_prepare", {"brief_path": _write_brief(product, "typo.png")})
    assert res.is_error is True
    assert res.structured_content["error_type"] == "FileNotFoundError"
    assert "typo.png" in res.structured_content["error"]
