"""One renderer per page type (Strategy registry ``RENDERERS``).

Each layout is a single ``draw(painter, page, assets) -> bottom`` function. The
:func:`page` wrapper runs it once on a dry painter to learn how tall the page must
be, then again on the real canvas, so measuring and drawing can never disagree."""

from __future__ import annotations

import math
from collections.abc import Callable
from functools import wraps

from PIL import Image

from .image_ops import WHITE, accent_band, fit, recolor, tint
from .painter import CLOUD, FOOTER_H, GUTTER, INK, SUB, TOP, TRACK, Painter, mix
from .render_assets import LoadedAssets
from .spec import (
    CalloutsPage,
    ChipsPage,
    DetailSpec,
    FeaturesPage,
    HeroPage,
    LevelsPage,
    ModesPage,
    OemOdmPage,
    ScenesPage,
    SpecTablePage,
    StepsPage,
    TrustPage,
)

GAP = 20          # between cards
SECTION = 80      # between page sections
BOTTOM = 90       # last content → footer hairline


def page(draw: Callable[[Painter, object, LoadedAssets], float]):
    @wraps(draw)
    def render(spec: DetailSpec, pg, a: LoadedAssets) -> Painter:
        bottom = draw(Painter(spec, 1, dry=True), pg, a)
        p = Painter(spec, math.ceil(bottom) + BOTTOM + FOOTER_H)
        draw(p, pg, a)
        p.footer()
        return p
    return render


def _grid(cols: int, width: float) -> float:
    return (width - GAP * (cols - 1)) / cols


def _rows(heights: list[float], cols: int) -> list[float]:
    return [max(heights[r * cols:(r + 1) * cols]) for r in range(math.ceil(len(heights) / cols))]


@page
def r_hero(p: Painter, pg: HeroPage, a: LoadedAssets) -> float:
    W, cx = p.W, p.W / 2
    spec = p.spec
    y = p.eyebrow((cx, TOP - 10), spec.brand, p.accent, size=22, align="m") + 26
    name_f = p.f(p.fit_size(spec.product_name, (96, 84, 72, 60), "bold", W - 160, TRACK), "bold")
    y = p.block((cx, y), spec.product_name, name_f, INK, max_w=W - 160, leading=1.05, align="m", track=TRACK)
    y = p.block((cx, y + 28), pg.tagline, p.f(46, "semibold"), max_w=W - 200, leading=1.12, align="m",
                track=-0.01, gradient=p.gradient)
    if pg.intro:
        y = p.block((cx, y + 30), pg.intro, p.f(26), SUB, max_w=820, leading=1.45, align="m")
    if pg.badges:
        y = p.pills(100, y + 36, pg.badges, p.f(20, "medium"), W - 200, center=True)
    y = p.stage(y + 70, a.hero, W - 280, 680, pad=80)
    if pg.stats:
        y = p.stats(y + SECTION, pg.stats)
    return y


@page
def r_features(p: Painter, pg: FeaturesPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle)
    y = p.stage(y + 70, a.side, W - 320, 540) + GAP
    pad = 44
    idx_f, title_f, body_f = p.f(18, "semibold"), p.f(34, "semibold"), p.f(22)
    col_w = _grid(2, W - 2 * GUTTER)
    items = pg.items
    # two columns; an odd last card spans the full width
    rows = [items[i:i + 2] for i in range(0, len(items) - len(items) % 2, 2)]
    if len(items) % 2:
        rows.append([items[-1]])

    def card_h(it, w) -> float:
        tw = w - 2 * pad
        return (2 * pad + p.lh(idx_f) + 18 + p.block_h(it.title, title_f, tw, 1.15, -0.01)
                + (12 + p.block_h(it.text, body_f, tw, 1.45) if it.text else 0))

    n = 0
    for row in rows:
        w = col_w if len(row) == 2 else W - 2 * GUTTER
        rh = max(card_h(it, w) for it in row)
        x = GUTTER
        for it in row:
            n += 1
            p.rrect((x, y, x + w, y + rh), 32, CLOUD)
            ty = p.eyebrow((x + pad, y + pad), f"{n:02d}", p.accent, size=18) + 18
            ty = p.block((x + pad, ty), it.title, title_f, INK, max_w=w - 2 * pad, leading=1.15, track=-0.01)
            if it.text:
                p.block((x + pad, ty + 12), it.text, body_f, SUB, max_w=w - 2 * pad, leading=1.45)
            x += w + GAP
        y += rh + GAP
    y -= GAP
    if pg.note:
        y = p.block((W / 2, y + SECTION), pg.note, p.f(36, "semibold"), max_w=W - 240, leading=1.2,
                    align="m", track=-0.01, gradient=p.gradient)
    return y


