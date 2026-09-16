# alibaba-seller-mcp

An MCP server for the **Alibaba.com Global B2B** open platform. It gives
an MCP client (Claude Desktop, Claude Code, or any MCP host) tools to:

- **Publish a listing from a brief** — the seller writes down facts, Claude writes
  the title, highlights, detail modules, FAQs and descriptive attributes, and the
  server handles the category schema, option codes and image uploads.
- **Render the listing's images in code, not with an image model** — main-image
  crops and text-dense detail pages are drawn with Pillow from a JSON spec, so the
  numbers and spelling are exact and re-rendering after an edit is free.
- **Authorize a seller** via OAuth 2.0 (authorization-code flow, with token storage
  and automatic refresh).
- **Publish and update products** from a lower-level manifest when you want full control.
- **Read images / videos / prices from local files** — validated locally, then
  uploaded to the photo bank to obtain hosted URLs.
- **Track AI token usage** with per-model USD cost estimates, and cap it per session.

## Project structure

Organised by layer, and the dependencies only point downwards — **adapters →
application → integrations → engine → shared**. The rendering engine knows nothing
about Alibaba, briefs or Claude; it depends on `pathsafe` and nothing else.

```
alibaba-seller-mcp/
├── pyproject.toml               # packaging, dependencies, console scripts
├── .env.example                 # configuration template
├── README.md
├── src/alibaba_seller_mcp/
│   ├── __main__.py              # entry point: python -m alibaba_seller_mcp
│   │   ── adapters ──────────────────────────────────────────────
│   ├── server.py                # MCP server — registers all tools + usage resource
│   ├── models.py                # typed Pydantic tool-output models
│   ├── authserver.py            # CLI OAuth helper (local-callback / paste / tunnel)
│   │   ── application ───────────────────────────────────────────
│   ├── listing/                 # the flow: a brief folder → a published listing
│   │   ├── publishing.py        # PublishFromBrief — owns media→schema→AI→publish
│   │   └── media.py             # brief.photos → main images + rendered detail pages
│   │   ── integrations ──────────────────────────────────────────
│   ├── alibaba/                 # open-platform client
│   │   ├── client.py            # signed REST client (HMAC-SHA256, GOP + TOP protocols)
│   │   ├── auth.py              # seller OAuth (authorize URL, code→token, auto-refresh)
│   │   ├── errors.py            # typed exceptions
│   │   ├── schema.py            # itemSchema parse + filled value-XML builder
│   │   ├── products.py          # publish API adapter: schema.get / add / add.draft / update
│   │   ├── manifest.py          # ProductManifest — the input document (DTO)
│   │   ├── values.py            # pure value rules: icbuCatProp, saleProp, ladders, clipping
│   │   ├── fields.py            # FieldAssembler — manifest + AI content → filled fields (does I/O)
│   │   ├── photobank.py         # image uploads + content-hash cache
│   │   ├── categories.py        # category system attributes (required + options)
│   │   ├── videos.py            # video ↔ product relation (+ id encrypt/resolve)
│   │   └── groups.py            # product groups
│   ├── ai/                      # Claude generators — one class per concern
│   │   ├── prompts.py           # every built-in prompt (edit wording rules here)
│   │   ├── base.py              # shared client / usage recording / JSON extraction
│   │   ├── product_detail.py    # title, highlights, modules, FAQs + attribute selection
│   │   └── detail_spec.py       # page spec for the code-rendered detail images
│   │   ── engine ────────────────────────────────────────────────
│   ├── rendering/               # deterministic renderer (no AI, no brief knowledge)
│   │   ├── spec.py              # page-template DTOs (the AI ↔ renderer contract)
│   │   ├── image_ops.py         # cut-outs, transparency, colour variants
│   │   ├── fonts.py / painter.py# font lookup, drawing primitives
│   │   ├── pages.py             # one renderer per page type
│   │   ├── render.py            # render_spec(spec, out_dir)
│   │   └── main_images.py       # main-image crops
│   │   ── shared ────────────────────────────────────────────────
│   ├── config.py                # env-driven config (gateway, API method names, keys)
│   ├── pathsafe.py              # filesystem allowlist (path confinement)
│   ├── storage.py               # local JSON/JSONL persistence (tokens, usage, cache)
│   ├── text_format.py           # title normalizer (case + length + punctuation rules)
│   ├── files/
│   │   └── readers.py           # local image / video / price-file ingestion + image checks
│   └── usage/
│       └── tracker.py           # token-usage accounting + USD cost estimates
├── products/
│   └── example/                 # ready-to-fill template (copy, replace placeholders)
│       ├── brief.json           # the ONE input file: facts, photos, prices, overrides, detail pages
│       └── README.md            # every key explained + page-type reference
└── tests/                       # pytest suite
```

