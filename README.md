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
| **Tools** | `inventory_summary` · `inventory_list` · `inventory_get` · `inventory_upsert_lot` · `inventory_upsert_item` · `inventory_delete_item` · `inventory_set_price` · `inventory_mark_sold` · `inventory_listing` · `inventory_import_csv` · `inventory_export_csv` · `inventory_stale` |
| **View** | a rail panel: the item grid (double-click to edit name/category/condition/qty; status select; click a price for the Price dialog), Price / Sold / Listing / Edit / Delete per row, lots with their P&L, sales, an activity log, CSV import (file or paste, with the mapping report) and export |
| **API** | bearer-gated JSON under `/api/plugins/inventory` — `summary`, `lots`, `items`, `items/{id}/price`, `items/{id}/sold`, `items/{id}/listings`, `listings/{id}/end`, `sales`, `stale`, `audit`, `import`, `export` |
| **Events** | `inventory.item.changed`, `inventory.lot.changed`, `inventory.sale.recorded`, `inventory.imported` |
| **Skill** | `inventory-ops` — the rules (a target needs a basis; never set sold by hand; sold ≠ active ≠ retail) and the re-price / weekly-review routines |

## The model

- **Lot** — a purchase: cost, date, source. P&L is against the lot cost.
- **Item** — one sellable thing from a lot. Status `planned → available → listed → pending → sold`, or `kept` / `withdrawn` (`planned` = a piece that exists once a sealed box is split).
  Targets low/target/high **with a `price_basis` and a date**.
- **Price observation** — the comps behind a target (source, n, p25/median/p75, query). Recorded by `inventory_set_price`.
- **Listing** — where the item is up (channel, url, price).
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