@page
def r_steps(p: Painter, pg: StepsPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle)
    y = p.stage(y + 70, a.side, W - 320, 420, pad=60) + GAP
    cols = 2 if len(pg.steps) in (2, 4) else 3
    cw = _grid(cols, W - 2 * GUTTER)
    pad = 36
    title_f, body_f = p.f(28, "semibold"), p.f(20)
    tw = cw - 2 * pad
    heights = [2 * pad + 44 + 22 + p.block_h(st.title, title_f, tw, 1.2, -0.01)
               + (10 + p.block_h(st.text, body_f, tw, 1.45) if st.text else 0) for st in pg.steps]
    row_h = _rows(heights, cols)
    for i, st in enumerate(pg.steps):
        r, c = divmod(i, cols)
        short = cols - min(cols, len(pg.steps) - r * cols)   # empty slots in this row
        x0 = GUTTER + (c + short / 2) * (cw + GAP)
        y0 = y + sum(h + GAP for h in row_h[:r])
        p.rrect((x0, y0, x0 + cw, y0 + row_h[r]), 32, CLOUD)
        p.number_badge(x0 + pad + 22, y0 + pad + 22, i + 1)
        ty = p.block((x0 + pad, y0 + pad + 44 + 22), st.title, title_f, INK, max_w=tw, leading=1.2, track=-0.01)
        if st.text:
            p.block((x0 + pad, ty + 10), st.text, body_f, SUB, max_w=tw, leading=1.45)
    y += sum(h + GAP for h in row_h) - GAP
    if pg.tip:
        y = p.note_card(y + 40, pg.tip.title or "Tip", pg.tip.text, p.accent)
    return y


@page
def r_levels(p: Painter, pg: LevelsPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle) + 70
    n = len(pg.levels)
    cw = _grid(n, W - 2 * GUTTER)
    pad, meter_h = 32, 150
    label_f, value_f, note_f = p.f(28, "semibold"), p.f(32, "bold"), p.f(20)
    tw = cw - 2 * pad
    label_h = max(p.block_h(lv.label, label_f, tw, 1.2) for lv in pg.levels)
    value_h = max(p.block_h(lv.value, value_f, tw, 1.1, TRACK) for lv in pg.levels)
    note_h = max(p.block_h(lv.note, note_f, tw, 1.4) for lv in pg.levels)
    card_h = 2 * pad + label_h + 36 + meter_h + 36 + value_h + (12 + note_h if note_h else 0)
    bars = max([lv.intensity for lv in pg.levels] + [3])
    for i, lv in enumerate(pg.levels):
        x0 = GUTTER + i * (cw + GAP)
        cx = x0 + cw / 2
        p.rrect((x0, y, x0 + cw, y + card_h), 32, CLOUD)
        p.block((cx, y + pad), lv.label, label_f, INK, max_w=tw, leading=1.2, align="m")
        # level meter: rising rounded bars, filled up to this level's intensity
        aw = min(tw, 200)
        base = y + pad + label_h + 36 + meter_h
        bw = aw / (2 * bars - 1)
        for k in range(bars):
            bh = meter_h * (k + 1) / bars
            bx = cx - aw / 2 + k * 2 * bw
            p.rrect((bx, base - bh, bx + bw, base), min(bw / 2, 8),
                    p.accent if k < lv.intensity else (222, 222, 227))
        p.block((cx, base + 36), lv.value, value_f, INK, max_w=tw, leading=1.1, align="m", track=TRACK)
        if lv.note:
            p.block((cx, y + card_h - pad - note_h), lv.note, note_f, SUB, max_w=tw, leading=1.4, align="m")
    y += card_h
    if pg.footnote:
        y = p.block((W / 2, y + 50), pg.footnote, p.f(24), SUB, max_w=W - 200, leading=1.4, align="m")
    return p.stage(y + 60, a.hero, 500, 360, pad=50)