## Requirements

- Python **3.11+**
- An Alibaba.com open-platform app (App Key / App Secret) with the product APIs granted
- An Anthropic API key — used for the brief's copy and the detail-page spec.
  Not needed for a brief with `"ai": false`.

## Install

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e .
```

## Configure

Copy `.env.example` to `.env` and fill it in (the server auto-loads `.env` from the
project root or the current directory):

```bash
cp .env.example .env
```

Key variables (see `.env.example` for the full list):

| Variable | Purpose |
|---|---|
| `ALIBABA_APP_KEY` / `ALIBABA_APP_SECRET` | App credentials from the console |
| `ALIBABA_REDIRECT_URI` | OAuth callback URL registered in the console |
| `ALIBABA_METHOD_SCHEMA_GET` / `_ADD` / `_ADD_DRAFT` / `_UPDATE` / `ALIBABA_METHOD_PHOTO_UPLOAD` | Granted API method names |
| `ANTHROPIC_API_KEY` | Claude access (brief copy, detail spec) |
| `SOCIAL_MODEL` | Claude model for AI generation (default `claude-opus-5`) |
| `ALIBABA_MCP_ALLOWED_DIRS` | Filesystem roots the server may read/write (default: cwd) |
| `ALIBABA_MCP_AI_TOKEN_BUDGET` | Soft cap on Claude tokens per session (unset = no limit) |

> **Confirm the API method names.** The exact method names/paths and product field
> schema are category-specific. Check your app console's *API权限包 → 详情* pages
> and adjust the `ALIBABA_METHOD_*` variables if they differ from the defaults. Use
> the `alibaba_raw_call` tool to test a method quickly.

## Run

```bash
python -m alibaba_seller_mcp
```

### Register with an MCP client

Claude Code:

```bash
claude mcp add alibaba-seller -- /path/to/.venv/bin/python -m alibaba_seller_mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "alibaba-seller": {
      "command": "/path/to/.venv/bin/python",
      "args": ["-m", "alibaba_seller_mcp"]
    }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `alibaba_get_authorize_url` | Get the URL the seller opens to grant access |
| `alibaba_complete_authorization` | Exchange the OAuth `code` for a token and store it |
| `alibaba_complete_authorization_from_url` | Finish auth from a pasted callback URL (auto-extracts `code`) |
| `alibaba_auth_status` | Show authorization state / token expiry |
| `alibaba_raw_call` | Call any granted API by method name (escape hatch / testing) |
| `read_local_media` | Inspect a local image/video (type, size, dimensions) |
| `read_price_file` | Read a CSV/JSON price file into normalized rows |
| `product_upload_image` | Upload one local image to the photo bank → `{file_id, url}` |
| `product_publish_from_brief` | Publish from a minimal brief — Claude enriches title/detail/attributes |
| `product_get_schema` | Get a category's publish schema (fields, types, required, options) |
| `category_get_attributes` | Get a category's system attributes for `icbuCatProp` (required flags, options) |
| `product_publish` | Publish a product from a `product.json` manifest (schema.add) |
| `product_update` | Overwrite a product from a **complete** manifest (schema.update — not a partial update) |
| `product_render_draft` | Read back a draft's saved fields to verify what a publish stored |
| `generate_product_detail` | AI-generate a structured product detail (title/highlights/modules/attributes) |
| `listing_media_prepare` | Build main images + code-rendered detail pages from a brief's `photos` (inline `detail_spec`, or AI writes one once) |
| `detail_images_render` | Re-render detail pages from an edited spec file — no tokens |
| `video_query` | List the seller's videos (needs `current_page`/`page_size`) → `video_id`s |
| `video_relate_product` | Set a product's main/detail video (single slot — replaces an existing one; numeric ids auto-encrypted) |
| `video_list_related` | List product ids related to a video |
| `product_group_get` | Get a product group / list top-level groups (`group_id=-1`) |
| `usage_stats` | Report AI token usage + estimated USD cost |

