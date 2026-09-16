"""One renderer per page type (Strategy registry ``RENDERERS``).

Each function takes the spec, one page model and the loaded assets, and returns a
:class:`Painter` holding the finished canvas."""

from __future__ import annotations

import math
from typing import Callable

from PIL import Image

from .image_ops import DARK_TEXT, GRAY, LIGHT, LINE, TINT, WHITE, accent_band, fit, recolor, tint
from .painter import Painter
from .render_assets import LoadedAssets
from .spec import (
    CalloutsPage, ChipsPage, DetailSpec, FeaturesPage, HeroPage, LevelsPage, ModesPage,
    OemOdmPage, ScenesPage, SpecTablePage, StepsPage, TrustPage,
)


def r_hero(spec: DetailSpec, page: HeroPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    badge_f, tag_f, intro_f, label_f = m.f(26, "bold"), m.f(34, "bold"), m.f(26), m.f(22)

    # badges flow onto as many rows as they need instead of running off the banner
    badge_rows: list[list[str]] = []
    x = 80
    for b in page.badges:
        w = m.tw(b, badge_f) + 44          # chip() pads 22px each side
        if not badge_rows or x + w > W - 80:
            badge_rows.append([])
            x = 80
        badge_rows[-1].append(b)
        x += w + 16
    tag_h = m.block_h(page.tagline, tag_f, W - 160, 6)
    intro_h = m.block_h(page.intro, intro_f, W - 160, 4)
    banner_h = (250 + tag_h + (14 + len(badge_rows) * 66 if badge_rows else 0)
                + (6 + intro_h if page.intro else 0) + 40)

    hero = fit(a.hero, W - 200, 640)
    label_w = (W - 160) // max(1, len(page.stats)) - 24
    value_f = m.f(40, "black")
    value_h = max((m.block_h(s.value, value_f, label_w, 6) for s in page.stats), default=0)
    label_h = max((m.block_h(s.label, label_f, label_w, 4) for s in page.stats), default=0)
    H = banner_h + 40 + hero.size[1] + (40 + value_h + 12 + label_h + 30 if page.stats else 30) + 60

    p = Painter(spec, H)
    p.d.rectangle((0, 0, W, banner_h), fill=p.dark)
    p.text((80, 90), spec.brand, p.f(40, "black"), p.primary)
    name_font = p.f(72 if p.tw(spec.product_name, p.f(72, "black")) < W - 160 else 56, "black")
    p.text((80, 150), spec.product_name, name_font, WHITE)
    y = p.block((80, 250), page.tagline, tag_f, (200, 200, 200), max_w=W - 160, spacing=6)
    if badge_rows:
        y += 14
        for row in badge_rows:
            x = 80
            for b in row:
                x = p.chip((x, y), b, badge_f, p.primary) + 16
            y += 66
    if page.intro:
        p.block((80, y + 6), page.intro, intro_f, (220, 220, 220), max_w=W - 160, spacing=4)
    hy = banner_h + 40
    p.paste(hero, ((W - hero.size[0]) // 2, hy))
    if page.stats:
        sy = hy + hero.size[1] + 40
        cw = (W - 160) // len(page.stats)
        for i, s in enumerate(page.stats):
            cx = 80 + cw * i + cw // 2
            p.block((cx, sy), s.value, value_f, p.primary, max_w=label_w, center=True, spacing=6)
            p.block((cx, sy + value_h + 12), s.label, label_f, GRAY, max_w=label_w, center=True, spacing=4)
    p.footer()
    return p


def r_features(spec: DetailSpec, page: FeaturesPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    title_f, body_f = m.f(28, "bold"), m.f(20)
    text_w = W - 60 - 720
    # each card is as tall as its own title + body needs
    title_h = [m.block_h(it.title, title_f, text_w, 6) for it in page.items]
    body_h = [m.block_h(it.text, body_f, text_w, 4) for it in page.items]
    card_h = [max(110, 16 + t + 8 + b + 16) for t, b in zip(title_h, body_h)]
    side = fit(a.side, 560, 420)
    H = (60 + m.header_h(page.title, page.subtitle)
         + max(sum(h + 16 for h in card_h) + 10, side.size[1] + 20) + 30
         + (100 if page.note else 20) + 60)
    p = Painter(spec, H)
    y = p.header(60, page.title, page.subtitle)
    p.paste(side, (60, y + 20))
    cy = y + 10
    for it, th, ch in zip(page.items, title_h, card_h):
        p.card((660, cy, W - 60, cy + ch))
        p.d.rectangle((660, cy, 672, cy + ch), fill=p.primary)
        p.block((700, cy + 16), it.title, title_f, p.dark, max_w=text_w, spacing=6)
        p.block((700, cy + 24 + th), it.text, body_f, GRAY, max_w=text_w, spacing=4)
        cy += ch + 16
    y = max(cy, y + 20 + side.size[1]) + 30
    if page.note:
        p.d.rectangle((60, y, W - 60, y + 2), fill=LINE)
        p.block((W // 2, y + 40), page.note, p.f(24, "bold"), DARK_TEXT, max_w=W - 200, center=True)
    p.footer()
    return p


def r_steps(spec: DetailSpec, page: StepsPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    rows = math.ceil(len(page.steps) / 3)
    cw = (W - 160 - 40) // 3
    title_f, body_f = m.f(30, "bold"), m.f(21)
    title_w, body_w = cw - 100, cw - 48
    # measure every card, then give the cards in a row the tallest one's height so
    # the row still lines up while no text can spill out of its box
    title_h = [m.block_h(st.title, title_f, title_w, 5) for st in page.steps]
    card_h = [max(84, 24 + t + 14) + m.block_h(st.text, body_f, body_w, 5) + 22
              for st, t in zip(page.steps, title_h)]
    row_h = [max(card_h[r * 3:(r + 1) * 3]) for r in range(rows)]
    side = fit(a.side, 700, 460)
    tip_h = 62 + m.block_h(page.tip.text, body_f, W - 220, 5) + 26 if page.tip else 0
    H = (60 + m.header_h(page.title, page.subtitle) + side.size[1] + 30
         + sum(h + 20 for h in row_h) + (tip_h + 40 if page.tip else 20) + 60)

    p = Painter(spec, H)
    y = p.header(60, page.title, page.subtitle)
    p.paste(side, ((W - side.size[0]) // 2, y))
    y += side.size[1] + 30
    for i, st in enumerate(page.steps):
        col, row = i % 3, i // 3
        x0 = 80 + col * (cw + 20)
        y0 = y + sum(h + 20 for h in row_h[:row])
        p.card((x0, y0, x0 + cw, y0 + row_h[row]))
        p.badge((x0 + 40, y0 + 40), i + 1)
        p.block((x0 + 80, y0 + 24), st.title, title_f, p.dark, max_w=title_w, spacing=5)
        p.block((x0 + 24, y0 + max(84, 24 + title_h[i] + 14)), st.text, body_f, GRAY,
                max_w=body_w, spacing=5)
    y += sum(h + 20 for h in row_h) + 20
    if page.tip:
        p.card((80, y, W - 80, y + tip_h), fill=TINT)
        p.text((110, y + 20), page.tip.title or "Tip", p.f(26, "bold"), p.primary)
        p.block((110, y + 62), page.tip.text, body_f, DARK_TEXT, max_w=W - 220, spacing=5)
    p.footer()
    return p


def r_levels(spec: DetailSpec, page: LevelsPage, a: LoadedAssets) -> Painter:
    p = Painter(spec, 1150)
    W = p.W
    y = p.header(60, page.title, page.subtitle)
    n = len(page.levels)
    cw = (W - 160 - 20 * (n - 1)) // n
    bars = max([lv.intensity for lv in page.levels] + [3])
    for i, lv in enumerate(page.levels):
        x0 = 80 + i * (cw + 20)
        p.card((x0, y, x0 + cw, y + 560))
        p.block((x0 + cw // 2, y + 24), lv.label, p.f(36, "black"), p.primary,
                max_w=cw - 30, center=True, spacing=4)
        # clean level meter: rising rounded bars, filled up to this mode's intensity
        cx = x0 + cw // 2
        aw, ah, base = 220.0, 190.0, y + 360
        bw = aw / (2 * bars - 1)
        for k in range(bars):
            bh = ah * (k + 1) / bars
            bx = cx - aw / 2 + k * 2 * bw
            col = p.primary if k < lv.intensity else LIGHT
            p.d.rounded_rectangle((bx, base - bh, bx + bw, base), radius=bw / 3, fill=col)
        p.block((cx, y + 400), lv.value, p.f(28, "bold"), p.dark, max_w=cw - 30, center=True, spacing=4)
        if lv.note:
            p.block((cx, y + 450), lv.note, p.f(21), GRAY, max_w=cw - 40, center=True)
        if i < n - 1:
            ax = x0 + cw + 10
            p.d.polygon([(ax - 6, y + 270), (ax + 14, y + 280), (ax - 6, y + 290)], fill=p.primary)
    y += 600
    if page.footnote:
        p.block((W // 2, y), page.footnote, p.f(24, "bold"), DARK_TEXT, max_w=W - 200, center=True)
    side = fit(a.side, 500, 250)
    p.paste(side, ((W - side.size[0]) // 2, y + 60))
    p.footer()
    return p


def r_modes(spec: DetailSpec, page: ModesPage, a: LoadedAssets) -> Painter:
    """Named operating modes as comparison cards. Category-agnostic: draws only the
    label, the concrete setting (``value`` pill) and ``note`` — no magnitude graphic,
    so nothing false is implied for products whose modes are not a ranking."""
    m = Painter.ruler(spec)
    W = spec.width
    n = len(page.modes)
    cw = (W - 160 - 20 * (n - 1)) // n
    label_f, value_f, note_f = m.f(34, "black"), m.f(30, "bold"), m.f(21)
    label_h = max((m.block_h(md.label, label_f, cw - 30, 6) for md in page.modes), default=0)
    note_h = max((m.block_h(md.note, note_f, cw - 40, 6) for md in page.modes if md.note), default=0)
    has_pill = any(md.value for md in page.modes)
    card_h = 40 + label_h + (28 + 60 if has_pill else 10) + (28 + note_h if note_h else 0) + 40
    H = (60 + m.header_h(page.title, page.subtitle) + card_h + 30
         + (60 if page.footnote else 0) + 60)
    p = Painter(spec, H)
    y = p.header(60, page.title, page.subtitle)
    for i, md in enumerate(page.modes):
        x0 = 80 + i * (cw + 20)
        p.card((x0, y, x0 + cw, y + card_h))
        cx = x0 + cw // 2
        yy = p.block((cx, y + 40), md.label, label_f, p.primary, max_w=cw - 30, center=True, spacing=6)
        if md.value:
            pill_w = min(cw - 48, m.tw(md.value, value_f) + 56)
            px0 = cx - pill_w // 2
            py0 = yy + 24
            p.d.rounded_rectangle((px0, py0, px0 + pill_w, py0 + 60), radius=30, fill=TINT)
            p.block((cx, py0 + 15), md.value, value_f, p.dark, max_w=pill_w - 24, center=True)
            yy = py0 + 60
        if md.note:
            p.block((cx, yy + 28), md.note, note_f, GRAY, max_w=cw - 40, center=True, spacing=6)
        if i < n - 1:
            ax, ay = x0 + cw + 10, y + card_h // 2
            p.d.polygon([(ax - 6, ay - 10), (ax + 14, ay), (ax - 6, ay + 10)], fill=p.primary)
    y += card_h + 30
    if page.footnote:
        p.block((W // 2, y), page.footnote, p.f(24, "bold"), DARK_TEXT, max_w=W - 200, center=True)
    p.footer()
    return p


def r_callouts(spec: DetailSpec, page: CalloutsPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    f = m.f(24, "bold")
    LH = 42                                   # one label row (line + gap)
    cos = page.callouts

    # stats / summary sizing (unchanged)
    stat_w = (W - 160) // max(1, len(page.stats)) - 30
    stat_val_f = m.f(34, "black")
    stat_val_h = max((m.block_h(s.value, stat_val_f, stat_w, 6) for s in page.stats), default=0)
    stat_h = max((22 + stat_val_h + 16 + m.block_h(s.label, m.f(19), stat_w, 2) + 18
                  for s in page.stats), default=0) if page.stats else 0
    summary_h = m.block_h(page.summary, m.f(23), W - 200, 8)

    # image geometry — horizontal placement is independent of the label bands, so
    # fit it first (wide side gutters leave room for left/right labels).
    img = fit(a.side if page.image == "side" else a.hero, W - 460, 560)
    sw, sh = img.size
    px = (W - sw) // 2

    by = {s: [i for i, c in enumerate(cos) if c.side == s] for s in ("up", "down", "left", "right")}

    def label_x(i: int) -> float:
        w = m.tw(cos[i].label, f)
        return min(max(px + sw * cos[i].x, 80 + w / 2), W - 80 - w / 2)

    def rows_for(idxs: list[int]) -> tuple[dict[int, int], int]:
        """Assign each up/down label a row so no two overlap horizontally."""
        row: dict[int, int] = {}
        lanes: list[list[tuple[float, float]]] = []
        for i in sorted(idxs, key=label_x):
            w = m.tw(cos[i].label, f)
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
    up_band = (40 + up_rows * LH) if up_rows else 90
    py = 60 + m.header_h(page.title, page.subtitle) + up_band

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

    down_band = (30 + down_rows * LH + 16) if down_rows else 0
    stats_y = max(py + sh + down_band + 30, side_bottom + 20)
    H = stats_y + (stat_h + 40 if page.stats else 0) + (summary_h + 40 if page.summary else 0) + 70

    p = Painter(spec, H)
    p.header(60, page.title, page.subtitle)
    p.paste(img, (px, py))
    for i, c in enumerate(cos):
        x, y0 = px + int(sw * c.x), py + int(sh * c.y)
        p.d.ellipse((x - 9, y0 - 9, x + 9, y0 + 9), fill=p.primary, outline=WHITE, width=3)
        if c.side == "up":
            ex, ey = int(label_x(i)), py - 40 - up_row[i] * LH
            p.d.line((x, y0, ex, ey), fill=p.primary, width=3)
            p.text((ex, ey - 34), c.label, f, p.dark, anchor="ma")
        elif c.side == "down":
            ex, ey = int(label_x(i)), py + sh + 30 + down_row[i] * LH
            p.d.line((x, y0, ex, ey), fill=p.primary, width=3)
            p.text((ex, ey + 8), c.label, f, p.dark, anchor="ma")
        elif c.side == "left":
            ly = side_ly[i]
            lx = min(80 + int(m.tw(c.label, f)) + 12, x - 20)
            p.d.line((x, y0, lx, ly), fill=p.primary, width=3)
            p.text((80, ly), c.label, f, p.dark, anchor="lm")
        else:
            ly = side_ly[i]
            lx = max(W - 80 - int(m.tw(c.label, f)) - 12, x + 20)
            p.d.line((x, y0, lx, ly), fill=p.primary, width=3)
            p.text((W - 80, ly), c.label, f, p.dark, anchor="rm")
    y = stats_y
    if page.stats:
        cw = (W - 160) // len(page.stats)
        for i, s in enumerate(page.stats):
            x0 = 80 + i * cw
            p.card((x0 + 6, y, x0 + cw - 6, y + stat_h))
            p.block((x0 + cw // 2, y + 22), s.value, stat_val_f, p.primary,
                    max_w=stat_w, center=True, spacing=6)
            p.block((x0 + cw // 2, y + 38 + stat_val_h), s.label, p.f(19), GRAY,
                    max_w=stat_w, center=True, spacing=2)
        y += stat_h + 40
    if page.summary:
        p.block((W // 2, y), page.summary, p.f(23), DARK_TEXT, max_w=W - 200, center=True)
    p.footer()
    return p


def r_chips(spec: DetailSpec, page: ChipsPage, a: LoadedAssets) -> Painter:
    # measure first so the canvas height fits the content
    probe = Painter.ruler(spec)
    f = probe.f(24, "bold")

    def rows_for(items: list[str]) -> int:
        x, rows = 80, 1
        for t in items:
            w = probe.tw(t, f) + 60 + 30
            if x + w > spec.width - 80:
                x, rows = 80, rows + 1
            x += w + 14
        return rows

    body = sum(50 + rows_for(g.items) * 70 + 40 for g in page.groups)
    notice_h = 66 + probe.block_h(page.notice.text, probe.f(22), spec.width - 220) + 26 if page.notice else 0
    H = 60 + probe.header_h(page.title, page.subtitle) + body + (notice_h + 50 if page.notice else 20) + 60
    p = Painter(spec, H)
    W = p.W
    y = p.header(60, page.title, page.subtitle)
    tone = {"positive": (p.positive, "✔"), "negative": (p.negative, "✖"), "neutral": (p.primary, None)}
    for g in page.groups:
        color, sym = tone[g.tone]
        p.text((80, y), g.heading.upper(), p.f(26, "black"), color)
        y += 50
        x, row_y = 80, y
        for t in g.items:
            w = p.tw(t, f) + 60 + (30 if sym else 0)
            if x + w > W - 80:
                x, row_y = 80, row_y + 70
            x = p.chip((x, row_y), t, f, color, symbol=sym) + 14
        y = row_y + 110
    if page.notice:
        p.card((80, y, W - 80, y + notice_h), fill=TINT)
        p.text((110, y + 22), page.notice.title or "Notice", p.f(28, "bold"), p.primary)
        p.block((110, y + 66), page.notice.text, p.f(22), DARK_TEXT, max_w=W - 220)
    p.footer()
    return p


def r_scenes(spec: DetailSpec, page: ScenesPage, a: LoadedAssets) -> Painter:
    scenes = a.scenes[:6]
    cols = 2
    cw = (spec.width - 160 - 20) // cols
    ch = int(cw * 4 / 3)
    rows = max(1, math.ceil(len(scenes) / cols))
    H = 230 + rows * (ch + 70) + 60
    p = Painter(spec, H)
    y = p.header(60, page.title, page.subtitle)
    for i, (cap, src) in enumerate(scenes):
        col, row = i % cols, i // cols
        x0, y0 = 80 + col * (cw + 20), y + row * (ch + 70)
        w, h = src.size
        if w / h < 3 / 4:  # tall: keep upper-middle
            th = int(w * 4 / 3)
            yy = int((h - th) * 0.35)
            crop = src.crop((0, yy, w, yy + th))
        else:              # wide/square: centre crop to 3:4
            tw_ = int(h * 3 / 4)
            xx = (w - tw_) // 2
            crop = src.crop((xx, 0, xx + tw_, h))
        p.paste(crop.resize((cw, ch), Image.LANCZOS), (x0, y0))
        if cap:
            p.d.rectangle((x0, y0 + ch, x0 + cw, y0 + ch + 50), fill=p.dark)
            p.text((x0 + cw // 2, y0 + ch + 25), cap, p.f(24, "bold"), WHITE, anchor="mm")
    p.footer()
    return p


def r_spec_table(spec: DetailSpec, page: SpecTablePage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    name_f, val_f = m.f(23, "bold"), m.f(23)
    name_w, val_w = 300, W - 110 - 430
    row_h = [max(66, 20 + max(m.block_h(r.name, name_f, name_w, 6),
                              m.block_h(r.value, val_f, val_w, 6)) + 14) for r in page.rows]
    H = 60 + m.header_h(page.title) + sum(row_h) + 50 + 60
    p = Painter(spec, H)
    y = p.header(60, page.title)
    for i, r in enumerate(page.rows):
        p.d.rectangle((80, y, W - 80, y + row_h[i]), fill=LIGHT if i % 2 == 0 else WHITE)
        p.block((110, y + 20), r.name, name_f, p.dark, max_w=name_w, spacing=6)
        p.block((430, y + 20), r.value, val_f, DARK_TEXT, max_w=val_w, spacing=6)
        y += row_h[i]
    p.d.rectangle((80, y, W - 80, y + 2), fill=LINE)
    p.footer()
    return p


def r_oem_odm(spec: DetailSpec, page: OemOdmPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    W = spec.width
    svc_f = m.f(18)
    svc_w = W - 80 - 672
    svc_title_f = m.f(23, "bold")
    svc_title_h = [m.block_h(it.title, svc_title_f, svc_w, 6) for it in page.services]
    svc_h = [max(72, 10 + t + 6 + m.block_h(it.text, svc_f, svc_w, 4) + 12)
             for it, t in zip(page.services, svc_title_h)]
    note_h = 24 + m.block_h(page.note, m.f(24, "bold"), W - 220, 10) + 24 if page.note else 0
    # the dark banner grows with its title / subtitle instead of clipping them
    title_f, sub_f = m.f(56, "black"), m.f(24)
    banner_title_h = m.block_h(page.title, title_f, W - 160, 8)
    banner_h = max(260, 70 + banner_title_h + 10 + m.block_h(page.subtitle, sub_f, W - 200, 6) + 40)
    H = banner_h + 40
    H += 100 + 310 if page.colors else 0
    H += 60 + max(sum(h + 12 for h in svc_h), 330) + 50
    H += 70 + 170 if page.process else 0
    H += note_h + 40 if page.note else 0
    H += 60
    p = Painter(spec, H)
    p.d.rectangle((0, 0, W, banner_h), fill=p.dark)
    by = p.block((W // 2, 70), page.title, title_f, p.primary, max_w=W - 160, center=True)
    if page.subtitle:
        p.block((W // 2, by + 10), page.subtitle, sub_f, (220, 220, 220), max_w=W - 200,
                center=True, spacing=6)
    y = banner_h + 40
    if page.colors:
        y = p.section_title(y, "Custom Colours", "Match your brand palette – the body can be moulded in your colour.")
        n = len(page.colors)
        cw = (W - 160) // n
        base = fit(a.side, cw - 20, 200)
        band = accent_band(base)
        for i, c in enumerate(page.colors):
            # The first swatch is the standard colour = the real render, untouched.
            if i == 0:
                v = base
            elif band is None:   # black/grey product: tint instead of hue-shift
                v = tint(base, c.hue, c.saturation, c.brightness)
            else:
                v = recolor(base, c.hue, c.saturation, c.brightness, band=band)
            x0 = 80 + i * cw
            p.card((x0 + 4, y, x0 + cw - 4, y + 260))
            p.paste(v, (x0 + (cw - v.size[0]) // 2, y + 20))
            p.text((x0 + cw // 2, y + 218), c.name, p.f(20, "bold"), DARK_TEXT, anchor="ma")
        y += 310
    y = p.section_title(y, "Custom Logo & Packaging")
    logo = fit(a.side, 520, 330)
    p.paste(logo, (80, y))
    lx, ly = 80 + int(logo.size[0] * 0.45), y + int(logo.size[1] * 0.33)
    p.d.rounded_rectangle((lx - 80, ly - 22, lx + 80, ly + 22), radius=8, outline=p.primary, width=4)
    p.text((lx, ly - 50), page.logo_label, p.f(18, "bold"), p.primary, anchor="ma")
    cy = y
    for it, th, h in zip(page.services, svc_title_h, svc_h):
        p.card((640, cy, W - 80, cy + h))
        p.d.rectangle((640, cy, 650, cy + h), fill=p.primary)
        p.block((672, cy + 10), it.title, svc_title_f, p.dark, max_w=svc_w, spacing=6)
        p.block((672, cy + 16 + th), it.text, svc_f, GRAY, max_w=svc_w, spacing=4)
        cy += h + 12
    y = max(cy, y + logo.size[1]) + 50
    if page.process:
        y = p.section_title(y, "How We Work With You")
        n = len(page.process)
        cw = (W - 160) // n
        for i, t in enumerate(page.process):
            cx = 80 + i * cw + cw // 2
            p.badge((cx, y + 30), i + 1, r=30)
            p.block((cx, y + 80), t, p.f(22, "bold"), DARK_TEXT, max_w=cw - 30, center=True, spacing=4)
            if i < n - 1:
                p.d.line((cx + 40, y + 30, cx + cw - 40, y + 30), fill=LINE, width=4)
        y += 170
    if page.note:
        p.card((80, y, W - 80, y + note_h), fill=TINT)
        p.block((110, y + 24), page.note, p.f(24, "bold"), DARK_TEXT, max_w=W - 220, spacing=10)
    p.footer()
    return p


def r_trust(spec: DetailSpec, page: TrustPage, a: LoadedAssets) -> Painter:
    m = Painter.ruler(spec)
    notice_h = 64 + m.block_h(page.notice.text, m.f(21), spec.width - 220) + 26 if page.notice else 0
    foot_h = m.block_h(page.footnote, m.f(19), spec.width - 160) + 20 if page.footnote else 0
    H = 60 + m.header_h(page.title) + 270 + foot_h + 40
    H += 60 + 300 + 50 if page.box_items else 0
    H += notice_h + 40 if page.notice else 0
    H += 60
    p = Painter(spec, H)
    W = p.W
    y = p.header(60, page.title)
    cw = (W - 160) // len(page.badges)
    for i, b in enumerate(page.badges):
        cx = 80 + i * cw + cw // 2
        p.d.ellipse((cx - 115, y, cx + 115, y + 230), outline=p.primary, width=8)
        vy = p.block((cx, y + 82), b.value, p.f(26, "black"), p.dark, max_w=190, center=True, spacing=4)
        p.block((cx, vy + 6), b.label, p.f(18), GRAY, max_w=190, center=True, spacing=2)
    y += 270
    if page.footnote:
        p.block((80, y), page.footnote, p.f(19), GRAY, max_w=W - 160)
        y += foot_h
    p.d.rectangle((80, y, W - 80, y + 2), fill=LINE)
    y += 40
    if page.box_items:
        p.text((80, y), page.box_title, p.f(34, "black"), p.dark)
        hero = fit(a.hero, 460, 300)
        p.paste(hero, (80, y + 60))
        cy = y + 80
        for ln in page.box_items:
            p.d.ellipse((600, cy + 10, 616, cy + 26), fill=p.primary)
            p.text((636, cy), ln, p.f(24), DARK_TEXT)
            cy += 50
        y = y + 60 + hero.size[1] + 50
    if page.notice:
        p.card((80, y, W - 80, y + notice_h), fill=LIGHT)
        p.text((110, y + 22), page.notice.title or "Notice", p.f(26, "bold"), p.negative)
        p.block((110, y + 64), page.notice.text, p.f(21), DARK_TEXT, max_w=W - 220)
    p.footer()
    return p


RENDERERS: dict[str, Callable] = {
    "hero": r_hero, "features": r_features, "steps": r_steps, "levels": r_levels,
    "modes": r_modes,
    "callouts": r_callouts, "chips": r_chips, "scenes": r_scenes, "spec_table": r_spec_table,
    "oem_odm": r_oem_odm, "trust": r_trust,
}
