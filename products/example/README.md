# Example product — one file controls everything

`brief.json` is the only input. Every parameter of a listing lives in it; anything you
leave empty is filled by Claude (title, highlights, FAQs, descriptive attributes, detail
pages) **unless** `"ai": false`, in which case nothing is generated and the brief must be
complete. Copy this folder, drop your photos next to it, edit the JSON, then:

```
product_publish_from_brief(brief_path="products/<your-product>/brief.json", draft=true)
```

Check the result: `biz_success` true, `missing_required` empty, and read `warnings`
(an image upload failure shows up there, as does any brief key or `facts` entry that
was ignored because it matches nothing — a typo in a brief is otherwise silent). Verify the draft with `product_render_draft`,
then publish for real from the console or with `draft=false`.

## Keys

| Key | Required | What it controls |
|---|---|---|
| `brief` | yes | The fact sheet. All AI text is derived from it; nothing outside it is invented. |
| `base_dir` | inline + `photos` | Only for a brief passed **inline** (not as a file): the folder its relative paths resolve against, and where the rendered `images/` tree is written. Required when an inline brief has `photos` — the server will not guess. A brief loaded from a file always uses its own folder. |
| `product_name`, `model`, `brand` | recommended | Identity used on detail pages and to preserve casing in the title. `brand` may list several tokens to keep as-is. |
| `category_id` | yes | Alibaba leaf category (`product_get_schema` / `category_get_attributes` show its fields). |
| `language` | no | Schema language, default `en_US`. |
| `ai` | no | `true` (default): Claude fills what is empty. `false`: no Claude calls at all — `content.title` and `detail_spec` must be provided. |
| `content` | no | Manual overrides that always win over AI: `title` (≤128 chars, only `- / , & .`), `highlights` (≤2000 chars), `company_intro` (≤2000), `keywords`, `attributes` (`[{name, value}]` → custom parameters; each value ≤ 70 chars, name ≤ 30), `faqs` (`[{question, answer}]`, max 8; question ≤150, answer ≤500). Anything longer is clipped at a word boundary on publish. Empty values are ignored. |
| `photos` | no | Seller photos for code-rendered media: `hero` (white-background render, required inside `photos`), `side` (render for callouts / colour variants), `scenes` (`[{path, caption}]`), `theme` (brand colours, optional `font_dir`). Paths are relative to this folder. |
| `how_to_use`, `parts` | no | Operation steps and named parts. The built-in prompt writes the steps / callouts pages **only** from these; without them those pages are omitted, never guessed. |
| `oem_odm` | no | What you offer (colours, logo, packaging, samples). Empty → no OEM/ODM page. |
| `images.main` | one of | 4–6 main images: local paths, photo-bank refs `{file_id, url}`, or leave empty to build them from `photos` (renders → 1000×1000, scenes → 3:4 / 4:3). |
| `images.detail` | one of | Detail pages: local paths / URLs, or leave empty to render from `detail_spec`. Max 30, width ≥ 1200. |
| `price` | yes | `unit` (display name or code), `moq`, `tiers` (`[[qty, price], …]`, max 4). |
| `sale_type` | no | `normal` (default) or `batch`. |
| `sale_props` | yes (Color) | Sales properties = the SKU-defining attributes under `saleProp`, keyed by name: `{"Color": ["Orange"], "Size": ["one size"]}`. Values from the option list get their code; anything else is sent as a custom value (negative id + `inputValue`). A `Color` left in `facts` is moved here automatically. |
| `lead_time` | yes | Shipping ladder `ladderPeriod`: `[[quantity, days], …]`, max 3 tiers, each meaning "orders up to *quantity* units ship in *days* days". Omitted → default `[[1000, 15]]` with a warning. |
| `facts` | yes | Category attributes by name. A key matching no attribute of the category is reported in `warnings` (with a "did you mean" when one is close) rather than silently dropped. `place_of_origin` / `material` / `brand` / `model` are aliases; any other key must match the attribute's display name and use its allowed options (`category_get_attributes`). Required attributes left out are chosen by Claude (unless `ai: false`). |
| `features`, `keywords` | no | Hints for the AI copywriter. |
| `version` | no | Detail template tier: `premium` (default) / `lite` / `general`. |
| `group` | no | Product group id (`product_group_get`). |
| `detail_spec` | no | The detail pages, page by page. Inline here it always wins; if absent the flow looks for `detail_spec.json` beside the brief, and only then asks Claude to write one (saved as that file). Re-render after edits with `detail_images_render`. |

## `detail_spec.pages`

Ten page types; every text field has a length limit that the validator enforces, so keep
copy short. `theme` inside `detail_spec` overrides `photos.theme`.

| `type` | Layout | Fields |
|---|---|---|
| `hero` | dark banner + hero render + stats row | `tagline`, `badges[≤5]`, `intro`, `stats[≤4]{value,label}` |
| `features` | side render + benefit cards | `title`, `subtitle`, `items[2–5]{title,text}`, `note` |
| `steps` | numbered 3-column grid | `title`, `subtitle`, `steps[3–6]{title,text}`, `tip{title,text}` |
| `levels` | 2–4 intensity panels | `title`, `subtitle`, `levels[2–4]{label,intensity 1–5,value,note}`, `footnote` |
| `callouts` | annotated render | `title`, `subtitle`, `image` (`side`/`hero`), `callouts[1–6]{label,x,y,side}`, `stats[≤4]`, `summary` |
| `chips` | grouped pill tags | `title`, `subtitle`, `groups[1–3]{heading,tone,items[]}`, `notice{title,text}` |
| `scenes` | 2-column photo grid from `photos.scenes` | `title`, `subtitle` |
| `spec_table` | zebra table | `title`, `rows[3–18]{name,value}` |
| `oem_odm` | colour variants + logo + services + process | `title`, `subtitle`, `colors[≤6]{name,hue,saturation,brightness}` (first = standard, untouched), `logo_label`, `services[≤5]`, `process[≤5]`, `note` |
| `trust` | ring badges + box contents + notice | `title`, `badges[1–4]{value,label}`, `footnote`, `box_title`, `box_items[≤6]`, `notice{title,text}` |

Colour hues are degrees: green 120, blue 210, red 355, purple 275; black is
`saturation 0, brightness 0.25`. Callout `x`/`y` are fractions of the render
(0 = left/top). Fonts: macOS Arial / Linux DejaVu are found automatically, or set
`theme.font_dir`.

## Field codes (category 201335115)

- `priceUnit`: `Set` → `20` (see `product_get_schema` for the full list)
- `scPrice` is fixed to `1` (tiered pricing) by the brief flow
- Required category attributes here: Place of Origin, Feature, Use, Application, Season,
  Material, Style. Color is a **sales property** (`sale_props`), and the shipping
  ladder (`lead_time`) is required too.

## Where to put the folder

Anywhere inside the server's allowed directories (default: the current working
directory). If you keep products elsewhere, set `ALIBABA_MCP_ALLOWED_DIRS=/path/to/products`.