Resource: `usage://summary` — all-time usage grouped by model.

## Authorizing a seller

The `redirect_uri` you send **must byte-match** the callback registered in the app
console. There are two ways to complete the flow:

### Local auth helper (recommended)

```bash
alibaba-seller-auth          # or: python -m alibaba_seller_mcp.authserver
```

- If `ALIBABA_REDIRECT_URI` is a **localhost** URL (e.g. `http://127.0.0.1:8721/callback`,
  registered in the console), it starts a tiny server, opens the browser, and
  **auto-captures** the `code` — fully hands-off.
- Otherwise (e.g. a test app whose callback is `https://www.alibaba.com`), it opens
  the browser and asks you to **paste the redirected URL**; it extracts the `code`
  for you. Force this with `--paste`. The callback page need not load correctly —
  the `code` is in the address bar regardless.

**If the console only accepts a public https callback** (no localhost), auto-capture
still works through a tunnel: run a public https tunnel (cloudflared/ngrok) to
`127.0.0.1:8721`, register the tunnel URL as the callback, set
`ALIBABA_REDIRECT_URI` to it, and start the helper with the local bind address:

```bash
alibaba-seller-auth --bind 127.0.0.1:8721   # or set ALIBABA_LOCAL_BIND=127.0.0.1:8721
```

### Via MCP tools

1. `alibaba_get_authorize_url` → open the URL, approve.
2. Either paste the redirected URL into `alibaba_complete_authorization_from_url`,
   or pass just the code to `alibaba_complete_authorization`.

## Typical flow

1. Authorize the seller (see above) → token stored + auto-refreshed.
2. Put `brief.json` and the seller's photos in a folder (copy `products/example/`).
3. `product_publish_from_brief(brief_path="products/my-product/brief.json")` →
   media rendered, images uploaded, draft created.
4. `product_render_draft(product_id=…, category_id=…)` to verify what was stored.
   Each publish creates a NEW draft — delete superseded ones in the console.
5. `usage_stats(group_by="day")` to see token spend.

## Publishing from a brief (recommended)

Instead of hand-filling the full manifest, give a minimal **brief** — the facts only —
and let Claude enrich the rest:

```jsonc
{
  "brief": "C01B Salt Blaster: handheld salt-firing insect killer, infrared laser aiming, ABS body, for home/garden mosquito & fly control",
  "brand": ["C01B", "ABS"],
  "category_id": 201335115,
  "images": { "main": [{ "file_id": "…", "url": "…" }, … 4–6], "detail": ["https://…", …] },
  "price": { "unit": "Set", "moq": 10, "tiers": [[10, 12.90], [500, 9.90]] },
  "facts": { "place_of_origin": "China", "material": "ABS" },
  "version": "premium", "group": "908868315"
}
```

`product_publish_from_brief(brief_path="products/example/brief.json")` then:

- **You provide** (facts only): brief, brand, category, images, price/MOQ, and factual
  attributes (origin, material).
- **Claude enriches**: title (title-cased, ≤128 chars), highlights, image-text detail
  modules, FAQs, custom parameters, and **picks the descriptive category attributes**
  (feature/use/season/style…) from the schema's allowed options.
- **The tool handles**: schema fetch, option-code mapping, required-attribute detection,
  `priceUnit`/`saleType`/`scPrice` codes, image `fileId`, and multiComplex XML.

The result reports `ai_filled` (what was generated), `missing_required` (required
attributes still unfilled), and `warnings` — **read them**. A brief is hand-written
JSON, so a typo is the likeliest failure and the quietest one: an unknown key is
simply never read and the draft publishes without it. Any top-level key the flow
does not know, and any `facts` entry matching no attribute of the category, is
reported there with a "did you mean" when something is close:

```
ignored unknown brief key(s): lead_tiem (did you mean 'lead_time'?)
ignored facts key(s) matching no attribute of this category: Colour (did you mean 'Color'?)
```
 **Main images must be 4–6** (≤5 MB each,
> 640×640, aspect ratio 3:4–4:3, square/1000×1000 best); fewer than 4 raises a warning
— Claude cannot generate real product photos, so add more.

