"""Detail-image **spec** — the contract between the AI copywriter and the renderer.

The AI (see ``ai.detail_spec.DetailSpecGenerator.generate``) only
produces ``pages`` — short, factual text slotted into a fixed set of page types.
The server adds ``assets`` (the seller's real product photos) and ``theme``; the
renderer (``render.render_spec``) turns the whole thing into detail images with
pixel-accurate text. Nothing here draws anything: it is a plain DTO layer so specs
can be saved, hand-edited and re-rendered without spending tokens.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── shared fragments ─────────────────────────────────────────────────────────
class Stat(_Strict):
    value: str = Field(max_length=24)
    label: str = Field(max_length=40)


class Item(_Strict):
    title: str = Field(max_length=48)
    text: str = Field(default="", max_length=180)


class Note(_Strict):
    title: str = Field(default="", max_length=40)
    text: str = Field(max_length=320)


class Row(_Strict):
    name: str = Field(max_length=40)
    value: str = Field(max_length=90)


# ── page types ───────────────────────────────────────────────────────────────
class HeroPage(_Strict):
    """Brand eyebrow, product name, gradient tagline, tags, the hero render and big-number stats."""

    type: Literal["hero"]
    tagline: str = Field(max_length=70)
    badges: list[str] = Field(default_factory=list, max_length=5)
    intro: str = Field(default="", max_length=140)
    stats: list[Stat] = Field(default_factory=list, max_length=4)


class FeaturesPage(_Strict):
    """Side render on a stage, then 2–5 benefit cards in a two-column grid."""

    type: Literal["features"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=120)
    items: list[Item] = Field(min_length=2, max_length=5)
    note: str = Field(default="", max_length=120)


class StepsPage(_Strict):
    """Numbered how-to steps (3–6) in a 3-column grid, optional tip box."""

    type: Literal["steps"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=120)
    steps: list[Item] = Field(min_length=3, max_length=6)
    tip: Note | None = None


class Level(_Strict):
    label: str = Field(max_length=24)
    intensity: int = Field(ge=1, le=5, description="1 = lightest … 5 = strongest")
    value: str = Field(max_length=32)
    note: str = Field(default="", max_length=60)


class LevelsPage(_Strict):
    """2–4 side-by-side intensity panels (e.g. dosing / speed / power levels)."""

    type: Literal["levels"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=140)
    levels: list[Level] = Field(min_length=2, max_length=4)
    footnote: str = Field(default="", max_length=120)


class Mode(_Strict):
    """One named operating mode. Carries only stated facts — no magnitude, so the
    card never implies a ranking the product does not have. ``icon`` is an optional
    generic icon name (reserved for the icon-font layer); unknown/blank → text only."""

    label: str = Field(max_length=24)
    value: str = Field(default="", max_length=32, description="the concrete setting, e.g. 'High', '365nm UV'")
    note: str = Field(default="", max_length=80, description="what it is for / best for")
    icon: str = Field(default="", max_length=24)


class ModesPage(_Strict):
    """2–4 named operating modes as comparison cards, category-agnostic and with no
    magnitude implied. Use for discrete modes (Indoor/Outdoor/Light, Eco/Turbo/Sleep,
    Smoothie/Ice/Pulse); use ``levels`` only for genuine intensity/dose/speed/power."""

    type: Literal["modes"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=140)
    modes: list[Mode] = Field(min_length=2, max_length=4)
    footnote: str = Field(default="", max_length=120)


class Callout(_Strict):
    label: str = Field(max_length=44)
    x: float = Field(ge=0.0, le=1.0, description="fraction of image width")
    y: float = Field(ge=0.0, le=1.0, description="fraction of image height")
    side: Literal["up", "down", "left", "right"] = "up"


class CalloutsPage(_Strict):
    """Annotated product render with pointer lines, plus a stats row."""

    type: Literal["callouts"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=140)
    image: Literal["side", "hero"] = "side"
    callouts: list[Callout] = Field(min_length=1, max_length=6)
    stats: list[Stat] = Field(default_factory=list, max_length=4)
    summary: str = Field(default="", max_length=160)


class ChipGroup(_Strict):
    heading: str = Field(max_length=40)
    tone: Literal["positive", "negative", "neutral"] = "neutral"
    items: list[str] = Field(min_length=1, max_length=12)


class ChipsPage(_Strict):
    """Grouped pill tags (e.g. compatible / not compatible), optional notice."""

    type: Literal["chips"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=120)
    groups: list[ChipGroup] = Field(min_length=1, max_length=3)
    notice: Note | None = None


class ScenesPage(_Strict):
    """2-column grid of the seller's lifestyle photos (``assets.scenes``)."""

    type: Literal["scenes"]
    title: str = Field(max_length=48)
    subtitle: str = Field(default="", max_length=120)


