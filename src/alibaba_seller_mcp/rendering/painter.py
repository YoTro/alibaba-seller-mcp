"""Drawing primitives shared by every page renderer, on a fixed-width canvas.

The look: bundled Inter with tight tracking on headlines, light neutral surfaces,
the brand accent used sparingly (eyebrows, numbers, one gradient line), big-number
stats, rounded cards, and product renders that sit on the surface with a soft
contact shadow instead of in a white box.

Renderers work in logical (output) pixels; the canvas is drawn at ``Painter.S``×
and downsampled on save so text and rounded corners come out clean. A *dry*
painter measures without drawing: each page runs its layout once dry to learn its
height, then once for real (see ``pages.page``).
"""

from __future__ import annotations

import colorsys
import re
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .fonts import font as _font
from .image_ops import RGB, WHITE, hex_rgb
from .spec import DetailSpec, Stat

INK: RGB = (29, 29, 31)        # headlines and body copy
SUB: RGB = (110, 110, 115)     # secondary copy
MUTED: RGB = (134, 134, 139)   # footer, leader lines, fine print
CLOUD: RGB = (245, 245, 247)   # card / stage / pill surface
HAIR: RGB = (210, 210, 215)    # hairlines
TRACK = -0.02                  # headline tracking (em)
TOP = 120                      # space above a page's first line
FOOTER_H = 84
GUTTER = 60                    # card inset from the page edge; text sits at 80

# CJK "compatibility" unit squares render as tofu (□) in most Latin fonts because
# the single-glyph forms are absent. Expand them to ASCII+combining equivalents the
# body fonts do have, so specs written with ㎡ / ℃ still render. Applied at the one
# place all text is measured and drawn, so width and pixels stay in sync.
_GLYPH_FIXUPS = {
    "㎡": "m²", "㎥": "m³",           # ㎡ ㎥
    "℃": "°C", "℉": "°F",           # ℃ ℉
    "㎜": "mm", "㎝": "cm", "㎞": "km",     # ㎜ ㎝ ㎞
    "㎏": "kg", "㎖": "ml", "ℓ": "L",      # ㎏ ㎖ ℓ
}

_NUM = re.compile(r"^([<>≤≥~+±]?\d[\d.,:/+×x~–-]*)\s*(\S.*)?$")


def _san(text: str) -> str:
    if text:
        for bad, good in _GLYPH_FIXUPS.items():
            if bad in text:
                text = text.replace(bad, good)
    return text


def mix(a: RGB, b: RGB, t: float) -> RGB:
    """``a`` moved ``t`` of the way towards ``b``."""
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b, strict=True))  # type: ignore[return-value]


def shift_hue(c: RGB, degrees: float, light: float = 1.0) -> RGB:
    hue, lum, sat = colorsys.rgb_to_hls(*(v / 255 for v in c))
    r, g, b = colorsys.hls_to_rgb((hue + degrees / 360) % 1.0, min(1.0, lum * light), sat)
    return round(r * 255), round(g * 255), round(b * 255)


def split_number(value: str) -> tuple[str, str]:
    """``"2 yrs"`` → ``("2", "yrs")``; values that don't start with a number stay whole."""
    m = _NUM.match(_san(value).strip())
    return (m.group(1), m.group(2) or "") if m else (value, "")


