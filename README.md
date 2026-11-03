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
| **Tools** | `inventory_summary` · `inventory_list` · `inventory_get` · `inventory_upsert_lot` · `inventory_upsert_item` · `inventory_delete_item` · `inventory_set_price` · `inventory_mark_sold` · `inventory_listing` · `inventory_bins` · `inventory_move_to_bin` · `inventory_packing` · `inventory_import_csv` · `inventory_export_csv` · `inventory_reprice_plan` · `inventory_stale` · `inventory_scan_photos` · `inventory_scan_propose` · `inventory_scan_fail` · `inventory_scan_pricing_done` · `inventory_draft_item` · `inventory_draft_write` · `inventory_draft_fail` (the scan and draft tools are for the turns the plugin starts) |
| **View** | a rail panel built around what you ask, not the tables. **Scan** (pinned at the bottom on a phone, in the header on desktop) walks a photo from box to binned pieces: what it is, a photo through the phone's own camera, the agent's identification (pick one of its guesses or type it when it isn't sure), Keep or Sell per piece, prices from sold comps, then a bin for each. **Next** is a work queue (to pack, grouped by bin; scans that need a look, resumable where you left off; ready to list; to split; items not in a bin; stale prices) whose sections only appear when there is something to do; **Stock** filters by lifecycle (for sale, to split, sold, all), with search and one System / Lot filter, list rows on a phone and a table from 640px; **Bins** shows each bin with its count and value, opens a bin's contents (move items in, copy its list), creates bins and prints their **labels**; **Lots** holds the P&L, with each lot's units and sales in its detail. One drawer holds everything about an item: price with its full basis, Record sale, Listing, Keep, Withdraw, Edit, Delete and its history. Multi-select with **Copy list** (also ⌘C) — a for-sale post per game system: the system as a header, then `Name NoS — $20` per line (condition inline, whole-dollar prices). More holds Add item, New lot, CSV import (file or paste, with the mapping report) and export, and a readable History |
| **API** | bearer-gated JSON under `/api/plugins/inventory` — `summary`, `lots`, `items`, `items/{id}/price`, `items/{id}/sold`, `items/{id}/listings`, `listings/{id}/end`, `sales`, `bins` (list/create/update/delete), `bins/move`, `labels/base`, `labels/sheet`, `to-pack`, `sales/{id}/packed`, `scans` (list/create/get/discard), `scans/{id}/photos` (raw image body; GET one by id), `scans/{id}/retry`, `scans/{id}/confirm`, `scans/{id}/price`, `scans/{id}/finish`, `drafts` (each item's draft state), `drafts/bulk` (at most 10), `items/{id}/draft` (GET the draft / POST to request one), `items/{id}/draft/copied`, `items/{id}/draft/live` (the eBay address), `items/{id}/photos.zip`, `stale`, `audit`, `import`, `export` |
| **Automations** | `weekly_review: true` arms a plugin-owned recurring turn (`weekly_review_cron`, default Monday 09:00 in `review_timezone`) that re-prices stale evidence from eBay sold comps through the same tools, reports stale listings with a recommendation (it never changes a listing itself), and posts the per-lot P&L. Cancelled when the plugin is disabled. |
| **Events** | `inventory.item.changed`, `inventory.lot.changed`, `inventory.sale.recorded`, `inventory.imported`, `inventory.bin.changed`, `inventory.sale.packed`, `inventory.scan.changed`, `inventory.draft.changed` |
| **Skill** | `inventory-ops` — the rules (a target needs a basis; never set sold by hand; sold ≠ active ≠ retail) and the re-price / weekly-review routines |

## The model

- **Lot** — a purchase: cost, date, source. P&L is against the lot cost.
- **Item** — one sellable thing from a lot, optionally tagged with its game **system** (Warhammer 40K, Blood Bowl, …). Status `planned → available → listed → pending → sold`, or `kept` / `withdrawn` (`planned` = a piece that exists once a sealed box is split).
  Targets low/target/high **with a `price_basis` and a date**.
- **Price observation** — the comps behind a target (source, n, p25/median/p75, query). Recorded by `inventory_set_price`.
- **Listing** — where the item is up (channel, url, price).
- **Bin** — where the item physically is (B01, B02 …). Moves are audited with where it came from. A sale waits on the **pick list** until it is packed; sales from before bins existed are recorded as already packed.
- **Bin label** — a printable US Letter sheet (3 × 4), each label a QR code, the bin id in large type and its label text. It opens in a new tab and you print from there. Every QR encodes `<base>/plugins/inventory/b/<BIN>`, where `<base>` is **Bin label address** in Settings, else the agent's public URL (a fleet member's hub address, which a paired phone on the tailnet can open), else its console. That one public route takes only a bin id and reads no data: on a protoAgent with plugin-view deep links (#4712) it forwards to the console with the bin open; on an older one it says the console needs an update. So printed labels never go stale when the console changes how it opens a view.
- **Scan** — a photo the agent identifies in the background. A **box** scan starts its lot at once with what you paid (named "Box scanned …" until confirmed) and proposes the pieces it splits into; a **piece** is scanned alone or into a box being broken down. Photos (up to 3 per scan) are stripped of metadata on upload (HEIC is converted with macOS `sips`) and kept beside the database. After a photo lands the plugin starts one agent turn with `sdk.run_in_session` in that scan's own session (`inventory-scan-<id>`, never your chat): it may not use eBay, pricing tools or subagents and allows two web searches; it answers through `inventory_scan_propose` (or `_fail`). Confirming writes the items (Keep → `kept`, Sell → `planned`) and keeps the correction (the agent's name vs yours) on the scan. **Price the sell pile** starts a second turn that prices from eBay sold comps with `inventory_set_price` and reports what it couldn't price. Binning makes each sell piece `available`. A turn that never answers shows as failed with Retry after **Scan: give up after** (Settings).
- **Listing drafts** — **Draft listing** on an item (or **Draft N** from Next ▸ Ready to list, at most 10, one after another) starts one agent turn in the item's own session (`inventory-draft-<id>`, never your chat). It reads the item and its scan photos and writes a title (at most 80 characters) and a plain-text description through `inventory_draft_write`, with no eBay, pricing tools or subagents. The draft's price is always the item's target, shown with its basis. Copy the title, price and description, download the photos, list it on eBay yourself, then **I listed it** with the listing's address (`ebay.com/itm/…`, or the app's `ebay.us` share link): that records an eBay listing at the target and marks the item listed. Next asks about drafts copied a day ago but never marked live. Nothing is ever posted for you.
- **Sale** — `net = price + shipping charged − fees − shipping cost`. Recording a sale closes live listings.
- **Audit** — every mutation, with the actor (`agent` or `console`) and the fields that changed.

Money is stored as integer cents and exposed as dollars.

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
