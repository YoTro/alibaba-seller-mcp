"""Drawing primitives shared by every page renderer (fonts, wrapped text, chips,
cards, headers, footer) on a fixed-width canvas."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .fonts import font as _font
from .image_ops import DARK_TEXT, GRAY, LIGHT, RGB, WHITE, hex_rgb
from .spec import DetailSpec


class Painter:
    def __init__(self, spec: DetailSpec, height: int, bg: RGB = WHITE):
        self.spec = spec
        self.W = spec.width
        self.H = height
        self.im = Image.new("RGB", (self.W, height), bg)
        self.d = ImageDraw.Draw(self.im)
        t = spec.theme
        self.primary, self.dark = hex_rgb(t.primary), hex_rgb(t.dark)
        self.positive, self.negative = hex_rgb(t.positive), hex_rgb(t.negative)
        self._font_dir = t.font_dir

    # fonts / text
    def f(self, size: int, weight: str = "regular"):
        return _font(size, weight, self._font_dir)

    def tw(self, text: str, fnt) -> float:
        return self.d.textlength(text, font=fnt)

    def wrap(self, text: str, fnt, max_w: int) -> list[str]:
        lines, cur = [], ""
        for wd in text.split():
            t = (cur + " " + wd).strip()
            if self.tw(t, fnt) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines

    @classmethod
    def ruler(cls, spec: DetailSpec) -> "Painter":
        """A throwaway 1px canvas for measuring text before the real page height is
        known. Every box on a page is sized from its own copy, so the renderer has
        to wrap the text once up front to learn how tall the page has to be."""
        return cls(spec, 1)

    def block_h(self, text: str, fnt, max_w: int, spacing: int = 8) -> int:
        """Height :meth:`block` will occupy for ``text`` (0 when there is none)."""
        if not text:
            return 0
        return len(self.wrap(text, fnt, max_w)) * (fnt.size + spacing)

    def header_h(self, title: str, subtitle: str = "") -> int:
        """Height :meth:`header` consumes below its starting y."""
        return (24 + self.block_h(title, self.f(46, "black"), self.W - 160, 8) + 14
                + self.block_h(subtitle, self.f(24), self.W - 200) + 20)

    def text(self, xy, text: str, fnt, fill: RGB = DARK_TEXT, anchor: str = "la"):
        self.d.text(xy, text, font=fnt, fill=fill, anchor=anchor)

    def block(self, xy, text: str, fnt, fill: RGB = DARK_TEXT, max_w: int | None = None,
              spacing: int = 8, center: bool = False) -> int:
        x, y = xy
        max_w = max_w or self.W - 160
        for ln in self.wrap(text, fnt, max_w):
            self.text((x, y), ln, fnt, fill, anchor="ma" if center else "la")
            y += fnt.size + spacing
        return y

    # layout pieces
    def header(self, y: int, title: str, subtitle: str = "") -> int:
        cx = self.W // 2
        self.d.rectangle((cx - 40, y, cx + 40, y + 6), fill=self.primary)
        # a long title wraps rather than running off both edges of the canvas
        y = self.block((cx, y + 24), title, self.f(46, "black"), self.dark,
                       max_w=self.W - 160, center=True) + 14
        if subtitle:
            y = self.block((cx, y), subtitle, self.f(24), GRAY, max_w=self.W - 200, center=True)
        return y + 20

    def section_title(self, y: int, title: str, subtitle: str = "") -> int:
        self.text((80, y), title, self.f(34, "black"), self.dark)
        y += 46
        if subtitle:
            y = self.block((80, y), subtitle, self.f(22), GRAY)
        return y + 14

    def chip(self, xy, text: str, fnt, fill: RGB, fg: RGB = WHITE, pad=(22, 12), symbol: str | None = None) -> int:
        x, y = xy
        sf = self.f(fnt.size, "symbol")
        sw = self.tw(symbol + " ", sf) if symbol else 0
        w = self.tw(text, fnt) + sw + 2 * pad[0]
        self.d.rounded_rectangle((x, y, x + w, y + fnt.size + 2 * pad[1]), radius=30, fill=fill)
        if symbol:
            self.text((x + pad[0], y + pad[1] - 2), symbol, sf, fg)
        self.text((x + pad[0] + sw, y + pad[1] - 2), text, fnt, fg)
        return int(x + w)

    def card(self, box, fill: RGB = LIGHT, radius: int = 24):
        self.d.rounded_rectangle(box, radius=radius, fill=fill)

    def badge(self, xy, n: int, r: int = 26):
        x, y = xy
        self.d.ellipse((x - r, y - r, x + r, y + r), fill=self.primary)
        self.text((x, y - 1), str(n), self.f(28, "black"), WHITE, anchor="mm")

    def paste(self, im: Image.Image, xy):
        self.im.paste(im, xy)

    def footer(self):
        self.d.rectangle((0, self.H - 60, self.W, self.H), fill=self.dark)
        self.text((self.W // 2, self.H - 30), self.spec.footer_text(), self.f(20, "bold"), WHITE, anchor="mm")

    def save(self, path: Path) -> Path:
        self.im.save(path, "JPEG", quality=88, optimize=True)
        return path