Image guidance differs by role:

| | Main images (`scImages`) | Detail images (`detailImage`) |
|---|---|---|
| Count | 4–6 | ≤ 30 |
| Size | ≤ 5 MB | ≤ 3 MB |
| Dimensions | > 640×640, ratio 3:4–4:3 (square/1000×1000 best) | width ≥ 1200 px; **height may be very long** |

Because detail images allow long/tall canvases, several sections can be **stitched into
one long image** (keep each ≤ 3 MB) — this fits more content under the 30-image cap, at the
cost of slower detail-page image loading for buyers, so balance long images vs. count.
Images may be **local file paths** (uploaded to the photo bank automatically, one per call)
or existing photo-bank refs. Local files are checked against the guidance above (warnings only).

**One file, full control.** Everything a listing needs lives in `brief.json`
(`products/example/brief.json` lists every key; its README explains each one):

- `ai: false` turns off every Claude call — then `content.title` and `detail_spec` are
  mandatory and the draft is built purely from what you wrote.
- `content` overrides always win over AI output: `title`, `highlights`, `company_intro`,
  `keywords`, `attributes`, `faqs`.
- `photos` + `detail_spec` (inline) + `how_to_use` + `parts` drive the code-rendered media
  (next section); `images` can still point at ready-made files or photo-bank refs instead.
- `sale_props` (`{"Color": ["Orange"]}`) fills the required sales properties (`saleProp`,
  values serialized with `inputValue`); `lead_time` (`[[quantity, days], …]`) fills the
  required shipping ladder (`ladderPeriod`), defaulting to ≤1000 units → 15 days with a
  warning when omitted. `missing_required` in the result now covers both.

The `product.json` manifest below remains available as a lower-level path.

## Listing media without an image model (code-rendered detail pages)

Detail pages are text-dense (specs, steps, OEM/ODM terms). Image models get numbers
and spelling wrong, bill per attempt, and need a full re-render for every copy change.
This server draws them instead: **the AI writes a compact JSON spec once, Pillow renders
it, and re-rendering is free.** Photos of the real product are the only pixels not drawn.

Put the seller's photos in the brief and leave `images` empty:

```jsonc
{
  "brief": "…facts…", "product_name": "Salt Gun Fly Blaster", "model": "C01",
  "brand": ["XXXX"], "category_id": 201335115,
  "photos": {
    "hero": "hero.png",                 // white-background render (required)
    "side": "side.png",                 // side render (for callouts / colour variants)
    "scenes": [{ "path": "kitchen.png", "caption": "Kitchen" }, { "path": "patio.png", "caption": "Patio" }],
    "theme": { "primary": "#F47A20", "dark": "#1A1A1A" }
  },
  "oem_odm": "custom colours, logo printing, retail packaging; samples available",
  "how_to_use": ["Fill the chamber with dry salt", "Rack the slide", "…"],   // → steps page
  "parts": ["salt chamber", "barrel", "elastic pouch", "grip"],              // → callouts page
  "price": { … }, "facts": { … }
}
```

`how_to_use` and `parts` are optional but matter: the built-in prompt only lets the AI
write a steps page from listed operation steps and only label parts you named — without
them those pages are omitted rather than guessed.

Then either call `listing_media_prepare(brief_path=…)` explicitly, or just
`product_publish_from_brief` — it runs the same preparation when `images.main` /
`images.detail` are empty:

| Step | What happens | Tokens |
|---|---|---|
| Main images | hero/side cropped to the product and padded to 1000×1000; scenes cropped to 3:4 / 4:3 (max 6) | 0 |
| `detail_spec` inline in the brief | rendered exactly as written — full manual control | 0 |
| else `detail_spec.json` beside the brief | rendered as-is — edit the text, call `detail_images_render`, done | 0 |
| else (and `ai` not false) | Claude writes the pages (hero, features, steps, levels, callouts, chips, scenes, spec_table, oem_odm, trust) from the brief's facts and saves them as `detail_spec.json`; the side render is attached so `callouts` land on real parts | ~6k measured |