@page
def r_modes(p: Painter, pg: ModesPage, a: LoadedAssets) -> float:
    """Named operating modes as comparison cards. Category-agnostic: draws only the
    label, the concrete setting and the note — no magnitude graphic, so nothing
    false is implied for products whose modes are not a ranking."""
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle) + 70
    n = len(pg.modes)
    cw = _grid(n, W - 2 * GUTTER)
    pad = 36
    tw = cw - 2 * pad
    label_f, value_f, note_f = p.f(36, "bold"), p.f(24, "semibold"), p.f(20)
    label_h = max(p.block_h(md.label, label_f, tw, 1.1, TRACK) for md in pg.modes)
    value_h = max(p.block_h(md.value, value_f, tw, 1.25) for md in pg.modes)
    note_h = max(p.block_h(md.note, note_f, tw, 1.45) for md in pg.modes)
    card_h = 2 * pad + label_h + (14 + value_h if value_h else 0) + (24 + note_h if note_h else 0)
    for i, md in enumerate(pg.modes):
        x0 = GUTTER + i * (cw + GAP)
        p.rrect((x0, y, x0 + cw, y + card_h), 32, CLOUD)
        ty = p.block((x0 + pad, y + pad), md.label, label_f, INK, max_w=tw, leading=1.1, track=TRACK)
        if md.value:
            p.block((x0 + pad, ty + 14), md.value, value_f, p.accent, max_w=tw, leading=1.25)
        if md.note:
            nty = y + card_h - pad - note_h   # notes line up across the cards
            p.hairline(x0 + pad, nty - 12, x0 + cw - pad)
            p.block((x0 + pad, nty), md.note, note_f, SUB, max_w=tw, leading=1.45)
    y += card_h
    if pg.footnote:
        y = p.block((W / 2, y + 50), pg.footnote, p.f(24), SUB, max_w=W - 200, leading=1.4, align="m")
    return y


