"""Path confinement, manifest safety, and tool annotation/output-schema coverage."""

import asyncio

import pytest

from alibaba_seller_mcp.alibaba.products import ProductManifest
from alibaba_seller_mcp.pathsafe import PathNotAllowedError, ensure_allowed


def test_ensure_allowed_within_root(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    resolved = ensure_allowed(str(tmp_path / "a.txt"), [tmp_path])
    assert resolved == (tmp_path / "a.txt").resolve()


def test_ensure_allowed_blocks_outside(tmp_path):
    other = tmp_path.parent / "outside"
    with pytest.raises(PathNotAllowedError):
        ensure_allowed(str(other / "secret"), [tmp_path])


def test_ensure_allowed_blocks_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(PathNotAllowedError):
        ensure_allowed(str(root / ".." / "etc" / "passwd"), [root])


def test_ensure_allowed_write_parent_need_not_exist(tmp_path):
    # a not-yet-created file under the root is allowed for writing
    resolved = ensure_allowed(str(tmp_path / "new" / "f.json"), [tmp_path], for_write=True)
    assert str(resolved).startswith(str(tmp_path.resolve()))


def test_manifest_resolve_confined(tmp_path):
    (tmp_path / "img.jpg").write_bytes(b"")
    m = ProductManifest({"category_id": 1}, tmp_path, allowed_roots=[tmp_path])
    assert m.resolve("img.jpg") == str((tmp_path / "img.jpg").resolve())
    with pytest.raises(PathNotAllowedError):
        m.resolve("../../etc/passwd")


def test_manifest_load_confined(tmp_path):
    with pytest.raises(PathNotAllowedError):
        ProductManifest.load(str(tmp_path.parent / "outside.json"), allowed_roots=[tmp_path])


def test_all_tools_have_annotations_and_output_schema():
    from alibaba_seller_mcp import server

    tools = asyncio.run(server.mcp.list_tools())
    assert len(tools) == 21
    for t in tools:
        assert t.annotations is not None, f"{t.name} missing annotations"
        assert t.output_schema is not None, f"{t.name} missing output_schema"
        # structured output must not be an open object
        assert t.output_schema.get("additionalProperties") is False, t.name


def test_readonly_tools_flagged():
    from alibaba_seller_mcp import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    assert tools["product_get_schema"].annotations.read_only_hint is True
    assert tools["usage_stats"].annotations.read_only_hint is True
    # publishing is not read-only and is non-idempotent
    assert tools["product_publish"].annotations.read_only_hint is False
    assert tools["product_publish"].annotations.idempotent_hint is False
    # relating a video is idempotent
    assert tools["video_relate_product"].annotations.idempotent_hint is True