The spec's page schema lives in `src/alibaba_seller_mcp/rendering/spec.py`; the prompt that fills it is `ai/prompts.py` → `DETAIL_SPEC_SYSTEM`
(`products/example/brief.json` → `detail_spec` is a filled template of every page
type). Field lengths are enforced, so copy stays short enough to fit; unknown facts are
omitted, never invented. Fonts: macOS Arial / Linux DejaVu are found automatically; set
`DETAIL_FONT_DIR` or `theme.font_dir` to use your own.

## Publishing a product (full manifest)

Product publishing on Alibaba.com Global B2B is **schema-based**: each category
defines its own fields. The flow is `schema.get` → fill values → `schema.add`.

1. **Inspect the category schema** to learn its field ids/options:
   `product_get_schema(category_id=201335115)`.
2. **Describe the product in a manifest** — a folder with `product.json` plus assets:

   ```
   products/salt-blaster/
     product.json
     images/main/01.jpg
     images/detail/01.jpg
     prices.csv
   ```

   ```json
   {
     "category_id": 201335115,
     "language": "en_US",
     "photobank_group_id": "17590",
     "fields": {
       "productTitle": "C01B Salt Blaster Insect Killer ...",
       "saleType": "normal",
       "scPrice": "1",
       "priceUnit": "20"
     },
     "main_images": ["images/main/01.jpg"],
     "detail_images": ["images/detail/01.jpg"],
     "price_file": "prices.csv"
   }
   ```
3. **Publish**: `product_publish(manifest_path="products/salt-blaster/product.json", draft=True)`
   (drafts are not listed live — use them to verify a manifest, then publish for real).

**Images:** `scImages` (main images) require the photo-bank **fileId** — the value is
serialized as `<value fileId="…">url</value>`. Local images are uploaded via the photo
bank's **`/sync` (TOP) gateway** (`photobank.upload` uses `session` + no-prefix signing;
`product_upload_image` and the manifest's `main_images`/`detail_images` local paths use
it automatically). To reuse images already in your photo bank instead of uploading, put
them in the manifest as `main_image_refs: [{"file_id": "…", "url": "…"}]` and
`detail_image_refs: ["https://…", …]`. List existing images with the
`alibaba.icbu.photobank.list` API and groups with `alibaba.icbu.photobank.group.list`.

### AI-generated structured detail (AI+结构化商详)

`generate_product_detail(product_name, version, features, keywords, detail_image_count, save_to)`
produces the title, highlights, ordered image+text modules, attributes and FAQs with
Claude. `version` ∈ `premium` (全能精装版) / `lite` (经济简装版) / `general` (通用排版)
controls completeness. The title is normalized to meet Alibaba's publishing rules:
**≤128 characters**, **only `- / , & .` punctuation** (special characters like
`@ ! ！ ? ？ $ ^ { } ~ 、` are stripped), and **title case** (major words and 4+ letter
words capitalized; short articles/conjunctions/prepositions lowercased unless first;
brand/model/acronym tokens preserved — pass `brands=["C01B", …]`). The same length +
punctuation rules are enforced on any manually-set `productTitle` at publish time. Save it with
`save_to="products/x/content/detail.json"` and point
the manifest at it via `"content_file": "content/detail.json"`.

The generated content follows the standard product-detail structure, and publishing maps
each part to its schema field:

| Detail section | Content key | Schema field |
|---|---|---|
| Title | `title` | `productTitle` (Alibaba title case) |
| Product Highlights | `highlights` (≤2000 chars) | `textDesc` |
| Scene / detail / dimensions / packaging modules | `modules[]` (tagged `section`, `image_slot`) | `detailImage` (gallery 200/300/350; captions on 300) |
| Custom parameters | `attributes[]` | `customMoreProperty` |
| Category attributes (required) | manifest `product_attributes` | `icbuCatProp` (auto-filled) |
| Company Introduction | `company_intro` (≤2000 chars) | `companyDesc` |
| FAQs | `faqs[]` (max 8; question ≤150, answer ≤500) | `companyFaqDesc` |
| Description mode | `detail_version` | `productDescType` (`ALIBABA_PRODUCT_DESC_TYPE`, default `1`=智能编辑) |

Product video is associated separately via the video tools; logistics dimensions live in
`pkgMeasure`/`pkgWeight`, packaging in `boxPackaging`, certifications in `productCertificate`.