class SpecTablePage(_Strict):
    type: Literal["spec_table"]
    title: str = Field(default="Specifications", max_length=48)
    rows: list[Row] = Field(min_length=3, max_length=18)


class ColorOption(_Strict):
    name: str = Field(max_length=24)
    hue: int | None = Field(default=None, ge=0, le=360, description="target hue in degrees; null keeps the original")
    saturation: float = Field(default=1.0, ge=0.0, le=2.0)
    brightness: float = Field(default=1.0, ge=0.0, le=2.0)


class OemOdmPage(_Strict):
    """OEM/ODM offer: colour variants (recoloured hero render), logo placement, services, process."""

    type: Literal["oem_odm"]
    title: str = Field(default="OEM / ODM SERVICE", max_length=32)
    subtitle: str = Field(default="", max_length=120)
    colors: list[ColorOption] = Field(default_factory=list, max_length=6)
    logo_label: str = Field(default="YOUR LOGO HERE", max_length=24)
    services: list[Item] = Field(default_factory=list, max_length=5)
    process: list[str] = Field(default_factory=list, max_length=5)
    note: str = Field(default="", max_length=240)


class TrustPage(_Strict):
    """Ring badges (patent / warranty / material…), box contents, safety notice."""

    type: Literal["trust"]
    title: str = Field(max_length=48)
    badges: list[Stat] = Field(min_length=1, max_length=4)
    footnote: str = Field(default="", max_length=140)
    box_title: str = Field(default="What's in the Box", max_length=40)
    box_items: list[str] = Field(default_factory=list, max_length=6)
    notice: Note | None = None


Page = Annotated[
    HeroPage | FeaturesPage | StepsPage | LevelsPage | ModesPage | CalloutsPage | ChipsPage | ScenesPage | SpecTablePage | OemOdmPage | TrustPage,
    Field(discriminator="type"),
]

PAGE_TYPES = ("hero", "features", "steps", "levels", "modes", "callouts", "chips", "scenes", "spec_table", "oem_odm", "trust")


# ── assets / theme / spec ────────────────────────────────────────────────────
class SceneAsset(_Strict):
    path: str
    caption: str = Field(default="", max_length=40)


class Assets(_Strict):
    """Seller-supplied photos. ``hero``/``side`` are white-background renders
    (auto-cropped to the product); ``scenes`` are lifestyle photos."""

    hero: str
    side: str | None = None
    scenes: list[SceneAsset] = Field(default_factory=list)


class Theme(_Strict):
    primary: str = "#F47A20"       # brand accent (eyebrows, numbers, headline gradient)
    dark: str = "#1A1A1A"          # shade behind scene captions
    positive: str = "#2EA05A"
    negative: str = "#D63C3C"
    font_dir: str | None = None    # override font lookup (see fonts.py)


class DetailSpec(_Strict):
    brand: str = Field(max_length=40)
    product_name: str = Field(max_length=60)
    model: str = Field(default="", max_length=30)
    footer: str = Field(default="", max_length=90, description="defaults to brand · product · model")
    width: int = Field(default=1200, ge=800, le=1600)
    theme: Theme = Field(default_factory=Theme)
    assets: Assets
    pages: list[Page] = Field(min_length=1, max_length=30)

    def footer_text(self) -> str:
        if self.footer:
            return self.footer
        parts = [self.brand, self.product_name] + ([f"Model {self.model}"] if self.model else [])
        return "  ·  ".join(p for p in parts if p)


class PagesOnly(_Strict):
    """What the AI returns: just the pages (assets/theme are added by the server)."""

    pages: list[Page] = Field(min_length=1, max_length=30)
