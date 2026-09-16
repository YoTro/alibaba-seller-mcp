"""Code-rendered detail images: spec validation, rendering, main-image prep, brief media flow."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import ValidationError

from alibaba_seller_mcp.ai.detail_spec import DetailSpecGenerator
from alibaba_seller_mcp.config import Config
from alibaba_seller_mcp.rendering import DetailSpec, PagesOnly, prepare_main_images, render_spec
from alibaba_seller_mcp.listing.media import prepare_brief_media
from alibaba_seller_mcp.rendering.image_ops import recolor
from alibaba_seller_mcp.rendering.spec import Assets, SceneAsset
from alibaba_seller_mcp.storage import UsageStore
from alibaba_seller_mcp.usage.tracker import UsageTracker


# ── fixtures ────────────────────────────────────────────────────────────────
def _render(path: Path, size=(800, 500)):
    """A white-background 'render': orange body + black top block."""
    im = Image.new("RGB", size, (255, 255, 255))
    px = im.load()
    for x in range(150, 650):
        for y in range(200, 420):
            px[x, y] = (244, 122, 32)
    for x in range(300, 450):
        for y in range(120, 200):
            px[x, y] = (30, 30, 30)
    im.save(path)


def _scene(path: Path, size=(600, 1000)):
    Image.new("RGB", size, (120, 160, 90)).save(path)


@pytest.fixture
def assets_dir(tmp_path):
    _render(tmp_path / "hero.png")
    _render(tmp_path / "side.png")
    _scene(tmp_path / "s1.png")
    _scene(tmp_path / "s2.png", (1000, 600))
    return tmp_path


ALL_PAGES = [
    {"type": "hero", "tagline": "Tagline", "badges": ["A", "B"], "intro": "Intro.",
     "stats": [{"value": "1 kg", "label": "weight"}]},
    {"type": "features", "title": "Features", "items": [{"title": "One", "text": "t"}, {"title": "Two", "text": "t"}],
     "note": "note"},
    {"type": "steps", "title": "Steps", "steps": [{"title": "A", "text": "a"}, {"title": "B", "text": "b"},
                                                 {"title": "C", "text": "c"}], "tip": {"title": "Tip", "text": "x"}},
    {"type": "levels", "title": "Levels", "levels": [{"label": "L1", "intensity": 1, "value": "1"},
                                                    {"label": "L2", "intensity": 5, "value": "2"}]},
    {"type": "modes", "title": "Modes", "subtitle": "sub",
     "modes": [{"label": "Indoor", "value": "Default", "note": "for rooms"},
               {"label": "Outdoor", "value": "High", "note": "larger areas"},
               {"label": "Light", "value": "Warm"}], "footnote": "no memory"},
    {"type": "callouts", "title": "Parts", "callouts": [{"label": "Top", "x": 0.5, "y": 0.05, "side": "up"},
                                                       {"label": "Left", "x": 0.02, "y": 0.5, "side": "left"}],
     "stats": [{"value": "v", "label": "l"}], "summary": "sum"},
    {"type": "chips", "title": "Chips", "groups": [{"heading": "Good", "tone": "positive", "items": ["a", "b"]},
                                                  {"heading": "Bad", "tone": "negative", "items": ["c"]}],
     "notice": {"title": "N", "text": "n"}},
    {"type": "scenes", "title": "Scenes"},
    {"type": "spec_table", "rows": [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}, {"name": "c", "value": "3"}]},
    {"type": "oem_odm", "colors": [{"name": "Std"}, {"name": "Green", "hue": 120}, {"name": "Black", "saturation": 0, "brightness": 0.25}],
     "services": [{"title": "Logo", "text": "print"}], "process": ["1", "2", "3"], "note": "note"},
    {"type": "trust", "title": "Trust", "badges": [{"value": "X", "label": "y"}], "footnote": "f",
     "box_items": ["item"], "notice": {"title": "S", "text": "s"}},
]


def _spec(assets_dir: Path, pages=None) -> DetailSpec:
    return DetailSpec(
        brand="ACME", product_name="Widget", model="W1",
        assets=Assets(hero="hero.png", side="side.png",
                      scenes=[SceneAsset(path="s1.png", caption="Kitchen"), SceneAsset(path="s2.png")]),
        pages=pages or ALL_PAGES,
    )


# ── spec ────────────────────────────────────────────────────────────────────
def test_spec_discriminator_and_limits():
    PagesOnly.model_validate({"pages": ALL_PAGES})
    with pytest.raises(ValidationError):
        PagesOnly.model_validate({"pages": [{"type": "banner", "tagline": "x"}]})
    with pytest.raises(ValidationError):  # too-long text is rejected, not silently clipped
        PagesOnly.model_validate({"pages": [{"type": "hero", "tagline": "x" * 200}]})
    with pytest.raises(ValidationError):  # extra keys forbidden
        PagesOnly.model_validate({"pages": [{"type": "scenes", "title": "t", "bogus": 1}]})


def test_footer_default():
    assert DetailSpec(brand="A", product_name="B", model="C", assets=Assets(hero="h"),
                      pages=[{"type": "scenes", "title": "t"}]).footer_text() == "A  ·  B  ·  Model C"


# ── rendering ───────────────────────────────────────────────────────────────
def test_render_all_page_types(assets_dir):
    outs = render_spec(_spec(assets_dir), assets_dir / "out", base_dir=assets_dir)
    assert [p.name for p in outs] == [f"{i:02d}_{pg['type']}.jpg" for i, pg in enumerate(ALL_PAGES, 1)]
    for p in outs:
        im = Image.open(p)
        assert im.size[0] == 1200 and im.size[1] >= 400
        assert p.stat().st_size < 3_000_000


# Spec-maximum copy for every field a box has to hold. These lengths are what the
# validator allows, so the renderer must fit them without spilling out of its boxes.
def _long(n: int) -> str:
    return " ".join(["wordy"] * (n // 6))[:n]


MAXED_PAGES = [
    {"type": "hero", "tagline": _long(70), "badges": [_long(20)] * 5, "intro": _long(140),
     "stats": [{"value": _long(24), "label": _long(40)} for _ in range(4)]},
    {"type": "features", "title": _long(48), "subtitle": _long(120),
     "items": [{"title": _long(48), "text": _long(180)} for _ in range(5)], "note": _long(120)},
    {"type": "steps", "title": _long(48), "subtitle": _long(120),
     "steps": [{"title": _long(48), "text": _long(180)} for _ in range(6)],
     "tip": {"title": _long(40), "text": _long(320)}},
    {"type": "callouts", "title": _long(48), "subtitle": _long(140),
     "callouts": [{"label": _long(44), "x": 0.5, "y": 0.5}],
     "stats": [{"value": _long(24), "label": _long(40)} for _ in range(4)], "summary": _long(160)},
    {"type": "chips", "title": _long(48), "subtitle": _long(120),
     "groups": [{"heading": _long(40), "tone": "positive", "items": [_long(20)] * 12}],
     "notice": {"title": _long(40), "text": _long(320)}},
    {"type": "spec_table", "title": _long(48),
     "rows": [{"name": _long(40), "value": _long(90)} for _ in range(4)]},
    {"type": "oem_odm", "subtitle": _long(120), "colors": [{"name": _long(24)}],
     "services": [{"title": _long(48), "text": _long(180)} for _ in range(5)],
     "process": [_long(20)] * 5, "note": _long(240)},
    {"type": "trust", "title": _long(48), "badges": [{"value": _long(24), "label": _long(40)}],
     "footnote": _long(140), "box_title": _long(40), "box_items": [_long(30)] * 6,
     "notice": {"title": _long(40), "text": _long(320)}},
]


def _ink_rows(im: Image.Image) -> list[int]:
    """Rows holding text-dark pixels, ignoring the dark footer band."""
    px = im.convert("RGB").load()
    rows = []
    for y in range(0, im.size[1] - 70):
        for x in range(0, im.size[0], 3):
            r, g, b = px[x, y]
            if r < 160 and g < 160 and b < 160 and abs(r - g) < 30 and abs(g - b) < 30:
                rows.append(y)
                break
    return rows


def _ink_in_margins(im: Image.Image, margin: int = 40) -> list[tuple[int, int]]:
    """Text pixels inside the page's outer margin — i.e. copy that ran off the side.
    Rows inside a full-bleed dark band (hero banner, footer) are skipped."""
    px = im.convert("RGB").load()
    W, H = im.size
    hits = []
    for y in range(H):
        bg = px[0, y]                       # the row's own background (white page or dark band)
        for x in list(range(1, margin)) + list(range(W - margin, W - 1)):
            if max(abs(c - d) for c, d in zip(px[x, y], bg)) > 55:
                hits.append((x, y))
    return hits


def test_maxed_out_copy_stays_inside_the_page(assets_dir):
    """Every box grows with its copy: with spec-maximum text nothing may be drawn
    into the footer band or off the bottom of the canvas."""
    outs = render_spec(_spec(assets_dir, MAXED_PAGES), assets_dir / "max", base_dir=assets_dir)
    assert len(outs) == len(MAXED_PAGES)
    for path, page in zip(outs, MAXED_PAGES):
        im = Image.open(path)
        rows = _ink_rows(im)
        assert rows, f"{page['type']}: nothing rendered"
        # the footer strip is the last 60px; leave a 10px breathing margin above it
        assert max(rows) < im.size[1] - 70, f"{page['type']}: text runs into the footer"
        assert not _ink_in_margins(im), f"{page['type']}: text runs off the side of the page"


def test_boxes_grow_with_their_copy(assets_dir):
    """A page with long copy must be taller than the same page with short copy —
    the regression guard against fixed-height cards clipping their text."""
    short = {"type": "steps", "title": "Steps",
             "steps": [{"title": "A", "text": "a"}] * 3, "tip": {"title": "Tip", "text": "x"}}
    long_ = {"type": "steps", "title": "Steps",
             "steps": [{"title": _long(48), "text": _long(180)}] * 3,
             "tip": {"title": "Tip", "text": _long(320)}}
    hs = [Image.open(render_spec(_spec(assets_dir, [pg]), assets_dir / f"g{i}", base_dir=assets_dir)[0]).size[1]
          for i, pg in enumerate((short, long_))]
    assert hs[1] > hs[0] + 200


def test_recolor_shifts_only_accent_pixels(assets_dir):
    im = Image.open(assets_dir / "side.png").convert("RGB")
    green = recolor(im, 120)
    r, g, b = green.getpixel((400, 300))       # inside the orange body
    assert g > r and g > b
    assert green.getpixel((350, 150)) == (30, 30, 30)   # black block untouched
    assert green.getpixel((10, 10)) == (255, 255, 255)  # background untouched


def test_black_product_gets_tinted_variants():
    from alibaba_seller_mcp.rendering.image_ops import accent_band, tint

    im = Image.new("RGB", (300, 200), (255, 255, 255))
    for x in range(80, 220):
        for y in range(40, 160):
            im.putpixel((x, y), (35, 35, 35))            # a black body, no saturated accent
    assert accent_band(im) is None
    green = tint(im, 120)
    r, g, b = green.getpixel((150, 100))
    assert g > r + 30 and g > b + 30                      # body reads green
    assert green.getpixel((5, 5)) == (255, 255, 255)      # background untouched


def test_transparent_render_is_composited_on_white(tmp_path):
    from alibaba_seller_mcp.rendering.image_ops import open_rgb
    from alibaba_seller_mcp.rendering.main_images import render_to_square

    im = Image.new("RGBA", (400, 300), (0, 0, 0, 0))          # fully transparent background
    for x in range(100, 300):
        for y in range(100, 200):
            im.putpixel((x, y), (244, 122, 32, 255))
    im.save(tmp_path / "t.png")
    assert open_rgb(tmp_path / "t.png").getpixel((5, 5)) == (255, 255, 255)
    out = render_to_square(tmp_path / "t.png", tmp_path / "sq.jpg")
    sq = Image.open(out)
    assert sq.size == (1000, 1000)
    r, g, b = sq.getpixel((3, 3))
    assert r > 250 and g > 250 and b > 250          # corners stay white, not black


# ── main images ─────────────────────────────────────────────────────────────
def test_prepare_main_images_ratios(assets_dir):
    spec = _spec(assets_dir)
    outs = prepare_main_images(spec.assets, assets_dir / "main", base_dir=assets_dir)
    assert len(outs) == 4
    sizes = [Image.open(p).size for p in outs]
    assert sizes[0] == (1000, 1000) and sizes[1] == (1000, 1000)
    w, h = sizes[2]
    assert abs(w / h - 3 / 4) < 0.01            # portrait scene → 3:4
    w, h = sizes[3]
    assert abs(w / h - 4 / 3) < 0.01            # landscape scene → 4:3


# ── AI spec generation (stubbed client) ─────────────────────────────────────
class _FakeMessages:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        text = self.replies.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=20,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )


def _generator(tmp_path, replies):
    cfg = Config(app_key="k", app_secret="s", state_dir=tmp_path)
    tracker = UsageTracker(UsageStore(tmp_path / "usage.jsonl"))
    client = SimpleNamespace(messages=_FakeMessages(replies))
    return DetailSpecGenerator(cfg, tracker, client=client), client


def test_generate_detail_spec_retries_once_on_invalid(tmp_path):
    bad = json.dumps({"pages": [{"type": "nope"}]})
    good = json.dumps({"pages": [{"type": "scenes", "title": "Scenes"}]})
    gen, client = _generator(tmp_path, [bad, good])
    out = gen.generate(brand="A", product_name="B", facts="f", product_image=b"\xff\xd8\xff")
    assert out["pages"] == [{"type": "scenes", "title": "Scenes", "subtitle": ""}]
    assert len(client.messages.calls) == 2
    assert out["usage"] == {"input_tokens": 20, "output_tokens": 40}
    # the image was attached on the first call
    first = client.messages.calls[0]["messages"][0]["content"]
    assert first[0]["type"] == "image"


def test_generate_detail_spec_gives_up_after_retry(tmp_path):
    gen, _ = _generator(tmp_path, ["not json", "{}"])
    with pytest.raises(ValueError):
        gen.generate(brand="A", product_name="B", facts="f")


# ── brief media flow ────────────────────────────────────────────────────────
def _brief():
    return {
        "brief": "ACME Widget W1: a thing. It does stuff.",
        "brand": ["ACME"],
        "model": "W1",
        "photos": {"hero": "hero.png", "side": "side.png",
                   "scenes": [{"path": "s1.png", "caption": "Kitchen"}, "s2.png"]},
    }


def test_brief_media_uses_ai_then_existing_spec(assets_dir, tmp_path):
    gen, client = _generator(tmp_path, [json.dumps({"pages": [{"type": "scenes", "title": "Scenes"},
                                                              {"type": "spec_table", "rows": [
                                                                  {"name": "a", "value": "1"}, {"name": "b", "value": "2"},
                                                                  {"name": "c", "value": "3"}]}]})])
    prep = prepare_brief_media(_brief(), assets_dir, gen)
    assert prep.spec_source == "ai"
    assert Path(prep.spec_path).name == "detail_spec.json"
    assert len(prep.main_paths) == 4 and len(prep.detail_paths) == 2
    saved = json.loads(Path(prep.spec_path).read_text())
    assert saved["brand"] == "ACME" and saved["product_name"] == "ACME Widget W1"

    # second run: spec exists → no AI call, generator not even needed
    prep2 = prepare_brief_media(_brief(), assets_dir, None)
    assert prep2.spec_source == "existing" and len(prep2.detail_paths) == 2
    assert len(client.messages.calls) == 1


def test_brief_media_inline_spec_wins_without_ai(assets_dir):
    brief = _brief()
    brief["ai"] = False
    brief["detail_spec"] = {"theme": {"primary": "#112233"},
                            "pages": [{"type": "scenes", "title": "Scenes"},
                                      {"type": "features", "title": "F", "items": [{"title": "a"}, {"title": "b"}]}]}
    prep = prepare_brief_media(brief, assets_dir, None)
    assert prep.spec_source == "inline" and prep.spec_path is None
    assert [Path(p).name for p in prep.detail_paths] == ["01_scenes.jpg", "02_features.jpg"]
    assert not (assets_dir / "detail_spec.json").exists()      # nothing written beside the brief


def test_brief_media_ai_false_never_calls_generator(assets_dir, tmp_path):
    gen, client = _generator(tmp_path, ["{}"])
    brief = _brief()
    brief["ai"] = False
    with pytest.raises(ValueError):
        prepare_brief_media(brief, assets_dir, gen)
    assert client.messages.calls == []


def test_brief_media_without_photos_is_noop(assets_dir):
    prep = prepare_brief_media({"brief": "x"}, assets_dir, None)
    assert prep.spec_source == "none" and not prep.main_paths and not prep.detail_paths


def test_brief_media_requires_generator_when_no_spec(assets_dir):
    with pytest.raises(ValueError):
        prepare_brief_media(_brief(), assets_dir, None)