@page
def r_callouts(p: Painter, pg: CalloutsPage, a: LoadedAssets) -> float:
    W = p.W
    f = p.f(22, "semibold")
    LH = 40                                   # one label row (line + gap)
    cos = pg.callouts
    img = a.side if pg.image == "side" else a.hero
    sw, sh = p.product_size(img, W - 460, 560)
    px = (W - sw) / 2

    stage_top = p.header(TOP, pg.title, pg.subtitle) + 70
    by = {s: [i for i, c in enumerate(cos) if c.side == s] for s in ("up", "down", "left", "right")}

    def label_x(i: int) -> float:
        w = p.tw(cos[i].label, f)
        return min(max(px + sw * cos[i].x, 90 + w / 2), W - 90 - w / 2)

    def rows_for(idxs: list[int]) -> tuple[dict[int, int], int]:
        """Assign each up/down label a row so no two overlap horizontally."""
        row: dict[int, int] = {}
        lanes: list[list[tuple[float, float]]] = []
        for i in sorted(idxs, key=label_x):
            w = p.tw(cos[i].label, f)
            cx = label_x(i)
            span = (cx - w / 2, cx + w / 2)
            r = 0
            while r < len(lanes) and any(not (span[1] < L - 18 or span[0] > R + 18) for L, R in lanes[r]):
                r += 1
            if r == len(lanes):
                lanes.append([])
            lanes[r].append(span)
            row[i] = r
        return row, len(lanes)

    up_row, up_rows = rows_for(by["up"])
    down_row, down_rows = rows_for(by["down"])
    py = stage_top + 50 + (40 + up_rows * LH if up_rows else 20)

    # left/right: stack labels down the margin so none overlap vertically
    def slots(idxs: list[int]) -> dict[int, float]:
        out: dict[int, float] = {}
        cur = None
        for i in sorted(idxs, key=lambda i: py + sh * cos[i].y):
            ly = py + sh * cos[i].y
            if cur is not None and ly < cur + LH:
                ly = cur + LH
            cur = out[i] = ly
        return out

    side_ly = {**slots(by["left"]), **slots(by["right"])}
    side_bottom = max((v + 20 for v in side_ly.values()), default=0)
    down_band = (30 + down_rows * LH) if down_rows else 0
    stage_bottom = max(py + sh + down_band + 60, side_bottom + 40)

    p.rrect((GUTTER, stage_top, W - GUTTER, stage_bottom), 40, CLOUD)
    p.product(img, W / 2, py, (sw, sh))
    lead = (150, 150, 155)
    for i, c in enumerate(cos):
        x, y0 = px + sw * c.x, py + sh * c.y
        if c.side == "up":
            ex, ey = label_x(i), py - 40 - up_row[i] * LH
            p.line([(x, y0), (ex, ey)], lead)
            p.text((ex, ey - 8), c.label, f, INK, align="m", valign="s")
        elif c.side == "down":
            ex, ey = label_x(i), py + sh + 30 + down_row[i] * LH
            p.line([(x, y0), (ex, ey)], lead)
            p.text((ex, ey + 8), c.label, f, INK, align="m")
        elif c.side == "left":
            ly = side_ly[i]
            lx = min(90 + p.tw(c.label, f) + 12, x - 20)
            p.line([(x, y0), (lx, ly)], lead)
            p.text((90, ly), c.label, f, INK, valign="m")
        else:
            ly = side_ly[i]
            lx = max(W - 90 - p.tw(c.label, f) - 12, x + 20)
            p.line([(x, y0), (lx, ly)], lead)
            p.text((W - 90, ly), c.label, f, INK, align="r", valign="m")
        p.circle(x, y0, 9, fill=p.accent, outline=WHITE, width=3)
    y = stage_bottom
    if pg.stats:
        y = p.stats(y + SECTION, pg.stats)
    if pg.summary:
        y = p.block((W / 2, y + 60), pg.summary, p.f(26), SUB, max_w=W - 200, leading=1.45, align="m")
    return y


@page
def r_chips(p: Painter, pg: ChipsPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle) + 70
    tone = {"positive": (p.positive, "check"), "negative": (p.negative, "cross"), "neutral": (INK, None)}
    for g in pg.groups:
        color, mark = tone[g.tone]
        y = p.eyebrow((80, y), g.heading, color, size=18) + 20
        y = p.pills(80, y, g.items, p.f(22, "medium"), W - 160,
                    fill=CLOUD if mark is None else mix(color, WHITE, 0.9),
                    mark=mark, mark_color=color) + 56
    y -= 56
    if pg.notice:
        y = p.note_card(y + 60, pg.notice.title or "Notice", pg.notice.text, p.accent)
    return y


def _crop_3x4(src: Image.Image) -> Image.Image:
    w, h = src.size
    if w / h < 3 / 4:  # tall: keep upper-middle
        th = int(w * 4 / 3)
        yy = int((h - th) * 0.35)
        return src.crop((0, yy, w, yy + th))
    tw = int(h * 3 / 4)  # wide/square: centre crop
    xx = (w - tw) // 2
    return src.crop((xx, 0, xx + tw, h))


@page
def r_scenes(p: Painter, pg: ScenesPage, a: LoadedAssets) -> float:
    y = p.header(TOP, pg.title, pg.subtitle) + 70
    scenes = a.scenes[:6]
    cw = _grid(2, p.W - 2 * GUTTER)
    ch = cw * 4 / 3
    cap_f = p.f(28, "semibold")
    for i, (cap, src) in enumerate(scenes):
        r, c = divmod(i, 2)
        x0, y0 = GUTTER + c * (cw + GAP), y + r * (ch + GAP)
        p.photo(_crop_3x4(src), (x0, y0, x0 + cw, y0 + ch), 28)
        if cap:
            p.scrim((x0, y0 + ch * 0.62, x0 + cw, y0 + ch), p.dark, 28)
            cap_h = p.block_h(cap, cap_f, cw - 64, 1.15, -0.01)
            p.block((x0 + 32, y0 + ch - 32 - cap_h), cap, cap_f, WHITE, max_w=cw - 64, leading=1.15, track=-0.01)
    rows = math.ceil(len(scenes) / 2)
    return y + rows * (ch + GAP) - GAP if rows else y


