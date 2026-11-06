# Tools

The agent tools this plugin registers. You don't call these yourself. The agent picks them from what you
ask, and the plugin's own turns (scan identification, pricing, listing drafts, pasted sales) use the turn
tools below. Prices go in and come out in dollars; the database stores cents.

`tests/test_docs_reference.py` fails if a tool is added or removed without this page changing.

## Reading the inventory

| Tool | What it does |
|---|---|
| `inventory_summary` | The P&L per lot: what it cost, what's left to sell at low/target/high, what's been realized (gross and net), and the projected net if the rest sells at target. The start for "how are we doing" |
| `inventory_list` | Items with their target, status and bin, ordered by game system, lot, then category. Filters: lot, status (comma-separated), category, system, bin (`-` for no bin) and a text search |
| `inventory_get` | One item in full: its fields, listings, sales and last 20 price observations |
| `inventory_stale` | What needs attention: listings live longer than `listed_days`, and unsold items whose price evidence is older than `price_days` or missing (`0` uses the Stale price setting) |
| `inventory_reprice_plan` | The re-price worklist: unsold items with stale or missing evidence, oldest first, capped, each with a suggested eBay search and its current band |

## Changing the inventory

| Tool | What it does |
|---|---|
| `inventory_upsert_lot` | Create or update a lot: a purchase that items came from, with what it cost. Only the fields you pass change |
| `inventory_upsert_item` | Create an item (a blank id mints one) or update fields on one. Only the fields you pass change. It doesn't set prices or sold status |
| `inventory_delete_item` | Remove an item and its listings, sales and observations for good. Meant for mistakes and duplicates; `withdrawn` or `kept` is the right status for things that just left the sale pile |
| `inventory_set_price` | Set an item's low/target/high **with** the evidence. `basis` is required (for example "eBay sold comps (22 sold, incl. shipping)"), and the comps are recorded as a price observation in the same write |
| `inventory_mark_sold` | Record a sale. Net = price + shipping charged − fees − shipping cost. Decrements quantity, or marks the last unit sold. A live listing on the same channel closes as sold; one on any other channel is flagged `needs_end`. Refuses an item that's already sold unless `force` |
| `inventory_listing` | Track where an item is listed. `add` records a live listing and moves an available item to listed. `end` closes one. `to_end` lists listings still up somewhere for items that sold elsewhere |
| `inventory_bins` | The physical bins (B01, B02…). `list` with counts and unsold value, `create` (the next numbers, or a named one, with a label), `update` a label or note, `delete` an empty bin |
| `inventory_move_to_bin` | Put items into a bin, or `""` to take them out. All or nothing: an unknown item or bin refuses the whole move. Every move is audited |
| `inventory_packing` | The pick list. `list` shows every sale not yet packed, grouped by the bin to pull it from. `packed` ticks one off, and an item with no stock left is taken out of its bin |
| `inventory_import_csv` | Load a spreadsheet from a path or pasted text. Common headers are matched for you. Rows are upserted by id; blank cells keep what's there; a sold row with a price also records the sale. Returns the mapping it used and per-line warnings |
| `inventory_export_csv` | Write items, lots or sales as CSV to a path in the workspace, or return the text. A spreadsheet view, not a backup |

## Turn tools: scan identification

The plugin starts these turns itself, in a session of their own. See [how-it-works.md](how-it-works.md).

| Tool | What it does |
|---|---|
| `inventory_scan_photos` | See a scan's photos (up to 3) and what's already known about it |
| `inventory_scan_propose` | Record what a scan shows: the name, a 0–1 confidence that it's the exact product, up to 3 alternatives, and for a box the pieces it splits into. Unknown model counts stay 0 |
| `inventory_scan_fail` | Give up on a scan with a one-line reason you'll read, such as "the photo is too dark to read the box" |

## Turn tools: pricing a scan

| Tool | What it does |
|---|---|
| `inventory_scan_sealed_price` | For a box, record what it would sell for sealed (from eBay sold comps), plus the fees on the sealed price and on the pieces' total. Kept on the scan as a comparison, never as an item price |
| `inventory_scan_pricing_done` | Close the pricing turn, listing each piece it couldn't price and why (for example "only 1 sold comp") |

## Turn tools: listing drafts

| Tool | What it does |
|---|---|
| `inventory_draft_item` | See an item and its scan photos for writing its eBay listing |
| `inventory_draft_write` | Save the draft: a title of at most 80 characters and a plain-text description with the condition first. The price always comes from the item's target |
| `inventory_draft_fail` | Give up on a draft with a reason, such as "no photos and the name doesn't say what is in the box" |

## Turn tools: pasted sales

| Tool | What it does |
|---|---|
| `inventory_sale_candidates` | For the view's **paste-a-sale** turns only. Finds the items a pasted order or note describes, best first, with bin, status, target and any live listing, plus items sold in the last 180 days (so a repeat paste reads as already recorded). A sale you mention in chat is found with `inventory_list` / `inventory_get` and recorded with `inventory_mark_sold`. |
| `inventory_sale_propose` | Propose one line per item for you to check: price, shipping charged, fees (marked estimated when they are), postage, channel and date. Nothing is recorded until you press Record |
| `inventory_sale_fail` | Give up on a paste with a reason. `already_recorded` names the item when the paste repeats a recorded sale |
