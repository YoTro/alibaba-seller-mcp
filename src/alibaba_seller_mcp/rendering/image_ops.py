"""Pure image operations for the renderer: colour constants, product cut-outs,
fitting, and the colour-variant transforms (hue shift for saturated accents, tint
for black/grey bodies). No layout, no text, no I/O beyond opening files."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops

RGB = tuple[int, int, int]
WHITE: RGB = (255, 255, 255)


def hex_rgb(value: str) -> RGB:
    v = value.lstrip("#")
    return int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16)


def fit(im: Image.Image, w: int, h: int) -> Image.Image:
    im = im.copy()
    im.thumbnail((w, h), Image.LANCZOS)
    return im


def recolor(im: Image.Image, hue_deg: int | None, sat: float = 1.0, val: float = 1.0,
            *, band: tuple[int, int] = (8, 32), min_sat: int = 90) -> Image.Image:
    """Shift the accent-coloured pixels (hue band, 0–255 scale) to another colour.

    Mask = pixels whose hue is inside ``band`` and saturation > ``min_sat``; on those,
    hue is replaced (unless ``hue_deg`` is None), saturation/brightness scaled.
    Pure-PIL (no numpy): point lookups + composite.
    """
    h, s, v = im.convert("HSV").split()
    lo, hi = band
    mask = ImageChops.multiply(
        h.point(lambda p: 255 if lo <= p <= hi else 0),
        s.point(lambda p: 255 if p > min_sat else 0),
    )
    if hue_deg is not None:
        h = Image.composite(Image.new("L", im.size, int(hue_deg * 255 / 360) % 256), h, mask)
    if sat != 1.0:
        s = Image.composite(s.point(lambda p: min(255, int(p * sat))), s, mask)
    if val != 1.0:
        v = Image.composite(v.point(lambda p: min(255, int(p * val))), v, mask)
    return Image.merge("HSV", (h, s, v)).convert("RGB")


def accent_band(im: Image.Image) -> tuple[int, int] | None:
    """Dominant saturated hue band of a render (for recolouring), or None when the
    product has no saturated accent (black / white / grey bodies)."""
    small = fit(im, 200, 200)
    h, s, _ = small.convert("HSV").split()
    hist = h.histogram(mask=s.point(lambda p: 255 if p > 120 else 0))
    peak = max(range(256), key=hist.__getitem__)
    if hist[peak] < 0.01 * small.size[0] * small.size[1]:
        return None
    return max(0, peak - 12), min(255, peak + 12)


def tint(im: Image.Image, hue_deg: int | None, sat: float = 1.0, val: float = 1.0) -> Image.Image:
    """Colour variant for an unsaturated (black/grey) product: every non-background
    pixel gets the target hue, a fixed saturation and a brightness lift so the
    colour reads on a dark body. Background (near-white) pixels are untouched."""
    h, s, v = im.convert("HSV").split()
    mask = ImageChops.multiply(
        v.point(lambda p: 255 if 10 < p < 242 else 0),    # not black-void, not white background
        s.point(lambda p: 255 if p < 60 else 0) if hue_deg is not None else v.point(lambda p: 255),
    )
    if hue_deg is None:  # e.g. "black": only brightness/saturation scaling
        s2 = s.point(lambda p: min(255, int(p * sat)))
        v2 = v.point(lambda p: min(255, int(p * val)))
        return Image.merge("HSV", (h, Image.composite(s2, s, mask), Image.composite(v2, v, mask))).convert("RGB")
    h2 = Image.new("L", im.size, int(hue_deg * 255 / 360) % 256)
    s2 = Image.new("L", im.size, min(255, int(150 * sat)))
    v2 = v.point(lambda p: min(255, int(60 + p * 1.6 * val)))
    return Image.merge("HSV", (Image.composite(h2, h, mask), Image.composite(s2, s, mask),
                               Image.composite(v2, v, mask))).convert("RGB")


def open_rgb(path: Path | str) -> Image.Image:
    """Open an image as RGB, compositing any transparency onto white.

    Renders often come as transparent PNGs; a plain ``convert("RGB")`` would turn
    the transparent area black and defeat the white-background crop."""
    im = Image.open(path)
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        return Image.alpha_composite(bg, im).convert("RGB")
    return im.convert("RGB")


def product_bbox(im: Image.Image, threshold: int = 12) -> tuple[int, int, int, int]:
    """Bounding box of non-white content (white-background renders)."""
    rgb = im.convert("RGB")
    bg = Image.new("RGB", rgb.size, (255, 255, 255))
    diff = ImageChops.difference(rgb, bg).convert("L").point(lambda p: 255 if p > threshold else 0)
    return diff.getbbox() or (0, 0, rgb.size[0], rgb.size[1])


def product_cutout(path: Path | str) -> Image.Image:
    """A white-background render cropped to the product."""
    im = open_rgb(path)
    return im.crop(product_bbox(im))