@page
def r_spec_table(p: Painter, pg: SpecTablePage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title) + 70
    name_f, val_f = p.f(21, "medium"), p.f(22)
    x0, x1, pad, vpad = GUTTER, W - GUTTER, 44, 24
    name_w = 300
    val_x = x0 + pad + name_w + 32
    val_w = x1 - pad - val_x
    heights = [2 * vpad + max(p.block_h(r.name, name_f, name_w, 1.4), p.block_h(r.value, val_f, val_w, 1.4))
               for r in pg.rows]
    p.rrect((x0, y, x1, y + sum(heights) + 24), 32, CLOUD)
    yy = y + 12
    for i, (r, h) in enumerate(zip(pg.rows, heights, strict=True)):
        if i:
            p.hairline(x0 + pad, yy, x1 - pad)
        p.block((x0 + pad, yy + vpad), r.name, name_f, SUB, max_w=name_w, leading=1.4)
        p.block((val_x, yy + vpad), r.value, val_f, INK, max_w=val_w, leading=1.4)
        yy += h
    return yy + 12


@page
def r_oem_odm(p: Painter, pg: OemOdmPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title, pg.subtitle) + SECTION
    if pg.colors:
        y = p.section_title(y, "Custom Colours", "Match your brand palette – the body can be moulded in your colour.")
        n = len(pg.colors)
        cw = _grid(n, W - 2 * GUTTER)
        img_h = 200
        base = fit(a.hero, int((cw - 40) * p.S), img_h * p.S)
        band = accent_band(base) if not p.dry else None
        name_f = p.f(20, "medium")
        card_h = 36 + img_h + 24 + p.lh(name_f) + 32
        for i, c in enumerate(pg.colors):
            # The first swatch is the standard colour = the real render, untouched.
            if i == 0 or p.dry:
                v = base
            elif band is None:   # black/grey product: tint instead of hue-shift
                v = tint(base, c.hue, c.saturation, c.brightness)
            else:
                v = recolor(base, c.hue, c.saturation, c.brightness, band=band)
            x0 = GUTTER + i * (cw + GAP)
            size = p.product_size(v, cw - 40, img_h)
            p.rrect((x0, y, x0 + cw, y + card_h), 28, CLOUD)
            p.product(v, x0 + cw / 2, y + 36 + img_h - size[1], size)
            p.text((x0 + cw / 2, y + 36 + img_h + 24), c.name, name_f, INK, align="m")
        y += card_h + SECTION

    y = p.section_title(y, "Custom Logo & Packaging")
    left_x1 = 560
    size = p.product_size(a.hero, left_x1 - GUTTER - 80, 330)
    left_bottom = y + size[1] + 120
    p.rrect((GUTTER, y, left_x1, left_bottom), 40, CLOUD)
    pcx = (GUTTER + left_x1) / 2
    p.product(a.hero, pcx, y + 70, size)
    lw = min(80, size[0] * 0.35)
    lx, ly = pcx, y + 70 + size[1] * 0.4
    p.rrect((lx - lw, ly - 22, lx + lw, ly + 22), 8, outline=p.accent, width=2)
    p.text((lx, ly - 30), pg.logo_label, p.f(15, "semibold"), p.accent, align="m", valign="s", track=0.08)

    sx0, sx1, pad = left_x1 + GAP, W - GUTTER, 28
    title_f, body_f = p.f(24, "semibold"), p.f(19)
    cy = y
    for it in pg.services:
        tw = sx1 - sx0 - 2 * pad
        h = 2 * pad + p.block_h(it.title, title_f, tw, 1.2) + (8 + p.block_h(it.text, body_f, tw, 1.45) if it.text else 0)
        p.rrect((sx0, cy, sx1, cy + h), 24, CLOUD)
        ty = p.block((sx0 + pad, cy + pad), it.title, title_f, INK, max_w=tw, leading=1.2)
        if it.text:
            p.block((sx0 + pad, ty + 8), it.text, body_f, SUB, max_w=tw, leading=1.45)
        cy += h + 12
    y = max(left_bottom, cy - 12 if pg.services else y)

    if pg.process:
        y = p.section_title(y + SECTION, "How We Work With You")
        n = len(pg.process)
        cw = (W - 160) / n
        step_f = p.f(21, "medium")
        text_h = 0.0
        for i, t in enumerate(pg.process):
            cx = 80 + i * cw + cw / 2
            if i < n - 1:
                p.hairline(cx + 40, y + 26, cx + cw - 40)
            p.number_badge(cx, y + 26, i + 1, r=26)
            text_h = max(text_h, p.block((cx, y + 72), t, step_f, INK, max_w=cw - 24, leading=1.35, align="m") - y)
        y += text_h
    if pg.note:
        y = p.note_card(y + 60, "", pg.note, p.accent)
    return y


