# inventory plugin

The resale inventory as a **source of truth** for [protoAgent](https://github.com/protoLabsAI/protoAgent):
lots, items, listings, sales, price evidence, and an audit trail of every change — in a
SQLite file the plugin owns. Built for merchantAgent, useful for any agent that sells things.

```
inventory_summary()
  BLOODBOWL-2026-09   cost $355.00   remaining (low/target/high) $612 / $790 / $1,010
                      realized net $0.00   projected net at target +$435.00
```

## What it contributes

| | |
|---|---|
| **Tools** | `inventory_summary` · `inventory_list` · `inventory_get` · `inventory_upsert_lot` · `inventory_upsert_item` · `inventory_delete_item` · `inventory_set_price` · `inventory_mark_sold` · `inventory_listing` · `inventory_import_csv` · `inventory_export_csv` · `inventory_reprice_plan` · `inventory_add_photo` · `inventory_publish_preview` · `inventory_stale` — there is deliberately **no publish tool** |
| **View** | a rail panel: the item grid (double-click to edit name/category/condition/qty; status select; click a price for the Price dialog), Price / Sold / Listing / Edit / Delete per row, an optional **game system** per item (filter, ordering, inline edit with suggestions), multi-select with **Copy list** (also ⌘C) — a for-sale post per game system: the system as a header, then `Name NoS — $20` per line (condition inline, whole-dollar prices), lots with their P&L, sales, an activity log, CSV import (file or paste, with the mapping report) and export; per item a **Show on the public site** flag, a public **blurb** and **photos** (upload from the Edit dialog, alt text, make cover, delete); a **Publish** button that previews the site catalog diff and publishes it |
| **API** | bearer-gated JSON under `/api/plugins/inventory` — `summary`, `lots`, `items`, `items/{id}/price`, `items/{id}/sold`, `items/{id}/listings`, `listings/{id}/end`, `sales`, `stale`, `audit`, `import`, `export`, `items/{id}/photos` (raw-body upload, list, bytes, PATCH alt/position, DELETE), `publish/preview`, `publish` |
| **Automations** | `weekly_review: true` arms a plugin-owned recurring turn (`weekly_review_cron`, default Monday 09:00 in `review_timezone`) that re-prices stale evidence from eBay sold comps through the same tools, reports stale listings with a recommendation (it never changes a listing itself), and posts the per-lot P&L. Cancelled when the plugin is disabled. |
| **Events** | `inventory.item.changed`, `inventory.lot.changed`, `inventory.sale.recorded`, `inventory.imported`, `inventory.published` |
| **Skill** | `inventory-ops` — the rules (a target needs a basis; never set sold by hand; sold ≠ active ≠ retail) and the re-price / weekly-review routines |

## The model

- **Lot** — a purchase: cost, date, source. P&L is against the lot cost.
- **Item** — one sellable thing from a lot, optionally tagged with its game **system** (Warhammer 40K, Blood Bowl, …). Status `planned → available → listed → pending → sold`, or `kept` / `withdrawn` (`planned` = a piece that exists once a sealed box is split).
  Targets low/target/high **with a `price_basis` and a date**.
- **Price observation** — the comps behind a target (source, n, p25/median/p75, query). Recorded by `inventory_set_price`.
- **Listing** — where the item is up (channel, url, price).
- **Sale** — `net = price + shipping charged − fees − shipping cost`. Recording a sale closes live listings.
- **Audit** — every mutation, with the actor (`agent` or `console`) and the fields that changed.

Money is stored as integer cents and exposed as dollars.

## Photos and the public site

- **Photos** are sniffed by their bytes (JPEG, PNG, WebP; HEIC/HEIF is converted to JPEG with
  macOS `sips`), capped at 20 MB, and **stripped of metadata on upload** — EXIF (GPS, camera,
  serial), XMP, IPTC, comments, and anything after a JPEG's end-of-image marker. Only the
  EXIF Orientation survives, so phone photos still display upright. Pure Python, no Pillow.
  Stored next to the database as `photos/<item_id>/<photo_id>.<ext>`; position 0 is the cover.
- **Public** is opt-in per item. **Publish** (the view's button — the agent can only preview)
  builds `src/data/catalog.json` in the `site_dir` checkout from an allowlist of fields —
  `id, name, system, category, condition, price_cents, quantity, status, blurb, photos, links,
  updated` — for items that are public, available or listed, priced, and in stock. Cost,
  lot, notes, the low/high band, retail, price basis, sales and the audit log never leave.
- The preview carries a hash of exactly what it showed; Publish refuses (409) if the
  inventory moved since. It mirrors the photos into `src/assets/catalog/` (only inside that
  folder), commits the two paths (`git commit --only`, so nothing else you staged rides
  along) and pushes. A failed push is reported; the files and the commit stay.

```yaml
inventory:
  site_dir: /path/to/nerdsville-site   # the site checkout; blank = preview only
  publish_git: true                    # commit catalog + photos after a publish
  publish_push: true                   # push so the site's CI deploys
```

## Setup

```bash
protoagent plugin install https://github.com/protoLabsAI/inventory-plugin
```

```yaml
# langgraph-config.yaml
plugins:
  enabled: [inventory]
inventory:
  workspace_dir: /path/to/agent/workspace   # relative CSV paths resolve here
```

The database lives in the host's instance-scoped plugin store, so each agent (and the dev
sandbox) gets its own. Seed it from a spreadsheet: `inventory_import_csv(path="inventory.csv")`
maps common headers (`inventory_id`, `item`, `lot_id`, `target_low_usd`, `target_price_usd`,
`status`, `sold_price_usd`, `price_source` …) and reports what it could not place.

## Development

```bash
uv venv --python 3.12 .venv && uv pip install -p .venv/bin/python -r requirements-dev.txt ruff
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/python -m pytest -q
```

The suite is host-free: no protoAgent, no database beyond a temp file.
