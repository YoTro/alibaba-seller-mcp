"""Path confinement, manifest safety, and tool annotation/output-schema coverage."""

import asyncio

import pytest

from alibaba_seller_mcp.alibaba.manifest import ProductManifest
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


# Tool names are the MCP public contract: a client calls them by name, so removing
# or renaming one silently breaks it. This is a FLOOR, not an equality check —
# adding a tool is fine and must not fail the suite. Delete a name from here only
# together with a deliberate decision to break callers.
PUBLISHED_TOOLS = frozenset({
    "alibaba_auth_status",
    "alibaba_complete_authorization",
    "alibaba_complete_authorization_from_url",
    "alibaba_get_authorize_url",
    "alibaba_raw_call",
    "category_get_attributes",
    "detail_images_render",
    "generate_product_detail",
    "generate_social_content",
    "listing_media_prepare",
    "product_get_schema",
    "product_group_get",
    "product_publish",
    "product_publish_from_brief",
    "product_render_draft",
    "product_update",
    "product_upload_image",
    "read_local_media",
    "read_price_file",
    "usage_stats",
    "video_list_related",
    "video_query",
    "video_relate_product",
})


def test_no_published_tool_disappears():
    from alibaba_seller_mcp import server

    names = {t.name for t in asyncio.run(server.mcp.list_tools())}
    missing = sorted(PUBLISHED_TOOLS - names)
    assert not missing, f"removed or renamed, which breaks existing clients: {missing}"


def test_all_tools_have_annotations_and_output_schema():
    from alibaba_seller_mcp import server

    tools = asyncio.run(server.mcp.list_tools())
    assert tools, "no tools registered — the checks below would pass vacuously"
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


# ── annotation honesty ──────────────────────────────────────────────────────
#
# Per the MCP spec, `destructiveHint: false` means the tool performs ONLY
# additive updates, and a client may auto-approve on the strength of it. It is a
# promise, so a tool earns a place here with evidence that nothing existing can
# be overwritten or removed — never by assumption. (The hint is meaningless for
# read-only tools, which are skipped.)
ADDITIVE_ONLY: set[str] = set()


def test_no_tool_claims_to_be_additive_without_evidence():
    from alibaba_seller_mcp import server

    for t in asyncio.run(server.mcp.list_tools()):
        a = t.annotations
        if a.read_only_hint:
            continue
        assert a.destructive_hint is not False or t.name in ADDITIVE_ONLY, (
            f"{t.name} claims destructiveHint=false (additive only). If it can "
            "overwrite or replace anything the seller already has, drop the claim."
        )


def test_overwriting_tools_are_flagged_destructive():
    from alibaba_seller_mcp import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    # schema.update replaces the whole product document
    assert tools["product_update"].annotations.destructive_hint is True
    # each video target is a single slot: relating replaces what was there
    assert tools["video_relate_product"].annotations.destructive_hint is True
    # the escape hatch can call any write API
    assert tools["alibaba_raw_call"].annotations.destructive_hint is True


def test_product_update_documents_that_it_needs_a_complete_manifest():
    """The tool used to advertise a partial update the platform does not offer —
    a caller following that description loses every field they left out."""
    from alibaba_seller_mcp import server

    desc = {t.name: t.description for t in asyncio.run(server.mcp.list_tools())}["product_update"]
    assert "complete" in desc.lower()
    assert "NOT a partial update" in desc
    assert "Only the fields present in the manifest are changed" not in desc


def test_the_oauth_helper_does_not_drag_in_the_mcp_server():
    """authserver is a small CLI; importing it used to pull in server.py and
    register all 23 tools just to reach a private .env loader."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c",
         "import alibaba_seller_mcp.authserver, sys; "
         "print('alibaba_seller_mcp.server' in sys.modules)"],
        capture_output=True, text=True, check=True,
    )
    assert r.stdout.strip() == "False"