@page
def r_trust(p: Painter, pg: TrustPage, a: LoadedAssets) -> float:
    W = p.W
    y = p.header(TOP, pg.title) + 70
    n = len(pg.badges)
    cw = _grid(n, W - 2 * GUTTER)
    pad = 32
    tw = cw - 2 * pad
    value_f, label_f = p.f(p.fit_size(" ".join(b.value for b in pg.badges), (44, 36, 30), "bold", tw, TRACK),
                           "bold"), p.f(20)
    value_h = max(p.block_h(b.value, value_f, tw, 1.1, TRACK) for b in pg.badges)
    label_h = max(p.block_h(b.label, label_f, tw, 1.4) for b in pg.badges)
    card_h = 2 * pad + 44 + 22 + value_h + 10 + label_h
    for i, b in enumerate(pg.badges):
        x0 = GUTTER + i * (cw + GAP)
        cx = x0 + cw / 2
        p.rrect((x0, y, x0 + cw, y + card_h), 32, CLOUD)
        p.circle(cx, y + pad + 22, 22, outline=p.accent, width=2.5)
        p.mark("check", cx, y + pad + 22, 18, p.accent)
        p.block((cx, y + pad + 44 + 22), b.value, value_f, INK, max_w=tw, leading=1.1, align="m", track=TRACK)
        p.block((cx, y + pad + 44 + 22 + value_h + 10), b.label, label_f, SUB, max_w=tw, leading=1.4, align="m")
    y += card_h
    if pg.footnote:
        y = p.block((80, y + 24), pg.footnote, p.f(19), SUB, max_w=W - 160, leading=1.45)
    if pg.box_items:
        y = p.section_title(y + SECTION, pg.box_title)
        left_x1 = 560
        stage_bottom = p.stage(y, a.hero, 400, 300, pad=50, x1=left_x1)
        item_f = p.f(24)
        lx0 = left_x1 + 60
        iy = y
        for k, ln in enumerate(pg.box_items):
            if k:
                p.hairline(lx0, iy, W - GUTTER)
            p.circle(lx0 + 5, iy + 22 + p.lh(item_f) / 2, 5, fill=p.accent)
            iy = p.block((lx0 + 26, iy + 22), ln, item_f, INK, max_w=W - GUTTER - lx0 - 26, leading=1.35) + 22
        y = max(stage_bottom, iy)
    if pg.notice:
        y = p.note_card(y + 60, pg.notice.title or "Notice", pg.notice.text, p.negative)
    return y


RENDERERS: dict[str, Callable] = {
    "hero": r_hero, "features": r_features, "steps": r_steps, "levels": r_levels,
    "modes": r_modes, "callouts": r_callouts, "chips": r_chips, "scenes": r_scenes,
    "spec_table": r_spec_table, "oem_odm": r_oem_odm, "trust": r_trust,
}