class Painter:
    S = 2
    STAT_MAX, STAT_MIN = 64, 32

    def __init__(self, spec: DetailSpec, height: int, bg: RGB = WHITE, *, dry: bool = False):
        self.spec = spec
        self.W, self.H = spec.width, height
        self.dry = dry
        self.im = Image.new("RGB", (self.W * self.S, height * self.S) if not dry else (1, 1), bg)
        self.d = ImageDraw.Draw(self.im)
        t = spec.theme
        self.accent, self.dark = hex_rgb(t.primary), hex_rgb(t.dark)
        self.positive, self.negative = hex_rgb(t.positive), hex_rgb(t.negative)
        self.gradient = (self.accent, shift_hue(self.accent, -30, 0.9))
        self._font_dir = t.font_dir

    def _s(self, v: float) -> int:
        return round(v * self.S)

    # ── text ────────────────────────────────────────────────────────────────
    def f(self, size: int, weight: str = "regular"):
        """Inter at logical ``size`` (weights: regular / medium / semibold / bold)."""
        return _font(size * self.S, weight, self._font_dir, family="inter")

    def _width(self, text: str, fnt, track: float) -> float:
        return self.d.textlength(text, font=fnt) + track * fnt.size * max(0, len(text) - 1)

    def tw(self, text: str, fnt, track: float = 0.0) -> float:
        return self._width(_san(text), fnt, track) / self.S

    def wrap(self, text: str, fnt, max_w: float, track: float = 0.0) -> list[str]:
        lines, cur = [], ""
        for wd in text.split():
            t = (cur + " " + wd).strip()
            if self.tw(t, fnt, track) <= max_w or not cur:
                cur = t
            else:
                lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines

    def lh(self, fnt, leading: float = 1.0) -> float:
        return fnt.size / self.S * leading

    def block_h(self, text: str, fnt, max_w: float, leading: float = 1.3, track: float = 0.0) -> float:
        return len(self.wrap(text, fnt, max_w, track)) * self.lh(fnt, leading) if text else 0

    def fit_size(self, text: str, sizes: tuple[int, ...], weight: str, max_w: float, track: float = 0.0) -> int:
        """Largest size whose longest word still fits ``max_w`` (so it only wraps between words)."""
        longest = max(text.split() or [""], key=len)
        for size in sizes:
            if self.tw(longest, self.f(size, weight), track) <= max_w:
                return size
        return sizes[-1]

    def text(self, xy, text: str, fnt, fill: RGB = INK, *, align: str = "l", valign: str = "a",
             track: float = 0.0, gradient: tuple[RGB, RGB] | None = None):
        """One line at logical ``xy``. ``align`` l / m / r; ``valign`` is a Pillow
        vertical anchor (a = ascender top, m = middle, s = baseline). Tracked text is
        set glyph by glyph at prefix-width offsets, so the font's kerning survives."""
        text = _san(text)
        if self.dry or not text:
            return
        w = self._width(text, fnt, track)
        x = self._s(xy[0]) - {"l": 0, "m": w / 2, "r": w}[align]
        y = self._s(xy[1])
        if gradient is None:
            draw, ox, oy, ink = self.d, x, y, fill
        else:  # draw into a mask, then paint a left→right ramp through it
            asc, desc = fnt.getmetrics()
            pad = fnt.size // 4
            mask = Image.new("L", (int(w) + 2 * pad, asc + desc + 2 * pad), 0)
            draw, ox, oy, ink, valign = ImageDraw.Draw(mask), pad, pad, 255, "a"
        anchor = "l" + valign
        if track:
            gap = track * fnt.size
            for i, ch in enumerate(text):
                draw.text((ox + self.d.textlength(text[:i], font=fnt) + i * gap, oy), ch,
                          font=fnt, fill=ink, anchor=anchor)
        else:
            draw.text((ox, oy), text, font=fnt, fill=ink, anchor=anchor)
        if gradient is not None:
            ramp = Image.linear_gradient("L").rotate(90, expand=True).resize(mask.size)  # 0 left → 255 right
            paint = Image.composite(Image.new("RGB", mask.size, gradient[1]),
                                    Image.new("RGB", mask.size, gradient[0]), ramp)
            self.im.paste(paint, (int(x) - pad, y - pad), mask)

    def block(self, xy, text: str, fnt, fill: RGB = INK, *, max_w: float, leading: float = 1.3,
              align: str = "l", track: float = 0.0, gradient: tuple[RGB, RGB] | None = None) -> float:
        """Wrapped text; returns the y below the last line."""
        x, y = xy
        for ln in self.wrap(text, fnt, max_w, track):
            self.text((x, y), ln, fnt, fill, align=align, track=track, gradient=gradient)
            y += self.lh(fnt, leading)
        return y

    # ── shapes ──────────────────────────────────────────────────────────────
    def rrect(self, box, radius: float, fill: RGB | None = None, outline: RGB | None = None, width: float = 0):
        if not self.dry:
            self.d.rounded_rectangle(tuple(self._s(v) for v in box), radius=self._s(radius), fill=fill,
                                     outline=outline, width=self._s(width))

    def hairline(self, x0: float, y: float, x1: float, fill: RGB = HAIR):
        if not self.dry:
            self.d.rectangle((self._s(x0), self._s(y), self._s(x1), self._s(y) + self.S - 1), fill=fill)

    def vline(self, x: float, y0: float, y1: float, fill: RGB = HAIR):
        if not self.dry:
            self.d.rectangle((self._s(x), self._s(y0), self._s(x) + self.S - 1, self._s(y1)), fill=fill)

    def line(self, points, fill: RGB, width: float = 2):
        if not self.dry:
            self.d.line([(self._s(x), self._s(y)) for x, y in points], fill=fill, width=self._s(width),
                        joint="curve")

    def circle(self, cx: float, cy: float, r: float, fill: RGB | None = None,
               outline: RGB | None = None, width: float = 0):
        if not self.dry:
            self.d.ellipse((self._s(cx - r), self._s(cy - r), self._s(cx + r), self._s(cy + r)),
                           fill=fill, outline=outline, width=self._s(width))

    def mark(self, kind: str, cx: float, cy: float, size: float, color: RGB):
        """A drawn ✓ / ✕ (font-independent)."""
        h, w = size / 2, max(2.0, size * 0.16)
        if kind == "check":
            self.line([(cx - h * 0.9, cy), (cx - h * 0.25, cy + h * 0.65), (cx + h * 0.9, cy - h * 0.6)], color, w)
        elif kind == "cross":
            self.line([(cx - h * 0.7, cy - h * 0.7), (cx + h * 0.7, cy + h * 0.7)], color, w)
            self.line([(cx - h * 0.7, cy + h * 0.7), (cx + h * 0.7, cy - h * 0.7)], color, w)

    def number_badge(self, cx: float, cy: float, n: int, r: float = 22):
        self.circle(cx, cy, r, fill=self.accent)
        self.text((cx, cy), str(n), self.f(round(r * 0.9), "semibold"), WHITE, align="m", valign="m")

    # ── images ──────────────────────────────────────────────────────────────
    def product_size(self, im: Image.Image, max_w: float, max_h: float) -> tuple[int, int]:
        """Logical size a render gets inside ``max_w``×``max_h`` (never enlarged
        beyond its own pixel size in logical units)."""
        k = min(max_w / im.width, max_h / im.height, 1.0)
        return max(1, round(im.width * k)), max(1, round(im.height * k))

    def product(self, im: Image.Image, cx: float, top: float, size: tuple[int, int], shadow: bool = True):
        """Place a white-background render so its background disappears into the
        light surface underneath (multiply), with a soft contact shadow."""
        if self.dry:
            return
        w, h = self._s(size[0]), self._s(size[1])
        x, y = self._s(cx) - w // 2, self._s(top)
        if shadow:
            sw, sh = int(w * 0.72), max(self.S * 6, int(w * 0.05))
            pad = sh * 3
            m = Image.new("L", (sw + 2 * pad, sh + 2 * pad), 0)
            ImageDraw.Draw(m).ellipse((pad, pad, pad + sw, pad + sh), fill=70)
            m = m.filter(ImageFilter.GaussianBlur(sh * 0.8))
            self.im.paste((0, 0, 0), (x + (w - sw) // 2 - pad, y + h - sh // 2 - pad), m)
        render = im.resize((w, h), Image.LANCZOS)
        region = self.im.crop((x, y, x + w, y + h))
        self.im.paste(ImageChops.multiply(region, render), (x, y))

    def stage(self, y: float, im: Image.Image, max_w: float, max_h: float, *, pad: float = 70,
              x0: float = GUTTER, x1: float | None = None) -> float:
        """A light rounded stage with the render centred on it; returns its bottom."""
        x1 = self.W - GUTTER if x1 is None else x1
        size = self.product_size(im, min(max_w, x1 - x0 - 80), max_h)
        bottom = y + size[1] + 2 * pad
        self.rrect((x0, y, x1, bottom), 40, CLOUD)
        self.product(im, (x0 + x1) / 2, y + pad, size)
        return bottom

    def photo(self, im: Image.Image, box, radius: float):
        """A photo scaled to ``box`` with rounded corners."""
        if self.dry:
            return
        x0, y0, x1, y1 = (self._s(v) for v in box)
        mask = Image.new("L", (x1 - x0, y1 - y0), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), radius=self._s(radius), fill=255)
        self.im.paste(im.resize((x1 - x0, y1 - y0), Image.LANCZOS), (x0, y0), mask)

    def scrim(self, box, color: RGB, radius: float, strength: int = 170):
        """Bottom-weighted shade over a photo so a caption reads on any image."""
        if self.dry:
            return
        x0, y0, x1, y1 = (self._s(v) for v in box)
        size = (x1 - x0, y1 - y0)
        ramp = Image.linear_gradient("L").resize(size).point(lambda v: v * strength // 255)
        shape = Image.new("L", size, 0)
        ImageDraw.Draw(shape).rounded_rectangle((0, -self._s(radius), size[0] - 1, size[1] - 1),
                                                radius=self._s(radius), fill=255)
        self.im.paste(color, (x0, y0), ImageChops.multiply(ramp, shape))

    # ── layout pieces ───────────────────────────────────────────────────────
    def header(self, y: float, title: str, subtitle: str = "") -> float:
        """Centred page headline + optional subtitle; returns the y below it."""
        cx = self.W / 2
        fnt = self.f(self.fit_size(title, (72, 64, 56), "bold", self.W - 160, TRACK), "bold")
        y = self.block((cx, y), title, fnt, INK, max_w=self.W - 160, leading=1.05, align="m", track=TRACK)
        if subtitle:
            y = self.block((cx, y + 20), subtitle, self.f(28), SUB, max_w=860, leading=1.4, align="m")
        return y

    def section_title(self, y: float, title: str, subtitle: str = "") -> float:
        y = self.block((80, y), title, self.f(40, "semibold"), INK, max_w=self.W - 160, leading=1.15,
                       track=-0.01)
        if subtitle:
            y = self.block((80, y + 10), subtitle, self.f(22), SUB, max_w=self.W - 160, leading=1.45)
        return y + 32

    def eyebrow(self, xy, text: str, color: RGB, size: int = 16, align: str = "l") -> float:
        fnt = self.f(size, "semibold")
        self.text(xy, text.upper(), fnt, color, align=align, track=0.12)
        return xy[1] + self.lh(fnt)

    def pill_w(self, text: str, fnt, mark: str | None = None) -> float:
        return self.tw(text, fnt) + 40 + (self.lh(fnt) + 8 if mark else 0)

    def pills(self, x: float, y: float, items: list[str], fnt, max_w: float, *, center: bool = False,
              fill: RGB = CLOUD, fg: RGB = INK, mark: str | None = None, mark_color: RGB = INK,
              gap: float = 12) -> float:
        """Rounded tags flowing onto as many rows as they need; returns the bottom."""
        rows: list[list[str]] = []
        used = 0.0
        for it in items:
            w = min(self.pill_w(it, fnt, mark), max_w)
            if not rows or used + w > max_w:
                rows.append([])
                used = 0.0
            rows[-1].append(it)
            used += w + gap
        ph = self.lh(fnt) + 24
        for row in rows:
            widths = [min(self.pill_w(it, fnt, mark), max_w) for it in row]
            px = x + ((max_w - sum(widths) - gap * (len(row) - 1)) / 2 if center else 0)
            for it, w in zip(row, widths, strict=True):
                self.rrect((px, y, px + w, y + ph), ph / 2, fill)
                tx = px + 20
                if mark:
                    s = self.lh(fnt)
                    self.mark(mark, tx + s / 2, y + ph / 2, s * 0.8, mark_color)
                    tx += s + 8
                self.text((tx, y + ph / 2), it, fnt, fg, valign="m")
                px += w + gap
            y += ph + gap
        return y - gap

    def note_card(self, y: float, title: str, text: str, color: RGB) -> float:
        """A lightly tinted card for tips / notices; returns its bottom."""
        pad, x0, x1 = 40, GUTTER, self.W - GUTTER
        tf, bf = self.f(22, "semibold"), self.f(22)
        h = 2 * pad + (self.lh(tf) + 14 if title else 0) + self.block_h(text, bf, x1 - x0 - 2 * pad, 1.5)
        self.rrect((x0, y, x1, y + h), 32, mix(color, WHITE, 0.9))
        ty = y + pad
        if title:
            self.text((x0 + pad, ty), title, tf, color)
            ty += self.lh(tf) + 14
        self.block((x0 + pad, ty), text, bf, INK, max_w=x1 - x0 - 2 * pad, leading=1.5)
        return y + h

    def _stat_fit(self, value: str, max_w: float):
        """Size for one stat value: the number shrinks until number + small unit fit
        on one line; at the minimum size the value wraps instead (``lines`` > 1)."""
        num, unit = split_number(value)
        size = self.STAT_MAX
        while True:
            nf, uf = self.f(size, "bold"), self.f(max(18, size * 4 // 10), "semibold")
            gap = size * 0.08 if unit else 0
            total = self.tw(num, nf, TRACK) + gap + self.tw(unit, uf)
            if total <= max_w or size <= self.STAT_MIN:
                break
            size -= 4
        lines = 1 if total <= max_w else len(self.wrap(value, nf, max_w, TRACK))
        return num, unit, nf, uf, gap, total, lines

    def stats(self, y: float, stats: list[Stat]) -> float:
        """Big-number row: value large with its unit set small, hairline dividers.
        Returns the bottom."""
        col_w = (self.W - 160) / len(stats)
        inner = col_w - 56
        label_f = self.f(20)
        fits = [self._stat_fit(s.value, inner) for s in stats]
        value_h = max(max(self.STAT_MAX, ft[-1] * self.lh(ft[2], 1.1)) for ft in fits)
        label_y = y + value_h + 30
        h = value_h + 30 + max(self.block_h(s.label, label_f, inner, 1.35) for s in stats)
        for i, (s, (num, unit, nf, uf, gap, total, lines)) in enumerate(zip(stats, fits, strict=True)):
            cx = 80 + col_w * i + col_w / 2
            if i:
                self.vline(80 + col_w * i, y + 8, y + h - 8)
            if lines > 1:
                self.block((cx, y), s.value, nf, INK, max_w=inner, leading=1.1, align="m", track=TRACK)
            else:
                base = y + self.STAT_MAX - nf.size / self.S  # keep baselines level across columns
                x0 = cx - total / 2
                self.text((x0, base), num, nf, INK, track=TRACK)
                if unit:
                    asc_n, asc_u = nf.getmetrics()[0] / self.S, uf.getmetrics()[0] / self.S
                    self.text((x0 + self.tw(num, nf, TRACK) + gap, base + asc_n - asc_u), unit, uf, SUB)
            self.block((cx, label_y), s.label, label_f, SUB, max_w=inner, leading=1.35, align="m")
        return y + h

    def footer(self):
        y = self.H - FOOTER_H
        self.hairline(80, y, self.W - 80)
        self.text((self.W / 2, y + 30), self.spec.footer_text(), self.f(17, "medium"), MUTED,
                  align="m", track=0.02)

    def save(self, path: Path) -> Path:
        self.im.resize((self.W, self.H), Image.LANCZOS).save(path, "JPEG", quality=90, optimize=True)
        return path
