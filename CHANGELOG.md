# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is
0.x, a new tool, setting or extra, or a change to how an existing one behaves,
bumps the minor version; fixes bump the patch.

## [0.3.0] - 2026-09-28

### Added
- Keyword-ad research on the public alibaba.com search, with no seller auth:
  `ads_search_keyword` lists who advertises on a keyword, and
  `ads_keyword_monopoly` scores how locked it is by annual buyouts (top booth,
  duxiu, star brand, topad_classic, bottom booth) versus open to P4P bidding.
  Snapshots are cached for a day by default.
- An optional anti-bot slider solver (`ALIBABA_MARKET_SOLVE_SLIDER=1`) that tries
  the cheapest route first: template replay, then an optional headless signer,
  then a Camoufox browser. The browser tier ships as the `[browser]` extra.
- `ALIBABA_MARKET_*` settings for the cookie file, request interval, cache TTL,
  buyer country and the solver tiers.

## [0.2.5] - 2026-09-17

### Added
- MIT licence; the bundled Inter typeface stays under the SIL OFL.
- ruff, import-linter contracts for the layer order, and CI running lint, tests
  on Python 3.11–3.13 and a wheel metadata check.

### Changed
- Detail pages restyled: bundled Inter, product renders on light stages, large
  stat figures and rounded cards. Pages render at 2x and measure and draw from
  the same layout pass, so page height and drawing can no longer drift apart.
- Helpers used across modules are public (no leading underscore); a test fails
  on any in-package import of a private name.

## [0.2.4] - 2026-09-16

### Added
- Per-SKU seller codes (`skuOuterId`, "Commodity code") built from `sale_props`,
  prefixed by the brief's model or `sku_code_prefix`.
- A `modes` detail page for named settings (Indoor/Outdoor, Eco/Turbo).

### Changed
- Listing text is normalized to the platform's character rules: custom
  attributes are restricted to its allowed characters, and accented Latin
  letters are transliterated to ASCII in the title, highlights, company intro,
  FAQs and custom attributes.
- The `levels` page uses a rising-bar meter.

### Fixed
- Callout labels no longer overlap each other or the stats row.
- CJK unit symbols (such as ㎡) render instead of empty boxes.

### Removed
- `generate_social_content` and its generator. `SOCIAL_MODEL` stays as the model
  setting for the remaining AI generators.

## [0.2.0] - 2026-09-12

### Changed
- Tool failures are reported per the MCP spec (`isError: true`), including
  business refusals the gateway returns with `code: "0"`.
- `product_update` is documented and annotated as the full-document overwrite
  it is.
- Internal restructure: the product service split by responsibility, a
  `PublishFromBrief` service owning the publish flow, and packages regrouped
  into `listing/` and `rendering/`.
- The README and `.env.example` corrected to match the code.

### Added
- Unknown brief keys and unmatched `facts` entries are reported with a
  "did you mean" suggestion.
- `ALIBABA_MCP_AI_TOKEN_BUDGET` soft-caps AI tokens per session.

### Fixed
- Photo and asset paths named inside a brief or spec now go through the
  `ALIBABA_MCP_ALLOWED_DIRS` allowlist.
- Free-text category attributes are sent as `inputValue`, so Brand Name, Model
  Number and similar fields are no longer silently stored as `-1`.
- Detail-page text boxes grow to fit their copy instead of clipping it.
- An inline brief with `photos` must name a `base_dir`, so generated images no
  longer land in the working directory.

## [0.1.0] - 2026-09-11

Initial release: seller OAuth, product publish and update, local media and price
ingestion, AI listing copy, code-rendered main and detail images, and token
usage stats.