**Required category attributes** (`icbuCatProp`, e.g. Place of Origin / Material / Feature)
are auto-filled from a manifest `product_attributes` map — keyed by attribute name **or** id,
with values given as display names or option codes:

```json
"product_attributes": {
  "Place of Origin": "China",
  "Feature": ["Eco-Friendly", "Durable"],
  "Material": "ABS"
}
```

Which attributes are required and their allowed options come straight from `schema.get`
(each attribute's `requiredRule` / options). Discover them with `category_get_attributes` or
`product_get_schema`. The publish result's `missing_required` lists any required attribute still
unfilled — a **draft tolerates gaps, but a real (non-draft) publish needs them all**.

At publish time, `main_images`/`detail_images` are uploaded to the photo bank
(cached by content hash), their URLs are placed into `scImages`/`detailImage`, and
`price_file` fills `ladderPrice` (when `scPrice` is `"1"`) plus MOQ. Field ids and
option codes are category-specific — always check `product_get_schema` first.

## Price file formats

**CSV** (header row required):

```csv
sku,price,MOQ,currency
A-1,12.5,100,USD
```

**JSON** (list of objects or a single object):

```json
[{ "sku": "A-1", "price": 12.5, "moq": 100, "currency": "USD" }]
```

Recognized aliases are normalized to `sku`, `price`, `min_order_quantity`,
`currency`, `quantity`, `min_price`, `max_price`; unknown columns are kept under
`extra`.

## Signing

Requests are signed with **HMAC-SHA256**: parameters (system + business, excluding
`sign` and file bytes) are sorted by name, concatenated as
`api_path + key1value1key2value2…`, and signed with the App Secret (uppercase hex).
`tests/test_signing.py` verifies this against the documented reference vector.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Tool safety & schemas

- **Annotations** — every tool advertises MCP hints so clients can reason about it:
  read-only tools set `readOnlyHint`; API/AI tools set `openWorldHint`. Writing tools
  set `destructiveHint` / `idempotentHint` honestly: `product_publish` is
  non-idempotent (every call creates a product), while `product_update` and
  `video_relate_product` are idempotent but **destructive** — each replaces something
  that already exists. `destructiveHint: false` promises an additive-only tool, so
  no tool here claims it (a test enforces that).
- **Structured output** — tools return typed Pydantic models, so each has an output
  schema with `additionalProperties: false` instead of an open object.
- **Error handling** — an anticipated failure (API error, rejected input, a path
  outside the allowlist, or a business refusal) comes back as a tool execution
  error: `isError: true`, the message in `content`, and the tool's own model as
  structured content with `ok: false` / `error` / `error_type`. Protocol errors
  stay reserved for unknown tools and malformed requests, per the MCP spec. A
  business refusal is a clean gateway `code: "0"` with a false verdict; which
  field carries that verdict differs per API (`biz_success` on a publish,
  `success` on a video relation), so each result model declares its own via
  `Result.OUTCOME_FIELD` rather than it being guessed by name — a status tool's
  `authorized: false` is an answer, not a failure.
- **Runaway-loop budget** — `ALIBABA_MCP_AI_TOKEN_BUDGET` caps Claude tokens for
  one server session (unset = no limit). Checked before each AI call, so it bounds
  a loop rather than aborting work midway. Platform API calls need no equivalent:
  one tool call is one request, with no retries, and image uploads are cached by
  content hash.
- **Filesystem allowlist** — tools that take a local path (`read_local_media`,
  `read_price_file`, product manifests and their referenced assets,
  `generate_product_detail(save_to=…)`) resolve the path and confine it to
  `ALIBABA_MCP_ALLOWED_DIRS` (default: the current working directory). Paths outside
  the allowlist — including `..` traversal — are rejected with a clear error. The
  same check covers paths named *inside* a file — a brief's `photos`, a spec's
  `assets`, a manifest's images — since those are read, rendered into listing images
  and uploaded to the photo bank, and are no more trustworthy than a tool argument.

## Security

The `.env` and the state dir (`~/.alibaba_seller_mcp`, holding OAuth tokens and the
usage log) are gitignored. Never commit real credentials. The filesystem allowlist
above limits what an untrusted MCP client can read or write.
