# Example product

Two ways to publish, both in this folder:

- **`brief.json` (recommended)** — the minimal, human-facing input: a short brief,
  brand, category, images, price, and a couple of hard facts. Claude enriches the
  title, highlights, detail modules, FAQs, custom parameters, and the descriptive
  category attributes. Publish with:
  `product_publish_from_brief(brief_path="products/example/brief.json")`.
- **`product.json` (full manifest)** — the low-level form where you spell out every
  field yourself. Use it only when you want manual control. Publish with
  `product_publish(manifest_path="products/example/product.json")`.

The table below is the full manifest reference; for the brief, you only fill
`brief.json`.

## What to prepare

| # | Material | Where it goes | Required? | How to get it |
|---|---|---|---|---|
| 1 | **Category id** | `product.json` → `category_id` | yes | `alibaba.icbu.category.predict` / your console |
| 2 | **Title** (≤128 chars, only `- / , & .` punctuation) | `fields.productTitle` | yes | write it, or let `generate_product_detail` produce it |
| 3 | **Main image(s)** (1–6) | `main_image_refs` (`file_id` + `url`) | yes | `video_query`? no — from `alibaba.icbu.photobank.list`; or upload in the console, then reference |
| 4 | **Detail images** | `detail_image_refs` (URLs) | yes (detailImage is required) | same photo bank |
| 5 | **Price** (tiered) | `prices.csv` (referenced by `price_file`) | yes | you |
| 6 | **Unit / sale type / price mode** | `fields.priceUnit` / `saleType` / `scPrice` | yes | see codes below |
| 7 | **MOQ** | `fields.minOrderQuantity` | yes | you |
| 8 | **Required category attributes** | `product_attributes` | yes | `category_get_attributes(category_id)` shows which are required + options |
| 9 | **Rich detail** (highlights, modules, company, FAQs) | `content/detail.json` (via `content_file`) | optional | `generate_product_detail(..., save_to="content/detail.json")` |
| 10 | **Product group** | `product_group.first_group_id` | optional | `product_group_get(-1)` |

## Field codes (category 201335115 example)

- `priceUnit`: `20` = Set/Sets (see `product_get_schema` for the full list)
- `saleType`: `normal` (by unit) or `batch`
- `scPrice`: `1` = tiered pricing (ladderPrice) / `3` = SKU pricing
- Required `product_attributes` for this category: place of origin, feature, use,
  application, Season, Material, Style (Color is a sale attribute, not here).

## Images note

Local image files are uploaded to the photo bank automatically (via the `/sync`
gateway). You can also **reference images already in your photo bank** by `file_id`
+ `url` — get them from the `alibaba.icbu.photobank.list` API (via `alibaba_raw_call`).
Main images: 4–6, ≤5 MB each, > 640×640, aspect ratio 3:4–4:3 (square / 1000×1000 best).
Detail images: up to 30, ≤3 MB each, width ≥1200 px, height may be very long (stitch
sections into one long image to fit more content — watch buyer page-load).
(Local images are uploaded to the photo bank one per API call.)

## Where to put the folder

Anywhere inside the server's allowed directories (default: the current working
directory). If you keep products elsewhere, set
`ALIBABA_MCP_ALLOWED_DIRS=/path/to/products`.

## Publish

```
product_publish(manifest_path="products/example/product.json", draft=true)
```

Check the result: `biz_success` should be true and `missing_required` should be `[]`.
Then review the draft in the Alibaba console before a real (non-draft) publish.
